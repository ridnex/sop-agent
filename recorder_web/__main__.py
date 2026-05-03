"""CLI: ``python -m recorder_web import <bundle>``.

Bundle is either:
  - a folder produced by the Chrome extension (containing ``recording.webm``,
    ``events.json``, ``manifest.json``); or
  - a ``.zip`` of that folder.
"""

import argparse
import sys

from recorder_web.adapter import convert


def main() -> int:
    p = argparse.ArgumentParser(
        prog="python -m recorder_web",
        description="Import a web-recorder bundle into the SOP pipeline.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    imp = sub.add_parser("import", help="Convert a bundle into outputs/<name> @ <ts>/")
    imp.add_argument("bundle", help="Path to the bundle folder or .zip")
    imp.add_argument(
        "--output", "-o", default="outputs",
        help="Base output dir (default: ./outputs)",
    )
    imp.add_argument(
        "--keep-temp", action="store_true",
        help="Don't delete the temp extraction dir if the bundle was a zip",
    )

    args = p.parse_args()

    if args.cmd == "import":
        try:
            convert(args.bundle, output_base=args.output, keep_temp=args.keep_temp)
        except Exception as e:
            print(f"[recorder_web] ERROR: {e}", file=sys.stderr)
            return 1
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
