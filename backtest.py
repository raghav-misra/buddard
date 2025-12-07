import time
import datetime
import pandas as pd
from collections import defaultdict
from nba_api.stats.endpoints import scoreboardv2, boxscoretraditionalv3, playercareerstats, playergamelog, leaguedashteamstats
from lib.prediction_engine import PredictionEngine

import json
import os
import lib.constants as constants

# Constants
SEASON = '2025-26' 
API_DELAY = 0.6
BASELINE_CACHE = {}
TEAM_DEF_RATINGS = {}

def fetch_team_defense():
    """Fetches team defensive ratings and pace for DvP adjustments."""
    global TEAM_DEF_RATINGS
    print("Fetching Team Stats (Defense & Pace)...")
    try:
        # 1. Fetch Advanced Team Stats (for PACE)
        teams_adv = leaguedashteamstats.LeagueDashTeamStats(
            season=SEASON,
            measure_type_detailed_defense='Advanced',
            timeout=10
        )
        df_adv = teams_adv.league_dash_team_stats.get_data_frame()
        time.sleep(API_DELAY)

        # 2. Fetch Opponent Stats (for OPP_PTS, OPP_REB, OPP_AST)
        teams_opp = leaguedashteamstats.LeagueDashTeamStats(
            season=SEASON,
            measure_type_detailed_defense='Opponent',
            timeout=10
        )
        df_opp = teams_opp.league_dash_team_stats.get_data_frame()
        time.sleep(API_DELAY)
        
        if not df_adv.empty and not df_opp.empty:
            # Calculate League Averages
            avg_pace = df_adv['PACE'].mean()
            avg_opp_pts = df_opp['OPP_PTS'].mean()
            avg_opp_reb = df_opp['OPP_REB'].mean()
            avg_opp_ast = df_opp['OPP_AST'].mean()

            print(f"League Averages - Pace: {avg_pace:.2f}, OppPTS: {avg_opp_pts:.1f}, OppREB: {avg_opp_reb:.1f}, OppAST: {avg_opp_ast:.1f}")

            for _, row in df_adv.iterrows():
                team_id = row['TEAM_ID']
                # Find corresponding row in df_opp
                opp_row = df_opp[df_opp['TEAM_ID'] == team_id].iloc[0]
                
                TEAM_DEF_RATINGS[team_id] = {
                    'pace': row['PACE'],
                    'opp_pts': opp_row['OPP_PTS'],
                    'opp_reb': opp_row['OPP_REB'],
                    'opp_ast': opp_row['OPP_AST'],
                    'league_avg_pace': avg_pace,
                    'league_avg_opp_pts': avg_opp_pts,
                    'league_avg_opp_reb': avg_opp_reb,
                    'league_avg_opp_ast': avg_opp_ast
                }
            print(f"Fetched detailed stats for {len(TEAM_DEF_RATINGS)} teams.")
        else:
            print("Could not fetch team stats.")
    except Exception as e:
        print(f"Error fetching team defense: {e}")

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
        # Return list of dicts with team info
        return games[['GAME_ID', 'HOME_TEAM_ID', 'VISITOR_TEAM_ID']].to_dict('records')
    except Exception as e:
        print(f"Error fetching games for {date_str}: {e}")
        return []

