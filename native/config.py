"""Paths and tunable defaults for the native automation tool."""
from pathlib import Path

NATIVE_DIR = Path(__file__).resolve().parent
STORAGE_DIR = NATIVE_DIR / "storage"
LOGS_DIR = NATIVE_DIR / "logs"

CALIBRATION_PATH = STORAGE_DIR / "calibration.json"
ACTION_SCRIPT_PATH = STORAGE_DIR / "action_script.json"

WS_HOST = "127.0.0.1"
WS_PORT = 8765

HEARTBEAT_INTERVAL_S = 15
HEARTBEAT_TIMEOUT_S = 5

# Pacing between usernames during replay (randomized within this range).
MIN_DELAY_BETWEEN_USERNAMES_S = 8
MAX_DELAY_BETWEEN_USERNAMES_S = 20

# Every N usernames, insert one longer "human break" pause.
HUMAN_BREAK_EVERY_N = 10
HUMAN_BREAK_MIN_S = 30
HUMAN_BREAK_MAX_S = 60

# Mouse movement / click hygiene.
MOUSE_MOVE_MIN_DURATION_S = 0.25
MOUSE_MOVE_MAX_DURATION_S = 0.6
CLICK_JITTER_PX = 3
PRE_CLICK_PAUSE_MIN_S = 0.05
PRE_CLICK_PAUSE_MAX_S = 0.25

# Wait after moving the mouse onto a search-result row before asking for the
# now-hover-revealed "Chat" button's rect.
HOVER_SETTLE_MIN_S = 0.15
HOVER_SETTLE_MAX_S = 0.3

# Timeouts for waiting on extension responses over the WebSocket.
LOCATE_TIMEOUT_S = 5
SEARCH_RESULT_TIMEOUT_S = 6
SEND_VERIFY_TIMEOUT_S = 4
RESET_VERIFY_RETRIES = 3

STATUS_SENT = "SENT"
STATUS_SKIPPED_NOT_FOUND = "SKIPPED_NOT_FOUND"
STATUS_FAILED_SEND_UNCONFIRMED = "FAILED_SEND_UNCONFIRMED"
STATUS_FAILED_UI_STUCK = "FAILED_UI_STUCK"
STATUS_ABORTED = "ABORTED"
STATUS_DONE_RECORDED = "DONE (recorded)"
