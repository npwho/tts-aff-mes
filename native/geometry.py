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
pixel positions in that screenshot.

That screenshot's pixel space is NOT automatically the same coordinate
space pyautogui.moveTo()/click() use for real cursor movement - on the same
Windows Server / RDP setup that broke the browser's own geometry reporting,
GDI-based screen capture (what PIL.ImageGrab / pyautogui.screenshot() use)
and SendInput-based cursor placement can silently operate at different
effective resolutions (confirmed in practice: a preview image rendered by
drawing directly on the screenshot showed circles landing correctly, but
real clicks using those same screenshot-pixel coordinates did not land in
the same place). So that gap is measured too, via pyautogui.size() (the
resolution pyautogui's own mouse-move calls are expressed in) vs. the
screenshot's actual pixel dimensions - their ratio corrects a screenshot
pixel position into a real mouse-movable coordinate.
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
    """Two affine transforms sharing the same measured viewport anchor
    points: one lands in screenshot pixel space (for drawing previews on a
    screenshot), the other in pyautogui's mouse-movable coordinate space
    (for actual clicking). They are only the same when screenshot capture
    and cursor placement happen to share a coordinate space - not something
    to assume, per the module docstring."""

    shot_scale_x: float
    shot_scale_y: float
    shot_offset_x: float
    shot_offset_y: float
    mouse_scale_x: float
    mouse_scale_y: float
    mouse_offset_x: float
    mouse_offset_y: float

    def viewport_point_to_mouse(self, x: float, y: float) -> tuple[float, float]:
        return (x * self.mouse_scale_x + self.mouse_offset_x, y * self.mouse_scale_y + self.mouse_offset_y)

    def rect_to_mouse(self, rect: dict) -> tuple[float, float]:
        cx = rect["x"] + rect["w"] / 2
        cy = rect["y"] + rect["h"] / 2
        return self.viewport_point_to_mouse(cx, cy)

    def viewport_point_to_screenshot(self, x: float, y: float) -> tuple[float, float]:
        return (x * self.shot_scale_x + self.shot_offset_x, y * self.shot_scale_y + self.shot_offset_y)

    def rect_to_screenshot(self, rect: dict) -> tuple[float, float]:
        cx = rect["x"] + rect["w"] / 2
        cy = rect["y"] + rect["h"] / 2
        return self.viewport_point_to_screenshot(cx, cy)


def _find_color_center(arr: np.ndarray, rgb: tuple[int, int, int], tol: int = MARKER_COLOR_TOLERANCE):
    r, g, b = rgb
    diff = np.abs(arr[:, :, :3].astype(int) - np.array([r, g, b]))
    mask = np.all(diff <= tol, axis=-1)
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return None
    return (float(xs.mean()), float(ys.mean()))


def _affine_2point(p1_viewport, p2_viewport, p1_target, p2_target):
    dvx = p2_viewport[0] - p1_viewport[0]
    dvy = p2_viewport[1] - p1_viewport[1]
    scale_x = (p2_target[0] - p1_target[0]) / dvx
    scale_y = (p2_target[1] - p1_target[1]) / dvy
    offset_x = p1_target[0] - p1_viewport[0] * scale_x
    offset_y = p1_target[1] - p1_viewport[1] * scale_y
    return scale_x, scale_y, offset_x, offset_y


def calibrate_via_screenshot() -> PixelTransform | None:
    """Must be called only after the extension has placed both markers and
    the browser window is confirmed foreground/visible. Returns None if
    either marker couldn't be found (e.g. hidden behind another window)."""
    screenshot = pyautogui.screenshot()
    shot_w, shot_h = screenshot.size
    arr = np.array(screenshot)

    origin_shot = _find_color_center(arr, MARKER_COLOR_ORIGIN)
    far_shot = _find_color_center(arr, MARKER_COLOR_FAR)
    if origin_shot is None or far_shot is None:
        log.warning(
            "calibration markers not found in screenshot (origin=%s, far=%s) - "
            "is the browser window fully visible and not covered by another window?",
            origin_shot, far_shot,
        )
        return None

    ox, oy = MARKER_VIEWPORT_ORIGIN
    fx, fy = MARKER_VIEWPORT_FAR
    if fx == ox or fy == oy:
        return None

    # Screenshot-pixel-space transform: for drawing on a freshly-taken
    # screenshot (the preview feature).
    shot_scale_x, shot_scale_y, shot_offset_x, shot_offset_y = _affine_2point(
        (ox, oy), (fx, fy), origin_shot, far_shot
    )

    # Correct screenshot pixel positions into pyautogui's own mouse-movable
    # coordinate space by comparing the screenshot's actual dimensions
    # against pyautogui.size() - the resolution its own moveTo()/click()
    # calls are expressed in. If screen capture and cursor placement happen
    # to already share a coordinate space, this ratio is just 1.0 and
    # changes nothing.
    mouse_w, mouse_h = pyautogui.size()
    corr_x = (mouse_w / shot_w) if shot_w else 1.0
    corr_y = (mouse_h / shot_h) if shot_h else 1.0
    origin_mouse = (origin_shot[0] * corr_x, origin_shot[1] * corr_y)
    far_mouse = (far_shot[0] * corr_x, far_shot[1] * corr_y)

    mouse_scale_x, mouse_scale_y, mouse_offset_x, mouse_offset_y = _affine_2point(
        (ox, oy), (fx, fy), origin_mouse, far_mouse
    )

    transform = PixelTransform(
        shot_scale_x, shot_scale_y, shot_offset_x, shot_offset_y,
        mouse_scale_x, mouse_scale_y, mouse_offset_x, mouse_offset_y,
    )
    log.info(
        "calibrated: screenshot=%dx%d pyautogui.size=%dx%d correction=(%.4f, %.4f) | "
        "origin_shot=%s far_shot=%s -> shot_scale=(%.4f, %.4f) mouse_scale=(%.4f, %.4f) mouse_offset=(%.1f, %.1f)",
        shot_w, shot_h, mouse_w, mouse_h, corr_x, corr_y,
        origin_shot, far_shot, shot_scale_x, shot_scale_y, mouse_scale_x, mouse_scale_y, mouse_offset_x, mouse_offset_y,
    )
    return transform


def render_preview(transform: PixelTransform, flow, output_path) -> None:
    """Takes a fresh screenshot and draws a numbered marker at each of the
    recorded flow's 5 points, *in screenshot pixel space*, so what you see
    in the saved image is exactly where those pixels are in the screenshot -
    separate from (and not necessarily equal to) the mouse-space coordinates
    actually used to click."""
    screenshot = pyautogui.screenshot()
    draw = ImageDraw.Draw(screenshot)
    radius = 14
    for i, (label, rect) in enumerate(flow.as_list(), start=1):
        sx, sy = transform.rect_to_screenshot(rect)
        draw.ellipse([sx - radius, sy - radius, sx + radius, sy + radius], outline=(255, 0, 0), width=3)
        draw.line([sx - radius, sy, sx + radius, sy], fill=(255, 0, 0), width=1)
        draw.line([sx, sy - radius, sx, sy + radius], fill=(255, 0, 0), width=1)
        draw.text((sx + radius + 4, sy - radius), f"{i}: {label}", fill=(255, 0, 0))
    screenshot.save(output_path)
