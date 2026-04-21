"""EasyOCR wrapper for text extraction from UI element crops."""

import easyocr
import numpy as np

from yolo.config import OCR_CROP_PADDING

_reader = None


def get_reader():
    """Get or create a cached EasyOCR reader instance."""
    global _reader
    if _reader is None:
        _reader = easyocr.Reader(["en"], gpu=False, verbose=False)
    return _reader


def extract_text(image, bbox):
    """Extract text from a bounding box region of an image.

    Args:
        image: numpy array (BGR, as loaded by OpenCV)
        bbox: [x1, y1, x2, y2] in pixels

    Returns:
        Extracted text string, or empty string if none found.
    """
    x1, y1, x2, y2 = bbox
    h, w = image.shape[:2]
    # Pad the crop for better OCR accuracy at edges
    pad = OCR_CROP_PADDING
    x1 = max(0, x1 - pad)
    y1 = max(0, y1 - pad)
    x2 = min(w, x2 + pad)
    y2 = min(h, y2 + pad)

    crop = image[y1:y2, x1:x2]
    if crop.size == 0:
        return ""

    reader = get_reader()
    results = reader.readtext(crop, detail=0, paragraph=True)
    text = " ".join(results).strip() if results else ""
    return text


def extract_texts_batch(image, bboxes):
    """Extract text from multiple bounding boxes.

    Returns list of text strings in same order as bboxes.
    """
    return [extract_text(image, bbox) for bbox in bboxes]
