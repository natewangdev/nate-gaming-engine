"""Minimal end-to-end smoke test for the nge controller.

Prerequisites:
  1. Flash firmware/nge_hid/nge_hid.ino to an ESP32-S3.
  2. Plug the ESP32-S3 into the PC you want to control.
  3. pip install -r requirements.txt

Run:
  python -m examples.quickstart
"""

from __future__ import annotations

import time

import nge


def main() -> None:
    # Auto-detect the ESP32-S3 serial port; pass port="COM5" to override.
    ctrl = nge.connect()
    print("Connected. Screen size:", ctrl.screen_size)

    w, h = ctrl.screen_size

    # Move along a human-like path to the screen center and click.
    ctrl.move_to(w * 0.5, h * 0.5)
    time.sleep(0.3)
    ctrl.click()

    # Move to a few points to visualize the path shape.
    for fx, fy in [(0.3, 0.3), (0.7, 0.4), (0.5, 0.7)]:
        ctrl.move_to(w * fx, h * fy)
        time.sleep(0.2)

    # Tap a key and a hotkey.
    ctrl.press("space")
    ctrl.hotkey("ctrl", "a")

    # Always release everything when done.
    ctrl.stop()
    print("Done.")


if __name__ == "__main__":
    main()
