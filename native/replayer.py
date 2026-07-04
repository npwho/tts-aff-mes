"""Replay against a fixed 5-point RecordedFlow (docs/protocol.md: replay
phase). No selector matching, no per-step kind inference - just: click New
message, click username input + paste + check existence, hover+click Chat,
click message input (+ paste unless dry run), click Send.

Coordinates come from a screenshot-based pixel calibration (geometry.py)
done fresh before every username - no trusting of self-reported browser
window geometry, which was found to be unreliable on at least one real
deployment (Windows Server 2022 / Remote Desktop).
"""
from __future__ import annotations

import asyncio
import csv
import datetime
import logging

from . import automation, config, geometry
from .models import CSV_HEADER, RecordedFlow, RunResult
from .ws_server import NativeBridge

log = logging.getLogger("replayer")

CALIBRATION_MARKERS = [
    {"x": geometry.MARKER_VIEWPORT_ORIGIN[0], "y": geometry.MARKER_VIEWPORT_ORIGIN[1],
     "size": geometry.MARKER_SIZE_PX, "rgb": list(geometry.MARKER_COLOR_ORIGIN)},
    {"x": geometry.MARKER_VIEWPORT_FAR[0], "y": geometry.MARKER_VIEWPORT_FAR[1],
     "size": geometry.MARKER_SIZE_PX, "rgb": list(geometry.MARKER_COLOR_FAR)},
]


class AbortRun(Exception):
    pass


