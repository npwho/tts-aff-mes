"""Local WebSocket server the extension connects to.

Owns the single connection to the extension's background service worker and
provides a small async request/response + pub/sub API on top of the raw
JSON protocol described in docs/protocol.md, so recorder.py/replayer.py
don't need to touch websockets directly.
"""
from __future__ import annotations

import asyncio
import json
import itertools
import logging

import websockets

from . import config

log = logging.getLogger("ws_server")

_corr_counter = itertools.count(1)


def next_corr_id(prefix: str = "c") -> str:
    return f"{prefix}-{next(_corr_counter)}"


class NativeBridge:
    """Async hub between the extension's WS connection and the rest of the app."""

    def __init__(self) -> None:
        self._ws = None
        self.connected = False
        self.mode = "idle"
        self._pending: dict[str, asyncio.Future] = {}
        self._listeners: dict[str, list] = {}
        self._pong_event = asyncio.Event()
        self._server = None
        self._heartbeat_task: asyncio.Task | None = None

    # ---- pub/sub for unsolicited events -----------------------------------

    def on(self, event_type: str, callback) -> None:
        self._listeners.setdefault(event_type, []).append(callback)

    def _notify(self, event_type: str, payload: dict | None = None) -> None:
        for cb in self._listeners.get(event_type, []):
            try:
                cb(payload or {})
            except Exception:
                log.exception("listener for %s raised", event_type)

    # ---- request/response ----------------------------------------------------

    async def request(self, msg_type: str, payload: dict | None = None, timeout: float = 5.0) -> dict:
        if not self.connected or self._ws is None:
            raise ConnectionError("extension not connected")
        corr_id = next_corr_id(msg_type[:4])
        msg = {"type": msg_type, "corrId": corr_id, **(payload or {})}
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[corr_id] = fut
        try:
            await self._send(msg)
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._pending.pop(corr_id, None)

    async def send_fire_and_forget(self, msg_type: str, payload: dict | None = None) -> None:
        await self._send({"type": msg_type, **(payload or {})})

    async def _send(self, msg: dict) -> None:
        if self._ws is None:
            raise ConnectionError("no active websocket")
        await self._ws.send(json.dumps(msg))

    # ---- server lifecycle -------------------------------------------------

    async def serve_forever(self) -> None:
        self._server = await websockets.serve(self._handle_connection, config.WS_HOST, config.WS_PORT)
        log.info("WS server listening on ws://%s:%s", config.WS_HOST, config.WS_PORT)
        await self._server.wait_closed()

    async def _handle_connection(self, websocket) -> None:
        # Single-client design: the newest connection wins.
        self._ws = websocket
        self.connected = True
        self._pong_event.set()
        if self._heartbeat_task is None:
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        self._notify("connected")
        try:
            async for raw in websocket:
                self._dispatch(raw)
        except websockets.ConnectionClosed:
            pass
        finally:
            if self._ws is websocket:
                self._ws = None
                self.connected = False
                self._notify("disconnected")

    def _dispatch(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return
        msg_type = msg.get("type")
        corr_id = msg.get("corrId")

        if corr_id and corr_id in self._pending:
            fut = self._pending[corr_id]
            if not fut.done():
                fut.set_result(msg)
            return

        if msg_type == "hello":
            self.mode = self.mode  # unchanged; ack current mode
            asyncio.create_task(self.send_fire_and_forget("hello_ack", {"mode": self.mode}))
            self._notify("hello", msg)
        elif msg_type == "pong":
            self._pong_event.set()
        elif msg_type in ("record_event", "captcha_detected", "error"):
            self._notify(msg_type, msg)
        else:
            log.debug("unhandled unsolicited message: %s", msg_type)

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(config.HEARTBEAT_INTERVAL_S)
            if not self.connected:
                continue
            self._pong_event.clear()
            try:
                await self.send_fire_and_forget("ping")
            except ConnectionError:
                continue
            try:
                await asyncio.wait_for(self._pong_event.wait(), timeout=config.HEARTBEAT_TIMEOUT_S)
            except asyncio.TimeoutError:
                self._notify("heartbeat_lost")
