"""Entrypoint: run with `python -m native.main` from the repo root.

Starts the asyncio WebSocket server on a background thread and the Tkinter
GUI on the main thread (Tkinter must own the main thread on Windows).
"""
from __future__ import annotations

import asyncio
import logging
import threading

from . import automation
from .gui import App
from .ws_server import NativeBridge

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


def _run_loop_in_thread(loop: asyncio.AbstractEventLoop, bridge: NativeBridge) -> None:
    asyncio.set_event_loop(loop)
    loop.create_task(bridge.serve_forever())
    loop.run_forever()


def main() -> None:
    automation.ensure_dpi_awareness()

    bridge = NativeBridge()
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=_run_loop_in_thread, args=(loop, bridge), daemon=True)
    thread.start()

    app = App(bridge, loop)
    try:
        app.mainloop()
    finally:
        loop.call_soon_threadsafe(loop.stop)


if __name__ == "__main__":
    main()
