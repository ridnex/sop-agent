import re
from pathlib import Path

from sop.api_client import call_openai
from sop.data_loader import Experiment, encode_screenshot_base64
from sop.action_formatter import format_action_dsl
from sop.prompts import prompt__td, prompt__td_kf, prompt__td_kf_act_intro, prompt__td_kf_act_close, prompt__fix_sop, prompt__repair_step


def build_messages_wd(exp: Experiment) -> list[dict]:
    """Method 1: Workflow Description only (text-only)."""
    prompt_text = prompt__td(exp.intent, exp.ui_name)
    return [{"role": "user", "content": prompt_text}]


def build_messages_wd_kf(exp: Experiment) -> list[dict]:
    """Method 2: Workflow Description + Key Frames (screenshots)."""
    prompt_text = prompt__td_kf(exp.intent, exp.ui_name)

    content = [{"type": "text", "text": prompt_text}]

    for state in exp.states:
        if state.screenshot_path and state.screenshot_path.exists():
            b64 = encode_screenshot_base64(state.screenshot_path)
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{b64}",
                    "detail": "high",
                },
            })

    return [{"role": "user", "content": content}]


def build_messages_wd_kf_act(exp: Experiment) -> list[dict]:
    """Method 3: Workflow Description + Key Frames + Actions (interleaved)."""
    content = []

    # Intro text
    intro_text = prompt__td_kf_act_intro(exp.intent, exp.ui_name)
    content.append({"type": "text", "text": intro_text})

    # Interleave: state[0], action[0], state[1], action[1], ..., state[N]
    # N states, N-1 actions
    for i, state in enumerate(exp.states):
        # Add screenshot
        if state.screenshot_path and state.screenshot_path.exists():
            b64 = encode_screenshot_base64(state.screenshot_path)
            content.append({"type": "text", "text": f"[Screenshot {i}]"})
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{b64}",
                    "detail": "high",
                },
            })

        # Add action after this state (if there is one)
        if i < len(exp.actions):
            dsl = format_action_dsl(exp.actions[i])
            content.append({"type": "text", "text": f"Action: {dsl}"})

    # Closing text
    close_text = prompt__td_kf_act_close()
    content.append({"type": "text", "text": close_text})

    return [{"role": "user", "content": content}]


METHODS = {
    "wd": build_messages_wd,
    "wd_kf": build_messages_wd_kf,
    "wd_kf_act": build_messages_wd_kf_act,
}

# Methods that require screenshots
VISION_METHODS = {"wd_kf", "wd_kf_act"}


def _extract_step_text(sop_text: str, step_number: int) -> str:
    """Extract the text of a specific numbered step from an SOP."""
    pattern = rf"^{step_number}\.\s+(.+?)(?=\n\d+\.|\Z)"
    match = re.search(pattern, sop_text, re.MULTILINE | re.DOTALL)
    return match.group(0).strip() if match else f"(step {step_number} text not found)"


def _strip_step_number_prefix(text: str, step_number: int) -> str:
    """Strip a leading 'N.' prefix if present — the model sometimes ignores instructions."""
    text = text.strip()
    pattern = rf"^{step_number}\.\s+"
    return re.sub(pattern, "", text, count=1).strip()


def replace_step(sop_text: str, step_number: int, new_step_text: str) -> str:
    """Replace step N's body with new_step_text, leaving numbering and other steps intact.

    Returns the patched SOP text.
    """
    new_body = _strip_step_number_prefix(new_step_text, step_number)
    pattern = rf"(^{step_number}\.\s+)(.+?)(?=\n\d+\.|\Z)"

    def _sub(match: re.Match) -> str:
        return f"{match.group(1)}{new_body}"

    patched, n = re.subn(pattern, _sub, sop_text, count=1, flags=re.MULTILINE | re.DOTALL)
    if n == 0:
        raise ValueError(f"Could not locate step {step_number} to replace in SOP")
    return patched


def repair_step(
    old_sop: str,
    failed_step: int,
    failure_reason: str,
    screenshot_path: Path | None = None,
) -> str:
    """Ask the model to rewrite ONLY the given step.

    Returns the replacement body for step `failed_step` (no leading "N." prefix).
    Raises RuntimeError if the model returns empty text.
    """
    failed_step_text = _extract_step_text(old_sop, failed_step)

    prompt_text = prompt__repair_step(
        old_sop=old_sop,
        failed_step=failed_step,
        failed_step_text=failed_step_text,
        failure_reason=failure_reason,
    )

    content = [{"type": "text", "text": prompt_text}]

    if screenshot_path and screenshot_path.exists():
        b64 = encode_screenshot_base64(screenshot_path)
        content.append({"type": "text", "text": "\n## Screenshot at the stuck step\n"})
        content.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:image/png;base64,{b64}",
                "detail": "high",
            },
        })

    messages = [{"role": "user", "content": content}]
    raw = call_openai(messages).strip()
    if not raw:
        raise RuntimeError("repair_step: model returned empty text")

    return _strip_step_number_prefix(raw, failed_step)


def regenerate_sop(
    old_sop: str,
    failed_step: int,
    failure_reason: str,
    screenshot_path: Path | None = None,
) -> str:
    """Regenerate an SOP by fixing it from the failed step onward.

    Args:
        old_sop: The original SOP text.
        failed_step: The step number where execution failed.
        failure_reason: Why the step failed.
        screenshot_path: Optional screenshot of the failure state.

    Returns:
        The new SOP text.
    """
    failed_step_text = _extract_step_text(old_sop, failed_step)

    prompt_text = prompt__fix_sop(
        old_sop=old_sop,
        failed_step=failed_step,
        failed_step_text=failed_step_text,
        failure_reason=failure_reason,
    )

    content = [{"type": "text", "text": prompt_text}]

    if screenshot_path and screenshot_path.exists():
        b64 = encode_screenshot_base64(screenshot_path)
        content.append({
            "type": "text",
            "text": "\n## Screenshot at failure point\n",
        })
        content.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:image/png;base64,{b64}",
                "detail": "high",
            },
        })

    messages = [{"role": "user", "content": content}]
    return call_openai(messages)
