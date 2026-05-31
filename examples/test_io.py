"""Interactive I/O test for the nge controller.

Verifies mouse movement, left/right click, and keyboard input through the
ESP32-S3 HID firmware.

Run from the repo root (so `import nge` works), with the ESP32-S3 plugged into
its native USB port and the serial monitor CLOSED:

    python -m examples.test_io
"""

from __future__ import annotations

import time

import nge


def banner(text: str) -> None:
    print(f"\n=== {text} ===")


def main() -> None:
    # Auto-detect the ESP32-S3 port; override with nge.connect(port="COM7").
    ctrl = nge.connect()
    w, h = ctrl.screen_size
    print(f"Connected. Screen size = {w}x{h}")
    print("Tip: press the BOOT button on the board at any time to release all.")

    while True:
        print(
            "\nChoose a test:\n"
            "  1) Mouse move (human-like) to a few points\n"
            "  2) Move to a specific pixel (you type x y)\n"
            "  3) Left click (at current position)\n"
            "  4) Right click (at current position)\n"
            "  5) Keyboard: type a word\n"
            "  6) Keyboard: press a single key (you type its name)\n"
            "  7) Hotkey Ctrl+A\n"
            "  0) Quit"
        )
        choice = input("> ").strip()

        if choice == "1":
            banner("Mouse move")
            for fx, fy in [(0.5, 0.5), (0.3, 0.3), (0.7, 0.4), (0.5, 0.8)]:
                px, py = w * fx, h * fy
                print(f"move_to({px:.0f}, {py:.0f})")
                ctrl.move_to(px, py)
                time.sleep(0.4)

        elif choice == "2":
            try:
                xs, ys = input("Enter pixel x y: ").split()
                ctrl.move_to(float(xs), float(ys))
            except ValueError:
                print("Bad input, expected: x y")

        elif choice == "3":
            banner("Left click")
            ctrl.click(button="L")

        elif choice == "4":
            banner("Right click")
            ctrl.click(button="R")

        elif choice == "5":
            banner("Type word")
            word = input("Word to type (letters/digits): ").strip()
            for ch in word:
                ctrl.press(ch)
                time.sleep(0.05)

        elif choice == "6":
            key = input("Key name (e.g. space, enter, f1, a): ").strip()
            try:
                ctrl.press(key)
                print(f"pressed {key}")
            except KeyError as exc:
                print(exc)

        elif choice == "7":
            banner("Ctrl+A")
            ctrl.hotkey("ctrl", "a")

        elif choice == "0":
            break

        else:
            print("Unknown choice")

    ctrl.stop()
    ctrl.transport.close()
    print("Closed.")


if __name__ == "__main__":
    main()
