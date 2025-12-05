import time
import sys
import datetime
import pandas as pd
from collections import defaultdict
from nba_api.stats.endpoints import scoreboardv2, boxscoretraditionalv3, playercareerstats, playergamelog
from prediction_engine import PredictionEngine

import json
import os
import constants

# Constants
SEASON = '2025-26' 
API_DELAY = 0.6
BASELINE_CACHE = {}

def load_baselines_from_file():
    """Loads baselines from the researcher's file to speed up backtesting."""
    global BASELINE_CACHE
    if os.path.exists(constants.BASELINES_FILE):
        try:
            with open(constants.BASELINES_FILE, 'r') as f:
                data = json.load(f)
                if 'players' in data:
                    print(f"Loaded {len(data['players'])} players from {constants.BASELINES_FILE}")
                    # Flatten structure: {pid: stats}
                    for pid, pdata in data['players'].items():
                        BASELINE_CACHE[str(pid)] = pdata['stats']
        except Exception as e:
            print(f"Error loading baselines file: {e}")

def get_dates_last_month():
    """Returns a list of date strings for the last 30 days (excluding today)."""
    dates = []
    today = datetime.date.today()
    for i in range(1, 31):
        d = today - datetime.timedelta(days=i)
        dates.append(d.strftime('%Y-%m-%d'))
    print(dates)
    return dates

def get_games_for_date(date_str):
    """Fetches game IDs for a specific date."""
    try:
        board = scoreboardv2.ScoreboardV2(game_date=date_str)
        games = board.game_header.get_data_frame()
        if games.empty:
            return []
        # Filter for completed games (Status=3 usually, or just check GAME_STATUS_TEXT)
        # We'll assume past dates have completed games.
        return games['GAME_ID'].tolist()
    except Exception as e:
        print(f"Error fetching games for {date_str}: {e}")
        return []

def get_player_baseline(player_id):
    """Fetches baseline stats for a player, with caching."""
    pid_str = str(player_id)
    if pid_str in BASELINE_CACHE:
        return BASELINE_CACHE[pid_str]

    try:
        # 1. Get Season Averages
        career = playercareerstats.PlayerCareerStats(player_id=player_id)
        season_df = career.season_totals_regular_season.get_data_frame()
        time.sleep(API_DELAY)

        if season_df.empty:
            return None

        current_season = season_df.iloc[-1]
        minutes = current_season['MIN']
        if minutes < 50: 
            return None

        games_played = current_season['GP']
        
        baseline = {
            'baseline_pts_min': current_season['PTS'] / minutes,
            'baseline_reb_min': current_season['REB'] / minutes,
            'baseline_ast_min': current_season['AST'] / minutes,
            'avg_minutes': minutes / games_played
        }

        # 2. Get Recent Game Logs for Sigma
        gamelog = playergamelog.PlayerGameLog(player_id=player_id, season=SEASON)
        logs_df = gamelog.player_game_log.get_data_frame()
        time.sleep(API_DELAY)

        if logs_df.empty:
            baseline['sigma_pts'] = 5.0
            baseline['sigma_reb'] = 2.0
            baseline['sigma_ast'] = 2.0
        else:
            recent_logs = logs_df.head(20)
            baseline['sigma_pts'] = recent_logs['PTS'].std() if not pd.isna(recent_logs['PTS'].std()) else 5.0
            baseline['sigma_reb'] = recent_logs['REB'].std() if not pd.isna(recent_logs['REB'].std()) else 2.0
            baseline['sigma_ast'] = recent_logs['AST'].std() if not pd.isna(recent_logs['AST'].std()) else 2.0

        BASELINE_CACHE[pid_str] = baseline
        return baseline
    except Exception as e:
        print(f"Error getting baseline for {player_id}: {e}")
        return None

def get_boxscore_snapshot(game_id, end_range):
    """Fetches boxscore for a specific range."""
    try:
        box = boxscoretraditionalv3.BoxScoreTraditionalV3(
            game_id=game_id,
            range_type="2",
            start_range="0",
            end_range=str(end_range)
        )
        return box.player_stats.get_data_frame()
    except Exception as e:
        print(f"Error fetching snapshot {end_range} for {game_id}: {e}")
        return pd.DataFrame()

def parse_minutes(min_str):
    try:
        parts = min_str.split(':')
        return int(parts[0]) + int(parts[1])/60
    except:
        return 0.0

