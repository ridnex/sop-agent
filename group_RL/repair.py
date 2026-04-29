"""Group repair: ask Claude N times to fix a failed SOP (parallel calls).

Wraps the existing `sop_data.repair.claude_repair_sop` — same prompt
template, same OS-aware modifier guidance, same screenshot-vision input —
sampled N times for the consensus ranker. Output of repair_group() →
input of consensus.rank_group().

Used by the `group_RL` pipeline as the failure-recovery branch when the
first execution attempt is validated `bad`. Companion to generate.py
(fresh generation via GPT) — the two modules share the same input/output
shape so the orchestration code can call them interchangeably.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sop_data.repair import claude_repair_sop

DEFAULT_GROUP_SIZE = 3


def _strip_markdown_fences(text: str) -> str:
    """Remove leading/trailing ```...``` fences sometimes emitted by Claude."""
    text = text.strip()
    if text.startswith("```"):
        nl = text.find("\n")
        text = text[nl + 1:] if nl != -1 else ""
    if text.endswith("```"):
        nl = text.rfind("\n")
        text = text[:nl] if nl != -1 else ""
    return text.strip()


def _one_call(
    old_sop: str,
    failed_step: int,
    failure_reason: str,
    screenshot_path: Path | None,
    model: str | None,
) -> str:
    raw = claude_repair_sop(
        old_sop=old_sop,
        failed_step=failed_step,
        failure_reason=failure_reason,
        screenshot_path=screenshot_path,
        model=model,
    )
    return _strip_markdown_fences(raw)


def repair_group(
    old_sop: str,
    failed_step: int,
    failure_reason: str,
    screenshot_path: Path | None = None,
    n: int = DEFAULT_GROUP_SIZE,
    model: str | None = None,
) -> list[str]:
    """Generate N repaired SOPs in parallel via Claude (vision-enabled).

    All N calls receive the same inputs (failed SOP + step + reason + final
    screenshot). Diversity comes from Anthropic's default sampling
    temperature (~1.0) — no temperature override is needed because the
    failure context already constrains the answer; we just want the
    minor wording variation needed for the consensus ranker to have signal.

    Returns N stripped SOP strings ready to feed to rank_group(). Wall time
    is ~one API call regardless of N.
    """
    if n <= 0:
        return []
    with ThreadPoolExecutor(max_workers=n) as pool:
        futures = [
            pool.submit(
                _one_call,
                old_sop,
                failed_step,
                failure_reason,
                screenshot_path,
                model,
            )
            for _ in range(n)
        ]
        return [f.result() for f in futures]
