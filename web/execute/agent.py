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

from sop.methods import _extract_step_text, replace_step, repair_step
from web.execute.api_client import call_claude
from web.execute.browser import BrowserController
from web.execute.config import (
    BROWSER_WIDTH,
    BROWSER_HEIGHT,
    MAX_ATTEMPTS_BEFORE_REPAIR,
    MAX_REPAIRS_PER_RUN,
    REPAIRED_SOPS_DIR,
)
from web.execute.models import ExecutionLog, StepRecord, RepairRecord
from web.execute.prompts import (
    build_system_prompt,
    build_stuck_message,
    build_post_repair_message,
    parse_last_step_tag,
    parse_step_repair,
    SOP_COMPLETED_SENTINEL,
)

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


def _allocate_repair_path(sop_file: Path | None, fallback_name: str) -> Path:
    """Find the next free <sop_id>__repair_N.txt path under REPAIRED_SOPS_DIR.

    If sop_file is provided, repair file sits next to the original.
    Otherwise it goes into REPAIRED_SOPS_DIR using fallback_name as the base id.
    """
    if sop_file is not None and sop_file.exists():
        base_dir = sop_file.parent
        base_id = sop_file.stem
    else:
        base_dir = REPAIRED_SOPS_DIR
        base_id = fallback_name

    base_dir.mkdir(parents=True, exist_ok=True)
    n = 1
    while True:
        candidate = base_dir / f"{base_id}__repair_{n}.txt"
        if not candidate.exists():
            return candidate
        n += 1


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
    sop_file: Path | None = None,
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

    original_sop_text = sop_text  # preserve for later diff / save-as-repair
    log = ExecutionLog(sop_text=original_sop_text, intent=intent, start_url=start_url)
    step_num = 0

    # Step-tracking state for inline repair
    current_sop_step: int | None = None
    attempts_on_current_step = 0

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

            # Parse STEP N: tag and update attempts counter
            tagged_step = parse_last_step_tag(full_text)
            if tagged_step is not None:
                if tagged_step == current_sop_step:
                    attempts_on_current_step += 1
                else:
                    current_sop_step = tagged_step
                    attempts_on_current_step = 1

            # Stuck check — trigger repair BEFORE executing the pending tool calls
            if (
                current_sop_step is not None
                and attempts_on_current_step > MAX_ATTEMPTS_BEFORE_REPAIR
            ):
                if len(log.repairs) >= MAX_REPAIRS_PER_RUN:
                    print(
                        f"\nRepair cap ({MAX_REPAIRS_PER_RUN}) reached on SOP step "
                        f"{current_sop_step}. Stopping."
                    )
                    log.stuck_on_step = current_sop_step
                    # Discard the pending tool_use response (never executed)
                    messages.pop()
                    break

                original_step_text = _extract_step_text(sop_text, current_sop_step)
                last_screenshot = Path(log.steps[-1].screenshot_path) if (
                    log.steps and log.steps[-1].screenshot_path
                ) else None

                print(
                    f"\n[Repair] Stuck on SOP step {current_sop_step} after "
                    f"{attempts_on_current_step - 1} attempts. Asking for replacement..."
                )

                # Drop the pending tool_use response — we won't execute it
                messages.pop()

                # Ask Claude for a STEP_REPAIR line
                stuck_msg = build_stuck_message(
                    current_sop_step, original_step_text, attempts_on_current_step - 1
                )
                messages.append({"role": "user", "content": stuck_msg})

                try:
                    repair_response = call_claude(
                        messages=messages, system=system_prompt, model=model
                    )
                    repair_content = _serialize_response_content(repair_response)
                    messages.append({"role": "assistant", "content": repair_content})
                    repair_text = " ".join(
                        b.text for b in repair_response.content if b.type == "text"
                    )
                    parsed = parse_step_repair(repair_text)
                except Exception as e:
                    logger.warning(f"Claude repair call failed: {e}")
                    parsed = None

                new_step_text: str | None = None
                if parsed is not None:
                    _, new_step_text = parsed

                # Fallback to GPT-4o if Claude didn't produce a usable STEP_REPAIR
                if not new_step_text:
                    try:
                        failure_reason = (
                            f"Claude made {attempts_on_current_step - 1} attempts on this "
                            f"step without visible progress."
                        )
                        new_step_text = repair_step(
                            sop_text=sop_text,
                            failed_step=current_sop_step,
                            failure_reason=failure_reason,
                            screenshot_path=last_screenshot,
                        )
                    except Exception as e:
                        logger.warning(f"GPT-4o repair fallback failed: {e}")
                        log.stuck_on_step = current_sop_step
                        break

                # Apply the repair in memory
                try:
                    patched_sop = replace_step(sop_text, current_sop_step, new_step_text)
                except ValueError as e:
                    logger.warning(f"Could not apply repair: {e}")
                    log.stuck_on_step = current_sop_step
                    break

                log.repairs.append(RepairRecord(
                    step_number=current_sop_step,
                    original_text=original_step_text,
                    new_text=new_step_text,
                    failure_screenshot_path=str(last_screenshot or ""),
                    attempt_count=attempts_on_current_step - 1,
                    at_execution_step=step_num,
                ))

                sop_text = patched_sop
                system_prompt = build_system_prompt(
                    sop_text, BROWSER_WIDTH, BROWSER_HEIGHT
                )
                messages.append({
                    "role": "user",
                    "content": build_post_repair_message(
                        current_sop_step, new_step_text, sop_text
                    ),
                })

                print(
                    f"[Repair {len(log.repairs)}] Step {current_sop_step} rewritten: "
                    f"{new_step_text[:100]}"
                )

                attempts_on_current_step = 0
                continue

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

    # Save repaired SOP if any repairs happened
    if log.repairs:
        log.effective_sop_text = sop_text
        try:
            repair_path = _allocate_repair_path(
                sop_file,
                fallback_name=output_dir.name.replace("exec_", "").rsplit("_", 5)[0] or "sop",
            )
            repair_path.write_text(sop_text, encoding="utf-8")
            log.repaired_sop_path = str(repair_path)
            print(f"Repaired SOP saved to: {repair_path}")
        except Exception as e:
            logger.warning(f"Failed to save repaired SOP: {e}")

    # Save log
    log_path = output_dir / "execution_log.json"
    log.save(log_path)
    print(f"\nExecution finished. Log saved to: {log_path}")
    print(f"Screenshots: {screenshots_dir}")

    if not log.completed_successfully:
        print(f"SOP was NOT completed within {max_steps} steps.")

    return log
