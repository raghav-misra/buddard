import time
import json
import pandas as pd
import os
from datetime import datetime
from nba_api.stats.endpoints import scoreboardv2, playercareerstats, playergamelog, commonteamroster, leaguedashteamstats
from nba_api.stats.static import players
import lib.constants as constants

class Researcher:
    def __init__(self):
        self.today_games = []
        self.player_baselines = {}
        self.team_def_ratings = {}

    def run(self):
        print(f"[{datetime.now()}] Starting Researcher...")
        
        if self.check_existing_baselines():
            print("Existing baselines found for today. Skipping re-calculation.")
            return

        self.fetch_todays_games()
        if not self.today_games:
            print("No games found for today.")
            return

        self.fetch_team_defense()
        self.build_baselines()
        self.save_baselines()
        print(f"[{datetime.now()}] Researcher completed. Baselines saved.")

    def fetch_team_defense(self):
        """Fetches team defensive ratings and pace to adjust baselines."""
        print("Fetching Team Stats (Defense & Pace)...")
        try:
            # 1. Fetch Advanced Team Stats (for PACE)
            teams_adv = leaguedashteamstats.LeagueDashTeamStats(
                season='2024-25',
                measure_type_detailed_defense='Advanced',
                timeout=10
            )
            df_adv = teams_adv.league_dash_team_stats.get_data_frame()
            time.sleep(constants.API_DELAY)

            # 2. Fetch Opponent Stats (for OPP_PTS, OPP_REB, OPP_AST)
            teams_opp = leaguedashteamstats.LeagueDashTeamStats(
                season='2024-25',
                measure_type_detailed_defense='Opponent',
                timeout=10
            )
            df_opp = teams_opp.league_dash_team_stats.get_data_frame()
            time.sleep(constants.API_DELAY)
            
            if not df_adv.empty and not df_opp.empty:
                # Calculate League Averages
                avg_pace = df_adv['PACE'].mean()
                avg_opp_pts = df_opp['OPP_PTS'].mean()
                avg_opp_reb = df_opp['OPP_REB'].mean()
                avg_opp_ast = df_opp['OPP_AST'].mean()

                print(f"League Averages - Pace: {avg_pace:.2f}, OppPTS: {avg_opp_pts:.1f}, OppREB: {avg_opp_reb:.1f}, OppAST: {avg_opp_ast:.1f}")

                # Merge or iterate to store
                # Both DFs should have TEAM_ID as unique identifier
                
                for _, row in df_adv.iterrows():
                    team_id = row['TEAM_ID']
                    # Find corresponding row in df_opp
                    opp_row = df_opp[df_opp['TEAM_ID'] == team_id].iloc[0]
                    
                    self.team_def_ratings[team_id] = {
                        'pace': row['PACE'],
                        'opp_pts': opp_row['OPP_PTS'],
                        'opp_reb': opp_row['OPP_REB'],
                        'opp_ast': opp_row['OPP_AST'],
                        # Store averages for easy access later
                        'league_avg_pace': avg_pace,
                        'league_avg_opp_pts': avg_opp_pts,
                        'league_avg_opp_reb': avg_opp_reb,
                        'league_avg_opp_ast': avg_opp_ast
                    }
                print(f"Fetched detailed stats for {len(self.team_def_ratings)} teams.")
            else:
                print("Could not fetch team stats.")
            
        except Exception as e:
            print(f"Error fetching team defense: {e}")

    def check_existing_baselines(self):
        """Checks if valid baselines for today already exist."""
        if not os.path.exists(constants.BASELINES_FILE):
            return False
        try:
            with open(constants.BASELINES_FILE, 'r') as f:
                data = json.load(f)
            
            # Check metadata
            if '_meta' in data:
                saved_date = data['_meta'].get('date')
                today = datetime.now().strftime('%Y-%m-%d')
                if saved_date == today:
                    return True
            return False
        except Exception as e:
            print(f"Error checking baselines: {e}")
            return False

    def fetch_todays_games(self):
        """Fetches the schedule for today."""
        print("Fetching today's schedule...")
        try:
            # ScoreboardV2 gets games for a specific date
            board = scoreboardv2.ScoreboardV2(game_date=datetime.now().strftime('%Y-%m-%d'), timeout=10)
            games_df = board.game_header.get_data_frame()
            
            # Filter for games that haven't finished (though usually we run this in AM)
            # For simplicity, we take all games listed for the day
            self.today_games = games_df[['GAME_ID', 'HOME_TEAM_ID', 'VISITOR_TEAM_ID']].to_dict('records')
            print(f"Found {len(self.today_games)} games.")
            time.sleep(constants.API_DELAY)
        except Exception as e:
            print(f"Error fetching schedule: {e}")

    def build_baselines(self):
        """Iterates through games and players to build statistical baselines."""
        for game in self.today_games:
            game_id = game['GAME_ID']
            home_team = game['HOME_TEAM_ID']
            visitor_team = game['VISITOR_TEAM_ID']
            
            print(f"Processing Game ID: {game_id}")
            # Process Home Team (vs Visitor)
            self._process_team(home_team, is_home=True, opponent_id=visitor_team)
            # Process Visitor Team (vs Home)
            self._process_team(visitor_team, is_home=False, opponent_id=home_team)

    def _process_team(self, team_id, is_home, opponent_id):
        """Fetches roster and stats for a specific team."""
        try:
            roster = commonteamroster.CommonTeamRoster(team_id=team_id, timeout=10)
            roster_df = roster.common_team_roster.get_data_frame()
            time.sleep(constants.API_DELAY)

            for _, player in roster_df.iterrows():
                player_id = player['PLAYER_ID']
                player_name = player['PLAYER']
                
                # Skip if we already have data (e.g. player traded or duplicate check)
                if str(player_id) in self.player_baselines:
                    continue

                print(f"  Analyzing {player_name} ({player_id})...")
                stats = self._get_player_stats(player_id, is_home, opponent_id)
                if stats:
                    self.player_baselines[str(player_id)] = {
                        'name': player_name,
                        'team_id': team_id,
                        'stats': stats
                    }
        except Exception as e:
            print(f"Error processing team {team_id}: {e}")

    def _get_player_stats(self, player_id, is_home, opponent_id):
        """Calculates baseline pace and standard deviation for a player."""
        try:
            # 1. Get Season Averages (Baseline Pace)
            career = playercareerstats.PlayerCareerStats(player_id=player_id, timeout=10)
            season_df = career.season_totals_regular_season.get_data_frame()
            time.sleep(constants.API_DELAY)

            if season_df.empty:
                return None

            # Get current season (last row usually, but safer to filter if needed)
            current_season = season_df.iloc[-1]
            
            minutes = current_season['MIN']
            if minutes < 50: # Skip players with very few minutes
                return None

            games_played = current_season['GP']
            
            # Season Per-Minute Baselines
            season_pts_min = current_season['PTS'] / minutes
            season_reb_min = current_season['REB'] / minutes
            season_ast_min = current_season['AST'] / minutes
            avg_minutes = minutes / games_played

            # 2. Get Recent Game Logs for Variance AND Weighted Baseline
            gamelog = playergamelog.PlayerGameLog(player_id=player_id, season='2024-25', timeout=10)
            logs_df = gamelog.player_game_log.get_data_frame()
            time.sleep(constants.API_DELAY)

            # --- Weighted Baseline Calculation ---
            # Weight: 70% Season, 30% Last 5 Games
            # If < 5 games played, rely on season.
            
            if not logs_df.empty:
                last_5 = logs_df.head(5)
                l5_min = last_5['MIN'].sum()
                
                if l5_min > 0:
                    l5_pts_min = last_5['PTS'].sum() / l5_min
                    l5_reb_min = last_5['REB'].sum() / l5_min
                    l5_ast_min = last_5['AST'].sum() / l5_min
                    
                    # Blend
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
            # Simple modifier: +2% for Home, -2% for Away
            ha_modifier = 1.02 if is_home else 0.98
            baseline_pts_min *= ha_modifier
            baseline_reb_min *= ha_modifier
            baseline_ast_min *= ha_modifier

            # --- Opponent DvP & Pace Adjustment ---
            if opponent_id in self.team_def_ratings:
                stats = self.team_def_ratings[opponent_id]
                
                # Pace Modifier: (Opp Pace / League Avg)
                # If opponent plays fast, we get more possessions.
                pace_modifier = stats['pace'] / stats['league_avg_pace']
                
                # Stat Specific Modifiers: (Opp Allowed / League Avg Allowed)
                # If opponent allows 10% more points than average, we expect 10% more points.
                pts_modifier = stats['opp_pts'] / stats['league_avg_opp_pts']
                reb_modifier = stats['opp_reb'] / stats['league_avg_opp_reb']
                ast_modifier = stats['opp_ast'] / stats['league_avg_opp_ast']
                
                # Apply Modifiers
                baseline_pts_min *= (pace_modifier * pts_modifier)
                baseline_reb_min *= (pace_modifier * reb_modifier)
                baseline_ast_min *= (pace_modifier * ast_modifier)

            # --- Variance (Sigma) Calculation ---
            if logs_df.empty:
                sigma_pts = 0
                sigma_reb = 0
                sigma_ast = 0
            else:
                recent_logs = logs_df.head(20)
                sigma_pts = recent_logs['PTS'].std()
                sigma_reb = recent_logs['REB'].std()
                sigma_ast = recent_logs['AST'].std()

            sigma_pts = 0 if pd.isna(sigma_pts) else sigma_pts
            sigma_reb = 0 if pd.isna(sigma_reb) else sigma_reb
            sigma_ast = 0 if pd.isna(sigma_ast) else sigma_ast

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
            print(f"    Error fetching stats for {player_id}: {e}")
            return None

    def save_baselines(self):
        """Saves the calculated baselines to a JSON file."""
        try:
            # Ensure data directory exists
            os.makedirs(constants.DATA_DIR, exist_ok=True)
            
            output = {
                '_meta': {
                    'date': datetime.now().strftime('%Y-%m-%d'),
                    'timestamp': time.time()
                },
                'players': self.player_baselines
            }

            with open(constants.BASELINES_FILE, 'w') as f:
                json.dump(output, f, indent=4)
            print(f"Baselines saved to {constants.BASELINES_FILE}")
        except Exception as e:
            print(f"Error saving baselines: {e}")

if __name__ == "__main__":
    researcher = Researcher()
    researcher.run()
