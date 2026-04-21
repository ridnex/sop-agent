"""Core execution loop: Claude drives via Computer Use tool requests.

Unlike the existing execute/agent.py (our code drives observe→think→act),
here Claude drives the loop — it requests tools and we execute them.
"""

import base64 as b64
import logging
import threading
import time
from pathlib import Path

from pynput import keyboard

from web.execute.api_client import call_claude
from web.execute.browser import BrowserController
from web.execute.config import BROWSER_WIDTH, BROWSER_HEIGHT
from web.execute.models import ExecutionLog, StepRecord
from web.execute.prompts import build_system_prompt, SOP_COMPLETED_SENTINEL

logger = logging.getLogger(__name__)

# --- ESC stop mechanism (reused pattern from existing agent.py) ---
_stop_event = threading.Event()


def _start_esc_listener() -> keyboard.Listener:
    """Start a background listener that sets _stop_event when ESC is pressed."""
    def on_press(key):
        if key == keyboard.Key.esc:
            _stop_event.set()
            return False

    listener = keyboard.Listener(on_press=on_press)
    listener.daemon = True
    listener.start()
    return listener


def _make_tool_result(result: dict, tool_use_id: str) -> dict:
    """Format an action result as an API tool_result block."""
    content = []
    is_error = False

    if result.get("error"):
        is_error = True
        content = result["error"]
    else:
        if result.get("output"):
            content.append({"type": "text", "text": result["output"]})
        if result.get("base64_image"):
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": result["base64_image"],
                },
            })

    return {
        "type": "tool_result",
        "content": content,
        "tool_use_id": tool_use_id,
        "is_error": is_error,
    }


def _serialize_response_content(response) -> list[dict]:
    """Convert response content blocks to serializable dicts for message history."""
    parts = []
    for block in response.content:
        if block.type == "text":
            parts.append({"type": "text", "text": block.text})
        elif block.type == "tool_use":
            parts.append(block.model_dump())
    return parts


def run_agent(
    sop_text: str,
    output_dir: Path,
    intent: str = "",
    start_url: str = "about:blank",
    max_steps: int = 50,
    delay: float = 2.0,
    auto_confirm: bool = False,
    headless: bool = False,
    launch: bool = False,
    model: str | None = None,
) -> ExecutionLog:
    """Run the web SOP execution agent.

    Claude drives the loop via Computer Use tool requests.
    We execute actions via CDP and return results.

    Args:
        sop_text: The SOP text to execute.
        output_dir: Directory to save screenshots and logs.
        intent: High-level intent/goal.
        start_url: Initial URL (default about:blank — SOP has navigation).
        max_steps: Maximum tool-use round-trips.
        delay: Seconds after each action for page settling.
        auto_confirm: If False, prompt user before starting.
        headless: Run browser without visible window (launch mode only).
        launch: If True, launch a new browser. If False (default),
                connect to existing Chrome via CDP.
        model: Claude model override.

    Returns:
        ExecutionLog with all step records.
    """
    if not auto_confirm:
        print("\n" + "=" * 60)
        print("WEB SOP EXECUTION AGENT (Claude Computer Use)")
        print("=" * 60)
        print(f"\nIntent: {intent or '(not specified)'}")
        print(f"Start URL: {start_url}")
        print(f"Max steps: {max_steps}")
        print(f"Delay: {delay}s")
        print(f"\nSOP:\n{sop_text[:500]}{'...' if len(sop_text) > 500 else ''}")
        print("\n" + "-" * 60)
        print("SAFETY: Press ESC to abort at any time")
        print("-" * 60)
        confirm = input("\nStart execution? [y/N] ").strip().lower()
        if confirm != "y":
            print("Aborted.")
            return ExecutionLog(sop_text=sop_text, intent=intent, start_url=start_url)

    # Prepare output dirs
    screenshots_dir = output_dir / "execution_screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)

    log = ExecutionLog(sop_text=sop_text, intent=intent, start_url=start_url)
    step_num = 0

    # Start ESC listener
    _stop_event.clear()
    esc_listener = _start_esc_listener()

    # Start browser
    browser = BrowserController(headless=headless, launch=launch)
    browser.start(start_url=start_url)

    # Build system prompt and initial message
    system_prompt = build_system_prompt(sop_text, BROWSER_WIDTH, BROWSER_HEIGHT)
    initial_message = "Please begin executing the SOP. Start by taking a screenshot to see the current browser state."
    if intent:
        initial_message += f"\n\nGoal: {intent}"

    messages: list[dict] = [
        {"role": "user", "content": initial_message},
    ]

    print(f"\nStarting web execution (up to {max_steps} steps)...")
    print("Press ESC at any time to stop.\n")

    try:
        while step_num < max_steps:
            # Check for ESC
            if _stop_event.is_set():
                print("\nStopped by ESC key.")
                break

            # Call Claude
            response = call_claude(
                messages=messages,
                system=system_prompt,
                model=model,
            )

            # Append assistant response to conversation
            assistant_content = _serialize_response_content(response)
            messages.append({"role": "assistant", "content": assistant_content})

            # Extract text content and check for completion
            text_parts = [b.text for b in response.content if b.type == "text"]
            full_text = " ".join(text_parts)

            if response.stop_reason != "tool_use":
                # No more tool calls — check if completed
                if SOP_COMPLETED_SENTINEL in full_text:
                    print(f"\n[Step {step_num}] SOP completed!")
                    log.completed_successfully = True
                else:
                    print(f"\n[Step {step_num}] Claude stopped without completing SOP.")
                    if full_text.strip():
                        print(f"  Claude says: {full_text[:200]}")
                break

            # Execute each tool_use block
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue

                step_num += 1
                if step_num > max_steps:
                    break

                action = block.input.get("action", "unknown")
                coord = block.input.get("coordinate", "")
                text = block.input.get("text", "")

                # Log action description
                action_desc = action
                if coord:
                    action_desc += f" at ({coord[0]}, {coord[1]})"
                if text and action in ("type", "key"):
                    action_desc += f": {text[:50]}"

                print(f"[Step {step_num}/{max_steps}] {action_desc}")

                # Execute action via CDP
                result = browser.execute_action(block.input)

                # Save screenshot if we got one
                screenshot_path = ""
                if result.get("base64_image"):
                    screenshot_path = str(screenshots_dir / f"step_{step_num:03d}.png")
                    with open(screenshot_path, "wb") as f:
                        f.write(b64.b64decode(result["base64_image"]))

                # Log step
                record = StepRecord(
                    step_number=step_num,
                    screenshot_path=screenshot_path,
                    page_url=browser.current_url,
                    model_action=action_desc,
                    model_rationale=full_text[:200] if full_text else "",
                    error=result.get("error"),
                )
                log.steps.append(record)

                if result.get("error"):
                    print(f"  Error: {result['error']}")

                # Build tool result
                tool_results.append(_make_tool_result(result, block.id))

                # Delay after non-screenshot actions for page settling
                if action != "screenshot":
                    time.sleep(delay)

            if not tool_results:
                break

            # Return results to Claude
            messages.append({"role": "user", "content": tool_results})

    finally:
        # Clean up
        esc_listener.stop()
        browser.stop()

    # Save log
    log_path = output_dir / "execution_log.json"
    log.save(log_path)
    print(f"\nExecution finished. Log saved to: {log_path}")
    print(f"Screenshots: {screenshots_dir}")

    if not log.completed_successfully:
        print(f"SOP was NOT completed within {max_steps} steps.")

    return log
