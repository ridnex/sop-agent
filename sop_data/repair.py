"""Claude-based SOP repair: given a failed SOP + failure info, return a repaired SOP."""

import base64
import logging
import platform as _platform
import re
from pathlib import Path

import anthropic

from sop.prompts import prompt__fix_sop
from validate.validator import _build_execution_summary
from web.execute.config import ANTHROPIC_API_KEY, MODEL

logger = logging.getLogger(__name__)

_REPAIR_MAX_TOKENS = 4096

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


def _extract_step_text(old_sop: str, step_num: int) -> str:
    pattern = re.compile(rf"^\s*{step_num}\.\s*(.*)$")
    for line in old_sop.splitlines():
        m = pattern.match(line)
        if m:
            return m.group(1).strip()
    return ""


def _host_os_note(platform_name: str | None = None) -> str:
    """Return a stable host-OS + keyboard-consistency directive to append to repair prompts."""
    system = (platform_name or _platform.system()).lower()
    if system in ("darwin", "mac", "macos", "osx"):
        os_name, mod = "macOS", "Cmd"
    elif system in ("windows", "win", "win32"):
        os_name, mod = "Windows", "Ctrl"
    elif system == "linux":
        os_name, mod = "Linux", "Ctrl"
    else:
        os_name, mod = system or "Unknown", "Ctrl"

    return f"""

## Host OS (important)

The original execution ran on **{os_name}**, and the rewritten SOP will also execute on **{os_name}**.

Keyboard shortcuts in this environment use **{mod}** as the primary modifier. When the action trace or failure reason shows a keystroke such as `{mod.lower()}+L` or `{mod.lower()}+A`, preserve that exact modifier when you reference it in the rewritten SOP. Do NOT translate `cmd` → `ctrl` or vice versa, and do NOT mix the two in the same SOP — stay consistent with `{mod}`.

Being explicit about keystrokes is welcomed: prefer "Press {mod}+L to focus the address bar, then type 'gmail.com' and press Return" over a vague "navigate to Gmail". Just make sure every shortcut you write uses `{mod}` and matches how the trace actually performed that action."""


def claude_repair_sop(
    old_sop: str,
    failed_step: int,
    failure_reason: str,
    screenshot_path: Path | None = None,
    model: str | None = None,
) -> str:
    """Ask Claude to rewrite an SOP that failed at a given step.

    Returns the raw repaired SOP text (stripped).
    """
    failed_step_text = _extract_step_text(old_sop, failed_step)
    prompt_text = prompt__fix_sop(old_sop, failed_step, failed_step_text, failure_reason) + _host_os_note()

    content: list[dict] = [{"type": "text", "text": prompt_text}]

    if screenshot_path and screenshot_path.exists():
        img_b64 = base64.standard_b64encode(screenshot_path.read_bytes()).decode("ascii")
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": img_b64,
            },
        })
    elif screenshot_path:
        logger.warning("Repair screenshot not found at %s", screenshot_path)

    client = _get_client()
    response = client.messages.create(
        model=model or MODEL,
        max_tokens=_REPAIR_MAX_TOKENS,
        messages=[{"role": "user", "content": content}],
    )

    text_parts = [block.text for block in response.content if block.type == "text"]
    return "\n".join(text_parts).strip()


_REWRITE_PROMPT = """# Task
You previously generated this SOP (Standard Operating Procedure):

```
{old_sop}
```

An AI agent executed this SOP and eventually **completed** the task, but struggled along the way at step {struggle_step}. The following issues were detected:
{signals_block}

Here is the actual action trace the agent took (in order):

```
{trace}
```

# Instructions

Rewrite the SOP so that it matches what actually worked, not what was originally written. The original SOP was under-specified, ambiguous, or subtly wrong at step {struggle_step}, and the trace above shows the real sequence the agent had to improvise to succeed.

- Keep the number and purpose of steps roughly the same.
- Replace ambiguous or wrong UI-element references with the precise ones the agent ended up interacting with.
- Remove steps the agent had to skip or that turned out to be unnecessary; add any steps the agent had to invent to get unstuck.
- Produce a clean, imitable procedure — NO meta-commentary like "the agent struggled" or "originally this said". Write it as if it were the first draft.
- Describe each UI element by its visual label, name, or role (e.g. "the 'Save' button", "the search text field"). Do NOT use pixel coordinates or screen positions.

Please write the complete rewritten SOP below:"""


def claude_rewrite_from_trace(
    old_sop: str,
    execution_log: dict,
    struggle_step: int,
    struggle_signals: list[str],
    screenshot_path: Path | None = None,
    model: str | None = None,
) -> str:
    """Ask Claude to rewrite an SOP that was completed but had a messy trajectory.

    Returns the raw rewritten SOP text (stripped).
    """
    trace = _build_execution_summary(execution_log)
    signals_block = "\n".join(f"- {s}" for s in struggle_signals) if struggle_signals else "- (none recorded)"
    prompt_text = _REWRITE_PROMPT.format(
        old_sop=old_sop,
        struggle_step=struggle_step,
        signals_block=signals_block,
        trace=trace,
    ) + _host_os_note()

    content: list[dict] = [{"type": "text", "text": prompt_text}]

    if screenshot_path and screenshot_path.exists():
        img_b64 = base64.standard_b64encode(screenshot_path.read_bytes()).decode("ascii")
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": img_b64,
            },
        })
    elif screenshot_path:
        logger.warning("Rewrite screenshot not found at %s", screenshot_path)

    client = _get_client()
    response = client.messages.create(
        model=model or MODEL,
        max_tokens=_REPAIR_MAX_TOKENS,
        messages=[{"role": "user", "content": content}],
    )

    text_parts = [block.text for block in response.content if block.type == "text"]
    return "\n".join(text_parts).strip()
