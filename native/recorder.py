"""Guided recording: capture exactly 5 real clicks, one per fixed step, in
order, via a native global mouse listener (pynput) - no browser extension,
no DOM, no selector. The user is told which point to click next; wherever
they actually click for that prompt becomes that step's point, and a small
screenshot patch around it becomes its verification template for replay.
"""
from __future__ import annotations

import json
import logging
import time

from pynput import mouse

from . import automation, geometry, template_match
from .config import RECORDED_FLOW_PATH, TEMPLATES_DIR
from .models import RecordedFlow, RecordedPoint, STEP_LABELS

log = logging.getLogger("recorder")


class Recorder:
    def __init__(self) -> None:
        self._listener: mouse.Listener | None = None
        self._captured: list[RecordedPoint] = []
        self._on_progress = None
        self._scale: tuple[float, float] | None = None
        self._browser_hwnd: int | None = None

    @property
    def next_label(self) -> str | None:
        if self.done:
            return None
        return STEP_LABELS[len(self._captured)]

    @property
    def done(self) -> bool:
        return len(self._captured) >= len(STEP_LABELS)

    def start(self, on_progress=None) -> None:
        self._captured = []
        self._on_progress = on_progress
        self._browser_hwnd = None
        self._scale = geometry.measure_scale()
        self._listener = mouse.Listener(on_click=self._on_click)
        self._listener.start()

    def _on_click(self, x: float, y: float, button, pressed: bool) -> None:
        if button != mouse.Button.left or not pressed or self.done:
            return

        if not self._captured:
            # This real click is, right now, definitely happening in the
            # correct browser window - a far more reliable source of the
            # window handle than guessing by title substring.
            self._browser_hwnd = automation.current_foreground_hwnd()

        # Brief settle so any :active/:focus visual state from the click
        # itself doesn't get baked into the template.
        time.sleep(0.15)
        shot_x, shot_y = geometry.mouse_to_shot(x, y, self._scale)
        template = template_match.capture_template(shot_x, shot_y)

        step_index = len(self._captured) + 1
        TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
        template_path = TEMPLATES_DIR / f"step_{step_index}.png"
        if template is not None:
            template_match.save_template(template, template_path)

        self._captured.append(RecordedPoint(mouse_x=x, mouse_y=y, template_path=str(template_path)))

        if self.done and self._listener:
            self._listener.stop()

        if self._on_progress:
            self._on_progress(len(self._captured), self.next_label)

    def cancel(self) -> None:
        if self._listener:
            self._listener.stop()
            self._listener = None

    def finish(self) -> RecordedFlow | None:
        if self._listener:
            self._listener.stop()
            self._listener = None
        if not self.done:
            return None
        flow = RecordedFlow(points=self._captured, browser_hwnd=self._browser_hwnd)
        self._save(flow)
        return flow

    def _save(self, flow: RecordedFlow) -> None:
        RECORDED_FLOW_PATH.parent.mkdir(parents=True, exist_ok=True)
        RECORDED_FLOW_PATH.write_text(json.dumps(flow.to_dict(), indent=2))
        log.info("Saved recorded flow to %s", RECORDED_FLOW_PATH)

    @staticmethod
    def load() -> RecordedFlow | None:
        if not RECORDED_FLOW_PATH.exists():
            return None
        try:
            return RecordedFlow.from_dict(json.loads(RECORDED_FLOW_PATH.read_text()))
        except (KeyError, json.JSONDecodeError):
            return None

    @staticmethod
    def describe(flow: RecordedFlow) -> str:
        lines = []
        for i, (label, point) in enumerate(zip(STEP_LABELS, flow.points), start=1):
            lines.append(f"{i}. {label}: mouse ({point.mouse_x:.0f}, {point.mouse_y:.0f})")
        return "\n".join(lines)
