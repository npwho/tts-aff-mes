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
        time.sleep(0.2)
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


def hover_settle() -> None:
    time.sleep(random.uniform(config.HOVER_SETTLE_MIN_S, config.HOVER_SETTLE_MAX_S))


def paste_text(text: str) -> None:
    """Set the OS clipboard and send a single real Ctrl+V chord.

    Deliberately never types character-by-character: that would risk a stray
    Enter mid-multiline-message and looks more scripted than a real paste.
    """
    prior_clipboard = None
    try:
        prior_clipboard = pyperclip.paste()
    except Exception:
        pass

    pyperclip.copy(text)
    time.sleep(0.05)
    with _keyboard.pressed(Key.ctrl):
        _keyboard.press("v")
        _keyboard.release("v")
    time.sleep(0.15)

    if prior_clipboard is not None:
        try:
            pyperclip.copy(prior_clipboard)
        except Exception:
            pass


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
