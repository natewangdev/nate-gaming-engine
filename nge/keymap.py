"""Key name to HID Usage ID mapping.

The firmware speaks raw HID Usage IDs (not ASCII). This module maps friendly
names to those codes and provides the modifier bitmask values.

Reference: USB HID Usage Tables, Keyboard/Keypad Page (0x07).
"""

from __future__ import annotations

# Modifier bitmask (matches the firmware MOD byte).
MOD_NONE = 0x00
MOD_LCTRL = 0x01
MOD_LSHIFT = 0x02
MOD_LALT = 0x04
MOD_LGUI = 0x08
MOD_RCTRL = 0x10
MOD_RSHIFT = 0x20
MOD_RALT = 0x40
MOD_RGUI = 0x80

MODIFIERS: dict[str, int] = {
    "ctrl": MOD_LCTRL,
    "lctrl": MOD_LCTRL,
    "rctrl": MOD_RCTRL,
    "shift": MOD_LSHIFT,
    "lshift": MOD_LSHIFT,
    "rshift": MOD_RSHIFT,
    "alt": MOD_LALT,
    "lalt": MOD_LALT,
    "ralt": MOD_RALT,
    "gui": MOD_LGUI,
    "win": MOD_LGUI,
    "cmd": MOD_LGUI,
}

# Friendly name -> HID Usage ID.
KEYS: dict[str, int] = {
    # Letters
    **{chr(ord("a") + i): 0x04 + i for i in range(26)},
    # Top-row digits (1-9 then 0)
    "1": 0x1E, "2": 0x1F, "3": 0x20, "4": 0x21, "5": 0x22,
    "6": 0x23, "7": 0x24, "8": 0x25, "9": 0x26, "0": 0x27,
    # Whitespace / editing
    "enter": 0x28, "return": 0x28,
    "esc": 0x29, "escape": 0x29,
    "backspace": 0x2A,
    "tab": 0x2B,
    "space": 0x2C, " ": 0x2C,
    "delete": 0x4C, "del": 0x4C,
    "insert": 0x49,
    "home": 0x4A, "end": 0x4D,
    "pageup": 0x4B, "pagedown": 0x4E,
    # Symbols
    "minus": 0x2D, "-": 0x2D,
    "equal": 0x2E, "=": 0x2E,
    "lbracket": 0x2F, "[": 0x2F,
    "rbracket": 0x30, "]": 0x30,
    "backslash": 0x31, "\\": 0x31,
    "semicolon": 0x33, ";": 0x33,
    "quote": 0x34, "'": 0x34,
    "grave": 0x35, "`": 0x35,
    "comma": 0x36, ",": 0x36,
    "period": 0x37, ".": 0x37,
    "slash": 0x38, "/": 0x38,
    "capslock": 0x39,
    # Arrows
    "right": 0x4F, "left": 0x50, "down": 0x51, "up": 0x52,
    # Function keys
    **{f"f{i}": 0x3A + (i - 1) for i in range(1, 13)},
}


def resolve_key(name: str) -> int:
    """Return the HID Usage ID for a key name.

    Accepts single characters or friendly names (case-insensitive).
    """
    key = name.lower()
    if key in KEYS:
        return KEYS[key]
    raise KeyError(f"Unknown key: {name!r}")


def resolve_modifiers(*names: str) -> int:
    """Combine modifier names into a single bitmask."""
    mask = MOD_NONE
    for name in names:
        key = name.lower()
        if key not in MODIFIERS:
            raise KeyError(f"Unknown modifier: {name!r}")
        mask |= MODIFIERS[key]
    return mask
