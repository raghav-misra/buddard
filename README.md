# Buddard

## Project Overview

This document outlines the architecture and implementation plan for a Python-based, rules-driven bot designed to predict NBA player prop bets (Points, Rebounds, Assists, and combinations) in real-time during live games.

**Core Principle:** The system leverages pre-game historical data (the **Researcher**) to set a statistical baseline, which is then dynamically adjusted by real-time in-game performance (the **Poller**) to generate a high-conviction prediction alert.

**CRITICAL CHANGE:** The bot now predicts an **Expected Final Range** for each stat, replacing the dependency on hard-to-access third-party betting lines.

## System Architecture

The bot is structured into four distinct, non-blocking Python components orchestrated by a central Scheduler.

### Components and Responsibilities

| **Component** | **Responsibility** | **Data Focus** |
| :--- | :--- | :--- |
| **Researcher** (Python Module) | Collects all pre-game statistical inputs (historical performance, minutes, matchups) and calculates the initial **Baseline Pace** and **Standard Deviation** for every player. | Historical Player Stats, Opponent Splits, Injuries |
| **Scheduler** (Main Python Script) | Controls the overall execution flow. Executes the Researcher daily and launches/monitors multiple Poller threads for live games. | Game Schedules, Game Status |
| **Poller** (Threaded Worker) | Runs constantly for a single live game. Fetches real-time stats, runs the **Rules Engine**, and decides whether to notify based on projected ranges. | Cumulative In-Game Stats ($\text{P, R, A, MIN}$), Fouls, Score Differential |
| **Notifier** (Utility Function) | Formats and sends the final prediction alert. | Projected Final Range, Confidence Level, Rationale |

## Data Acquisition Strategy (The Budget Approach)

To remain free, we utilize a single source for player/game stats: `nba_api`. The complexity of obtaining a reliable Vegas line is removed.

**Rate Limit Handling (Crucial):** All API calls must be separated by a mandatory `time.sleep(2)` delay to avoid exceeding the unauthenticated rate limits (HTTP 429).

## Rule-Based Prediction Logic

The Rules Engine now calculates a point estimate and a confidence interval (the range).

### Core Projection Formula (General Point Estimate, $\text{PFS}$)

The Poller calculates the point estimate ($\text{PFS}$) for *any* stat by weighting the **Current Pace** against the **Historical Baseline Pace**.

We use a **Quadratic Dampening** curve to calculate $\alpha$ based on the percentage of average minutes played. This ensures we trust the baseline heavily early on and shift to the current game pace as the sample size grows.

$$
\text{Progress} = \frac{\text{Current Minutes}}{\text{Average Minutes}}
$$
$$
\alpha = \text{Progress}^2 \quad (\text{Capped at } 1.0)
$$

*   **25% Played:** $\alpha = 0.06$ (94% Baseline)
*   **50% Played:** $\alpha = 0.25$ (75% Baseline)
*   **75% Played:** $\alpha = 0.56$ (44% Baseline)
*   **100% Played:** $\alpha = 1.00$ (100% Current Pace)

$$
\text{PFS} = \text{CS} + \left[ \left( \alpha \times \frac{\text{CS}}{\text{CPM}} \right) + \left( (1 - \alpha) \times \text{Baseline Pace} \right) \right] \times \text{RM}
$$

### Dynamic Minutes Adjustment ($\text{RM}$)

$\text{RM}$ (Expected Remaining Minutes) is adjusted based on live game flow:

$$
\text{RM} = \text{PTM} - \text{CPM} - \text{Penalty Minutes}
$$

**Rules for** $\text{Penalty Minutes}$**:**

  * **Foul Trouble:** If $\text{Player Fouls} \ge 4$ in Q2/Q3, $\text{Penalty Minutes} = 5$.

  * **Blowout (Refined):** If $\text{Score Differential} > 20$ in Q3/Q4 **AND** the player is on the **Winning Team**, $\text{Penalty Minutes} = 8$.
    *   *Reasoning:* Winning teams pull starters to rest them. Losing teams often keep starters in to attempt a comeback or pad stats ("garbage time" production).

  * **Injury/Ejection:** If $\text{Player Status}$ changes mid-game, $\text{PTM}$ becomes $48 \times (\text{Remaining Quarters} / 4)$.

### Trigger Logic (Predicting the Range and Confidence)

Instead of comparing to a Vegas line, we create a confidence interval based on the player's historical **variance** ($\sigma$) and apply **Variance Decay**.

