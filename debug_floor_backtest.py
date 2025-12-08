import time
import pandas as pd
from collections import defaultdict
from nba_api.stats.endpoints import scoreboardv2, boxscoretraditionalv3, playercareerstats, playergamelog, leaguedashteamstats
from lib.prediction_engine import PredictionEngine
import lib.constants as constants
import json
import os
import datetime

# Constants
SEASON = '2025-26' 
API_DELAY = 0.6
TEAM_DEF_RATINGS = {}
BASELINE_CACHE = {}

def fetch_team_defense():
    global TEAM_DEF_RATINGS
    print("Fetching Team Stats (Defense & Pace)...")
    try:
        teams_adv = leaguedashteamstats.LeagueDashTeamStats(season=SEASON, measure_type_detailed_defense='Advanced', timeout=10)
        df_adv = teams_adv.league_dash_team_stats.get_data_frame()
        time.sleep(API_DELAY)

        teams_opp = leaguedashteamstats.LeagueDashTeamStats(season=SEASON, measure_type_detailed_defense='Opponent', timeout=10)
        df_opp = teams_opp.league_dash_team_stats.get_data_frame()
        time.sleep(API_DELAY)
        
        if not df_adv.empty and not df_opp.empty:
            avg_pace = df_adv['PACE'].mean()
            avg_opp_pts = df_opp['OPP_PTS'].mean()
            avg_opp_reb = df_opp['OPP_REB'].mean()
            avg_opp_ast = df_opp['OPP_AST'].mean()

            for _, row in df_adv.iterrows():
                team_id = row['TEAM_ID']
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
    except Exception as e:
        print(f"Error fetching team defense: {e}")

def get_player_baseline(player_id, game_date, is_home, opponent_id):
    cache_key = f"{player_id}_{game_date}_{is_home}_{opponent_id}"
    if cache_key in BASELINE_CACHE: return BASELINE_CACHE[cache_key]

    try:
        career = playercareerstats.PlayerCareerStats(player_id=player_id)
        season_df = career.season_totals_regular_season.get_data_frame()
        time.sleep(API_DELAY)

        if season_df.empty: return None
        current_season = season_df.iloc[-1]
        minutes = current_season['MIN']
        if minutes < 50: return None
        games_played = current_season['GP']
        
        season_pts_min = current_season['PTS'] / minutes
        season_reb_min = current_season['REB'] / minutes
        season_ast_min = current_season['AST'] / minutes
        avg_minutes = minutes / games_played

        gamelog = playergamelog.PlayerGameLog(player_id=player_id, season=SEASON)
        logs_df = gamelog.player_game_log.get_data_frame()
        time.sleep(API_DELAY)

        if not logs_df.empty:
            logs_df['date_dt'] = pd.to_datetime(logs_df['GAME_DATE'])
            target_dt = pd.to_datetime(game_date)
            past_logs = logs_df[logs_df['date_dt'] < target_dt]
        else:
            past_logs = pd.DataFrame()

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
                baseline_pts_min, baseline_reb_min, baseline_ast_min = season_pts_min, season_reb_min, season_ast_min
        else:
            baseline_pts_min, baseline_reb_min, baseline_ast_min = season_pts_min, season_reb_min, season_ast_min

        ha_modifier = 1.02 if is_home else 0.98
        baseline_pts_min *= ha_modifier
        baseline_reb_min *= ha_modifier
        baseline_ast_min *= ha_modifier

        if opponent_id in TEAM_DEF_RATINGS:
            stats = TEAM_DEF_RATINGS[opponent_id]
            pace_modifier = stats['pace'] / stats['league_avg_pace']
            baseline_pts_min *= (pace_modifier * (stats['opp_pts'] / stats['league_avg_opp_pts']))
            baseline_reb_min *= (pace_modifier * (stats['opp_reb'] / stats['league_avg_opp_reb']))
            baseline_ast_min *= (pace_modifier * (stats['opp_ast'] / stats['league_avg_opp_ast']))

        if past_logs.empty:
            sigma_pts, sigma_reb, sigma_ast = 5.0, 2.0, 2.0
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
    except Exception:
        return None

def get_boxscore_snapshot(game_id, end_range):
    try:
        box = boxscoretraditionalv3.BoxScoreTraditionalV3(game_id=game_id, range_type="2", start_range="0", end_range=str(end_range))
        return box.player_stats.get_data_frame()
    except Exception:
        return pd.DataFrame()

def parse_minutes(min_str):
    try:
        parts = min_str.split(':')
        return int(parts[0]) + int(parts[1])/60
    except:
        return 0.0

