"""Orchestration: execute SOP -> validate -> (failure repair OR struggle rewrite) -> execute -> validate.

One SOP file in, 1 or 2 rows appended to outputs/sop_data/runs.jsonl.
"""

import json
import logging
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from sop_data.manifest import append_row
from sop_data.repair import claude_repair_sop, claude_rewrite_from_trace
from sop_data.struggle import detect_struggle
from validate.validator import validate_execution
from web.execute.config import BASE_DIR, MODEL

logger = logging.getLogger(__name__)

SOP_DATA_DIR = BASE_DIR / "outputs" / "sop_data"
SOPS_DIR = SOP_DATA_DIR / "sops"
EXECUTIONS_DIR = SOP_DATA_DIR / "executions"
MANIFEST_PATH = SOP_DATA_DIR / "runs.jsonl"


def _rel_to_repo(p: Path) -> str:
    try:
        return str(p.resolve().relative_to(BASE_DIR.resolve()))
    except ValueError:
        return str(p)


def _count_sop_steps(sop_text: str) -> int:
    return sum(1 for line in sop_text.splitlines() if re.match(r"^\s*\d+\.\s", line))


def _find_final_screenshot(execution_log: dict, execution_dir: Path) -> Path | None:
    steps = execution_log.get("steps", [])
    if steps:
        last_path = Path(steps[-1].get("screenshot_path", ""))
        if last_path.exists():
            return last_path
    screenshots_dir = execution_dir / "execution_screenshots"
    if screenshots_dir.exists():
        pngs = sorted(screenshots_dir.glob("*.png"))
        if pngs:
            return pngs[-1]
    return None


