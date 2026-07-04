"""Guided recording: capture exactly 5 clicks, one per fixed step, in order.

No inference from a raw click/focus/paste stream, no heuristic tagging, no
selector. The user is told which point to click next; whatever they click
is recorded as that step's rect and nothing else.
"""
from __future__ import annotations

import json
import logging

from .config import RECORDED_FLOW_PATH
from .models import RecordedFlow, STEP_LABELS
from .ws_server import NativeBridge

log = logging.getLogger("recorder")


class Recorder:
    def __init__(self, bridge: NativeBridge) -> None:
        self.bridge = bridge
        self._recording = False
        self._captured: list[dict] = []
        self._on_progress = None
        bridge.on("record_event", self._on_record_event)

    @property
    def next_label(self) -> str | None:
        if len(self._captured) >= len(STEP_LABELS):
            return None
        return STEP_LABELS[len(self._captured)]

    @property
    def done(self) -> bool:
        return len(self._captured) >= len(STEP_LABELS)

    def _on_record_event(self, msg: dict) -> None:
        if not self._recording or self.done:
            return
        if msg.get("eventType") != "click":
            return
        rect = msg.get("rectViewport")
        if not rect:
            return
        self._captured.append(rect)
        if self.done:
            self._recording = False
        if self._on_progress:
            self._on_progress(len(self._captured), self.next_label)

    async def start(self, on_progress=None) -> None:
        self._captured = []
        self._recording = True
        self._on_progress = on_progress
        await self.bridge.send_fire_and_forget("start_recording")

    async def cancel(self) -> None:
        self._recording = False
        await self.bridge.send_fire_and_forget("stop_recording")

    async def finish(self) -> RecordedFlow | None:
        """Call once `done` is True. Saves and returns the flow, or None if
        called before all 5 points were captured."""
        self._recording = False
        await self.bridge.send_fire_and_forget("stop_recording")
        if not self.done:
            return None
        flow = RecordedFlow(*self._captured)
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
        for i, (label, rect) in enumerate(flow.as_list(), start=1):
            lines.append(f"{i}. {label}: viewport ({rect['x']:.0f}, {rect['y']:.0f}) size {rect['w']:.0f}x{rect['h']:.0f}")
        return "\n".join(lines)
