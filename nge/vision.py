"""Template matching helpers.

Thin wrappers over OpenCV's ``matchTemplate`` to locate UI elements / icons in a
captured frame and report screen-space center coordinates suitable for
:meth:`nge.controller.Controller.move_to`.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .logger import get_logger

log = get_logger(__name__)


@dataclass
class Match:
    """A single template match result."""

    x: int  # center x (screen pixels, including region offset)
    y: int  # center y
    score: float
    width: int
    height: int


def load_template(path: str) -> np.ndarray:
    """Load a template image as BGR."""
    # cv2.imread cannot open non-ASCII paths on Windows; decode from bytes instead.
    try:
        data = np.fromfile(path, dtype=np.uint8)
        img = cv2.imdecode(data, cv2.IMREAD_COLOR) if data.size else None
    except OSError:
        img = None
    if img is None:
        img = cv2.imread(path, cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(f"Template not found: {path}")
    return img


def find_template(
    frame: np.ndarray,
    template: np.ndarray,
    threshold: float = 0.85,
    region_offset: tuple[int, int] = (0, 0),
) -> Match | None:
    """Return the best match above ``threshold`` or ``None``.

    ``region_offset`` is added to results so coordinates map back to the full
    screen when ``frame`` is a cropped region.
    """
    result = cv2.matchTemplate(frame, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    if max_val < threshold:
        return None
    th, tw = template.shape[:2]
    ox, oy = region_offset
    return Match(
        x=ox + max_loc[0] + tw // 2,
        y=oy + max_loc[1] + th // 2,
        score=float(max_val),
        width=tw,
        height=th,
    )


def find_template_in_region(
    frame: np.ndarray,
    template: np.ndarray,
    region: tuple[int, int, int, int],
    threshold: float = 0.85,
    frame_offset: tuple[int, int] = (0, 0),
) -> Match | None:
    """Return the best match of ``template`` inside ``region`` (l, t, r, b).

    Coordinates are in screen space. When ``frame`` is a crop of the full
    screen, pass its top-left corner in ``frame_offset`` so results map back
    to screen coordinates.
    """
    l, t, r, b = region
    ox, oy = frame_offset
    ll, tt, rr, bb = l - ox, t - oy, r - ox, b - oy
    h, w = frame.shape[:2]
    ll, tt = max(0, min(ll, w)), max(0, min(tt, h))
    rr, bb = max(0, min(rr, w)), max(0, min(bb, h))
    if ll >= rr or tt >= bb:
        return None
    crop = frame[tt:bb, ll:rr]
    if crop.size == 0:
        return None
    return find_template(crop, template, threshold, region_offset=(l, t))


def find_all(
    frame: np.ndarray,
    template: np.ndarray,
    threshold: float = 0.85,
    region_offset: tuple[int, int] = (0, 0),
) -> list[Match]:
    """Return all non-overlapping matches above ``threshold``."""
    result = cv2.matchTemplate(frame, template, cv2.TM_CCOEFF_NORMED)
    th, tw = template.shape[:2]
    ox, oy = region_offset

    matches: list[Match] = []
    ys, xs = np.where(result >= threshold)
    for y, x in zip(ys.tolist(), xs.tolist()):
        cx, cy = ox + x + tw // 2, oy + y + th // 2
        # Suppress near-duplicate detections.
        if any(abs(cx - m.x) < tw // 2 and abs(cy - m.y) < th // 2 for m in matches):
            continue
        matches.append(Match(cx, cy, float(result[y, x]), tw, th))
    matches.sort(key=lambda m: m.score, reverse=True)
    return matches
