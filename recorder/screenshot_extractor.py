"""Extract PNG screenshot frames from the .mp4 screen recording.

For each state in the cleaned trace, extracts the video frame at the appropriate
timestamp. Uses the state's own timestamp (what the screen looked like at that moment)
with a small negative offset (~0.5s) to capture "just before action" keyframes.

Accepts an explicit video_start_time for accurate offset calculation instead of
inferring it from the first state's timestamp (which may have drift).
"""

import os
import subprocess
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


def _probe_duration(video_path: str) -> float:
    """Return video duration via ffprobe. Used when the caller bypasses moviepy."""
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
        )
        return float(proc.stdout.decode("utf-8").strip())
    except (subprocess.CalledProcessError, ValueError, FileNotFoundError):
        return 0.0


def extract_screenshots(
    trace: list,
    video_path: str,
    screenshots_dir: str,
    output_prefix: str = "./screenshots",
    video_start_time: Optional[datetime] = None,
    pre_action_offset_secs: Optional[float] = None,
    use_state_ts_at_boundaries: bool = False,
    frame_extractor=None,
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

    offset_secs = (
        PRE_ACTION_OFFSET_SECS if pre_action_offset_secs is None else pre_action_offset_secs
    )

    # Only open the video with moviepy if we're using its frame extractor.
    # When the caller provides a custom ``frame_extractor`` (e.g. ffmpeg
    # ``-ss`` for the web recorder), we bypass moviepy entirely — moviepy's
    # ``get_frame`` snaps to the nearest available frame which is unreliable
    # on variable-frame-rate WebM-converted MP4s.
    video = VideoFileClip(video_path) if frame_extractor is None else None
    # Duration used for clamping target_secs to a valid video position.
    video_duration = video.duration if video is not None else _probe_duration(video_path)

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
        if video is not None:
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
        is_first_state = (si == 0)
        is_last_state = (si == len(state_indices) - 1)
        # Caller can request that boundary states use their own ts (web recorder
        # sets explicit opening/closing keyframes at session start / end).
        boundary_uses_state_ts = use_state_ts_at_boundaries and (
            is_first_state or is_last_state
        )

        # Use the state's own timestamp as the base
        state_ts = _parse_timestamp(item["data"]["timestamp"])

        # Look for the next action after this state
        next_action = None
        if not is_last_state:
            for j in range(i + 1, len(trace)):
                if trace[j]["type"] == "action":
                    next_action = trace[j]["data"]
                    break

        if next_action is not None and not is_last_state and not boundary_uses_state_ts:
            # Get the action timestamp to apply pre-action offset
            if next_action.get("type") == "keystroke" and next_action.get("start_timestamp"):
                action_ts = _parse_timestamp(next_action["start_timestamp"])
            else:
                action_ts = _parse_timestamp(next_action["timestamp"])

            # Use action timestamp minus a small offset to get "just before action"
            target_secs = (action_ts - recording_start).total_seconds() - offset_secs
        else:
            # Last state, or a boundary state when caller opted in: use the
            # state's own timestamp directly.
            target_secs = (state_ts - recording_start).total_seconds()

        # Clamp to valid video range
        target_secs = max(0.0, min(target_secs, video_duration - 0.1))

        # Extract frame and save as PNG
        png_path = os.path.join(screenshots_dir, f"{img_idx}.png")
        if frame_extractor is not None:
            frame_extractor(video_path, target_secs, png_path)
        else:
            frame = video.get_frame(target_secs)
            img = Image.fromarray(frame)
            img.save(png_path)

        # Update trace with screenshot path
        item["data"]["path_to_screenshot"] = f"{output_prefix}/{img_idx}.png"
        item["data"]["screenshot_base64"] = None  # Not stored in cleaned trace

        img_idx += 1

    if video is not None:
        video.close()
    print(f"[ScreenshotExtractor] Extracted {img_idx} screenshots to {screenshots_dir}")
    return trace
