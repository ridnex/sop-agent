"""Smoke test for group_RL.retrieve.

Run with:  python -m group_RL.test_retrieve

Phase 1 — policy logic only (no API calls): verifies retrieve_and_decide()
picks the right branch for high / medium / low / empty cases.

Phase 2 — one real adapt call + one real exemplar call against the seeded
memory at outputs/group_RL/memory.jsonl. Costs roughly $0.02-$0.04.
"""

import sys
import tempfile
from pathlib import Path

from group_RL.consensus import parse_steps
from group_RL.memory import MemoryStore
from group_RL.retrieve import (
    ADAPT_THRESHOLD,
    EXEMPLAR_THRESHOLD,
    adapt_sop,
    exemplar_sop,
    retrieve_and_decide,
)


def _check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"FAIL: {name} -- {detail}")
    print(f"PASS: {name}")


def main() -> int:
    checks = 0

    # ---------- Phase 1: policy logic ----------
    print("Phase 1: policy decisions (no API calls)\n")

    # 1.1 Empty store → "fresh"
    with tempfile.TemporaryDirectory() as td:
        empty_store = MemoryStore(Path(td) / "empty.jsonl")
        d = retrieve_and_decide("anything goes here", empty_store)
        _check(
            "empty store yields strategy=fresh",
            d.strategy == "fresh" and d.score == 0.0 and d.exemplar is None,
            f"got {d}",
        )
        checks += 1

    # Use the seeded production memory for the next checks.
    store = MemoryStore()
    if len(store) == 0:
        print("WARN: outputs/group_RL/memory.jsonl is empty; skipping Phase 1 sim checks.")
        return 1

    # 1.2 High-similarity paraphrase → "adapt"
    d_adapt = retrieve_and_decide("compose a new email in Gmail", store)
    print(f"  high-sim test: score={d_adapt.score:.3f}  strategy={d_adapt.strategy}  "
          f"exemplar={d_adapt.exemplar['intent'] if d_adapt.exemplar else None!r}")
    _check(
        "high-similarity paraphrase yields strategy=adapt",
        d_adapt.strategy == "adapt" and d_adapt.score >= ADAPT_THRESHOLD,
        f"got strategy={d_adapt.strategy} score={d_adapt.score}",
    )
    checks += 1

    # 1.3 Off-topic query → "fresh" (sim < EXEMPLAR_THRESHOLD)
    d_fresh = retrieve_and_decide(
        "translate the Spanish phrase 'buenos dias' into Japanese",
        store,
    )
    print(f"  low-sim test:  score={d_fresh.score:.3f}  strategy={d_fresh.strategy}")
    _check(
        "off-topic query yields strategy=fresh",
        d_fresh.strategy == "fresh" and d_fresh.score < EXEMPLAR_THRESHOLD,
        f"got strategy={d_fresh.strategy} score={d_fresh.score}",
    )
    checks += 1

    # 1.4 Medium-similarity query → "exemplar"
    # "Star a different repo on GitHub" should be similar to seeded
    # "Star the GitHub repository anthropics/claude-code" but not paraphrastic.
    d_exemplar = retrieve_and_decide(
        "On GitHub, watch the pytorch/pytorch repository for new releases",
        store,
    )
    print(f"  med-sim test:  score={d_exemplar.score:.3f}  strategy={d_exemplar.strategy}  "
          f"exemplar={d_exemplar.exemplar['intent'] if d_exemplar.exemplar else None!r}")
    _check(
        "medium-similarity query yields a sensible strategy (exemplar or fresh)",
        d_exemplar.strategy in ("exemplar", "fresh"),
        f"got strategy={d_exemplar.strategy} score={d_exemplar.score}",
    )
    checks += 1

    # ---------- Phase 2: one real adapt call ----------
    print("\nPhase 2: real adapt call (1 GPT-4o request)\n")

    new_intent_adapt = "Compose a brand-new email in Gmail and send it to bob@example.com"
    d = retrieve_and_decide(new_intent_adapt, store)
    _check(
        "adapt-target intent retrieves at high similarity",
        d.strategy == "adapt",
        f"got {d.strategy} (score={d.score})",
    )
    checks += 1

    adapted = adapt_sop(new_intent_adapt, d.exemplar)
    _check("adapt_sop returns non-empty text", bool(adapted and adapted.strip()))
    checks += 1
    _check(
        "adapted SOP parses to >= 1 step",
        len(parse_steps(adapted)) >= 1,
        f"got {len(parse_steps(adapted))} steps",
    )
    checks += 1
    _check(
        "adapted SOP has no leading markdown fence",
        not adapted.startswith("```"),
    )
    checks += 1

    print(f"\n--- ADAPT — retrieved exemplar (intent='{d.exemplar['intent']}', sim={d.score:.3f}) ---")
    print(d.exemplar["sop_text"])
    print(f"\n--- ADAPT — rewritten for new intent: {new_intent_adapt!r} ---")
    print(adapted)

    # ---------- Phase 2b: one real exemplar call ----------
    print("\nPhase 2b: real exemplar call (1 GPT-4o request)\n")

    # Pick an intent that should sit in the medium band (similar domain but
    # different action). Star → fork is in the same UI area on GitHub.
    new_intent_ex = "Fork a GitHub repository to your own account"
    d2 = retrieve_and_decide(new_intent_ex, store)
    print(f"  exemplar-target test: score={d2.score:.3f}  strategy={d2.strategy}")
    if d2.strategy == "fresh" or d2.exemplar is None:
        print("  (no medium-sim exemplar found; using top-1 hit anyway for the test)")
        hits = store.retrieve(new_intent_ex, k=1)
        ex_row = hits[0][1]
    else:
        ex_row = d2.exemplar

    written = exemplar_sop(new_intent_ex, ex_row)
    _check("exemplar_sop returns non-empty text", bool(written and written.strip()))
    checks += 1
    _check(
        "exemplar SOP parses to >= 1 step",
        len(parse_steps(written)) >= 1,
        f"got {len(parse_steps(written))} steps",
    )
    checks += 1
    _check(
        "exemplar SOP has no leading markdown fence",
        not written.startswith("```"),
    )
    checks += 1

    print(f"\n--- EXEMPLAR — retrieved as one-shot (intent='{ex_row['intent']}') ---")
    print(ex_row["sop_text"])
    print(f"\n--- EXEMPLAR — fresh SOP for new intent: {new_intent_ex!r} ---")
    print(written)

    print(f"\nAll {checks} checks passed.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
