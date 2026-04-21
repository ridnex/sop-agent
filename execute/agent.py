"""Core execution loop: observe → think → act → repeat."""

import json
import logging
import re
import threading
import time
from pathlib import Path

import pyautogui
from pynput import keyboard

from sop.api_client import call_openai
from execute.executor import execute_action, _get_display_info
from execute.models import ExecutionLog, StepRecord
from execute.prompts import build_execution_message
from execute.screenshotter import take_screenshot


def _get_detect_fn(detector: str):
    """Dynamically import detect_elements from the chosen detector module."""
    if detector == "dino":
        from dino import detect_elements
    else:
        from yolo import detect_elements
    return detect_elements

logger = logging.getLogger(__name__)

# --- ESC stop mechanism ---
_stop_event = threading.Event()


def _start_esc_listener() -> keyboard.Listener:
    """Start a background listener that sets _stop_event when ESC is pressed."""
    def on_press(key):
        if key == keyboard.Key.esc:
            _stop_event.set()
            return False  # stop the listener

    listener = keyboard.Listener(on_press=on_press)
    listener.daemon = True
    listener.start()
    return listener


def _parse_model_response(raw: str) -> dict:
    """Parse model JSON response, tolerating markdown fences."""
    text = raw.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first and last fence lines
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines)
    return json.loads(text)


def _get_active_app_name() -> str:
    """Get the name of the currently active application.

    Uses NSWorkspace which doesn't require accessibility permissions.
    """
    try:
        from Cocoa import NSWorkspace
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        return str(app.localizedName()) if app else "Unknown"
    except Exception as e:
        logger.debug(f"Could not get active app name: {e}")
        return "Unknown"


# Regex to detect click actions
_CLICK_RE = re.compile(
    r"(CLICK|DOUBLE_CLICK|RIGHT_CLICK)\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)"
)


