"""Mouse-space <-> screenshot-space pixel conversion.

pyautogui's mouse-move coordinate space and its own screenshot capture were
found to sometimes differ (observed in practice on Windows Server 2022 /
Remote Desktop: GDI-based screen capture and SendInput-based cursor
placement can silently use different effective resolutions - a preview
drawn directly on a screenshot showed correct positions, but real clicks
using those same coordinates landed elsewhere). This measures that gap
directly by comparing pyautogui.size() against a screenshot's actual pixel
dimensions - nothing here is assumed, and no browser involvement is needed
at all to do it.
"""
from __future__ import annotations

import pyautogui


def measure_scale() -> tuple[float, float]:
    """Returns (x, y) multipliers: multiply a mouse-space coordinate by
    these to get the corresponding screenshot-space coordinate."""
    shot = pyautogui.screenshot()
    shot_w, shot_h = shot.size
    mouse_w, mouse_h = pyautogui.size()
    scale_x = (shot_w / mouse_w) if mouse_w else 1.0
    scale_y = (shot_h / mouse_h) if mouse_h else 1.0
    return (scale_x, scale_y)


def mouse_to_shot(x: float, y: float, scale: tuple[float, float]) -> tuple[float, float]:
    return (x * scale[0], y * scale[1])


def shot_to_mouse(x: float, y: float, scale: tuple[float, float]) -> tuple[float, float]:
    return (x / scale[0], y / scale[1])
