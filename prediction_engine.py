import math
import constants

class PredictionEngine:
    @staticmethod
    def calculate_alpha(minutes_played, avg_minutes):
        """
        Calculates the weight (Alpha) given to the current game stats vs baseline.
        
        Uses a quadratic curve to dampen early-game variance.
        Early in the game, we trust the baseline much more.
        
        Formula: (minutes_played / avg_minutes) ^ 2
        
        Ex: 
        - 25% through game (Linear=0.25) -> Alpha = 0.06 (Trust baseline 94%)
        - 50% through game (Linear=0.50) -> Alpha = 0.25 (Trust baseline 75%)
        - 75% through game (Linear=0.75) -> Alpha = 0.56 (Trust baseline 44%)
        - 100% through game (Linear=1.0) -> Alpha = 1.00 (Trust current 100%)
        """
        safe_avg_min = max(10, avg_minutes)
        progress = minutes_played / safe_avg_min
        
        if progress > 1.0:
            return 1.0
            
        # Quadratic dampening
        return progress ** 2

    @staticmethod
    def calculate_pfs(current_stat, current_min, baseline_pace, remaining_min, alpha):
        """
        Calculates the Projected Final Stat (PFS).
        """
        if current_min <= 0:
            return current_stat + (baseline_pace * remaining_min)
            
        current_pace = current_stat / current_min
        weighted_pace = (alpha * current_pace) + ((1 - alpha) * baseline_pace)
        return current_stat + (weighted_pace * remaining_min)

    @staticmethod
    def get_prediction_range(pfs, sigma, minutes_played, avg_minutes):
        """
        Calculates the confidence interval (low, high) with variance decay.
        """
        # Handle missing sigma
        if sigma == 0:
            sigma = pfs * 0.2

        # Variance Decay: Scale sigma by remaining time
        remaining_pct = max(0, (avg_minutes - minutes_played) / avg_minutes) if avg_minutes > 0 else 0
        decay_factor = math.sqrt(remaining_pct)
        
        # Ensure we don't decay to absolute zero too early
        decay_factor = max(0.1, decay_factor)

        adjusted_sigma = sigma * decay_factor

        low = pfs - (constants.SIGMA_MULTIPLIER * adjusted_sigma)
        high = pfs + (constants.SIGMA_MULTIPLIER * adjusted_sigma)
        
        return low, high, adjusted_sigma
