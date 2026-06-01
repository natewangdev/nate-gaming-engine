"""YOLO detection example (ONNX + onnxruntime, no PyTorch).

Prepare a model first with Ultralytics, then export to ONNX:

    pip install ultralytics
    yolo export model=yolo11n.pt format=onnx        # or your trained best.pt

Place the resulting .onnx somewhere and point MODEL_PATH at it. A pretrained
COCO model only knows generic classes; train your own for game objects.

Run:
    python -m examples.detect_example
"""

from __future__ import annotations

from pathlib import Path

from nge.capture import ScreenCapture
from nge.detect import YoloDetector

MODEL_PATH = Path(r"C:\Users\Admin\Desktop\d4\models\best.onnx")


def main() -> None:
    detector = YoloDetector(str(MODEL_PATH))
    capture = ScreenCapture()

    frame = capture.grab()
    h, w = frame.shape[:2]

    # Detect across the whole frame, or pass region=(l, t, r, b) to limit + speed up.
    dets = detector.detect(frame, conf=0.4)
    print(f"{len(dets)} detection(s):")
    for d in dets:
        print(f"  {d.label:<16} conf={d.conf:.2f} center=({d.x},{d.y}) box={d.box}")

    # Find a specific class (replace 'monster' with one of your trained labels).
    target = detector.find(frame, "monster", conf=0.4)
    if target:
        print(f"\nNearest 'monster' at ({target.x},{target.y})")

    capture.close()


if __name__ == "__main__":
    main()
