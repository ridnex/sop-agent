"""Ensure a detached Chrome is running with CDP enabled on port 9222.

This replaces the `launch` path in web.execute.browser, which had Chrome as a
child of the worker process — so Chrome died when the worker exited. Here we
spawn Chrome once, detached (own session), and connect to it via CDP. Chrome
survives every execution and stays open after the run finishes.
"""

import socket
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

from web.execute.config import BROWSER_PROFILE_DIR, CDP_URL

CHROME_APP = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def _cdp_port() -> int:
    parsed = urlparse(CDP_URL)
    return parsed.port or 9222


def _port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    s = socket.socket()
    try:
        s.settimeout(timeout)
        s.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def ensure_chrome_running(wait_timeout: float = 20.0, profile_dir: Path | None = None) -> bool:
    """Return True if Chrome had to be launched; False if it was already running.

    Raises RuntimeError if Chrome fails to come up within wait_timeout.
    """
    port = _cdp_port()
    if _port_open("127.0.0.1", port):
        return False

    profile = profile_dir or BROWSER_PROFILE_DIR
    profile.mkdir(parents=True, exist_ok=True)

    if not Path(CHROME_APP).exists():
        raise RuntimeError(
            f"Chrome binary not found at {CHROME_APP}. Adjust CHROME_APP in "
            f"rl_data/browser_keepalive.py if you installed Chrome elsewhere."
        )

    subprocess.Popen(
        [
            CHROME_APP,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}",
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,  # detaches from our process group
    )

    deadline = time.time() + wait_timeout
    while time.time() < deadline:
        if _port_open("127.0.0.1", port):
            return True
        time.sleep(0.5)

    raise RuntimeError(
        f"Chrome did not open CDP port {port} within {wait_timeout}s. "
        f"Check that {CHROME_APP} is installed and that nothing else is using port {port}."
    )
