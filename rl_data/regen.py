"""Regenerate a failed SOP via sop.methods.regenerate_sop and save it."""

from pathlib import Path

from rl_data.config import SOPS_DIR
from rl_data.models import RunRecord, SOPEntry
from sop.methods import regenerate_sop


def _find_failure_screenshot(execution_dir: Path, failed_step: int | None) -> Path | None:
    """Find the last screenshot near the failure point.

    The web executor numbers screenshots per Computer-Use action, which does NOT
    line up with SOP step numbers. Fall back to the last screenshot available.
    """
    screenshots_dir = execution_dir / "execution_screenshots"
    if not screenshots_dir.exists():
        return None
    pngs = sorted(screenshots_dir.glob("*.png"))
    return pngs[-1] if pngs else None


def build_regenerated_entry(
    parent: SOPEntry,
    failed_record: RunRecord,
    depth: int,
) -> SOPEntry | None:
    """Call regenerate_sop on the parent's text + validator's failure info.

    Returns a new SOPEntry (variant = f'regen_{depth}'), or None if the failure
    info is missing (nothing to regenerate from).
    """
    failed_step = failed_record.failed_step
    failure_reason = failed_record.failure_reason
    if failed_step is None:
        # If validator didn't pinpoint a step, try stuck_on_step from the log.
        failed_step = failed_record.stuck_on_step
    if failed_step is None:
        return None

    screenshot = _find_failure_screenshot(
        execution_dir=Path(failed_record.execution_dir),
        failed_step=failed_step,
    )

    new_text = regenerate_sop(
        old_sop=parent.sop_text,
        failed_step=failed_step,
        failure_reason=failure_reason or "(no reason provided)",
        screenshot_path=screenshot,
    )

    new_id = f"{_root_id(parent.id)}__regen_{depth}"
    out_path = SOPS_DIR / f"{new_id}.txt"
    out_path.write_text(new_text, encoding="utf-8")

    return SOPEntry(
        id=new_id,
        sop_text=new_text,
        task_intent=parent.task_intent,
        ui_name=parent.ui_name,
        start_url=parent.start_url,
        variant=f"regen_{depth}",
        parent_sop_id=parent.id,
    )


def _root_id(sop_id: str) -> str:
    """Strip trailing '__regen_N' so depth counts from the original id."""
    idx = sop_id.find("__regen_")
    return sop_id if idx == -1 else sop_id[:idx]
