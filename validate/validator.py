"""Core validation logic: sends execution data + final screenshot to GPT-4o."""

import json
import logging
from pathlib import Path

from sop.api_client import call_openai
from sop.data_loader import encode_screenshot_base64
from validate.prompts import VALIDATION_PROMPT

logger = logging.getLogger(__name__)


def _build_execution_summary(execution_log: dict) -> str:
    """Build a text summary of the execution from the log dict."""
    lines = []
    for step in execution_log.get("steps", []):
        status = "OK" if not step.get("error") else f"ERROR: {step['error']}"
        sop_step = step.get("current_sop_step", "?")
        action = step.get("model_action", "")
        rationale = step.get("model_rationale", "")
        completed = step.get("is_completed", False)
        line = f"  Step {step['step_number']} (SOP step {sop_step}): {action} -- {rationale} [{status}]"
        if completed:
            line += " [MARKED COMPLETE]"
        lines.append(line)
    return "\n".join(lines) if lines else "(no steps recorded)"


def _find_final_screenshot(execution_log: dict, execution_dir: Path) -> Path | None:
    """Find the last screenshot from the execution."""
    steps = execution_log.get("steps", [])
    if not steps:
        return None
    last_step = steps[-1]
    screenshot_path = Path(last_step.get("screenshot_path", ""))
    if screenshot_path.exists():
        return screenshot_path
    # Try relative to execution dir
    screenshots_dir = execution_dir / "execution_screenshots"
    if screenshots_dir.exists():
        pngs = sorted(screenshots_dir.glob("*.png"))
        if pngs:
            return pngs[-1]
    return None


def validate_execution(
    execution_log: dict,
    execution_dir: Path,
    final_screenshot_path: Path | None = None,
    stuck_on_step: int | None = None,
) -> dict:
    """Validate whether an executed SOP achieved its goal.

    Args:
        execution_log: Parsed execution_log.json dict.
        execution_dir: Path to the execution output directory.
        final_screenshot_path: Override path to the final screenshot.
        stuck_on_step: SOP step number where the agent got stuck (if any).

    Returns:
        Dict with "thinking", "was_completed", "failed_step", "failure_reason".
    """
    intent = execution_log.get("intent", "")
    sop_text = execution_log.get("sop_text", "")
    execution_summary = _build_execution_summary(execution_log)

    # Use stuck_on_step from arg or from the log itself
    if stuck_on_step is None:
        stuck_on_step = execution_log.get("stuck_on_step")

    stuck_info = ""
    if stuck_on_step is not None:
        stuck_info = (
            f"\n## Agent Stuck Info\n"
            f"The execution agent got stuck on SOP step {stuck_on_step} "
            f"and was unable to advance past it after multiple attempts.\n\n"
        )

    if not final_screenshot_path:
        final_screenshot_path = _find_final_screenshot(execution_log, execution_dir)

    prompt_text = VALIDATION_PROMPT.format(
        intent=intent or "(not specified)",
        sop_text=sop_text,
        execution_summary=execution_summary,
        stuck_info=stuck_info,
    )

    # Build message with screenshot
    content_parts = [{"type": "text", "text": prompt_text}]

    if final_screenshot_path and final_screenshot_path.exists():
        b64 = encode_screenshot_base64(final_screenshot_path)
        content_parts.append({
            "type": "text",
            "text": "\n## Final Screenshot\n",
        })
        content_parts.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:image/png;base64,{b64}",
                "detail": "high",
            },
        })
    else:
        logger.warning("No final screenshot found for validation")

    messages = [{"role": "user", "content": content_parts}]

    raw_response = call_openai(messages)

    # Parse response
    text = raw_response.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines)

    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        logger.error(f"Failed to parse validation response: {text}")
        result = {"thinking": text, "was_completed": False}

    return result
