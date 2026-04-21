"""CLI entry point for the SOP execution agent.

Usage:
    # Execute SOP from recorded experiment:
    python -m execute.main test_video1

    # Auto-confirm, custom step limit:
    python -m execute.main test_video1 --yes --max-steps 30

    # Standalone SOP file:
    python -m execute.main --sop-file /path/to/sop.txt --yes
"""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

from config import BASE_DIR
from execute.agent import run_agent

OUTPUTS_DIR = BASE_DIR / "outputs"

# SOP method file names
SOP_METHODS = {
    "wd": "method_wd.txt",
    "wd_kf": "method_wd_kf.txt",
    "wd_kf_act": "method_wd_kf_act.txt",
}


def _find_experiment_folder(name: str) -> Path | None:
    """Find an experiment folder by prefix name.

    Experiment folders are named like 'test_video1 @ 2026-03-10-13-23-53'.
    We match by the prefix before ' @ '.
    Returns the most recent match.
    """
    if not OUTPUTS_DIR.exists():
        return None

    matches = []
    for entry in OUTPUTS_DIR.iterdir():
        if entry.is_dir() and entry.name.startswith(name + " @ "):
            matches.append(entry)

    if not matches:
        return None

    # Return the most recent (sorted by name, which includes timestamp)
    return sorted(matches)[-1]


def _load_sop_text(folder: Path, method: str) -> str:
    """Load SOP text from an experiment folder."""
    filename = SOP_METHODS.get(method)
    if not filename:
        raise ValueError(f"Unknown SOP method: {method}. Choose from: {list(SOP_METHODS.keys())}")

    sop_file = folder / filename
    if not sop_file.exists():
        raise FileNotFoundError(f"SOP file not found: {sop_file}")

    return sop_file.read_text(encoding="utf-8").strip()


def _load_intent(folder: Path) -> str:
    """Load the intent/prompt from an experiment folder."""
    prompt_file = folder / "prompt.txt"
    if prompt_file.exists():
        return prompt_file.read_text(encoding="utf-8").strip()
    return ""


def main():
    parser = argparse.ArgumentParser(
        description="Execute an SOP on the macOS desktop using GPT-4o vision.",
    )
    parser.add_argument(
        "name",
        nargs="?",
        help="Experiment name prefix (e.g., 'test_video1'). "
             "Finds the most recent matching folder in outputs/.",
    )
    parser.add_argument(
        "--sop-file",
        type=Path,
        help="Path to a standalone SOP text file (alternative to experiment name).",
    )
    parser.add_argument(
        "--sop-method",
        choices=list(SOP_METHODS.keys()),
        default="wd_kf_act",
        help="Which SOP method to use (default: wd_kf_act).",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=50,
        help="Maximum number of agent steps (default: 50).",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=2.0,
        help="Seconds to wait between steps (default: 2.0).",
    )
    parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Skip confirmation prompt.",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging.",
    )
    parser.add_argument(
        "--detector",
        choices=["yolo", "dino"],
        default="yolo",
        help="UI element detector to use (default: yolo).",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Run validation after execution to check if the task was completed.",
    )

    args = parser.parse_args()

    # Setup logging
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    # Resolve SOP text and experiment context
    sop_text = ""
    intent = ""
    experiment_name = "standalone"

    if args.sop_file:
        # Standalone SOP file
        if not args.sop_file.exists():
            print(f"Error: SOP file not found: {args.sop_file}", file=sys.stderr)
            sys.exit(1)
        sop_text = args.sop_file.read_text(encoding="utf-8").strip()
        experiment_name = args.sop_file.stem

    elif args.name:
        # Find experiment folder
        folder = _find_experiment_folder(args.name)
        if folder is None:
            print(f"Error: No experiment folder found for '{args.name}' in {OUTPUTS_DIR}", file=sys.stderr)
            sys.exit(1)

        print(f"Using experiment: {folder.name}")
        experiment_name = args.name

        sop_text = _load_sop_text(folder, args.sop_method)
        intent = _load_intent(folder)

    else:
        parser.error("Provide either an experiment name or --sop-file")

    if not sop_text:
        print("Error: SOP text is empty.", file=sys.stderr)
        sys.exit(1)

    # Create output directory
    timestamp = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
    output_dir = OUTPUTS_DIR / f"executions/exec_{experiment_name}_{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"Output directory: {output_dir}")

    # Run the agent
    log = run_agent(
        sop_text=sop_text,
        output_dir=output_dir,
        intent=intent,
        max_steps=args.max_steps,
        delay=args.delay,
        auto_confirm=args.yes,
        detector=args.detector,
    )

    # Optional validation
    if args.validate:
        import json
        from validate.validator import validate_execution

        log_path = output_dir / "execution_log.json"
        with open(log_path) as f:
            execution_log_data = json.load(f)

        print("\nRunning post-execution validation...")
        validation_result = validate_execution(execution_log_data, output_dir)

        val_path = output_dir / "validation_result.json"
        with open(val_path, "w") as f:
            json.dump(validation_result, f, indent=2, ensure_ascii=False)

        was_completed = validation_result.get("was_completed", False)
        print(f"\nValidation: {'PASSED' if was_completed else 'FAILED'}")
        print(f"Reasoning: {validation_result.get('thinking', '')}")
        print(f"Saved: {val_path}")

        sys.exit(0 if was_completed else 1)

    sys.exit(0 if log.completed_successfully else 1)


if __name__ == "__main__":
    main()
