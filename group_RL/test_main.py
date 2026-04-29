"""Smoke test for group_RL.main CLI.

Run with:  python -m group_RL.test_main

Verifies argparse → run_one wiring without hitting browser/API. Mocks
run_one and confirms every flag maps to the right keyword argument.
"""

import sys
from pathlib import Path
from unittest.mock import patch

import group_RL.main as cli


def _check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"FAIL: {name} -- {detail}")
    print(f"PASS: {name}")


def main() -> int:
    checks = 0
    captured: dict = {}

    def _fake_good(**kwargs):
        captured.clear()
        captured.update(kwargs)
        return {
            "final_label": "good",
            "strategy": "fresh",
            "retrieval_score": 0.0,
            "memory_writeback": True,
            "bad_memory_writeback": False,
        }

    def _fake_bad(**kwargs):
        captured.clear()
        captured.update(kwargs)
        return {
            "final_label": "bad",
            "strategy": "fresh",
            "retrieval_score": 0.0,
            "memory_writeback": False,
            "bad_memory_writeback": True,
        }

    # 1. --help exits 0
    try:
        cli.main(["--help"])
    except SystemExit as e:
        _check("--help exits with code 0", e.code == 0)
        checks += 1

    # 2. missing required --intent fails
    try:
        cli.main([])
    except SystemExit as e:
        _check("missing --intent exits non-zero", e.code != 0)
        checks += 1

    # 3. defaults wire through correctly
    with patch("group_RL.main.run_one", side_effect=_fake_good):
        rc = cli.main(["--intent", "test"])
    _check("returns 0 on good outcome", rc == 0)
    checks += 1
    _check("intent passed through", captured.get("intent") == "test")
    checks += 1
    _check("start_url default", captured.get("start_url") == "about:blank")
    checks += 1
    _check("n_group default", captured.get("n_group") == 3)
    checks += 1
    _check("max_steps default", captured.get("max_steps") == 50)
    checks += 1
    _check("delay default", captured.get("delay") == 2.0)
    checks += 1
    _check("launch default False", captured.get("launch") is False)
    checks += 1
    _check("headless default False", captured.get("headless") is False)
    checks += 1
    _check("output_root default None", captured.get("output_root") is None)
    checks += 1

    # 4. all flags pass through
    with patch("group_RL.main.run_one", side_effect=_fake_good):
        rc = cli.main([
            "--intent", "do something specific",
            "--start-url", "https://example.com",
            "--n-group", "5",
            "--max-steps", "30",
            "--delay", "1.5",
            "--launch",
            "--headless",
            "--output-root", "/tmp/group_RL_test",
        ])
    _check("custom intent", captured["intent"] == "do something specific")
    checks += 1
    _check("custom start_url", captured["start_url"] == "https://example.com")
    checks += 1
    _check("custom n_group=5", captured["n_group"] == 5)
    checks += 1
    _check("custom max_steps=30", captured["max_steps"] == 30)
    checks += 1
    _check("custom delay=1.5", captured["delay"] == 1.5)
    checks += 1
    _check("launch True", captured["launch"] is True)
    checks += 1
    _check("headless True", captured["headless"] is True)
    checks += 1
    _check(
        "output_root parsed as Path",
        captured["output_root"] == Path("/tmp/group_RL_test"),
    )
    checks += 1

    # 5. exit code 1 on bad outcome
    with patch("group_RL.main.run_one", side_effect=_fake_bad):
        rc = cli.main(["--intent", "fail"])
    _check("returns 1 on bad outcome", rc == 1)
    checks += 1

    print(f"\nAll {checks} checks passed.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
