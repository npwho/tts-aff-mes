"""Per-username replay loop (docs/protocol.md: replay phase).

Clicks fixed viewport positions recorded during the Record step, converted
to a real screen coordinate against the browser window's *current*
geometry (see geometry.py) on every single click - no selector matching at
all. The extension is only asked to answer "does this username exist" and
to read back text at a point for paste/send verification, never to
re-locate an element.
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

    async def _screen_point(self, rect: dict) -> tuple[float, float]:
        resp = await self.bridge.request("get_window_geometry", {}, timeout=config.REQUEST_TIMEOUT_S)
        sx, sy = geometry.viewport_rect_to_screen(rect, resp["geometry"])
        log.info("rect=%s geometry=%s -> screen=(%.0f, %.0f)", rect, resp["geometry"], sx, sy)
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
        # rather than failing immediately.
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
