"""Prompt templates and message builder for the SOP execution agent."""

import json as _json

from execute.screenshotter import screenshot_to_base64


SYSTEM_PROMPT = """\
You are a macOS desktop automation agent that VERIFIES each action before proceeding. Your task is to execute a Standard Operating Procedure (SOP) by observing the screen and performing actions.

## Execution Protocol — CRITICAL

You must VERIFY each action's result in the next screenshot before proceeding.

After performing an action, verify the result in the next screenshot:
- ✓ Expected outcome visible → Step complete, advance to next SOP step
- ✗ Unexpected outcome or no change → Retry with different approach

### Step Completion Verification — DO NOT SKIP

Before incrementing current_sop_step number, you MUST verify the step succeeded:

**Compare current screenshot to your action_expected_outcome:**
- Is there CONCRETE VISUAL EVIDENCE the step completed?
  - Window opened? Menu expanded? Text appeared? Page changed? Checkbox checked?
- Does current screenshot look DIFFERENT from before the action?
  - If screen looks SAME → action had NO EFFECT → step FAILED
  - If unexpected change (error message, wrong window) → step FAILED

**Only advance when you see CLEAR VISUAL PROOF the step succeeded.**

If step failed:
- Explain what you expected vs what you see
- Analyze why it failed (wrong coordinates? Element not clickable? Prerequisite missing?)
- Try different approach (different position, keyboard shortcut, scroll first, etc.)
- NEVER repeat exact same action that just failed

### Self-Correction

When an action doesn't produce expected result:
- State what you expected vs what you see
- Analyze why it might have failed
- Try a different approach (different coordinates, keyboard shortcut, etc.)
- Do NOT repeat the exact same action

## Available Actions

Output exactly ONE action per turn using this DSL:

- CLICK_ELEMENT(id) — Click the center of a detected UI element by its ID. Preferred over CLICK(x, y) when the target is a detected element. The system resolves the element's center coordinates automatically.
- MOVE_MOUSE(x, y) — Move the mouse cursor without clicking. Use only for hovering (e.g., to reveal tooltips or hover states).
- CLICK(x, y) — Left-click at point coordinates (x, y). Cursor position is automatically verified before the click executes. Use when the target is NOT in the detected elements list.
- DOUBLE_CLICK(x, y) — Double-click at point coordinates (x, y)
- RIGHT_CLICK(x, y) — Right-click at point coordinates (x, y)
- TYPE('text') — Type the given text. Use single quotes around the text.
- KEYPRESS(key) — Press a single key (e.g., enter, tab, escape)
- KEYPRESS(cmd+a) — Press a key combination (e.g., cmd+c, cmd+v, alt+tab)
- SCROLL(0, dy) — Scroll vertically. Positive dy = scroll up, negative = scroll down.
- WAIT(seconds) — Wait for the given number of seconds (max 10).

## Detected UI Elements

Each screenshot is analyzed by a vision model that detects interactive elements. You receive:
1. An annotated screenshot with numbered bounding boxes around detected elements
2. A JSON list of detected elements with IDs and labels

Use CLICK_ELEMENT(id) when your target matches a detected element — it is more precise than estimating coordinates. Fall back to CLICK(x, y) only when the target element is not in the detected list.

**Important**: Match elements by their `label` text (OCR), not by `class`. The class (button, icon, input_field, etc.) is approximate and may be wrong. What matters is the label text and whether the element is visually clickable.

## Coordinate System

- Use POINT coordinates (logical pixels), NOT physical pixels.
- Screen size in points will be provided. On Retina displays, the screenshot image may be larger than the point dimensions — always use point coordinates for actions.
- (0, 0) is the top-left corner of the screen.

## Response Format

Respond with a single JSON object (no markdown fences, no extra text):

{
  "current_sop_step": <int, SOP step you're working on - only advance after verifying completion>,
  "is_completed": <bool, true only if ENTIRE SOP verified complete>,
  "action": "<ACTION DSL string>",
  "action_rationale": "<why this action, what you see in screenshot, verification of previous action if applicable>",
  "action_expected_outcome": "<specific visual change you expect to see in next screenshot>"
}

If the SOP is fully completed, set is_completed to true and action to "WAIT(1)".

## Guidelines

- Treat each screenshot as feedback on your last action
- Compare screenshot to expected outcome BEFORE deciding next action
- If result doesn't match expectation → action failed → adjust approach
- Focus on visual changes when verifying CLICK/TYPE/KEYPRESS results
- Execute the SOP steps in order. Do not skip steps.
- When typing in a field, click on it first to focus it.
"""


