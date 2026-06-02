"""Text recognition (OCR).

Backed by RapidOCR (``rapidocr-onnxruntime``), which runs the PP-OCR models on
ONNXRuntime: strong Chinese support, fast on CPU, and a light install (no
PyTorch/Paddle). The engine is created lazily on first use.

To keep recognition fast, always recognize the smallest region you need: pass a
``region`` (left, top, right, bottom) and only that crop is sent to the model.
Result coordinates are mapped back to the original image space via the crop
offset, so they remain usable as click targets.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np

from .logger import get_logger

log = get_logger(__name__)

Region = tuple[int, int, int, int]  # (left, top, right, bottom)

# Matches integers or decimals (thousands separators are stripped beforehand).
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


@dataclass
class OCRResult:
    """A single recognized text box."""

    text: str
    score: float
    x: int  # center x in the source-image space (crop offset already applied)
    y: int  # center y
    box: list[tuple[int, int]]  # 4 corner points, offset-applied


def _active_provider(engine: object) -> str | None:
    """Best-effort read of the onnxruntime provider RapidOCR is actually using.

    RapidOCR-onnxruntime wraps three models (det/cls/rec); each holds an
    ``OrtInferSession`` whose ``.session`` is the real ``InferenceSession``. The
    exact attribute names vary across versions, so probe defensively and return
    the first provider found.
    """
    for model_attr in ("text_rec", "text_det", "text_cls"):
        model = getattr(engine, model_attr, None)
        if model is None:
            continue
        ort_wrap = getattr(model, "session", None)
        sess = getattr(ort_wrap, "session", ort_wrap)
        get_providers = getattr(sess, "get_providers", None)
        if callable(get_providers):
            try:
                providers = get_providers()
            except Exception:  # noqa: BLE001
                continue
            if providers:
                return providers[0]
    return None


def _crop(image: np.ndarray, region: Region | None) -> tuple[np.ndarray, tuple[int, int]]:
    """Return (cropped_image, (offset_x, offset_y)). No-op when region is None."""
    if region is None:
        return image, (0, 0)
    l, t, r, b = region
    h, w = image.shape[:2]
    l = max(0, min(int(l), w))
    t = max(0, min(int(t), h))
    r = max(0, min(int(r), w))
    b = max(0, min(int(b), h))
    return image[t:b, l:r], (l, t)


@dataclass
class OCREngine:
    """Lazy wrapper over RapidOCR.

    Parameters
    ----------
    engine_kwargs:
        Extra keyword arguments forwarded to ``RapidOCR(...)`` (e.g. to tune the
        detection/recognition thresholds or pick models).
    """

    engine_kwargs: dict = field(default_factory=dict)
    _engine: object | None = None

    def _ensure(self) -> None:
        if self._engine is not None:
            return
        try:
            from rapidocr_onnxruntime import RapidOCR
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "RapidOCR is not installed. Run: pip install rapidocr-onnxruntime"
            ) from exc
        self._engine = RapidOCR(**self.engine_kwargs)
        provider = _active_provider(self._engine)
        mode = "GPU" if provider and provider.startswith("CUDA") else "CPU"
        log.info(
            "OCR engine ready (RapidOCR) [%s mode] (provider=%s)",
            mode, provider or "unknown",
        )

    def read(
        self,
        image: np.ndarray,
        region: Region | None = None,
        min_score: float = 0.5,
    ) -> list[OCRResult]:
        """Recognize all text in ``image`` (optionally limited to ``region``)."""
        self._ensure()
        crop, (ox, oy) = _crop(image, region)
        if crop.size == 0:
            return []
        raw, _ = self._engine(crop)  # type: ignore[operator]
        results: list[OCRResult] = []
        if not raw:
            return results
        for box, text, score in raw:
            score = float(score)
            if score < min_score:
                continue
            pts = [(int(px) + ox, int(py) + oy) for px, py in box]
            cx = sum(p[0] for p in pts) // len(pts)
            cy = sum(p[1] for p in pts) // len(pts)
            results.append(OCRResult(text=text, score=score, x=cx, y=cy, box=pts))
        return results

    def read_text(
        self,
        image: np.ndarray,
        region: Region | None = None,
        sep: str = "\n",
        min_score: float = 0.5,
    ) -> str:
        """Return all recognized text joined into a single string."""
        return sep.join(r.text for r in self.read(image, region, min_score))

    def read_numbers(
        self,
        image: np.ndarray,
        region: Region | None = None,
        min_score: float = 0.5,
    ) -> list[float | int]:
        """Return every number found in the recognized text.

        Thousands separators (``,``) are stripped. Values with a decimal point
        are returned as ``float``, otherwise as ``int``.
        """
        text = self.read_text(image, region, sep=" ", min_score=min_score)
        text = text.replace(",", "").replace("，", "")
        out: list[float | int] = []
        for m in _NUMBER_RE.findall(text):
            out.append(float(m) if "." in m else int(m))
        return out

    def read_number(
        self,
        image: np.ndarray,
        region: Region | None = None,
        min_score: float = 0.5,
    ) -> float | int | None:
        """Return the first number found, or ``None`` if there is none.

        Best used with a tight ``region`` containing a single value (HP, gold...).
        """
        nums = self.read_numbers(image, region, min_score)
        return nums[0] if nums else None

    def find_text(
        self,
        image: np.ndarray,
        target: str,
        region: Region | None = None,
        contains: bool = True,
        min_score: float = 0.5,
    ) -> OCRResult | None:
        """Return the best result matching ``target``.

        ``contains=True`` matches substrings (case-insensitive); ``False`` requires
        an exact (stripped, case-insensitive) match. Returns the highest-scoring
        match or ``None``.
        """
        needle = target.strip().lower()
        best: OCRResult | None = None
        for r in self.read(image, region, min_score):
            hay = r.text.strip().lower()
            hit = needle in hay if contains else needle == hay
            if hit and (best is None or r.score > best.score):
                best = r
        return best
