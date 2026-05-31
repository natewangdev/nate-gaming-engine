"""USB CDC serial transport to the ESP32-S3 HID firmware.

Wraps pyserial with a simple line protocol: each command is sent as a text line
and the firmware replies with ``OK`` / ``PONG`` / ``ERR``. The transport waits
for that acknowledgement so callers stay in sync with the device.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import serial
from serial.tools import list_ports

from .logger import get_logger

log = get_logger(__name__)

# USB VID/PID reported by ESP32-S3 in USB-OTG (TinyUSB) mode. Used only as a
# hint when auto-detecting the port; override via ``find_port`` arguments.
ESP32S3_HINTS = ("303a",)  # Espressif vendor id


class TransportError(RuntimeError):
    """Raised when the device does not acknowledge a command."""


def find_port(vid_hints: tuple[str, ...] = ESP32S3_HINTS) -> str | None:
    """Return the first serial port that looks like an ESP32-S3, or ``None``."""
    for port in list_ports.comports():
        hwid = (port.hwid or "").lower()
        if any(hint in hwid for hint in vid_hints):
            return port.device
    return None


@dataclass
class SerialTransport:
    """Synchronous, line-oriented serial transport.

    Parameters
    ----------
    port:
        Serial port (e.g. ``COM5``). If ``None``, auto-detection is attempted.
    baudrate:
        Ignored by USB CDC but required by pyserial.
    timeout:
        Read timeout (seconds) used while waiting for an acknowledgement.
    """

    port: str | None = None
    baudrate: int = 115200
    timeout: float = 0.5
    _serial: serial.Serial | None = None

    def open(self) -> "SerialTransport":
        port = self.port or find_port()
        if port is None:
            raise TransportError(
                "No ESP32-S3 serial port found. Pass port=... explicitly."
            )
        self.port = port
        self._serial = serial.Serial(port, self.baudrate, timeout=self.timeout)
        # Give the USB CDC stack a moment to settle after opening.
        time.sleep(0.2)
        self._serial.reset_input_buffer()
        log.info("Opened serial transport on %s", port)
        return self

    def close(self) -> None:
        if self._serial and self._serial.is_open:
            try:
                self.command("STOP")
            except TransportError:
                pass
            self._serial.close()
            log.info("Closed serial transport")
        self._serial = None

    def __enter__(self) -> "SerialTransport":
        return self.open()

    def __exit__(self, *exc: object) -> None:
        self.close()

    def command(self, line: str, expect: str = "OK") -> str:
        """Send one command line and wait for an acknowledgement.

        Returns the raw reply. Raises :class:`TransportError` on ``ERR``,
        timeout, or an unexpected reply.
        """
        if self._serial is None:
            raise TransportError("Transport is not open")

        payload = (line.strip() + "\n").encode("ascii")
        self._serial.write(payload)
        self._serial.flush()

        reply = self._serial.readline().decode("ascii", errors="replace").strip()
        if not reply:
            raise TransportError(f"Timed out waiting for reply to {line!r}")
        if reply == "ERR":
            raise TransportError(f"Device rejected command {line!r}")
        if expect and reply != expect:
            raise TransportError(
                f"Unexpected reply to {line!r}: got {reply!r}, want {expect!r}"
            )
        return reply

    def ping(self) -> bool:
        """Return ``True`` if the device answers PING with PONG."""
        try:
            return self.command("PING", expect="PONG") == "PONG"
        except TransportError:
            return False
