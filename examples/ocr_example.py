"""OCR example: recognize text in a screen region.

Demonstrates region-limited recognition (fast) with RapidOCR via nge.ocr.

Prerequisites:
  pip install rapidocr-onnxruntime
  pip install -r requirements.txt   (for the capture backend)

Run:
  python -m examples.ocr_example
"""

from __future__ import annotations

from nge.capture import ScreenCapture
from nge.ocr import OCREngine


def main() -> None:
    capture = ScreenCapture()
    ocr = OCREngine()

    frame = capture.grab()
    h, w = frame.shape[:2]

    # Recognize only a region to keep it fast. Adjust to where your text is;
    # here we read the top-center strip of the screen as an example.
    region = (int(w * 0.30), 0, int(w * 0.70), int(h * 0.12))

    print("Full-region text:")
    print(ocr.read_text(frame, region=region))

    print("\nPer-line results (text @ center, score):")
    for r in ocr.read(frame, region=region):
        print(f"  {r.text!r} @ ({r.x},{r.y}) score={r.score:.2f}")

    # Locate a specific word and (for example) where to click it.
    hit = ocr.find_text(frame, "确定", region=region)
    if hit:
        print(f"\nFound '确定' at ({hit.x},{hit.y})")
    else:
        print("\n'确定' not found in region")

    capture.close()


if __name__ == "__main__":
    main()
