"""Screenshot capture using macOS native screencapture."""

import base64
import subprocess
from pathlib import Path


def _get_display_number(display_id: int) -> int:
    """Convert CGDisplayID to screencapture display number (1-based).

    screencapture -D expects: 1 for main display, 2 for second display, etc.

    Args:
        display_id: The CGDisplayID from Quartz.

    Returns:
        Display number for screencapture (1-based index).
    """
    import Quartz

    main_display = Quartz.CGMainDisplayID()

    # Main display is always 1
    if display_id == main_display:
        return 1

    # Get all active displays
    max_displays = 32
    (err, active_displays, count) = Quartz.CGGetActiveDisplayList(max_displays, None, None)

    if err == 0 and count > 0:
        # Find the index of this display (skip main display)
        display_num = 1
        for disp_id in active_displays:
            if disp_id == main_display:
                continue
            display_num += 1
            if disp_id == display_id:
                return display_num

    # Fallback: return 1 (main display)
    return 1


def take_screenshot(output_path: str, display_id: int | None = None) -> str:
    """Capture screen to a PNG file using macOS screencapture.

    Args:
        output_path: Where to save the screenshot.
        display_id: Specific display ID (CGDisplayID) to capture. If None, captures main display.

    Returns:
        The output path.
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    cmd = ["screencapture", "-x", "-C"]  # -x = no sound, -C = capture cursor

    if display_id is not None:
        # Convert CGDisplayID to screencapture display number
        display_num = _get_display_number(display_id)
        cmd.extend(["-D", str(display_num)])

    cmd.append(output_path)
    subprocess.run(cmd, check=True, capture_output=True)
    return output_path


def screenshot_to_base64(path: str) -> str:
    """Read a PNG file and return its base64 string."""
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")
