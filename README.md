# Buddard

## 1. Project Overview

This document outlines the architecture and implementation plan for a Python-based, rules-driven bot designed to predict NBA player prop bets (Points, Rebounds, Assists, and combinations) in real-time during live games.

**Core Principle:** The system leverages pre-game historical data (the **Researcher**) to set a statistical baseline, which is then dynamically adjusted by real-time in-game performance (the **Poller**) to generate a high-conviction prediction alert.

**CRITICAL CHANGE:** The bot now predicts an **Expected Final Range** for each stat, replacing the dependency on hard-to-access third-party betting lines.

## 2. System Architecture

The bot is structured into four distinct, non-blocking Python components orchestrated by a central Scheduler.

### Architecture Diagram

```mermaid
graph TD
    A[Scheduler: Main Loop] --> B{8:00 AM PST?};
    B -- Yes --> C(Researcher: Pre-Game Data Fetch);
    C --> D[Data Store: Baselines & Projected Ranges];
    B -- No / Game Starts --> E(Poller: Game-Specific Instance);
    E --> F[API Query: Live Box Score];
    F --> G{Rules Engine: Project & Trigger?};
    G -- Alert YES --> H[Notifier: Send Alert];
    G -- Alert NO --> E;

    style A fill:#f9f,stroke:#333
    style C fill:#ccf,stroke:#333
    style D fill:#ddf,stroke:#333
    style E fill:#fcf,stroke:#333
    style F fill:#aaf,stroke:#333
    style G fill:#ffc,stroke:#333
    style H fill:#afa,stroke:#333
````

### Components and Responsibilities

| **Component** | **Responsibility** | **Data Focus** | **Libraries Used** |
| :--- | :--- | :--- | :--- |
| **Researcher** (Python Module) | Collects all pre-game statistical inputs (historical performance, minutes, matchups) and calculates the initial **Baseline Pace** and **Standard Deviation** for every player. | Historical Player Stats, Opponent Splits, Injuries | `nba_api` |
| **Scheduler** (Main Python Script) | Controls the overall execution flow. Executes the Researcher daily and launches/monitors multiple Poller threads for live games. | Game Schedules, Game Status | `schedule`, `time` (built-in) |
| **Poller** (Threaded Worker) | Runs constantly for a single live game. Fetches real-time stats, runs the **Rules Engine**, and decides whether to notify based on projected ranges. | Cumulative In-Game Stats ($\text{P, R, A, MIN}$), Fouls, Score Differential | `nba_api`, `time` |
| **Notifier** (Utility Function) | Formats and sends the final prediction alert. | Projected Final Range, Confidence Level, Rationale | `smtplib` (Email) or `requests` (Pushbullet/Telegram) |

## 3\. Data Acquisition Strategy (The Budget Approach)

To remain free, we utilize a single source for player/game stats: `nba_api`. The complexity of obtaining a reliable Vegas line is removed.

### A. Pre-Game Data Fetch (Researcher, Daily at 8 AM PST)

| **Data Point** | **nba\_api Endpoint** | **Purpose in Rules Engine** |
| :--- | :--- | :--- |
| **Schedule & Game IDs** | `ScoreboardV2` | Identifies which games are played today and their unique IDs for the Poller. |
| **Player Baseline Stats** | `PlayerCareerStats` | Establishes season-long $\text{Stat/Minute}$ efficiency and Average Minutes ($\overline{\text{MIN}}$). |
| **Recent Form & Variance** | `PlayerGameLog` | Fetches the last 10-15 game logs to calculate **Standard Deviation** ($\sigma$) for each stat (needed for range prediction). |
| **Injury/Roster Changes** | `CommonTeamRoster` | Helps estimate **Projected Total Minutes** ($\text{PTM}$) if a primary player is out. |

**Rate Limit Handling (Crucial):** All API calls must be separated by a mandatory `time.sleep(2)` delay to avoid exceeding the unauthenticated rate limits (HTTP 429).

### B. Real-Time Polling (Poller, Every $30-60$ Seconds)

| **Data Point** | **nba\_api Endpoint** | **Real-Time Use** |
| :--- | :--- | :--- |
| **Cumulative Player Stats** | `BoxScoreTraditionalV3` | Provides instantaneous $\text{PTS, REB, AST, MIN}$ for every player in the game. |
| **Game Flow** | `BoxScoreTraditionalV3` | Provides current $\text{Quarter, Time Remaining}$, and **Score Differential** (for dynamic minute adjustment). |

## 4\. Rule-Based Prediction Logic

The Rules Engine now calculates a point estimate and a confidence interval (the range).

### 4.1. Core Projection Formula (General Point Estimate, $\text{PFS}$)

The Poller calculates the point estimate ($\text{PFS}$) for *any* stat by weighting the **Current Pace** against the **Historical Baseline Pace**.

**CRITICAL UPDATE: Dynamic Alpha ($\alpha$)**
Instead of a static weighting, $\alpha$ adjusts dynamically based on game progress to reduce noise from small sample sizes early in the game.

*   **Early Game (Q1):** $\alpha = 0.2$ (Trust the **Baseline** history).
*   **Mid Game (Q2-Q3):** $\alpha = 0.5$ to $0.7$ (Balanced approach).
*   **Late Game (Q4):** $\alpha = 0.9$ (Trust the **Current Pace**).

$$
\text{PFS} = \text{CS} + \left[ \left( \alpha \times \frac{\text{CS}}{\text{CPM}} \right) + \left( (1 - \alpha) \times \text{Baseline Pace} \right) \right] \times \text{RM}
$$

| Variable | Description | Source |
| :--- | :--- | :--- |
| $\text{PFS}$ | **Projected Final Stat** ($\text{Pts, Reb, Ast, etc.}$) | Calculated |
| $\text{CS}$ | **Current Stat** (e.g., $18 \text{ Pts}$) | `BoxScoreTraditionalV3` |
| $\text{CPM}$ | **Current Minutes Played** ($15 \text{ min}$) | `BoxScoreTraditionalV3` |
| $\text{Baseline Pace}$ | Average $\text{Stat/Minute}$ from **Researcher** data (Rule Set 1). | `PlayerCareerStats` / `PlayerGameLog` |
| $\text{RM}$ | **Expected Remaining Minutes** (Dynamic). | Calculated (See 4.2) |
| $\alpha$ | **Regression Factor** (Dynamic). Higher $\alpha$ favors current hot/cold streak. | Calculated based on Quarter |

### 4.1.1. Specific Stat Projections

The general formula is instantiated for the three primary betting categories: Points, Rebounds, and Assists.

**A. Projected Final Points ($\text{PFS}_{Pts}$)**

$$
\text{PFS}_{Pts} = \text{Current Pts} + \left[ \left( \alpha \times \frac{\text{Current Pts}}{\text{CPM}} \right) + \left( (1 - \alpha) \times \text{Baseline Pace}_{Pts} \right) \right] \times \text{RM}
$$

*Where:* $\text{Baseline Pace}_{Pts} = \text{Season Pts/Min}$

**B. Projected Final Rebounds ($\text{PFS}_{Reb}$)**

$$
\text{PFS}_{Reb} = \text{Current Reb} + \left[ \left( \alpha \times \frac{\text{Current Reb}}{\text{CPM}} \right) + \left( (1 - \alpha) \times \text{Baseline Pace}_{Reb} \right) \right] \times \text{RM}
$$

*Where:* $\text{Baseline Pace}_{Reb} = \text{Season Reb/Min}$

**C. Projected Final Assists ($\text{PFS}_{Ast}$)**

$$
\text{PFS}_{Ast} = \text{Current Ast} + \left[ \left( \alpha \times \frac{\text{Current Ast}}{\text{CPM}} \right) + \left( (1 - \alpha) \times \text{Baseline Pace}_{Ast} \right) \right] \times \text{RM}
$$

*Where:* $\text{Baseline Pace}_{Ast} = \text{Season Ast/Min}$

### 4.2. Dynamic Minutes Adjustment ($\text{RM}$)

$\text{RM}$ (Expected Remaining Minutes) is adjusted based on live game flow:

$$
\text{RM} = \text{PTM} - \text{CPM} - \text{Penalty Minutes}
$$

**Rules for** $\text{Penalty Minutes}$**:**

  * **Foul Trouble:** If $\text{Player Fouls} \ge 4$ in Q2/Q3, $\text{Penalty Minutes} = 5$.

  * **Blowout (Refined):** If $\text{Score Differential} > 20$ in Q3/Q4 **AND** the player is on the **Winning Team**, $\text{Penalty Minutes} = 8$.
    *   *Reasoning:* Winning teams pull starters to rest them. Losing teams often keep starters in to attempt a comeback or pad stats ("garbage time" production).

  * **Injury/Ejection:** If $\text{Player Status}$ changes mid-game, $\text{PTM}$ becomes $48 \times (\text{Remaining Quarters} / 4)$.

### 4.3. Trigger Logic (Predicting the Range and Confidence)

Instead of comparing to a Vegas line, we create a confidence interval based on the player's historical **variance** ($\sigma$).

1.  **Calculate Prediction Range:** Define the **Expected Final Range** as $\text{PFS} \pm (1.5 \times \sigma)$.

      * $\text{PFS}_{low} = \text{PFS} - (1.5 \times \sigma)$

      * $\text{PFS}_{high} = \text{PFS} + (1.5 \times \sigma)$

      * Where $\sigma$ is the **Standard Deviation of the stat per game** (calculated by the Researcher).

2.  **Define Prediction Threshold:** For a notification, the prediction must be a strong anomaly. A **Prediction Threshold ($\text{T}$)** is a manually defined number representing a significant output (e.g., $25 \text{ Pts}$).

3.  **Trigger Condition:** Alert if the **entire Expected Final Range is above or below the Prediction Threshold**.

      * **HIGH Output Alert:** Alert if $\text{PFS}_{low} > \text{T} + \text{Confidence Buffer}$

      * **LOW Output Alert:** Alert if $\text{PFS}_{high} < \text{T} - \text{Confidence Buffer}$

| **Prop Category** | **Example Prediction Threshold (T)** | **Confidence Buffer** |
| :--- | :--- | :--- |
| **Points** | $25 \text{ Pts}$ (for a star) or $12 \text{ Pts}$ (for a role player) | $1.0 \text{ Pts}$ |
| **Double-Double** | $9.0$ (If $\text{PFS}_{low}$ for Pts is $> 9.0$ AND $\text{PFS}_{low}$ for Reb is $> 9.0$) | $0.5$ |

The notification is designed to empower the user to make the final betting decision. It does not say "Bet Over" or "Bet Under". Instead, it provides the data and the "Why".

> **PREDICT: HIGH** $\text{PTS}$ on **Luka Dončić**
> *   **Current:** $15 \text{ Pts}$ in $10 \text{ min}$ (Q1).
> *   **Projected Range:** $[31.5 \text{ to } 36.5] \text{ Pts}$
> *   **Reasoning:** High usage early (Q1 $\alpha=0.2$), no foul trouble.
> *   **Action:** Check live line. If line $< 31.5$, consider OVER.
**Notifier Alert Example:** **PREDICT: HIGH** $\text{PTS}$ on **Luka Dončić**. Current: $15 \text{ Pts}$ in $10 \text{ min}$. Projected Range: $[31.5 \text{ to } 36.5] \text{ Pts}$**.**

## 5\. Implementation Details

This section outlines the initial codebase structure and dependencies needed to run the application locally.

### 5.1. Codebase Structure

The project will use a modular structure to separate data fetching, rule processing, and scheduling logic. This improves readability, maintenance, and the ability to test components in isolation.

```
/nba-prediction-bot/
├── main.py             # SCHEDULER: Main execution loop, task scheduling, and poller thread management.
├── researcher.py       # RESEARCHER: Logic for fetching historical stats and calculating baselines (runs daily).
├── poller.py           # POLLER: Logic for real-time data fetching, projection, and trigger evaluation (runs in threads).
├── notifier.py         # NOTIFIER: Utility functions for formatting and sending push/email alerts.
├── constants.py        # Stores player IDs, API headers, tunable parameters (e.g., alpha, confidence buffers).
├── data/               # Local directory for persistent data storage.
│   ├── baselines.json  # JSON file storing all calculated baseline stats and sigma ($\sigma$).
└── requirements.txt    # Python dependency list.
```

### 5.2. Dependencies

The following Python packages are mandatory for core functionality:

| **Package** | **Purpose** | **Installation** |
| :--- | :--- | :--- |
| `nba_api` | Core library for fetching all stats data from the NBA endpoints. | `pip install nba_api` |
| `pandas` | Essential for handling, cleaning, and calculating statistical metrics ($\text{P/min}, \sigma$) from API responses efficiently. | `pip install pandas` |
| `schedule` | A simple, readable library for handling the periodic running of the Researcher (daily) and the Pollers (every $30-60 \text{ seconds}$). | `pip install schedule` |
| `requests` | Needed for making general HTTP requests, particularly for external notification services like Pushover or Telegram. | `pip install requests` |
| `python-dotenv` | Best practice for securely loading API tokens (e.g., Pushover, email SMTP) from a local `.env` file. | `pip install python-dotenv` |

### 5.3. Initial Setup Steps

1.  **Virtual Environment:** Create and activate a Python virtual environment to manage dependencies locally.

2.  **Install:** Install all packages listed in `requirements.txt`.

3.  **Configuration:** Create a `.env` file in the root directory to store any secret credentials needed for the `notifier.py` module (e.g., Pushbullet token, email password).

## 6\. Deployment and Scaling

### A. Local Deployment (Initial Phase)

1.  **Execution:** The `Scheduler` component, containing the main `while True: schedule.run_pending(); time.sleep(1)` loop, will run continuously in the background using a tool like `screen` or `tmux` (on Linux/macOS) or a service wrapper.

2.  **Scheduling Library:** The `schedule` Python library is ideal for handling the daily 8 AM tasks and launching the high-frequency Pollers.

3.  **Local Notification:** For instant alerts, we will integrate a service like **Pushover**, **Pushbullet**, or **Telegram** (using their HTTP APIs), as these are easy to set up with Python's `requests` library and provide instant mobile notifications.

### B. Scaling Considerations (Future)

If the bot proves profitable and requires handling more data or processing, the architecture can be scaled:

| **Component to Scale** | **Scaling Method** |
| :--- | :--- |
| **Scheduler/Poller** | Migrate to a dedicated task queue system like **Celery** or **RQ** with a **Redis** backend. This decouples the Poller threads, making them more resilient and allowing for true parallel processing across multiple machines. |
| **Data Storage** | Switch from storing data in memory (Python objects) to a persistent database (e.g., **PostgreSQL** or a time-series database) for back-testing and historical analysis. |
| **API Integration** | Upgrade from `nba_api` to a paid service like **BallDontLie** or **Sportradar** to obtain guaranteed low latency, higher rate limits, and direct access to betting odds. |