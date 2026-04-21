"""CLI for the RL data pipeline.

One SOP per invocation. The orchestrator runs it, applies inline repair on
stuck steps, and if the run still fails falls back to post-hoc regeneration
up to MAX_REGEN_DEPTH times.

Example:
    python -m rl_data.cli run --sop sop_04_github_star
"""

import argparse
import logging
import sys

from rl_data.config import ACTION_DELAY, MAX_STEPS
from rl_data.loader import load_sop
from rl_data.orchestrator import run_many


def main() -> int:
    parser = argparse.ArgumentParser(
        description="RL data pipeline: execute one SOP, validate, repair/regenerate on failure.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Execute a single SOP.")
    p_run.add_argument(
        "--sop",
        required=True,
        help="SOP id to execute (e.g. sop_04_github_star).",
    )
    p_run.add_argument("--max-steps", type=int, default=MAX_STEPS)
    p_run.add_argument("--delay", type=float, default=ACTION_DELAY)
    p_run.add_argument("--verbose", action="store_true")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    if args.command == "run":
        entry = load_sop(args.sop)
        print(f"Running SOP: {entry.id}")
        run_many([entry], max_steps=args.max_steps, delay=args.delay)
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
