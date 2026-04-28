"""CLI entry point for the web SOP execution agent.

Usage:
    # Execute SOP in Chrome via Claude Computer Use:
    python -m web.execute.main --sop-file path/to/sop.txt --yes

    # With options:
    python -m web.execute.main --sop-file sop.txt --yes --max-steps 30 --delay 1.5
"""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

from web.execute.config import BASE_DIR, OUTPUTS_DIR
from web.execute.agent import run_agent


def main():
    parser = argparse.ArgumentParser(
        description="Execute an SOP in Chrome using Claude Computer Use (CDP-based).",
    )
    parser.add_argument(
        "--sop-file",
        type=Path,
        required=True,
        help="Path to the SOP text file.",
    )
    parser.add_argument(
        "--url",
        default="about:blank",
        help="Initial URL to navigate to (default: about:blank — SOP should contain navigation).",
    )
    parser.add_argument(
        "--intent",
        default="",
        help="High-level intent/goal description.",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=50,
        help="Maximum number of tool-use steps (default: 50).",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=2.0,
        help="Seconds to wait after each action for page settling (default: 2.0).",
    )
    parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Skip confirmation prompt.",
    )
    parser.add_argument(
        "--launch",
        action="store_true",
        help="Launch a new browser instead of connecting to existing Chrome.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run browser without visible window (only with --launch).",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Claude model override (default: from config).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Custom output directory (default: outputs/web_executions/exec_<name>_<ts>/).",
    )
    parser.add_argument(
        "--platform",
        default=None,
        choices=[None, "darwin", "windows", "linux"],
        help="Host OS for keyboard-shortcut guidance (default: auto-detect).",
    )

    args = parser.parse_args()

    # Setup logging
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    # Load SOP
    if not args.sop_file.exists():
        print(f"Error: SOP file not found: {args.sop_file}", file=sys.stderr)
        sys.exit(1)

    sop_text = args.sop_file.read_text(encoding="utf-8").strip()
    if not sop_text:
        print("Error: SOP text is empty.", file=sys.stderr)
        sys.exit(1)

    sop_name = args.sop_file.stem

    # Create output directory
    if args.output_dir:
        output_dir = args.output_dir
    else:
        timestamp = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
        output_dir = OUTPUTS_DIR / f"exec_{sop_name}_{timestamp}"

    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"Output directory: {output_dir}")

    # Run the agent
    log = run_agent(
        sop_text=sop_text,
        output_dir=output_dir,
        intent=args.intent,
        start_url=args.url,
        max_steps=args.max_steps,
        delay=args.delay,
        auto_confirm=args.yes,
        headless=args.headless,
        launch=args.launch,
        model=args.model,
        platform_name=args.platform,
    )

    sys.exit(0 if log.completed_successfully else 1)


if __name__ == "__main__":
    main()
