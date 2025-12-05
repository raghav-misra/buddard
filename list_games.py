import datetime
from nba_api.stats.endpoints import scoreboardv2

def list_games():
    today = datetime.datetime.now().strftime('%Y-%m-%d')
    print(f"Fetching games for {today}...")
    
    try:
        # Fetch Scoreboard
        board = scoreboardv2.ScoreboardV2(game_date=today)
        games = board.game_header.get_data_frame()
        line_score = board.line_score.get_data_frame()
        
        if games.empty:
            print("No games found for today.")
            return

        print(f"\n--- NBA SCHEDULE FOR {today} ---")
        print(f"{'GAME ID':<12} | {'VISITOR':<5} @ {'HOME':<5} | {'STATUS'}")
        print("-" * 45)

        for _, game in games.iterrows():
            game_id = game['GAME_ID']
            home_id = game['HOME_TEAM_ID']
            visitor_id = game['VISITOR_TEAM_ID']
            status = game['GAME_STATUS_TEXT'] # e.g. "7:30 pm ET" or "Final"
            
            home_team = game["GAMECODE"][-3:] 
            visitor_team = game["GAMECODE"][-6:-3]
            
            if not line_score.empty:
                h_data = line_score[line_score['teamId  '] == home_id]
                v_data = line_score[line_score['TEAM_ID'] == visitor_id]
                
                if not h_data.empty:
                    home_team = h_data.iloc[0]['TEAM_ABBREVIATION']
                if not v_data.empty:
                    visitor_team = v_data.iloc[0]['TEAM_ABBREVIATION']

            print(f"{game_id:<12} | {visitor_team:<5} @ {home_team:<5} | {status}")
        print("-" * 45)

    except Exception as e:
        print(f"Error fetching schedule: {e}")

if __name__ == "__main__":
    list_games()
