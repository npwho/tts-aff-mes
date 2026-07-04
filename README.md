# TikTok Shop Bulk Creator Messenger

Two connected components that together send the same multi-line message to a
list of TikTok Shop creators through the Seller/Affiliate Center's messaging
UI, without ever synthesizing input events in the page (which triggers
TikTok's captcha):

- **`extension/`** — a Manifest V3 browser extension that only *observes* the
  DOM (element existence, position, text). It never calls `.click()`,
  `.dispatchEvent()`, sets `.value`, or calls `.focus()` on page elements.
- **`native/`** — a Python desktop tool that performs the real OS-level mouse
  and keyboard input (genuine clicks/keystrokes, indistinguishable from a
  human), driven by what the extension reports over a local WebSocket
  connection (`ws://127.0.0.1:8765`).

See `docs/protocol.md` for the full message schema between the two, and the
plan file this was built from for the design rationale.

## Setup

1. **Extension**: open `chrome://extensions` (or `edge://extensions`), enable
   Developer Mode, "Load unpacked", select the `extension/` folder.
   - Edit `extension/manifest.json`'s `host_permissions` / content script
     `matches` to the actual Seller/Affiliate Center domain you use if it
     differs from the placeholders already there.
2. **Native tool**: from the repo root,
   ```
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r native/requirements.txt
   python -m native.main
   ```

## Usage (run in this order every session)

1. **Calibrate** — open the TikTok Shop messaging page, click "Start
   Calibration" in the tool. Two red markers appear on the page one at a
   time; for each, hover your *real* mouse over the marker and click the
   corresponding "Capture Point" button in the tool. This solves for the
   viewport-to-screen coordinate transform (handles zoom/DPI automatically).
   Re-run this if you move/resize the browser window or change zoom.
2. **Record** — click "Start Recording", then manually perform the entire
   flow yourself on the very first creator: click "New message", search the
   username, hover the result row, click the revealed "Chat" button, click
   the message box, paste the message, press Enter. Click "Stop Recording &
   Save" once you see it sent. This creator is already messaged — don't
   include them in the replay list.
3. **Replay** — paste the remaining usernames (one per line) and the message
   into the tool, click Start. Each username is looked up fresh; if no
   search result exists (username doesn't exist), it's logged
   `SKIPPED_NOT_FOUND` and the tool moves on automatically. Results are
   written incrementally to `native/logs/run_<timestamp>.csv`.

## Safety notes

- If the extension detects a captcha on the page, or loses its WebSocket
  connection, the whole run pauses rather than continuing to click blind.
- Default pacing is conservative (randomized 8-20s between usernames, plus
  occasional longer breaks) — tune `native/config.py` if you want to go
  faster, at your own risk of platform rate-limiting.
- Keep the browser window focused and don't move/resize it mid-run; the tool
  checks foreground state per username but a moved window invalidates
  calibration silently otherwise.
