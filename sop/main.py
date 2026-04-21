import argparse
import logging
import sys
from pathlib import Path

from config import RESULTS_DIR, BASE_DIR
from sop.data_loader import load_all_experiments, load_experiment, Experiment
from sop.methods import METHODS, VISION_METHODS
from sop.api_client import call_openai

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

OUTPUTS_DIR = BASE_DIR / "outputs"


def _find_folder(name: str) -> Path:
    """Find an experiment folder by prefix in both experiments/ and outputs/.

    Allows short names like 'kaust-ex8' to match 'kaust-ex8 @ 2026-03-04-10-03-27'.
    """
    for parent in [OUTPUTS_DIR, BASE_DIR / "experiments"]:
        if not parent.is_dir():
            continue
        # Exact match
        exact = parent / name
        if exact.is_dir():
            return exact
        # Prefix match
        matches = [d for d in parent.iterdir() if d.is_dir() and d.name.startswith(name)]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            names = [m.name for m in matches]
            logger.error(f"Ambiguous name '{name}', matches: {names}")
            sys.exit(1)
    return None


def result_path(exp: Experiment, method_name: str, output_dir: Path = None) -> Path:
    if output_dir:
        return output_dir / f"method_{method_name}.txt"
    return RESULTS_DIR / exp.folder / f"method_{method_name}.txt"


def run_method(exp: Experiment, method_name: str, output_dir: Path = None, dry_run: bool = False) -> None:
    """Run a single method on a single experiment."""
    build_fn = METHODS[method_name]
    out_path = result_path(exp, method_name, output_dir)

    if dry_run:
        messages = build_fn(exp)
        img_count = 0
        for msg in messages:
            if isinstance(msg.get("content"), list):
                img_count = sum(1 for c in msg["content"] if c.get("type") == "image_url")
        logger.info(f"  [DRY RUN] {method_name}: {len(messages)} message(s), {img_count} image(s)")
        return

    logger.info(f"  Calling API for method={method_name}...")
    messages = build_fn(exp)
    result = call_openai(messages)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(result, encoding="utf-8")
    logger.info(f"  Saved: {out_path}")


