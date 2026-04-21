"""YOLO wrapper for UI element detection using OmniParser weights."""

import os
from ultralytics import YOLO
from yolo.config import YOLO_CONFIDENCE_THRESHOLD, YOLO_IOU_THRESHOLD, YOLO_INPUT_SIZE, MODEL_PATH


def load_model(model_path=None):
    """Load the OmniParser YOLO model."""
    path = model_path or os.path.join(os.path.dirname(__file__), "..", MODEL_PATH)
    path = os.path.normpath(path)
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Model not found at {path}. Run 'python download_models.py' first."
        )
    return YOLO(path)


def detect(model, image_path, confidence=YOLO_CONFIDENCE_THRESHOLD, iou=YOLO_IOU_THRESHOLD, imgsz=YOLO_INPUT_SIZE):
    """Run YOLO detection on an image.

    Returns list of dicts: [{"bbox": [x1, y1, x2, y2], "confidence": float}, ...]
    """
    results = model(image_path, conf=confidence, iou=iou, imgsz=imgsz, verbose=False)
    detections = []
    for result in results:
        boxes = result.boxes
        if boxes is None:
            continue
        for i in range(len(boxes)):
            x1, y1, x2, y2 = boxes.xyxy[i].tolist()
            conf = float(boxes.conf[i])
            detections.append({
                "bbox": [int(x1), int(y1), int(x2), int(y2)],
                "confidence": round(conf, 4),
            })
    return detections
