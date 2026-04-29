"""Smoke test for group_RL.memory.

Run with:  python -m group_RL.test_memory

Uses a temporary directory so it never touches outputs/.
"""

import sys
import tempfile
from pathlib import Path

from group_RL.memory import MemoryStore


def _check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"FAIL: {name} -- {detail}")
    print(f"PASS: {name}")


def main() -> int:
    checks = 0

    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "memory.jsonl"

        # 1. Empty store: file does not exist yet, length is 0, retrieve returns [].
        store = MemoryStore(path)
        _check("empty store has length 0", len(store) == 0)
        checks += 1
        _check("retrieve on empty store returns []", store.retrieve("anything") == [])
        checks += 1
        _check("file is not created until first add", not path.exists())
        checks += 1

        # 2. Add one row, length 1, JSONL file now exists with exactly one line.
        row = store.add("send a Gmail", "1. Open Gmail.\n2. Click Compose.")
        _check("add returns row with intent", row["intent"] == "send a Gmail")
        checks += 1
        _check("add returns row with embedder_model recorded", row["embedder_model"].startswith("BAAI/"))
        checks += 1
        _check("add returns row with embedding length 384", len(row["embedding"]) == 384)
        checks += 1
        _check("len(store) == 1 after one add", len(store) == 1)
        checks += 1
        _check("memory.jsonl was created on add", path.exists())
        checks += 1
        _check("memory.jsonl has exactly one line", len(path.read_text().strip().split("\n")) == 1)
        checks += 1

        # 3. Self-retrieve gives near-1.0 similarity.
        hits = store.retrieve("send a Gmail", k=1)
        _check("self-retrieve returns one hit", len(hits) == 1)
        checks += 1
        score, retrieved = hits[0]
        _check(
            "self-retrieve similarity ~ 1.0",
            abs(score - 1.0) < 1e-3,
            f"got {score}",
        )
        checks += 1
        _check(
            "self-retrieve returns the right SOP text",
            retrieved["sop_text"].startswith("1. Open Gmail."),
        )
        checks += 1

        # 4. Add several rows; retrieve top-3 ranks by intent similarity.
        store.add("compose and send an email in Gmail", "...sop A...")
        store.add("post a tweet about cats", "...sop B...")
        store.add("open Google Calendar and create an event", "...sop C...")
        _check("len(store) == 4 after three more adds", len(store) == 4)
        checks += 1

        hits = store.retrieve("send a Gmail message", k=3)
        _check("retrieve k=3 returns 3 hits", len(hits) == 3)
        checks += 1
        # Top hit should be Gmail-related (either the original "send a Gmail"
        # or the "compose and send an email in Gmail" paraphrase).
        top_intent = hits[0][1]["intent"]
        _check(
            "top-1 hit is Gmail-related",
            "Gmail" in top_intent,
            f"got intent={top_intent!r}",
        )
        checks += 1
        # Tweet should NOT be in top-2 — it's the unrelated one.
        top2_intents = [h[1]["intent"] for h in hits[:2]]
        _check(
            "top-2 hits do not include the tweet outlier",
            not any("tweet" in i for i in top2_intents),
            f"got top-2={top2_intents}",
        )
        checks += 1
        # Scores must be sorted descending.
        scores = [h[0] for h in hits]
        _check(
            "hits are sorted by score descending",
            scores == sorted(scores, reverse=True),
            f"got scores={scores}",
        )
        checks += 1

        # 5. k larger than store size returns all rows without error.
        all_hits = store.retrieve("anything", k=999)
        _check(
            "k > len(store) returns len(store) rows",
            len(all_hits) == len(store),
            f"got {len(all_hits)}, expected {len(store)}",
        )
        checks += 1

        # 6. Persistence: re-open the same path, content is preserved.
        store2 = MemoryStore(path)
        _check(
            "new MemoryStore on same path loads all rows",
            len(store2) == 4,
            f"got len={len(store2)}",
        )
        checks += 1
        hits2 = store2.retrieve("send a Gmail", k=1)
        _check(
            "self-retrieve after reload still scores ~1.0",
            abs(hits2[0][0] - 1.0) < 1e-3,
            f"got {hits2[0][0]}",
        )
        checks += 1

        # 7. Custom metadata round-trips.
        store2.add(
            "delete a file in Finder",
            "1. Right-click. 2. Move to Trash.",
            label="bad",
            failed_step=2,
            failure_reason="permission denied",
        )
        hits3 = store2.retrieve("delete a file in Finder", k=1)
        _check(
            "metadata round-trips through retrieve",
            hits3[0][1]["label"] == "bad"
            and hits3[0][1]["failed_step"] == 2
            and hits3[0][1]["failure_reason"] == "permission denied",
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
