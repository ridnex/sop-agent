"""CLI: collect labeled (SOP, outcome) data for RL training.

Usage:
    python -m sop_data.main --sop-file path/to/sop.txt
    python -m sop_data.main --sop-file sop.txt --name gmail_compose --intent "send test email"

Flow:
    sop.txt -> execute (web) -> validate
        good -> label v0 "good", stop
        bad  -> label v0 "bad", Claude repairs, execute v1, validate, stop (either label)
"""

import argparse
import logging
import sys
from pathlib import Path

from sop_data.pipeline import run_one


def main():
    parser = argparse.ArgumentParser(
        description="Run one SOP through execute->validate, repair once on failure, log to runs.jsonl.",
    )
    parser.add_argument("--sop-file", type=Path, required=True, help="Path to SOP text file.")
    parser.add_argument("--name", default=None, help="SOP id (defaults to file stem).")
    parser.add_argument("--intent", default="", help="High-level goal description.")
    parser.add_argument("--url", default="about:blank", help="Initial URL (default about:blank).")
    parser.add_argument("--max-steps", type=int, default=50, help="Max tool-use steps per run.")
    parser.add_argument("--delay", type=float, default=2.0, help="Seconds after each action.")
    parser.add_argument("--launch", action="store_true", help="Launch a new browser instead of connecting to Chrome via CDP.")
    parser.add_argument("--headless", action="store_true", help="Headless browser (only with --launch).")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging.")
    parser.add_argument(
        "--struggle-repeat-threshold", type=int, default=4,
        help="Min times the same SOP step can repeat before being flagged as struggle (default 4).",
    )
    parser.add_argument(
        "--struggle-overshoot-ratio", type=float, default=3.0,
        help="Trigger struggle when exec_steps > ratio * sop_steps (default 3.0).",
    )
    parser.add_argument(
        "--no-struggle-check", action="store_true",
        help="Disable struggle detection on v0 (good-by-validator always stays 'good').",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    if not args.sop_file.exists():
        print(f"Error: SOP file not found: {args.sop_file}", file=sys.stderr)
        sys.exit(1)

    summary = run_one(
        sop_path=args.sop_file,
        name=args.name,
        intent=args.intent,
        start_url=args.url,
        max_steps=args.max_steps,
        delay=args.delay,
        headless=args.headless,
        launch=args.launch,
        struggle_repeat_threshold=args.struggle_repeat_threshold,
        struggle_overshoot_ratio=args.struggle_overshoot_ratio,
        struggle_check=not args.no_struggle_check,
    )

    print("\n=== Run summary ===")
    print(f"sop_id: {summary['sop_id']}")
    for v in summary["variants"]:
        print(f"  {v['variant']}: {v['label']}")

    any_good = any(v["label"] == "good" for v in summary["variants"])
    sys.exit(0 if any_good else 2)


if __name__ == "__main__":
    main()
