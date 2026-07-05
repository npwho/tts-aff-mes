# Design: fully native, screenshot/template-based automation

There is no browser extension, no WebSocket, no DOM access of any kind
anymore. Everything lives in one Python process (`native/`) and drives the
browser purely through genuine OS-level input (`pyautogui`/`pynput`) and
real screenshots.

## Why this replaced the earlier browser-extension design

Three earlier architectures were tried and each broke in practice:

1. **CSS selector matching** (extension re-locates each element by selector
   on every replay) - broke repeatedly against TikTok's generated and
   state-dependent class names (a class that only exists while a tooltip
   happens to be showing, or only while an element is focused).
2. **Browser-reported window geometry** (`window.screenX`, `outerWidth`,
   `innerWidth`, `devicePixelRatio`) - found to be internally inconsistent
   on Windows Server 2022 / Remote Desktop (`innerWidth` observed larger
   than `outerWidth`, which is physically impossible; `devicePixelRatio`
   reported as `0.8`, an abnormal value). No coordinate formula built on
   that data could ever be reliable there.
3. **Browser-placed calibration markers + screenshot** - fixed the above,
   but still needed the extension/WebSocket link just to place/remove
   marker elements, and depended on the page's CSS not blocking them.

The current design needs none of that. Only two things are ever measured,
both purely on the native side:

- **Mouse-space vs. screenshot-space scale** (`geometry.py`): `pyautogui`'s
  own mouse-move coordinate space and its own screenshot capture were found
  to sometimes differ (GDI-based screen capture and SendInput-based cursor
  placement can silently use different effective resolutions on the same
  Windows Server / RDP setup). Measured directly via `pyautogui.size()` vs.
  a screenshot's actual pixel dimensions - no browser involvement needed.
- **Whether a recorded element is actually on screen right now**
  (`template_match.py`): a small image patch captured around each point at
  record time, matched against a fresh screenshot via OpenCV
  `matchTemplate` before every click.

## Recording (`recorder.py`)

A `pynput.mouse.Listener` runs globally (not tied to any window) while
recording is active. The GUI tells the user which of 5 fixed points to
click next, in order:

1. New message button
2. Username input
3. Chat button (hover point)
4. Message input
5. Send button

Whatever the user actually clicks for a given prompt *is* that step - no
inference, no selector, no DOM. For each click: the real mouse-space
coordinate is recorded, and a small screenshot patch (`TEMPLATE_PATCH_RADIUS_PX`,
default 30px radius) centered on it (converted to screenshot space via the
measured scale) is saved as that step's verification template
(`native/storage/templates/step_N.png`). The browser window handle is also
captured from `GetForegroundWindow()` at the very first click - guaranteed
correct, since the user is actively clicking in that window at that exact
moment, unlike guessing by window title.

Saved to `native/storage/recorded_flow.json` (`models.RecordedFlow`).

## Replay (`replayer.py`)

Per username, a fixed sequence (not a generic loop):

1. **New message** - wait for its template to match, click.
2. **Username input** - wait, click, paste the username, then a short
   settle delay (`API_SETTLE_DELAY_S`) before the next step, since pasting
   triggers a search API call.
3. **Chat button** - hover at its recorded point (which lies inside the
   search result row, so hovering there *is* what triggers the row's CSS
   hover reveal) and poll for its template to appear, up to
   `STEP_MAX_WAIT_S` (default 8s). **If it never appears, that's treated as
   the username not existing** (`SKIPPED_NOT_FOUND`) - there's no DOM
   existence check anymore, so this timeout is the only signal for it.
4. **Message input** - wait (thread loading is another API call), click,
   paste the message unless this is a dry run.
5. **Send** - wait, click. Not verified either way; the run just moves on.

Every wait-and-click goes through the same `template_match.wait_for_match`
polling loop: take a screenshot, search a margin (`TEMPLATE_SEARCH_MARGIN_PX`)
around the expected position for the recorded template, and if found,
refine the click target to wherever the match actually is (correcting any
small drift from the originally recorded position) rather than blindly
trusting the original coordinate. This is what makes "wait for slow-loading
elements" and "confirm before clicking" the same mechanism.

A short random delay (`INTER_STEP_DELAY_MIN_S`/`MAX_S`) is inserted between
steps so the whole sequence doesn't fire off unnaturally fast.

## What was deliberately dropped, and why

- **Username-existence check via DOM** - replaced by the Chat button
  template timeout, per explicit request to make the whole tool
  image/coordinate-based rather than depend on any browser-side
  introspection.
- **Send verification** (previously: DOM readback confirming the message
  box went empty) - dropped per explicit request; Send is clicked and the
  run moves on without confirming.
- **Captcha detection** - the old extension watched the DOM for a captcha
  container appearing anywhere on the page. There's no reliable
  image-template equivalent (captchas render differently every time), so
  this is not currently replaced - if you're worried about triggering one,
  monitor the browser window manually during a run.
- **Reset-state verification** (previously: DOM check confirming no dialog
  remained open) - replaced with a best-effort double Escape keypress
  between usernames, with no way to confirm it actually worked.
