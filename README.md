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

There's no CSS-selector matching anywhere, and no inferring intent from a
recorded event stream either — both were tried and both broke in practice.
Recording is **guided**: the tool asks for exactly 5 clicks, one at a time,
in a fixed order (New message button, Username input, Chat button hover
point, Message input, Send button). Whatever you click *is* that point.
Replay then clicks those 5 fixed positions directly, converted to a real
screen coordinate via a screenshot-based pixel calibration (see below) - not
by trusting anything the browser self-reports about its own window geometry.
The extension's only job during replay is answering "does this username
exist" and reading back text at a point to verify a paste/send — never
"where is this element now."

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

There's no manual calibration step, but there is an *automatic* one, done
fresh before every username: the extension places two distinctly-colored
marker squares at known page positions, the native tool takes a real
screenshot and finds them by their exact color, and the two markers' known
vs. found positions solve the coordinate transform. This was built after
discovering that Chrome's self-reported window geometry
(`window.screenX`/`outerWidth`/`innerWidth`/`devicePixelRatio`) is
internally inconsistent on Windows Server / Remote Desktop sessions — no
formula built on that data could ever be reliable there, so nothing here
trusts it. Moving the browser window mid-run is handled automatically, since
calibration re-runs every time. Only requirement: the browser window has to
actually be visible on screen (not minimized, not covered by another window)
when a click or calibration happens.

## Usage (run in this order every session)

1. **Record** — click "Start Recording". The tool tells you exactly what to
   click next, one point at a time:
   1. New message button
   2. Username input (search box)
   3. Chat button — you'll need a real username already searched and showing
      a result row so the (hover-revealed) Chat button is visible to click
   4. Message input (the chat's text box)
   5. Send button
   It saves automatically the moment all 5 are captured
   (`native/storage/recorded_flow.json`). Nothing is sent to anyone during
   recording — clicking these 5 points doesn't type or send a message.
   - Click **"Preview Recording (screenshot)"** afterwards — it calibrates,
     takes a real screenshot, and draws a numbered red circle at exactly
     where each of the 5 points will click. The image opens automatically
     (`native/storage/recording_preview.png`). Check every circle actually
     lands on the right button/input before trusting a replay run.
2. **Replay** — paste usernames (one per line) and the message into the
   tool, click Start. Each username is looked up fresh; if no search result
   exists (username doesn't exist), it's logged `SKIPPED_NOT_FOUND` and the
   tool moves on automatically. Results are written incrementally to
   `native/logs/run_<timestamp>.csv`.
   - Check **"Dry run"** first — it runs the *entire* click sequence
     (New message → username → hover+click Chat → message input → Send) for
     only the **first** username in your list, but leaves the message empty
     and still clicks Send for real. Most chat UIs simply no-op on an empty
     send, so this safely confirms the whole path works, including the
     actual Send click, without risking a real message going out.

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
