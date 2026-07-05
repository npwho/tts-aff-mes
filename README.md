# TikTok Shop Bulk Creator Messenger

A single Python desktop tool that sends the same multi-line message to a
list of TikTok Shop creators through the Seller/Affiliate Center's
messaging UI, using genuine OS-level mouse/keyboard input (indistinguishable
from a human) - never in-page JavaScript automation, which triggers
TikTok's captcha.

There is no browser extension, no WebSocket, no DOM access. Recording
captures 5 real click points (via a native global mouse listener) plus a
small screenshot patch around each one; replay waits for each patch to
visually reappear on screen (confirming the element actually loaded) before
clicking it. See `docs/design.md` for the full rationale and how it works,
including why three earlier, more complex designs (CSS selectors, trusting
the browser's own reported window geometry, browser-placed calibration
markers) were each tried and dropped.

## Setup

From the repo root:
```
python -m venv .venv
.venv\Scripts\activate
pip install -r native/requirements.txt
python -m native.main
```
No browser extension to install - just open the TikTok Shop messaging page
in your normal, already-logged-in browser and run the tool alongside it.

## Usage (run in this order every session)

1. **Record** — click "Start Recording". The tool tells you exactly what to
   click next, one point at a time:
   1. New message button
   2. Username input (search box)
   3. Chat button — you'll need a real username already searched and
      showing a result row so the (hover-revealed) Chat button is visible
      to click
   4. Message input (the chat's text box)
   5. Send button

   It saves automatically the moment all 5 are captured
   (`native/storage/recorded_flow.json`, with each point's verification
   image under `native/storage/templates/`). Nothing is sent to anyone
   during recording — clicking these 5 points doesn't type or send a
   message.
   - Click **"Preview Recording (screenshot)"** afterwards — it takes a
     real screenshot and draws a numbered red circle at each of the 5
     recorded points. The image opens automatically
     (`native/storage/recording_preview.png`). Check every circle actually
     lands on the right button/input before trusting a replay run.
2. **Replay** — paste usernames (one per line) and the message into the
   tool, click Start. For each username, every click waits (up to 8s,
   polling) for its recorded element to visually reappear before acting -
   this is what makes the tool tolerate normal page-load/API latency
   without needing fixed guessed delays. If the Chat button never appears
   after hovering, that username is logged `SKIPPED_NOT_FOUND` and the
   tool moves on automatically. Results are written incrementally to
   `native/logs/run_<timestamp>.csv`.
   - Check **"Dry run"** first — it runs the *entire* click sequence (New
     message → username → hover+click Chat → message input → Send) for
     only the **first** username in your list, but leaves the message
     empty and still clicks Send for real. Most chat UIs simply no-op on
     an empty send, so this safely confirms the whole path works,
     including the actual Send click, without risking a real message
     going out.

## Safety notes

- Default pacing is conservative (randomized 8-20s between usernames, plus
  occasional longer breaks and short pauses between steps within a single
  username) — tune `native/config.py` if you want to go faster, at your own
  risk of platform rate-limiting.
- **Don't resize the browser window or change zoom between recording and
  replay** — recorded points and their verification images are tied to
  where things render at a specific window size/zoom. Moving the window
  (without resizing) is fine, since every click re-measures the current
  mouse/screenshot coordinate relationship fresh. Re-record if you resize
  or change zoom.
- **No automatic captcha detection** — the earlier browser-extension
  design could watch the DOM for one; there's no reliable image-based
  equivalent, since captchas render differently every time. Keep an eye on
  the browser window during a run.
- Sending isn't verified after clicking Send (by design, per explicit
  request) — check `native/logs/*.csv` and the actual chat threads
  afterward if you want confirmation messages went out.
