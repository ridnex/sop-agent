"""Heuristic classifier for UI element types."""

from yolo.config import (
    CHECKBOX_MAX_SIZE,
    ICON_MAX_SIZE,
    NAV_WIDTH_RATIO,
    NAV_EDGE_MARGIN,
    BUTTON_MIN_ASPECT,
    INPUT_MIN_ASPECT,
    INPUT_MIN_WIDTH,
    BUTTON_KEYWORDS,
    DROPDOWN_INDICATORS,
)


def classify(bbox, text, image_width, image_height):
    """Classify a detected UI element into one of 8 classes.

    Priority: navigation → checkbox → dropdown → input_field → button → link → icon → text

    Args:
        bbox: [x1, y1, x2, y2]
        text: OCR text found in the element
        image_width: full image width
        image_height: full image height

    Returns:
        Class name string.
    """
    x1, y1, x2, y2 = bbox
    w = x2 - x1
    h = y2 - y1
    aspect = w / max(h, 1)
    text_lower = text.lower().strip()

    # 1. Navigation: full-width (or near), at top or bottom of image
    if w >= image_width * NAV_WIDTH_RATIO:
        at_top = y1 < image_height * NAV_EDGE_MARGIN
        at_bottom = y2 > image_height * (1 - NAV_EDGE_MARGIN)
        if at_top or at_bottom:
            return "navigation"

    # 2. Checkbox: very small and roughly square
    if w <= CHECKBOX_MAX_SIZE and h <= CHECKBOX_MAX_SIZE and 0.5 <= aspect <= 2.0:
        return "checkbox"

    # 3. Dropdown: has dropdown indicator characters
    for indicator in DROPDOWN_INDICATORS:
        if indicator in text:
            return "dropdown"

    # 4. Input field: wide, short, no actionable text (or placeholder-like)
    if aspect >= INPUT_MIN_ASPECT and w >= INPUT_MIN_WIDTH:
        has_button_keyword = any(kw in text_lower for kw in BUTTON_KEYWORDS)
        if not has_button_keyword:
            return "input_field"

    # 5. Button: wide-ish, short, has actionable text
    if aspect >= BUTTON_MIN_ASPECT:
        has_button_keyword = any(kw in text_lower for kw in BUTTON_KEYWORDS)
        if has_button_keyword:
            return "button"

    # 6. Link: small inline text, URL-like patterns or short text
    if text_lower and len(text_lower) < 40:
        if any(p in text_lower for p in ["http", "www.", ".com", ".org", ".net"]):
            return "link"

    # 7. Icon: small and roughly square
    if w <= ICON_MAX_SIZE and h <= ICON_MAX_SIZE:
        return "icon"

    # 8. Fallback rules based on size + text
    if not text_lower:
        # No text: small → icon, else generic text region
        if w <= ICON_MAX_SIZE * 1.5 and h <= ICON_MAX_SIZE * 1.5:
            return "icon"
        return "text"

    # Has text: check if it looks like a button
    if aspect >= BUTTON_MIN_ASPECT and len(text_lower) < 30:
        return "button"

    return "text"


def classify_all(detections, texts, image_width, image_height):
    """Classify a list of detections.

    Args:
        detections: list of {"bbox": [...], "confidence": float}
        texts: list of text strings (same order)
        image_width: full image width
        image_height: full image height

    Returns:
        List of class name strings.
    """
    classes = []
    for det, text in zip(detections, texts):
        cls = classify(det["bbox"], text, image_width, image_height)
        classes.append(cls)
    return classes
