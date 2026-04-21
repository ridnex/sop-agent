r"""Main entry point for the system-wide interaction recorder with video.

Usage:
    python -m recorder.record --name "my_task"
    python -m recorder.record --name "my_task" --output ./outputs

    # Do the task in any app... press ESC when done.

Architecture:
    - Continuous video recording via macOS screencapture (or ffmpeg on Linux)
    - pynput listeners capture every event with synchronous AX element lookup
    - State captured at meaningful boundaries (mouseup, scroll stop, typing pause)
    - After recording stops, screenshots are extracted from video at state timestamps
    - ESC stops recording, saves raw JSON, post-processes, extracts frames
"""

import argparse
import json
import os
import sys
import time
import threading
from datetime import datetime

from pynput import mouse, keyboard

from recorder.models import State
from recorder.observer import SystemObserver
from recorder.screen_recorder import ScreenRecorder
from recorder.screenshot_extractor import extract_screenshots
from recorder.postprocess import postprocess
from recorder import accessibility


def _check_permissions():
    """Check macOS Accessibility and Screen Recording permissions at startup."""
    if sys.platform != "darwin":
        print("[Recorder] Warning: This recorder is designed for macOS.")
        return

    if not accessibility.check_accessibility_permission():
        print("[Recorder] ERROR: Accessibility permission is required.")
        print("[Recorder] Please grant permission in:")
        print("  System Preferences > Privacy & Security > Accessibility")
        print("  Add your terminal app (Terminal.app, iTerm, etc.)")
        sys.exit(1)

    # Screen Recording permission is needed for both video and AX in some cases.
    # The video will fail obviously if not granted, so just warn.
    try:
        import Quartz
        image_ref = Quartz.CGWindowListCreateImage(
            Quartz.CGRectInfinite,
            Quartz.kCGWindowListOptionOnScreenOnly,
            Quartz.kCGNullWindowID,
            Quartz.kCGWindowImageDefault,
        )
        if image_ref is None:
            print("[Recorder] Warning: Screen Recording permission may not be granted.")
            print("[Recorder] Please grant permission in:")
            print("  System Preferences > Privacy & Security > Screen Recording")
            print("  Add your terminal app (Terminal.app, iTerm, etc.)")
            print("[Recorder] Video recording may fail without this permission.\n")
    except Exception:
        print("[Recorder] Warning: Could not verify Screen Recording permission.\n")


