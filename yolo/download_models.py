"""Download OmniParser YOLO weights from HuggingFace."""

import os
import shutil
from huggingface_hub import hf_hub_download

MODEL_DIR = os.path.join(os.path.dirname(__file__), "models", "icon_detect")
MODEL_PATH = os.path.join(MODEL_DIR, "model.pt")

REPO_ID = "microsoft/OmniParser-v2.0"
FILENAME = "icon_detect/model.pt"


def download_model():
    if os.path.exists(MODEL_PATH):
        size_mb = os.path.getsize(MODEL_PATH) / (1024 * 1024)
        print(f"Model already exists at {MODEL_PATH} ({size_mb:.1f} MB) — skipping download.")
        return MODEL_PATH

    os.makedirs(MODEL_DIR, exist_ok=True)
    print(f"Downloading OmniParser YOLO weights from {REPO_ID}...")
    downloaded_path = hf_hub_download(
        repo_id=REPO_ID,
        filename=FILENAME,
        local_dir=os.path.join(os.path.dirname(__file__), "models"),
    )
    # hf_hub_download with local_dir puts file at models/icon_detect/model.pt
    if not os.path.exists(MODEL_PATH):
        shutil.copy2(downloaded_path, MODEL_PATH)

    size_mb = os.path.getsize(MODEL_PATH) / (1024 * 1024)
    print(f"Model downloaded to {MODEL_PATH} ({size_mb:.1f} MB)")
    return MODEL_PATH


if __name__ == "__main__":
    download_model()
