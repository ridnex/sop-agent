"""Group generation: ask GPT N times for the same intent (parallel calls).

Returns N candidate SOPs ready to feed into consensus.rank_group(). High
sampling temperature keeps the group diverse so consensus has signal.

Used by the `group_RL` pipeline as the "fresh-generation" branch — no
retrieval exemplar yet (that comes in Step 6).
"""

from __future__ import annotations

import platform as _platform
from concurrent.futures import ThreadPoolExecutor

from sop.api_client import call_openai

DEFAULT_GROUP_SIZE = 5
DEFAULT_TEMPERATURE = 0.8
DEFAULT_MODEL = "gpt-4o"


def _host_os_hint(platform_name: str | None = None) -> str:
    """Short OS + keyboard-modifier directive appended to the prompt."""
    system = (platform_name or _platform.system()).lower()
    if system in ("darwin", "mac", "macos", "osx"):
        os_name, mod = "macOS", "Cmd"
    elif system in ("windows", "win", "win32"):
        os_name, mod = "Windows", "Ctrl"
    elif system == "linux":
        os_name, mod = "Linux", "Ctrl"
    else:
        os_name, mod = system or "Unknown", "Ctrl"
    return (
        f"\n# Host OS\n"
        f"The SOP will execute on **{os_name}**. Use **{mod}** as the keyboard "
        f"modifier (e.g. {mod.lower()}+l to focus the address bar). Be "
        f"consistent — do NOT mix Cmd and Ctrl in the same SOP."
    )


_PROMPT_TEMPLATE = """You are writing a Standard Operating Procedure (SOP) for an AI agent that will follow it inside a Chrome browser.

# Task
{intent}

# How to write the SOP
- Output a numbered list of concrete steps. Start each line with a number and a period (e.g. "1.", "2.", ...).
- Reference UI elements by their visible label or role (e.g. 'the "Compose" button', 'the search input field'). Do NOT use pixel coordinates.
- Always include the initial navigation step (which URL to open) at the start.
- If the task requires being signed in, include a step like "wait for me to log in" so the human can authenticate.
- End with a verification or confirmation step where applicable.
- Keep the SOP between 4 and 12 steps.
- Output ONLY the numbered list — no preamble, no explanation, no markdown code fences.
{os_note}

Write the SOP now."""


def _strip_markdown_fences(text: str) -> str:
    """Remove leading/trailing ```...``` fences sometimes emitted by GPT."""
    text = text.strip()
    if text.startswith("```"):
        nl = text.find("\n")
        text = text[nl + 1:] if nl != -1 else ""
    if text.endswith("```"):
        nl = text.rfind("\n")
        text = text[:nl] if nl != -1 else ""
    return text.strip()


def _one_call(intent: str, model: str, temperature: float) -> str:
    prompt = _PROMPT_TEMPLATE.format(intent=intent, os_note=_host_os_hint())
    messages = [{"role": "user", "content": prompt}]
    raw = call_openai(messages, model=model, temperature=temperature)
    return _strip_markdown_fences(raw)


def generate_group(
    intent: str,
    n: int = DEFAULT_GROUP_SIZE,
    temperature: float = DEFAULT_TEMPERATURE,
    model: str = DEFAULT_MODEL,
) -> list[str]:
    """Sample N candidate SOPs for `intent` via parallel GPT calls.

    Returns N stripped SOP strings ready to feed to rank_group(). Wall time
    is ~one API call regardless of N. With n <= 0, returns [].
    """
    if n <= 0:
        return []
    with ThreadPoolExecutor(max_workers=n) as pool:
        futures = [
            pool.submit(_one_call, intent, model, temperature)
            for _ in range(n)
        ]
        return [f.result() for f in futures]
