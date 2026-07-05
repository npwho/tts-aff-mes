"""Real OS-level mouse/keyboard input wrappers.

Everything in this module performs genuine OS input (Windows SendInput via
pyautogui/pynput) - nothing here talks to the browser DOM. The extension
never sees any of this; it only observes the resulting page state.
"""
from __future__ import annotations

import logging
import random
import sys
import time

import pyautogui
import pyperclip
from pynput.keyboard import Controller as KeyboardController, Key

from . import config

log = logging.getLogger("automation")

_keyboard = KeyboardController()

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0


def ensure_dpi_awareness() -> None:
    """Must be called once, at process start, before any pyautogui call.

    Without this, Windows can silently rescale coordinates under display
    scaling, making calibration inconsistent between runs.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
    except Exception:
        try:
            import ctypes

            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def is_browser_foreground(expected_hwnd: int | None) -> bool:
    """Best-effort foreground-window check. Returns True if we can't check
    (non-Windows) so callers don't hard-fail in that case."""
    if sys.platform != "win32" or expected_hwnd is None:
        return True
    try:
        import win32gui

        return win32gui.GetForegroundWindow() == expected_hwnd
    except Exception:
        return True


def activate_browser_window(hwnd: int | None) -> bool:
    """Best-effort: bring the target browser window to the foreground with a
    genuine OS click on its title bar - the same thing a human alt-tabbing
    back to the browser would do. A blind SetForegroundWindow call is
    unreliable from a background process (Windows restricts programmatic
    foreground-stealing), but a real click is not restricted, since it's
    indistinguishable from actual user input.

    Returns True if the window is (now) foreground, best-effort.
    """
    if sys.platform != "win32" or hwnd is None:
        return True
    try:
        import win32gui

        if win32gui.GetForegroundWindow() == hwnd:
            return True
        left, top, right, _bottom = win32gui.GetWindowRect(hwnd)
        # A point near the top of the window, inside its title bar, so the
        # click activates the window without landing on page content.
        x = left + max(100, (right - left) // 4)
        y = top + 10
        click(x, y, jitter=False)
        time.sleep(0.15)
        return win32gui.GetForegroundWindow() == hwnd
    except Exception:
        return True


def find_browser_hwnd(title_substring: str) -> int | None:
    """Fallback only: matches the first top-level window whose title
    contains the given substring. Unreliable whenever more than one window
    matches (e.g. multiple Chrome windows) - prefer
    current_foreground_hwnd() captured at a moment guaranteed to be the
    right window, such as the user's first real click during recording."""
    if sys.platform != "win32":
        return None
    try:
        import win32gui

        matches = []

        def _cb(hwnd, _):
            title = win32gui.GetWindowText(hwnd)
            if title_substring.lower() in title.lower():
                matches.append(hwnd)

        win32gui.EnumWindows(_cb, None)
        return matches[0] if matches else None
    except Exception:
        return None


def current_foreground_hwnd() -> int | None:
    """Whatever window is actually in the foreground right now. Reliable
    when called at a moment the caller *knows* the right window is
    foreground (e.g. the instant a real user click was just observed)."""
    if sys.platform != "win32":
        return None
    try:
        import win32gui

        return win32gui.GetForegroundWindow()
    except Exception:
        return None


def _jittered(x: float, y: float, jitter_px: int = config.CLICK_JITTER_PX) -> tuple[int, int]:
    return (
        int(x + random.uniform(-jitter_px, jitter_px)),
        int(y + random.uniform(-jitter_px, jitter_px)),
    )


def move_to(x: float, y: float, jitter: bool = True) -> None:
    tx, ty = _jittered(x, y) if jitter else (int(x), int(y))
    duration = random.uniform(config.MOUSE_MOVE_MIN_DURATION_S, config.MOUSE_MOVE_MAX_DURATION_S)
    pyautogui.moveTo(tx, ty, duration=duration)
    actual = pyautogui.position()
    if abs(actual.x - tx) > 3 or abs(actual.y - ty) > 3:
        log.warning(
            "cursor landed at (%s, %s) but was told to go to (%s, %s) - "
            "possible DPI/multi-monitor coordinate mismatch",
            actual.x, actual.y, tx, ty,
        )


def click(x: float, y: float, jitter: bool = True) -> None:
    move_to(x, y, jitter=jitter)
    time.sleep(random.uniform(config.PRE_CLICK_PAUSE_MIN_S, config.PRE_CLICK_PAUSE_MAX_S))
    pyautogui.click()


def inter_step_delay() -> None:
    """Pause between steps within a single username's flow, so the whole
    sequence doesn't fire off unnaturally fast."""
    time.sleep(random.uniform(config.INTER_STEP_DELAY_MIN_S, config.INTER_STEP_DELAY_MAX_S))


def hover_reveal_once(
    x: float,
    y: float,
    offset: float = config.HOVER_REVEAL_OFFSET_PX,
    pause: float = config.HOVER_REVEAL_PAUSE_S,
    settle: float = config.HOVER_REVEAL_SETTLE_S,
) -> None:
    """One away-and-back cycle. Some hover-revealed elements (e.g. a chat
    button that only appears on :hover) don't reliably show up just from
    the cursor teleporting directly onto them - moving away and back is a
    much closer match to a real hover gesture and more reliably fires
    whatever mouseenter/mouseover handling reveals the element. The pause
    after returning to the target is deliberately longer than after moving
    away: the hover CSS/JS reveal needs real time to actually render before
    a screenshot will show it, not just enough time to register the cursor
    left. Call this repeatedly (checking in between) only when the element
    isn't already visible - if it's already there, no wiggling is needed at
    all."""
    away_y = y - offset if y - offset > 0 else y + offset
    move_to(x, away_y, jitter=False)
    time.sleep(pause)
    move_to(x, y, jitter=True)
    time.sleep(settle)


def api_settle_delay() -> None:
    """Short pause right after an action that triggers an API call (pasting
    a username to search, clicking Chat to open a thread), before polling
    for the next element - avoids hammering the CPU with screenshots in the
    first instant when nothing could possibly match yet."""
    time.sleep(config.API_SETTLE_DELAY_S)


def paste_text(text: str) -> None:
    """Set the OS clipboard and send a single real Ctrl+V chord.

    Deliberately never types character-by-character: that would risk a stray
    Enter mid-multiline-message and looks more scripted than a real paste.

    Deliberately does NOT restore whatever was previously on the clipboard
    afterward. That was tried and is dangerous: if the target app takes even
    slightly longer than our fixed post-paste delay to actually read the
    clipboard (very plausible over Remote Desktop's extra input latency),
    the clipboard gets overwritten back to the old content before the paste
    completes - so the app ends up pasting that stale old content instead
    of what was intended. Confirmed in practice: a stale clipboard value
    (an old username list) got pasted into the message box this way. Not
    worth the convenience.
    """
    pyperclip.copy(text)
    time.sleep(0.05)
    with _keyboard.pressed(Key.ctrl):
        _keyboard.press("v")
        _keyboard.release("v")
    # Deliberately NOT cut aggressively: this delay is what stands between
    # the paste actually completing and the next action - too short a
    # value here was a confirmed real bug (a race that let stale clipboard
    # content get pasted). Speed elsewhere, not here.
    time.sleep(0.25)


def select_all() -> None:
    """Ctrl+A on whatever's currently focused - used to select (and so
    replace on the next paste) any leftover text already in a field,
    rather than pasting new text in *alongside* it."""
    with _keyboard.pressed(Key.ctrl):
        _keyboard.press("a")
        _keyboard.release("a")
    time.sleep(0.1)


def select_all_and_delete() -> None:
    """Ctrl+A then Backspace on whatever's currently focused - clears it in
    one shot. Safe to use right after a real click we just performed
    ourselves on the target field (focus is certain), unlike a generic
    "clear whatever's focused" call where Ctrl+A risks selecting the whole
    page if focus isn't exactly where expected."""
    select_all()
    _keyboard.press(Key.backspace)
    _keyboard.release(Key.backspace)
    time.sleep(0.1)


_READBACK_SENTINEL = "\x00__tts_aff_mes_readback_sentinel__\x00"


def read_focused_field() -> str:
    """Ctrl+A then Ctrl+C on whatever's currently focused, returns the
    clipboard afterward - a DOM-free way to read back what a field actually
    contains, used to verify a paste landed correctly.

    The clipboard is poisoned with a sentinel value before Ctrl+C. If
    nothing is actually selected (e.g. focus isn't really on the field,
    which is exactly the failure case this is meant to catch), Ctrl+C is a
    no-op and the clipboard is left holding the sentinel rather than real
    field content - previously this was missed entirely, since the
    clipboard would still hold the text `paste_text()` had just set,
    making an unfocused/empty field falsely "verify" as correct."""
    pyperclip.copy(_READBACK_SENTINEL)
    time.sleep(0.05)
    with _keyboard.pressed(Key.ctrl):
        _keyboard.press("a")
        _keyboard.release("a")
    time.sleep(0.12)
    with _keyboard.pressed(Key.ctrl):
        _keyboard.press("c")
        _keyboard.release("c")
    time.sleep(0.2)
    try:
        result = pyperclip.paste()
    except Exception:
        return ""
    return "" if result == _READBACK_SENTINEL else result


def _normalize_for_compare(s: str) -> str:
    """Collapses all whitespace (including every kind of line break) so
    comparison is about actual content, not formatting. A multi-line
    message pasted into a contenteditable box very often comes back with a
    different line-break structure than what went in - e.g. a blank-line
    paragraph break gets represented as a separate <div>, and copying it
    back out can produce a different number of newlines than the original
    text had. That's a rendering/serialization difference, not a sign the
    wrong content was pasted, so exact string equality is the wrong check
    here."""
    return "".join(s.split())


def paste_text_and_verify(text: str, click_fn, max_attempts: int = 3) -> bool:
    """Clicks the field (click_fn), selects any existing content, pastes
    text, then reads the field back (read_focused_field) to confirm it
    actually landed - there's no DOM to check this against otherwise. Every
    attempt re-clicks first rather than assuming focus was retained from a
    previous failed attempt, since losing focus is a likely reason the
    paste didn't land in the first place. Returns True once verified,
    False if it never matched after max_attempts."""
    for attempt in range(1, max_attempts + 1):
        click_fn()
        select_all()
        paste_text(text)
        actual = read_focused_field()
        if _normalize_for_compare(actual) == _normalize_for_compare(text):
            _keyboard.press(Key.end)
            _keyboard.release(Key.end)
            return True
        log.warning("paste verification mismatch on attempt %d/%d", attempt, max_attempts)
    return False


def press_enter() -> None:
    _keyboard.press(Key.enter)
    _keyboard.release(Key.enter)


def press_escape() -> None:
    _keyboard.press(Key.esc)
    _keyboard.release(Key.esc)


def maybe_human_break(username_index: int) -> None:
    if username_index > 0 and username_index % config.HUMAN_BREAK_EVERY_N == 0:
        time.sleep(random.uniform(config.HUMAN_BREAK_MIN_S, config.HUMAN_BREAK_MAX_S))


def between_username_delay() -> None:
    time.sleep(random.uniform(config.MIN_DELAY_BETWEEN_USERNAMES_S, config.MAX_DELAY_BETWEEN_USERNAMES_S))
