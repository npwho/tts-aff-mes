"""Viewport-rect -> real screen coordinate conversion.

No calibration step: the extension reports the browser window's OS-level
geometry alongside every element rect it locates, so this is recomputed
fresh on every single click. That also means a window that gets moved or
resized mid-run is handled automatically, with no separate drift check
needed.

Deliberately does NOT trust `devicePixelRatio` at face value to convert to
pyautogui's coordinate space. That assumes ensure_dpi_awareness() (see
automation.py) actually succeeded in making this process report true
physical pixels - but Windows only lets DPI awareness be set once per
process, and Python's own bundled manifest can already lock in a mode
before our code ever runs, silently making the awareness call a no-op.
When that happens, pyautogui ends up operating in a different coordinate
space than the math assumes, and every click comes out scaled wrong in a
way that's invisible from Python alone.

Instead, the actual relationship between pyautogui's coordinate space and
the browser's CSS-pixel space is measured directly: pyautogui.size() vs.
the browser-reported screen.width. Their ratio *is* the correct multiplier,
regardless of whether DPI awareness worked.
"""
from __future__ import annotations

import logging

import pyautogui

log = logging.getLogger("geometry")


def _measured_scale(browser_screen_width: float) -> float:
    if not browser_screen_width:
        return 1.0
    try:
        pyautogui_width = pyautogui.size().width
    except Exception:
        return 1.0
    scale = pyautogui_width / browser_screen_width
    log.debug(
        "measured scale=%.4f (pyautogui width=%s, browser screen width=%s)",
        scale, pyautogui_width, browser_screen_width,
    )
    return scale


def viewport_rect_to_screen(rect: dict, geometry: dict) -> tuple[float, float]:
    cx = rect["x"] + rect["w"] / 2
    cy = rect["y"] + rect["h"] / 2

    # Height of the browser's own UI (tabs/address bar) above the page
    # viewport. Left/right chrome is assumed to be 0, which holds for the
    # overwhelming majority of normal browser windows.
    chrome_top = max(0, geometry.get("outerHeight", 0) - geometry.get("innerHeight", 0))
    scale = _measured_scale(geometry.get("screenWidth"))

    screen_x = (geometry.get("screenX", 0) + cx) * scale
    screen_y = (geometry.get("screenY", 0) + chrome_top + cy) * scale
    return screen_x, screen_y