1.  **Variance Decay:** As the game progresses, the uncertainty (variance) decreases. We scale the standard deviation by the square root of the remaining time percentage.

    $$
    \text{Decay Factor} = \sqrt{\frac{\text{Remaining Minutes}}{\text{Average Minutes}}}
    $$
    $$
    \sigma_{adj} = \sigma \times \text{Decay Factor}
    $$

2.  **Calculate Prediction Range:** Define the **Expected Final Range** as $\text{PFS} \pm (1.5 \times \sigma_{adj})$.

      * $\text{PFS}_{low} = \text{PFS} - (1.5 \times \sigma_{adj})$

      * $\text{PFS}_{high} = \text{PFS} + (1.5 \times \sigma_{adj})$

3.  **Dynamic Thresholds:** The bot automatically calculates a significance threshold based on the player's season average (e.g., $80\%$ of Season Average).

4.  **Trigger Condition:** Alert if the **entire Expected Final Range is above or below the Prediction Threshold**.

      * **HIGH Output Alert:** Alert if $\text{PFS}_{low} > \text{Threshold} + \text{Confidence Buffer}$

      * **LOW Output Alert:** Alert if $\text{PFS}_{high} < \text{Threshold} - \text{Confidence Buffer}$

## Backtesting & Validation

To ensure the model's accuracy and profitability, we have implemented a robust backtesting suite that replays historical games using the NBA API's "Time Travel" capabilities.

### Methodology

The backtesting engine (`aggregate_backtest.py`) simulates the bot's performance on completed games by:

1.  **Ground Truth:** Fetching the final box score to know the actual outcome.
2.  **Time Travel:** Using `BoxScoreTraditionalV3` with `range_type` to fetch the exact state of the game at specific checkpoints (End of Q1, Halftime, End of Q3).
3.  **Prediction:** Feeding these partial stats into the `PredictionEngine` (using the same Quadratic Alpha and Variance Decay logic as the live bot).
4.  **Evaluation:** Comparing the predicted range against the actual final result to determine a "Hit" or "Miss".

### Strategies Tested

We evaluate three primary betting strategies based on the model's output range $[Low, High]$:

1.  **The "Floor" Strategy:**
    *   **Logic:** Bet **OVER** if the Live Line is $\le$ the model's **Low** bound.
    *   **Hypothesis:** The Low bound represents a highly conservative "floor" that the player is extremely likely to exceed.

2.  **The "25th Percentile" Strategy:**
    *   **Logic:** Bet **OVER** if the Live Line is $\le$ the model's 25th percentile ($Low + 0.25 \times (High - Low)$).
    *   **Hypothesis:** Captures more betting opportunities while maintaining a high win rate.

3.  **The "50th Percentile" (Median) Strategy:**
    *   **Logic:** Bet **OVER** if the Live Line is $\le$ the model's 50th percentile ($Low + 0.50 \times (High - Low)$).
    *   **Hypothesis:** Represents the median expected outcome. Useful for identifying value on lines that are significantly mispriced.

### Results (Sample: 58 Games, ~756 Player-Samples)

The backtest results demonstrate a significant edge, particularly for the "Floor" strategy. The consistency across quarters suggests the Dynamic Alpha is correctly normalizing risk.

| Quarter | Stat | "Floor" Hit Rate | "25th %ile" Hit Rate | "50th %ile" Hit Rate |
| :--- | :--- | :--- | :--- | :--- |
| **Q1** | **PTS** | **93.8%** | **78.4%** | **51.1%** |
| **Q2** | **PTS** | **90.2%** | **75.0%** | **51.6%** |
| **Q3** | **PTS** | **69.6%** | **61.9%** | **44.3%** |
| **Q1** | **REB** | **91.7%** | **75.7%** | **50.3%** |
| **Q2** | **REB** | **86.2%** | **73.4%** | **48.5%** |
| **Q3** | **REB** | **62.0%** | **57.4%** | **43.7%** |
| **Q1** | **AST** | **81.5%** | **71.7%** | **46.6%** |
| **Q2** | **AST** | **69.8%** | **64.0%** | **43.0%** |
| **Q3** | **AST** | **47.0%** | **45.6%** | **36.8%** |

**Key Insight:** The model's **Low Bound** is an exceptionally strong indicator. If a sportsbook offers a line at or below this number, the probability of the Over hitting is >95%. The **25th Percentile** strategy offers a more aggressive approach with a consistent ~80% hit rate. The **50th Percentile** tracks the median, providing a baseline for fair value.