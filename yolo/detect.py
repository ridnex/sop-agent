"""UI Element Detector — main entry point.

Usage:
    python detect.py <image_path>

Outputs annotated image and JSON to output/ directory.
"""

import argparse
import json
import os
import sys

import cv2
from PIL import Image, ImageDraw, ImageFont

from yolo.config import CLASS_COLORS, MODEL_PATH
from yolo.utils.detector import load_model, detect
from yolo.utils.ocr import extract_texts_batch
from yolo.utils.classifier import classify_all


def draw_annotations(image, elements):
    """Draw bounding boxes and labels on the image using PIL."""
    import numpy as np

    img_pil = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(img_pil)

    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 14)
    except (IOError, OSError):
        font = ImageFont.load_default()

    for el in elements:
        x1, y1, x2, y2 = el["bbox"]
        cls = el["class"]
        conf = el["confidence"]
        label_text = el["label"]

        bgr = CLASS_COLORS.get(cls, (0, 255, 0))
        rgb = (bgr[2], bgr[1], bgr[0])

        draw.rectangle([x1, y1, x2, y2], outline=rgb, width=2)

        tag = f"{el['id']}: {label_text[:30]}" if label_text else str(el["id"])

        bbox_text = draw.textbbox((x1, y1), tag, font=font)
        text_w = bbox_text[2] - bbox_text[0]
        text_h = bbox_text[3] - bbox_text[1]
        label_y = max(y1 - text_h - 4, 0)
        draw.rectangle([x1, label_y, x1 + text_w + 4, label_y + text_h + 4], fill=rgb)
        draw.text((x1 + 2, label_y + 2), tag, fill=(255, 255, 255), font=font)

    return cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)


def main():
    parser = argparse.ArgumentParser(description="Detect UI elements in a screenshot.")
    parser.add_argument("image", help="Path to the input image")
    parser.add_argument("--output-dir", default="output", help="Output directory (default: output)")
    parser.add_argument("--confidence", type=float, default=None, help="Override YOLO confidence threshold")
    args = parser.parse_args()

    if not os.path.exists(args.image):
        print(f"Error: Image not found: {args.image}")
        sys.exit(1)

    # Resolve paths
    project_dir = os.path.dirname(os.path.abspath(__file__))
    model_path = os.path.join(project_dir, MODEL_PATH)
    output_dir = os.path.join(project_dir, args.output_dir)
    os.makedirs(output_dir, exist_ok=True)

    # Load image
    print(f"Loading image: {args.image}")
    image = cv2.imread(args.image)
    if image is None:
        print(f"Error: Could not read image: {args.image}")
        sys.exit(1)
    img_h, img_w = image.shape[:2]
    print(f"Image resolution: {img_w}x{img_h}")

    # Load model and run detection
    print("Loading YOLO model...")
    model = load_model(model_path)

    print("Running detection...")
    kwargs = {}
    if args.confidence is not None:
        kwargs["confidence"] = args.confidence
    detections = detect(model, args.image, **kwargs)
    print(f"Found {len(detections)} raw detections")

    if not detections:
        print("No elements detected.")
        sys.exit(0)

    # Extract text from each detection
    print("Running OCR on detected regions...")
    bboxes = [d["bbox"] for d in detections]
    texts = extract_texts_batch(image, bboxes)

    # Classify each detection
    print("Classifying elements...")
    classes = classify_all(detections, texts, img_w, img_h)

    # Build output elements
    elements = []
    for i, (det, text, cls) in enumerate(zip(detections, texts, classes), start=1):
        x1, y1, x2, y2 = det["bbox"]
        elements.append({
            "id": i,
            "class": cls,
            "confidence": det["confidence"],
            "bbox": det["bbox"],
            "center": [(x1 + x2) // 2, (y1 + y2) // 2],
            "label": text,
        })

    # Draw annotations
    print("Drawing annotations...")
    annotated = draw_annotations(image, elements)

    # Save outputs
    base_name = os.path.splitext(os.path.basename(args.image))[0]
    annotated_path = os.path.join(output_dir, f"{base_name}_annotated.png")
    json_path = os.path.join(output_dir, f"{base_name}.json")

    cv2.imwrite(annotated_path, annotated)
    print(f"Annotated image saved: {annotated_path}")

    output_data = {
        "image": args.image,
        "resolution": {"width": img_w, "height": img_h},
        "elements": elements,
    }
    with open(json_path, "w") as f:
        json.dump(output_data, f, indent=2)
    print(f"JSON saved: {json_path}")

    # Print summary
    class_counts = {}
    for el in elements:
        class_counts[el["class"]] = class_counts.get(el["class"], 0) + 1
    print(f"\nSummary: {len(elements)} elements detected")
    for cls, count in sorted(class_counts.items()):
        print(f"  {cls}: {count}")


if __name__ == "__main__":
    main()
