"""Smoke test for group_RL.embedding.

Run with:  python -m group_RL.test_embedding
First run downloads ~130 MB of model weights into ~/.cache/huggingface/.
"""

import sys

import numpy as np

from group_RL.embedding import (
    EMBEDDING_DIM,
    cosine_similarity,
    embed_text,
    embed_texts,
    get_embedder,
)


def _check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"FAIL: {name} -- {detail}")
    print(f"PASS: {name}")


def main() -> int:
    checks = 0

    # 1. Model loads and is cached.
    m1 = get_embedder()
    m2 = get_embedder()
    _check("model is cached as singleton", m1 is m2)
    checks += 1

    # 2. Single embedding has correct shape & dtype.
    v = embed_text("hello world")
    _check(
        "embed_text shape & dtype",
        v.shape == (EMBEDDING_DIM,) and v.dtype == np.float32,
        f"got shape={v.shape} dtype={v.dtype}",
    )
    checks += 1

    # 3. Vectors are L2-normalized (norm ≈ 1.0).
    norm = float(np.linalg.norm(v))
    _check(
        "embedding is L2-normalized",
        abs(norm - 1.0) < 1e-3,
        f"got norm={norm}",
    )
    checks += 1

    # 4. Batch embedding has correct shape.
    M = embed_texts(["alpha", "bravo", "charlie"])
    _check(
        "embed_texts shape",
        M.shape == (3, EMBEDDING_DIM) and M.dtype == np.float32,
        f"got shape={M.shape} dtype={M.dtype}",
    )
    checks += 1

    # 5. Empty list edge case.
    E = embed_texts([])
    _check(
        "embed_texts empty list",
        E.shape == (0, EMBEDDING_DIM) and E.dtype == np.float32,
        f"got shape={E.shape} dtype={E.dtype}",
    )
    checks += 1

    # 6. Cosine similarity sanity: same text ≈ 1.0; unrelated < 0.5.
    same = float(cosine_similarity(v, v))
    _check("self-similarity ≈ 1.0", abs(same - 1.0) < 1e-3, f"got {same}")
    checks += 1

    a = embed_text("the cat sat on the mat")
    b = embed_text("quarterly earnings beat analyst estimates")
    unrelated = float(cosine_similarity(a, b))
    _check(
        "unrelated texts have low similarity (< 0.5)",
        unrelated < 0.5,
        f"got {unrelated}",
    )
    checks += 1

    # 7. Semantic similarity ordering.
    q = embed_text("send a Gmail message")
    pos = embed_text("compose and send an email in Gmail")
    neg = embed_text("post a tweet about cats")
    sim_pos = float(cosine_similarity(q, pos))
    sim_neg = float(cosine_similarity(q, neg))
    _check(
        "Gmail intent closer to email-compose than tweeting",
        sim_pos > sim_neg,
        f"sim_pos={sim_pos:.3f}  sim_neg={sim_neg:.3f}",
    )
    checks += 1

    # 8. Cross-type comparison works mechanically (intent vs. step).
    intent_vec = embed_text("send a Gmail message to Alice")
    step_vec = embed_text("Click the Compose button")
    cross = float(cosine_similarity(intent_vec, step_vec))
    _check(
        "intent-vs-step similarity is finite scalar",
        np.isfinite(cross),
        f"got {cross}",
    )
    checks += 1

    # 9. Multi-sentence intent works (3-5 sentences, single goal).
    long_intent = (
        "Open Gmail in the browser. Compose a new email to alice@example.com "
        "with the subject 'hello'. Type a short message body greeting Alice. "
        "Send the email and confirm it was sent."
    )
    L = embed_text(long_intent)
    _check(
        "multi-sentence intent embeds to (384,) float32",
        L.shape == (EMBEDDING_DIM,) and L.dtype == np.float32,
        f"got shape={L.shape} dtype={L.dtype}",
    )
    checks += 1

    # 10. Bonus: matrix-vs-matrix shape.
    sim_matrix = cosine_similarity(M, M)
    _check(
        "matrix-vs-matrix cosine returns (N, N)",
        sim_matrix.shape == (3, 3),
        f"got shape={sim_matrix.shape}",
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
