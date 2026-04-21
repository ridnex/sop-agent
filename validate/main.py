"""CLI entry point for post-execution validation.

Usage:
    # Validate a specific execution:
    python -m validate.main /path/to/execution_dir

    # Or by execution name prefix:
    python -m validate.main exec_test_video4_2026-03-11-10-32-25
"""

import argparse
import json
import logging
import sys
from pathlib import Path

from config import BASE_DIR
from validate.validator import validate_execution

EXECUTIONS_DIR = BASE_DIR / "outputs" / "executions"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def _find_execution_dir(name: str) -> Path | None:
    """Find an execution directory by name or prefix."""
    # Try as absolute/relative path first
    p = Path(name)
    if p.is_dir() and (p / "execution_log.json").exists():
        return p

    # Search in executions/
    if not EXECUTIONS_DIR.exists():
        return None
    for entry in sorted(EXECUTIONS_DIR.iterdir()):
        if entry.is_dir() and entry.name.startswith(name):
            if (entry / "execution_log.json").exists():
                return entry
    return None


def main():
    parser = argparse.ArgumentParser(description="Validate SOP execution completion")
    parser.add_argument("execution", help="Execution directory path or name prefix")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Save validation result to this file (default: <exec_dir>/validation_result.json)",
    )
    args = parser.parse_args()

    exec_dir = _find_execution_dir(args.execution)
    if not exec_dir:
        print(f"Error: Cannot find execution '{args.execution}'", file=sys.stderr)
        sys.exit(1)

    log_path = exec_dir / "execution_log.json"
    with open(log_path) as f:
        execution_log = json.load(f)

    logger.info(f"Validating execution: {exec_dir.name}")
    logger.info(f"  Intent: {execution_log.get('intent', '(none)')}")
    logger.info(f"  Steps: {len(execution_log.get('steps', []))}")
    logger.info(f"  Agent said completed: {execution_log.get('completed_successfully', False)}")

    result = validate_execution(execution_log, exec_dir)

    print(f"\n{'=' * 60}")
    print("VALIDATION RESULT")
    print(f"{'=' * 60}")
    print(f"Completed: {result.get('was_completed', False)}")
    print(f"Reasoning: {result.get('thinking', '(none)')}")
    print(f"{'=' * 60}")

    # Save result
    output_path = args.output or (exec_dir / "validation_result.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    logger.info(f"Saved validation result: {output_path}")

    sys.exit(0 if result.get("was_completed") else 1)


if __name__ == "__main__":
    main()
