"""Decision loop: capture -> recognize -> act.

A small rule engine that ties :class:`~nge.capture.ScreenCapture`,
:mod:`nge.vision` template matching, and :class:`~nge.controller.Controller`
into a repeating loop. Rules are evaluated each tick in priority order; the
first rule that acts can optionally stop further evaluation for that tick.

Anti-detection helpers are built in: randomized tick timing, periodic human-like
breaks, and a maximum session duration. Press Ctrl+C to stop; the controller is
always released on exit.

Example
-------
    import nge
    from nge.bot import Bot
    from nge.capture import ScreenCapture

    bot = Bot(nge.connect(), ScreenCapture())

    @bot.rule(name="pickup", cooldown=0.5, priority=10)
    def pickup(ctx):
        m = ctx.find("templates/loot.png", threshold=0.85)
        if m:
            ctx.controller.click(m.x, m.y)
            return True
        return False

    bot.run()
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from .capture import ScreenCapture
from .controller import Controller
from .detect import Detection, YoloDetector
from .logger import get_logger
from .ocr import OCREngine, OCRResult, Region
from .vision import Match, find_all, find_template, find_template_in_region, load_template

log = get_logger(__name__)

# A rule receives the context and returns True if it performed an action.
RuleFn = Callable[["BotContext"], bool]


@dataclass
class BotConfig:
    """Loop timing and anti-detection tunables."""

    # Target loop rate. The actual interval is randomized by ``tick_jitter``.
    tick_hz: float = 8.0
    # Per-tick interval randomization (+/- this fraction of the base interval).
    tick_jitter: float = 0.35
    # Stop a rule sweep after the first acting rule (True) or run them all.
    one_action_per_tick: bool = True
    # End the session after this many seconds (0 = unlimited).
    session_max_seconds: float = 0.0
    # Periodic human-like breaks: take a break roughly every
    # ``break_every`` seconds (0 = never), lasting a random time in
    # ``break_duration`` (min, max). Timing is randomized around break_every.
    break_every: float = 0.0
    break_duration: tuple[float, float] = (4.0, 12.0)


@dataclass
class BotContext:
    """Per-tick context passed to rules."""

    controller: Controller
    capture: ScreenCapture
    frame: np.ndarray | None = None
    now: float = 0.0
    tick: int = 0
    # Free-form shared state for rules to coordinate.
    state: dict = field(default_factory=dict)
    # Internal: loaded-template cache, region offset, asset root, OCR engine.
    _templates: dict[str, np.ndarray] = field(default_factory=dict)
    _region_offset: tuple[int, int] = (0, 0)
    _asset_root: Path | None = None
    _ocr: OCREngine | None = None
    _detector: YoloDetector | None = None

    def refresh_frame(self) -> bool:
        """Grab a new screenshot and replace :attr:`frame`.

        Call after in-rule actions (clicks, key presses, delays) that change
        the screen before another detect/find pass in the same tick.
        """
        frame = self.capture.grab()
        if frame is None:
            return False
        self.frame = frame
        return True

    def find(self, template_path: str, threshold: float = 0.85) -> Match | None:
        """Find the best match of a template in the current frame."""
        tpl = self._template(template_path)
        return find_template(self.frame, tpl, threshold, self._region_offset)

    def find_all(self, template_path: str, threshold: float = 0.85) -> list[Match]:
        """Find all matches of a template in the current frame."""
        tpl = self._template(template_path)
        return find_all(self.frame, tpl, threshold, self._region_offset)

    def find_in_region(
        self,
        template_path: str,
        region: tuple[int, int, int, int],
        threshold: float = 0.85,
    ) -> Match | None:
        """Find the best template match inside ``region`` (screen-space l, t, r, b)."""
        if self.frame is None:
            return None
        tpl = self._template(template_path)
        return find_template_in_region(
            self.frame, tpl, region, threshold, frame_offset=self._region_offset
        )

    @property
    def ocr(self) -> OCREngine:
        """Lazily-created shared OCR engine."""
        if self._ocr is None:
            self._ocr = OCREngine()
        return self._ocr

    def read_text(self, region: Region | None = None, min_score: float = 0.5) -> str:
        """OCR the current frame (optionally a region) into a single string."""
        return self.ocr.read_text(self.frame, region, min_score=min_score)

    def read_number(
        self, region: Region | None = None, min_score: float = 0.5
    ) -> float | int | None:
        """OCR the first number in the frame/region (e.g. HP, gold)."""
        return self.ocr.read_number(self.frame, region, min_score=min_score)

    def read_numbers(
        self, region: Region | None = None, min_score: float = 0.5
    ) -> list[float | int]:
        """OCR all numbers in the frame/region."""
        return self.ocr.read_numbers(self.frame, region, min_score=min_score)

    def find_text(
        self,
        target: str,
        region: Region | None = None,
        contains: bool = True,
        min_score: float = 0.5,
    ) -> OCRResult | None:
        """Find ``target`` text in the frame; coords mapped to screen space."""
        res = self.ocr.find_text(self.frame, target, region, contains, min_score)
        if res is not None and self._region_offset != (0, 0):
            ox, oy = self._region_offset
            res.x += ox
            res.y += oy
            res.box = [(px + ox, py + oy) for px, py in res.box]
        return res

    @property
    def detector(self) -> YoloDetector:
        """The YOLO detector (must be configured via ``Bot(yolo_model=...)``)."""
        if self._detector is None:
            raise RuntimeError(
                "No YOLO model configured. Pass yolo_model=... to Bot(...)."
            )
        return self._detector

    def detect(
        self,
        region: Region | None = None,
        conf: float = 0.5,
        classes: list[str | int] | None = None,
    ) -> list[Detection]:
        """Detect objects in the current frame; coords mapped to screen space."""
        dets = self.detector.detect(self.frame, region=region, conf=conf, classes=classes)
        return [self._shift_detection(d) for d in dets]

    def find_object(
        self,
        label: str,
        region: Region | None = None,
        conf: float = 0.5,
    ) -> Detection | None:
        """Find the best detection matching ``label`` (coords in screen space)."""
        d = self.detector.find(self.frame, label, region=region, conf=conf)
        return self._shift_detection(d) if d is not None else None

    def _shift_detection(self, d: Detection) -> Detection:
        ox, oy = self._region_offset
        if (ox, oy) == (0, 0):
            return d
        x1, y1, x2, y2 = d.box
        d.box = (x1 + ox, y1 + oy, x2 + ox, y2 + oy)
        d.x += ox
        d.y += oy
        return d

    def asset_path(self, path: str) -> str:
        """Resolve ``path`` against the asset root (if set and path is relative)."""
        p = Path(path)
        if self._asset_root is not None and not p.is_absolute():
            p = self._asset_root / p
        return str(p)

    def _template(self, path: str) -> np.ndarray:
        tpl = self._templates.get(path)
        if tpl is None:
            tpl = load_template(self.asset_path(path))
            self._templates[path] = tpl
        return tpl


@dataclass(order=True)
class Rule:
    """A named condition/action with priority and cooldown."""

    priority: int
    name: str = field(compare=False)
    fn: RuleFn = field(compare=False)
    cooldown: float = field(default=0.0, compare=False)
    _last_fired: float = field(default=0.0, compare=False)

    def ready(self, now: float) -> bool:
        return (now - self._last_fired) >= self.cooldown


class Bot:
    """Capture/recognize/act loop with a priority rule list."""

    def __init__(
        self,
        controller: Controller,
        capture: ScreenCapture,
        config: BotConfig | None = None,
        rng: random.Random | None = None,
        asset_root: str | Path | None = None,
        yolo_model: str | Path | None = None,
    ) -> None:
        self.controller = controller
        self.capture = capture
        self.config = config or BotConfig()
        self.rng = rng or random.Random()
        self.rules: list[Rule] = []
        self._stop = False
        # Base directory for resolving relative resource paths (e.g. templates).
        self.asset_root = Path(asset_root) if asset_root is not None else None

        detector = YoloDetector(str(yolo_model)) if yolo_model is not None else None

        offset = (0, 0)
        if capture.region is not None:
            offset = (capture.region[0], capture.region[1])
        self.ctx = BotContext(
            controller=controller,
            capture=capture,
            _region_offset=offset,
            _asset_root=self.asset_root,
            _detector=detector,
        )

    # ----------------------------------------------------------- rule setup
    def add_rule(
        self,
        fn: RuleFn,
        name: str | None = None,
        priority: int = 0,
        cooldown: float = 0.0,
    ) -> Rule:
        """Register a rule function. Higher ``priority`` runs first."""
        rule = Rule(
            priority=-priority,  # dataclass order is ascending; invert for "high first"
            name=name or getattr(fn, "__name__", "rule"),
            fn=fn,
            cooldown=cooldown,
        )
        self.rules.append(rule)
        self.rules.sort()
        return rule

    def rule(
        self,
        name: str | None = None,
        priority: int = 0,
        cooldown: float = 0.0,
    ) -> Callable[[RuleFn], RuleFn]:
        """Decorator form of :meth:`add_rule`."""

        def deco(fn: RuleFn) -> RuleFn:
            self.add_rule(fn, name=name, priority=priority, cooldown=cooldown)
            return fn

        return deco

    # ---------------------------------------------------------------- loop
    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        """Run the decision loop until stopped, the session ends, or Ctrl+C."""
        cfg = self.config
        base_interval = 1.0 / cfg.tick_hz if cfg.tick_hz > 0 else 0.0
        start = time.time()
        next_break = (
            start + self._jittered(cfg.break_every) if cfg.break_every > 0 else None
        )
        self._stop = False
        log.info("Bot started with %d rule(s)", len(self.rules))

        try:
            while not self._stop:
                now = time.time()
                if cfg.session_max_seconds and (now - start) >= cfg.session_max_seconds:
                    log.info("Session time limit reached")
                    break

                if next_break is not None and now >= next_break:
                    self._take_break()
                    next_break = time.time() + self._jittered(cfg.break_every)
                    continue

                self._tick(now)

                # Randomized inter-tick delay.
                sleep = base_interval * (
                    1.0 + self.rng.uniform(-cfg.tick_jitter, cfg.tick_jitter)
                )
                if sleep > 0:
                    time.sleep(sleep)
        except KeyboardInterrupt:
            log.info("Interrupted by user")
        finally:
            try:
                self.controller.stop()
            except Exception:  # noqa: BLE001
                pass
            # Explicitly release the capture backend to avoid COM teardown
            # access violations from dxcam/comtypes at interpreter shutdown.
            try:
                self.capture.close()
            except Exception:  # noqa: BLE001
                pass
            log.info("Bot stopped after %.1fs", time.time() - start)

    # ------------------------------------------------------------- internal
    def _tick(self, now: float) -> None:
        frame = self.capture.grab()
        if frame is None:
            return
        self.ctx.frame = frame
        self.ctx.now = now
        self.ctx.tick += 1

        for rule in self.rules:
            if not rule.ready(now):
                continue
            try:
                acted = bool(rule.fn(self.ctx))
            except Exception as exc:  # noqa: BLE001 - one bad rule shouldn't kill the loop
                log.exception("Rule %r raised: %s", rule.name, exc)
                continue
            if acted:
                rule._last_fired = now
                log.debug("Rule %r acted", rule.name)
                if self.config.one_action_per_tick:
                    break

    def _take_break(self) -> None:
        lo, hi = self.config.break_duration
        dur = self.rng.uniform(lo, hi)
        log.info("Taking a break for %.1fs", dur)
        # Release inputs during the break so nothing stays held down.
        try:
            self.controller.stop()
        except Exception:  # noqa: BLE001
            pass
        time.sleep(dur)

    def _jittered(self, base: float) -> float:
        return base * self.rng.uniform(0.7, 1.3)