def build_execution_message(
    sop_text: str,
    current_screenshot_path: str,
    screen_width: int,
    screen_height: int,
    active_app_name: str,
    action_history: list[dict],
    current_step_hint: int | None = None,
    elements: list[dict] | None = None,
    annotated_screenshot_path: str | None = None,
) -> list[dict]:
    """Build the messages list for a single GPT-4o call.

    Uses a single-turn approach (system + one user message) to avoid
    accumulating expensive screenshot tokens across turns.

    Args:
        sop_text: The full SOP text to execute.
        current_screenshot_path: Path to the current screenshot.
        screen_width: Screen width in points.
        screen_height: Screen height in points.
        active_app_name: Name of the currently active application.
        action_history: List of prior step summaries.
        current_step_hint: The SOP step number the agent is currently on.
        elements: Detected UI elements from YOLO (list of dicts with id, class, label, etc.).
        annotated_screenshot_path: Path to annotated screenshot with bounding boxes.

    Returns:
        Messages list for call_openai().
    """
    # Build user message content parts
    content_parts = []

    # 1. SOP text
    content_parts.append({
        "type": "text",
        "text": f"## SOP to Execute\n\n{sop_text}",
    })

    # 2. Current step focus (if provided)
    if current_step_hint is not None:
        import re
        steps = re.findall(r'^\d+\.\s+.+', sop_text, re.MULTILINE)
        if 0 < current_step_hint <= len(steps):
            step_text = steps[current_step_hint - 1]
            content_parts.append({
                "type": "text",
                "text": f"\n## Current Focus\nYou should be working on SOP step {current_step_hint}:\n{step_text}\n",
            })

    # 3. Action history (simplified)
    if action_history:
        history_lines = []
        for step in action_history:
            status = "✓" if not step.get("error") else f"✗ ({step['error']})"
            line = f"  Step {step['step_number']}: {step['action']} — {step['rationale']} [{status}]"
            expected = step.get("expected_outcome", "")
            if expected:
                line += f"\n    Expected outcome: {expected}"
            history_lines.append(line)

        history_text = "\n## Action History\n" + "\n".join(history_lines)
        history_text += "\n\nREMINDER: Before advancing to the next SOP step, compare the current screenshot to your last expected_outcome. Do you see visual proof that the step succeeded?"

        content_parts.append({
            "type": "text",
            "text": history_text,
        })

    # 4. Current state (screen size + active app only)
    state_text = f"\n## Current State\n"
    state_text += f"- Screen size: {screen_width} x {screen_height} points\n"
    state_text += f"- Active app: {active_app_name}\n"

    content_parts.append({"type": "text", "text": state_text})

    # 5. Detected elements JSON (compact: only id, label, class to save tokens)
    if elements:
        compact = [{"id": el["id"], "label": el["label"], "class": el["class"]} for el in elements]
        content_parts.append({
            "type": "text",
            "text": f"\n## Detected Elements\n{_json.dumps(compact)}\n",
        })

    # 6. Screenshot — prefer annotated (has bounding boxes + IDs), fall back to raw
    screenshot_path = annotated_screenshot_path if annotated_screenshot_path else current_screenshot_path
    screenshot_b64 = screenshot_to_base64(screenshot_path)
    label = "Annotated Screenshot" if annotated_screenshot_path else "Current Screenshot"
    content_parts.append({
        "type": "text",
        "text": f"\n## {label}\n",
    })
    content_parts.append({
        "type": "image_url",
        "image_url": {
            "url": f"data:image/png;base64,{screenshot_b64}",
            "detail": "high",
        },
    })

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": content_parts},
    ]