def get_player_baseline(player_id, game_date, is_home, opponent_id):
    """Fetches baseline stats for a player, with caching."""
    # Cache key needs to include date/home/opp now, or we just cache the raw stats and adjust dynamically.
    # To keep it simple and fast, let's cache the RAW season/log data, and do the adjustments every time.
    # But BASELINE_CACHE currently stores the final dict.
    # Let's change the cache key to include the context: f"{player_id}_{game_date}"
    
    cache_key = f"{player_id}_{game_date}_{is_home}_{opponent_id}"
    if cache_key in BASELINE_CACHE:
        return BASELINE_CACHE[cache_key]

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
        
        season_pts_min = current_season['PTS'] / minutes
        season_reb_min = current_season['REB'] / minutes
        season_ast_min = current_season['AST'] / minutes
        avg_minutes = minutes / games_played

        # 2. Get Recent Game Logs (Filtered by Date)
        gamelog = playergamelog.PlayerGameLog(player_id=player_id, season=SEASON)
        logs_df = gamelog.player_game_log.get_data_frame()
        time.sleep(API_DELAY)

        # Filter logs to only include games BEFORE the backtest date
        if not logs_df.empty:
            # Convert GAME_DATE to datetime for comparison
            # API format is usually "OCT 29, 2024" or similar. 
            # Actually, let's check the format. It's usually 'YYYY-MM-DD' in some endpoints or 'MMM DD, YYYY' in others.
            # In research_api output it was "Apr 11, 2025".
            # We need to parse it.
            logs_df['date_dt'] = pd.to_datetime(logs_df['GAME_DATE'])
            target_dt = pd.to_datetime(game_date)
            
            past_logs = logs_df[logs_df['date_dt'] < target_dt]
        else:
            past_logs = pd.DataFrame()

        # --- Weighted Baseline ---
        if not past_logs.empty:
            last_5 = past_logs.head(5)
            l5_min = last_5['MIN'].sum()
            
            if l5_min > 0:
                l5_pts_min = last_5['PTS'].sum() / l5_min
                l5_reb_min = last_5['REB'].sum() / l5_min
                l5_ast_min = last_5['AST'].sum() / l5_min
                
                baseline_pts_min = (0.7 * season_pts_min) + (0.3 * l5_pts_min)
                baseline_reb_min = (0.7 * season_reb_min) + (0.3 * l5_reb_min)
                baseline_ast_min = (0.7 * season_ast_min) + (0.3 * l5_ast_min)
            else:
                baseline_pts_min = season_pts_min
                baseline_reb_min = season_reb_min
                baseline_ast_min = season_ast_min
        else:
            baseline_pts_min = season_pts_min
            baseline_reb_min = season_reb_min
            baseline_ast_min = season_ast_min

        # --- Home/Away Adjustment ---
        ha_modifier = 1.02 if is_home else 0.98
        baseline_pts_min *= ha_modifier
        baseline_reb_min *= ha_modifier
        baseline_ast_min *= ha_modifier

        # --- Opponent DvP & Pace Adjustment ---
        if opponent_id in TEAM_DEF_RATINGS:
            stats = TEAM_DEF_RATINGS[opponent_id]
            
            # Pace Modifier
            pace_modifier = stats['pace'] / stats['league_avg_pace']
            
            # Stat Specific Modifiers
            pts_modifier = stats['opp_pts'] / stats['league_avg_opp_pts']
            reb_modifier = stats['opp_reb'] / stats['league_avg_opp_reb']
            ast_modifier = stats['opp_ast'] / stats['league_avg_opp_ast']
            
            # Apply Modifiers
            baseline_pts_min *= (pace_modifier * pts_modifier)
            baseline_reb_min *= (pace_modifier * reb_modifier)
            baseline_ast_min *= (pace_modifier * ast_modifier)

        # --- Variance (Sigma) ---
        if past_logs.empty:
            sigma_pts = 5.0
            sigma_reb = 2.0
            sigma_ast = 2.0
        else:
            recent_logs = past_logs.head(20)
            sigma_pts = recent_logs['PTS'].std() if not pd.isna(recent_logs['PTS'].std()) else 5.0
            sigma_reb = recent_logs['REB'].std() if not pd.isna(recent_logs['REB'].std()) else 2.0
            sigma_ast = recent_logs['AST'].std() if not pd.isna(recent_logs['AST'].std()) else 2.0

        baseline = {
            'baseline_pts_min': baseline_pts_min,
            'baseline_reb_min': baseline_reb_min,
            'baseline_ast_min': baseline_ast_min,
            'avg_minutes': avg_minutes,
            'sigma_pts': sigma_pts,
            'sigma_reb': sigma_reb,
            'sigma_ast': sigma_ast
        }

        BASELINE_CACHE[cache_key] = baseline
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

