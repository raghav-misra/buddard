import os

# --- File Paths ---
DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
BASELINES_FILE = os.path.join(DATA_DIR, 'baselines.json')

# --- API Configuration ---
# Headers to mimic a browser to avoid some basic blocking, though nba_api handles most.
API_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'Accept-Language': 'en-US,en;q=0.5'
}
API_DELAY = 2.0  # Seconds to sleep between calls to avoid 429 errors

# --- Dynamic Alpha (Regression Factors) ---
# Used to weight Current Pace vs. Baseline Pace based on game progress.
ALPHA_Q1 = 0.2
ALPHA_MID_GAME = 0.6  # Q2 and Q3
ALPHA_Q4 = 0.9

# --- Minute Adjustments ---
FOUL_TROUBLE_THRESHOLD = 4  # Fouls >= 4 in Q2/Q3
FOUL_TROUBLE_PENALTY = 5    # Minutes to subtract

BLOWOUT_DIFF_THRESHOLD = 20 # Score differential > 20
BLOWOUT_PENALTY = 8         # Minutes to subtract (if winning)

# --- Prediction Thresholds & Buffers ---
# Confidence Buffer: The range must clear the threshold by this amount to trigger.
BUFFER_PTS = 1.0
BUFFER_REB = 0.5
BUFFER_AST = 0.5

# Multiplier for Standard Deviation to create the range (e.g., 1.5 * sigma)
SIGMA_MULTIPLIER = 1.5

# Default Thresholds (Can be overridden per player if we get advanced later)
# These are just defaults to prevent spam if not specified.
DEFAULT_THRESHOLD_PTS_HIGH = 25
DEFAULT_THRESHOLD_PTS_LOW = 10
DEFAULT_THRESHOLD_REB_HIGH = 10
DEFAULT_THRESHOLD_AST_HIGH = 8
