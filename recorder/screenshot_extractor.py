"""Extract PNG screenshot frames from the .mp4 screen recording.

For each state in the cleaned trace, extracts the video frame at the appropriate
timestamp. Uses the state's own timestamp (what the screen looked like at that moment)
with a small negative offset (~0.5s) to capture "just before action" keyframes.

Accepts an explicit video_start_time for accurate offset calculation instead of
inferring it from the first state's timestamp (which may have drift).
"""

import os
from datetime import datetime
from typing import List, Optional

from moviepy import VideoFileClip
from PIL import Image
import numpy as np


# Small offset before action to capture the "just before" keyframe
PRE_ACTION_OFFSET_SECS = 0.5


def _parse_timestamp(ts_str: str) -> datetime:
    """Parse an ISO format timestamp string."""
    # Handle both with and without microseconds
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(ts_str, fmt)
        except ValueError:
            continue
    raise ValueError(f"Cannot parse timestamp: {ts_str}")


def extract_screenshots(
    trace: list,
    video_path: str,
    screenshots_dir: str,
    output_prefix: str = "./screenshots",
    video_start_time: Optional[datetime] = None,
) -> list:
    """Extract PNG frames from a video at each state's timestamp.

    Uses state timestamps (representing "what the screen looked like") with
    a small pre-action offset for better keyframe timing.

    For the last state, uses its own timestamp without offset (already correct).

    Args:
        trace: Cleaned trace (list of {"type": "state"|"action", "data": {...}}).
        video_path: Path to the .mp4 screen recording.
        screenshots_dir: Directory to save extracted PNGs.
        output_prefix: Prefix for path_to_screenshot in the trace (default "./screenshots").
        video_start_time: Actual video recording start time (from ScreenRecorder.start()).
                         Falls back to first state timestamp if not provided.

    Returns:
        The trace with updated path_to_screenshot fields.
    """
    os.makedirs(screenshots_dir, exist_ok=True)

    video = VideoFileClip(video_path)

    # Determine recording start time
    recording_start = video_start_time

    if recording_start is None:
        # Fallback: use first state's timestamp
        for item in trace:
            if item["type"] == "state":
                recording_start = _parse_timestamp(item["data"]["timestamp"])
                break

    if recording_start is None:
        print("[ScreenshotExtractor] Warning: No states found in trace.")
        video.close()
        return trace

    # Collect state indices and check which are followed by actions
    state_indices = []
    for i, item in enumerate(trace):
        if item["type"] == "state":
            state_indices.append(i)

    img_idx = 0
    for si, i in enumerate(state_indices):
        item = trace[i]
        is_last_state = (si == len(state_indices) - 1)

        # Use the state's own timestamp as the base
        state_ts = _parse_timestamp(item["data"]["timestamp"])

        # Look for the next action after this state
        next_action = None
        if not is_last_state:
            for j in range(i + 1, len(trace)):
                if trace[j]["type"] == "action":
                    next_action = trace[j]["data"]
                    break

        if next_action is not None and not is_last_state:
            # Get the action timestamp to apply pre-action offset
            if next_action.get("type") == "keystroke" and next_action.get("start_timestamp"):
                action_ts = _parse_timestamp(next_action["start_timestamp"])
            else:
                action_ts = _parse_timestamp(next_action["timestamp"])

            # Use action timestamp minus a small offset to get "just before action"
            target_secs = (action_ts - recording_start).total_seconds() - PRE_ACTION_OFFSET_SECS
        else:
            # Last state — use its own timestamp
            target_secs = (state_ts - recording_start).total_seconds()

        # Clamp to valid video range
        target_secs = max(0, min(target_secs, video.duration - 0.1))

        # Extract frame and save as PNG
        frame = video.get_frame(target_secs)
        img = Image.fromarray(frame)
        png_path = os.path.join(screenshots_dir, f"{img_idx}.png")
        img.save(png_path)

        # Update trace with screenshot path
        item["data"]["path_to_screenshot"] = f"{output_prefix}/{img_idx}.png"
        item["data"]["screenshot_base64"] = None  # Not stored in cleaned trace

        img_idx += 1

    video.close()
    print(f"[ScreenshotExtractor] Extracted {img_idx} screenshots to {screenshots_dir}")
    return trace