def process_game(game_id, aggregator):
    """Runs the prediction engine on a game and updates the aggregator."""
    print(f"  Processing Game {game_id}...")
    
    # 1. Ground Truth
    try:
        full_box = boxscoretraditionalv3.BoxScoreTraditionalV3(game_id=game_id)
        full_df = full_box.player_stats.get_data_frame()
    except Exception as e:
        print(f"    Failed to fetch full box: {e}")
        return

    if full_df.empty:
        return

    full_df['min_float'] = full_df['minutes'].apply(parse_minutes)
    significant_players = full_df[full_df['min_float'] > 20]
    
    if significant_players.empty:
        return

    # 2. Snapshots
    checkpoints = {
        "Q1": 7200,
        "Q2": 14400, # Halftime
        "Q3": 21600
    }
    
    snapshots = {}
    for label, rng in checkpoints.items():
        snapshots[label] = get_boxscore_snapshot(game_id, rng)
        time.sleep(API_DELAY)

    # 3. Evaluate
    for _, player in significant_players.iterrows():
        player_id = player['personId']
        
        baseline = get_player_baseline(player_id)
        if not baseline:
            continue

        final_stats = {
            'PTS': player['points'],
            'REB': player['reboundsTotal'],
            'AST': player['assists']
        }

        for label, snapshot_df in snapshots.items():
            if snapshot_df.empty:
                continue
                
            p_snap = snapshot_df[snapshot_df['personId'] == player_id]
            if p_snap.empty:
                cur_stats = {'PTS': 0, 'REB': 0, 'AST': 0}
                cur_min = 0.0
            else:
                p_data = p_snap.iloc[0]
                cur_stats = {
                    'PTS': p_data['points'],
                    'REB': p_data['reboundsTotal'],
                    'AST': p_data['assists']
                }
                cur_min = parse_minutes(p_data['minutes'])

            # Calc Prediction
            alpha = PredictionEngine.calculate_alpha(cur_min, baseline['avg_minutes'])
            rm = max(0, baseline['avg_minutes'] - cur_min)

            stat_types = [
                ('PTS', 'baseline_pts_min', 'sigma_pts'),
                ('REB', 'baseline_reb_min', 'sigma_reb'),
                ('AST', 'baseline_ast_min', 'sigma_ast')
            ]

            for stat_name, base_key, sigma_key in stat_types:
                pfs = PredictionEngine.calculate_pfs(
                    cur_stats[stat_name], cur_min, baseline[base_key], rm, alpha
                )
                low, high, _ = PredictionEngine.get_prediction_range(
                    pfs, baseline[sigma_key], cur_min, baseline['avg_minutes']
                )
                
                # Strategy 1: Floor (Low End of Range)
                # Bet OVER if Line <= Low.
                # Hit if Final > Low.
                floor_threshold = low
                is_floor_hit = final_stats[stat_name] > floor_threshold

                # Strategy 2: 25th Percentile
                # Bet OVER if Line <= 25th Percentile.
                # Hit if Final > 25th Percentile.
                p25_threshold = low + 0.25 * (high - low)
                is_p25_hit = final_stats[stat_name] > p25_threshold
                
                aggregator[(label, stat_name)]['total'] += 1
                if is_floor_hit:
                    aggregator[(label, stat_name)]['floor_hits'] += 1
                if is_p25_hit:
                    aggregator[(label, stat_name)]['p25_hits'] += 1

def main():
    load_baselines_from_file()
    
    # (Quarter, Stat) -> {floor_hits, p25_hits, total}
    aggregator = defaultdict(lambda: {'floor_hits': 0, 'p25_hits': 0, 'total': 0})
    
    target_dates = get_dates_last_month()
    
    print("Fetching games for the last 30 days...")
    
    total_games_processed = 0
    
    # Use all dates for the full run
    # target_dates = dates[:1] # TEMPORARY LIMIT FOR VERIFICATION
    print(f"Processing {len(target_dates)} dates from {target_dates[0]} to {target_dates[-1]}")

    try:
        for date_str in target_dates:
            print(f"\n--- Date: {date_str} ---")
            game_ids = get_games_for_date(date_str)
            print(f"Found {len(game_ids)} games.")
            
            for gid in game_ids:
                process_game(gid, aggregator)
                total_games_processed += 1
    except KeyboardInterrupt:
        print("\nRun interrupted by user. Showing partial results...")

    print("\n" + "="*65)
    print(f"AGGREGATE BACKTEST RESULTS ({total_games_processed} Games)")
    print("Strategies: Floor (Low > Line) vs 25th Percentile (Low+25% > Line)")
    print("="*65)
    print(f"{'QTR':<5} | {'STAT':<5} | {'FLOOR %':<8} | {'25th %':<8} | {'SAMPLE':<6}")
    print("-" * 65)
    
    # Sort by Quarter then Stat
    sorted_keys = sorted(aggregator.keys())
    
    for qtr, stat in sorted_keys:
        data = aggregator[(qtr, stat)]
        floor_hits = data['floor_hits']
        p25_hits = data['p25_hits']
        total = data['total']
        
        floor_ratio = (floor_hits / total * 100) if total > 0 else 0.0
        p25_ratio = (p25_hits / total * 100) if total > 0 else 0.0
        
        print(f"{qtr:<5} | {stat:<5} | {floor_ratio:<6.1f}%  | {p25_ratio:<6.1f}%  | {total:<6}")
    print("="*65)

if __name__ == "__main__":
    main()