def main():
    parser = argparse.ArgumentParser(description="Generate SOPs from workflow traces using GPT-4o")
    parser.add_argument(
        "name",
        nargs="?",
        default=None,
        help="Experiment name or prefix (e.g. 'kaust-ex8'). Searches outputs/ and experiments/.",
    )
    parser.add_argument(
        "--methods",
        nargs="+",
        choices=list(METHODS.keys()),
        default=["wd_kf_act"],
        help="Methods to run (default: wd_kf_act)",
    )
    parser.add_argument(
        "--experiments",
        nargs="+",
        default=None,
        help="Experiment folder names to process (default: all). Used with --source.",
    )
    parser.add_argument(
        "--source",
        default=None,
        help="Source directory containing experiments (default: experiments).",
    )
    parser.add_argument(
        "--intent",
        default="",
        help="Task intent/description (prompted interactively if not set)",
    )
    parser.add_argument(
        "--ui-name",
        default="",
        help="UI/application name (default: auto-detect from URL)",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="Save results inside the experiment folder instead of results/",
    )
    parser.add_argument(
        "--no-skip",
        action="store_true",
        help="Re-run even if result file already exists",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build messages but don't call the API",
    )
    args = parser.parse_args()

    # ── Simple mode: python main.py kaust-ex8 ──
    if args.name:
        folder = _find_folder(args.name)
        if not folder:
            logger.error(f"Cannot find experiment '{args.name}' in outputs/ or experiments/")
            sys.exit(1)

        intent = args.intent
        ui_name = args.ui_name

        # Prompt interactively if intent not provided and no webarena metadata
        import json
        json_file = folder / f"{folder.name}.json"
        with open(json_file) as f:
            data = json.load(f)

        has_webarena = "webarena" in data

        if not intent and not has_webarena:
            intent = input("What is the task? (intent): ").strip()
        if not ui_name and not has_webarena:
            # Auto-detect from trace data
            detected_name = ""
            for entry in data.get("trace", []):
                if entry.get("type") == "state":
                    d = entry.get("data", entry)
                    # Prefer active_application_name (system recorder)
                    app_name = d.get("active_application_name", "")
                    if app_name:
                        detected_name = app_name
                        break
                    # Fall back to URL parsing (browser recorder)
                    first_url = d.get("url", "")
                    if first_url and first_url.startswith("http"):
                        from urllib.parse import urlparse
                        host = urlparse(first_url).hostname or ""
                        if host.startswith("www."):
                            host = host[4:]
                        if host:
                            detected_name = host
                            break
            if detected_name:
                ui_name = detected_name
                logger.info(f"  Auto-detected UI: {ui_name}")
            else:
                ui_name = input("Application name (ui_name): ").strip() or "Application"

        exp = load_experiment(folder, intent=intent, ui_name=ui_name)
        experiments = [exp]
        # Save results inside the experiment folder
        output_dir = folder

        # Save prompt (intent) to file
        prompt_path = folder / "prompt.txt"
        prompt_path.write_text(intent, encoding="utf-8")
        logger.info(f"  Saved prompt: {prompt_path}")
        logger.info(f"Experiment: {exp.folder}")
        logger.info(f"  Intent: {exp.intent}")
        logger.info(f"  UI: {exp.ui_name}")
        logger.info(f"  States: {len(exp.states)}, Actions: {len(exp.actions)}, Screenshots: {exp.has_screenshots}")
        logger.info(f"  Output: {output_dir}")

        total = 0
        for method_name in args.methods:
            if method_name in VISION_METHODS and not exp.has_screenshots:
                logger.info(f"  Skipping {method_name} (no screenshots)")
                continue
            out_path = result_path(exp, method_name, output_dir)
            if out_path.exists() and not args.no_skip:
                logger.info(f"  Skipping {method_name} (already exists)")
                continue
            run_method(exp, method_name, output_dir, dry_run=args.dry_run)
            total += 1

        logger.info(f"Done. Processed {total} method(s).")
        return

    # ── Batch mode: python main.py --source outputs --experiments ... ──
    if args.source:
        source_dir = Path(args.source)
        if not source_dir.is_absolute():
            source_dir = BASE_DIR / source_dir
    else:
        source_dir = None

    if source_dir:
        experiments = []
        for entry in sorted(source_dir.iterdir()):
            if not entry.is_dir():
                continue
            json_file = entry / f"{entry.name}.json"
            if not json_file.exists():
                continue
            if entry.name.startswith("["):
                continue
            experiments.append(load_experiment(entry, intent=args.intent, ui_name=args.ui_name))
    else:
        experiments = load_all_experiments()

    logger.info(f"Loaded {len(experiments)} experiments")

    if args.experiments:
        exp_set = set(args.experiments)
        experiments = [e for e in experiments if e.folder in exp_set]
        if not experiments:
            logger.error(f"No matching experiments found for: {args.experiments}")
            sys.exit(1)
        logger.info(f"Filtered to {len(experiments)} experiment(s)")

    total = 0
    skipped = 0

    for i, exp in enumerate(experiments, 1):
        intent_preview = exp.intent[:60] if exp.intent else "(no intent)"
        logger.info(f"[{i}/{len(experiments)}] {exp.folder} (intent: {intent_preview}...)")

        output_dir = None
        if args.in_place and source_dir:
            output_dir = source_dir / exp.folder

        for method_name in args.methods:
            if method_name in VISION_METHODS and not exp.has_screenshots:
                logger.info(f"  Skipping {method_name} (no screenshots)")
                skipped += 1
                continue

            out_path = result_path(exp, method_name, output_dir)
            if out_path.exists() and not args.no_skip:
                logger.info(f"  Skipping {method_name} (already exists)")
                skipped += 1
                continue

            run_method(exp, method_name, output_dir, dry_run=args.dry_run)
            total += 1

    logger.info(f"Done. Processed: {total}, Skipped: {skipped}")


if __name__ == "__main__":
    main()
