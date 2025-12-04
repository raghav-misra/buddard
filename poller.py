import time
import json
import threading
from nba_api.live.nba.endpoints import boxscore
import constants
from notifier import Notifier

class Poller(threading.Thread):
    def __init__(self, game_id, home_team_id, visitor_team_id):
        super().__init__()
        self.game_id = game_id
        self.home_team_id = home_team_id
        self.visitor_team_id = visitor_team_id
        self.running = True
        self.baselines = self._load_baselines()
        self.notifier = Notifier()
        self.alerted_players = set() # Track alerted players to avoid spam

    def _load_baselines(self):
        try:
            with open(constants.BASELINES_FILE, 'r') as f:
                data = json.load(f)
                # Handle new format with metadata
                if '_meta' in data and 'players' in data:
                    return data['players']
                return data
        except FileNotFoundError:
            print("Baselines file not found. Run researcher first.")
            return {}

    def run(self):
        print(f"Starting Poller for Game {self.game_id}...")
        while self.running:
            try:
                self.poll()
                time.sleep(30) # Poll every 30 seconds
            except Exception as e:
                print(f"Error in Poller {self.game_id}: {e}")
                time.sleep(30)

    def poll(self):
        # Fetch live box score
        # Note: Using the live endpoint which is faster and lighter than stats endpoint
        box = boxscore.BoxScore(game_id=self.game_id)
        data = box.get_dict()
        
        game_status = data['game']['gameStatus'] # 1=Not Started, 2=Live, 3=Final
        
        if game_status == 3:
            print(f"Game {self.game_id} is Final. Stopping Poller.")
            self.running = False
            return

        if game_status != 2:
            print(f"Game {self.game_id} not live yet.")
            return

        # Game Flow Data
        period = data['game']['period']
        clock = data['game']['gameClock'] # String "PT10M00.00S" or similar, need to parse if precise, but period is enough for Alpha
        
        home_score = data['game']['homeTeam']['score']
        away_score = data['game']['awayTeam']['score']
        score_diff = abs(home_score - away_score)
        winning_team_id = self.home_team_id if home_score > away_score else self.visitor_team_id

        # Process Players
        all_players = data['game']['homeTeam']['players'] + data['game']['awayTeam']['players']
        
        for player in all_players:
            self.process_player(player, period, score_diff, winning_team_id)

    def process_player(self, player_data, period, score_diff, winning_team_id):
        player_id = str(player_data['personId'])
        name = player_data['name']
        
        if player_id not in self.baselines:
            return

        baseline = self.baselines[player_id]['stats']
        
        # Parse Current Stats
        # Live endpoint structure is different from stats endpoint
        stats = player_data['statistics']
        minutes_str = stats['minutes'] # Format "PT12M34.00S"
        
        try:
            # Simple parsing for minutes "PT12M..." -> 12
            # This is a rough parsing, good enough for MVP
            minutes_played = 0
            if 'M' in minutes_str:
                minutes_played = int(minutes_str.split('M')[0].replace('PT', ''))
        except:
            minutes_played = 0

        if minutes_played < 1:
            return

        current_pts = stats['points']
        current_reb = stats['reboundsTotal']
        current_ast = stats['assists']
        current_fouls = stats['foulsPersonal']

        # --- 1. Determine Alpha (Dynamic) ---
        if period == 1:
            alpha = constants.ALPHA_Q1
        elif period in [2, 3]:
            alpha = constants.ALPHA_MID_GAME
        else:
            alpha = constants.ALPHA_Q4

        # --- 2. Calculate Remaining Minutes (RM) ---
        avg_minutes = baseline['avg_minutes']
        
        # Penalty Logic
        penalty = 0
        reasoning_flags = []

        # Foul Trouble
        if current_fouls >= constants.FOUL_TROUBLE_THRESHOLD and period in [2, 3]:
            penalty += constants.FOUL_TROUBLE_PENALTY
            reasoning_flags.append("Foul Trouble")

        # Blowout (Garbage Time) - Only if on winning team
        team_id = self.baselines[player_id]['team_id']
        if score_diff > constants.BLOWOUT_DIFF_THRESHOLD and period >= 3 and team_id == winning_team_id:
            penalty += constants.BLOWOUT_PENALTY
            reasoning_flags.append("Blowout Risk")

        # Projected Total Minutes (PTM) - Simple assumption: PTM is Avg Minutes unless penalized
        # A better model would project PTM based on current rotation, but let's stick to the design doc
        # RM = PTM - CPM - Penalty
        # If PTM is just Avg Minutes:
        rm = avg_minutes - minutes_played - penalty
        if rm < 0: rm = 0

        # --- 3. Calculate PFS (Projected Final Stat) ---
        pfs_pts = self._calculate_pfs(current_pts, minutes_played, baseline['baseline_pts_min'], rm, alpha)
        pfs_reb = self._calculate_pfs(current_reb, minutes_played, baseline['baseline_reb_min'], rm, alpha)
        pfs_ast = self._calculate_pfs(current_ast, minutes_played, baseline['baseline_ast_min'], rm, alpha)

        # --- 4. Check Triggers ---
        self._check_trigger(player_id, name, 'PTS', pfs_pts, baseline['sigma_pts'], current_pts, minutes_played, period, reasoning_flags, alpha)
        # Can add REB and AST triggers similarly

    def _calculate_pfs(self, current_stat, current_min, baseline_pace, remaining_min, alpha):
        current_pace = current_stat / current_min
        weighted_pace = (alpha * current_pace) + ((1 - alpha) * baseline_pace)
        return current_stat + (weighted_pace * remaining_min)

    def _check_trigger(self, player_id, name, stat_type, pfs, sigma, current_val, minutes, period, flags, alpha):
        # Range
        low = pfs - (constants.SIGMA_MULTIPLIER * sigma)
        high = pfs + (constants.SIGMA_MULTIPLIER * sigma)

        # Thresholds
        # For MVP, using default constants. In prod, these would be player-specific.
        threshold_high = constants.DEFAULT_THRESHOLD_PTS_HIGH
        
        # Alert Key to prevent duplicate alerts for the same condition
        alert_key = f"{player_id}_{stat_type}_{period}"

        if alert_key in self.alerted_players:
            return

        # HIGH Alert
        if low > (threshold_high + constants.BUFFER_PTS):
            reasoning = f"Q{period} Alpha={alpha}. " + ", ".join(flags)
            if not flags: reasoning += "High usage/efficiency."
            
            self.notifier.send_alert(
                player_name=name,
                stat_type=stat_type,
                prediction="HIGH",
                current_val=current_val,
                minutes=minutes,
                projected_range=(low, high),
                reasoning=reasoning
            )
            self.alerted_players.add(alert_key)

