"""Tkinter control panel: paste usernames + message, run Record / Replay.

Fully synchronous native automation now (no browser extension, no
WebSocket, no asyncio) - the only concurrency here is plain Python threads:
pynput's own listener thread during recording, and a background
threading.Thread for anything that blocks (screenshots, polling, sleeps)
during preview/replay, so the Tk main loop stays responsive. Every update
coming back from another thread goes through `self.after(0, ...)` to stay
on the Tk thread.
"""
from __future__ import annotations

import json
import os
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext

import pyautogui
from PIL import ImageDraw

from . import automation, config, geometry
from .recorder import Recorder
from .replayer import Replayer


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("TikTok Shop Bulk Creator Messenger")
        self.geometry("720x620")

        self.recorder = Recorder()
        self.replayer: Replayer | None = None

        self._build_widgets()
        self._refresh_recording_status()
        self._load_last_input()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---- widget layout -----------------------------------------------------

    def _build_widgets(self) -> None:
        rec_frame = tk.LabelFrame(self, text="1. Record (5 clicks, one per prompt)")
        rec_frame.pack(fill="x", padx=8, pady=4)
        self.btn_record_start = tk.Button(rec_frame, text="Start Recording", command=self._start_recording)
        self.btn_record_start.pack(side="left", padx=4, pady=4)
        self.btn_record_cancel = tk.Button(rec_frame, text="Cancel", command=self._cancel_recording, state="disabled")
        self.btn_record_cancel.pack(side="left", padx=4, pady=4)
        self.btn_preview = tk.Button(rec_frame, text="Preview Recording (screenshot)", command=self._preview_recording)
        self.btn_preview.pack(side="left", padx=4, pady=4)
        self.recording_status_label = tk.Label(rec_frame, text="", fg="blue")
        self.recording_status_label.pack(side="left", padx=8)

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
        self.btn_resume = tk.Button(btn_row, text="Resume", command=self._resume_replay, bg="#f9a825", fg="white", state="disabled")
        self.btn_resume.pack(side="left", padx=4)
        self.dry_run_var = tk.BooleanVar(value=False)
        self.chk_dry_run = tk.Checkbutton(
            btn_row,
            text="Dry run (first username only, leaves message empty, still clicks Send)",
            variable=self.dry_run_var,
        )
        self.chk_dry_run.pack(side="left", padx=12)

        log_frame = tk.LabelFrame(self, text="Log")
        log_frame.pack(fill="both", expand=True, padx=8, pady=4)
        self.log_text = scrolledtext.ScrolledText(log_frame, height=10, state="disabled")
        self.log_text.pack(fill="both", expand=True)

    # ---- helpers -------------------------------------------------------------

    def _log(self, line: str) -> None:
        self.log_text.config(state="normal")
        self.log_text.insert("end", line + "\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def _run_bg(self, target, *args) -> None:
        threading.Thread(target=target, args=args, daemon=True).start()

    def _refresh_recording_status(self) -> None:
        flow = Recorder.load()
        self.recording_status_label.config(text="Recording saved." if flow else "No recording yet.")

    def _load_last_input(self) -> None:
        if not config.LAST_INPUT_PATH.exists():
            return
        try:
            data = json.loads(config.LAST_INPUT_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        self.usernames_text.insert("1.0", data.get("usernames", ""))
        self.message_text.insert("1.0", data.get("message", ""))

    def _save_last_input(self) -> None:
        data = {
            "usernames": self.usernames_text.get("1.0", "end").rstrip("\n"),
            "message": self.message_text.get("1.0", "end").rstrip("\n"),
        }
        try:
            config.STORAGE_DIR.mkdir(parents=True, exist_ok=True)
            config.LAST_INPUT_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError:
            pass

    def _on_close(self) -> None:
        self._save_last_input()
        self.destroy()

    # ---- recording -------------------------------------------------------------

    def _start_recording(self) -> None:
        def on_progress(count, next_label):
            text = f"Captured {count}/5. Next: click the {next_label}." if next_label else "All 5 points captured - saving..."
            self.after(0, lambda: self.recording_status_label.config(text=text))
            if next_label is None:
                self._finish_recording()

        self.recorder.start(on_progress=on_progress)
        self.btn_record_start.config(state="disabled")
        self.btn_record_cancel.config(state="normal")
        self.recording_status_label.config(text="Click the New message button now.")
        self._log("Recording started - click each point as prompted (New message, username input, Chat button, message input, Send button).")

    def _cancel_recording(self) -> None:
        self.recorder.cancel()
        self.btn_record_start.config(state="normal")
        self.btn_record_cancel.config(state="disabled")
        self.recording_status_label.config(text="Recording cancelled.")
        self._log("Recording cancelled.")

    def _finish_recording(self) -> None:
        # Called from the pynput listener thread (safe: pynput supports
        # stopping a listener from within its own callback).
        flow = self.recorder.finish()
        if flow is None:
            self.after(0, lambda: self._log("Recording finished with fewer than 5 points captured - not saved."))
            return
        summary = Recorder.describe(flow)
        self.after(0, lambda: (
            self.btn_record_start.config(state="normal"),
            self.btn_record_cancel.config(state="disabled"),
            self.recording_status_label.config(text="Recording saved."),
            self._log("Recording saved:\n" + summary),
        ))

    def _preview_recording(self) -> None:
        flow = Recorder.load()
        if not flow:
            messagebox.showerror("No recording", "Record the flow first.")
            return
        self._log("Rendering preview screenshot...")
        self._run_bg(self._do_preview, flow)

    def _do_preview(self, flow) -> None:
        if not automation.activate_browser_window(flow.browser_hwnd):
            self.after(0, lambda: self._log("Preview failed: could not bring the browser window to the foreground."))
            return

        scale = geometry.measure_scale()
        screenshot = pyautogui.screenshot()
        draw = ImageDraw.Draw(screenshot)
        radius = config.TEMPLATE_PATCH_RADIUS_PX
        for i, point in enumerate(flow.points, start=1):
            sx, sy = geometry.mouse_to_shot(point.mouse_x, point.mouse_y, scale)
            draw.ellipse([sx - radius, sy - radius, sx + radius, sy + radius], outline=(255, 0, 0), width=3)
            draw.line([sx - radius, sy, sx + radius, sy], fill=(255, 0, 0), width=1)
            draw.line([sx, sy - radius, sx, sy + radius], fill=(255, 0, 0), width=1)
            draw.text((sx + radius + 4, sy - radius), f"{i}", fill=(255, 0, 0))
        screenshot.save(config.PREVIEW_IMAGE_PATH)

        try:
            os.startfile(config.PREVIEW_IMAGE_PATH)
        except Exception:
            pass
        self.after(0, lambda: self._log(f"Preview saved and opened: {config.PREVIEW_IMAGE_PATH}"))

    # ---- replay -------------------------------------------------------------

    def _start_replay(self) -> None:
        flow = Recorder.load()
        if not flow:
            messagebox.showerror("No recording", "Record the flow first.")
            return
        usernames = [u.strip() for u in self.usernames_text.get("1.0", "end").splitlines() if u.strip()]
        message = self.message_text.get("1.0", "end").rstrip("\n")
        dry_run = self.dry_run_var.get()
        if not usernames or (not message and not dry_run):
            messagebox.showerror("Missing input", "Provide at least one username and a message.")
            return

        self.replayer = Replayer(flow, dry_run=dry_run)
        self.replayer.on_pause_requested = self._on_replay_paused
        count = 1 if dry_run else len(usernames)
        self._log(f"Starting {'DRY RUN ' if dry_run else ''}replay for {count} username(s).")
        self._run_bg(self._do_replay, usernames, message)

    def _on_replay_paused(self, message: str) -> None:
        # Called from the replay background thread - marshal to the Tk
        # thread before touching any widget.
        def show():
            self.btn_resume.config(state="normal")
            self._log(f"PAUSED: {message}")
            messagebox.showwarning("Paused - needs attention", message)

        self.after(0, show)

    def _resume_replay(self) -> None:
        if self.replayer:
            self.replayer.resume()
            self.btn_resume.config(state="disabled")
            self._log("Resumed.")

    def _do_replay(self, usernames: list[str], message: str) -> None:
        def on_progress(result):
            self.after(0, lambda: self._log(f"{result.username}: {result.status} ({result.notes})"))

        results = self.replayer.run(usernames, message, on_progress=on_progress)
        ok_statuses = (config.STATUS_SENT, config.STATUS_DRY_RUN_OK)
        ok = [r for r in results if r.status in ok_statuses]
        not_found = [r for r in results if r.status == config.STATUS_SKIPPED_NOT_FOUND]
        other = [r for r in results if r not in ok and r not in not_found]
        label = "reached Send" if self.replayer.dry_run else "sent"

        def summarize():
            self._log(f"Run finished: {len(ok)}/{len(results)} {label}.")
            if not_found:
                self._log("Not found: " + ", ".join(r.username for r in not_found))
            if other:
                self._log("Errors: " + ", ".join(f"{r.username} ({r.status})" for r in other))
            self._log("See native/logs/ for the full CSV.")

        self.after(0, summarize)

    def _stop_replay(self) -> None:
        if self.replayer:
            self.replayer.request_stop()
            self._log("Stop requested - finishing current step then halting.")
