"""Smoke test for group_RL.generate.

Run with:  python -m group_RL.test_generate

WARNING: makes real OpenAI API calls (3 calls at temperature 0.8).
Costs roughly $0.02-$0.05 depending on response length.
"""

import sys

from group_RL.consensus import parse_steps, rank_group
from group_RL.generate import generate_group


def _check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"FAIL: {name} -- {detail}")
    print(f"PASS: {name}")


def main() -> int:
    checks = 0
    intent = "Open the Google homepage and click the I'm Feeling Lucky button"

    print(f"Intent: {intent!r}")
    print("Generating 3 SOPs in parallel...\n")

    sops = generate_group(intent, n=3)

    _check("returns 3 SOPs", len(sops) == 3, f"got {len(sops)}")
    checks += 1

    for i, sop in enumerate(sops):
        _check(f"sop {i + 1} is non-empty", bool(sop and sop.strip()))
        checks += 1
        steps = parse_steps(sop)
        _check(
            f"sop {i + 1} parses to >= 1 step",
            len(steps) >= 1,
            f"got {len(steps)} steps",
        )
        checks += 1
        _check(
            f"sop {i + 1} has no leading markdown fence",
            not sop.startswith("```"),
        )
        checks += 1

    _check(
        "the 3 SOPs are not all identical (temperature gives diversity)",
        len(set(sops)) > 1,
        "all 3 outputs were identical strings",
    )
    checks += 1

    # Display
    print()
    for i, sop in enumerate(sops, start=1):
        print(f"========== SOP_{i} ({len(parse_steps(sop))} steps) ==========")
        print(sop)
        print()

    print("========== Consensus ranking ==========")
    ranked = rank_group(sops)
    for score, idx, _ in ranked:
        print(f"  {score:.3f}  SOP_{idx + 1}")

    print(f"\nAll {checks} checks passed.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
