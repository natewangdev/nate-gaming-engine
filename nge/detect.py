"""YOLO object detection via ONNXRuntime.

Runs an Ultralytics YOLO model exported to ONNX (``yolo export format=onnx``)
without a PyTorch dependency -- it reuses the same ``onnxruntime`` already pulled
in by the OCR stack. Supports Detect-head models (YOLOv8 / YOLO11 / similar)
whose ONNX output is ``[1, 4 + num_classes, num_anchors]``.

As with OCR, pass a ``region`` to recognize only the part of the frame you care
about; result coordinates are mapped back to the source-image space so they can
be fed straight to ``Controller.click``.

Note: pretrained COCO models only know generic classes. To detect game-specific
objects (monsters, loot, markers) you must label a dataset and train a custom
model with Ultralytics, then export it to ONNX.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

import cv2
import numpy as np

from .logger import get_logger

log = get_logger(__name__)

Region = tuple[int, int, int, int]  # (left, top, right, bottom)


@dataclass
class Detection:
    """A single detected object (coordinates in source-image space)."""

    label: str
    class_id: int
    conf: float
    x: int  # center x
    y: int  # center y
    box: tuple[int, int, int, int]  # (x1, y1, x2, y2)


def _crop(image: np.ndarray, region: Region | None) -> tuple[np.ndarray, tuple[int, int]]:
    if region is None:
        return image, (0, 0)
    l, t, r, b = region
    h, w = image.shape[:2]
    l, t = max(0, min(int(l), w)), max(0, min(int(t), h))
    r, b = max(0, min(int(r), w)), max(0, min(int(b), h))
    return image[t:b, l:r], (l, t)


def _letterbox(img: np.ndarray, size: int, color: int = 114):
    """Resize keeping aspect ratio and pad to a square; return (canvas, r, dw, dh)."""
    h, w = img.shape[:2]
    r = min(size / h, size / w)
    nw, nh = int(round(w * r)), int(round(h * r))
    resized = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LINEAR)
    canvas = np.full((size, size, 3), color, dtype=np.uint8)
    dw, dh = (size - nw) // 2, (size - nh) // 2
    canvas[dh:dh + nh, dw:dw + nw] = resized
    return canvas, r, dw, dh


@dataclass
class YoloDetector:
    """ONNXRuntime YOLO detector.

    Parameters
    ----------
    model_path:
        Path to a YOLO ``.onnx`` model.
    input_size:
        Square model input size; auto-read from the model when fixed, else 640.
    providers:
        ONNXRuntime execution providers; defaults to CUDA then CPU.
    names:
        Optional class-id -> name mapping; auto-read from model metadata if absent.
    """

    model_path: str
    input_size: int = 0
    providers: list[str] | None = None
    names: dict[int, str] = field(default_factory=dict)
    _session: object | None = None

    def _ensure(self) -> None:
        if self._session is not None:
            return
        import onnxruntime as ort

        providers = self.providers or ["CUDAExecutionProvider", "CPUExecutionProvider"]
        # Drop providers that aren't available to avoid a hard failure.
        available = set(ort.get_available_providers())
        providers = [p for p in providers if p in available] or ["CPUExecutionProvider"]
        self._session = ort.InferenceSession(self.model_path, providers=providers)

        inp = self._session.get_inputs()[0]
        self._input_name = inp.name
        if not self.input_size:
            dims = inp.shape  # e.g. [1, 3, 640, 640]
            self.input_size = dims[2] if isinstance(dims[2], int) else 640

        if not self.names:
            self.names = self._read_names()
        log.info(
            "YOLO detector ready (%s, input=%d, %d classes)",
            providers[0], self.input_size, len(self.names),
        )

    def _read_names(self) -> dict[int, str]:
        try:
            meta = self._session.get_modelmeta().custom_metadata_map  # type: ignore[union-attr]
            raw = meta.get("names")
            if raw:
                parsed = ast.literal_eval(raw)
                return {int(k): str(v) for k, v in parsed.items()}
        except Exception:  # noqa: BLE001
            pass
        return {}

    def _name(self, class_id: int) -> str:
        return self.names.get(class_id, str(class_id))

    def detect(
        self,
        image: np.ndarray,
        region: Region | None = None,
        conf: float = 0.5,
        iou: float = 0.45,
        classes: list[str | int] | None = None,
    ) -> list[Detection]:
        """Detect objects in ``image`` (optionally limited to ``region``)."""
        self._ensure()
        crop, (ox, oy) = _crop(image, region)
        if crop.size == 0:
            return []

        size = self.input_size
        canvas, r, dw, dh = _letterbox(crop, size)
        blob = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        blob = np.transpose(blob, (2, 0, 1))[None]  # NCHW

        out = self._session.run(None, {self._input_name: blob})[0]  # type: ignore[union-attr]
        preds = out[0] if out.ndim == 3 else out
        if preds.shape[0] < preds.shape[1]:  # (features, anchors) -> (anchors, features)
            preds = preds.T

        boxes_xywh = preds[:, :4]
        cls_scores = preds[:, 4:]
        class_ids = np.argmax(cls_scores, axis=1)
        confs = cls_scores[np.arange(len(cls_scores)), class_ids]

        keep = confs >= conf
        boxes_xywh, class_ids, confs = boxes_xywh[keep], class_ids[keep], confs[keep]
        if len(boxes_xywh) == 0:
            return []

        # xywh(center, letterbox space) -> xyxy in crop space.
        cx, cy, bw, bh = boxes_xywh.T
        x1 = (cx - bw / 2 - dw) / r
        y1 = (cy - bh / 2 - dh) / r
        x2 = (cx + bw / 2 - dw) / r
        y2 = (cy + bh / 2 - dh) / r

        nms_boxes = np.stack([x1, y1, x2 - x1, y2 - y1], axis=1).tolist()
        idxs = cv2.dnn.NMSBoxes(nms_boxes, confs.tolist(), conf, iou)
        if len(idxs) == 0:
            return []
        idxs = np.array(idxs).flatten()

        ch, cw = crop.shape[:2]
        wanted = set(classes) if classes else None
        results: list[Detection] = []
        for i in idxs:
            cid = int(class_ids[i])
            label = self._name(cid)
            if wanted is not None and label not in wanted and cid not in wanted:
                continue
            bx1 = int(max(0, min(x1[i], cw))) + ox
            by1 = int(max(0, min(y1[i], ch))) + oy
            bx2 = int(max(0, min(x2[i], cw))) + ox
            by2 = int(max(0, min(y2[i], ch))) + oy
            results.append(
                Detection(
                    label=label,
                    class_id=cid,
                    conf=float(confs[i]),
                    x=(bx1 + bx2) // 2,
                    y=(by1 + by2) // 2,
                    box=(bx1, by1, bx2, by2),
                )
            )
        results.sort(key=lambda d: d.conf, reverse=True)
        return results

    def find(
        self,
        image: np.ndarray,
        label: str,
        region: Region | None = None,
        conf: float = 0.5,
    ) -> Detection | None:
        """Return the highest-confidence detection matching ``label``."""
        dets = self.detect(image, region=region, conf=conf, classes=[label])
        return dets[0] if dets else None
