"""Shared dataclasses for the native automation tool."""
from __future__ import annotations

from dataclasses import dataclass

# Fixed, ordered list of the 5 points a recording captures. Guided recording
# asks for exactly these, one click each, in this order - no inference from
# a raw event stream, no selector, no browser DOM involvement at all.
STEP_LABELS = [
    "New message button",
    "Username input",
    "Chat button (hover point)",
    "Message input",
    "Send button",
]


@dataclass
class RecordedPoint:
    """A single recorded click: the real mouse-space coordinate it landed
    at, and the path to a small screenshot patch captured around it at
    record time. Replay waits for that same patch to visually reappear
    before clicking, confirming the element actually rendered there rather
    than blindly trusting a stale coordinate."""

    mouse_x: float
    mouse_y: float
    template_path: str

    def to_dict(self) -> dict:
        return {"mouseX": self.mouse_x, "mouseY": self.mouse_y, "templatePath": self.template_path}

    @staticmethod
    def from_dict(d: dict) -> "RecordedPoint":
        return RecordedPoint(mouse_x=d["mouseX"], mouse_y=d["mouseY"], template_path=d["templatePath"])


@dataclass
class RecordedFlow:
    """The 5 recorded points, in STEP_LABELS order, plus the browser window
    handle captured at record time (for foreground checks/activation)."""

    points: list[RecordedPoint]
    browser_hwnd: int | None = None

    def to_dict(self) -> dict:
        return {
            "points": [p.to_dict() for p in self.points],
            "browserHwnd": self.browser_hwnd,
        }

    @staticmethod
    def from_dict(d: dict) -> "RecordedFlow":
        return RecordedFlow(
            points=[RecordedPoint.from_dict(p) for p in d["points"]],
            browser_hwnd=d.get("browserHwnd"),
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
