"""Configuration for UI element detection."""

# Target element classes
CLASSES = [
    "button",
    "input_field",
    "navigation",
    "icon",
    "dropdown",
    "checkbox",
    "link",
    "text",
]

# YOLO settings
YOLO_CONFIDENCE_THRESHOLD = 0.4
YOLO_IOU_THRESHOLD = 0.5  # NMS IoU threshold to remove overlapping boxes
YOLO_INPUT_SIZE = 1280
MODEL_PATH = "models/icon_detect/model.pt"

# OCR settings
OCR_CROP_PADDING = 5  # pixels to pad around bbox crops for better OCR

# Per-class colors (BGR for OpenCV)
CLASS_COLORS = {
    "button":      (0, 165, 255),   # orange
    "input_field": (255, 144, 30),  # dodger blue
    "navigation":  (0, 255, 0),     # green
    "icon":        (255, 0, 255),   # magenta
    "dropdown":    (0, 255, 255),   # yellow
    "checkbox":    (255, 255, 0),   # cyan
    "link":        (147, 20, 255),  # pink
    "text":        (180, 180, 180), # gray
}

# Heuristic classifier thresholds
CHECKBOX_MAX_SIZE = 40       # max width/height for checkbox
ICON_MAX_SIZE = 80           # max width/height for icon
NAV_WIDTH_RATIO = 0.7        # min width relative to image width for navigation
NAV_EDGE_MARGIN = 0.15       # max distance from top/bottom edge (fraction of image height)
BUTTON_MIN_ASPECT = 1.5      # min width/height ratio for button
INPUT_MIN_ASPECT = 2.5       # min width/height ratio for input field
INPUT_MIN_WIDTH = 100        # min width in pixels for input field

# Actionable button keywords
BUTTON_KEYWORDS = [
    "submit", "ok", "cancel", "login", "sign", "click", "save",
    "next", "back", "continue", "confirm", "delete", "remove",
    "add", "create", "update", "send", "apply", "close", "open",
    "start", "stop", "yes", "no", "retry", "done", "finish",
    "accept", "decline", "buy", "checkout", "register", "log in",
    "sign up", "sign in", "get started", "learn more", "try",
    "download", "upload", "share", "edit", "search", "go",
]

# Dropdown indicators
DROPDOWN_INDICATORS = ["▼", "▾", "⌄", "v", "▿", "↓"]
