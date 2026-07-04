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

There's no CSS-selector matching anywhere in the replay path. TikTok's
generated/state-dependent class names made re-locating elements by selector
unreliable in practice (a class can exist only while a tooltip happens to be
showing, or only while an input is focused). Instead, the native tool clicks
*fixed viewport positions* recorded once during the Record step, converted to
a real screen coordinate against the browser window's live on-screen position
every single time. The extension's only job during replay is answering "does
this username exist" and reading back text at a point to verify a
paste/send — never "where is this element now."

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

There's no manual calibration step either: the extension reports the browser
window's actual on-screen geometry alongside the request, and the native
side *measures* (rather than assumes) the scale between its own coordinate
space and the browser's, so it's correct even if Windows' per-process DPI
awareness didn't take effect the way you'd expect. Moving the browser window
mid-run is handled automatically, since geometry is re-fetched on every click.

## Usage (run in this order every session)

1. **Record** — click "Start Recording", then manually perform the entire
   flow yourself on the very first creator: click "New message", search the
   username, hover the result row, click the revealed "Chat" button, click
   the message box, paste a placeholder message. You do **not** need to
   press Enter or actually send anything — the recorder only needs to see
   you click/focus/paste on each element; click "Stop Recording & Save" as
   soon as you've pasted into the message box. Nothing is sent to that
   creator unless you choose to press Enter yourself.
2. **Replay** — paste the remaining usernames (one per line) and the message
   into the tool, click Start. Each username is looked up fresh; if no
   search result exists (username doesn't exist), it's logged
   `SKIPPED_NOT_FOUND` and the tool moves on automatically. Results are
   written incrementally to `native/logs/run_<timestamp>.csv`. Check "Dry
   run" first to click through the whole flow (New message → search → hover
   + click Chat → focus message box) without ever pasting the message or
   pressing Enter, to visually confirm it's clicking the right elements
   before sending anything for real.

## Safety notes

- If the extension detects a captcha on the page, or loses its WebSocket
  connection, the whole run pauses rather than continuing to click blind.
- Default pacing is conservative (randomized 8-20s between usernames, plus
  occasional longer breaks) — tune `native/config.py` if you want to go
  faster, at your own risk of platform rate-limiting.
- **Don't resize the browser window or change zoom between recording and
  replay** — the recorded click positions are fixed viewport coordinates, so
  a different window size/zoom shifts where everything actually is on the
  page. Moving the window (without resizing) is fine. Re-record if you
  change either.
