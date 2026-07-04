"""Per-username replay loop (docs/protocol.md: replay phase).

Clicks fixed viewport positions recorded during the Record step, converted
to a real screen coordinate via a screenshot-based pixel calibration (see
geometry.py) done fresh at the start of every username - no selector
matching, and no trusting of self-reported browser window geometry, which
was found to be unreliable on at least one real deployment (Windows Server
2022 / Remote Desktop). The extension is only asked to answer "does this
username exist", place/remove calibration markers, and read back text at a
point for paste/send verification - never to re-locate an element.
"""
from __future__ import annotations

import asyncio
import csv
import datetime
import logging

from . import automation, config, geometry
from .models import ActionStep, CSV_HEADER, RunResult
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
        steps: list[ActionStep],
        browser_hwnd: int | None = None,
        dry_run: bool = False,
    ) -> None:
        self.bridge = bridge
        self.steps = steps
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

    async def _screen_point(self, rect: dict) -> tuple[float, float]:
        sx, sy = self._transform.rect_to_screen(rect)
        log.info("rect=%s -> screen=(%.0f, %.0f)", rect, sx, sy)
        return sx, sy

    async def _click_rect(self, rect: dict, jitter: bool = True) -> None:
        sx, sy = await self._screen_point(rect)
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

        for step in self.steps:
            if self._abort:
                raise AbortRun()

            if step.kind in ("CLICK", "FOCUS_AND_PASTE"):
                await self._click_rect(step.rect_viewport)

            elif step.kind == "PASTE_USERNAME":
                await self._click_rect(step.rect_viewport)
                automation.paste_text(username)
                search = await self.bridge.request(
                    "check_username_exists", {"username": username},
                    timeout=config.SEARCH_RESULT_TIMEOUT_S,
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

            elif step.kind == "HOVER_THEN_CLICK":
                # The recorded rect is the Chat button's own position, which
                # lies inside the result row - moving the cursor there is
                # itself what triggers the row's hover reveal, so there's no
                # need for a separate "row" position at all.
                sx, sy = await self._screen_point(step.rect_viewport)
                automation.move_to(sx, sy)
                automation.hover_settle()
                automation.click(sx, sy, jitter=True)

            elif step.kind == "PASTE_MULTILINE_THEN_ENTER":
                if self.dry_run:
                    end = datetime.datetime.now().isoformat(timespec="seconds")
                    await self._reset_state()
                    preview = (message.strip()[:60] + "...") if message.strip() else "(no message provided)"
                    return RunResult(
                        username, config.STATUS_DRY_RUN_OK, timestamp_start=start,
                        timestamp_end=end, notes=f"reached message box, would send: {preview}",
                    )

                await self._click_rect(step.rect_viewport)
                automation.paste_text(message)
                pasted_text = await self._text_at_rect(step.rect_viewport)
                if not pasted_text.strip().startswith(message.strip()[:40]):
                    end = datetime.datetime.now().isoformat(timespec="seconds")
                    await self._reset_state()
                    return RunResult(
                        username, config.STATUS_FAILED_UI_STUCK, timestamp_start=start,
                        timestamp_end=end, notes="paste into message box did not verify",
                    )
                automation.press_enter()
                await asyncio.sleep(config.SEND_VERIFY_WAIT_S)
                remaining_text = await self._text_at_rect(step.rect_viewport)
                end = datetime.datetime.now().isoformat(timespec="seconds")
                await self._reset_state()
                if remaining_text.strip() == "":
                    return RunResult(username, config.STATUS_SENT, timestamp_start=start, timestamp_end=end)
                return RunResult(username, config.STATUS_FAILED_SEND_UNCONFIRMED, timestamp_start=start, timestamp_end=end)

        end = datetime.datetime.now().isoformat(timespec="seconds")
        return RunResult(username, config.STATUS_FAILED_UI_STUCK, timestamp_start=start, timestamp_end=end, notes="script ended without sending")

    async def run(self, usernames: list[str], message: str, on_progress=None) -> list[RunResult]:
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
