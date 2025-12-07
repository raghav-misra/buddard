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

The Rules Engine uses a **"Bank & Burn"** model with dynamic minute adjustments.

### Core Projection Formula (Bank & Burn)

Instead of blending paces, we "Bank" the current stats and assume the player will "Burn" the remaining minutes at their historical baseline pace. This is statistically safer than assuming a hot streak will continue indefinitely.

$$
\text{PFS} = \text{Current Stats} + (\text{Baseline Pace} \times \text{Expected Remaining Minutes})
$$

### Dynamic Minutes Adjustment (Hot Hand Logic)

We adjust the **Expected Remaining Minutes** based on game context and player performance.

1.  **Base Calculation:** $\text{Avg Minutes} - \text{Current Minutes}$
2.  **Contextual Penalties:**
    *   **Foul Trouble:** Reduced by 15-25% if fouls are high relative to the quarter.
    *   **Blowout Risk:** Reduced by 15-30% if the score differential is >20 in the second half (winning team only).
3.  **Hot Hand Bonus:** If a player is outperforming their baseline pace (Points Per Minute), we assume the coach will extend their rotation.
    $$
    \text{Performance Factor} = \frac{\text{Current Pace}}{\text{Baseline Pace}}
    $$
    $$
    \text{Bonus} = 1 + 0.2 \times \ln(\text{Performance Factor})
    $$

### Trigger Logic (Asymmetric Range)

We calculate a confidence interval using **Variance Decay**, but unlike standard models, we use an **Asymmetric Range** to account for the "elastic ceiling" of NBA scoring.

1.  **Variance Decay:**
    $$
    \text{Decay Factor} = \sqrt{\frac{\text{Remaining Minutes}}{\text{Average Minutes}}}
    $$
    $$
    \sigma_{adj} = \sigma \times \text{Decay Factor}
    $$

2.  **Asymmetric Bounds:**
    *   **Low Bound (Floor):** $\text{PFS} - (1.0 \times \sigma_{adj})$
        *   *Tighter floor because players rarely underperform massively once minutes are secured.*
    *   **High Bound (Ceiling):** $\text{PFS} + (2.0 \times \sigma_{adj})$
        *   *Wider ceiling to account for overtime or "garbage time" stat padding.*

3.  **Trigger Condition:**
    *   **HIGH Alert:** If $\text{Low Bound} > \text{Threshold} + \text{Buffer}$
    *   **LOW Alert:** If $\text{High Bound} < \text{Threshold} - \text{Buffer}$

## Backtesting & Validation

To ensure the model's accuracy and profitability, we have implemented a robust backtesting suite that replays historical games using the NBA API's "Time Travel" capabilities.

### Methodology

The backtesting engine (`aggregate_backtest.py`) simulates the bot's performance on completed games by:

1.  **Ground Truth:** Fetching the final box score to know the actual outcome.
2.  **Time Travel:** Using `BoxScoreTraditionalV3` with `range_type` to fetch the exact state of the game at specific checkpoints (End of Q1, Halftime, End of Q3).
3.  **Prediction:** Feeding these partial stats into the `PredictionEngine` (using the same Bank & Burn logic and Hot Hand adjustments as the live bot).
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

### Results

*Note: The model has recently been overhauled to the "Bank & Burn" architecture. New backtesting results are pending.*