def analyze_game(game_info, game_date):
    game_id = game_info['GAME_ID']
    home_team_id = game_info['HOME_TEAM_ID']
    visitor_team_id = game_info['VISITOR_TEAM_ID']
    
    print(f"Analyzing Game {game_id}...")
    
    try:
        full_box = boxscoretraditionalv3.BoxScoreTraditionalV3(game_id=game_id)
        full_df = full_box.player_stats.get_data_frame()
    except Exception:
        return

    if full_df.empty: return
    full_df['min_float'] = full_df['minutes'].apply(parse_minutes)
    significant_players = full_df[full_df['min_float'] > 20]
    if significant_players.empty: return

    # Only look at Q3 (End of Q3 is 21600 tenths of seconds = 36 mins)
    q3_snapshot = get_boxscore_snapshot(game_id, 21600)
    if q3_snapshot.empty: return

    for _, player in significant_players.iterrows():
        player_id = player['personId']
        name = f"{player['firstName']} {player['familyName']}"
        team_id = player['teamId']
        is_home = (team_id == home_team_id)
        opponent_id = visitor_team_id if is_home else home_team_id
        
        baseline = get_player_baseline(player_id, game_date, is_home, opponent_id)
        if not baseline: continue

        final_stats = {'PTS': player['points'], 'REB': player['reboundsTotal'], 'AST': player['assists']}
        final_min = player['min_float']

        p_snap = q3_snapshot[q3_snapshot['personId'] == player_id]
        if p_snap.empty:
            cur_stats = {'PTS': 0, 'REB': 0, 'AST': 0}
            cur_min = 0.0
            cur_fouls = 0
        else:
            p_data = p_snap.iloc[0]
            cur_stats = {'PTS': p_data['points'], 'REB': p_data['reboundsTotal'], 'AST': p_data['assists']}
            cur_min = parse_minutes(p_data['minutes'])
            cur_fouls = p_data['foulsPersonal']

        team_scores = q3_snapshot.groupby('teamId')['points'].sum()
        my_score = team_scores.get(team_id, 0)
        opp_score = team_scores.get(opponent_id, 0)
        score_diff = my_score - opp_score

        # Calculate Prediction
        if cur_min > 0:
            cur_pace_pts = cur_stats['PTS'] / cur_min
        else:
            cur_pace_pts = 0
            
        perf_factor = PredictionEngine.calculate_performance_factor(cur_pace_pts, baseline['baseline_pts_min'])
        
        rm = PredictionEngine.calculate_dynamic_remaining_minutes(
            baseline['avg_minutes'], cur_min, cur_fouls, score_diff, 3, perf_factor
        )

        stat_types = [('PTS', 'baseline_pts_min', 'sigma_pts'), ('REB', 'baseline_reb_min', 'sigma_reb'), ('AST', 'baseline_ast_min', 'sigma_ast')]

        for stat_name, base_key, sigma_key in stat_types:
            pfs = PredictionEngine.calculate_pfs(cur_stats[stat_name], baseline[base_key], rm, 3)
            low, high, sigma = PredictionEngine.get_prediction_range(pfs, baseline[sigma_key], cur_min, baseline['avg_minutes'], cur_stats[stat_name])
            
            # Check for Floor Failure (Actual < Floor)
            if final_stats[stat_name] < low:
                print(f"\n[FLOOR MISS] {name} - {stat_name} (Q3)")
                print(f"  Floor: {low:.1f} | Actual: {final_stats[stat_name]} | PFS: {pfs:.1f}")
                print(f"  Q3 Stats: {cur_stats[stat_name]} in {cur_min:.1f} min")
                print(f"  Minutes: Avg={baseline['avg_minutes']:.1f} | Final={final_min:.1f} | Proj Rem={rm:.1f} | Act Rem={final_min - cur_min:.1f}")
                print(f"  Context: Diff={score_diff} | Fouls={cur_fouls} | PerfFactor={perf_factor:.2f}")
                print(f"  Pace: Base={baseline[base_key]:.2f} | Cur={cur_stats[stat_name]/cur_min if cur_min>0 else 0:.2f}")
                
                # Diagnosis
                reasons = []
                if (final_min - cur_min) < (rm - 2): reasons.append("Played less than projected")
                if (final_min - cur_min) > (rm + 2): reasons.append("Played MORE than projected (Efficiency Drop?)")
                if abs(score_diff) > 20: reasons.append("Blowout")
                if cur_fouls >= 4: reasons.append("Foul Trouble")
                if perf_factor > 1.2: reasons.append("Hot Hand Regression")
                
                print(f"  Diagnosis: {', '.join(reasons)}")
            else:
                print(f"[FLOOR HIT] {name} - {stat_name} (Q3) | Floor: {low:.1f} <= Actual: {final_stats[stat_name]}")

def main():
    fetch_team_defense()
    # Get games from yesterday or a specific date to test
    # Let's try to find a date with games. 
    # Since I don't know the exact date of the 9 games, I'll try the last few days.
    
    today = datetime.date.today()
    dates = [today - datetime.timedelta(days=i) for i in range(1, 4)]
    
    for d in dates:
        date_str = d.strftime('%Y-%m-%d')
        print(f"\nChecking {date_str}...")
        try:
            board = scoreboardv2.ScoreboardV2(game_date=date_str)
            games = board.game_header.get_data_frame()
            if not games.empty:
                game_list = games[['GAME_ID', 'HOME_TEAM_ID', 'VISITOR_TEAM_ID']].to_dict('records')
                for game in game_list:
                    analyze_game(game, date_str)
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    main()
