"""CLI entry point for the group_RL pipeline.

Usage:
    python -m group_RL.main --intent "send a Gmail to alice@example.com"
    python -m group_RL.main --intent "..." --n-group 5 --launch --headless

Thin argparse layer on top of group_RL.pipeline.run_one. Exit code is 0
when the run produced a good final SOP (v0 or v1 succeeded), 1 otherwise.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from group_RL.pipeline import run_one


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "group_RL: retrieve → generate (G-of-G consensus) → execute → "
            "validate → memory writeback for one intent."
        ),
    )
    parser.add_argument(
        "--intent",
        required=True,
        help="Natural-language goal to execute (required).",
    )
    parser.add_argument(
        "--start-url",
        default="about:blank",
        help="Initial URL (default: about:blank — SOP should contain navigation).",
    )
    parser.add_argument(
        "--n-group",
        type=int,
        default=3,
        help="Group size G for fresh generation and repair (default: 3).",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=50,
        help="Max executor steps per attempt (default: 50).",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=2.0,
        help="Seconds to wait after each browser action for page settling (default: 2.0).",
    )
    parser.add_argument(
        "--launch",
        action="store_true",
        help="Launch a fresh browser instead of connecting to existing Chrome.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run browser without visible window (only with --launch).",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=None,
        help="Override the outputs/group_RL/ root directory (mostly for tests).",
    )

    args = parser.parse_args(argv)

    summary = run_one(
        intent=args.intent,
        start_url=args.start_url,
        n_group=args.n_group,
        max_steps=args.max_steps,
        delay=args.delay,
        launch=args.launch,
        headless=args.headless,
        output_root=args.output_root,
    )

    print()
    print("=" * 60)
    print(f"final_label:          {summary['final_label']}")
    print(
        f"strategy:             {summary['strategy']}  "
        f"(retrieval score={summary['retrieval_score']:.3f})"
    )
    print(f"memory_writeback:     {summary['memory_writeback']}")
    print(f"bad_memory_writeback: {summary['bad_memory_writeback']}")
    print("=" * 60)

    return 0 if summary["final_label"] == "good" else 1


if __name__ == "__main__":
    sys.exit(main())
