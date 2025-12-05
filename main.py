import time
import schedule
from datetime import datetime
from researcher import Researcher
from poller import Poller

# Track active pollers: {game_id: PollerThread}
active_pollers = {}

def job_research():
    """Runs the daily research task."""
    print(f"[{datetime.now()}] Running Daily Research Task...")
    r = Researcher()
    r.run()

def job_check_games():
    """Checks for live games and spawns pollers."""
    print(f"[{datetime.now()}] Checking for live games...")
    
    # We reuse the Researcher's method to get today's games, 
    # or we could make a lighter weight check. 
    # For simplicity, let's just instantiate Researcher to get the schedule.
    # In a real app, we might cache the schedule.
    r = Researcher()
    r.fetch_todays_games()
    
    for game in r.today_games:
        game_id = game['GAME_ID']
        
        # If poller already running, skip
        if game_id in active_pollers:
            if not active_pollers[game_id].is_alive():
                print(f"Poller for {game_id} finished. Removing.")
                del active_pollers[game_id]
            continue

        # Start new poller
        # Note: In a real scenario, we'd check if the game status is actually live 
        # BEFORE spawning the thread to save resources, but the Poller class 
        # handles the "wait until live" logic too (it checks status).
        # However, spawning threads for games 5 hours away is wasteful.
        # Let's assume we only spawn if it's close to start time or live.
        # For this MVP, we'll spawn and let the Poller sleep if not live.
        
        print(f"Spawning Poller for Game {game_id}")
        p = Poller(game_id, game['HOME_TEAM_ID'], game['VISITOR_TEAM_ID'])
        p.start()
        active_pollers[game_id] = p

def main():
    print("NBA Prop Bet Bot Started.")
    
    # Run research immediately on startup to populate baselines
    job_research()
    job_check_games()

    # Schedule daily research
    schedule.every().day.at("08:00").do(job_research)

    # Schedule game checks every 5 minutes
    # schedule.every(5).minutes.do(job_check_games)

    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        print("Stopping bot...")
        # Stop all pollers
        for gid, poller in active_pollers.items():
            poller.running = False
            poller.join()
        print("Bot stopped.")
        raise SystemExit

if __name__ == "__main__":
    main()
