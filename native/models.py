"""Shared dataclasses for the native automation tool."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ActionStep:
    """One step of the replayable action script.

    Stores the recorded element's viewport-relative rect directly - no
    selector, no DOM matching. Replay converts this rect to a real screen
    coordinate using the browser window's *current* geometry (see
    geometry.py) and clicks there. Positions inside TikTok's messaging
    dialog are stable once it's open, so this is far more reliable than
    re-locating elements by CSS selector, which broke repeatedly on
    TikTok's generated/state-dependent class names.
    """

    step_id: int
    kind: str  # CLICK | HOVER_THEN_CLICK | FOCUS_AND_PASTE | PASTE_USERNAME | PASTE_MULTILINE_THEN_ENTER
    rect_viewport: dict  # {x, y, w, h} in CSS px, as recorded
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "stepId": self.step_id,
            "kind": self.kind,
            "rectViewport": self.rect_viewport,
            "notes": self.notes,
        }

    @staticmethod
    def from_dict(d: dict) -> "ActionStep":
        return ActionStep(
            step_id=d["stepId"],
            kind=d["kind"],
            rect_viewport=d.get("rectViewport") or d.get("rect_viewport"),
            notes=d.get("notes", ""),
        )


@dataclass
class RunResult:
    username: str
    status: str
    timestamp_start: str = ""
    timestamp_end: str = ""
    notes: str = ""

    def to_row(self) -> list:
        return [
            self.username,
            self.status,
            self.timestamp_start,
            self.timestamp_end,
            self.notes,
        ]


CSV_HEADER = [
    "username",
    "status",
    "timestamp_start",
    "timestamp_end",
    "notes",
]
