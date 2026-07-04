"""Shared dataclasses for the native automation tool."""
from __future__ import annotations

from dataclasses import dataclass

# Fixed, ordered list of the 5 points a recording captures. Guided recording
# asks for exactly these, one click each, in this order - no inference from
# a raw click/focus/paste event stream, no selector matching, nothing to
# misclassify.
STEP_LABELS = [
    "New message button",
    "Username input",
    "Chat button (hover point)",
    "Message input",
    "Send button",
]


@dataclass
class RecordedFlow:
    """The 5 fixed viewport rects captured during Record. Each is clicked
    directly during replay, converted to a real screen coordinate via
    geometry.PixelTransform - no selector, no DOM matching."""

    new_message: dict
    username_input: dict
    chat_button: dict
    message_input: dict
    send_button: dict

    def as_list(self) -> list[tuple[str, dict]]:
        return list(zip(STEP_LABELS, [
            self.new_message, self.username_input, self.chat_button,
            self.message_input, self.send_button,
        ]))

    def to_dict(self) -> dict:
        return {
            "newMessage": self.new_message,
            "usernameInput": self.username_input,
            "chatButton": self.chat_button,
            "messageInput": self.message_input,
            "sendButton": self.send_button,
        }

    @staticmethod
    def from_dict(d: dict) -> "RecordedFlow":
        return RecordedFlow(
            new_message=d["newMessage"],
            username_input=d["usernameInput"],
            chat_button=d["chatButton"],
            message_input=d["messageInput"],
            send_button=d["sendButton"],
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
