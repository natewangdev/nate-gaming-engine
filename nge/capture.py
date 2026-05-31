"""Screen capture.

Uses ``dxcam`` (fast DXGI Desktop Duplication) when available, otherwise falls
back to ``mss``. Screen reading via the desktop duplication API is a passive
operation and does not inject input or touch game memory.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .logger import get_logger

log = get_logger(__name__)


@dataclass
class ScreenCapture:
    """Grabs BGR frames of the screen or a region.

    Parameters
    ----------
    region:
        Optional ``(left, top, right, bottom)`` crop in pixels.
    """

    region: tuple[int, int, int, int] | None = None
    _backend: str = "none"
    _dxcam: object | None = None
    _mss: object | None = None

    def __post_init__(self) -> None:
        try:
            import dxcam

            self._dxcam = dxcam.create(output_color="BGR")
            self._backend = "dxcam"
        except Exception as exc:  # noqa: BLE001 - fall back to mss
            log.debug("dxcam unavailable (%s), trying mss", exc)
            try:
                import mss

                self._mss = mss.mss()
                self._backend = "mss"
            except Exception as exc2:  # noqa: BLE001
                raise RuntimeError(
                    "No capture backend available. Install 'dxcam' or 'mss'."
                ) from exc2
        log.info("Screen capture backend: %s", self._backend)

    def grab(self) -> np.ndarray:
        """Return a single frame as a BGR ``np.ndarray`` (H, W, 3)."""
        if self._backend == "dxcam":
            frame = self._dxcam.grab(region=self.region)  # type: ignore[union-attr]
            if frame is None:
                # dxcam returns None when no new frame; retry latest.
                frame = self._dxcam.grab()  # type: ignore[union-attr]
            return frame

        # mss path
        import numpy as _np

        if self.region:
            l, t, r, b = self.region
            mon = {"left": l, "top": t, "width": r - l, "height": b - t}
        else:
            mon = self._mss.monitors[1]  # type: ignore[union-attr]
        raw = self._mss.grab(mon)  # type: ignore[union-attr]
        arr = _np.asarray(raw)  # BGRA
        return arr[:, :, :3]

    def close(self) -> None:
        if self._dxcam is not None:
            try:
                self._dxcam.release()  # type: ignore[union-attr]
            except Exception:  # noqa: BLE001
                pass
        if self._mss is not None:
            try:
                self._mss.close()  # type: ignore[union-attr]
            except Exception:  # noqa: BLE001
                pass
