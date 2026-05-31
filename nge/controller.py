"""High-level input controller.

Bridges pixel-space intent (``move_to``, ``click``, ``press``) to the firmware's
absolute HID protocol. Pixel coordinates are mapped to the 0..32767 device
range, and pointer motion is humanized via :mod:`nge.humanize`.

Coordinate mapping note
-----------------------
HID absolute coordinates map across a single display. By default the primary
monitor size (Windows ``GetSystemMetrics``) is used. For a different target,
pass ``screen_size=(width, height)`` explicitly.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field

from .humanize import HumanizeConfig, generate_path
from .keymap import resolve_key, resolve_modifiers
from .logger import get_logger
from .transport import SerialTransport

log = get_logger(__name__)

HID_MAX = 32767


def _primary_screen_size() -> tuple[int, int]:
    """Best-effort primary monitor size (Windows). Falls back to 1920x1080."""
    try:
        import ctypes

        user32 = ctypes.windll.user32
        user32.SetProcessDPIAware()
        return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    except Exception:  # noqa: BLE001 - non-Windows or unavailable
        return 1920, 1080


@dataclass
class Controller:
    """High-level mouse/keyboard controller backed by the HID firmware."""

    transport: SerialTransport
    screen_size: tuple[int, int] | None = None
    humanize: HumanizeConfig = field(default_factory=HumanizeConfig)
    rng: random.Random = field(default_factory=random.Random)
    # Tracked pointer position in pixels (device has no position query).
    _pos: tuple[float, float] = (0.0, 0.0)

    def __post_init__(self) -> None:
        if self.screen_size is None:
            self.screen_size = _primary_screen_size()
        w, h = self.screen_size
        self._pos = (w / 2.0, h / 2.0)
        log.info("Controller screen size = %dx%d", w, h)

    # ---- coordinate mapping ----
    def _to_device(self, px: float, py: float) -> tuple[int, int]:
        w, h = self.screen_size
        x = int(round(_clamp(px, 0, w - 1) * HID_MAX / max(1, w - 1)))
        y = int(round(_clamp(py, 0, h - 1) * HID_MAX / max(1, h - 1)))
        return x, y

    def _emit(self, px: float, py: float) -> None:
        dx, dy = self._to_device(px, py)
        self.transport.command(f"MA {dx} {dy}")
        self._pos = (px, py)

    # ---- mouse ----
    def move_to(
        self,
        px: float,
        py: float,
        duration: float | None = None,
    ) -> None:
        """Move the pointer to a pixel coordinate along a human-like path."""
        path = generate_path(
            self._pos, (px, py), duration=duration, config=self.humanize, rng=self.rng
        )
        for wp in path:
            if wp.delay > 0:
                time.sleep(wp.delay)
            self._emit(wp.x, wp.y)

    def move_instant(self, px: float, py: float) -> None:
        """Jump the pointer directly to a coordinate (no humanization)."""
        self._emit(px, py)

    def click(
        self,
        px: float | None = None,
        py: float | None = None,
        button: str = "L",
        hold: float | None = None,
        duration: float | None = None,
    ) -> None:
        """Optionally move to (px, py), then click ``button``.

        ``hold`` is the press duration in seconds; if ``None`` a small randomized
        human-like delay is used.
        """
        if px is not None and py is not None:
            self.move_to(px, py, duration=duration)
            time.sleep(self.rng.uniform(0.02, 0.06))
        hold_ms = (
            int(hold * 1000)
            if hold is not None
            else self.rng.randint(45, 110)
        )
        self.transport.command(f"CLK {button.upper()} {hold_ms}")

    def mouse_down(self, button: str = "L") -> None:
        self.transport.command(f"BTN {button.upper()} 1")

    def mouse_up(self, button: str = "L") -> None:
        self.transport.command(f"BTN {button.upper()} 0")

    def wheel(self, delta: int) -> None:
        self.transport.command(f"WHEEL {int(_clamp(delta, -127, 127))}")

    # ---- keyboard ----
    def set_modifiers(self, *names: str) -> None:
        self.transport.command(f"MOD {resolve_modifiers(*names)}")

    def clear_modifiers(self) -> None:
        self.transport.command("MOD 0")

    def key_down(self, key: str) -> None:
        self.transport.command(f"KD {resolve_key(key)}")

    def key_up(self, key: str) -> None:
        self.transport.command(f"KU {resolve_key(key)}")

    def press(self, key: str, hold: float | None = None) -> None:
        """Press and release a key. ``hold`` is the press duration in seconds."""
        hold_ms = (
            int(hold * 1000) if hold is not None else self.rng.randint(40, 90)
        )
        self.transport.command(f"KP {resolve_key(key)} {hold_ms}")

    def hotkey(self, *keys: str, hold: float | None = None) -> None:
        """Press a combination like ``hotkey('ctrl', 'c')``.

        Leading entries that are modifier names are applied as the modifier
        byte; the final entry is the main key.
        """
        *mods, main = keys
        if mods:
            self.set_modifiers(*mods)
        try:
            self.press(main, hold=hold)
        finally:
            if mods:
                self.clear_modifiers()

    def stop(self) -> None:
        """Release everything (panic)."""
        self.transport.command("STOP")


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else (hi if v > hi else v)
