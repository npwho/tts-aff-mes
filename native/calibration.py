"""Viewport-to-screen coordinate calibration.

Two-point affine transform: screenX = viewportX * scaleX + offsetX (same shape
for Y). Two well-separated points solve for scale + offset simultaneously,
which naturally accounts for browser zoom, chrome height, and Windows DPI
scaling without hardcoding any of them.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from .config import CALIBRATION_PATH
from .models import CalibrationTransform

DRIFT_TOLERANCE_PX = 5


@dataclass
class CalibrationPoint:
    viewport_x: float
    viewport_y: float
    screen_x: float
    screen_y: float


def compute_transform(p1: CalibrationPoint, p2: CalibrationPoint) -> CalibrationTransform:
    dvx = p2.viewport_x - p1.viewport_x
    dvy = p2.viewport_y - p1.viewport_y
    if abs(dvx) < 1 or abs(dvy) < 1:
        raise ValueError("Calibration points are too close together on at least one axis")

    scale_x = (p2.screen_x - p1.screen_x) / dvx
    scale_y = (p2.screen_y - p1.screen_y) / dvy
    offset_x = p1.screen_x - p1.viewport_x * scale_x
    offset_y = p1.screen_y - p1.viewport_y * scale_y

    return CalibrationTransform(
        scale_x=scale_x,
        scale_y=scale_y,
        offset_x=offset_x,
        offset_y=offset_y,
        calibrated_screen_x=int(p1.screen_x),
        calibrated_screen_y=int(p1.screen_y),
    )


def save_calibration(transform: CalibrationTransform) -> None:
    CALIBRATION_PATH.parent.mkdir(parents=True, exist_ok=True)
    CALIBRATION_PATH.write_text(json.dumps(transform.to_dict(), indent=2))


def load_calibration() -> CalibrationTransform | None:
    if not CALIBRATION_PATH.exists():
        return None
    try:
        return CalibrationTransform.from_dict(json.loads(CALIBRATION_PATH.read_text()))
    except Exception:
        return None


def has_drifted(transform: CalibrationTransform, current_screen_x: int, current_screen_y: int) -> bool:
    """True if the browser window appears to have moved since calibration,
    meaning the transform is likely stale and re-calibration should be
    prompted rather than silently continuing."""
    dx = abs(current_screen_x - transform.calibrated_screen_x)
    dy = abs(current_screen_y - transform.calibrated_screen_y)
    return dx > DRIFT_TOLERANCE_PX or dy > DRIFT_TOLERANCE_PX
