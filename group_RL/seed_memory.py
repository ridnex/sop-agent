"""One-shot seed of the good-memory store with SOPs already validated by hand.

Run with:  python -m group_RL.seed_memory

Idempotent — re-running skips intents that are already in the store.
Each seed entry maps a natural-language intent to one of the user's
known-good SOP files under outputs/sop_data/sops/.
"""

from __future__ import annotations

from pathlib import Path

from config import BASE_DIR
from group_RL.memory import MemoryStore

SOPS_DIR = BASE_DIR / "outputs" / "sop_data" / "sops"

# (intent, sop_filename relative to SOPS_DIR)
SEEDS: list[tuple[str, str]] = [
    ("Compose and send a new email in Gmail",
     "sop_01_gmail_compose__v0.txt"),
    ("Open the first unread email in the Gmail inbox",
     "sop_02_gmail_open_unread__v0.txt"),
    ("Create a new event in Google Calendar",
     "sop_03_calendar_create_event__v0.txt"),
    ("Star the GitHub repository anthropics/claude-code",
     "sop_04_github_star__v0.txt"),
    ("Create a new issue on a GitHub repository",
     "sop_05_github_create_issue__v0.txt"),
    ("Run a search query on Google",
     "sop_06_google_search__v0.txt"),
    ("Play a video on YouTube",
     "sop_08_youtube_play__v1.txt"),
    ("Create a new blank Google Doc",
     "sop_13_docs_create__v1.txt"),
]


def _strip_markdown_fences(text: str) -> str:
    """Remove leading/trailing ```...``` fences emitted by Claude's repair output."""
    text = text.strip()
    if text.startswith("```"):
        nl = text.find("\n")
        text = text[nl + 1:] if nl != -1 else ""
    if text.endswith("```"):
        nl = text.rfind("\n")
        text = text[:nl] if nl != -1 else ""
    return text.strip()


def main() -> int:
    store = MemoryStore()
    existing_intents = {row["intent"] for row in store._rows}

    added = 0
    skipped = 0
    missing = 0

    for intent, filename in SEEDS:
        if intent in existing_intents:
            print(f"SKIP (already in memory): {intent}")
            skipped += 1
            continue

        path = SOPS_DIR / filename
        if not path.exists():
            print(f"MISS (file not found): {filename}")
            missing += 1
            continue

        raw = path.read_text(encoding="utf-8")
        sop_text = _strip_markdown_fences(raw)
        n_lines = len([ln for ln in sop_text.splitlines() if ln.strip()])

        store.add(
            intent=intent,
            sop_text=sop_text,
            label="good",
            source=f"outputs/sop_data/sops/{filename}",
            seeded=True,
        )
        print(f"ADD: {intent}  ({n_lines} lines, from {filename})")
        added += 1

    print()
    print(f"Seed summary: {added} added, {skipped} skipped, {missing} missing.")
    print(f"Memory now holds {len(store)} rows at {store.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
