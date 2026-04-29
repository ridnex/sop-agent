"""Static group-consensus ranking for a group of G candidate SOPs.

Given G SOPs sampled from an LLM for the same intent, score each one by how
much its steps agree with the steps in the other G−1 siblings. The winner is
the SOP closest to "what the group agreed on" — outliers get penalized.

No execution, no validator, no API calls — only the local embedder. The
intuition: LLMs hallucinate idiosyncratically (each sample's hallucination
differs) but converge consistently on correct content. Steps that appear in
many siblings are likely right; steps that appear in only one are likely
noise.

Public API:
    parse_steps(sop_text)            → list[str]
    rank_group(sops)                 → list[(score, index, sop_text)]
    best_of_group(sops)              → (score, index, sop_text)
"""

from __future__ import annotations

import re

import numpy as np

from group_RL.embedding import cosine_similarity, embed_texts

_STEP_HEADER = re.compile(r"^\s*(\d+)\.\s+(.*)$")


def parse_steps(sop_text: str) -> list[str]:
    """Extract numbered steps from an SOP, absorbing continuation lines.

    A "step" begins on a line matching `^\\s*\\d+\\.\\s+` and continues
    onto subsequent non-numbered, non-blank lines until the next step
    marker or end of text. Returns the trimmed text of each step,
    *without* the leading number.

    Returns an empty list if no numbered steps are found.
    """
    steps: list[list[str]] = []
    for line in (sop_text or "").splitlines():
        m = _STEP_HEADER.match(line)
        if m:
            steps.append([m.group(2).strip()])
        elif steps and line.strip():
            # continuation line for the most recent step
            steps[-1].append(line.strip())
    return [" ".join(parts).strip() for parts in steps if parts]


def rank_group(sops: list[str]) -> list[tuple[float, int, str]]:
    """Rank each SOP by step-level agreement with siblings.

    Algorithm:
      1. Parse every SOP into its steps and embed them in one batched call.
      2. For each step s in SOP i, find the best-matching step (max cosine)
         in every other SOP j ≠ i. The step's consensus score is the mean
         of those best-matches across siblings.
      3. The SOP's score is the mean of its step consensus scores.

    Returns a list of (score, original_index, sop_text) tuples sorted by
    score descending. SOPs with no parseable steps receive score 0.0.
    Empty input returns an empty list. With a single SOP (no siblings),
    that SOP's score is 0.0 by convention (no group to compare against).
    """
    n = len(sops)
    if n == 0:
        return []

    parsed: list[list[str]] = [parse_steps(s) for s in sops]
    flat_steps: list[str] = []
    offsets: list[tuple[int, int]] = []
    for steps in parsed:
        start = len(flat_steps)
        flat_steps.extend(steps)
        offsets.append((start, len(flat_steps)))

    if not flat_steps:
        return [(0.0, i, sops[i]) for i in range(n)]

    embs = embed_texts(flat_steps)               # (total, 384)
    sims = cosine_similarity(embs, embs)         # (total, total)

    scores: list[float] = []
    for i, (s_i, e_i) in enumerate(offsets):
        if e_i == s_i:
            scores.append(0.0)
            continue
        step_scores: list[float] = []
        for step_idx in range(s_i, e_i):
            best_per_sibling: list[float] = []
            for j, (s_j, e_j) in enumerate(offsets):
                if j == i or e_j == s_j:
                    continue
                row = sims[step_idx, s_j:e_j]
                best_per_sibling.append(float(row.max()))
            if best_per_sibling:
                step_scores.append(float(np.mean(best_per_sibling)))
        scores.append(float(np.mean(step_scores)) if step_scores else 0.0)

    ranked = sorted(
        zip(scores, range(n), sops),
        key=lambda triple: triple[0],
        reverse=True,
    )
    return ranked


def best_of_group(sops: list[str]) -> tuple[float, int, str]:
    """Return the (score, index, sop_text) of the highest-consensus SOP.

    Raises ValueError on empty input.
    """
    ranked = rank_group(sops)
    if not ranked:
        raise ValueError("best_of_group: empty SOP list")
    return ranked[0]