def create_output_dir(name: str, output_base: str) -> tuple:
    """Create the output directory structure.

    Returns:
        (folder_name, folder_path) tuple.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
    folder_name = f"{name} @ {timestamp}"
    folder_path = os.path.join(output_base, folder_name)
    screenshots_path = os.path.join(folder_path, "screenshots")
    os.makedirs(screenshots_path, exist_ok=True)
    print(f"[Recorder] Output directory: {folder_path}")
    return folder_name, folder_path


def main():
    parser = argparse.ArgumentParser(description="Record system-wide interactions with video")
    parser.add_argument("--name", required=True, help="Task name for the output folder")
    parser.add_argument("--output", default="./outputs", help="Base output directory")
    args = parser.parse_args()

    # Check permissions
    _check_permissions()

    # Create output directory
    folder_name, folder_path = create_output_dir(args.name, args.output)
    screenshots_dir = os.path.join(folder_path, "screenshots")
    raw_json_path = os.path.join(folder_path, f"[raw] {folder_name}.json")
    clean_json_path = os.path.join(folder_path, f"{folder_name}.json")
    video_path = os.path.join(folder_path, f"{folder_name}.mp4")

    # Initialize observer
    observer = SystemObserver()

    # Start video recording
    screen_recorder = ScreenRecorder(video_path)
    video_start_time = screen_recorder.start()
    time.sleep(1)  # Let the recorder stabilize

    # ── All events are stored here ──
    raw_events = []       # list of {"type": "state"|"action", "data": dict}
    raw_lock = threading.Lock()
    event_counter = 0
    start_time = datetime.now()

    def secs_from_start(ts: datetime) -> float:
        return (ts - start_time).total_seconds()

    # ── Helper: convert State to raw dict ──
    def state_to_raw_dict(state: State, ts: datetime) -> dict:
        return {
            "id": None,
            "step": None,
            "timestamp": ts.isoformat(),
            "secs_from_start": round(secs_from_start(ts), 6),
            "url": state.url,
            "tab": state.tab,
            "json_state": state.json_state,
            "html": state.html,
            "screenshot_base64": None,
            "path_to_screenshot": None,
            "window_position": state.window_position,
            "window_size": state.window_size,
            "active_application_name": state.active_application_name,
            "screen_size": state.screen_size,
            "is_headless": state.is_headless,
        }

    # ── Helper: capture state (no screenshot — extracted from video later) ──
    def capture_state() -> None:
        nonlocal event_counter
        state = observer.run()
        now = datetime.now()

        with raw_lock:
            raw_events.append({
                "type": "state",
                "data": state_to_raw_dict(state, now)
            })
            event_counter += 1

    # ── Capture initial state ──
    print("[Recorder] Capturing initial state...")
    capture_state()
    print("[Recorder] Initial state captured.")

    # ── Helper: log an action ──
    def log_action(event_type: str, **kwargs) -> int:
        """Append an action to raw_events. Returns internal ID."""
        now = datetime.now()
        with raw_lock:
            nonlocal event_counter
            aid = event_counter
            data = {
                "id": aid,
                "type": event_type,
                "timestamp": now.isoformat(),
                "secs_from_start": round(secs_from_start(now), 6),
            }
            data.update(kwargs)
            raw_events.append({"type": "action", "data": data})
            event_counter += 1
            return aid

    # ── Debounce timers ──
    _scroll_timer = None
    _scroll_lock = threading.Lock()
    SCROLL_DEBOUNCE_SECS = 0.6

    _type_timer = None
    _type_lock = threading.Lock()
    TYPE_DEBOUNCE_SECS = 0.5

    # ── pynput callbacks ──
    stop_event = threading.Event()
    action_count = 0

    def on_click(x, y, button, pressed):
        nonlocal action_count
        if stop_event.is_set():
            return False

        event_type = "mousedown" if pressed else "mouseup"
        is_right = button == mouse.Button.right

        # Get element at click position (synchronous — AX API is fast)
        element_attrs = observer.get_element_at_position(float(x), float(y))

        log_action(
            event_type,
            x=float(x), y=float(y),
            is_right_click=is_right,
            pressed=pressed,
            element_attributes=element_attrs,
        )
        action_count += 1
        print(f"  [{action_count}] {event_type} ({x:.0f}, {y:.0f})")

        # Capture state after mouseup
        if not pressed:
            capture_state()

    def on_scroll(x, y, dx, dy):
        nonlocal action_count, _scroll_timer
        if stop_event.is_set():
            return False

        # Get element at scroll position
        element_attrs = observer.get_element_at_position(float(x), float(y))

        log_action(
            "scroll",
            x=float(x), y=float(y),
            dx=float(dx), dy=float(dy),
            element_attributes=element_attrs,
        )
        action_count += 1
        print(f"  [{action_count}] scroll ({x:.0f}, {y:.0f}) dx={dx} dy={dy}")

        # Debounce: capture state after scrolling stops
        with _scroll_lock:
            if _scroll_timer is not None:
                _scroll_timer.cancel()
            _scroll_timer = threading.Timer(
                SCROLL_DEBOUNCE_SECS,
                capture_state,
            )
            _scroll_timer.start()

    def _key_to_str(key):
        """Convert pynput key to the reference format."""
        if hasattr(key, 'char') and key.char is not None:
            return f"'{key.char}'"
        elif hasattr(key, 'name'):
            return f"Key.{key.name}"
        return str(key)

    def on_press(key):
        nonlocal action_count, _type_timer
        key_str = _key_to_str(key)

        # ESC stops recording
        if key_str == "Key.esc":
            print("\n[Recorder] ESC pressed — stopping recording...")
            stop_event.set()
            return False

        if stop_event.is_set():
            return False

        # Get focused element for keyboard events
        element_attrs = observer.get_focused_element()

        log_action(
            "keypress",
            key=key_str,
            element_attributes=element_attrs,
        )
        action_count += 1
        print(f"  [{action_count}] keypress {key_str}")

        # Debounce: capture state after typing pause
        with _type_lock:
            if _type_timer is not None:
                _type_timer.cancel()
            _type_timer = threading.Timer(
                TYPE_DEBOUNCE_SECS,
                capture_state,
            )
            _type_timer.start()

    def on_release(key):
        nonlocal action_count
        key_str = _key_to_str(key)

        if stop_event.is_set():
            return False

        log_action(
            "keyrelease",
            key=key_str,
            element_attributes={},
        )
        action_count += 1

    # ── Start listeners ──
    print("\n[Recorder] Recording started. Perform your task in any application.")
    print("[Recorder] Press ESC when done.\n")

    mouse_listener = mouse.Listener(on_click=on_click, on_scroll=on_scroll)
    keyboard_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    mouse_listener.start()
    keyboard_listener.start()

    # Wait for ESC
    keyboard_listener.join()
    mouse_listener.stop()

    # ── Stop and save ──
    # Cancel any pending debounce timers
    with _scroll_lock:
        if _scroll_timer is not None:
            _scroll_timer.cancel()
    with _type_lock:
        if _type_timer is not None:
            _type_timer.cancel()

    print(f"\n[Recorder] Stopping... {action_count} actions recorded.")

    # 1. Capture final state
    print("[Recorder] [1/5] Capturing final state...")
    capture_state()
    print("[Recorder]       Done.")

    # 2. Save raw trace
    print("[Recorder] [2/5] Saving raw trace...")
    with raw_lock:
        total_events = len(raw_events)
    with open(raw_json_path, "w") as f:
        json.dump({"trace": raw_events}, f, indent=2)
    print(f"[Recorder]       Saved {total_events} events → {raw_json_path}")

    # 3. Stop video recording
    print("[Recorder] [3/5] Stopping video recording...")
    screen_recorder.stop()
    print("[Recorder]       Done.")

    # 4. Post-process into clean trace
    print("[Recorder] [4/5] Post-processing...")
    clean_trace_data = postprocess(raw_events)
    print(f"[Recorder]       Raw: {total_events} → Clean: {len(clean_trace_data)} events")

    # 5. Extract screenshots from video
    if os.path.exists(video_path):
        print("[Recorder] [5/5] Extracting screenshots from video...")
        try:
            clean_trace_data = extract_screenshots(
                clean_trace_data, video_path, screenshots_dir,
                output_prefix="./screenshots",
                video_start_time=video_start_time,
            )
        except Exception as e:
            print(f"[Recorder]       Screenshot extraction failed: {e}")
    else:
        print("[Recorder] [5/5] No video file found — skipping screenshot extraction.")

    # Save final clean trace (with screenshot paths)
    with open(clean_json_path, "w") as f:
        json.dump({"trace": clean_trace_data}, f, indent=2)

    print(f"\n[Recorder] Done! Output: {folder_path}")
    print(f"  Raw trace:    {raw_json_path}")
    print(f"  Clean trace:  {clean_json_path}")
    print(f"  Video:        {video_path}")
    print(f"  Screenshots:  {screenshots_dir}/")


if __name__ == "__main__":
    main()
