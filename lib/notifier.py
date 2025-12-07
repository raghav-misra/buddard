class Notifier:
    def __init__(self):
        pass

    def send_alert(self, player_name, stat_type, prediction, current_val, minutes, projected_range, reasoning, p50=None):
        """
        Formats and sends the prediction alert.
        Currently prints to console.
        """
        low, high = projected_range
        p25 = low + 0.25 * (high - low)
        
        # Determine Action advice based on prediction direction
        if prediction == "HIGH":
            targets = (
                f"  - **Floor (>95% Hit):** {low:.1f}\n"
                f"  - **25th %ile (~80% Hit):** {p25:.1f}"
            )
            if p50 is not None:
                targets += f"\n  - **50th %ile (Median):** {p50:.1f}"
                
            action_text = (
                f"• **Betting Targets (OVER):**\n"
                f"{targets}\n"
                f"• **Action:** Check live line. If line <= Target, consider OVER."
            )
        else:
            # For UNDER, we'd look at High and 75th percentile, but we focused on OVERs in backtest.
            # Keeping simple logic for now.
            action_text = f"• **Action:** Check live line. If line > {high:.1f}, consider UNDER."

        message = (
            f"\n--------------------------------------------------\n"
            f"🚨 **PREDICT: {prediction}** {stat_type} on **{player_name}**\n"
            f"--------------------------------------------------\n"
            f"• **Current:** {current_val} {stat_type} in {minutes:.1f} min.\n"
            f"• **Projected Range:** [{low:.1f} to {high:.1f}] {stat_type}\n"
            f"• **Reasoning:** {reasoning}\n"
            f"{action_text}\n"
            f"--------------------------------------------------\n"
        )
        
        print(message)
        # In the future, add requests.post() here for Pushover/Telegram
