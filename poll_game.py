import argparse
import time
from poller import Poller
from nba_api.live.nba.endpoints import boxscore

def start_poller(game_id):
    print(f"Initializing poller for Game ID: {game_id}")
    p = None
    
    try:
        # 1. Fetch Game Details to get Team IDs
        print("Fetching game details from NBA Live API...")
        try:
            box = boxscore.BoxScore(game_id=game_id)
            data = box.get_dict()
        except Exception as e:
            print(f"Error fetching boxscore: {e}")
            print("Ensure the Game ID is correct and the game is scheduled for today.")
            return

        # Check if data is valid (sometimes API returns empty if too early)
        if 'game' not in data:
            print("Invalid API response. Game data not found.")
            return

        home_team_id = data['game']['homeTeam']['teamId']
        visitor_team_id = data['game']['awayTeam']['teamId']
        
        home_name = data['game']['homeTeam']['teamName']
        visitor_name = data['game']['awayTeam']['teamName']
        
        print(f"Matchup Found: {visitor_name} @ {home_name}")
        print("Starting Poller Thread...")
        print("Press Ctrl+C to stop.")
        print("-" * 30)

        # 2. Start Poller
        # We run the poller in a separate thread (as designed), 
        # but keep the main script alive to handle shutdown.
        p = Poller(game_id, home_team_id, visitor_team_id)
        p.start()

        # 3. Keep Main Thread Alive
        while p.is_alive():
            p.join(1.0) # Join with timeout to allow KeyboardInterrupt

    except KeyboardInterrupt:
        print("\n[User Interrupt] Stopping poller...")
        if p:
          p.running = False
          p.join()
        print("Poller stopped safely.")
    except Exception as e:
        print(f"Unexpected error: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Start the Buddard Prediction Poller for a specific game.')
    parser.add_argument('game_id', type=str, help='The 10-digit NBA Game ID (e.g., 0022400123)')
    
    args = parser.parse_args()
    start_poller(args.game_id)
