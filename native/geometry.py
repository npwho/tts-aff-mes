"""Viewport-coordinate -> real screen coordinate conversion, via screenshot.

Chrome's self-reported window geometry (window.screenX/screenY,
outerWidth/outerHeight, innerWidth/innerHeight, devicePixelRatio) turned out
to be internally inconsistent on at least one real deployment target
(Windows Server 2022) - innerWidth was observed larger than outerWidth,
which is physically impossible for a real window, and devicePixelRatio was
reported as 0.8, an abnormal value. This is a known category of problem
with Remote Desktop / virtualized-display Windows sessions, where per-app
DPI/window-metric APIs don't reflect the physical display truthfully.

Rather than trust ANY browser-reported metric, this calibrates by placing
two distinctly-colored marker elements on the page at known viewport
coordinates, taking a real screenshot, and finding the markers' actual
screen pixel positions directly. That's ground truth: whatever pyautogui
sees in the screenshot is, by definition, in the same coordinate space
pyautogui's own mouse-move calls use, so there is no unit-conversion
assumption left to get wrong.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pyautogui
from PIL import ImageDraw

log = logging.getLogger("geometry")

# Two viewport points far enough apart to get a precise scale reading, and
# small enough to sit inside any reasonably-sized browser window.
MARKER_VIEWPORT_ORIGIN = (20, 20)
MARKER_VIEWPORT_FAR = (520, 520)
MARKER_SIZE_PX = 40
# Distinct, essentially never-occurring-naturally colors.
MARKER_COLOR_ORIGIN = (255, 0, 255)  # magenta
MARKER_COLOR_FAR = (0, 255, 255)  # cyan
MARKER_COLOR_TOLERANCE = 15


@dataclass
class PixelTransform:
    scale_x: float
    scale_y: float
    offset_x: float
    offset_y: float

    def viewport_point_to_screen(self, x: float, y: float) -> tuple[float, float]:
        return (x * self.scale_x + self.offset_x, y * self.scale_y + self.offset_y)

    def rect_to_screen(self, rect: dict) -> tuple[float, float]:
        cx = rect["x"] + rect["w"] / 2
        cy = rect["y"] + rect["h"] / 2
        return self.viewport_point_to_screen(cx, cy)


def _find_color_center(arr: np.ndarray, rgb: tuple[int, int, int], tol: int = MARKER_COLOR_TOLERANCE):
    r, g, b = rgb
    diff = np.abs(arr[:, :, :3].astype(int) - np.array([r, g, b]))
    mask = np.all(diff <= tol, axis=-1)
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return None
    return (float(xs.mean()), float(ys.mean()))


def calibrate_via_screenshot() -> PixelTransform | None:
    """Must be called only after the extension has placed both markers and
    the browser window is confirmed foreground/visible. Returns None if
    either marker couldn't be found (e.g. hidden behind another window)."""
    screenshot = pyautogui.screenshot()
    arr = np.array(screenshot)

    origin_screen = _find_color_center(arr, MARKER_COLOR_ORIGIN)
    far_screen = _find_color_center(arr, MARKER_COLOR_FAR)
    if origin_screen is None or far_screen is None:
        log.warning(
            "calibration markers not found in screenshot (origin=%s, far=%s) - "
            "is the browser window fully visible and not covered by another window?",
            origin_screen, far_screen,
        )
        return None

    ox, oy = MARKER_VIEWPORT_ORIGIN
    fx, fy = MARKER_VIEWPORT_FAR
    dvx, dvy = fx - ox, fy - oy
    if dvx == 0 or dvy == 0:
        return None

    scale_x = (far_screen[0] - origin_screen[0]) / dvx
    scale_y = (far_screen[1] - origin_screen[1]) / dvy
    offset_x = origin_screen[0] - ox * scale_x
    offset_y = origin_screen[1] - oy * scale_y

    transform = PixelTransform(scale_x, scale_y, offset_x, offset_y)
    log.info(
        "calibrated via screenshot: origin_screen=%s far_screen=%s -> scale=(%.4f, %.4f) offset=(%.1f, %.1f)",
        origin_screen, far_screen, scale_x, scale_y, offset_x, offset_y,
    )
    return transform


def render_preview(transform: PixelTransform, steps, output_path) -> None:
    """Takes a fresh screenshot and draws a numbered marker at each step's
    computed screen click point, so a recording can be visually confirmed
    before ever running real OS clicks against it."""
    screenshot = pyautogui.screenshot()
    draw = ImageDraw.Draw(screenshot)
    radius = 14
    for step in steps:
        sx, sy = transform.rect_to_screen(step.rect_viewport)
        draw.ellipse([sx - radius, sy - radius, sx + radius, sy + radius], outline=(255, 0, 0), width=3)
        draw.line([sx - radius, sy, sx + radius, sy], fill=(255, 0, 0), width=1)
        draw.line([sx, sy - radius, sx, sy + radius], fill=(255, 0, 0), width=1)
        label = f"{step.step_id}:{step.kind}"
        draw.text((sx + radius + 4, sy - radius), label, fill=(255, 0, 0))
    screenshot.save(output_path)
