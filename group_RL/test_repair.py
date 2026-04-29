"""Smoke test for group_RL.repair.

Run with:  python -m group_RL.test_repair

Reads the first `label="bad"` row from outputs/sop_data/runs.jsonl and uses
its failure context as input — so the test exercises the same shape of data
the pipeline will see in production.

WARNING: makes real Claude API calls (3 calls with vision attachment).
Costs roughly $0.10-$0.20 depending on response length.
"""

import json
import sys
from pathlib import Path

from group_RL.consensus import parse_steps, rank_group
from group_RL.repair import repair_group


def _check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"FAIL: {name} -- {detail}")
    print(f"PASS: {name}")


def _load_first_bad_row(path: Path) -> dict | None:
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            if (
                row.get("label") == "bad"
                and row.get("failure_reason")
                and row.get("failed_step") is not None
            ):
                return row
    return None


def main() -> int:
    runs_path = Path("outputs/sop_data/runs.jsonl")
    row = _load_first_bad_row(runs_path)
    if row is None:
        print(
            "No bad rows found in outputs/sop_data/runs.jsonl. "
            "Run the sop_data pipeline on a failing SOP first.",
            file=sys.stderr,
        )
        return 2

    print(f"Using failure from: {row['sop_id']} ({row['variant']})")
    print(f"  failed_step:    {row['failed_step']}")
    print(f"  failure_reason: {row['failure_reason'][:150]}")

    screenshot = row.get("final_screenshot")
    screenshot_path = Path(screenshot) if screenshot and Path(screenshot).exists() else None
    print(f"  screenshot:     {screenshot_path or '(not found, no vision input)'}")
    print()

    print("Generating 3 repaired SOPs in parallel via Claude...\n")

    repairs = repair_group(
        old_sop=row["sop_text"],
        failed_step=row["failed_step"],
        failure_reason=row["failure_reason"],
        screenshot_path=screenshot_path,
        n=3,
    )

    checks = 0

    _check("returns 3 repaired SOPs", len(repairs) == 3, f"got {len(repairs)}")
    checks += 1

    for i, sop in enumerate(repairs):
        _check(f"repair {i + 1} is non-empty", bool(sop and sop.strip()))
        checks += 1
        steps = parse_steps(sop)
        _check(
            f"repair {i + 1} parses to >= 1 step",
            len(steps) >= 1,
            f"got {len(steps)} steps",
        )
        checks += 1
        _check(
            f"repair {i + 1} has no leading markdown fence",
            not sop.startswith("```"),
        )
        checks += 1

    _check(
        "the 3 repaired SOPs are not all identical",
        len(set(repairs)) > 1,
        "all 3 outputs were identical strings",
    )
    checks += 1

    print()
    print(f"========== Original (failed at step {row['failed_step']}) ==========")
    print(row["sop_text"])
    print()

    for i, sop in enumerate(repairs, start=1):
        print(f"========== REPAIR_{i} ({len(parse_steps(sop))} steps) ==========")
        print(sop)
        print()

    print("========== Consensus ranking of repairs ==========")
    ranked = rank_group(repairs)
    for score, idx, _ in ranked:
        print(f"  {score:.3f}  REPAIR_{idx + 1}")

    print(f"\nAll {checks} checks passed.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
