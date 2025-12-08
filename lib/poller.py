import time
import json
import threading
import math
from nba_api.live.nba.endpoints import boxscore
import lib.constants as constants
from lib.notifier import Notifier
from lib.prediction_engine import PredictionEngine

class Poller(threading.Thread):
    def __init__(self, game_id, home_team_id, visitor_team_id):
        super().__init__()
        self.game_id = game_id
        self.home_team_id = home_team_id
        self.visitor_team_id = visitor_team_id
        self.running = True
        self.baselines = self._load_baselines()
        self.notifier = Notifier()
        self.alerted_players = set()  # Track alerted players to avoid spam

    def _load_baselines(self):
        try:
            with open(constants.BASELINES_FILE, "r") as f:
                data = json.load(f)
                # Handle new format with metadata
                if "_meta" in data and "players" in data:
                    return data["players"]
                return data
        except FileNotFoundError:
            print("Baselines file not found. Run researcher first.")
            return {}

    def run(self):
        print(f"Starting Poller for Game {self.game_id}...")
        while self.running:
            try:
                self.poll()
                time.sleep(30)  # Poll every 30 seconds
            except Exception as e:
                print(f"Error in Poller {self.game_id}", e)
                time.sleep(30)

    def poll(self):
        # Fetch live box score
        # Note: Using the live endpoint which is faster and lighter than stats endpoint
        try:
            # Set a timeout to prevent hanging
            box = boxscore.BoxScore(game_id=self.game_id, timeout=10)
            data = box.get_dict()
        except Exception as e:
            # If the game hasn't started, the API returns XML (403/404) which fails JSON parsing.
            # This is normal behavior for pre-game.
            # However, we should log if it's a timeout or other error to help debugging.
            if "timeout" in str(e).lower():
                print(f"Game {self.game_id} poll timed out.")
            elif "Expecting value" in str(e): # JSONDecodeError
                print(f"Game {self.game_id} not active yet (JSON error).")
            else:
                print(f"Game {self.game_id} poll failed: {e}")
            return

        game_status = data["game"]["gameStatus"]  # 1=Not Started, 2=Live, 3=Final

        if game_status == 3:
            print(f"Game {self.game_id} is Final. Stopping Poller.")
            self.running = False
            return

        if game_status != 2:
            print(f"Game {self.game_id} not live yet.")
            return

        # Game Flow Data
        period = data["game"]["period"]
        clock = data["game"][
            "gameClock"
        ]  # String "PT10M00.00S" or similar, need to parse if precise, but period is enough for Alpha

        print(f"[Game {self.game_id}] Live - Q{period} {clock} - Scanning players...")

        home_score = data["game"]["homeTeam"]["score"]
        away_score = data["game"]["awayTeam"]["score"]
        score_diff = abs(home_score - away_score)
        winning_team_id = (
            self.home_team_id if home_score > away_score else self.visitor_team_id
        )

        # Process Players
        all_players = (
            data["game"]["homeTeam"]["players"] + data["game"]["awayTeam"]["players"]
        )

        for player in all_players:
            self.process_player(player, period, score_diff, winning_team_id)

    def process_player(self, player_data, period, score_diff, winning_team_id):
        player_id = str(player_data["personId"])
        name = player_data["name"]

        if player_id not in self.baselines:
            return

        baseline = self.baselines[player_id]["stats"]

        # Parse Current Stats
        stats = player_data["statistics"]
        minutes_str = stats["minutes"]

        try:
            time_str = minutes_str.replace('PT', '')
            minutes = 0
            seconds = 0
            
            if 'M' in time_str:
                parts = time_str.split('M')
                minutes = int(parts[0])
                time_str = parts[1]
                
            if 'S' in time_str:
                seconds = float(time_str.replace('S', ''))
                
            minutes_played = minutes + (seconds / 60.0)
        except:
            minutes_played = 0.0

        if minutes_played < 1:
            return

        current_pts = stats["points"]
        current_reb = stats["reboundsTotal"]
        current_ast = stats["assists"]
        current_fouls = stats["foulsPersonal"]
        avg_minutes = baseline["avg_minutes"]

        # --- 1. Calculate Performance Factor (Hot Hand) ---
        # Use PTS pace as the primary driver for minutes adjustment
        current_pace_pts = current_pts / minutes_played
        perf_factor = PredictionEngine.calculate_performance_factor(current_pace_pts, baseline["baseline_pts_min"])

        # --- 2. Calculate Expected Remaining Minutes ---
        rm = PredictionEngine.calculate_dynamic_remaining_minutes(
            avg_minutes, minutes_played, current_fouls, score_diff, period, perf_factor
        )

        # Reasoning Flags
        reasoning_flags = []
        if current_fouls >= constants.FOUL_TROUBLE_THRESHOLD and period in [2, 3]:
            reasoning_flags.append("Foul Trouble")
        
        team_id = self.baselines[player_id]["team_id"]
        if (score_diff > constants.BLOWOUT_DIFF_THRESHOLD and period >= 3 and team_id == winning_team_id):
            reasoning_flags.append("Blowout Risk")
            
        if perf_factor > 1.2:
            reasoning_flags.append("Hot Hand")

        # --- 3. Calculate PFS (Projected Final Stat) ---
        pfs_pts = PredictionEngine.calculate_pfs(current_pts, baseline["baseline_pts_min"], rm, period)
        pfs_reb = PredictionEngine.calculate_pfs(current_reb, baseline["baseline_reb_min"], rm, period)
        pfs_ast = PredictionEngine.calculate_pfs(current_ast, baseline["baseline_ast_min"], rm, period)

        # --- 4. Check Triggers ---
        self._check_trigger(player_id, name, "PTS", pfs_pts, baseline["sigma_pts"], current_pts, minutes_played, period, reasoning_flags, perf_factor, baseline["baseline_pts_min"] * avg_minutes, avg_minutes)
        self._check_trigger(player_id, name, "REB", pfs_reb, baseline["sigma_reb"], current_reb, minutes_played, period, reasoning_flags, perf_factor, baseline["baseline_reb_min"] * avg_minutes, avg_minutes)
        self._check_trigger(player_id, name, "AST", pfs_ast, baseline["sigma_ast"], current_ast, minutes_played, period, reasoning_flags, perf_factor, baseline["baseline_ast_min"] * avg_minutes, avg_minutes)


    def _check_trigger(
        self,
        player_id,
        name,
        stat_type,
        pfs,
        sigma,
        current_val,
        minutes,
        period,
        flags,
        perf_factor,
        season_avg,
        player_avg_minutes
    ):
        # Calculate Range using Engine
        low, high, adjusted_sigma = PredictionEngine.get_prediction_range(
            pfs, sigma, minutes, player_avg_minutes, current_val
        )
        
        # Dynamic Thresholds
        # High Threshold: 80% of Season Avg (Alert if we are sure to beat this)
        threshold_high = season_avg * 0.8
        
        if stat_type == 'PTS':
            threshold_high = max(threshold_high, 7)
            buffer = constants.BUFFER_PTS
        elif stat_type == 'REB':
            threshold_high = max(threshold_high, 3)
            buffer = constants.BUFFER_REB
        elif stat_type == 'AST':
            threshold_high = max(threshold_high, 3)
            buffer = constants.BUFFER_AST
        else:
            buffer = 0

        alert_key = f"{player_id}_{stat_type}_{period}"

        # Debug Log
        if low > (threshold_high * 0.5):
            p50 = low + 0.50 * (high - low)
            print(f"[DEBUG] {name} {stat_type}: Cur={current_val} PFS={pfs:.1f} Range=[{low:.1f}-{high:.1f}] P50={p50:.1f} Perf={perf_factor:.2f}")

        if alert_key in self.alerted_players:
            return

        # HIGH Alert (Entire range is ABOVE threshold)
        if low > (threshold_high + buffer):
            reasoning = f"Q{period} Perf={perf_factor:.2f}. " + ", ".join(flags)
            p50 = low + 0.50 * (high - low)
            self.notifier.send_alert(name, stat_type, "HIGH", current_val, minutes, (low, high), reasoning, p50)
            self.alerted_players.add(alert_key)
            
        # LOW Alert (Entire range is BELOW threshold)
        elif high < (threshold_high - buffer):
             # Only alert LOW if significant minutes played to avoid early game noise
            if minutes > (player_avg_minutes * 0.4):
                reasoning = f"Q{period} Perf={perf_factor:.2f}. " + ", ".join(flags)
                p50 = low + 0.50 * (high - low)
                self.notifier.send_alert(name, stat_type, "LOW", current_val, minutes, (low, high), reasoning, p50)
                self.alerted_players.add(alert_key)
