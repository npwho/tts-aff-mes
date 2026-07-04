"""Turns the observed event stream from the user's first, manual creator run
into a generic, ordered ActionStep script (docs/protocol.md: recording phase).
"""
from __future__ import annotations

import json
import logging

from .config import ACTION_SCRIPT_PATH
from .models import ActionStep, SelectorDescriptor
from .ws_server import NativeBridge

log = logging.getLogger("recorder")

# Button text that only appears once the user has genuinely hovered a search
# result row - this is the "Chat" button case called out by the user.
HOVER_REVEAL_KEYWORDS = {"chat", "message"}


def _same_selector(a: dict, b: dict) -> bool:
    return a.get("strategy") == b.get("strategy") and a.get("value") == b.get("value")


class Recorder:
    def __init__(self, bridge: NativeBridge) -> None:
        self.bridge = bridge
        self._raw_events: list[dict] = []
        self._recording = False
        bridge.on("record_event", self._on_record_event)

    def _on_record_event(self, msg: dict) -> None:
        if not self._recording:
            return
        self._raw_events.append(msg)

    async def start(self) -> None:
        self._raw_events = []
        self._recording = True
        await self.bridge.send_fire_and_forget("start_recording")

    async def stop_and_save(self) -> list[ActionStep]:
        self._recording = False
        await self.bridge.send_fire_and_forget("stop_recording")
        steps = self._process(self._raw_events)
        self._save(steps)
        return steps

    def _process(self, raw_events: list[dict]) -> list[ActionStep]:
        steps: list[ActionStep] = []
        n = len(raw_events)
        paste_count = 0
        # Exactly two pastes are expected in the recorded flow: the username
        # into the search box (first), then the message into the chat box
        # (last, followed by the user pressing Enter to send). Order is the
        # only reliable signal available, since the content script never
        # reports the actual pasted text back (kept intentionally read-only
        # and privacy-light).
        for i, ev in enumerate(raw_events):
            event_type = ev.get("eventType")
            selector = ev.get("selector", {})

            if event_type == "focus":
                nxt = raw_events[i + 1] if i + 1 < n else None
                # Dedupe a focus immediately followed by a click/paste on the
                # same element - keep only the more informative later event.
                if nxt and nxt.get("eventType") in ("click", "paste") and _same_selector(
                    nxt.get("selector", {}), selector
                ):
                    continue
                kind = "FOCUS_AND_PASTE"
            elif event_type == "paste":
                paste_count += 1
                kind = "PASTE_USERNAME" if paste_count == 1 else "PASTE_MULTILINE_THEN_ENTER"
            elif event_type == "click":
                text = (selector.get("textContent") or "").strip().lower()
                kind = "HOVER_THEN_CLICK" if text in HOVER_REVEAL_KEYWORDS else "CLICK"
            else:
                continue

            steps.append(
                ActionStep(
                    step_id=len(steps) + 1,
                    kind=kind,
                    selector=SelectorDescriptor.from_dict(selector),
                    notes=f"recorded from {event_type}",
                )
            )
        return steps

    def _save(self, steps: list[ActionStep]) -> None:
        ACTION_SCRIPT_PATH.parent.mkdir(parents=True, exist_ok=True)
        ACTION_SCRIPT_PATH.write_text(json.dumps([s.to_dict() for s in steps], indent=2))
        log.info("Saved %d action steps to %s", len(steps), ACTION_SCRIPT_PATH)

    @staticmethod
    def load() -> list[ActionStep]:
        if not ACTION_SCRIPT_PATH.exists():
            return []
        raw = json.loads(ACTION_SCRIPT_PATH.read_text())
        return [ActionStep.from_dict(d) for d in raw]

    @staticmethod
    def describe(steps: list[ActionStep]) -> str:
        lines = []
        for s in steps:
            label = s.selector.text_content or s.selector.value
            lines.append(f"{s.step_id}. [{s.kind}] {label}")
        return "\n".join(lines)
