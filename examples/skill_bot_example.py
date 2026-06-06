"""Skill rotation test bot: hold right mouse, cycle keys 1-5.

Keeps the right mouse button held while repeatedly pressing 1, 2, 3, 4, 5.
Useful for testing skill-bar automation in games that aim with RMB.

Prerequisites:
  - Firmware flashed, ESP32-S3 on the native USB port.
  - pip install -r requirements.txt

Run (serial monitor CLOSED, focus the game window first):
  python -m examples.skill_bot_example

Stop: Ctrl+C (releases RMB and all keys). BOOT button on the board = HID panic.
"""

from __future__ import annotations

import time

import nge

SKILL_KEYS = ("1", "2", "3", "4", "5")
KEY_INTERVAL = 0.35  # seconds between each skill press


def main() -> None:
    ctrl = nge.connect()
    print("Connected. Screen size:", ctrl.screen_size)
    print(
        f"Holding RMB, cycling {', '.join(SKILL_KEYS)} every {KEY_INTERVAL:g}s. "
        "Ctrl+C to stop."
    )

    ctrl.mouse_down("R")
    try:
        while True:
            for key in SKILL_KEYS:
                ctrl.press(key)
                time.sleep(KEY_INTERVAL)
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        ctrl.mouse_up("R")
        ctrl.stop()
        print("Released RMB and all keys.")


if __name__ == "__main__":
    main()
