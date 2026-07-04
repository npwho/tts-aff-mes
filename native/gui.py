"""Tkinter control panel: paste usernames + message, run Record / Replay.
Tkinter runs on the main thread; the WebSocket server and all async
protocol work run on a background thread's asyncio event loop. Every call
from a button handler into async code goes through
`asyncio.run_coroutine_threadsafe`; every update coming back into the GUI
from that thread goes through `self.after(0, ...)` to stay on the Tk thread.
"""
from __future__ import annotations

import asyncio
import tkinter as tk
from tkinter import messagebox, scrolledtext

from . import automation, config
from .recorder import Recorder
from .replayer import Replayer
from .ws_server import NativeBridge


class App(tk.Tk):
    def __init__(self, bridge: NativeBridge, loop: asyncio.AbstractEventLoop) -> None:
        super().__init__()
        self.title("TikTok Shop Bulk Creator Messenger")
        self.geometry("720x600")

        self.bridge = bridge
        self.loop = loop
        self.recorder = Recorder(bridge)
        self.replayer: Replayer | None = None
        self.browser_hwnd = automation.find_browser_hwnd("Chrome")

        bridge.on("connected", lambda _m: self._set_status("connected"))
        bridge.on("disconnected", lambda _m: self._set_status("disconnected"))
        bridge.on("captcha_detected", lambda _m: self._on_captcha())

        self._build_widgets()
        self._set_status("waiting for extension...")

    # ---- widget layout -----------------------------------------------------

    def _build_widgets(self) -> None:
        top = tk.Frame(self)
        top.pack(fill="x", padx=8, pady=4)
        self.status_label = tk.Label(top, text="", fg="gray")
        self.status_label.pack(side="left")

        rec_frame = tk.LabelFrame(self, text="1. Record (perform the flow yourself on the first creator)")
        rec_frame.pack(fill="x", padx=8, pady=4)
        self.btn_record_start = tk.Button(rec_frame, text="Start Recording", command=self._start_recording)
        self.btn_record_start.pack(side="left", padx=4, pady=4)
        self.btn_record_stop = tk.Button(rec_frame, text="Stop Recording && Save", command=self._stop_recording, state="disabled")
        self.btn_record_stop.pack(side="left", padx=4, pady=4)

        input_frame = tk.LabelFrame(self, text="2. Replay (remaining usernames)")
        input_frame.pack(fill="both", expand=True, padx=8, pady=4)

        tk.Label(input_frame, text="Usernames (one per line):").pack(anchor="w")
        self.usernames_text = scrolledtext.ScrolledText(input_frame, height=8)
        self.usernames_text.pack(fill="both", expand=True, padx=4)

        tk.Label(input_frame, text="Message (multi-line):").pack(anchor="w")
        self.message_text = scrolledtext.ScrolledText(input_frame, height=6)
        self.message_text.pack(fill="both", expand=True, padx=4)

        btn_row = tk.Frame(input_frame)
        btn_row.pack(fill="x", pady=4)
        self.btn_start = tk.Button(btn_row, text="Start", command=self._start_replay, bg="#2e7d32", fg="white")
        self.btn_start.pack(side="left", padx=4)
        self.btn_stop = tk.Button(btn_row, text="Stop", command=self._stop_replay, bg="#b71c1c", fg="white")
        self.btn_stop.pack(side="left", padx=4)
        self.dry_run_var = tk.BooleanVar(value=False)
        self.chk_dry_run = tk.Checkbutton(
            btn_row, text="Dry run (click through, don't paste/send message)", variable=self.dry_run_var
        )
        self.chk_dry_run.pack(side="left", padx=12)

        log_frame = tk.LabelFrame(self, text="Log")
        log_frame.pack(fill="both", expand=True, padx=8, pady=4)
        self.log_text = scrolledtext.ScrolledText(log_frame, height=10, state="disabled")
        self.log_text.pack(fill="both", expand=True)

    # ---- helpers -------------------------------------------------------------

    def _set_status(self, text: str) -> None:
        self.status_label.config(text=f"Extension: {text}")

    def _log(self, line: str) -> None:
        self.log_text.config(state="normal")
        self.log_text.insert("end", line + "\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def _run_async(self, coro) -> None:
        asyncio.run_coroutine_threadsafe(coro, self.loop)

    def _on_captcha(self) -> None:
        self.after(0, lambda: messagebox.showwarning(
            "Captcha detected", "TikTok showed a captcha. The run has been paused - solve it manually, then restart."
        ))

    # ---- recording -------------------------------------------------------------

    def _start_recording(self) -> None:
        self._run_async(self.recorder.start())
        self.btn_record_start.config(state="disabled")
        self.btn_record_stop.config(state="normal")
        self._log("Recording started - perform the full flow yourself on the first creator now.")

    def _stop_recording(self) -> None:
        self._run_async(self._do_stop_recording())

    async def _do_stop_recording(self) -> None:
        steps = await self.recorder.stop_and_save()
        summary = Recorder.describe(steps)
        self.after(0, lambda: (
            self.btn_record_start.config(state="normal"),
            self.btn_record_stop.config(state="disabled"),
            self._log("Recording saved:\n" + summary),
        ))

    # ---- replay -------------------------------------------------------------

    def _start_replay(self) -> None:
        steps = Recorder.load()
        if not steps:
            messagebox.showerror("No recording", "Record the first creator's flow first.")
            return
        usernames = [u.strip() for u in self.usernames_text.get("1.0", "end").splitlines() if u.strip()]
        message = self.message_text.get("1.0", "end").rstrip("\n")
        dry_run = self.dry_run_var.get()
        if not usernames or (not message and not dry_run):
            messagebox.showerror("Missing input", "Provide at least one username and a message.")
            return

        self.replayer = Replayer(self.bridge, steps, browser_hwnd=self.browser_hwnd, dry_run=dry_run)
        self._log(f"Starting {'DRY RUN ' if dry_run else ''}replay for {len(usernames)} usernames.")
        self._run_async(self._do_replay(usernames, message))

    async def _do_replay(self, usernames: list[str], message: str) -> None:
        def on_progress(result):
            self.after(0, lambda: self._log(f"{result.username}: {result.status} ({result.notes})"))

        results = await self.replayer.run(usernames, message, on_progress=on_progress)
        ok_statuses = (config.STATUS_SENT, config.STATUS_DRY_RUN_OK)
        ok = sum(1 for r in results if r.status in ok_statuses)
        label = "reached message step" if self.replayer.dry_run else "sent"
        self.after(0, lambda: self._log(f"Run finished: {ok}/{len(results)} {label}. See native/logs/ for the full CSV."))

    def _stop_replay(self) -> None:
        if self.replayer:
            self.replayer.request_stop()
            self._log("Stop requested - finishing current step then halting.")
