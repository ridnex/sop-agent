"""Smoke test for group_RL.pipeline.run_one.

Run with:  python -m group_RL.test_pipeline

Mocks every expensive call (executor subprocess, validator, GPT and Claude
generators) so the test runs in seconds and doesn't touch the browser.
The local embedder is the only "real" component — needed to drive
retrieval against a temp memory store.

Three scenarios verified:
  1. v0 PASS via the adapt branch       → good memory only
  2. v0 FAIL → v1 PASS via fresh+repair → bad memory has v0; good memory has v1
  3. v0 FAIL → v1 FAIL via fresh+repair → bad memory has v0 AND v1
"""

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from group_RL.memory import MemoryStore


def _check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"FAIL: {name} -- {detail}")
    print(f"PASS: {name}")


def _seed_memory(path: Path) -> None:
    """Drop one validated SOP into a fresh memory file so retrieval has something to hit."""
    store = MemoryStore(path)
    store.add(
        intent="Compose and send a new email in Gmail",
        sop_text=(
            "1. Navigate to gmail.com\n"
            "2. Click the \"Compose\" button\n"
            "3. In the \"To\" field, type the recipient address\n"
            "4. Click the \"Send\" button"
        ),
        label="good",
    )


def _fake_execute_and_validate_factory(was_completed: bool, failed_step: int | None = None,
                                        failure_reason: str | None = None):
    """Build a stub for pipeline._execute_and_validate that mimics its dict shape."""

    def _fake(sop_text, sop_id, variant, intent, start_url, ts, paths,
             max_steps, delay, headless, launch):
        # Persist the SOP file like the real path does — so we can assert on it.
        paths.sops_dir.mkdir(parents=True, exist_ok=True)
        sop_path = paths.sops_dir / f"{sop_id}__{variant}.txt"
        sop_path.write_text(sop_text, encoding="utf-8")
        execution_dir = paths.executions_dir / f"exec_{sop_id}__{variant}_{ts}"
        execution_dir.mkdir(parents=True, exist_ok=True)
        return {
            "sop_path": str(sop_path),
            "execution_dir": str(execution_dir),
            "was_completed": was_completed,
            "failed_step": failed_step,
            "failure_reason": failure_reason,
            "validation_thinking": "stub",
            "final_screenshot": None,
            "n_sop_steps": sop_text.count("\n") + 1,
            "n_exec_steps": 5,
            "subprocess_rc": 0,
            "_execution_log": {"steps": []},
            "_final_screenshot_path": None,
        }

    return _fake


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def main() -> int:
    checks = 0

    # ---------- scenario 1: v0 succeeds via adapt ----------
    print("\n--- scenario 1: v0 PASS via adapt ---\n")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _seed_memory(root / "memory.jsonl")

        v0_pass = _fake_execute_and_validate_factory(was_completed=True)

        with patch("group_RL.pipeline._execute_and_validate", v0_pass), \
             patch("group_RL.pipeline.adapt_sop", return_value=(
                "1. Navigate to gmail.com\n"
                "2. Click the \"Compose\" button\n"
                "3. In the \"To\" field, type bob@example.com\n"
                "4. Click the \"Send\" button"
             )):
            from group_RL.pipeline import run_one
            summary = run_one(
                intent="Compose a new email in Gmail to bob@example.com",
                output_root=root,
            )

        _check("scenario 1: strategy is adapt",
               summary["strategy"] == "adapt",
               f"got {summary['strategy']}")
        checks += 1
        _check("scenario 1: final_label good", summary["final_label"] == "good")
        checks += 1
        _check("scenario 1: memory_writeback True", summary["memory_writeback"])
        checks += 1
        _check("scenario 1: bad_memory_writeback False",
               summary["bad_memory_writeback"] is False)
        checks += 1
        _check("scenario 1: v1 is None", summary["v1"] is None)
        checks += 1
        _check("scenario 1: good memory has 2 rows (seed + new)",
               len(MemoryStore(root / "memory.jsonl")) == 2)
        checks += 1
        _check("scenario 1: bad memory file does not exist",
               not (root / "bad_memory.jsonl").exists())
        checks += 1
        _check("scenario 1: runs.jsonl has 1 row",
               len(_read_jsonl(root / "runs.jsonl")) == 1)
        checks += 1

    # ---------- scenario 2: v0 fails, v1 succeeds via fresh+repair ----------
    print("\n--- scenario 2: v0 FAIL → v1 PASS via fresh+repair ---\n")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        # No seed → retrieval must fall through to "fresh"
        results = iter([
            _fake_execute_and_validate_factory(
                was_completed=False, failed_step=2,
                failure_reason="element not found",
            ),
            _fake_execute_and_validate_factory(was_completed=True),
        ])

        def _fake_eav(*args, **kwargs):
            return next(results)(*args, **kwargs)

        fake_fresh = ["1. Open site\n2. Click thing", "1. Open site\n2. Click thing v2",
                      "1. Open site\n2. Tap thing"]
        fake_repairs = ["1. Open site\n2. Click thing properly",
                        "1. Open site\n2. Click thing carefully",
                        "1. Open site\n2. Find and click the thing"]

        with patch("group_RL.pipeline._execute_and_validate", _fake_eav), \
             patch("group_RL.pipeline.generate_group", return_value=fake_fresh), \
             patch("group_RL.pipeline.repair_group", return_value=fake_repairs):
            from group_RL.pipeline import run_one
            summary = run_one(
                intent="Order a pizza on a delivery website",
                output_root=root,
            )

        _check("scenario 2: strategy is fresh",
               summary["strategy"] == "fresh",
               f"got {summary['strategy']}")
        checks += 1
        _check("scenario 2: v0 ran, v0 failed, v1 ran, v1 succeeded",
               (summary["v0"]["was_completed"] is False
                and summary["v1"] is not None
                and summary["v1"]["was_completed"] is True))
        checks += 1
        _check("scenario 2: final_label good", summary["final_label"] == "good")
        checks += 1
        _check("scenario 2: memory_writeback True", summary["memory_writeback"])
        checks += 1
        _check("scenario 2: bad_memory_writeback True",
               summary["bad_memory_writeback"])
        checks += 1
        _check("scenario 2: bad memory has v0 row",
               len(MemoryStore(root / "bad_memory.jsonl")) == 1)
        checks += 1
        _check("scenario 2: good memory has v1 row",
               len(MemoryStore(root / "memory.jsonl")) == 1)
        checks += 1
        _check("scenario 2: v0 has candidate_scores (G=3 fresh)",
               summary["v0"]["candidate_scores"] is not None
               and len(summary["v0"]["candidate_scores"]) == 3)
        checks += 1
        _check("scenario 2: v1 has candidate_scores (3 repairs)",
               summary["v1"]["candidate_scores"] is not None
               and len(summary["v1"]["candidate_scores"]) == 3)
        checks += 1

    # ---------- scenario 3: v0 fails, v1 also fails ----------
    print("\n--- scenario 3: v0 FAIL → v1 FAIL ---\n")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)

        results = iter([
            _fake_execute_and_validate_factory(
                was_completed=False, failed_step=2,
                failure_reason="element not found",
            ),
            _fake_execute_and_validate_factory(
                was_completed=False, failed_step=3,
                failure_reason="page did not load",
            ),
        ])

        def _fake_eav(*args, **kwargs):
            return next(results)(*args, **kwargs)

        with patch("group_RL.pipeline._execute_and_validate", _fake_eav), \
             patch("group_RL.pipeline.generate_group",
                   return_value=["1. a\n2. b", "1. a\n2. c", "1. a\n2. d"]), \
             patch("group_RL.pipeline.repair_group",
                   return_value=["1. a\n2. b'", "1. a\n2. c'", "1. a\n2. d'"]):
            from group_RL.pipeline import run_one
            summary = run_one(
                intent="Some hard task that nobody can do",
                output_root=root,
            )

        _check("scenario 3: final_label bad", summary["final_label"] == "bad")
        checks += 1
        _check("scenario 3: memory_writeback False",
               summary["memory_writeback"] is False)
        checks += 1
        _check("scenario 3: bad_memory_writeback True",
               summary["bad_memory_writeback"])
        checks += 1
        _check("scenario 3: bad memory has 2 rows (v0 + v1)",
               len(MemoryStore(root / "bad_memory.jsonl")) == 2)
        checks += 1
        _check("scenario 3: good memory does not exist",
               not (root / "memory.jsonl").exists())
        checks += 1

    print(f"\nAll {checks} checks passed.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
