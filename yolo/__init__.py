"""YOLO UI element detection — public API."""

import os

import cv2
from pathlib import Path

from yolo.config import MODEL_PATH
from yolo.utils.detector import load_model, detect as run_detection
from yolo.utils.ocr import extract_texts_batch
from yolo.utils.classifier import classify_all
from yolo.detect import draw_annotations

# Cached model instance (loaded once, reused across steps)
_model = None


def _get_model():
    global _model
    if _model is None:
        model_path = os.path.join(os.path.dirname(__file__), MODEL_PATH)
        _model = load_model(model_path)
    return _model


def detect_elements(
    image_path: str,
    screen_width_points: int,
    screen_height_points: int,
    annotated_output_path: str | None = None,
) -> tuple[list[dict], str | None]:
    """Detect UI elements and return structured data + annotated image.

    Args:
        image_path: Path to screenshot (pixel resolution).
        screen_width_points: Screen width in logical points.
        screen_height_points: Screen height in logical points.
        annotated_output_path: Where to save annotated image (optional, auto-generated if None).

    Returns:
        (elements, annotated_path) where elements is a list of:
        {
            "id": int,
            "class": str,
            "label": str,
            "confidence": float,
            "bbox_pixels": [x1, y1, x2, y2],
            "center_points": [x, y],  # logical points for pyautogui
        }
    """
    model = _get_model()
    image = cv2.imread(image_path)
    img_h, img_w = image.shape[:2]

    # Scale factor: pixel coords -> logical point coords
    scale_x = img_w / screen_width_points
    scale_y = img_h / screen_height_points

    # Detect -> OCR -> Classify
    detections = run_detection(model, image_path)
    if not detections:
        return [], None

    bboxes = [d["bbox"] for d in detections]
    texts = extract_texts_batch(image, bboxes)
    classes = classify_all(detections, texts, img_w, img_h)

    # Build elements with point coordinates
    elements = []
    for i, (det, text, cls) in enumerate(zip(detections, texts, classes), start=1):
        x1, y1, x2, y2 = det["bbox"]
        center_px = ((x1 + x2) / 2, (y1 + y2) / 2)
        elements.append({
            "id": i,
            "class": cls,
            "label": text,
            "confidence": det["confidence"],
            "bbox_pixels": det["bbox"],
            "center_points": [round(center_px[0] / scale_x), round(center_px[1] / scale_y)],
        })

    # Draw annotations and save
    annotated = draw_annotations(image, [
        {**el, "bbox": el["bbox_pixels"], "center": el["center_points"]}
        for el in elements
    ])
    if annotated_output_path is None:
        p = Path(image_path)
        annotated_output_path = str(p.parent / f"{p.stem}_annotated.png")
    cv2.imwrite(annotated_output_path, annotated)

    return elements, annotated_output_path
