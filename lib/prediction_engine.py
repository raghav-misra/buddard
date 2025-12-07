import math
import lib.constants as constants

class PredictionEngine:
    @staticmethod
    def calculate_performance_factor(current_pace, baseline_pace):
        """
        Calculates how much better/worse the player is performing vs baseline.
        Returns a factor, e.g., 1.5 means 50% better.
        """
        if baseline_pace == 0:
            return 1.0
        return current_pace / baseline_pace

    @staticmethod
    def calculate_dynamic_remaining_minutes(avg_minutes, current_minutes, current_fouls, score_diff, period, performance_factor=1.0):
        """
        Calculates expected remaining minutes.
        1. Starts with base remaining (Avg - Current).
        2. Applies Penalty Modifiers (Fouls, Blowouts).
        3. Applies 'Hot Hand' Modifier (Performance Factor).
        """
        base_remaining = max(0, avg_minutes - current_minutes)
        
        if base_remaining == 0:
            return 0

        modifier = 1.0

        # --- Foul Trouble Adjustments ---
        # Q1 (Period 1): 2+ Fouls is trouble
        if period == 1 and current_fouls >= 2:
            modifier *= 0.85
        # Q2 (Period 2): 3+ Fouls is trouble
        elif period == 2 and current_fouls >= 3:
            modifier *= 0.80
        # Q3 (Period 3): 4+ Fouls is trouble
        elif period == 3 and current_fouls >= 4:
            modifier *= 0.75
        # Any time: 5 Fouls is critical
        if current_fouls >= 5:
            modifier *= 0.50

        # --- Blowout Adjustments ---
        # If score diff is massive, starters sit.
        # Q3: Diff > 20
        if period == 3 and abs(score_diff) > 20:
            modifier *= 0.85
        # Q4 (or late Q3 context): Diff > 25
        if period >= 3 and abs(score_diff) > 25:
            modifier *= 0.70

        # --- Hot Hand Adjustment (New) ---
        # If playing well, coach plays them more.
        # Formula: 1 + 0.2 * ln(Performance Factor)
        # Cap performance factor to avoid log explosions or massive minutes
        safe_perf = max(0.5, min(performance_factor, 2.0)) 
        hot_hand_mod = 1.0 + (0.2 * math.log(safe_perf))
        
        # Apply modifiers
        expected_remaining = base_remaining * modifier * hot_hand_mod
        
        # Hard cap: Cannot play more than (48 - current_minutes)
        max_possible = 48.0 - current_minutes
        return min(expected_remaining, max_possible)

    @staticmethod
    def calculate_pfs(current_stat, baseline_pace, expected_remaining_min):
        """
        Calculates the Projected Final Stat (PFS) using 'Bank & Burn'.
        
        PFS = Current Stats + (Baseline Pace * Expected Remaining Min)
        
        We assume regression to the mean for the remainder of the game.
        """
        future_production = baseline_pace * expected_remaining_min
        return current_stat + future_production

    @staticmethod
    def get_prediction_range(pfs, sigma, minutes_played, avg_minutes, current_stat=0):
        """
        Calculates the asymmetric confidence interval.
        
        Low: PFS - (1.0 * Sigma * Decay)
        High: PFS + (2.0 * Sigma * Decay)
        
        Decay is purely sqrt(remaining_pct), no artificial floor.
        """
        # Handle missing sigma
        if sigma == 0:
            sigma = pfs * 0.2

        # Variance Decay: Scale sigma by remaining time
        remaining_pct = max(0, (avg_minutes - minutes_played) / avg_minutes) if avg_minutes > 0 else 0
        decay_factor = math.sqrt(remaining_pct)
        
        adjusted_sigma = sigma * decay_factor

        # Asymmetric Bounds
        # Floor is tighter (1.0 sigma)
        low = pfs - (1.0 * adjusted_sigma)
        # Ceiling is wider (2.0 sigma)
        high = pfs + (2.0 * adjusted_sigma)
        
        # Clamp low to current_stat (cannot score negative points from now on)
        low = max(low, current_stat)
        # Ensure high is at least low
        high = max(high, low)

        return low, high, adjusted_sigma
