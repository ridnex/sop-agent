"""Smoke test for group_RL.consensus.

Run with:  python -m group_RL.test_consensus

Pure offline — only embedder, no API or browser.
"""

import sys

from group_RL.consensus import best_of_group, parse_steps, rank_group


def _check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"FAIL: {name} -- {detail}")
    print(f"PASS: {name}")


def main() -> int:
    checks = 0

    # 1. parse_steps on a clean numbered list returns text without leading numbers.
    sop = "1. Open Gmail\n2. Click Compose\n3. Type recipient\n4. Click Send"
    steps = parse_steps(sop)
    _check(
        "parse_steps yields the right step count",
        len(steps) == 4,
        f"got {len(steps)}",
    )
    checks += 1
    _check(
        "parse_steps strips the leading number-and-dot",
        steps[0] == "Open Gmail" and steps[3] == "Click Send",
        f"got {steps}",
    )
    checks += 1

    # 2. parse_steps absorbs continuation lines into the previous step.
    multi = (
        "1. Press cmd+L to focus the address bar,\n"
        "   then type 'gmail.com' and press Return\n"
        "2. Wait for the page to load"
    )
    s = parse_steps(multi)
    _check(
        "parse_steps absorbs continuation lines",
        len(s) == 2 and "address bar" in s[0] and "gmail.com" in s[0],
        f"got {s}",
    )
    checks += 1

    # 3. parse_steps on text without numbered steps returns [].
    _check(
        "parse_steps on prose returns []",
        parse_steps("just a paragraph with no numbers.") == [],
    )
    checks += 1

    # 4. Empty input → empty ranking.
    _check("rank_group([]) == []", rank_group([]) == [])
    checks += 1

    # 5. Single SOP → one row, score 0.0 by convention (no siblings).
    single = rank_group([sop])
    _check(
        "rank_group with a single SOP returns one row",
        len(single) == 1 and single[0][1] == 0,
        f"got {single}",
    )
    checks += 1
    _check(
        "single-SOP score is 0.0 (no siblings)",
        single[0][0] == 0.0,
        f"got {single[0][0]}",
    )
    checks += 1

    # 6. Identical SOPs → all score = 1.0.
    sops_same = [sop, sop, sop, sop]
    ranked = rank_group(sops_same)
    scores = [t[0] for t in ranked]
    _check(
        "identical SOPs all score ~1.0",
        all(abs(s - 1.0) < 1e-3 for s in scores),
        f"got scores={scores}",
    )
    checks += 1

    # 7. One outlier among similar SOPs → outlier ranks last.
    sop_a = "1. Open Gmail\n2. Click Compose\n3. Type recipient\n4. Click Send"
    sop_b = "1. Open Gmail\n2. Click Compose\n3. Fill the To field\n4. Click Send"
    sop_c = "1. Visit Gmail\n2. Click Compose\n3. Type recipient\n4. Press Send"
    sop_outlier = (
        "1. Open the YouTube homepage\n"
        "2. Search for cat videos\n"
        "3. Click the first result\n"
        "4. Watch the video"
    )
    group = [sop_a, sop_b, sop_c, sop_outlier]
    ranked = rank_group(group)
    last_score, last_idx, last_text = ranked[-1]
    _check(
        "outlier (YouTube) ranks last in a Gmail-heavy group",
        last_idx == 3,
        f"got last_idx={last_idx}, ranked={[(round(s,3), i) for s, i, _ in ranked]}",
    )
    checks += 1
    _check(
        "outlier score is meaningfully lower than the median Gmail score",
        last_score < ranked[1][0],
        f"outlier={last_score:.3f}  median={ranked[1][0]:.3f}",
    )
    checks += 1

    # 8. best_of_group returns the top-1 of rank_group.
    top = best_of_group(group)
    _check(
        "best_of_group equals rank_group()[0]",
        top == ranked[0],
    )
    checks += 1

    # 9. best_of_group raises on empty input.
    raised = False
    try:
        best_of_group([])
    except ValueError:
        raised = True
    _check("best_of_group([]) raises ValueError", raised)
    checks += 1

    # 10. SOPs with no parseable steps still get a row with score 0.0.
    bad = "this has no numbered steps"
    mixed = rank_group([sop_a, bad])
    bad_row = next(r for r in mixed if r[1] == 1)
    _check(
        "SOP without numbered steps gets score 0.0",
        bad_row[0] == 0.0,
        f"got {bad_row[0]}",
    )
    checks += 1

    print(f"\nAll {checks} checks passed.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
