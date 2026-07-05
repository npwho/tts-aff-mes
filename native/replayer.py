"""Replay against a recorded 5-point flow - fully native, no browser
extension, no DOM queries, no WebSocket. Each click waits for its recorded
template patch to visually reappear on screen before acting (confirming the
element actually loaded, not just clicking a stale coordinate), refining
the click point to wherever the match actually is.

"Not found" (username doesn't exist) is inferred purely from the Chat
button's template never appearing after hovering - there's no DOM-based
existence check anymore. Sending isn't verified either; Send is clicked and
the run moves on.
"""
from __future__ import annotations

import csv
import datetime
import logging
import time

from . import automation, config, geometry, template_match
from .models import CSV_HEADER, RecordedFlow, RunResult
from .template_match import load_template

log = logging.getLogger("replayer")

# Indices into RecordedFlow.points, matching models.STEP_LABELS order.
STEP_NEW_MESSAGE, STEP_USERNAME_INPUT, STEP_CHAT_BUTTON, STEP_MESSAGE_INPUT, STEP_SEND_BUTTON = range(5)


class AbortRun(Exception):
    pass


class Replayer:
    def __init__(self, flow: RecordedFlow, dry_run: bool = False) -> None:
        self.flow = flow
        self.dry_run = dry_run
        self._abort = False
        self._scale: tuple[float, float] | None = None
        self._templates = [load_template(p.template_path) for p in flow.points]

    def request_stop(self) -> None:
        self._abort = True

    def _should_abort(self) -> bool:
        return self._abort

    def _click_step(self, index: int, max_wait: float = config.STEP_MAX_WAIT_S) -> tuple[float, float] | None:
        point = self.flow.points[index]
        template = self._templates[index]
        expected_x, expected_y = geometry.mouse_to_shot(point.mouse_x, point.mouse_y, self._scale)
        automation.move_to(point.mouse_x, point.mouse_y)
        result = template_match.wait_for_match(
            template, expected_x, expected_y, max_wait_s=max_wait, should_abort=self._should_abort,
        )
        if result is None:
            return None
        mx, my = geometry.shot_to_mouse(result[0], result[1], self._scale)
        automation.click(mx, my)
        return (mx, my)

    def _click_chat_button(self) -> tuple[float, float] | None:
        """Only appears on :hover. Checks once at the recorded position
        with no wiggling - if it's already visible, click it immediately.
        Only if it's NOT visible does it wiggle the mouse away and back
        (a single teleport onto the spot doesn't reliably fire the
        mouseenter/mouseover that reveals it), up to a fixed number of
        attempts - not a time-based wait."""
        point = self.flow.points[STEP_CHAT_BUTTON]
        template = self._templates[STEP_CHAT_BUTTON]
        expected_x, expected_y = geometry.mouse_to_shot(point.mouse_x, point.mouse_y, self._scale)

        automation.move_to(point.mouse_x, point.mouse_y)
        result = template_match.find_once(template, expected_x, expected_y)

        attempts = 0
        while result is None and attempts < config.HOVER_REVEAL_REPEATS:
            if self._should_abort():
                return None
            automation.hover_reveal_once(point.mouse_x, point.mouse_y)
            result = template_match.find_once(template, expected_x, expected_y)
            attempts += 1

        if result is None:
            return None
        mx, my = geometry.shot_to_mouse(result[0], result[1], self._scale)
        automation.click(mx, my)
        return (mx, my)

    def _check_foreground(self) -> bool:
        if automation.is_browser_foreground(self.flow.browser_hwnd):
            return True
        return automation.activate_browser_window(self.flow.browser_hwnd)

    def _reset_state(self) -> None:
        # No DOM to confirm a dialog actually closed - best effort only.
        automation.press_escape()
        time.sleep(0.3)
        automation.press_escape()

    def run_one(self, username: str, message: str) -> RunResult:
        start = datetime.datetime.now().isoformat(timespec="seconds")

        if not self._check_foreground():
            return RunResult(
                username, config.STATUS_FAILED_UI_STUCK, timestamp_start=start,
                timestamp_end=start, notes="browser window not in foreground",
            )

        self._scale = geometry.measure_scale()

        def fail(notes: str) -> RunResult:
            self._reset_state()
            end = datetime.datetime.now().isoformat(timespec="seconds")
            return RunResult(username, config.STATUS_FAILED_UI_STUCK, timestamp_start=start, timestamp_end=end, notes=notes)

        if self._abort:
            raise AbortRun()

        # 1. New message
        if self._click_step(STEP_NEW_MESSAGE) is None:
            return fail("New message button not found")
        automation.inter_step_delay()

        if self._abort:
            raise AbortRun()

        # 2. Username input: click, paste, let the search API call fire
        if self._click_step(STEP_USERNAME_INPUT) is None:
            return fail("Username input not found")
        automation.paste_text(username)
        automation.api_settle_delay()

        if self._abort:
            raise AbortRun()

        # 3. Chat button: checked once at its recorded position with no
        # wiggling; if not visible, wiggle the mouse away and back up to a
        # few times (see _click_chat_button). If it still never appears,
        # the username doesn't exist (or the search never returned a
        # result) - not a stuck-UI failure.
        if self._click_chat_button() is None:
            self._reset_state()
            end = datetime.datetime.now().isoformat(timespec="seconds")
            return RunResult(username, config.STATUS_SKIPPED_NOT_FOUND, timestamp_start=start, timestamp_end=end)
        automation.api_settle_delay()

        if self._abort:
            raise AbortRun()

        # 4. Message input: wait for the thread to load, click, paste
        # (unless dry run, which leaves it empty on purpose).
        if self._click_step(STEP_MESSAGE_INPUT) is None:
            return fail("Message input never loaded")
        if not self.dry_run:
            automation.paste_text(message)
        automation.inter_step_delay()

        if self._abort:
            raise AbortRun()

        # 5. Send - clicked for real even in a dry run (message left empty,
        # so most chat UIs simply no-op). Not verified either way.
        if self._click_step(STEP_SEND_BUTTON) is None:
            return fail("Send button not found")

        end = datetime.datetime.now().isoformat(timespec="seconds")
        self._reset_state()
        status = config.STATUS_DRY_RUN_OK if self.dry_run else config.STATUS_SENT
        notes = "clicked Send with an empty message (intentional no-op)" if self.dry_run else ""
        return RunResult(username, status, timestamp_start=start, timestamp_end=end, notes=notes)

    def run(self, usernames: list[str], message: str, on_progress=None) -> list[RunResult]:
        # A dry run only ever exercises the first username - it's a
        # click-path sanity check, not a batch operation.
        if self.dry_run:
            usernames = usernames[:1]

        config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
        log_path = config.LOGS_DIR / f"run_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        results: list[RunResult] = []

        with open(log_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(CSV_HEADER)

            for i, username in enumerate(usernames):
                if self._abort:
                    results.append(RunResult(username, config.STATUS_ABORTED))
                    writer.writerow(results[-1].to_row())
                    f.flush()
                    break

                automation.maybe_human_break(i)
                try:
                    result = self.run_one(username, message)
                except AbortRun:
                    result = RunResult(username, config.STATUS_ABORTED)

                results.append(result)
                writer.writerow(result.to_row())
                f.flush()
                if on_progress:
                    on_progress(result)

                if i < len(usernames) - 1 and not self._abort:
                    automation.between_username_delay()

        return results
