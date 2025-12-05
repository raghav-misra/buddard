import time
import pandas as pd
from nba_api.stats.endpoints import boxscoretraditionalv3, playercareerstats, playergamelog
from prediction_engine import PredictionEngine

# Constants
SEASON = '2025-26' 
API_DELAY = 0.6

def get_player_baseline(player_id):
    """Fetches baseline stats for a player."""
    try:
        # 1. Get Season Averages
        career = playercareerstats.PlayerCareerStats(player_id=player_id)
        season_df = career.season_totals_regular_season.get_data_frame()
        time.sleep(API_DELAY)

        if season_df.empty:
            return None

        # Get current season (last row)
        # In a real robust system, filter by SEASON_ID
        current_season = season_df.iloc[-1]
        
        minutes = current_season['MIN']
        if minutes < 50: 
            return None

        games_played = current_season['GP']
        
        baseline_pts_min = current_season['PTS'] / minutes
        baseline_reb_min = current_season['REB'] / minutes
        baseline_ast_min = current_season['AST'] / minutes
        avg_minutes = minutes / games_played

        # 2. Get Recent Game Logs for Sigma
        gamelog = playergamelog.PlayerGameLog(player_id=player_id, season=SEASON)
        logs_df = gamelog.player_game_log.get_data_frame()
        time.sleep(API_DELAY)

        if logs_df.empty:
            sigma_pts = 5.0 # Default fallback
            sigma_reb = 2.0
            sigma_ast = 2.0
        else:
            recent_logs = logs_df.head(20)
            sigma_pts = recent_logs['PTS'].std()
            sigma_reb = recent_logs['REB'].std()
            sigma_ast = recent_logs['AST'].std()

        # Handle NaNs
        sigma_pts = 5.0 if pd.isna(sigma_pts) else sigma_pts
        sigma_reb = 2.0 if pd.isna(sigma_reb) else sigma_reb
        sigma_ast = 2.0 if pd.isna(sigma_ast) else sigma_ast

        return {
            'baseline_pts_min': baseline_pts_min,
            'baseline_reb_min': baseline_reb_min,
            'baseline_ast_min': baseline_ast_min,
            'avg_minutes': avg_minutes,
            'sigma_pts': sigma_pts,
            'sigma_reb': sigma_reb,
            'sigma_ast': sigma_ast
        }
    except Exception as e:
        print(f"Error getting baseline for {player_id}: {e}")
        return None

def get_boxscore_snapshot(game_id, end_range):
    """Fetches boxscore for a specific range (0 to end_range)."""
    try:
        box = boxscoretraditionalv3.BoxScoreTraditionalV3(
            game_id=game_id,
            range_type=2,
            start_range=0,
            end_range=end_range
        )
        return box.player_stats.get_data_frame()
    except Exception as e:
        print(f"Error fetching snapshot {end_range}: {e}")
        return pd.DataFrame()

def run_backtest(game_id):
    print(f"Starting Backtest for Game {game_id}...")
    
    # 1. Get Ground Truth (Full Game)
    print("Fetching Full Game Results...")
    full_box = boxscoretraditionalv3.BoxScoreTraditionalV3(game_id=game_id)
    full_df = full_box.player_stats.get_data_frame()
    
    if full_df.empty:
        print("Could not fetch full game stats.")
        return

    # Filter for significant players (e.g., > 20 mins)
    # Note: V3 column names are camelCase usually
    # Let's inspect columns if needed, but assuming standard V3: 'minutes', 'personId', 'firstName', 'familyName'
    
    # Convert minutes string "32:10" to float
    def parse_minutes(min_str):
        try:
            parts = min_str.split(':')
            return int(parts[0]) + int(parts[1])/60
        except:
            return 0.0

    full_df['min_float'] = full_df['minutes'].apply(parse_minutes)
    significant_players = full_df[full_df['min_float'] > 20]
    
    print(f"Found {len(significant_players)} players with > 20 mins.")
    
    # 2. Define Checkpoints
    # Q1 End: 12 mins = 7200 tenths
    # Halftime: 24 mins = 14400 tenths
    # Q3 End: 36 mins = 21600 tenths
    checkpoints = {
        "Q1 End": 7200,
        "Halftime": 14400,
        "Q3 End": 21600
    }
    
    snapshots = {}
    for label, rng in checkpoints.items():
        print(f"Fetching {label} snapshot...")
        snapshots[label] = get_boxscore_snapshot(game_id, rng)
        time.sleep(API_DELAY)

    # 3. Run Analysis
    print("\n--- ANALYSIS ---")
    
    for _, player in significant_players.iterrows():
        player_id = player['personId']
        name = f"{player['firstName']} {player['familyName']}"
        final_stats = {
            'PTS': player['points'],
            'REB': player['reboundsTotal'],
            'AST': player['assists']
        }
        final_min = player['min_float']
        
        print(f"\nPlayer: {name} (Final: {final_stats['PTS']} PTS, {final_stats['REB']} REB, {final_stats['AST']} AST in {final_min:.1f} min)")
        
        baseline = get_player_baseline(player_id)
        if not baseline:
            print("  No baseline data found.")
            continue
            
        print(f"  Baseline: {baseline['baseline_pts_min']*baseline['avg_minutes']:.1f} PTS, {baseline['baseline_reb_min']*baseline['avg_minutes']:.1f} REB, {baseline['baseline_ast_min']*baseline['avg_minutes']:.1f} AST (Avg Min: {baseline['avg_minutes']:.1f})")
        
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
            
            # Calculate Prediction
            # Alpha
            alpha = PredictionEngine.calculate_alpha(cur_min, baseline['avg_minutes'])
            
            # Remaining Min (Estimate based on Avg)
            rm = baseline['avg_minutes'] - cur_min
            if rm < 0: rm = 0
            
            # Process each stat type
            stat_types = [
                ('PTS', 'baseline_pts_min', 'sigma_pts'),
                ('REB', 'baseline_reb_min', 'sigma_reb'),
                ('AST', 'baseline_ast_min', 'sigma_ast')
            ]
            
            print(f"  @{label:<8} (Min={cur_min:<4.1f}):")
            for stat_name, base_key, sigma_key in stat_types:
                cur_val = cur_stats[stat_name]
                final_val = final_stats[stat_name]
                
                # PFS
                pfs = PredictionEngine.calculate_pfs(
                    cur_val, cur_min, baseline[base_key], rm, alpha
                )
                
                # Range
                low, high, sigma = PredictionEngine.get_prediction_range(
                    pfs, baseline[sigma_key], cur_min, baseline['avg_minutes']
                )
                
                # Error
                error = pfs - final_val
                
                print(f"    {stat_name:<3}: Cur={cur_val:<2} | PFS={pfs:<4.1f} Range=[{low:<4.1f}-{high:<4.1f}] | Act={final_val} | Err={error:+.1f}")

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        game_id = sys.argv[1]
        run_backtest(game_id)
    else:
        print("Usage: python backtest_game.py <GAME_ID>")
        # Default for testing
        # run_backtest("0022500324")
