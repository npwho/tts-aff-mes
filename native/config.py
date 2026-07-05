"""Paths and tunable defaults for the native automation tool.

Two very different categories of wait live here, tuned differently on
purpose:
- API-related waits (search results, thread loading) - kept generous,
  since real network/server latency is unpredictable and cutting these
  risks false not-found/stuck failures.
- Local UI-update waits (dialog rendering, mouse movement, click
  registration) - made as fast as possible, since these are just the
  browser's own rendering, not a network round trip.
"""
from pathlib import Path

NATIVE_DIR = Path(__file__).resolve().parent
STORAGE_DIR = NATIVE_DIR / "storage"
TEMPLATES_DIR = STORAGE_DIR / "templates"
LOGS_DIR = NATIVE_DIR / "logs"

RECORDED_FLOW_PATH = STORAGE_DIR / "recorded_flow.json"
PREVIEW_IMAGE_PATH = STORAGE_DIR / "recording_preview.png"
LAST_INPUT_PATH = STORAGE_DIR / "last_input.json"

# ---- API-related waits: keep generous -------------------------------------

# Settle delay right after an action that triggers an API call (pasting the
# username to search, clicking Chat to open a thread), before starting to
# poll for the next element. Real server latency, not UI rendering - kept
# long deliberately.
API_SETTLE_DELAY_S = 1.0

# Ceiling on how long to poll for an element that depends on an API call
# having returned (the Chat button existing at all, the message thread
# having loaded). Only matters on the slow/failure path - costs nothing
# when the API responds quickly - so there's no speed reason to cut this;
# kept generous to avoid false not-found/stuck results on a slow response.
STEP_MAX_WAIT_S = 10.0

# ---- Local UI-update waits: blazing fast -----------------------------------

# Pacing between usernames during replay - used for skipped/failed
# usernames only (a successful send uses SUCCESS_NEXT_USER_DELAY_S
# instead). Not a network wait, just spacing out actions.
MIN_DELAY_BETWEEN_USERNAMES_S = 0.3
MAX_DELAY_BETWEEN_USERNAMES_S = 0.8

# After a *successful* send, move on to the next username almost instantly.
SUCCESS_NEXT_USER_DELAY_S = 0.3

# Every N usernames, insert one short breathing-room pause.
HUMAN_BREAK_EVERY_N = 20
HUMAN_BREAK_MIN_S = 2
HUMAN_BREAK_MAX_S = 4

# Mouse movement / click hygiene. Kept non-instant only so a click is a
# real, visible mouse movement rather than a teleport, not for stealth
# pacing.
MOUSE_MOVE_MIN_DURATION_S = 0.03
MOUSE_MOVE_MAX_DURATION_S = 0.08
CLICK_JITTER_PX = 3
PRE_CLICK_PAUSE_MIN_S = 0.01
PRE_CLICK_PAUSE_MAX_S = 0.03

# Pause between steps within a single username's flow.
INTER_STEP_DELAY_MIN_S = 0.05
INTER_STEP_DELAY_MAX_S = 0.1

RESET_VERIFY_RETRIES = 3

# Hover-reveal wiggle (automation.hover_reveal_once) - used only for the
# Chat button, which only appears on :hover and doesn't reliably show up
# from the cursor just teleporting directly onto it. Only wiggled if it
# isn't already visible on the first check; HOVER_REVEAL_REPEATS caps how
# many wiggle attempts before giving up. This is pure local CSS rendering,
# not an API wait - kept short, not long.
HOVER_REVEAL_OFFSET_PX = 200
HOVER_REVEAL_REPEATS = 3
# Brief pause after moving away - just needs to register the cursor left.
HOVER_REVEAL_PAUSE_S = 0.05
# Settle after moving back onto the target - the hover CSS/JS reveal needs
# real time to render before a screenshot will show it. Deliberately NOT
# cut to zero: too short a settle here was a confirmed real bug (missed
# reveals) - but this is a local rendering wait, not an API wait, so it
# stays short rather than long.
HOVER_REVEAL_SETTLE_S = 0.25

# Template matching (native/template_match.py). A smaller patch is more
# sensitive to just the button/icon itself rather than surrounding content
# that can shift or change between recording and replay. Poll interval is
# how often we re-check while waiting - kept short so a fast API response
# is noticed immediately rather than waiting out a longer interval.
TEMPLATE_PATCH_RADIUS_PX = 14
TEMPLATE_SEARCH_MARGIN_PX = 150
TEMPLATE_MATCH_THRESHOLD = 0.7
TEMPLATE_POLL_INTERVAL_S = 0.15

# Steps that don't get image verification at all (New message button,
# Username input) - assumed to always be present once the page/dialog has
# had a moment to render, so this just waits briefly (local rendering, not
# an API call) then clicks the recorded position directly.
FIXED_STEP_WAIT_S = 0.2

STATUS_SENT = "SENT"
STATUS_SKIPPED_NOT_FOUND = "SKIPPED_NOT_FOUND"
STATUS_FAILED_UI_STUCK = "FAILED_UI_STUCK"
STATUS_ABORTED = "ABORTED"
STATUS_DRY_RUN_OK = "DRY_RUN_OK"
STATUS_FOUND = "FOUND"
