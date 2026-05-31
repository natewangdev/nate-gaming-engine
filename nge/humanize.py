"""Human-like pointer path generation.

Produces a sequence of timed waypoints between two screen points using a cubic
Bezier curve with randomized control points, ease-in/ease-out timing, small
positional jitter, an optional overshoot near the target, a configurable peak
speed limit (so the pointer never moves faster than a human hand could), and
occasional hesitation pauses. The controller streams these waypoints to the
firmware as absolute moves.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass


@dataclass
class Waypoint:
    """A single point on the path with the delay to wait before sending it."""

    x: float
    y: float
    delay: float  # seconds to sleep before emitting this waypoint


@dataclass
class HumanizeConfig:
    """Tunables for the path generator."""

    # Curve shape: how far control points may deviate from the straight line,
    # as a fraction of the path length.
    curvature: float = 0.1
    # Per-waypoint positional jitter in pixels.
    jitter: float = 0.4
    # Chance to overshoot the target and correct back.
    overshoot_chance: float = 0.15
    overshoot_pixels: float = 14.0
    # Report cadence: target waypoints per second (clamped by duration).
    rate_hz: float = 144.0
    # Random multiplier applied to each inter-waypoint delay (+/- this fraction).
    timing_noise: float = 0.35
    # Maximum pointer speed in pixels/second. Any segment that would move faster
    # than this gets its delay stretched, capping the peak speed to a humanly
    # reachable value. Set to 0 to disable the limit.
    max_speed: float = 9000.0
    # Chance, per intermediate waypoint, to insert a brief hesitation pause.
    pause_chance: float = 0.00
    # Hesitation pause duration range in seconds (min, max).
    pause_range: tuple[float, float] = (0.04, 0.14)


def _ease_in_out(t: float) -> float:
    """Smoothstep-style easing: slow start, fast middle, slow end."""
    return t * t * (3.0 - 2.0 * t)


def _cubic_bezier(p0, p1, p2, p3, t: float) -> tuple[float, float]:
    u = 1.0 - t
    a = u * u * u
    b = 3 * u * u * t
    c = 3 * u * t * t
    d = t * t * t
    x = a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0]
    y = a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1]
    return x, y


def _control_point(p0, p3, frac: float, spread: float, rng: random.Random):
    """A control point along the line p0->p3, pushed sideways by ``spread``."""
    bx = p0[0] + (p3[0] - p0[0]) * frac
    by = p0[1] + (p3[1] - p0[1]) * frac
    dx, dy = p3[0] - p0[0], p3[1] - p0[1]
    length = math.hypot(dx, dy) or 1.0
    # Perpendicular unit vector.
    nx, ny = -dy / length, dx / length
    offset = rng.uniform(-spread, spread)
    return bx + nx * offset, by + ny * offset


def _limit_speed(
    waypoints: list["Waypoint"],
    origin: tuple[float, float],
    max_speed: float,
) -> None:
    """Stretch delays in place so no segment exceeds ``max_speed`` (px/s)."""
    if max_speed <= 0:
        return
    prev = origin
    for wp in waypoints:
        seg = math.hypot(wp.x - prev[0], wp.y - prev[1])
        min_delay = seg / max_speed
        if wp.delay < min_delay:
            wp.delay = min_delay
        prev = (wp.x, wp.y)


def _apply_pauses(
    waypoints: list["Waypoint"],
    cfg: "HumanizeConfig",
    rng: random.Random,
) -> None:
    """Randomly add hesitation pauses before some intermediate waypoints."""
    if cfg.pause_chance <= 0:
        return
    for wp in waypoints[:-1]:
        if rng.random() < cfg.pause_chance:
            wp.delay += rng.uniform(*cfg.pause_range)


def generate_path(
    start: tuple[float, float],
    end: tuple[float, float],
    duration: float | None = None,
    config: HumanizeConfig | None = None,
    rng: random.Random | None = None,
) -> list[Waypoint]:
    """Generate a human-like path of timed waypoints from ``start`` to ``end``.

    Parameters
    ----------
    start, end:
        Pixel coordinates.
    duration:
        Total travel time in seconds. If ``None``, it scales with distance
        (Fitts'-law-ish): longer moves take longer.
    """
    cfg = config or HumanizeConfig()
    rng = rng or random.Random()

    dist = math.hypot(end[0] - start[0], end[1] - start[1])
    if dist < 1e-3:
        return [Waypoint(end[0], end[1], 0.0)]

    if duration is None:
        # Base 90ms plus distance-dependent term, with a little randomness.
        duration = 0.09 + dist / 2600.0
        duration *= rng.uniform(0.85, 1.20)

    spread = cfg.curvature * dist
    c1 = _control_point(start, end, rng.uniform(0.25, 0.45), spread, rng)
    c2 = _control_point(start, end, rng.uniform(0.55, 0.75), spread, rng)

    # Optional overshoot: aim slightly past the target, then settle back.
    target = end
    overshoot = None
    if rng.random() < cfg.overshoot_chance:
        ang = math.atan2(end[1] - start[1], end[0] - start[0])
        over = rng.uniform(0.4, 1.0) * cfg.overshoot_pixels
        overshoot = (end[0] + math.cos(ang) * over, end[1] + math.sin(ang) * over)
        target = overshoot

    steps = max(2, int(duration * cfg.rate_hz))
    base_delay = duration / steps

    waypoints: list[Waypoint] = []
    for i in range(1, steps + 1):
        t = _ease_in_out(i / steps)
        x, y = _cubic_bezier(start, c1, c2, target, t)
        if cfg.jitter and i != steps:
            x += rng.uniform(-cfg.jitter, cfg.jitter)
            y += rng.uniform(-cfg.jitter, cfg.jitter)
        noise = 1.0 + rng.uniform(-cfg.timing_noise, cfg.timing_noise)
        waypoints.append(Waypoint(x, y, base_delay * noise))

    # If we overshot, add a short corrective segment back to the true target.
    if overshoot is not None:
        correct_steps = rng.randint(3, 6)
        for i in range(1, correct_steps + 1):
            t = _ease_in_out(i / correct_steps)
            x = overshoot[0] + (end[0] - overshoot[0]) * t
            y = overshoot[1] + (end[1] - overshoot[1]) * t
            waypoints.append(Waypoint(x, y, base_delay * rng.uniform(0.8, 1.3)))

    # Cap peak speed to a humanly reachable value, then sprinkle in hesitations.
    _limit_speed(waypoints, start, cfg.max_speed)
    _apply_pauses(waypoints, cfg, rng)

    return waypoints
