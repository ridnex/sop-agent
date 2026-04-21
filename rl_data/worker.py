"""Subprocess worker: runs one SOP execution + validation, writes a RunRecord JSON.

Invoked by the orchestrator as:
    python -m rl_data.worker --input <entry.json> --output <record.json> [--launch] [--max-steps N] [--delay S]

Each invocation is a fresh Python process so Playwright's sync API doesn't clash
with the asyncio loop state left by Anthropic/OpenAI SDK calls.
"""

import argparse
import json
import sys
from dataclasses import asdict

from rl_data.models import SOPEntry
from rl_data.runner import run_one


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Path to SOPEntry JSON.")
    parser.add_argument("--output", required=True, help="Path to write RunRecord JSON.")
    parser.add_argument("--max-steps", type=int, default=25)
    parser.add_argument("--delay", type=float, default=2.0)
    args = parser.parse_args()

    with open(args.input, "r", encoding="utf-8") as f:
        data = json.load(f)

    entry = SOPEntry(**data)

    records, _ = run_one(
        entry,
        max_steps=args.max_steps,
        delay=args.delay,
        auto_confirm=True,
    )

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump([asdict(r) for r in records], f, ensure_ascii=False, indent=2)

    # Return 0 if any record in the batch is good; parent doesn't rely on this.
    return 0 if any(r.label == "good" for r in records) else 1


if __name__ == "__main__":
    sys.exit(main())