def run_agent(
    sop_text: str,
    output_dir: Path,
    intent: str = "",
    reference_screenshots: list[Path] | None = None,
    max_steps: int = 50,
    delay: float = 2.0,
    auto_confirm: bool = False,
    max_attempts_per_step: int = 10,
    detector: str = "yolo",
) -> ExecutionLog:
    """Run the SOP execution agent.

    Observe → Think → Act loop up to max_steps iterations.

    Args:
        sop_text: The SOP text to execute.
        output_dir: Directory to save screenshots and logs.
        intent: The high-level intent/goal of the SOP.
        reference_screenshots: Optional list of reference screenshot paths (ignored in vision-first mode).
        max_steps: Maximum number of steps before stopping.
        delay: Seconds to wait between steps for UI to settle.
        auto_confirm: If False, prompt user before starting.
        max_attempts_per_step: Max consecutive attempts on the same SOP step
            before declaring it stuck (default 10).

    Returns:
        ExecutionLog with all step records.
    """
    if not auto_confirm:
        print("\n" + "=" * 60)
        print("SOP EXECUTION AGENT (Vision-First Mode)")
        print("=" * 60)
        print(f"\nIntent: {intent or '(not specified)'}")
        print(f"Max steps: {max_steps}")
        print(f"Delay between steps: {delay}s")
        print(f"\nSOP:\n{sop_text[:500]}{'...' if len(sop_text) > 500 else ''}")
        print("\n" + "-" * 60)
        print("SAFETY: Press ESC or move mouse to screen corner to abort")
        print("-" * 60)
        confirm = input("\nStart execution? [y/N] ").strip().lower()
        if confirm != "y":
            print("Aborted.")
            return ExecutionLog(sop_text=sop_text, intent=intent)

    # Resolve detector
    detect_elements = _get_detect_fn(detector)
    detector_label = detector.upper()

    # Prepare output dirs
    screenshots_dir = output_dir / "execution_screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    detector_dir = output_dir / detector
    detector_dir.mkdir(parents=True, exist_ok=True)

    log = ExecutionLog(sop_text=sop_text, intent=intent)
    action_history: list[dict] = []
    last_sop_step: int | None = None
    attempts_on_current_step = 0
    prev_sop_step: int | None = None

    # Start ESC listener
    _stop_event.clear()
    esc_listener = _start_esc_listener()

    print(f"\nStarting execution (up to {max_steps} steps)...")
    print("Press ESC at any time to stop.\n")

    for step_num in range(1, max_steps + 1):
        # Check for ESC
        if _stop_event.is_set():
            print("\n⏹ Stopped by ESC key.")
            break

        # --- OBSERVE ---
        # Detect display at cursor each step (handles multi-display & display switches)
        display_info = _get_display_info()
        screen_w = display_info["width"]
        screen_h = display_info["height"]
        display_origin = (display_info["origin_x"], display_info["origin_y"])
        logger.info(
            f"Step {step_num}: Display {display_info['display_id']} "
            f"({screen_w}x{screen_h} @ origin {display_origin})"
        )

        screenshot_path = str(screenshots_dir / f"step_{step_num:03d}.png")
        take_screenshot(screenshot_path, display_id=display_info["display_id"])

        active_app_name = _get_active_app_name()
        logger.info(f"Step {step_num}: Active app = {active_app_name}")

        # Run element detection (outputs saved to detector/ dir)
        elements = []
        annotated_path = None
        try:
            annotated_out = str(detector_dir / f"step_{step_num:03d}_annotated.png")
            elements, annotated_path = detect_elements(
                screenshot_path, screen_w, screen_h, annotated_out
            )
            logger.info(f"Step {step_num}: {detector_label} detected {len(elements)} elements")

            # Save elements JSON alongside the annotated image
            elements_json_path = detector_dir / f"step_{step_num:03d}_elements.json"
            with open(elements_json_path, "w") as f:
                json.dump({
                    "step": step_num,
                    "screenshot": screenshot_path,
                    "display": display_info,
                    "element_count": len(elements),
                    "elements": elements,
                }, f, indent=2)
        except Exception as e:
            logger.warning(f"Step {step_num}: {detector_label} detection failed: {e}")

        # --- THINK ---
        messages = build_execution_message(
            sop_text=sop_text,
            current_screenshot_path=screenshot_path,
            screen_width=screen_w,
            screen_height=screen_h,
            active_app_name=active_app_name,
            action_history=action_history,
            current_step_hint=last_sop_step,
            elements=elements,
            annotated_screenshot_path=annotated_path,
        )

        error = None
        action_dsl = ""
        rationale = ""
        expected_outcome = ""
        sop_step = None
        is_completed = False

        try:
            raw_response = call_openai(messages)
            logger.debug(f"Model response: {raw_response}")
            try:
                parsed = _parse_model_response(raw_response)
            except json.JSONDecodeError:
                # Retry once: ask the model to fix its JSON
                logger.warning(f"Step {step_num}: Invalid JSON, retrying...")
                retry_messages = messages + [
                    {"role": "assistant", "content": raw_response},
                    {"role": "user", "content": "Your response was not valid JSON. Please respond with ONLY a valid JSON object, no markdown fences or extra text."},
                ]
                raw_response = call_openai(retry_messages)
                parsed = _parse_model_response(raw_response)

            action_dsl = parsed.get("action", "")
            rationale = parsed.get("action_rationale", "")
            expected_outcome = parsed.get("action_expected_outcome", "")
            sop_step = parsed.get("current_sop_step")
            is_completed = parsed.get("is_completed", False)

            if sop_step is not None:
                last_sop_step = sop_step

                # Track consecutive attempts on the same SOP step
                if sop_step == prev_sop_step:
                    attempts_on_current_step += 1
                else:
                    attempts_on_current_step = 1
                    prev_sop_step = sop_step

        except (json.JSONDecodeError, KeyError) as e:
            error = f"Parse error: {e}"
            logger.warning(f"Step {step_num}: {error}")
        except Exception as e:
            error = f"API error: {e}"
            logger.warning(f"Step {step_num}: {error}")

        # Log step
        record = StepRecord(
            step_number=step_num,
            screenshot_path=screenshot_path,
            active_app=active_app_name,
            model_action=action_dsl,
            model_rationale=rationale,
            current_sop_step=sop_step,
            is_completed=is_completed,
            error=error,
        )
        log.steps.append(record)

        # Print progress
        step_info = f"[Step {step_num}/{max_steps}]"
        if sop_step:
            step_info += f" (SOP step {sop_step})"
        if is_completed:
            print(f"{step_info} ✓ SOP completed!")
        elif error:
            print(f"{step_info} ✗ {error}")
        else:
            print(f"{step_info} {action_dsl}  — {rationale}")

        # Check completion
        if is_completed:
            log.completed_successfully = True
            break

        # Check if stuck on the same step
        if attempts_on_current_step >= max_attempts_per_step:
            print(f"\n⚠ Stuck on SOP step {prev_sop_step} after {max_attempts_per_step} attempts. Stopping early.")
            log.stuck_on_step = prev_sop_step
            break

        # --- ACT ---
        action_type = None
        if action_dsl and not error:
            try:
                # Check for CLICK_ELEMENT(id) first
                elem_match = re.match(r"CLICK_ELEMENT\(\s*(\d+)\s*\)", action_dsl)
                if elem_match:
                    elem_id = int(elem_match.group(1))
                    target_el = next((e for e in elements if e["id"] == elem_id), None)
                    if target_el is None:
                        record.error = f"Element {elem_id} not found in detected elements"
                        logger.warning(f"Step {step_num}: {record.error}")
                        print(f"  ↳ {record.error}")
                    else:
                        ex, ey = target_el["center_points"]
                        action_type = execute_action(
                            f"CLICK({ex}, {ey})",
                            display_origin=display_origin,
                        )
                else:
                    action_type = execute_action(action_dsl, display_origin=display_origin)
            except Exception as e:
                record.error = f"Execution error: {e}"
                logger.warning(f"Step {step_num}: {record.error}")
                print(f"  ↳ Action failed: {e}")

        # Track history for next iteration
        action_history.append({
            "step_number": step_num,
            "action": action_dsl,
            "rationale": rationale,
            "expected_outcome": expected_outcome,
            "error": record.error,
        })

        # --- DELAY (two-phase: shorter for MOVE_MOUSE, full for others) ---
        if action_type == "MOVE_MOUSE":
            time.sleep(0.3)  # Just need a fresh screenshot with cursor
        elif action_type == "WAIT":
            pass  # Wait IS the delay, don't add more
        else:
            time.sleep(delay)  # UI needs to settle

    # Clean up listener
    esc_listener.stop()

    # Save log
    log_path = output_dir / "execution_log.json"
    log.save(log_path)
    print(f"\nExecution finished. Log saved to: {log_path}")
    print(f"Screenshots: {screenshots_dir}")

    if not log.completed_successfully:
        print(f"⚠ SOP was NOT completed within {max_steps} steps.")

    return log
