"""Tkinter GUI test tool for the nge controller.

Lets you connect to the ESP32-S3 HID firmware and drive mouse/keyboard from a
window: enter coordinates, click buttons, type text, toggle modifier keys, etc.

Only depends on pyserial (tkinter ships with CPython on Windows).

Run from the repo root with the serial monitor CLOSED:

    python -m examples.gui_test
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import ttk

import nge
from nge.humanize import HumanizeConfig
from nge.transport import SerialTransport, find_port


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("NGE HID Test Tool")
        root.geometry("580x960")
        root.minsize(540, 800)

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

        self._build_ui()

        # Single worker thread owns all serial access.
        threading.Thread(target=self._worker, daemon=True).start()
        self.root.after(100, self._drain_log)

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        pad = {"padx": 6, "pady": 4}

        # --- Connection ---
        conn = ttk.LabelFrame(self.root, text="Connection")
        conn.pack(fill="x", **pad)

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
        self.connect_btn.grid(row=1, column=0, columnspan=2, sticky="we", padx=4, pady=4)
        self.status_var = tk.StringVar(value="Not connected")
        ttk.Label(conn, textvariable=self.status_var, foreground="#a00").grid(
            row=1, column=2, columnspan=4, sticky="w"
        )

        # --- Mouse ---
        mouse = ttk.LabelFrame(self.root, text="Mouse")
        mouse.pack(fill="x", **pad)

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

        # --- Keyboard ---
        kb = ttk.LabelFrame(self.root, text="Keyboard")
        kb.pack(fill="x", **pad)

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

        # --- Humanize tuning (live) ---
        self._build_humanize_sliders()

        # --- OCR ---
        ocr = ttk.LabelFrame(self.root, text="OCR (region select)")
        ocr.pack(fill="x", **pad)
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

        # --- Panic ---
        panic = ttk.Frame(self.root)
        panic.pack(fill="x", **pad)
        stop = tk.Button(
            panic, text="STOP (release all)", command=self.on_stop,
            bg="#c0392b", fg="white", font=("", 11, "bold"),
        )
        stop.pack(fill="x")

        # --- Log ---
        logf = ttk.LabelFrame(self.root, text="Log")
        logf.pack(fill="both", expand=True, **pad)
        self.log = tk.Text(logf, height=10, state="disabled", wrap="word")
        self.log.pack(fill="both", expand=True, padx=4, pady=4)

    # ----------------------------------------------------- humanize sliders
    def _build_humanize_sliders(self) -> None:
        frame = ttk.LabelFrame(self.root, text="Humanize tuning (applies live)")
        frame.pack(fill="x", padx=6, pady=4)
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

    # ------------------------------------------------------------------ OCR
    def on_select_region(self) -> None:
        """Draw a fullscreen overlay and let the user drag a rectangle."""
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
                self.ocr_region = (left, top, right, bottom)
                self.ocr_region_var.set(f"Region: ({left},{top},{right},{bottom})")
                self._log(f"[ocr] region set to {self.ocr_region}")

        def on_cancel(_e: tk.Event) -> None:
            overlay.destroy()

        canvas.bind("<ButtonPress-1>", on_press)
        canvas.bind("<B1-Motion>", on_drag)
        canvas.bind("<ButtonRelease-1>", on_release)
        overlay.bind("<Escape>", on_cancel)
        overlay.focus_force()

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

    # -------------------------------------------------------------- helpers
    def _log(self, msg: str) -> None:
        self.log_q.put(msg)

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