def _run_variant(
    *,
    sop_id: str,
    variant: str,
    sop_text: str,
    parent_variant: str | None,
    intent: str,
    start_url: str,
    max_steps: int,
    delay: float,
    headless: bool,
    launch: bool,
    timestamp: str,
    repair_reason: str | None,
    repair_model: str | None,
) -> tuple[dict, dict, Path | None]:
    """Run one variant in a subprocess, validate, and build (but do NOT append) a manifest row.

    Returns (row, execution_log_dict, final_screenshot_path).
    """
    execution_dir = EXECUTIONS_DIR / f"exec_{sop_id}__{variant}_{timestamp}"
    execution_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n[{variant}] Executing SOP ({_count_sop_steps(sop_text)} steps) -> {execution_dir}")

    variant_sop_path = execution_dir / "_input_sop.txt"
    variant_sop_path.write_text(sop_text, encoding="utf-8")

    cmd = [
        sys.executable, "-m", "web.execute.main",
        "--sop-file", str(variant_sop_path),
        "--url", start_url,
        "--max-steps", str(max_steps),
        "--delay", str(delay),
        "--yes",
        "--output-dir", str(execution_dir),
    ]
    if intent:
        cmd.extend(["--intent", intent])
    if launch:
        cmd.append("--launch")
    if headless:
        cmd.append("--headless")

    subprocess.run(cmd, check=False, cwd=str(BASE_DIR))

    execution_log_path = execution_dir / "execution_log.json"
    if not execution_log_path.exists():
        raise RuntimeError(f"Execution did not produce a log at {execution_log_path}")
    execution_log_dict = json.loads(execution_log_path.read_text(encoding="utf-8"))

    print(f"[{variant}] Validating...")
    validation = validate_execution(
        execution_log=execution_log_dict,
        execution_dir=execution_dir,
        stuck_on_step=execution_log_dict.get("stuck_on_step"),
    )
    (execution_dir / "validation_result.json").write_text(
        json.dumps(validation, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    was_completed = bool(validation.get("was_completed"))
    label = "good" if was_completed else "bad"

    final_screenshot = _find_final_screenshot(execution_log_dict, execution_dir)
    sop_path = SOPS_DIR / f"{sop_id}__{variant}.txt"

    row = {
        "sop_id": sop_id,
        "variant": variant,
        "parent_variant": parent_variant,
        "sop_path": _rel_to_repo(sop_path),
        "sop_text": sop_text,
        "execution_dir": _rel_to_repo(execution_dir),
        "intent": intent,
        "start_url": start_url,
        "was_completed": was_completed,
        "label": label,
        "failed_step": validation.get("failed_step"),
        "failure_reason": validation.get("failure_reason"),
        "validation_thinking": validation.get("thinking", ""),
        "stuck_on_step": execution_log_dict.get("stuck_on_step"),
        "n_sop_steps": _count_sop_steps(sop_text),
        "n_exec_steps": len(execution_log_dict.get("steps", [])),
        "final_screenshot": _rel_to_repo(final_screenshot) if final_screenshot else None,
        "repair_reason": repair_reason,
        "struggle_signals": None,
        "repair_model": repair_model,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }

    reason_snippet = (validation.get("failure_reason") or "").replace("\n", " ")
    if len(reason_snippet) > 120:
        reason_snippet = reason_snippet[:117] + "..."
    print(
        f"[{variant}] label={label} "
        f"was_completed={was_completed} "
        f"failed_step={validation.get('failed_step')} "
        f"reason={reason_snippet!r}"
    )

    return row, execution_log_dict, final_screenshot


def run_one(
    sop_path: Path,
    *,
    name: str | None = None,
    intent: str = "",
    start_url: str = "about:blank",
    max_steps: int = 50,
    delay: float = 2.0,
    headless: bool = False,
    launch: bool = False,
    struggle_repeat_threshold: int = 4,
    struggle_overshoot_ratio: float = 3.0,
    struggle_check: bool = True,
) -> dict:
    """Execute one SOP; produce v1 on validator-failure OR struggle. Returns a run summary."""
    sop_text = sop_path.read_text(encoding="utf-8").strip()
    if not sop_text:
        raise ValueError(f"SOP file is empty: {sop_path}")

    sop_id = name or sop_path.stem
    timestamp = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")

    SOPS_DIR.mkdir(parents=True, exist_ok=True)
    EXECUTIONS_DIR.mkdir(parents=True, exist_ok=True)

    v0_sop_path = SOPS_DIR / f"{sop_id}__v0.txt"
    v0_sop_path.write_text(sop_text, encoding="utf-8")

    v0_row, v0_log, v0_screenshot = _run_variant(
        sop_id=sop_id,
        variant="v0",
        sop_text=sop_text,
        parent_variant=None,
        intent=intent,
        start_url=start_url,
        max_steps=max_steps,
        delay=delay,
        headless=headless,
        launch=launch,
        timestamp=timestamp,
        repair_reason=None,
        repair_model=None,
    )

    summary: dict = {"sop_id": sop_id, "variants": []}

    # Decide branch: clean-good / struggled-good / bad
    do_v1 = False
    v1_kind: str | None = None  # "failure" | "struggle"
    repair_sop: str | None = None

    if not v0_row["was_completed"]:
        # (c) Failed — repair via Claude
        v0_row["repair_reason"] = "failure"
        failed_step = v0_row["failed_step"] or 1
        failure_reason = v0_row["failure_reason"] or ""
        print(f"\n[v0 -> v1] Asking Claude to repair SOP from step {failed_step}...")
        repair_sop = claude_repair_sop(
            old_sop=sop_text,
            failed_step=failed_step,
            failure_reason=failure_reason,
            screenshot_path=v0_screenshot,
        )
        do_v1, v1_kind = True, "failure"
    else:
        # v0 validated good — run struggle detection unless disabled
        struggle = (
            detect_struggle(
                v0_log,
                repeat_threshold=struggle_repeat_threshold,
                overshoot_ratio=struggle_overshoot_ratio,
            )
            if struggle_check
            else {"detected": False, "first_struggle_step": None, "signals": []}
        )

        if struggle["detected"]:
            # (b) Struggled-good — relabel v0 bad, rewrite from trace
            signals = struggle["signals"]
            signals_joined = ", ".join(signals)
            struggle_step = struggle["first_struggle_step"] or 1
            v0_row["label"] = "bad"
            v0_row["failure_reason"] = f"struggled_but_completed: {signals_joined}"
            v0_row["failed_step"] = struggle_step
            v0_row["struggle_signals"] = signals
            v0_row["repair_reason"] = "struggle"
            print(
                f"\n[v0] struggle detected: {signals_joined}; "
                f"rewriting SOP from trace (struggle_step={struggle_step})..."
            )
            repair_sop = claude_rewrite_from_trace(
                old_sop=sop_text,
                execution_log=v0_log,
                struggle_step=struggle_step,
                struggle_signals=signals,
                screenshot_path=v0_screenshot,
            )
            do_v1, v1_kind = True, "struggle"
        # (a) Clean-good — no v1

    # Persist v0 row now (after possible struggle mutation).
    append_row(v0_row, MANIFEST_PATH)
    summary["variants"].append({
        "variant": "v0",
        "label": v0_row["label"],
        "repair_reason": v0_row["repair_reason"],
    })

    if not do_v1:
        return summary

    if not repair_sop:
        raise RuntimeError("Claude returned an empty SOP for the v1 variant")

    v1_sop_path = SOPS_DIR / f"{sop_id}__v1.txt"
    v1_sop_path.write_text(repair_sop, encoding="utf-8")

    v1_row, _v1_log, _v1_screenshot = _run_variant(
        sop_id=sop_id,
        variant="v1",
        sop_text=repair_sop,
        parent_variant="v0",
        intent=intent,
        start_url=start_url,
        max_steps=max_steps,
        delay=delay,
        headless=headless,
        launch=launch,
        timestamp=timestamp,
        repair_reason=v1_kind,
        repair_model=MODEL,
    )
    append_row(v1_row, MANIFEST_PATH)
    summary["variants"].append({
        "variant": "v1",
        "label": v1_row["label"],
        "repair_reason": v1_row["repair_reason"],
    })

    return summary
