"""Validated-SOP memory store backed by an append-only JSONL file.

Each row stores the intent, the SOP text, an inline cached embedding of the
intent (so loads are O(rows) and don't re-embed), and arbitrary metadata.
Retrieval embeds the query intent once and scores it against all stored
embeddings via cosine similarity (= dot product, since vectors are
L2-normalized).

Typical use:

    from pathlib import Path
    from group_RL.memory import MemoryStore

    store = MemoryStore(Path("outputs/group_RL/memory.jsonl"))
    store.add("send a Gmail", "1. Open Gmail. 2. ...", label="good")
    hits = store.retrieve("compose an email in Gmail", k=3)
    for score, row in hits:
        print(score, row["sop_text"])

The embedding model is recorded on every row (`embedder_model`). Mixing
rows from different embedders inside one file is unsafe — change the
file path if you swap models.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from config import BASE_DIR
from group_RL.embedding import (
    EMBEDDING_DIM,
    MODEL_NAME,
    cosine_similarity,
    embed_text,
)

GROUP_RL_DIR = BASE_DIR / "outputs" / "group_RL"
DEFAULT_MEMORY_PATH = GROUP_RL_DIR / "memory.jsonl"
BAD_MEMORY_PATH = GROUP_RL_DIR / "bad_memory.jsonl"


class MemoryStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else DEFAULT_MEMORY_PATH
        self._rows: list[dict[str, Any]] = []
        self._embeddings: np.ndarray = np.zeros((0, EMBEDDING_DIM), dtype=np.float32)
        self._load()

    # ---------- public API ----------

    def __len__(self) -> int:
        return len(self._rows)

    def add(
        self,
        intent: str,
        sop_text: str,
        label: str = "good",
        **metadata: Any,
    ) -> dict[str, Any]:
        """Embed the intent, append a row to the JSONL, update the in-memory cache.

        Returns the row that was appended (with the embedding inlined).
        """
        vec = embed_text(intent)
        row: dict[str, Any] = {
            "intent": intent,
            "sop_text": sop_text,
            "label": label,
            "embedder_model": MODEL_NAME,
            "embedding": vec.tolist(),
            "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            **metadata,
        }
        self._append_to_disk(row)
        self._rows.append(row)
        self._embeddings = np.vstack([self._embeddings, vec[None, :]])
        return row

    def retrieve(self, intent: str, k: int = 1) -> list[tuple[float, dict[str, Any]]]:
        """Return the top-k rows most similar to `intent`, sorted by score desc.

        If the store is empty, returns []. If k > len(self), returns all rows.
        Each tuple is (cosine_similarity, row_dict). The embedding field is
        kept on the row in case the caller wants it; strip it if not needed.
        """
        if len(self._rows) == 0:
            return []
        q = embed_text(intent)
        sims = cosine_similarity(q, self._embeddings)
        k_eff = min(k, len(self._rows))
        top_idx = np.argsort(sims)[::-1][:k_eff]
        return [(float(sims[i]), self._rows[i]) for i in top_idx]

    # ---------- internals ----------

    def _load(self) -> None:
        if not self.path.exists():
            return
        rows: list[dict[str, Any]] = []
        embs: list[np.ndarray] = []
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                emb = row.get("embedding")
                if emb is None:
                    continue
                vec = np.asarray(emb, dtype=np.float32)
                if vec.shape != (EMBEDDING_DIM,):
                    continue
                rows.append(row)
                embs.append(vec)
        self._rows = rows
        self._embeddings = (
            np.stack(embs).astype(np.float32)
            if embs
            else np.zeros((0, EMBEDDING_DIM), dtype=np.float32)
        )

    def _append_to_disk(self, row: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
