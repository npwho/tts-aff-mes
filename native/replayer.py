"""Replay against a recorded 5-point flow - fully native, no browser
extension, no DOM queries, no WebSocket. Most clicks wait for their
recorded template patch to visually reappear on screen before acting
(confirming the element actually loaded, not just clicking blind), but
ALWAYS click the originally recorded coordinate - never the matched
position. A small template patch can false-positive-match unrelated
content elsewhere on the page; letting that relocate the click defeats
the entire point of recording an exact position. Template matching is
purely a presence/timing gate here, never a source of click coordinates.
The New message button and Username input are the exception: they're
assumed to always be present once the page/dialog has had a moment to
render, so those two just wait briefly and click the recorded position
directly, with no image verification at all.

"Not found" (username doesn't exist) is inferred purely from the Chat
button's template never appearing after hovering - there's no DOM-based
existence check anymore. Sending isn't verified either; Send is clicked and
the run moves on.
"""
from __future__ import annotations

import csv
import datetime
import logging
import threading
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
        self._pause_event = threading.Event()
        self._pause_event.set()  # not paused by default
        self.on_pause_requested = None  # callback(message: str), set by the GUI

    def request_stop(self) -> None:
        self._abort = True
        self._pause_event.set()  # unblock a paused wait so it can see the abort and exit

    def resume(self) -> None:
        self._pause_event.set()

    def _should_abort(self) -> bool:
        return self._abort

    def _pause_for_user(self, message: str) -> None:
        """Blocks the replay thread until the GUI calls resume() (or Stop
        is requested, which also unblocks it). Used when something can't be
        automatically verified after retrying and needs a human to look."""
        log.warning("Paused for user: %s", message)
        self._pause_event.clear()
        if self.on_pause_requested:
            self.on_pause_requested(message)
        self._pause_event.wait()

    def _click_step(self, index: int, max_wait: float = config.STEP_MAX_WAIT_S) -> bool:
        """Waits for the recorded template to appear (confirming the
        element has loaded), but ALWAYS clicks the originally recorded
        coordinate - never the matched position. A small template patch can
        false-positive-match unrelated content elsewhere on the page;
        letting that relocate the click defeats the entire point of
        recording an exact position. Template matching here is purely a
        presence/timing gate, not a source of click coordinates."""
        point = self.flow.points[index]
        template = self._templates[index]
        expected_x, expected_y = geometry.mouse_to_shot(point.mouse_x, point.mouse_y, self._scale)
        automation.move_to(point.mouse_x, point.mouse_y)
        result = template_match.wait_for_match(
            template, expected_x, expected_y, max_wait_s=max_wait, should_abort=self._should_abort,
        )
        if result is None:
            return False
        automation.click(point.mouse_x, point.mouse_y)
        return True

    def _click_fixed(self, index: int) -> None:
        """No image verification at all - used for steps assumed to always
        be present once the page/dialog has had a moment to render (New
        message button, Username input). Just waits a bit then clicks the
        originally recorded position directly."""
        point = self.flow.points[index]
        time.sleep(config.FIXED_STEP_WAIT_S)
        automation.click(point.mouse_x, point.mouse_y)

    def _click_chat_button(self) -> bool:
        """Only appears on :hover. Checks once at the recorded position
        with no wiggling - if it's already visible, click it immediately.
        Only if it's NOT visible does it wiggle the mouse away and back
        (a single teleport onto the spot doesn't reliably fire the
        mouseenter/mouseover that reveals it), up to a fixed number of
        attempts - not a time-based wait. Always clicks the originally
        recorded coordinate (see _click_step for why), never a matched
        position - the point we hover to reveal it is exactly where it
        renders, so there's nothing to relocate to anyway."""
        point = self.flow.points[STEP_CHAT_BUTTON]
        template = self._templates[STEP_CHAT_BUTTON]
        expected_x, expected_y = geometry.mouse_to_shot(point.mouse_x, point.mouse_y, self._scale)

        automation.move_to(point.mouse_x, point.mouse_y)
        time.sleep(config.HOVER_REVEAL_SETTLE_S)
        result = template_match.find_once(template, expected_x, expected_y)

        attempts = 0
        while result is None and attempts < config.HOVER_REVEAL_REPEATS:
            if self._should_abort():
                return False
            automation.hover_reveal_once(point.mouse_x, point.mouse_y)
            result = template_match.find_once(template, expected_x, expected_y)
            attempts += 1

        if result is None:
            return False
        automation.click(point.mouse_x, point.mouse_y)
        return True

    def _check_foreground(self) -> bool:
        # A single check can catch the window mid-transition (e.g. right
        # after the previous username's reset-state Escape presses, before
        # focus has fully settled back) and read as a false negative -
        # retry a couple of times with a short pause before actually
        # attempting to re-activate the window.
        for _ in range(3):
            if automation.is_browser_foreground(self.flow.browser_hwnd):
                return True
            time.sleep(0.3)
        return automation.activate_browser_window(self.flow.browser_hwnd)

    def _reset_state(self) -> None:
        # No DOM to confirm a dialog/chat panel actually closed - best
        # effort only. Several Escape presses with pauses between, since a
        # chat thread panel may take more than one to fully dismiss, plus a
        # settle wait so the page has time to animate closed before the
        # next username's flow starts clicking again.
        for _ in range(3):
            automation.press_escape()
            time.sleep(0.3)

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

        # 1. New message - no image verification, just wait a moment for
        # the page to be ready and click the recorded position directly.
        self._click_fixed(STEP_NEW_MESSAGE)
        automation.inter_step_delay()

        if self._abort:
            raise AbortRun()

        # 2. Username input - same: wait for the dialog to render, click
        # directly, paste, let the search API call fire.
        self._click_fixed(STEP_USERNAME_INPUT)
        automation.paste_text(username)
        automation.api_settle_delay()

        if self._abort:
            raise AbortRun()

        # 3. Chat button: checked once at its recorded position with no
        # wiggling; if not visible, wiggle the mouse away and back up to a
        # few times (see _click_chat_button). If it still never appears,
        # the username doesn't exist (or the search never returned a
        # result) - not a stuck-UI failure.
        if not self._click_chat_button():
            self._reset_state()
            end = datetime.datetime.now().isoformat(timespec="seconds")
            return RunResult(username, config.STATUS_SKIPPED_NOT_FOUND, timestamp_start=start, timestamp_end=end)
        automation.api_settle_delay()

        if self._abort:
            raise AbortRun()

        # 4. Message input: wait for the thread to load, click, paste
        # (unless dry run, which leaves it empty on purpose). The paste is
        # verified by reading the field back (Ctrl+A/Ctrl+C, no DOM access
        # available) and retried on mismatch - each retry re-clicks the
        # input first rather than assuming focus was retained, since losing
        # focus is a likely reason the paste didn't land in the first
        # place. If it still doesn't match after a few attempts, pause and
        # wait for a human rather than risk sending the wrong content (this
        # is how a stale clipboard paste bug was caught previously).
        if not self._click_step(STEP_MESSAGE_INPUT):
            return fail("Message input never loaded")
        if not self.dry_run:
            message_point = self.flow.points[STEP_MESSAGE_INPUT]
            if not automation.paste_text_and_verify(
                message, lambda: automation.click(message_point.mouse_x, message_point.mouse_y)
            ):
                self._pause_for_user(
                    f"Could not verify the message pasted correctly for '{username}' after 3 attempts. "
                    "Check the message box, fix it manually if needed, then click Resume (or Stop to abort)."
                )
                if self._abort:
                    raise AbortRun()
        automation.inter_step_delay()

        if self._abort:
            raise AbortRun()

        # 5. Send - clicked for real even in a dry run (message left empty,
        # so most chat UIs simply no-op). Not verified either way.
        if not self._click_step(STEP_SEND_BUTTON):
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
                    if result.status == config.STATUS_SENT:
                        # Already confirmed sent - no need for the long
                        # randomized inter-user pacing, just a brief beat
                        # before starting the next one.
                        time.sleep(config.SUCCESS_NEXT_USER_DELAY_S)
                    else:
                        automation.between_username_delay()

        return results
