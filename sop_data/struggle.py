"""Heuristic struggle detection on an execution log.

A "struggle" means the agent eventually succeeded but the trajectory is not a
clean example — execution errors, retries on the same SOP step, or blowing past
the expected step count. Such v0 runs are reclassified as bad training data.
"""

import re
from collections import Counter

# Tool actions that observe state rather than change it — they inflate the
# step count without representing real SOP progress, so we strip them from
# the overshoot comparison.
_NON_ACTION_PREFIXES = (
    "screenshot",
    "wait",
    "cursor_position",
    "mouse_move",  # moves without clicking — usually exploratory / hover
)


def _count_sop_steps(sop_text: str) -> int:
    return sum(1 for line in (sop_text or "").splitlines() if re.match(r"^\s*\d+\.\s", line))


def _is_real_action(step: dict) -> bool:
    """True if the step represents an actual state-changing interaction (click/type/key/scroll/drag)."""
    action = (step.get("model_action") or "").strip().lower()
    if not action:
        return False
    return not action.startswith(_NON_ACTION_PREFIXES)


def detect_struggle(
    execution_log: dict,
    repeat_threshold: int = 4,
    overshoot_ratio: float = 3.0,
) -> dict:
    """Return {detected, first_struggle_step, signals} for an execution log dict."""
    steps = execution_log.get("steps", []) or []
    sop_text = execution_log.get("sop_text", "") or ""
    n_sop_steps = _count_sop_steps(sop_text)
    action_steps = [s for s in steps if _is_real_action(s)]
    n_actions = len(action_steps)

    signals: list[str] = []
    first_struggle_step: int | None = None

    # 1. Any explicit execution error
    for step in steps:
        if step.get("error"):
            sop_step = step.get("current_sop_step")
            tag_target = sop_step if sop_step is not None else step.get("step_number")
            signals.append(f"execution_error_at_sop_step_{tag_target}")
            if first_struggle_step is None and sop_step is not None:
                first_struggle_step = sop_step
            break  # one error is enough signal; don't spam

    # 2. Repeated SOP step (agent had to retry the same step N+ times)
    sop_step_counts = Counter(
        step["current_sop_step"]
        for step in steps
        if step.get("current_sop_step") is not None
    )
    repeated = [(s, c) for s, c in sop_step_counts.items() if c >= repeat_threshold]
    repeated.sort(key=lambda sc: sc[0])
    for sop_step, count in repeated:
        signals.append(f"repeated_sop_step_{sop_step}_x{count}")
        if first_struggle_step is None:
            first_struggle_step = sop_step

    # 3. Overshoot — count only real actions (clicks/types/keys/scrolls),
    # not observation steps (screenshots/waits/cursor moves) which naturally
    # 3-5x the tool-call count on every healthy run.
    if n_sop_steps >= 3 and n_actions > overshoot_ratio * n_sop_steps:
        signals.append(f"overshoot_actions={n_actions}_sop={n_sop_steps}")
        if first_struggle_step is None:
            first_struggle_step = 1

    return {
        "detected": bool(signals),
        "first_struggle_step": first_struggle_step,
        "signals": signals,
    }
