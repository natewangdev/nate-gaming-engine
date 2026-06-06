"""Tkinter GUI test tool for the nge controller.

Lets you connect to the ESP32-S3 HID firmware and drive mouse/keyboard from a
window: enter coordinates, click buttons, type text, toggle modifier keys, etc.

Only depends on pyserial (tkinter ships with CPython on Windows).

Run from the repo root with the serial monitor CLOSED:

    python -m examples.gui_test
"""

from __future__ import annotations

import queue
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, ttk

import nge
from nge.humanize import HumanizeConfig
from nge.transport import SerialTransport, find_port

# Default save folder for dataset screenshots (under the repo root).
DEFAULT_CAPTURE_DIR = r"C:\Users\Admin\Pictures\d4captures"
DEFAULT_CAPTURE_INTERVAL = "1.0"  # seconds
DEFAULT_YOLO_MODEL = (
    r"D:\Project\ultralytics-8.3.163\runs\detect\d4\weights\best.onnx"
)


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("NGE HID Test Tool")
        root.geometry("580x860")
        root.minsize(540, 640)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)

        self.ctrl: nge.Controller | None = None
        # Shared config object: the controller holds a reference to it, so slider
        # changes take effect on the next move without reconnecting.
        self.hcfg = HumanizeConfig()
        self.task_q: "queue.Queue" = queue.Queue()
        self.log_q: "queue.Queue[str]" = queue.Queue()

        # OCR state (lazy: capture + engine created on first use).
        self.ocr_region: tuple[int, int, int, int] | None = None
        self._ocr_capture = None
        self._ocr_engine = None

        # YOLO state (lazy: capture + detector created on first use).
        self.yolo_region: tuple[int, int, int, int] | None = None
        self._yolo_capture = None
        self._yolo_detector = None
        self._yolo_detector_path: str | None = None
        self._yolo_overlay: tk.Toplevel | None = None

        # Dataset capture state (its own ScreenCapture + dedicated thread).
        self.cap_region: tuple[int, int, int, int] | None = None
        self._cap_capture = None
        self._cap_thread: threading.Thread | None = None
        self._cap_stop = threading.Event()
        self._cap_count = 0
        self._cap_manual_busy = False
        self._cap_lock = threading.Lock()

        self._build_ui()
        self._setup_prtsc_hotkey()

        # Single worker thread owns all serial access.
        threading.Thread(target=self._worker, daemon=True).start()
        self.root.after(100, self._drain_log)

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        pad = {"padx": 6, "pady": 4}

        self._build_connection_bar(pad)

        notebook = ttk.Notebook(self.root)
        notebook.grid(row=1, column=0, sticky="nsew", **pad)

        tab_hid = ttk.Frame(notebook, padding=4)
        tab_ocr = ttk.Frame(notebook, padding=4)
        tab_yolo = ttk.Frame(notebook, padding=4)
        tab_cap = ttk.Frame(notebook, padding=4)
        notebook.add(tab_hid, text="HID Control")
        notebook.add(tab_ocr, text="OCR")
        notebook.add(tab_yolo, text="YOLO")
        notebook.add(tab_cap, text="Capture")

        self._build_hid_tab(tab_hid)
        self._build_ocr_tab(tab_ocr)
        self._build_yolo_tab(tab_yolo)
        self._build_capture_tab(tab_cap)

        bottom = ttk.Frame(self.root)
        bottom.grid(row=2, column=0, sticky="nsew", **pad)
        bottom.columnconfigure(0, weight=1)
        bottom.rowconfigure(1, weight=1)

        stop = tk.Button(
            bottom, text="STOP (release all)", command=self.on_stop,
            bg="#c0392b", fg="white", font=("", 11, "bold"),
        )
        stop.grid(row=0, column=0, sticky="ew", pady=(0, 4))

        logf = ttk.LabelFrame(bottom, text="Log")
        logf.grid(row=1, column=0, sticky="nsew")
        logf.columnconfigure(0, weight=1)
        logf.rowconfigure(1, weight=1)

        log_toolbar = ttk.Frame(logf)
        log_toolbar.grid(row=0, column=0, columnspan=2, sticky="ew", padx=4, pady=(4, 0))
        ttk.Button(log_toolbar, text="Clear", command=self.on_clear_log).pack(side="right")

        log_scroll = ttk.Scrollbar(logf, orient="vertical")
        log_scroll.grid(row=1, column=1, sticky="ns", pady=4, padx=(0, 4))
        self.log = tk.Text(
            logf, height=16, state="disabled", wrap="word",
            yscrollcommand=log_scroll.set,
        )
        self.log.grid(row=1, column=0, sticky="nsew", padx=(4, 0), pady=4)
        log_scroll.config(command=self.log.yview)

    def _build_connection_bar(self, pad: dict) -> None:
        conn = ttk.LabelFrame(self.root, text="Connection")
        conn.grid(row=0, column=0, sticky="ew", **pad)
        conn.columnconfigure(1, weight=1)

        ttk.Label(conn, text="Port:").grid(row=0, column=0, sticky="e", padx=4, pady=4)
        self.port_var = tk.StringVar(value=find_port() or "")
        ttk.Entry(conn, textvariable=self.port_var, width=12).grid(row=0, column=1, sticky="w")

        ttk.Label(conn, text="Screen W x H:").grid(row=0, column=2, sticky="e", padx=4)
        self.sw_var = tk.StringVar()
        self.sh_var = tk.StringVar()
        ttk.Entry(conn, textvariable=self.sw_var, width=6).grid(row=0, column=3, sticky="w")
        ttk.Entry(conn, textvariable=self.sh_var, width=6).grid(row=0, column=4, sticky="w")
        ttk.Label(conn, text="(blank = auto)").grid(row=0, column=5, sticky="w", padx=4)

        self.connect_btn = ttk.Button(conn, text="Connect", command=self.on_connect)
        self.connect_btn.grid(row=1, column=0, sticky="we", padx=4, pady=4)
        self.status_var = tk.StringVar(value="Not connected")
        ttk.Label(conn, textvariable=self.status_var, foreground="#a00").grid(
            row=1, column=1, columnspan=5, sticky="w", padx=4
        )

    def _build_hid_tab(self, parent: ttk.Frame) -> None:
        mouse = ttk.LabelFrame(parent, text="Mouse")
        mouse.pack(fill="x", pady=(0, 4))

        ttk.Label(mouse, text="X:").grid(row=0, column=0, sticky="e", padx=4, pady=4)
        self.x_var = tk.StringVar(value="960")
        ttk.Entry(mouse, textvariable=self.x_var, width=8).grid(row=0, column=1, sticky="w")
        ttk.Label(mouse, text="Y:").grid(row=0, column=2, sticky="e", padx=4)
        self.y_var = tk.StringVar(value="540")
        ttk.Entry(mouse, textvariable=self.y_var, width=8).grid(row=0, column=3, sticky="w")

        self.humanize_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(mouse, text="Humanize path", variable=self.humanize_var).grid(
            row=0, column=4, columnspan=2, sticky="w", padx=6
        )

        ttk.Button(mouse, text="Move", command=self.on_move).grid(
            row=1, column=0, columnspan=2, sticky="we", padx=4, pady=4
        )
        ttk.Button(mouse, text="Move + Left Click", command=lambda: self.on_move(click="L")).grid(
            row=1, column=2, columnspan=2, sticky="we", padx=4
        )
        ttk.Button(mouse, text="Move + Right Click", command=lambda: self.on_move(click="R")).grid(
            row=1, column=4, columnspan=2, sticky="we", padx=4
        )

        ttk.Button(mouse, text="Left Click", command=lambda: self.on_click("L")).grid(
            row=2, column=0, columnspan=2, sticky="we", padx=4, pady=4
        )
        ttk.Button(mouse, text="Right Click", command=lambda: self.on_click("R")).grid(
            row=2, column=2, columnspan=2, sticky="we", padx=4
        )
        ttk.Button(mouse, text="Middle Click", command=lambda: self.on_click("M")).grid(
            row=2, column=4, columnspan=2, sticky="we", padx=4
        )

        ttk.Label(mouse, text="Wheel:").grid(row=3, column=0, sticky="e", padx=4, pady=4)
        self.wheel_var = tk.StringVar(value="3")
        ttk.Entry(mouse, textvariable=self.wheel_var, width=8).grid(row=3, column=1, sticky="w")
        ttk.Button(mouse, text="Scroll", command=self.on_wheel).grid(
            row=3, column=2, columnspan=2, sticky="we", padx=4
        )

        kb = ttk.LabelFrame(parent, text="Keyboard")
        kb.pack(fill="x", pady=(0, 4))

        self.mod_ctrl = tk.BooleanVar()
        self.mod_shift = tk.BooleanVar()
        self.mod_alt = tk.BooleanVar()
        self.mod_win = tk.BooleanVar()
        ttk.Checkbutton(kb, text="Ctrl", variable=self.mod_ctrl).grid(row=0, column=0, sticky="w", padx=4)
        ttk.Checkbutton(kb, text="Shift", variable=self.mod_shift).grid(row=0, column=1, sticky="w")
        ttk.Checkbutton(kb, text="Alt", variable=self.mod_alt).grid(row=0, column=2, sticky="w")
        ttk.Checkbutton(kb, text="Win", variable=self.mod_win).grid(row=0, column=3, sticky="w")

        ttk.Label(kb, text="Key:").grid(row=1, column=0, sticky="e", padx=4, pady=4)
        self.key_var = tk.StringVar(value="space")
        ttk.Entry(kb, textvariable=self.key_var, width=12).grid(row=1, column=1, sticky="w")
        ttk.Button(kb, text="Press Key", command=self.on_press_key).grid(
            row=1, column=2, columnspan=2, sticky="we", padx=4
        )

        ttk.Label(kb, text="Text:").grid(row=2, column=0, sticky="e", padx=4, pady=4)
        self.text_var = tk.StringVar(value="hello")
        ttk.Entry(kb, textvariable=self.text_var, width=24).grid(
            row=2, column=1, columnspan=2, sticky="we"
        )
        ttk.Button(kb, text="Type Text", command=self.on_type_text).grid(
            row=2, column=3, sticky="we", padx=4
        )

        self._build_humanize_sliders(parent)

    def _build_ocr_tab(self, parent: ttk.Frame) -> None:
        ocr = ttk.LabelFrame(parent, text="OCR (region select)")
        ocr.pack(fill="x")
        ocr.columnconfigure(1, weight=1)

        ttk.Button(ocr, text="Select Region", command=self.on_select_region).grid(
            row=0, column=0, sticky="we", padx=4, pady=4
        )
        self.ocr_region_var = tk.StringVar(value="Region: full screen")
        ttk.Label(ocr, textvariable=self.ocr_region_var).grid(
            row=0, column=1, columnspan=3, sticky="w", padx=4
        )
        self.ocr_numbers_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(ocr, text="Numbers only", variable=self.ocr_numbers_var).grid(
            row=1, column=0, sticky="w", padx=4
        )
        ttk.Button(ocr, text="Recognize", command=self.on_ocr_read).grid(
            row=1, column=1, sticky="we", padx=4, pady=4
        )
        ttk.Button(ocr, text="Clear Region", command=self.on_clear_region).grid(
            row=1, column=2, sticky="we", padx=4
        )

    def _build_yolo_tab(self, parent: ttk.Frame) -> None:
        yolo = ttk.LabelFrame(parent, text="YOLO detection")
        yolo.pack(fill="x")
        yolo.columnconfigure(1, weight=1)

        ttk.Label(yolo, text="Model:").grid(row=0, column=0, sticky="e", padx=4, pady=4)
        self.yolo_model_var = tk.StringVar(value=DEFAULT_YOLO_MODEL)
        ttk.Entry(yolo, textvariable=self.yolo_model_var).grid(
            row=0, column=1, columnspan=2, sticky="we", padx=4
        )
        ttk.Button(yolo, text="Browse...", command=self.on_yolo_browse_model).grid(
            row=0, column=3, sticky="we", padx=4
        )

        ttk.Button(yolo, text="Select Region", command=self.on_yolo_select_region).grid(
            row=1, column=0, sticky="we", padx=4, pady=4
        )
        self.yolo_region_var = tk.StringVar(value="Region: full screen")
        ttk.Label(yolo, textvariable=self.yolo_region_var).grid(
            row=1, column=1, columnspan=2, sticky="w", padx=4
        )
        ttk.Button(yolo, text="Clear Region", command=self.on_yolo_clear_region).grid(
            row=1, column=3, sticky="we", padx=4
        )

        ttk.Label(yolo, text="Conf:").grid(row=2, column=0, sticky="e", padx=4, pady=4)
        self.yolo_conf_var = tk.StringVar(value="0.5")
        ttk.Entry(yolo, textvariable=self.yolo_conf_var, width=8).grid(
            row=2, column=1, sticky="w", padx=4
        )
        ttk.Button(yolo, text="Predict", command=self.on_yolo_predict).grid(
            row=2, column=2, sticky="we", padx=4, pady=4
        )
        ttk.Button(yolo, text="Close Overlay", command=self.on_yolo_close_overlay).grid(
            row=2, column=3, sticky="we", padx=4
        )

    def _build_capture_tab(self, parent: ttk.Frame) -> None:
        cap = ttk.LabelFrame(parent, text="Dataset capture (screenshots)")
        cap.pack(fill="x")
        cap.columnconfigure(1, weight=1)

        ttk.Label(cap, text="Save dir:").grid(row=0, column=0, sticky="e", padx=4, pady=4)
        self.cap_dir_var = tk.StringVar(value=DEFAULT_CAPTURE_DIR)
        ttk.Entry(cap, textvariable=self.cap_dir_var).grid(
            row=0, column=1, columnspan=2, sticky="we", padx=4
        )
        ttk.Button(cap, text="Browse...", command=self.on_cap_browse).grid(
            row=0, column=3, sticky="we", padx=4
        )

        ttk.Button(cap, text="Select Region", command=self.on_cap_select_region).grid(
            row=1, column=0, sticky="we", padx=4, pady=4
        )
        self.cap_region_var = tk.StringVar(value="Region: full screen")
        ttk.Label(cap, textvariable=self.cap_region_var).grid(
            row=1, column=1, columnspan=2, sticky="w", padx=4
        )
        ttk.Button(cap, text="Clear Region", command=self.on_cap_clear_region).grid(
            row=1, column=3, sticky="we", padx=4
        )

        ttk.Label(cap, text="Interval (s):").grid(row=2, column=0, sticky="e", padx=4, pady=4)
        self.cap_interval_var = tk.StringVar(value=DEFAULT_CAPTURE_INTERVAL)
        ttk.Entry(cap, textvariable=self.cap_interval_var, width=8).grid(
            row=2, column=1, sticky="w", padx=4
        )
        self.cap_status_var = tk.StringVar(value="Idle - 0 saved")
        ttk.Label(cap, textvariable=self.cap_status_var).grid(
            row=2, column=2, columnspan=2, sticky="w", padx=4
        )

        ttk.Label(cap, text="Format:").grid(row=3, column=0, sticky="e", padx=4, pady=4)
        self.cap_format_var = tk.StringVar(value="jpg")
        fmt_box = ttk.Combobox(
            cap, textvariable=self.cap_format_var, values=["jpg", "png"],
            width=6, state="readonly",
        )
        fmt_box.grid(row=3, column=1, sticky="w", padx=4)

        ttk.Label(cap, text="JPG quality:").grid(row=3, column=2, sticky="e", padx=4)
        self.cap_quality_var = tk.StringVar(value="90")
        ttk.Entry(cap, textvariable=self.cap_quality_var, width=6).grid(
            row=3, column=3, sticky="w", padx=4
        )

        ttk.Label(cap, text="Max width (0=keep):").grid(row=4, column=0, sticky="e", padx=4, pady=4)
        self.cap_maxw_var = tk.StringVar(value="0")
        ttk.Entry(cap, textvariable=self.cap_maxw_var, width=8).grid(
            row=4, column=1, sticky="w", padx=4
        )
        ttk.Label(cap, text="(downscale wider images)").grid(
            row=4, column=2, columnspan=2, sticky="w", padx=4
        )

        self.cap_start_btn = ttk.Button(cap, text="Start", command=self.on_cap_start)
        self.cap_start_btn.grid(row=5, column=0, columnspan=2, sticky="we", padx=4, pady=4)
        self.cap_stop_btn = ttk.Button(
            cap, text="Stop", command=self.on_cap_stop, state="disabled"
        )
        self.cap_stop_btn.grid(row=5, column=2, columnspan=2, sticky="we", padx=4)

        self.cap_manual_btn = ttk.Button(
            cap, text="Manual capture", command=self.on_cap_manual
        )
        self.cap_manual_btn.grid(row=6, column=0, columnspan=2, sticky="we", padx=4, pady=4)
        ttk.Label(cap, text="Hotkey: PrtSc").grid(
            row=6, column=2, columnspan=2, sticky="w", padx=4
        )

    # ----------------------------------------------------- humanize sliders
    def _build_humanize_sliders(self, parent) -> None:
        frame = ttk.LabelFrame(parent, text="Humanize tuning (applies live)")
        frame.pack(fill="x", pady=(0, 4))
        frame.columnconfigure(1, weight=1)

        # (label, attribute, min, max, integer?, value-format)
        specs = [
            ("Max speed (px/s)", "max_speed", 1000.0, 12000.0, True, "{:.0f}"),
            ("Curvature", "curvature", 0.0, 0.5, False, "{:.2f}"),
            ("Jitter (px)", "jitter", 0.0, 3.0, False, "{:.2f}"),
            ("Overshoot chance", "overshoot_chance", 0.0, 1.0, False, "{:.2f}"),
            ("Pause chance", "pause_chance", 0.0, 0.30, False, "{:.2f}"),
            ("Rate (Hz)", "rate_hz", 60.0, 250.0, True, "{:.0f}"),
        ]
        for row, (label, attr, lo, hi, is_int, fmt) in enumerate(specs):
            self._slider_row(frame, row, label, attr, lo, hi, is_int, fmt)

        ttk.Button(frame, text="Reset defaults", command=self.on_reset_humanize).grid(
            row=len(specs), column=0, columnspan=3, sticky="we", padx=4, pady=4
        )

    def _slider_row(self, parent, row, label, attr, lo, hi, is_int, fmt) -> None:
        ttk.Label(parent, text=label, width=16, anchor="e").grid(
            row=row, column=0, sticky="e", padx=4, pady=2
        )
        value_lbl = ttk.Label(parent, width=7, anchor="w")
        cur = getattr(self.hcfg, attr)

        def on_change(raw: str) -> None:
            val = float(raw)
            if is_int:
                val = round(val)
            setattr(self.hcfg, attr, val)
            value_lbl.config(text=fmt.format(val))

        scale = ttk.Scale(parent, from_=lo, to=hi, value=cur, command=on_change)
        scale.grid(row=row, column=1, sticky="we", padx=4)
        value_lbl.grid(row=row, column=2, sticky="w", padx=4)
        value_lbl.config(text=fmt.format(cur))
        # Remember the scale widget so we can reset it later.
        self._scales = getattr(self, "_scales", {})
        self._scales[attr] = (scale, fmt, is_int, value_lbl)

    def on_reset_humanize(self) -> None:
        defaults = HumanizeConfig()
        for attr, (scale, fmt, is_int, value_lbl) in self._scales.items():
            val = getattr(defaults, attr)
            setattr(self.hcfg, attr, val)
            scale.set(val)
            value_lbl.config(text=fmt.format(val))
        self._log("[ok] Humanize params reset to defaults")

    # -------------------------------------------------------- region overlay
    def _drag_region(self, on_done) -> None:
        """Draw a fullscreen overlay; call on_done(left, top, right, bottom)."""
        overlay = tk.Toplevel(self.root)
        overlay.attributes("-fullscreen", True)
        overlay.attributes("-alpha", 0.25)
        overlay.attributes("-topmost", True)
        overlay.configure(cursor="cross", bg="black")
        canvas = tk.Canvas(overlay, highlightthickness=0, bg="gray20")
        canvas.pack(fill="both", expand=True)
        state = {"x0": 0, "y0": 0, "rect": None}

        def on_press(e: tk.Event) -> None:
            state["x0"], state["y0"] = e.x, e.y
            state["rect"] = canvas.create_rectangle(
                e.x, e.y, e.x, e.y, outline="red", width=2
            )

        def on_drag(e: tk.Event) -> None:
            if state["rect"] is not None:
                canvas.coords(state["rect"], state["x0"], state["y0"], e.x, e.y)

        def on_release(e: tk.Event) -> None:
            left, top = min(state["x0"], e.x), min(state["y0"], e.y)
            right, bottom = max(state["x0"], e.x), max(state["y0"], e.y)
            overlay.destroy()
            if right - left > 2 and bottom - top > 2:
                on_done(left, top, right, bottom)

        def on_cancel(_e: tk.Event) -> None:
            overlay.destroy()

        canvas.bind("<ButtonPress-1>", on_press)
        canvas.bind("<B1-Motion>", on_drag)
        canvas.bind("<ButtonRelease-1>", on_release)
        overlay.bind("<Escape>", on_cancel)
        overlay.focus_force()

    # ------------------------------------------------------------------ OCR
    def on_select_region(self) -> None:
        def done(left: int, top: int, right: int, bottom: int) -> None:
            self.ocr_region = (left, top, right, bottom)
            self.ocr_region_var.set(f"Region: ({left},{top},{right},{bottom})")
            self._log(f"[ocr] region set to {self.ocr_region}")

        self._drag_region(done)

    def on_clear_region(self) -> None:
        self.ocr_region = None
        self.ocr_region_var.set("Region: full screen")
        self._log("[ocr] region cleared (full screen)")

    def on_ocr_read(self) -> None:
        region = self.ocr_region
        numbers = self.ocr_numbers_var.get()

        def task() -> None:
            try:
                if self._ocr_capture is None:
                    from nge.capture import ScreenCapture

                    self._ocr_capture = ScreenCapture()
                if self._ocr_engine is None:
                    from nge.ocr import OCREngine

                    self._log("[ocr] loading engine (first run may take a moment)...")
                    self._ocr_engine = OCREngine()
                frame = self._ocr_capture.grab()
                if frame is None:
                    self._log("[error] OCR: no frame captured")
                    return
                if numbers:
                    nums = self._ocr_engine.read_numbers(frame, region=region)
                    self._log(f"[ocr] numbers: {nums}")
                else:
                    results = self._ocr_engine.read(frame, region=region)
                    if not results:
                        self._log("[ocr] no text found")
                    for r in results:
                        self._log(f"[ocr] {r.text!r} @ ({r.x},{r.y}) score={r.score:.2f}")
            except Exception as exc:  # noqa: BLE001
                self._log(f"[error] OCR failed: {exc}")

        self._enqueue(task)

    # -------------------------------------------------------- YOLO detection
    def on_yolo_browse_model(self) -> None:
        initial = self.yolo_model_var.get().strip() or DEFAULT_YOLO_MODEL
        initial_dir = str(Path(initial).parent) if Path(initial).parent.exists() else ""
        chosen = filedialog.askopenfilename(
            initialdir=initial_dir or None,
            title="Select YOLO ONNX model",
            filetypes=[("ONNX models", "*.onnx"), ("All files", "*.*")],
        )
        if chosen:
            self.yolo_model_var.set(chosen)
            self._yolo_detector = None
            self._yolo_detector_path = None

    def on_yolo_select_region(self) -> None:
        def done(left: int, top: int, right: int, bottom: int) -> None:
            self.yolo_region = (left, top, right, bottom)
            self.yolo_region_var.set(f"Region: ({left},{top},{right},{bottom})")
            self._log(f"[yolo] region set to {self.yolo_region}")

        self._drag_region(done)

    def on_yolo_clear_region(self) -> None:
        self.yolo_region = None
        self.yolo_region_var.set("Region: full screen")
        self._log("[yolo] region cleared (full screen)")

    def on_yolo_close_overlay(self) -> None:
        if self._yolo_overlay is not None:
            try:
                self._yolo_overlay.destroy()
            except tk.TclError:
                pass
            self._yolo_overlay = None

    def _show_yolo_overlay(self, detections) -> None:
        """Draw detection boxes on a fullscreen topmost overlay."""
        self.on_yolo_close_overlay()

        overlay = tk.Toplevel(self.root)
        overlay.attributes("-fullscreen", True)
        overlay.attributes("-alpha", 0.35)
        overlay.attributes("-topmost", True)
        overlay.configure(cursor="arrow", bg="black")
        canvas = tk.Canvas(overlay, highlightthickness=0, bg="gray20")
        canvas.pack(fill="both", expand=True)

        for d in detections:
            x1, y1, x2, y2 = d.box
            canvas.create_rectangle(x1, y1, x2, y2, outline="#00ff00", width=2)
            label = f"{d.label} {d.conf:.2f}"
            canvas.create_text(
                x1 + 2, max(y1 - 4, 0), text=label, anchor="sw",
                fill="#00ff00", font=("", 10, "bold"),
            )
            canvas.create_oval(
                d.x - 3, d.y - 3, d.x + 3, d.y + 3,
                outline="#ff4444", fill="#ff4444",
            )

        def dismiss(_e: tk.Event | None = None) -> None:
            self.on_yolo_close_overlay()

        canvas.bind("<ButtonPress-1>", dismiss)
        overlay.bind("<Escape>", dismiss)
        overlay.focus_force()
        self._yolo_overlay = overlay

    def _get_yolo_detector(self, model_path: str):
        from nge.detect import YoloDetector

        if self._yolo_detector is None or self._yolo_detector_path != model_path:
            self._log(f"[yolo] loading model: {model_path}")
            self._yolo_detector = YoloDetector(model_path)
            self._yolo_detector_path = model_path
        return self._yolo_detector

    def on_yolo_predict(self) -> None:
        model_path = self.yolo_model_var.get().strip()
        if not model_path:
            self._log("[error] YOLO model path is empty")
            return
        if not Path(model_path).is_file():
            self._log(f"[error] YOLO model not found: {model_path}")
            return
        try:
            conf = float(self.yolo_conf_var.get())
            if not 0.0 < conf <= 1.0:
                raise ValueError
        except ValueError:
            self._log("[error] Conf must be a number between 0 and 1")
            return

        region = self.yolo_region

        def task() -> None:
            try:
                if self._yolo_capture is None:
                    from nge.capture import ScreenCapture

                    self._yolo_capture = ScreenCapture()
                frame = self._yolo_capture.grab()
                if frame is None:
                    self._log("[error] YOLO: no frame captured")
                    return

                detector = self._get_yolo_detector(model_path)
                t0 = time.perf_counter()
                dets = detector.detect(frame, region=region, conf=conf)
                elapsed = time.perf_counter() - t0

                print(f"[yolo] inference: {elapsed * 1000:.1f} ms, {len(dets)} detection(s)")
                self._log(
                    f"[yolo] inference: {elapsed * 1000:.1f} ms, {len(dets)} detection(s)"
                )
                if not dets:
                    self._log("[yolo] no objects detected")
                    return
                for d in dets:
                    msg = (
                        f"[yolo] {d.label} conf={d.conf:.2f} "
                        f"center=({d.x},{d.y}) box={d.box}"
                    )
                    print(msg)
                    self._log(msg)
                self.root.after(0, lambda: self._show_yolo_overlay(dets))
            except Exception as exc:  # noqa: BLE001
                self._log(f"[error] YOLO failed: {exc}")

        self._enqueue(task)

    # -------------------------------------------------------- dataset capture
    def on_cap_browse(self) -> None:
        initial = self.cap_dir_var.get().strip() or DEFAULT_CAPTURE_DIR
        chosen = filedialog.askdirectory(initialdir=initial, title="Select save folder")
        if chosen:
            self.cap_dir_var.set(chosen)

    def on_cap_select_region(self) -> None:
        def done(left: int, top: int, right: int, bottom: int) -> None:
            self.cap_region = (left, top, right, bottom)
            self.cap_region_var.set(f"Region: ({left},{top},{right},{bottom})")
            self._log(f"[capture] region set to {self.cap_region}")

        self._drag_region(done)

    def on_cap_clear_region(self) -> None:
        self.cap_region = None
        self.cap_region_var.set("Region: full screen")
        self._log("[capture] region cleared (full screen)")

    def _read_cap_settings(self) -> dict | None:
        """Parse dataset-capture UI fields (shared by auto and manual capture)."""
        save_dir = Path(self.cap_dir_var.get().strip() or DEFAULT_CAPTURE_DIR)
        try:
            save_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # noqa: BLE001
            self._log(f"[error] cannot create save dir: {exc}")
            return None

        fmt = self.cap_format_var.get().strip().lower()
        try:
            quality = max(1, min(100, int(self.cap_quality_var.get())))
        except ValueError:
            self._log("[error] JPG quality must be an integer (1-100)")
            return None
        try:
            max_w = max(0, int(self.cap_maxw_var.get()))
        except ValueError:
            self._log("[error] Max width must be an integer (0 = keep)")
            return None

        return {
            "save_dir": save_dir,
            "region": self.cap_region,
            "fmt": fmt,
            "quality": quality,
            "max_w": max_w,
        }

    def _init_cap_capture(self) -> bool:
        try:
            if self._cap_capture is None:
                from nge.capture import ScreenCapture

                self._cap_capture = ScreenCapture()
            return True
        except Exception as exc:  # noqa: BLE001
            self._log(f"[error] capture init failed: {exc}")
            return False

    def _write_cap_frame(self, frame, settings: dict) -> Path | None:
        import cv2

        region = settings["region"]
        max_w = settings["max_w"]
        fmt = settings["fmt"]
        quality = settings["quality"]
        save_dir = settings["save_dir"]

        if region is not None:
            l, t, r, b = region
            frame = frame[t:b, l:r]
        if max_w and frame.shape[1] > max_w:
            scale = max_w / frame.shape[1]
            new_h = int(round(frame.shape[0] * scale))
            frame = cv2.resize(frame, (max_w, new_h), interpolation=cv2.INTER_AREA)

        ext = "jpg" if fmt == "jpg" else "png"
        write_params = [cv2.IMWRITE_JPEG_QUALITY, quality] if ext == "jpg" else []
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        path = save_dir / f"shot_{ts}.{ext}"
        if cv2.imwrite(str(path), frame, write_params):
            return path
        self._log(f"[error] imwrite failed: {path}")
        return None

    def _update_cap_status(self) -> None:
        running = self._cap_thread is not None and self._cap_thread.is_alive()
        prefix = "Running" if running else "Idle"
        self.cap_status_var.set(f"{prefix} - {self._cap_count} saved")

    def _capture_once(self) -> bool:
        """Grab one frame and save using current dataset-capture settings."""
        settings = self._read_cap_settings()
        if settings is None:
            return False
        with self._cap_lock:
            if not self._init_cap_capture():
                return False
            try:
                frame = self._cap_capture.grab()
                if frame is None:
                    self._log("[capture] no frame, skipping")
                    return False
                path = self._write_cap_frame(frame, settings)
                if path is None:
                    return False
                self._cap_count += 1
                self.root.after(0, self._update_cap_status)
                self._log(f"[capture] saved {path.name} (#{self._cap_count})")
                return True
            except Exception as exc:  # noqa: BLE001
                self._log(f"[error] capture failed: {exc}")
                return False

    def on_cap_manual(self) -> None:
        if self._cap_manual_busy:
            return
        self._cap_manual_busy = True

        def work() -> None:
            try:
                self._capture_once()
            finally:
                self._cap_manual_busy = False

        threading.Thread(target=work, daemon=True).start()

    def _setup_prtsc_hotkey(self) -> None:
        """Register PrtSc: global on Windows, window-local fallback elsewhere."""
        self.root.bind("<Print>", lambda _e: self.on_cap_manual())
        if sys.platform != "win32":
            return
        import ctypes
        from ctypes import wintypes

        self._user32 = ctypes.windll.user32
        self._prtsc_hotkey_id = 1
        hwnd = self.root.winfo_id()
        # MOD_NOREPEAT: ignore auto-repeat while key is held.
        ok = self._user32.RegisterHotKey(hwnd, self._prtsc_hotkey_id, 0x4000, 0x2C)
        if not ok:
            self._log("[capture] global PrtSc unavailable; use button or focus window + PrtSc")
            return
        self._prtsc_msg = wintypes.MSG()
        self._log("[capture] PrtSc hotkey registered (global)")

        def poll() -> None:
            if not getattr(self, "_user32", None):
                return
            pm_remove = 0x0001
            while self._user32.PeekMessageW(
                ctypes.byref(self._prtsc_msg), None, 0x0312, 0x0312, pm_remove
            ):
                if self._prtsc_msg.wParam == self._prtsc_hotkey_id:
                    self.on_cap_manual()
            self.root.after(50, poll)

        self.root.after(50, poll)
        self.root.bind("<Destroy>", self._unregister_prtsc_hotkey, add="+")

    def _unregister_prtsc_hotkey(self, _event: tk.Event | None = None) -> None:
        if sys.platform != "win32" or not getattr(self, "_user32", None):
            return
        try:
            hwnd = self.root.winfo_id()
            self._user32.UnregisterHotKey(hwnd, self._prtsc_hotkey_id)
        except tk.TclError:
            pass
        self._user32 = None

    def on_cap_start(self) -> None:
        if self._cap_thread is not None and self._cap_thread.is_alive():
            self._log("[capture] already running")
            return
        try:
            interval = float(self.cap_interval_var.get())
            if interval <= 0:
                raise ValueError
        except ValueError:
            self._log("[error] Interval must be a positive number")
            return
        settings = self._read_cap_settings()
        if settings is None:
            return

        self._cap_count = 0
        self._cap_stop.clear()
        self._cap_thread = threading.Thread(
            target=self._capture_loop,
            args=(interval, settings),
            daemon=True,
        )
        self._cap_thread.start()
        self.cap_start_btn.config(state="disabled")
        self.cap_stop_btn.config(state="normal")
        s = settings
        self._log(
            f"[capture] started -> {s['save_dir']} (every {interval:g}s, region={s['region']}, "
            f"{s['fmt']}{'/q'+str(s['quality']) if s['fmt'] == 'jpg' else ''}, "
            f"max_w={s['max_w'] or 'keep'})"
        )

    def on_cap_stop(self) -> None:
        self._cap_stop.set()
        self.cap_start_btn.config(state="normal")
        self.cap_stop_btn.config(state="disabled")
        self._log(f"[capture] stopped - {self._cap_count} image(s) saved")

    def _capture_loop(self, interval: float, settings: dict) -> None:
        if not self._init_cap_capture():
            self.root.after(0, self.on_cap_stop)
            return

        while not self._cap_stop.is_set():
            start = time.monotonic()
            # Re-read UI each shot so manual tweaks apply without restarting.
            live = self._read_cap_settings()
            if live is not None:
                settings = live
            self._capture_once()
            elapsed = time.monotonic() - start
            self._cap_stop.wait(max(0.0, interval - elapsed))

        self.root.after(0, self._update_cap_status)

    # -------------------------------------------------------------- helpers
    def _log(self, msg: str) -> None:
        self.log_q.put(msg)

    def on_clear_log(self) -> None:
        while True:
            try:
                self.log_q.get_nowait()
            except queue.Empty:
                break
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _drain_log(self) -> None:
        try:
            while True:
                msg = self.log_q.get_nowait()
                self.log.configure(state="normal")
                self.log.insert("end", msg + "\n")
                self.log.see("end")
                self.log.configure(state="disabled")
        except queue.Empty:
            pass
        self.root.after(100, self._drain_log)

    def _set_status(self, text: str, ok: bool) -> None:
        self.status_var.set(text)

    def _need_ctrl(self) -> bool:
        if self.ctrl is None:
            self._log("[error] Not connected")
            return False
        return True

    def _enqueue(self, fn) -> None:
        self.task_q.put(fn)

    def _modifiers(self) -> list[str]:
        mods = []
        if self.mod_ctrl.get():
            mods.append("ctrl")
        if self.mod_shift.get():
            mods.append("shift")
        if self.mod_alt.get():
            mods.append("alt")
        if self.mod_win.get():
            mods.append("win")
        return mods

    # ------------------------------------------------------------- actions
    def on_connect(self) -> None:
        port = self.port_var.get().strip() or None
        sw, sh = self.sw_var.get().strip(), self.sh_var.get().strip()
        screen = (int(sw), int(sh)) if sw and sh else None

        def task() -> None:
            if self.ctrl is not None:
                try:
                    self.ctrl.transport.close()
                except Exception:  # noqa: BLE001
                    pass
                self.ctrl = None
            try:
                transport = SerialTransport(port=port).open()
                if not transport.ping():
                    transport.close()
                    self._log("[error] Device did not answer PING")
                    self.root.after(0, lambda: self._set_status("PING failed", False))
                    return
                self.ctrl = nge.Controller(
                    transport=transport, screen_size=screen, humanize=self.hcfg
                )
                w, h = self.ctrl.screen_size
                self._log(f"[ok] Connected on {transport.port}, screen {w}x{h}")
                self.root.after(0, lambda: self._set_status(f"Connected {transport.port}", True))
            except Exception as exc:  # noqa: BLE001
                self._log(f"[error] Connect failed: {exc}")
                self.root.after(0, lambda: self._set_status("Connect failed", False))

        self._enqueue(task)

    def _xy(self) -> tuple[float, float] | None:
        try:
            return float(self.x_var.get()), float(self.y_var.get())
        except ValueError:
            self._log("[error] X/Y must be numbers")
            return None

    def on_move(self, click: str | None = None) -> None:
        if not self._need_ctrl():
            return
        xy = self._xy()
        if xy is None:
            return
        human = self.humanize_var.get()

        def task() -> None:
            try:
                if human:
                    self.ctrl.move_to(xy[0], xy[1])
                else:
                    self.ctrl.move_instant(xy[0], xy[1])
                self._log(f"[ok] move{'+'+click if click else ''} -> {xy[0]:.0f},{xy[1]:.0f}")
                if click:
                    self.ctrl.click(button=click)
            except Exception as exc:  # noqa: BLE001
                self._log(f"[error] move failed: {exc}")

        self._enqueue(task)

    def on_click(self, button: str) -> None:
        if not self._need_ctrl():
            return

        def task() -> None:
            try:
                self.ctrl.click(button=button)
                self._log(f"[ok] click {button}")
            except Exception as exc:  # noqa: BLE001
                self._log(f"[error] click failed: {exc}")

        self._enqueue(task)

    def on_wheel(self) -> None:
        if not self._need_ctrl():
            return
        try:
            delta = int(self.wheel_var.get())
        except ValueError:
            self._log("[error] Wheel must be an integer")
            return

        def task() -> None:
            try:
                self.ctrl.wheel(delta)
                self._log(f"[ok] wheel {delta}")
            except Exception as exc:  # noqa: BLE001
                self._log(f"[error] wheel failed: {exc}")

        self._enqueue(task)

    def on_press_key(self) -> None:
        if not self._need_ctrl():
            return
        key = self.key_var.get().strip()
        mods = self._modifiers()

        def task() -> None:
            try:
                if mods:
                    self.ctrl.hotkey(*mods, key)
                    self._log(f"[ok] hotkey {'+'.join(mods)}+{key}")
                else:
                    self.ctrl.press(key)
                    self._log(f"[ok] press {key}")
            except Exception as exc:  # noqa: BLE001
                self._log(f"[error] key failed: {exc}")

        self._enqueue(task)

    def on_type_text(self) -> None:
        if not self._need_ctrl():
            return
        text = self.text_var.get()

        def task() -> None:
            for ch in text:
                try:
                    self.ctrl.press(ch)
                except Exception as exc:  # noqa: BLE001
                    self._log(f"[skip] {ch!r}: {exc}")
            self._log(f"[ok] typed {text!r}")

        self._enqueue(task)

    def on_stop(self) -> None:
        if not self._need_ctrl():
            return

        def task() -> None:
            try:
                self.ctrl.stop()
                self._log("[ok] STOP - released all")
            except Exception as exc:  # noqa: BLE001
                self._log(f"[error] stop failed: {exc}")

        self._enqueue(task)

    # -------------------------------------------------------------- worker
    def _worker(self) -> None:
        while True:
            task = self.task_q.get()
            try:
                task()
            except Exception as exc:  # noqa: BLE001
                self._log(f"[error] {exc}")


def main() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
