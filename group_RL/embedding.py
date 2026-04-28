"""Sentence embedding helpers for retrieval and group-consensus ranking.

Single local model (bge-small-en-v1.5, 384-dim, ~130MB, CPU-only) used for
every embedding task in the group_RL pipeline:
  - intent ↔ intent retrieval against memory
  - step ↔ step similarity for group-consensus ranking
  - any other text-similarity need that comes up later

The model is loaded lazily and cached as a module-level singleton, so the
~1s load cost is paid exactly once per process.
"""

from __future__ import annotations

import numpy as np
from sentence_transformers import SentenceTransformer

MODEL_NAME = "BAAI/bge-small-en-v1.5"
EMBEDDING_DIM = 384

_model: SentenceTransformer | None = None


def get_embedder() -> SentenceTransformer:
    """Return a cached SentenceTransformer instance, loading on first call."""
    global _model
    if _model is None:
        _model = SentenceTransformer(MODEL_NAME)
    return _model


def embed_text(text: str) -> np.ndarray:
    """Embed a single string into a 384-dim L2-normalized vector."""
    model = get_embedder()
    vec = model.encode(text, normalize_embeddings=True)
    return np.asarray(vec, dtype=np.float32)


def embed_texts(texts: list[str]) -> np.ndarray:
    """Embed a list of strings into an (N, 384) L2-normalized matrix.

    Batched internally for efficiency. Empty list returns an (0, 384) array.
    """
    if not texts:
        return np.zeros((0, EMBEDDING_DIM), dtype=np.float32)
    model = get_embedder()
    mat = model.encode(texts, normalize_embeddings=True, convert_to_numpy=True)
    return np.asarray(mat, dtype=np.float32)


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Cosine similarity between L2-normalized vectors.

    Inputs must come from embed_text / embed_texts (already normalized), so
    cosine reduces to a plain dot product.

    Shapes:
      (D,)   vs (D,)   → scalar
      (D,)   vs (N, D) → (N,)
      (N, D) vs (M, D) → (N, M)
    """
    return np.asarray(a) @ np.asarray(b).T
