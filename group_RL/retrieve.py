"""Retrieval policy: decide what to do with the closest match in memory.

Given a new intent and the validated-SOP memory, pick one of three branches
based on the top-1 cosine similarity:

  - sim ≥ ADAPT_THRESHOLD     → adapt the retrieved SOP to the new intent
                                (single GPT call, low temperature, conservative)
  - sim ≥ EXEMPLAR_THRESHOLD  → use the retrieved SOP as a one-shot exemplar
                                in the prompt for a fresh write
                                (single GPT call, normal temperature)
  - otherwise                  → ignore retrieval; caller should run
                                generate_group() with no exemplar

The two retrieval-augmented generators (adapt_sop, exemplar_sop) live here
because they're tightly coupled to the retrieval row — they need both the
retrieved intent and the retrieved SOP text to do their jobs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sop.api_client import call_openai

from group_RL.generate import _host_os_hint, _strip_markdown_fences
from group_RL.memory import MemoryStore

ADAPT_THRESHOLD = 0.85
EXEMPLAR_THRESHOLD = 0.70

DEFAULT_MODEL = "gpt-4o"
ADAPT_TEMPERATURE = 0.3
EXEMPLAR_TEMPERATURE = 0.7

Strategy = Literal["adapt", "exemplar", "fresh"]


@dataclass
class RetrievalDecision:
    strategy: Strategy
    score: float                  # top-1 cosine similarity, 0.0 if memory empty
    exemplar: dict | None         # the retrieved row (intent + sop_text + ...) or None


# ---------- policy ----------

def retrieve_and_decide(intent: str, store: MemoryStore) -> RetrievalDecision:
    """Pick a retrieval strategy for `intent` based on the top-1 similarity."""
    if len(store) == 0:
        return RetrievalDecision("fresh", 0.0, None)

    hits = store.retrieve(intent, k=1)
    score, row = hits[0]

    if score >= ADAPT_THRESHOLD:
        return RetrievalDecision("adapt", score, row)
    if score >= EXEMPLAR_THRESHOLD:
        return RetrievalDecision("exemplar", score, row)
    return RetrievalDecision("fresh", score, None)


# ---------- adapt branch (high-similarity, conservative single rewrite) ----------

_ADAPT_PROMPT = """You are editing an existing Standard Operating Procedure (SOP) to fit a new but very similar task. The original SOP was already validated to work; the new task only differs from the original in small ways.

# Original task
{retrieved_intent}

# Original SOP (proven to work)
{retrieved_sop}

# New task
{new_intent}

# Instructions
- Modify ONLY the steps that need to change for the new task. Keep every other step byte-identical.
- If a step still applies, leave it alone.
- If a step partially applies, edit it minimally.
- If a step is irrelevant to the new task, remove it.
- If the new task needs a step the original SOP did not have, insert it in the natural position.
- Preserve the original numbering style and any OS-specific keystrokes (Cmd vs Ctrl).
- Output ONLY the rewritten numbered list — no preamble, no explanation, no markdown code fences.
{os_note}

Write the adapted SOP now."""


def adapt_sop(
    new_intent: str,
    retrieved_row: dict,
    model: str = DEFAULT_MODEL,
    temperature: float = ADAPT_TEMPERATURE,
) -> str:
    """Surgically rewrite a retrieved SOP for a near-paraphrase intent.

    Single GPT call at low temperature — preserve what works, change only
    what the new intent demands. Returns the adapted SOP text, fences stripped.
    """
    prompt = _ADAPT_PROMPT.format(
        retrieved_intent=retrieved_row["intent"],
        retrieved_sop=retrieved_row["sop_text"],
        new_intent=new_intent,
        os_note=_host_os_hint(),
    )
    raw = call_openai(
        messages=[{"role": "user", "content": prompt}],
        model=model,
        temperature=temperature,
    )
    return _strip_markdown_fences(raw)


# ---------- exemplar branch (medium-similarity, fresh write with one-shot) ----------

_EXEMPLAR_PROMPT = """You are writing a Standard Operating Procedure (SOP) for an AI agent that will follow it inside a Chrome browser. To help you, here is an example SOP for a similar but different task that was successfully executed.

# Example task
{retrieved_intent}

# Example SOP (worked for the example task above)
{retrieved_sop}

# Your task
{new_intent}

# Instructions
- Use the example as a STYLE GUIDE for structure, length, and level of detail.
- Do NOT copy the example's specific URLs, button labels, or steps verbatim — adapt every step to YOUR task.
- Reference UI elements by their visible label or role (e.g. 'the "Save" button'), not pixel coordinates.
- If your task requires being signed in, include a "wait for me to log in" step.
- Output ONLY the new numbered list — no preamble, no explanation, no markdown code fences.
{os_note}

Write the new SOP now."""


def exemplar_sop(
    new_intent: str,
    retrieved_row: dict,
    model: str = DEFAULT_MODEL,
    temperature: float = EXEMPLAR_TEMPERATURE,
) -> str:
    """Write a fresh SOP for `new_intent`, using a retrieved SOP as a one-shot.

    Single GPT call at moderate temperature — the exemplar pins the style,
    the intent forces the content. Returns the new SOP text, fences stripped.
    """
    prompt = _EXEMPLAR_PROMPT.format(
        retrieved_intent=retrieved_row["intent"],
        retrieved_sop=retrieved_row["sop_text"],
        new_intent=new_intent,
        os_note=_host_os_hint(),
    )
    raw = call_openai(
        messages=[{"role": "user", "content": prompt}],
        model=model,
        temperature=temperature,
    )
    return _strip_markdown_fences(raw)
