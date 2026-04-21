"""Run one SOP: execute via web agent, validate, build RunRecord(s)."""

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from rl_data.browser_keepalive import ensure_chrome_running
from rl_data.config import EXECUTIONS_DIR, MAX_STEPS, ACTION_DELAY, SOPS_DIR
from rl_data.loader import count_sop_steps
from rl_data.models import RunRecord, SOPEntry
from validate.validator import validate_execution
from web.execute.agent import run_agent


def _slug_ts() -> str:
    return datetime.now().strftime("%Y-%m-%d-%H-%M-%S")


def run_one(
    entry: SOPEntry,
    max_steps: int = MAX_STEPS,
    delay: float = ACTION_DELAY,
    auto_confirm: bool = True,
) -> tuple[list[RunRecord], Path]:
    """Execute one SOPEntry.

    Returns (records, execution_dir). The records list is:
      - `[original]` in the plain case (no inline repair, or repair did not rescue the run).
      - `[original (bad), repaired (good)]` when inline repair happened AND the validator
        agrees the run completed. The original is forcibly marked bad because the SOP as
        written was not directly executable; the repaired variant is the training "good".

    Always uses connect mode against a detached Chrome started via
    `ensure_chrome_running`. That Chrome survives the worker process, so the
    browser window stays open after execution finishes.
    """
    ts = _slug_ts()
    exec_dir = EXECUTIONS_DIR / f"exec_{entry.id}__{entry.variant}_{ts}"
    exec_dir.mkdir(parents=True, exist_ok=True)

    ensure_chrome_running()

    # Pass the source sop file so any inline repairs write their __repair_N.txt
    # right next to the original — matches the __regen_N convention.
    sop_file_hint = SOPS_DIR / f"{entry.id}.txt"

    exec_log = run_agent(
        sop_text=entry.sop_text,
        output_dir=exec_dir,
        intent=entry.task_intent,
        start_url=entry.start_url,
        max_steps=max_steps,
        delay=delay,
        auto_confirm=auto_confirm,
        launch=False,          # connect to detached Chrome; do NOT own it
        headless=False,
        sop_file=sop_file_hint if sop_file_hint.exists() else None,
    )

    exec_log_dict = asdict(exec_log)

    validation = validate_execution(
        execution_log=exec_log_dict,
        execution_dir=exec_dir,
    )

    val_path = exec_dir / "validation_result.json"
    val_path.write_text(json.dumps(validation, indent=2), encoding="utf-8")

    was_completed = bool(validation.get("was_completed"))
    repairs = exec_log.repairs or []
    now_iso = datetime.now().isoformat(timespec="seconds")

    original = RunRecord(
        sop_id=entry.id,
        variant=entry.variant,
        parent_sop_id=entry.parent_sop_id,
        task_intent=entry.task_intent,
        sop_text=entry.sop_text,
        execution_dir=str(exec_dir),
        was_completed=was_completed,
        label="good" if was_completed else "bad",
        failed_step=validation.get("failed_step"),
        failure_reason=validation.get("failure_reason"),
        n_sop_steps=count_sop_steps(entry.sop_text),
        created_at=now_iso,
        validation_thinking=validation.get("thinking", ""),
        stuck_on_step=exec_log.stuck_on_step,
        n_exec_steps=len(exec_log.steps),
        inline_repair_count=len(repairs),
    )

    records: list[RunRecord] = [original]

    # Emit a second record for the repaired variant ONLY when repairs happened
    # AND the validator confirmed the run completed — the user's "good only if
    # everything done" rule.
    if repairs and was_completed and exec_log.effective_sop_text:
        repaired_path_str = exec_log.repaired_sop_path or ""
        repaired_id = (
            Path(repaired_path_str).stem
            if repaired_path_str
            else f"{entry.id}__repair_1"
        )
        first_repair = repairs[0]

        # Downgrade the original: as written, it could not be executed verbatim.
        original.was_completed = False
        original.label = "bad"
        original.failed_step = first_repair.step_number
        original.failure_reason = (
            f"Step {first_repair.step_number} needed inline repair: "
            f"{first_repair.original_text[:80]!r} -> {first_repair.new_text[:80]!r}"
        )

        repaired = RunRecord(
            sop_id=repaired_id,
            variant="repair_1",
            parent_sop_id=entry.id,
            task_intent=entry.task_intent,
            sop_text=exec_log.effective_sop_text,
            execution_dir=str(exec_dir),
            was_completed=True,
            label="good",
            failed_step=None,
            failure_reason=None,
            n_sop_steps=count_sop_steps(exec_log.effective_sop_text),
            created_at=now_iso,
            validation_thinking=validation.get("thinking", ""),
            stuck_on_step=None,
            n_exec_steps=len(exec_log.steps),
            inline_repair_count=len(repairs),
            repair_of_step=first_repair.step_number,
        )
        records.append(repaired)

    return records, exec_dir
