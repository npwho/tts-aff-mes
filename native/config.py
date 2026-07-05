"""Paths and tunable defaults for the native automation tool."""
from pathlib import Path

NATIVE_DIR = Path(__file__).resolve().parent
STORAGE_DIR = NATIVE_DIR / "storage"
TEMPLATES_DIR = STORAGE_DIR / "templates"
LOGS_DIR = NATIVE_DIR / "logs"

RECORDED_FLOW_PATH = STORAGE_DIR / "recorded_flow.json"
PREVIEW_IMAGE_PATH = STORAGE_DIR / "recording_preview.png"
LAST_INPUT_PATH = STORAGE_DIR / "last_input.json"

# Pacing between usernames during replay (randomized within this range) -
# used for skipped/failed usernames only.
MIN_DELAY_BETWEEN_USERNAMES_S = 8
MAX_DELAY_BETWEEN_USERNAMES_S = 20

# After a *successful* send, move on to the next username almost
# immediately instead of the long randomized delay above - still enough
# for the reset-state Escape presses' UI transitions to actually settle
# before the next username's foreground check runs.
SUCCESS_NEXT_USER_DELAY_S = 1.2

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

# Pause between steps within a single username's flow, so the whole
# sequence doesn't fire off unnaturally fast.
INTER_STEP_DELAY_MIN_S = 0.4
INTER_STEP_DELAY_MAX_S = 1.0

# A short settle delay right after an action that triggers an API call
# (pasting the username to search, clicking Chat to open a thread), before
# starting to poll for the next element - avoids hammering the CPU with
# screenshots in the first instant when we already know nothing will match.
API_SETTLE_DELAY_S = 0.8

RESET_VERIFY_RETRIES = 3

# Hover-reveal wiggle (automation.hover_reveal_once) - used only for the
# Chat button, which only appears on :hover and doesn't reliably show up
# from the cursor just teleporting directly onto it. Only wiggled if it
# isn't already visible on the first check; HOVER_REVEAL_REPEATS caps how
# many wiggle attempts before giving up.
HOVER_REVEAL_OFFSET_PX = 200
HOVER_REVEAL_REPEATS = 3
# Brief pause after moving away - just needs to register the cursor left.
HOVER_REVEAL_PAUSE_S = 0.15
# Longer settle after moving back onto the target - the hover CSS/JS reveal
# needs real time to render before a screenshot will show it.
HOVER_REVEAL_SETTLE_S = 0.35

# Template matching (native/template_match.py). A smaller patch is more
# sensitive to just the button/icon itself rather than surrounding content
# that can shift or change between recording and replay.
TEMPLATE_PATCH_RADIUS_PX = 14
TEMPLATE_SEARCH_MARGIN_PX = 150
TEMPLATE_MATCH_THRESHOLD = 0.7
TEMPLATE_POLL_INTERVAL_S = 0.5
STEP_MAX_WAIT_S = 8.0

# Steps that don't get image verification at all (New message button,
# Username input) - assumed to always be present once the page/dialog has
# had a moment to render, so this just waits a bit then clicks the
# recorded position directly.
FIXED_STEP_WAIT_S = 1.0

STATUS_SENT = "SENT"
STATUS_SKIPPED_NOT_FOUND = "SKIPPED_NOT_FOUND"
STATUS_FAILED_UI_STUCK = "FAILED_UI_STUCK"
STATUS_ABORTED = "ABORTED"
STATUS_DRY_RUN_OK = "DRY_RUN_OK"
