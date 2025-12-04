class Notifier:
    def __init__(self):
        pass

    def send_alert(self, player_name, stat_type, prediction, current_val, minutes, projected_range, reasoning):
        """
        Formats and sends the prediction alert.
        Currently prints to console.
        """
        low, high = projected_range
        
        # Determine Action advice based on prediction direction
        if prediction == "HIGH":
            action = f"Check live line. If line < {low:.1f}, consider OVER."
        else:
            action = f"Check live line. If line > {high:.1f}, consider UNDER."

        message = (
            f"\n--------------------------------------------------\n"
            f"🚨 **PREDICT: {prediction}** {stat_type} on **{player_name}**\n"
            f"--------------------------------------------------\n"
            f"• **Current:** {current_val} {stat_type} in {minutes} min.\n"
            f"• **Projected Range:** [{low:.1f} to {high:.1f}] {stat_type}\n"
            f"• **Reasoning:** {reasoning}\n"
            f"• **Action:** {action}\n"
            f"--------------------------------------------------\n"
        )
        
        print(message)
        # In the future, add requests.post() here for Pushover/Telegram
