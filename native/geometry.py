"""Viewport-rect -> real screen coordinate conversion.

No calibration step: the extension reports the browser window's OS-level
geometry (window.screenX/screenY, outer/inner size, devicePixelRatio)
alongside every element rect it locates, so this is recomputed fresh on
every single click. That also means a window that gets moved/resized mid
run is handled automatically, with no separate drift check needed.
"""
from __future__ import annotations


def viewport_rect_to_screen(rect: dict, geometry: dict) -> tuple[float, float]:
    cx = rect["x"] + rect["w"] / 2
    cy = rect["y"] + rect["h"] / 2

    # Height of the browser's own UI (tabs/address bar) above the page
    # viewport. Left/right chrome is assumed to be 0, which holds for the
    # overwhelming majority of normal browser windows.
    chrome_top = max(0, geometry.get("outerHeight", 0) - geometry.get("innerHeight", 0))
    dpr = geometry.get("devicePixelRatio") or 1

    screen_x = (geometry.get("screenX", 0) + cx) * dpr
    screen_y = (geometry.get("screenY", 0) + chrome_top + cy) * dpr
    return screen_x, screen_y
