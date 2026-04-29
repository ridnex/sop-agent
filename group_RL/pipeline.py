"""End-to-end orchestration for the group_RL pipeline.

One public function, `run_one(intent, ...)`, which:

  1. Retrieves the closest validated SOP from MemoryStore.
  2. Picks a generation strategy based on similarity (adapt / exemplar / fresh).
  3. Executes the chosen SOP via the existing web agent (subprocess).
  4. Validates the execution via the existing GPT-4o validator.
  5. On success → writes (intent, sop) to good memory.
  6. On failure → writes (intent, failed_sop) to bad memory, then runs a
     single repair attempt (Claude + group consensus) and re-executes.
     If v1 also fails, the v1 SOP is also added to bad memory.

Reuses every primitive from Steps 1–6 plus the existing executor and
validator. No new heavy logic — pure glue.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import BASE_DIR
from validate.validator import validate_execution

from group_RL.consensus import rank_group
from group_RL.generate import generate_group
from group_RL.memory import GROUP_RL_DIR, MemoryStore
from group_RL.repair import repair_group
from group_RL.retrieve import (
    RetrievalDecision,
    adapt_sop,
    exemplar_sop,
    retrieve_and_decide,
)

logger = logging.getLogger(__name__)


# ---------- path / id helpers ----------

@dataclass(frozen=True)
class Paths:
    root: Path
    sops_dir: Path
    executions_dir: Path
    runs_jsonl: Path
    good_memory: Path
    bad_memory: Path


def _make_paths(output_root: Path | None) -> Paths:
    root = Path(output_root) if output_root else GROUP_RL_DIR
    return Paths(
        root=root,
        sops_dir=root / "sops",
        executions_dir=root / "executions",
        runs_jsonl=root / "runs.jsonl",
        good_memory=root / "memory.jsonl",
        bad_memory=root / "bad_memory.jsonl",
    )


def _slug(intent: str, max_len: int = 50) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", intent.strip().lower())
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:max_len] or "untitled"


def _rel(p: Path | None) -> str | None:
    if p is None:
        return None
    p = Path(p).resolve()
    base = BASE_DIR.resolve()
    try:
        return str(p.relative_to(base))
    except ValueError:
        return str(p)


def _count_sop_steps(sop_text: str) -> int:
    return sum(1 for line in sop_text.splitlines() if re.match(r"^\s*\d+\.\s", line))


# ---------- execution / validation ----------

def _execute_sop_subprocess(
    sop_path: Path,
    execution_dir: Path,
    intent: str,
    start_url: str,
    max_steps: int,
    delay: float,
    headless: bool,
    launch: bool,
) -> int:
    """Run the existing web agent in a subprocess (Playwright sync isolation)."""
    cmd = [
        "python", "-m", "web.execute.main",
        "--sop-file", str(sop_path),
        "--yes",
        "--output-dir", str(execution_dir),
        "--max-steps", str(max_steps),
        "--delay", str(delay),
        "--intent", intent,
        "--url", start_url,
    ]
    if headless:
        cmd.append("--headless")
    if launch:
        cmd.append("--launch")
    return subprocess.run(cmd, cwd=str(BASE_DIR)).returncode


def _load_execution_log(execution_dir: Path) -> dict:
    log_path = execution_dir / "execution_log.json"
    if not log_path.exists():
        raise FileNotFoundError(f"missing {log_path}")
    with log_path.open(encoding="utf-8") as f:
        return json.load(f)


def _find_final_screenshot(execution_log: dict, execution_dir: Path) -> Path | None:
    steps = execution_log.get("steps", []) or []
    if steps:
        last = Path(steps[-1].get("screenshot_path", ""))
        if last.exists():
            return last
    screenshots_dir = execution_dir / "execution_screenshots"
    if screenshots_dir.exists():
        pngs = sorted(screenshots_dir.glob("*.png"))
        if pngs:
            return pngs[-1]
    return None


def _execute_and_validate(
    sop_text: str,
    sop_id: str,
    variant: str,
    intent: str,
    start_url: str,
    ts: str,
    paths: Paths,
    max_steps: int,
    delay: float,
    headless: bool,
    launch: bool,
) -> dict:
    """Persist the SOP, run the agent, validate. Returns a flat result dict.

    The returned dict carries two underscore-prefixed fields meant for the
    in-process orchestrator (`_execution_log`, `_final_screenshot_path`).
    Strip those before serializing to runs.jsonl.
    """
    paths.sops_dir.mkdir(parents=True, exist_ok=True)
    sop_path = paths.sops_dir / f"{sop_id}__{variant}.txt"
    sop_path.write_text(sop_text, encoding="utf-8")

    execution_dir = paths.executions_dir / f"exec_{sop_id}__{variant}_{ts}"
    execution_dir.mkdir(parents=True, exist_ok=True)

    rc = _execute_sop_subprocess(
        sop_path=sop_path,
        execution_dir=execution_dir,
        intent=intent,
        start_url=start_url,
        max_steps=max_steps,
        delay=delay,
        headless=headless,
        launch=launch,
    )

    log = _load_execution_log(execution_dir)
    final_screenshot = _find_final_screenshot(log, execution_dir)
    result = validate_execution(log, execution_dir)

    return {
        "sop_path": _rel(sop_path),
        "execution_dir": _rel(execution_dir),
        "was_completed": bool(result.get("was_completed", False)),
        "failed_step": result.get("failed_step"),
        "failure_reason": result.get("failure_reason"),
        "validation_thinking": result.get("thinking", ""),
        "final_screenshot": _rel(final_screenshot),
        "n_sop_steps": _count_sop_steps(sop_text),
        "n_exec_steps": len(log.get("steps", []) or []),
        "subprocess_rc": rc,
        "_execution_log": log,
        "_final_screenshot_path": final_screenshot,
    }


# ---------- v0 candidate selection ----------

def _print_candidates(label: str, ranked: list[tuple[float, int, str]]) -> None:
    """Pretty-print every candidate in a ranked group for debugging visibility."""
    print(f"\n[{label}]  {len(ranked)} candidates ranked by consensus:")
    for rank_pos, (score, orig_idx, sop) in enumerate(ranked, start=1):
        marker = "  (WINNER)" if rank_pos == 1 else ""
        print(
            f"\n--- candidate #{orig_idx + 1}  rank={rank_pos}  "
            f"consensus={score:.3f}{marker} ---"
        )
        print(sop)


def _produce_v0(
    intent: str,
    decision: RetrievalDecision,
    n_group: int,
) -> tuple[str, list[dict] | None]:
    """Return (chosen_sop_text, candidate_scores_or_None) for the given strategy.

    candidate_scores is non-null only when strategy=='fresh' (G samples were
    actually compared via consensus). Prints each candidate to stdout when
    fresh-generation runs, so you can see what the consensus picked from.
    """
    if decision.strategy == "adapt":
        return adapt_sop(intent, decision.exemplar), None
    if decision.strategy == "exemplar":
        return exemplar_sop(intent, decision.exemplar), None
    # fresh
    candidates = generate_group(intent, n=n_group)
    ranked = rank_group(candidates)
    _print_candidates("fresh v0", ranked)
    scores = [{"index": idx, "score": float(score)} for score, idx, _ in ranked]
    return ranked[0][2], scores


# ---------- runs.jsonl writer ----------

def _append_runs_jsonl(row: dict, runs_jsonl: Path) -> None:
    runs_jsonl.parent.mkdir(parents=True, exist_ok=True)
    serializable = json.loads(json.dumps(row, default=str))
    with runs_jsonl.open("a", encoding="utf-8") as f:
        f.write(json.dumps(serializable, ensure_ascii=False) + "\n")


def _strip_private_keys(d: dict) -> dict:
    return {k: v for k, v in d.items() if not k.startswith("_")}


# ---------- public entry point ----------

def run_one(
    intent: str,
    start_url: str = "about:blank",
    n_group: int = 3,
    max_steps: int = 50,
    delay: float = 2.0,
    headless: bool = False,
    launch: bool = False,
    output_root: Path | None = None,
) -> dict:
    """Run the full retrieve-generate-validate-repair-writeback loop for one intent.

    Returns a summary dict (also appended to runs.jsonl). Side effects:
      - <root>/sops/<sop_id>__v0.txt  (always)
      - <root>/sops/<sop_id>__v1.txt  (only if v0 failed)
      - <root>/executions/exec_<sop_id>__v?_<ts>/
      - <root>/runs.jsonl             (one row per call)
      - <root>/memory.jsonl           (writeback on any successful execution)
      - <root>/bad_memory.jsonl       (writeback on any failed execution)
    """
    paths = _make_paths(output_root)
    ts = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
    sop_id = f"{_slug(intent)}_{ts}"

    print(f"\n=== run_one ===")
    print(f"intent: {intent}")
    print(f"sop_id: {sop_id}")

    # 1. retrieve + decide
    good_store = MemoryStore(paths.good_memory)
    decision = retrieve_and_decide(intent, good_store)
    print(
        f"\n[retrieval]  strategy={decision.strategy}  "
        f"score={decision.score:.3f}"
        + (f"  exemplar={decision.exemplar['intent']!r}" if decision.exemplar else "")
    )

    # 2. produce v0
    print(f"\n[generate v0]  via {decision.strategy} ...")
    sop_v0, v0_candidate_scores = _produce_v0(intent, decision, n_group)
    print(f"v0 SOP ({_count_sop_steps(sop_v0)} steps):\n{sop_v0}\n")

    # 3. execute + validate v0
    print("[execute v0]")
    v0 = _execute_and_validate(
        sop_v0, sop_id, "v0", intent, start_url, ts,
        paths, max_steps, delay, headless, launch,
    )
    print(
        f"\n[validate v0]  was_completed={v0['was_completed']}"
        + (
            ""
            if v0["was_completed"]
            else f"  failed_step={v0['failed_step']}  reason={(v0['failure_reason'] or '')[:120]}"
        )
    )

    # 4. assemble summary skeleton
    summary: dict[str, Any] = {
        "sop_id": sop_id,
        "intent": intent,
        "start_url": start_url,
        "strategy": decision.strategy,
        "retrieval_score": float(decision.score),
        "retrieved_intent": decision.exemplar["intent"] if decision.exemplar else None,
        "n_group": n_group,
        "v0": {
            **_strip_private_keys(v0),
            "candidate_scores": v0_candidate_scores,
        },
        "v1": None,
        "final_label": None,
        "memory_writeback": False,
        "bad_memory_writeback": False,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }

    # 5. branch on v0 outcome
    if v0["was_completed"]:
        good_store.add(
            intent=intent,
            sop_text=sop_v0,
            label="good",
            strategy=decision.strategy,
            sop_id=sop_id,
            variant="v0",
            execution_dir=v0["execution_dir"],
        )
        summary["final_label"] = "good"
        summary["memory_writeback"] = True
        print("\n[memory]  v0 added to good memory")
        _append_runs_jsonl(summary, paths.runs_jsonl)
        return summary

    # v0 FAILED → write to bad memory immediately
    bad_store = MemoryStore(paths.bad_memory)
    bad_store.add(
        intent=intent,
        sop_text=sop_v0,
        label="bad",
        strategy=decision.strategy,
        sop_id=sop_id,
        variant="v0",
        failed_step=v0["failed_step"],
        failure_reason=v0["failure_reason"],
        execution_dir=v0["execution_dir"],
        final_screenshot=v0["final_screenshot"],
    )
    summary["bad_memory_writeback"] = True
    print("[bad memory]  v0 added to bad memory")

    # 6. repair → v1
    print(f"\n[repair v1]  generating {n_group} repaired SOPs via Claude ...")
    repairs = repair_group(
        old_sop=sop_v0,
        failed_step=v0["failed_step"] or 1,
        failure_reason=v0["failure_reason"] or "(unspecified)",
        screenshot_path=v0["_final_screenshot_path"],
        n=n_group,
    )
    ranked = rank_group(repairs)
    _print_candidates("repair v1", ranked)
    sop_v1 = ranked[0][2]
    v1_scores = [{"index": idx, "score": float(score)} for score, idx, _ in ranked]
    print(f"\nv1 SOP picked: candidate #{ranked[0][1] + 1} ({_count_sop_steps(sop_v1)} steps, consensus={ranked[0][0]:.3f})")

    print("\n[execute v1]")
    v1 = _execute_and_validate(
        sop_v1, sop_id, "v1", intent, start_url, ts,
        paths, max_steps, delay, headless, launch,
    )
    print(f"\n[validate v1]  was_completed={v1['was_completed']}")

    summary["v1"] = {
        **_strip_private_keys(v1),
        "candidate_scores": v1_scores,
    }

    if v1["was_completed"]:
        good_store.add(
            intent=intent,
            sop_text=sop_v1,
            label="good",
            strategy="repair",
            sop_id=sop_id,
            variant="v1",
            parent_variant="v0",
            execution_dir=v1["execution_dir"],
        )
        summary["final_label"] = "good"
        summary["memory_writeback"] = True
        print("[memory]  v1 added to good memory")
    else:
        bad_store.add(
            intent=intent,
            sop_text=sop_v1,
            label="bad",
            strategy="repair",
            sop_id=sop_id,
            variant="v1",
            parent_variant="v0",
            failed_step=v1["failed_step"],
            failure_reason=v1["failure_reason"],
            execution_dir=v1["execution_dir"],
            final_screenshot=v1["final_screenshot"],
        )
        summary["final_label"] = "bad"
        print("[bad memory]  v1 added to bad memory")

    _append_runs_jsonl(summary, paths.runs_jsonl)
    return summary
