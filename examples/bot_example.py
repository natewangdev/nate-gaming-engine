"""Example bot built on nge.bot.

Demonstrates the rule engine: capture the screen each tick, look for templates,
and act via the HID controller. Replace the template paths and logic with your
own; this file is a scaffold, not a ready-made cheat.

Prerequisites:
  - Firmware flashed, ESP32-S3 on the native USB port.
  - pip install -r requirements.txt   (needs opencv + a capture backend)
  - PNG templates placed under ./templates/

Run (serial monitor CLOSED):
  python -m examples.bot_example
"""

from __future__ import annotations

from pathlib import Path

import nge
from nge.bot import Bot, BotConfig
from nge.capture import ScreenCapture

# Resource root for relative paths (templates, etc.).
ASSET_ROOT = Path(r"C:\Users\Admin\Desktop\d4")


def build_bot() -> Bot:
    controller = nge.connect()  # or nge.connect(port="COM7")

    # Capture the whole primary screen; pass region=(l, t, r, b) to limit it.
    capture = ScreenCapture()

    config = BotConfig(
        tick_hz=1.0,  # evaluate rules ~1x per second
        tick_jitter=0.35,  # randomize timing +/-35%
        one_action_per_tick=True,
        session_max_seconds=0,  # 0 = run until Ctrl+C
        break_every=3600.0,  # take a human-like break ~every 1 hour
        break_duration=(5.0, 15.0),
    )

    bot = Bot(controller, capture, config=config, asset_root=ASSET_ROOT)

    # --- Rules: higher priority is evaluated first ---

    @bot.rule(name="pickup_loot", priority=10, cooldown=0.4)
    def pickup_loot(ctx) -> bool:
        m = ctx.find("images/ceshi1.png", threshold=0.85)
        if m:
            ctx.controller.click(m.x, m.y, spread=10)
            return True
        return False

    @bot.rule(name="pickup_loot2", priority=5, cooldown=1)
    def pickup_loot2(ctx) -> bool:
        m = ctx.find("images/xueping.png", threshold=0.85)
        if m:
            ctx.controller.click(m.x, m.y, spread=10)
            return True
        return False

    @bot.rule(name="advance", priority=0, cooldown=0.0)
    def advance(ctx) -> bool:
        # Fallback behavior when nothing else matched.
        ctx.controller.press("enter")
        return True

    return bot


def main() -> None:
    bot = build_bot()
    print("Bot running. Press Ctrl+C to stop. BOOT button on the board = HID panic.")
    bot.run()


if __name__ == "__main__":
    main()
