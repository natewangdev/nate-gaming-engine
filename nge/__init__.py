"""nge - Nate Gaming Engine.

A hardware-HID automation toolkit. Input is delivered through an ESP32-S3 acting
as a real USB keyboard/mouse, driven over a USB CDC serial link; screen sensing
is done passively via desktop duplication. See ``firmware/nge_hid`` for the
device firmware.
"""

from __future__ import annotations

from .controller import Controller
from .humanize import HumanizeConfig, generate_path
from .transport import SerialTransport, TransportError, find_port

__all__ = [
    "Controller",
    "SerialTransport",
    "TransportError",
    "find_port",
    "HumanizeConfig",
    "generate_path",
]

__version__ = "0.1.0"


def connect(port: str | None = None, **controller_kwargs: object) -> Controller:
    """Open a serial transport and return a ready :class:`Controller`.

    Example
    -------
    >>> import nge
    >>> ctrl = nge.connect()        # auto-detect ESP32-S3
    >>> ctrl.move_to(960, 540)
    >>> ctrl.click()
    """
    transport = SerialTransport(port=port).open()
    if not transport.ping():
        transport.close()
        raise TransportError("Device did not respond to PING")
    return Controller(transport=transport, **controller_kwargs)  # type: ignore[arg-type]
