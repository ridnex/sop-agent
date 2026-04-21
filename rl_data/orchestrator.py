"""Top-level loop: for each SOP, run → on failure, regenerate → rerun.

Each browser execution runs in a subprocess so Playwright's sync API doesn't
clash with the asyncio loop state left by Anthropic/OpenAI SDK calls made in
the parent (regeneration + jsonl bookkeeping).
"""

import json
import subprocess
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

from rl_data.config import MAX_REGEN_DEPTH, RL_DATA_DIR, RUNS_JSONL
from rl_data.models import RunRecord, SOPEntry
from rl_data.regen import build_regenerated_entry


def _append_record(record: RunRecord) -> None:
    RL_DATA_DIR.mkdir(parents=True, exist_ok=True)
    with RUNS_JSONL.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")


def _run_in_subprocess(
    entry: SOPEntry,
    max_steps: int,
    delay: float,
) -> list[RunRecord]:
    """Run one SOPEntry by spawning a fresh Python process.

    Returns the list of RunRecords the worker produced. The worker emits one record
    for the original + an optional second record for the repaired variant when inline
    repair rescued the run.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        in_path = Path(tmpdir) / "entry.json"
        out_path = Path(tmpdir) / "record.json"

        with in_path.open("w", encoding="utf-8") as f:
            json.dump(asdict(entry), f, ensure_ascii=False)

        cmd = [
            sys.executable, "-m", "rl_data.worker",
            "--input", str(in_path),
            "--output", str(out_path),
            "--max-steps", str(max_steps),
            "--delay", str(delay),
        ]

        # Inherit stdout/stderr so user sees the execution progress live.
        result = subprocess.run(cmd, check=False)

        if not out_path.exists():
            raise RuntimeError(
                f"Worker for {entry.id} [{entry.variant}] did not produce a record "
                f"(exit code {result.returncode})."
            )

        with out_path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        # Backward compat: tolerate a single-record dict from older workers.
        if isinstance(data, dict):
            data = [data]
        return [RunRecord(**row) for row in data]


def process_entry(entry: SOPEntry, max_steps: int, delay: float) -> list[RunRecord]:
    """Run one entry, then regenerate-and-rerun up to MAX_REGEN_DEPTH times on failure.

    Each depth iteration may produce 1 or 2 RunRecords (the latter when inline repair
    rescued the run). We break as soon as any record in the most recent batch is good.
    """
    all_records: list[RunRecord] = []
    current = entry
    for depth in range(MAX_REGEN_DEPTH + 1):
        batch = _run_in_subprocess(
            current,
            max_steps=max_steps,
            delay=delay,
        )
        for r in batch:
            all_records.append(r)
            _append_record(r)

        if any(r.label == "good" for r in batch):
            break
        if depth == MAX_REGEN_DEPTH:
            break

        # Regen targets the original (first) record of the batch — that's the one
        # actually tied to `current`. The optional repaired record is a child, not a
        # re-run target.
        primary = batch[0]
        next_entry = build_regenerated_entry(current, primary, depth + 1)
        if next_entry is None:
            break
        current = next_entry

    return all_records


def run_many(entries: list[SOPEntry], max_steps: int, delay: float) -> None:
    for i, entry in enumerate(entries, 1):
        print(f"\n=== [{i}/{len(entries)}] SOP: {entry.id} ===")
        try:
            process_entry(entry, max_steps=max_steps, delay=delay)
        except Exception as e:
            print(f"  ! failed to process {entry.id}: {e}")