class Replayer:
    def __init__(
        self,
        bridge: NativeBridge,
        flow: RecordedFlow,
        browser_hwnd: int | None = None,
        dry_run: bool = False,
    ) -> None:
        self.bridge = bridge
        self.flow = flow
        self.browser_hwnd = browser_hwnd
        self.dry_run = dry_run
        self._abort = False
        self._transform: geometry.PixelTransform | None = None

        bridge.on("captcha_detected", self._on_captcha)
        bridge.on("heartbeat_lost", self._on_heartbeat_lost)
        bridge.on("disconnected", self._on_heartbeat_lost)

    def request_stop(self) -> None:
        self._abort = True

    def _on_captcha(self, _msg: dict) -> None:
        log.warning("Captcha detected on page - pausing run")
        self._abort = True

    def _on_heartbeat_lost(self, _msg: dict) -> None:
        log.warning("Extension connection lost - pausing run")
        self._abort = True

    async def _calibrate(self) -> bool:
        """Places marker elements on the page and finds them via a real
        screenshot - pure pixel ground truth, done fresh before every
        username so a moved window can never poison a click."""
        await self.bridge.request(
            "place_calibration_markers", {"markers": CALIBRATION_MARKERS}, timeout=config.REQUEST_TIMEOUT_S
        )
        try:
            self._transform = await asyncio.to_thread(geometry.calibrate_via_screenshot)
        finally:
            await self.bridge.send_fire_and_forget("remove_calibration_markers")
        return self._transform is not None

    def _mouse_point(self, rect: dict) -> tuple[float, float]:
        sx, sy = self._transform.rect_to_mouse(rect)
        log.info("rect=%s -> mouse=(%.0f, %.0f)", rect, sx, sy)
        return sx, sy

    def _click_rect(self, rect: dict, jitter: bool = True) -> None:
        sx, sy = self._mouse_point(rect)
        automation.click(sx, sy, jitter=jitter)

    async def _check_foreground(self) -> bool:
        if automation.is_browser_foreground(self.browser_hwnd):
            return True
        # Not foreground yet - most commonly because the user just clicked
        # Start in this tool's own window. Bring the browser forward with a
        # real click on its title bar (same as a human alt-tabbing back)
        # rather than failing immediately. Also required before calibrating,
        # since the screenshot needs the browser actually visible on screen.
        return automation.activate_browser_window(self.browser_hwnd)

    async def _reset_state(self) -> bool:
        automation.press_escape()
        for _ in range(config.RESET_VERIFY_RETRIES):
            resp = await self.bridge.request("reset_state", {}, timeout=config.REQUEST_TIMEOUT_S)
            if not resp.get("dialogOpen"):
                return True
            automation.press_escape()
        return False

    async def _text_at_rect(self, rect: dict) -> str:
        cx = rect["x"] + rect["w"] / 2
        cy = rect["y"] + rect["h"] / 2
        resp = await self.bridge.request(
            "get_text_at_point", {"viewportX": cx, "viewportY": cy}, timeout=config.REQUEST_TIMEOUT_S
        )
        return resp.get("text", "")

    async def run_one(self, username: str, message: str) -> RunResult:
        start = datetime.datetime.now().isoformat(timespec="seconds")
        flow = self.flow

        if not await self._check_foreground():
            return RunResult(
                username, config.STATUS_FAILED_UI_STUCK, timestamp_start=start,
                timestamp_end=start, notes="browser window not in foreground",
            )

        if not await self._calibrate():
            end = datetime.datetime.now().isoformat(timespec="seconds")
            return RunResult(
                username, config.STATUS_FAILED_UI_STUCK, timestamp_start=start, timestamp_end=end,
                notes="calibration markers not found in screenshot - is the browser window fully visible?",
            )

        if self._abort:
            raise AbortRun()

        # 1. New message
        self._click_rect(flow.new_message)

        # 2. Username input: click, paste, check existence
        self._click_rect(flow.username_input)
        automation.paste_text(username)
        search = await self.bridge.request(
            "check_username_exists", {"username": username}, timeout=config.SEARCH_RESULT_TIMEOUT_S,
        )
        status = search.get("status")
        end = datetime.datetime.now().isoformat(timespec="seconds")
        if status == "not_found":
            await self._reset_state()
            return RunResult(username, config.STATUS_SKIPPED_NOT_FOUND, timestamp_start=start, timestamp_end=end)
        if status == "ambiguous":
            await self._reset_state()
            return RunResult(
                username, config.STATUS_SKIPPED_NOT_FOUND, timestamp_start=start,
                timestamp_end=end, notes=f"ambiguous match, count={search.get('count')}",
            )

        if self._abort:
            raise AbortRun()

        # 3. Chat button: moving the cursor to its own recorded point is
        # itself what triggers the containing row's hover reveal.
        sx, sy = self._mouse_point(flow.chat_button)
        automation.move_to(sx, sy)
        automation.hover_settle()
        automation.click(sx, sy, jitter=True)

        # 4. Message input: click, and paste the real message unless this is
        # a dry run (left empty on purpose so Send can be safely clicked for
        # real without actually sending anything - most chat UIs no-op on
        # an empty send).
        self._click_rect(flow.message_input)
        if not self.dry_run:
            automation.paste_text(message)
            pasted_text = await self._text_at_rect(flow.message_input)
            if not pasted_text.strip().startswith(message.strip()[:40]):
                end = datetime.datetime.now().isoformat(timespec="seconds")
                await self._reset_state()
                return RunResult(
                    username, config.STATUS_FAILED_UI_STUCK, timestamp_start=start,
                    timestamp_end=end, notes="paste into message box did not verify",
                )

        # 5. Send button - clicked for real even in a dry run, per the
        # empty-input safety net above.
        self._click_rect(flow.send_button)
        await asyncio.sleep(config.SEND_VERIFY_WAIT_S)
        end = datetime.datetime.now().isoformat(timespec="seconds")
        await self._reset_state()

        if self.dry_run:
            return RunResult(
                username, config.STATUS_DRY_RUN_OK, timestamp_start=start, timestamp_end=end,
                notes="clicked Send with an empty message (intentional no-op)",
            )

        remaining_text = await self._text_at_rect(flow.message_input)
        if remaining_text.strip() == "":
            return RunResult(username, config.STATUS_SENT, timestamp_start=start, timestamp_end=end)
        return RunResult(username, config.STATUS_FAILED_SEND_UNCONFIRMED, timestamp_start=start, timestamp_end=end)

    async def run(self, usernames: list[str], message: str, on_progress=None) -> list[RunResult]:
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
                    result = await self.run_one(username, message)
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
