"""Screenshot-based element verification via template matching.

Records a small image patch around each critical point at Record time.
Replay waits for that same patch to visually reappear (within a search
window around the expected position) before acting, which confirms the
element actually rendered rather than blindly clicking a pre-computed
coordinate - and naturally implements "wait for it to load" for anything
that triggers an API call, since the poll loop just keeps checking until
it appears or times out. Also refines the exact click point to wherever
the best match actually is, correcting small drift from the originally
recorded position.
"""
from __future__ import annotations

import logging
import time

import cv2
import numpy as np
import pyautogui
from PIL import Image

from . import config

log = logging.getLogger("template_match")


def capture_template(shot_x: float, shot_y: float, radius: int = config.TEMPLATE_PATCH_RADIUS_PX) -> np.ndarray | None:
    """Crops a small patch centered on a screenshot-space point, from a
    fresh screenshot taken right now."""
    screenshot = pyautogui.screenshot()
    arr = np.array(screenshot)
    h, w = arr.shape[:2]
    x0, y0 = max(0, int(shot_x - radius)), max(0, int(shot_y - radius))
    x1, y1 = min(w, int(shot_x + radius)), min(h, int(shot_y + radius))
    if x1 <= x0 or y1 <= y0:
        return None
    return arr[y0:y1, x0:x1].copy()


def save_template(template: np.ndarray, path) -> None:
    Image.fromarray(template).save(path)


def load_template(path) -> np.ndarray:
    return np.array(Image.open(path))


def find_once(
    template: np.ndarray,
    expected_x: float,
    expected_y: float,
    margin: int = config.TEMPLATE_SEARCH_MARGIN_PX,
    threshold: float = config.TEMPLATE_MATCH_THRESHOLD,
):
    """Single-shot check against a fresh screenshot right now - no polling,
    no waiting. Returns (x, y, confidence) in screenshot space, or None."""
    return _find_in_region(template, expected_x, expected_y, margin, threshold)


def _find_in_region(template: np.ndarray, expected_x: float, expected_y: float, margin: int, threshold: float):
    screenshot = pyautogui.screenshot()
    arr = np.array(screenshot)
    h, w = arr.shape[:2]
    th, tw = template.shape[:2]

    x0 = max(0, int(expected_x - margin))
    y0 = max(0, int(expected_y - margin))
    x1 = min(w, int(expected_x + margin + tw))
    y1 = min(h, int(expected_y + margin + th))
    region = arr[y0:y1, x0:x1]
    if region.shape[0] < th or region.shape[1] < tw:
        return None

    region_bgr = cv2.cvtColor(region, cv2.COLOR_RGB2BGR)
    template_bgr = cv2.cvtColor(template, cv2.COLOR_RGB2BGR)
    result = cv2.matchTemplate(region_bgr, template_bgr, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    if max_val < threshold:
        return None
    center_x = x0 + max_loc[0] + tw / 2
    center_y = y0 + max_loc[1] + th / 2
    return (center_x, center_y, max_val)


def wait_for_match(
    template: np.ndarray,
    expected_x: float,
    expected_y: float,
    max_wait_s: float = config.STEP_MAX_WAIT_S,
    margin: int = config.TEMPLATE_SEARCH_MARGIN_PX,
    threshold: float = config.TEMPLATE_MATCH_THRESHOLD,
    should_abort=None,
):
    """Polls a fresh screenshot until the template is found near the
    expected point, or gives up after max_wait_s. Returns (x, y,
    confidence) in screenshot space, or None. `should_abort`, if given, is
    checked each poll so a Stop request can interrupt a long wait."""
    deadline = time.monotonic() + max_wait_s
    while True:
        if should_abort and should_abort():
            return None
        found = _find_in_region(template, expected_x, expected_y, margin, threshold)
        if found:
            log.info("template matched at (%.0f, %.0f) confidence=%.3f", found[0], found[1], found[2])
            return found
        if time.monotonic() >= deadline:
            log.warning(
                "template not found near (%.0f, %.0f) after %.1fs (threshold=%.2f)",
                expected_x, expected_y, max_wait_s, threshold,
            )
            return None
        time.sleep(config.TEMPLATE_POLL_INTERVAL_S)
