"""Action executor: parses DSL strings and drives macOS desktop via pyautogui."""

import re
import time
import subprocess
import logging

import pyautogui
import Quartz

logger = logging.getLogger(__name__)

# Safety settings
pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.3

# Maximum wait time in seconds
MAX_WAIT = 10


def _get_cursor_position() -> tuple[float, float]:
    """Get the current mouse cursor position in global coordinates.

    Returns:
        (x, y) tuple in point coordinates.
    """
    event = Quartz.CGEventCreate(None)
    cursor_loc = Quartz.CGEventGetLocation(event)
    return cursor_loc.x, cursor_loc.y


def _get_display_at_cursor() -> int:
    """Get the display ID where the mouse cursor is currently located.

    Returns the display containing the cursor. Falls back to main display
    if cursor position cannot be determined.
    """
    # Get current cursor position
    cursor_x, cursor_y = _get_cursor_position()

    # Find which display contains this point
    # Default fallback to main display
    display_id = Quartz.CGMainDisplayID()

    # Get all active displays and check which one contains the cursor
    max_displays = 32
    (err, active_displays, count) = Quartz.CGGetActiveDisplayList(max_displays, None, None)

    if err == 0 and count > 0:
        for disp_id in active_displays:
            bounds = Quartz.CGDisplayBounds(disp_id)
            # Check if cursor is within this display's bounds
            if (bounds.origin.x <= cursor_x < bounds.origin.x + bounds.size.width and
                bounds.origin.y <= cursor_y < bounds.origin.y + bounds.size.height):
                display_id = disp_id
                break

    return display_id


def _get_screen_size_points() -> tuple[int, int]:
    """Get screen size in logical points for the display containing the cursor.

    Detects which display the mouse is on and returns that display's dimensions.
    On Retina displays, returns point dimensions (e.g. 2560x1440),
    not physical pixels (e.g. 5120x2880).
    """
    cursor_display = _get_display_at_cursor()
    bounds = Quartz.CGDisplayBounds(cursor_display)
    return int(bounds.size.width), int(bounds.size.height)


def _get_display_info() -> dict:
    """Get complete info for the display at the cursor.

    Returns dict with: display_id, width, height, origin_x, origin_y.
    The origin is the top-left corner in global virtual screen coordinates.
    On a single-display setup, origin is (0, 0). On multi-display setups,
    secondary displays have non-zero origins (e.g., (2560, 0)).
    """
    display_id = _get_display_at_cursor()
    bounds = Quartz.CGDisplayBounds(display_id)
    return {
        "display_id": display_id,
        "width": int(bounds.size.width),
        "height": int(bounds.size.height),
        "origin_x": float(bounds.origin.x),
        "origin_y": float(bounds.origin.y),
    }


def _type_text(text: str) -> None:
    """Type text, using clipboard paste for non-ASCII characters."""
    if all(ord(c) < 128 for c in text):
        pyautogui.typewrite(text, interval=0.02)
    else:
        # Use clipboard for unicode text
        process = subprocess.run(
            ["pbcopy"],
            input=text.encode("utf-8"),
            check=True,
        )
        pyautogui.hotkey("command", "v")
        time.sleep(0.1)


def _parse_key_combo(key_str: str) -> list[str]:
    """Parse a key combo string like 'cmd+a' into a list of keys.

    Normalizes modifier names to pyautogui names.
    """
    key_map = {
        "cmd": "command",
        "ctrl": "ctrl",
        "alt": "alt",
        "option": "alt",
        "shift": "shift",
        "enter": "enter",
        "return": "enter",
        "tab": "tab",
        "esc": "escape",
        "escape": "escape",
        "space": "space",
        "backspace": "backspace",
        "delete": "delete",
        "up": "up",
        "down": "down",
        "left": "left",
        "right": "right",
        "home": "home",
        "end": "end",
        "pageup": "pageup",
        "pagedown": "pagedown",
        "f1": "f1", "f2": "f2", "f3": "f3", "f4": "f4",
        "f5": "f5", "f6": "f6", "f7": "f7", "f8": "f8",
        "f9": "f9", "f10": "f10", "f11": "f11", "f12": "f12",
    }
    parts = [p.strip().lower() for p in key_str.split("+")]
    return [key_map.get(p, p) for p in parts]


def execute_action(action_dsl: str, display_origin: tuple[float, float] = (0, 0)) -> str:
    """Parse and execute a single action DSL string.

    Supported actions:
        MOVE_MOUSE(x, y)
        CLICK(x, y)
        DOUBLE_CLICK(x, y)
        RIGHT_CLICK(x, y)
        TYPE('text')
        KEYPRESS(key) or KEYPRESS(cmd+a)
        SCROLL(dx, dy)
        WAIT(seconds)

    Args:
        action_dsl: The DSL string to execute.
        display_origin: (origin_x, origin_y) of the active display in global
            virtual screen coordinates. Added to all coordinate-based actions
            so that pyautogui targets the correct display.

    Returns:
        The action type name (e.g., "MOVE_MOUSE", "CLICK") for variable delay logic.

    Raises:
        ValueError: If the action string cannot be parsed.
    """
    action_dsl = action_dsl.strip()
    logger.info(f"Executing: {action_dsl}")
    ox, oy = display_origin

    # MOVE_MOUSE(x, y)
    m = re.match(r"MOVE_MOUSE\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)", action_dsl)
    if m:
        x, y = float(m.group(1)) + ox, float(m.group(2)) + oy
        pyautogui.moveTo(x, y)
        return "MOVE_MOUSE"

    # CLICK(x, y)
    m = re.match(r"CLICK\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)", action_dsl)
    if m:
        x, y = float(m.group(1)) + ox, float(m.group(2)) + oy
        pyautogui.click(x, y)
        return "CLICK"

    # DOUBLE_CLICK(x, y)
    m = re.match(r"DOUBLE_CLICK\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)", action_dsl)
    if m:
        x, y = float(m.group(1)) + ox, float(m.group(2)) + oy
        pyautogui.doubleClick(x, y)
        return "DOUBLE_CLICK"

    # RIGHT_CLICK(x, y)
    m = re.match(r"RIGHT_CLICK\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)", action_dsl)
    if m:
        x, y = float(m.group(1)) + ox, float(m.group(2)) + oy
        pyautogui.rightClick(x, y)
        return "RIGHT_CLICK"

    # TYPE('text') or TYPE("text")
    m = re.match(r"TYPE\(\s*(['\"])(.*?)\1\s*\)", action_dsl, re.DOTALL)
    if m:
        text = m.group(2)
        _type_text(text)
        return "TYPE"

    # KEYPRESS(key) or KEYPRESS(cmd+a)
    m = re.match(r"KEYPRESS\(\s*(.+?)\s*\)", action_dsl)
    if m:
        keys = _parse_key_combo(m.group(1))
        if len(keys) == 1:
            pyautogui.press(keys[0])
        else:
            pyautogui.hotkey(*keys)
        return "KEYPRESS"

    # SCROLL(dx, dy)
    m = re.match(r"SCROLL\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)", action_dsl)
    if m:
        dy = int(float(m.group(2)))
        pyautogui.scroll(dy)
        return "SCROLL"

    # WAIT(seconds)
    m = re.match(r"WAIT\(\s*(\d+(?:\.\d+)?)\s*\)", action_dsl)
    if m:
        seconds = min(float(m.group(1)), MAX_WAIT)
        time.sleep(seconds)
        return "WAIT"

    raise ValueError(f"Unknown action: {action_dsl}")