def process_game(game_info, game_date, aggregator):
    """Runs the prediction engine on a game and updates the aggregator."""
    game_id = game_info['GAME_ID']
    home_team_id = game_info['HOME_TEAM_ID']
    visitor_team_id = game_info['VISITOR_TEAM_ID']
    
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
        team_id = player['teamId']
        
        # Determine Home/Away and Opponent
        is_home = (team_id == home_team_id)
        opponent_id = visitor_team_id if is_home else home_team_id
        
        baseline = get_player_baseline(player_id, game_date, is_home, opponent_id)
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
                cur_fouls = 0
            else:
                p_data = p_snap.iloc[0]
                cur_stats = {
                    'PTS': p_data['points'],
                    'REB': p_data['reboundsTotal'],
                    'AST': p_data['assists']
                }
                cur_min = parse_minutes(p_data['minutes'])
                cur_fouls = p_data['foulsPersonal']

            # Calculate Score Differential
            # Group by teamId to get team scores
            team_scores = snapshot_df.groupby('teamId')['points'].sum()
            
            # Determine my team and opp team score
            my_score = team_scores.get(team_id, 0)
            opp_score = team_scores.get(opponent_id, 0)
            score_diff = my_score - opp_score

            # Map label to period number
            period_map = {"Q1": 1, "Q2": 2, "Q3": 3}
            period = period_map.get(label, 0)

            # Calc Prediction
            # Use PTS pace for performance factor (Hot Hand)
            if cur_min > 0:
                cur_pace_pts = cur_stats['PTS'] / cur_min
            else:
                cur_pace_pts = 0
                
            perf_factor = PredictionEngine.calculate_performance_factor(cur_pace_pts, baseline['baseline_pts_min'])
            
            # Use new method for remaining minutes
            rm = PredictionEngine.calculate_dynamic_remaining_minutes(
                baseline['avg_minutes'], 
                cur_min, 
                cur_fouls, 
                score_diff, 
                period,
                perf_factor
            )

            stat_types = [
                ('PTS', 'baseline_pts_min', 'sigma_pts'),
                ('REB', 'baseline_reb_min', 'sigma_reb'),
                ('AST', 'baseline_ast_min', 'sigma_ast')
            ]

            for stat_name, base_key, sigma_key in stat_types:
                pfs = PredictionEngine.calculate_pfs(
                    cur_stats[stat_name], baseline[base_key], rm
                )
                low, high, _ = PredictionEngine.get_prediction_range(
                    pfs, baseline[sigma_key], cur_min, baseline['avg_minutes'], cur_stats[stat_name]
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

                # Strategy 3: 50th Percentile (Median)
                # Bet OVER if Line <= 50th Percentile.
                # Hit if Final > 50th Percentile.
                p50_threshold = low + 0.50 * (high - low)
                is_p50_hit = final_stats[stat_name] > p50_threshold
                
                aggregator[(label, stat_name)]['total'] += 1
                if is_floor_hit:
                    aggregator[(label, stat_name)]['floor_hits'] += 1
                if is_p25_hit:
                    aggregator[(label, stat_name)]['p25_hits'] += 1
                if is_p50_hit:
                    aggregator[(label, stat_name)]['p50_hits'] += 1

def print_running_summary(aggregator, total_games_processed):
    print(f"\n--- Running Stats ({total_games_processed} Games) ---")
    print(f"{'QTR':<5} | {'STAT':<5} | {'FLOOR %':<8} | {'25th %':<8} | {'50th %':<8} | {'SAMPLE':<6}")
    
    sorted_keys = sorted(aggregator.keys())
    for qtr, stat in sorted_keys:
        data = aggregator[(qtr, stat)]
        total = data['total']
        if total == 0: continue
        
        floor_ratio = (data['floor_hits'] / total * 100)
        p25_ratio = (data['p25_hits'] / total * 100)
        p50_ratio = (data['p50_hits'] / total * 100)
        
        print(f"{qtr:<5} | {stat:<5} | {floor_ratio:<6.1f}%  | {p25_ratio:<6.1f}%  | {p50_ratio:<6.1f}%  | {total:<6}")

def main():
    # load_baselines_from_file() # Disable file loading for now as we need dynamic calculation
    fetch_team_defense()
    
    # (Quarter, Stat) -> {floor_hits, p25_hits, p50_hits, total}
    aggregator = defaultdict(lambda: {'floor_hits': 0, 'p25_hits': 0, 'p50_hits': 0, 'total': 0})
    
    target_dates = get_dates_last_month()
    
    print("Fetching games for the last 30 days...")
    
    total_games_processed = 0
    
    # Use all dates for the full run
    # target_dates = dates[:1] # TEMPORARY LIMIT FOR VERIFICATION
    print(f"Processing {len(target_dates)} dates from {target_dates[0]} to {target_dates[-1]}")

    try:
        for date_str in target_dates:
            print(f"\n--- Date: {date_str} ---")
            games = get_games_for_date(date_str)
            print(f"Found {len(games)} games.")
            
            for game in games:
                process_game(game, date_str, aggregator)
                total_games_processed += 1
                print_running_summary(aggregator, total_games_processed)
    except KeyboardInterrupt:
        print("\nRun interrupted by user. Showing partial results...")

    print("\n" + "="*80)
    print(f"AGGREGATE BACKTEST RESULTS ({total_games_processed} Games)")
    print("Strategies: Floor (Low > Line) vs 25th Percentile vs 50th Percentile")
    print("="*80)
    print(f"{'QTR':<5} | {'STAT':<5} | {'FLOOR %':<8} | {'25th %':<8} | {'50th %':<8} | {'SAMPLE':<6}")
    print("-" * 80)
    
    # Sort by Quarter then Stat
    sorted_keys = sorted(aggregator.keys())
    
    for qtr, stat in sorted_keys:
        data = aggregator[(qtr, stat)]
        floor_hits = data['floor_hits']
        p25_hits = data['p25_hits']
        p50_hits = data['p50_hits']
        total = data['total']
        
        floor_ratio = (floor_hits / total * 100) if total > 0 else 0.0
        p25_ratio = (p25_hits / total * 100) if total > 0 else 0.0
        p50_ratio = (p50_hits / total * 100) if total > 0 else 0.0
        
        print(f"{qtr:<5} | {stat:<5} | {floor_ratio:<6.1f}%  | {p25_ratio:<6.1f}%  | {p50_ratio:<6.1f}%  | {total:<6}")
    print("="*80)

if __name__ == "__main__":
    main()
