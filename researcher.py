import time
import json
import pandas as pd
from datetime import datetime
from nba_api.stats.endpoints import scoreboardv2, playercareerstats, playergamelog, commonteamroster
from nba_api.stats.static import players
import constants

class Researcher:
    def __init__(self):
        self.today_games = []
        self.player_baselines = {}

    def run(self):
        print(f"[{datetime.now()}] Starting Researcher...")
        self.fetch_todays_games()
        if not self.today_games:
            print("No games found for today.")
            return

        self.build_baselines()
        self.save_baselines()
        print(f"[{datetime.now()}] Researcher completed. Baselines saved.")

    def fetch_todays_games(self):
        """Fetches the schedule for today."""
        print("Fetching today's schedule...")
        try:
            # ScoreboardV2 gets games for a specific date
            board = scoreboardv2.ScoreboardV2(game_date=datetime.now().strftime('%Y-%m-%d'))
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
            self._process_team(home_team)
            self._process_team(visitor_team)

    def _process_team(self, team_id):
        """Fetches roster and stats for a specific team."""
        try:
            roster = commonteamroster.CommonTeamRoster(team_id=team_id)
            roster_df = roster.common_team_roster.get_data_frame()
            time.sleep(constants.API_DELAY)

            for _, player in roster_df.iterrows():
                player_id = player['PLAYER_ID']
                player_name = player['PLAYER']
                
                # Skip if we already have data (e.g. player traded or duplicate check)
                if str(player_id) in self.player_baselines:
                    continue

                print(f"  Analyzing {player_name} ({player_id})...")
                stats = self._get_player_stats(player_id)
                if stats:
                    self.player_baselines[str(player_id)] = {
                        'name': player_name,
                        'team_id': team_id,
                        'stats': stats
                    }
        except Exception as e:
            print(f"Error processing team {team_id}: {e}")

    def _get_player_stats(self, player_id):
        """Calculates baseline pace and standard deviation for a player."""
        try:
            # 1. Get Season Averages (Baseline Pace)
            career = playercareerstats.PlayerCareerStats(player_id=player_id)
            season_df = career.season_totals_regular_season.get_data_frame()
            time.sleep(constants.API_DELAY)

            if season_df.empty:
                return None

            # Get current season (last row usually, but safer to filter if needed)
            # Assuming the last row is the current season for simplicity in this MVP
            current_season = season_df.iloc[-1]
            
            # Avoid division by zero
            minutes = current_season['MIN']
            if minutes < 50: # Skip players with very few minutes
                return None

            games_played = current_season['GP']
            
            # Calculate Per-Minute Baselines
            baseline_pts_min = current_season['PTS'] / minutes
            baseline_reb_min = current_season['REB'] / minutes
            baseline_ast_min = current_season['AST'] / minutes
            avg_minutes = minutes / games_played

            # 2. Get Recent Game Logs for Variance (Sigma)
            # We use the last 10 games for variance calculation
            gamelog = playergamelog.PlayerGameLog(player_id=player_id, season='2024-25') # Update season dynamically in prod
            logs_df = gamelog.player_game_log.get_data_frame()
            time.sleep(constants.API_DELAY)

            if logs_df.empty:
                sigma_pts = 0
                sigma_reb = 0
                sigma_ast = 0
            else:
                # Calculate Standard Deviation of the raw totals
                # Note: We use std dev of the *totals*, not per-minute, as the range is on the final total.
                recent_logs = logs_df.head(20) # Last 20 games
                sigma_pts = recent_logs['PTS'].std()
                sigma_reb = recent_logs['REB'].std()
                sigma_ast = recent_logs['AST'].std()

            # Handle NaN from std() if only 1 game played
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
            import os
            os.makedirs(constants.DATA_DIR, exist_ok=True)
            
            with open(constants.BASELINES_FILE, 'w') as f:
                json.dump(self.player_baselines, f, indent=4)
            print(f"Baselines saved to {constants.BASELINES_FILE}")
        except Exception as e:
            print(f"Error saving baselines: {e}")

if __name__ == "__main__":
    researcher = Researcher()
    researcher.run()
