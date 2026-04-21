"""System state capture via macOS APIs.

Captures the current system state: active app, window title, window geometry,
screen size. Delegates element detection to the accessibility module.
"""

import subprocess
import sys
from datetime import datetime

from recorder.models import State
from recorder import accessibility


def _get_screen_size() -> dict:
    """Get the main display resolution."""
    try:
        import Quartz
        main_display = Quartz.CGMainDisplayID()
        width = Quartz.CGDisplayPixelsWide(main_display)
        height = Quartz.CGDisplayPixelsHigh(main_display)
        return {"width": int(width), "height": int(height)}
    except Exception:
        pass
    # Fallback: system_profiler
    if sys.platform == "darwin":
        try:
            output = subprocess.check_output(
                ["system_profiler", "SPDisplaysDataType"],
                text=True,
            )
            for line in output.split("\n"):
                if "Resolution" in line:
                    parts = line.split(":")[1].strip().split(" ")
                    return {"width": int(parts[0]), "height": int(parts[2])}
        except Exception:
            pass
    return {"width": 1920, "height": 1080}


class SystemObserver:
    """Captures system-wide state using macOS Accessibility and Quartz APIs."""

    def __init__(self):
        self._screen_size = _get_screen_size()

    def run(self) -> State:
        """Capture the current system state.

        Returns:
            State object with active app name, window title, window geometry, etc.
        """
        now = datetime.now()
        app_info = accessibility.get_frontmost_app_info()

        return State(
            url=app_info["bundle_id"],
            tab=app_info["window_title"],
            json_state="[]",
            html="",
            screenshot_base64=None,
            path_to_screenshot="",
            window_position={"x": app_info["window_x"], "y": app_info["window_y"]},
            window_size={"width": app_info["window_width"], "height": app_info["window_height"]},
            screen_size=self._screen_size,
            active_application_name=app_info["app_name"],
            is_headless=False,
            timestamp=now,
        )

    def get_element_at_position(self, x: float, y: float) -> dict:
        """Get UI element at screen coordinates. Delegates to accessibility module."""
        return accessibility.get_element_at_position(x, y)

    def get_focused_element(self) -> dict:
        """Get the currently focused UI element. Delegates to accessibility module."""
        return accessibility.get_focused_element()
