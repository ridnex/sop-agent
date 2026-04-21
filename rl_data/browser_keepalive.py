"""Ensure a detached Chrome is running with CDP enabled on port 9222.

This replaces the `launch` path in web.execute.browser, which had Chrome as a
child of the worker process — so Chrome died when the worker exited. Here we
spawn Chrome once, detached (own session), and connect to it via CDP. Chrome
survives every execution and stays open after the run finishes.

When a previous run left Chrome in a wedged state (port open but CDP endpoint
unresponsive, or context refuses new tabs), `ensure_chrome_running` can be
asked to force-restart it instead of trying to reuse the broken instance.
"""

import json
import logging
import socket
import subprocess
import time
import urllib.request
import urllib.error
from pathlib import Path
from urllib.parse import urlparse

from web.execute.config import BROWSER_PROFILE_DIR, CDP_URL

logger = logging.getLogger(__name__)

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


def _cdp_healthy(port: int, timeout: float = 2.0) -> bool:
    """Check that the CDP endpoint actually responds, not just that the port is open.

    A Chrome that crashed or got stuck on an auth/update prompt sometimes leaves
    the port listening but won't answer /json/version.
    """
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/json/version", timeout=timeout
        ) as resp:
            payload = json.loads(resp.read().decode())
        return "Browser" in payload
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError, OSError):
        return False


def _kill_detached_chrome(port: int) -> None:
    """Best-effort shutdown of any detached Chrome still listening on this port.

    Uses pkill on the specific --remote-debugging-port argument so we do NOT
    touch the user's normal Chrome windows.
    """
    try:
        subprocess.run(
            ["pkill", "-f", f"remote-debugging-port={port}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        # Give the OS a moment to release the socket
        time.sleep(1.0)
    except Exception as e:
        logger.warning(f"Could not kill stale Chrome: {e}")


def _spawn_chrome(port: int, profile: Path) -> None:
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


def ensure_chrome_running(
    wait_timeout: float = 20.0,
    profile_dir: Path | None = None,
    force_restart: bool = False,
) -> bool:
    """Guarantee a healthy Chrome is reachable on the CDP port.

    Returns True if Chrome had to be launched, False if an already-running
    Chrome was reused. Raises RuntimeError if Chrome never comes up.

    If `force_restart` is True, any existing detached Chrome on the port is
    killed first. Use this when the previous run left Chrome wedged.

    Auto-recovery: if the port is open but /json/version does not respond,
    we treat the instance as wedged and restart it, even without
    force_restart.
    """
    port = _cdp_port()
    port_was_open = _port_open("127.0.0.1", port)

    if port_was_open and not force_restart:
        if _cdp_healthy(port):
            return False
        logger.warning(
            f"Port {port} is open but CDP endpoint is unresponsive; "
            f"killing and restarting Chrome."
        )
        _kill_detached_chrome(port)
    elif port_was_open and force_restart:
        logger.info("force_restart requested; killing existing Chrome on port %d.", port)
        _kill_detached_chrome(port)

    profile = profile_dir or BROWSER_PROFILE_DIR
    profile.mkdir(parents=True, exist_ok=True)

    if not Path(CHROME_APP).exists():
        raise RuntimeError(
            f"Chrome binary not found at {CHROME_APP}. Adjust CHROME_APP in "
            f"rl_data/browser_keepalive.py if you installed Chrome elsewhere."
        )

    _spawn_chrome(port, profile)

    deadline = time.time() + wait_timeout
    while time.time() < deadline:
        if _port_open("127.0.0.1", port) and _cdp_healthy(port):
            return True
        time.sleep(0.5)

    raise RuntimeError(
        f"Chrome did not open a healthy CDP endpoint on port {port} within "
        f"{wait_timeout}s. Check that {CHROME_APP} is installed and that "
        f"nothing else is using port {port}."
    )
