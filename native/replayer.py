"""Per-username locate-then-act replay loop (docs/protocol.md: replay phase).

Never caches a DOM reference across steps - every step re-queries the live
page right before acting, which is what makes this robust to TikTok's SPA
re-rendering between usernames.
"""
from __future__ import annotations

import csv
import datetime
import logging

from . import automation, config
from .models import ActionStep, CalibrationTransform, CSV_HEADER, RunResult
from .ws_server import NativeBridge

log = logging.getLogger("replayer")


class AbortRun(Exception):
    pass


class Replayer:
    def __init__(
        self,
        bridge: NativeBridge,
        transform: CalibrationTransform,
        steps: list[ActionStep],
        browser_hwnd: int | None = None,
        dry_run: bool = False,
    ) -> None:
        self.bridge = bridge
        self.transform = transform
        self.steps = steps
        self.browser_hwnd = browser_hwnd
        self.dry_run = dry_run
        self._abort = False
        self._last_row_rect_screen: tuple[float, float] | None = None

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

    def _to_screen(self, rect: dict) -> tuple[float, float]:
        cx = rect["x"] + rect["w"] / 2
        cy = rect["y"] + rect["h"] / 2
        return self.transform.viewport_to_screen(cx, cy)

    async def _locate(self, step: ActionStep) -> dict:
        return await self.bridge.request(
            "replay_locate",
            {"stepId": step.step_id, "selectorDescriptor": step.selector.to_dict()},
            timeout=config.LOCATE_TIMEOUT_S,
        )

    async def _click_step(self, step: ActionStep) -> bool:
        result = await self._locate(step)
        if not result.get("found"):
            return False
        sx, sy = self._to_screen(result["rectViewport"])
        automation.click(sx, sy)
        return True

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
            resp = await self.bridge.request("reset_state", {}, timeout=config.LOCATE_TIMEOUT_S)
            if not resp.get("dialogOpen"):
                return True
            automation.press_escape()
        return False

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
                ok = await self._click_step(step)
                if not ok:
                    end = datetime.datetime.now().isoformat(timespec="seconds")
                    await self._reset_state()
                    return RunResult(
                        username, config.STATUS_FAILED_UI_STUCK, timestamp_start=start,
                        timestamp_end=end, notes=f"step {step.step_id} ({step.kind}) not found",
                    )

            elif step.kind == "PASTE_USERNAME":
                automation.paste_text(username)
                search = await self.bridge.request(
                    "replay_locate_search_result", {"username": username},
                    timeout=config.SEARCH_RESULT_TIMEOUT_S,
                )
                status = search.get("status")
                end = datetime.datetime.now().isoformat(timespec="seconds")
                if status == "not_found":
                    await self._reset_state()
                    return RunResult(username, config.STATUS_SKIPPED_NOT_FOUND, timestamp_start=start, timestamp_end=end)
                if status == "ambiguous" and not search.get("exactMatchRect"):
                    await self._reset_state()
                    return RunResult(
                        username, config.STATUS_SKIPPED_NOT_FOUND, timestamp_start=start,
                        timestamp_end=end, notes=f"ambiguous match, count={search.get('count')}",
                    )
                rect = search.get("exactMatchRect") or search.get("rectViewport")
                self._last_row_rect_screen = self._to_screen(rect)

            elif step.kind == "HOVER_THEN_CLICK":
                if self._last_row_rect_screen is None:
                    end = datetime.datetime.now().isoformat(timespec="seconds")
                    await self._reset_state()
                    return RunResult(
                        username, config.STATUS_FAILED_UI_STUCK, timestamp_start=start,
                        timestamp_end=end, notes="no search result row to hover",
                    )
                automation.move_to(*self._last_row_rect_screen)
                automation.hover_settle()
                result = await self._locate(step)
                if not result.get("found"):
                    end = datetime.datetime.now().isoformat(timespec="seconds")
                    await self._reset_state()
                    return RunResult(
                        username, config.STATUS_FAILED_UI_STUCK, timestamp_start=start,
                        timestamp_end=end, notes="Chat button not revealed after hover",
                    )
                sx, sy = self._to_screen(result["rectViewport"])
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

                automation.paste_text(message)
                readback = await self.bridge.request(
                    "get_text_content", {"selectorDescriptor": step.selector.to_dict()},
                    timeout=config.LOCATE_TIMEOUT_S,
                )
                pasted_ok = readback.get("text", "").strip().startswith(message.strip()[:40])
                if not pasted_ok:
                    end = datetime.datetime.now().isoformat(timespec="seconds")
                    await self._reset_state()
                    return RunResult(
                        username, config.STATUS_FAILED_UI_STUCK, timestamp_start=start,
                        timestamp_end=end, notes="paste into message box did not verify",
                    )
                automation.press_enter()
                verify = await self.bridge.request(
                    "send_verify", {"expectedTextPrefix": message.strip()[:40]},
                    timeout=config.SEND_VERIFY_TIMEOUT_S,
                )
                end = datetime.datetime.now().isoformat(timespec="seconds")
                await self._reset_state()
                if verify.get("sent"):
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
