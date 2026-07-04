"""Shared dataclasses for the native automation tool."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional


@dataclass
class SelectorDescriptor:
    """Resilient element descriptor recorded from a real user action.

    `strategy` records which attribute/heuristic produced `value`, tried in this
    preference order during replay: data-testid/aria-label > stable id > text
    content scoped to an ancestor > structural/role-based fallback.
    """

    strategy: str
    value: str
    attributes: dict = field(default_factory=dict)
    text_content: Optional[str] = None
    ancestor_path: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "SelectorDescriptor":
        return SelectorDescriptor(
            strategy=d["strategy"],
            value=d["value"],
            attributes=d.get("attributes", {}),
            text_content=d.get("textContent") or d.get("text_content"),
            ancestor_path=d.get("ancestorPath") or d.get("ancestor_path"),
        )


@dataclass
class ActionStep:
    """One step of the generic, replayable action script."""

    step_id: int
    kind: str  # CLICK | HOVER_THEN_CLICK | FOCUS_AND_PASTE | PASTE_MULTILINE_THEN_ENTER
    selector: SelectorDescriptor
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "stepId": self.step_id,
            "kind": self.kind,
            "selector": self.selector.to_dict(),
            "notes": self.notes,
        }

    @staticmethod
    def from_dict(d: dict) -> "ActionStep":
        return ActionStep(
            step_id=d["stepId"],
            kind=d["kind"],
            selector=SelectorDescriptor.from_dict(d["selector"]),
            notes=d.get("notes", ""),
        )


@dataclass
class CalibrationTransform:
    scale_x: float
    scale_y: float
    offset_x: float
    offset_y: float
    calibrated_screen_x: int
    calibrated_screen_y: int

    def viewport_to_screen(self, vx: float, vy: float) -> tuple[float, float]:
        return (vx * self.scale_x + self.offset_x, vy * self.scale_y + self.offset_y)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "CalibrationTransform":
        return CalibrationTransform(**d)


@dataclass
class RunResult:
    username: str
    status: str
    matched_by_fallback: bool = False
    timestamp_start: str = ""
    timestamp_end: str = ""
    notes: str = ""

    def to_row(self) -> list:
        return [
            self.username,
            self.status,
            self.matched_by_fallback,
            self.timestamp_start,
            self.timestamp_end,
            self.notes,
        ]


CSV_HEADER = [
    "username",
    "status",
    "matchedBySelectorFallback",
    "timestamp_start",
    "timestamp_end",
    "notes",
]
