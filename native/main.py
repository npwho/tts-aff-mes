"""Entrypoint: run with `python -m native.main` from the repo root.

Fully native - no browser extension, no WebSocket server, no asyncio.
Tkinter owns the main thread; recording uses pynput's own listener thread,
and replay/preview run on plain background threads (see gui.py).
"""
from __future__ import annotations

import logging

from . import automation
from .gui import App

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


def main() -> None:
    automation.ensure_dpi_awareness()
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
