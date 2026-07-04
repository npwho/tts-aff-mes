"""Tkinter control panel: paste usernames + message, run Calibrate / Record /
Replay. Tkinter runs on the main thread; the WebSocket server and all async
protocol work run on a background thread's asyncio event loop. Every call
from a button handler into async code goes through
`asyncio.run_coroutine_threadsafe`; every update coming back into the GUI
from that thread goes through `self.after(0, ...)` to stay on the Tk thread.
"""
from __future__ import annotations

import asyncio
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext

from pynput import keyboard as pkeyboard

from . import automation, calibration, config
from .models import CalibrationTransform
from .recorder import Recorder
from .replayer import Replayer
from .ws_server import NativeBridge


class App(tk.Tk):
    def __init__(self, bridge: NativeBridge, loop: asyncio.AbstractEventLoop) -> None:
        super().__init__()
        self.title("TikTok Shop Bulk Creator Messenger")
        self.geometry("720x640")

        self.bridge = bridge
        self.loop = loop
        self.recorder = Recorder(bridge)
        self.replayer: Replayer | None = None
        self.transform: CalibrationTransform | None = calibration.load_calibration()
        self._cal_point1 = None
        self._cal_point2_viewport = None
        self._capture_armed: int | None = None
        self.browser_hwnd = automation.find_browser_hwnd("Chrome")

        self._kb_listener = pkeyboard.Listener(on_press=self._on_key_press)
        self._kb_listener.daemon = True
        self._kb_listener.start()

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

        cal_frame = tk.LabelFrame(self, text="1. Calibrate (once per session)")
        cal_frame.pack(fill="x", padx=8, pady=4)
        self.btn_calibrate = tk.Button(cal_frame, text="Start Calibration", command=self._start_calibration)
        self.btn_calibrate.pack(side="left", padx=4, pady=4)
        self.cal_hint_label = tk.Label(cal_frame, text="", fg="blue")
        self.cal_hint_label.pack(side="left", padx=8)

        rec_frame = tk.LabelFrame(self, text="2. Record (perform the flow yourself on the first creator)")
        rec_frame.pack(fill="x", padx=8, pady=4)
        self.btn_record_start = tk.Button(rec_frame, text="Start Recording", command=self._start_recording)
        self.btn_record_start.pack(side="left", padx=4, pady=4)
        self.btn_record_stop = tk.Button(rec_frame, text="Stop Recording && Save", command=self._stop_recording, state="disabled")
        self.btn_record_stop.pack(side="left", padx=4, pady=4)

        input_frame = tk.LabelFrame(self, text="3. Replay (remaining usernames)")
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

    # ---- calibration ---------------------------------------------------------
    #
    # Capture is done via a global F8 hotkey, not a GUI button: clicking a
    # button would require moving the real mouse cursor off the on-page
    # marker and onto the button first, which would capture the wrong
    # position. F8 lets you capture while the cursor is still on the marker.

    def _start_calibration(self) -> None:
        self._run_async(self._calibration_point(1, 50, 50))

    async def _calibration_point(self, point_id: int, vx: int, vy: int) -> None:
        try:
            resp = await self.bridge.request("calibration_request_point", {"pointId": point_id, "viewportX": vx, "viewportY": vy})
        except ConnectionError as e:
            error_text = str(e) or "extension not connected"
            self.after(0, lambda: self._log(f"Calibration failed: {error_text}"))
            return
        except asyncio.TimeoutError:
            self.after(0, lambda: self._log(
                "Calibration failed: no response from the extension within 5s. "
                "The TikTok Shop tab's content script isn't answering - make sure that tab "
                "is open and was refreshed since the extension was last (re)loaded, and that "
                "it was the active/focused tab when you clicked Start Calibration."
            ))
            return
        if point_id == 1:
            self._cal_marker1 = resp
            hint = "Point 1 marker placed (top-left). Hover your real mouse over it, then press F8."
        else:
            self._cal_marker2 = resp
            hint = "Point 2 marker placed (bottom-right). Hover your real mouse over it, then press F8."

        def _arm():
            self._log(hint)
            self.cal_hint_label.config(text=hint)
            self._capture_armed = point_id

        self.after(0, _arm)

    def _on_key_press(self, key) -> None:
        # Runs on pynput's own listener thread - only read a thread-safe
        # value (mouse position) here, then hand off to the Tk thread.
        if key != pkeyboard.Key.f8 or self._capture_armed is None:
            return
        point_id = self._capture_armed
        self._capture_armed = None
        sx, sy = automation.current_screen_position()
        self.after(0, lambda: self._handle_capture(point_id, sx, sy))

    def _handle_capture(self, point_id: int, sx: int, sy: int) -> None:
        self.cal_hint_label.config(text="")
        if point_id == 1:
            self._cal_point1 = calibration.CalibrationPoint(50, 50, sx, sy)
            vx = self._cal_marker1["windowInnerWidth"] - 50
            vy = self._cal_marker1["windowInnerHeight"] - 50
            self._cal_point2_viewport = (vx, vy)
            self._log(f"Point 1 captured at screen ({sx}, {sy}).")
            self._run_async(self._calibration_point(2, vx, vy))
        else:
            vx, vy = self._cal_point2_viewport
            p2 = calibration.CalibrationPoint(vx, vy, sx, sy)
            try:
                transform = calibration.compute_transform(self._cal_point1, p2)
            except ValueError as e:
                self._log(f"Calibration failed: {e}")
                return
            calibration.save_calibration(transform)
            self.transform = transform
            self._log(f"Calibration saved: scale=({transform.scale_x:.3f}, {transform.scale_y:.3f}) offset=({transform.offset_x:.1f}, {transform.offset_y:.1f})")
            self._run_async(self.bridge.send_fire_and_forget("calibration_complete", {"transform": transform.to_dict()}))

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
        if self.transform is None:
            messagebox.showerror("Not calibrated", "Run calibration first.")
            return
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

        self.replayer = Replayer(self.bridge, self.transform, steps, browser_hwnd=self.browser_hwnd, dry_run=dry_run)
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
