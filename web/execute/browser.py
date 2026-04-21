"""Browser controller using Playwright + CDP.

Two modes:
  - connect (default): Connects to your existing Chrome via CDP on localhost:9222.
    Opens a new tab in your browser. Has access to all your sessions/cookies.
  - launch: Launches a separate Chrome with a persistent profile directory.

All input actions go through CDP (via Playwright's APIs which wrap CDP internally).
No OS-level simulation — actions target the browser directly.
"""

import base64
import logging
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page

from web.execute.config import (
    BROWSER_PROFILE_DIR,
    BROWSER_WIDTH,
    BROWSER_HEIGHT,
    CDP_URL,
)

logger = logging.getLogger(__name__)

# Map Claude Computer Use key names (X11-style) to Playwright key names
_KEY_MAP = {
    "Return": "Enter",
    "space": " ",
    "BackSpace": "Backspace",
    "Page_Up": "PageUp",
    "Page_Down": "PageDown",
    "Up": "ArrowUp",
    "Down": "ArrowDown",
    "Left": "ArrowLeft",
    "Right": "ArrowRight",
    "super": "Meta",
    "ctrl": "Control",
    "alt": "Alt",
    "shift": "Shift",
}


def _translate_key(key_str: str) -> str:
    """Translate Claude Computer Use key names to Playwright key names.

    Handles single keys ("Return" -> "Enter") and combos
    ("ctrl+a" -> "Control+a", "super+l" -> "Meta+l").
    """
    parts = key_str.split("+")
    translated = []
    for part in parts:
        mapped = _KEY_MAP.get(part, part)
        translated.append(mapped)
    return "+".join(translated)


class BrowserController:
    """Manages Chrome via Playwright — connects to existing or launches new."""

    def __init__(self, headless: bool = False, launch: bool = False):
        """
        Args:
            headless: Only used in launch mode. Ignored in connect mode.
            launch: If True, launch a new browser. If False (default),
                    connect to existing Chrome on CDP_URL.
        """
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._headless = headless
        self._launch = launch
        self._owns_browser = False  # True if we launched it, False if we connected
        self._nav_mode = False  # Set by Cmd+L/Ctrl+L, next type() becomes goto()
        self._page_count = 0  # Track page count to detect new tabs

    def start(self, start_url: str = "about:blank") -> None:
        """Start browser — connect to existing Chrome or launch new one."""
        self._playwright = sync_playwright().start()

        if self._launch:
            self._start_launch(start_url)
        else:
            self._start_connect(start_url)

    def _start_connect(self, start_url: str) -> None:
        """Connect to existing Chrome via CDP and open a new tab.

        Robust against the common failure modes:
          - CDP endpoint reachable but Chrome wedged  -> clearly fail
          - Multiple contexts, first one stale        -> try the others
          - new_page() hangs indefinitely             -> 10s timeout
        """
        try:
            self._browser = self._playwright.chromium.connect_over_cdp(
                CDP_URL, timeout=10000
            )
        except Exception as e:
            self._playwright.stop()
            raise RuntimeError(
                f"Could not connect to Chrome at {CDP_URL}.\n"
                f"If a stale Chrome is the problem, kill it and retry:\n\n"
                f"  pkill -f 'remote-debugging-port=9222'\n"
                f"  python -m rl_data.cli run --sop <id>\n\n"
                f"Original error: {e}"
            ) from e

        self._owns_browser = False

        contexts = list(self._browser.contexts)
        if not contexts:
            self._playwright.stop()
            raise RuntimeError(
                f"Connected to Chrome at {CDP_URL} but it has no browser contexts. "
                f"Chrome is in a broken state — kill it and retry:\n"
                f"  pkill -f 'remote-debugging-port=9222'"
            )

        # Try to open a new tab. If the first context refuses, walk through the
        # remaining contexts before giving up — a previous wedged run can leave
        # contexts[0] unusable while later ones are fine.
        last_error: Exception | None = None
        for ctx in contexts:
            try:
                page = ctx.new_page()
                self._context = ctx
                self._page = page
                break
            except Exception as e:
                last_error = e
                logger.warning(f"new_page failed on context {ctx}: {e}. Trying next context...")
        else:
            self._playwright.stop()
            raise RuntimeError(
                f"All {len(contexts)} Chrome contexts refused to open a new tab. "
                f"The attached Chrome is wedged. Kill and retry:\n"
                f"  pkill -f 'remote-debugging-port=9222'\n"
                f"Last error: {last_error}"
            )

        self._safe_initial_goto(start_url)

        self._page_count = len(self._context.pages)
        self._context.on("page", self._on_new_page)
        logger.info(
            f"Connected to existing Chrome at {CDP_URL}, opened new tab "
            f"({len(self._context.pages)} total tabs in context)"
        )

    def _start_launch(self, start_url: str) -> None:
        """Launch a new browser with persistent profile."""
        BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)

        self._context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(BROWSER_PROFILE_DIR),
            headless=self._headless,
            viewport={"width": BROWSER_WIDTH, "height": BROWSER_HEIGHT},
            device_scale_factor=1,
            args=["--disable-blink-features=AutomationControlled"],
        )
        self._owns_browser = True

        # Close any restored pages from previous sessions
        for old_page in self._context.pages:
            old_page.close()

        # Always start with a fresh page
        self._page = self._context.new_page()

        self._safe_initial_goto(start_url)

        self._page_count = len(self._context.pages)
        self._context.on("page", self._on_new_page)
        logger.info(
            f"Launched new browser: {BROWSER_WIDTH}x{BROWSER_HEIGHT} "
            f"(headless={self._headless}, profile={BROWSER_PROFILE_DIR})"
        )

    def stop(self) -> None:
        """Disconnect from browser. Browser window stays open."""
        if self._owns_browser:
            # Launch mode: detach Playwright without killing the browser.
            # Setting refs to None lets the Chromium process survive as orphan.
            self._page = None
            self._context = None
            # Don't call playwright.stop() — it kills the child process
            logger.info("Detached from launched browser (window stays open)")
        else:
            # Connect mode: just disconnect CDP, browser stays alive
            if self._browser:
                self._browser.close()
            if self._playwright:
                self._playwright.stop()
            logger.info("Disconnected from Chrome (window stays open)")

    def _safe_initial_goto(self, start_url: str) -> None:
        """Best-effort initial navigation.

        The previous implementation waited up to 30s for `domcontentloaded` and
        crashed the whole worker when heavy pages (Gmail redirects, SSO prompts)
        did not fire that event in time. The SOP's step 1 already contains the
        navigation instruction and Claude handles it via ctrl+l, so if this
        fails we warn and move on instead of aborting.
        """
        if not start_url or start_url == "about:blank":
            return
        try:
            self._page.goto(start_url, wait_until="commit", timeout=10000)
        except Exception as e:
            logger.warning(
                f"Initial goto({start_url}) failed: {e}. "
                f"Continuing — the SOP's first step should handle navigation."
            )

    def _on_new_page(self, page: Page) -> None:
        """Playwright event: a new tab or popup opened. Switch immediately."""
        try:
            page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            pass
        self._page = page
        try:
            page.bring_to_front()
        except Exception:
            pass
        if self._context is not None:
            self._page_count = len(self._context.pages)
        logger.info(f"[event] Switched to new tab: {page.url}")

    def _check_for_new_tab(self) -> None:
        """Sync fallback: pick up a new tab or recover from a closed one.

        The `page` event handler catches most cases, but this runs before every
        screenshot and after every click as a safety net.
        """
        if self._context is None:
            return
        pages = self._context.pages

        # Current page was closed -> fall back to newest open tab
        if self._page is not None and self._page.is_closed():
            open_pages = [p for p in pages if not p.is_closed()]
            if open_pages:
                self._page = open_pages[-1]
                try:
                    self._page.bring_to_front()
                except Exception:
                    pass
                logger.info(f"Current tab was closed, switched to: {self._page.url}")

        # New tab count -> switch to newest
        if len(pages) > self._page_count:
            new_page = pages[-1]
            if new_page != self._page and not new_page.is_closed():
                try:
                    new_page.wait_for_load_state("domcontentloaded", timeout=5000)
                except Exception:
                    pass
                self._page = new_page
                try:
                    new_page.bring_to_front()
                except Exception:
                    pass
                logger.info(f"Switched to new tab: {self._page.url}")

        self._page_count = len(pages)

    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError("Browser not started")
        return self._page

    @property
    def current_url(self) -> str:
        return self.page.url

    # --- Actions matching Claude Computer Use tool ---

    def _capture_png(self) -> bytes:
        """Take a screenshot with a short timeout, falling back to a no-wait capture.

        A page that is still loading heavy content can make the default 30s
        screenshot timeout hit. We try a fast capture first; if that stalls we
        retry with a minimal-wait path that snapshots whatever is rendered.
        """
        try:
            return self.page.screenshot(type="png", timeout=10000)
        except Exception as e:
            logger.warning(f"Screenshot stalled ({e}); retrying with animations disabled.")
            return self.page.screenshot(type="png", timeout=10000, animations="disabled", caret="hide")

    def screenshot(self) -> str:
        """Capture screenshot, return base64 PNG string."""
        self._check_for_new_tab()
        png_bytes = self._capture_png()
        return base64.standard_b64encode(png_bytes).decode("utf-8")

    def screenshot_to_file(self, path: str) -> str:
        """Capture screenshot, save to file, return base64 PNG string."""
        self._check_for_new_tab()
        png_bytes = self._capture_png()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            f.write(png_bytes)
        return base64.standard_b64encode(png_bytes).decode("utf-8")

    def left_click(self, x: int, y: int) -> None:
        self.page.mouse.click(x, y)

    def right_click(self, x: int, y: int) -> None:
        self.page.mouse.click(x, y, button="right")

    def middle_click(self, x: int, y: int) -> None:
        self.page.mouse.click(x, y, button="middle")

    def double_click(self, x: int, y: int) -> None:
        self.page.mouse.dblclick(x, y)

    def triple_click(self, x: int, y: int) -> None:
        self.page.mouse.click(x, y, click_count=3)

    def mouse_move(self, x: int, y: int) -> None:
        self.page.mouse.move(x, y)

    def left_click_drag(self, start_x: int, start_y: int, end_x: int, end_y: int) -> None:
        self.page.mouse.move(start_x, start_y)
        self.page.mouse.down()
        self.page.mouse.move(end_x, end_y, steps=10)
        self.page.mouse.up()

    def type_text(self, text: str) -> None:
        """Insert text. If nav_mode is set (after Cmd+L), navigates instead."""
        if self._nav_mode:
            self._nav_mode = False
            url = text.strip()
            if not url.startswith(("http://", "https://")):
                url = "https://" + url
            logger.info(f"Nav mode: navigating to {url}")
            # Use `commit` (URL change) with a short timeout rather than
            # `domcontentloaded` (30s) — heavy pages like Gmail/SSO redirect
            # chains rarely fire domcontentloaded cleanly and a stalled initial
            # navigation poisons every subsequent screenshot.
            try:
                self.page.goto(url, wait_until="commit", timeout=10000)
            except Exception as e:
                logger.warning(f"Nav-mode goto({url}) did not commit cleanly: {e}")
            return
        self.page.keyboard.insert_text(text)

    def key_press(self, key_combo: str) -> None:
        """Press a key or key combination.

        Intercepts Cmd+L / Ctrl+L (address bar focus) since CDP can't access
        browser chrome. Sets nav_mode so the next type() becomes page.goto().

        Args:
            key_combo: Claude Computer Use key name, e.g. "Return", "ctrl+a", "super+l"
        """
        translated = _translate_key(key_combo)
        # Intercept address bar shortcuts — CDP can't focus the real address bar
        if translated in ("Control+l", "Meta+l"):
            self._nav_mode = True
            logger.info("Nav mode activated (Cmd+L / Ctrl+L intercepted)")
            return
        # If Enter pressed right after nav_mode type, just ignore (page already navigated)
        if translated == "Enter" and self._nav_mode:
            self._nav_mode = False
            return
        self.page.keyboard.press(translated)

    def scroll(self, x: int, y: int, direction: str, amount: int) -> None:
        """Scroll at position.

        Args:
            x, y: Position to scroll at.
            direction: "up", "down", "left", "right"
            amount: Number of scroll clicks.
        """
        self.page.mouse.move(x, y)
        pixels_per_click = 100
        if direction == "up":
            self.page.mouse.wheel(0, -amount * pixels_per_click)
        elif direction == "down":
            self.page.mouse.wheel(0, amount * pixels_per_click)
        elif direction == "left":
            self.page.mouse.wheel(-amount * pixels_per_click, 0)
        elif direction == "right":
            self.page.mouse.wheel(amount * pixels_per_click, 0)

    def navigate(self, url: str) -> None:
        """Navigate to URL."""
        self.page.goto(url, wait_until="domcontentloaded")

    def execute_action(self, action_input: dict) -> dict:
        """Execute a Claude Computer Use action and return a tool result.

        Args:
            action_input: The `input` dict from a Computer Use tool_use block.
                         Contains "action" key and action-specific params.

        Returns:
            Dict with "output" (str|None), "error" (str|None),
            "base64_image" (str|None).
        """
        action = action_input.get("action", "")

        try:
            if action == "screenshot":
                return {"output": None, "error": None, "base64_image": self.screenshot()}

            elif action == "left_click":
                coord = action_input.get("coordinate")
                if not coord:
                    return {"output": None, "error": "left_click requires coordinate", "base64_image": None}
                self.left_click(coord[0], coord[1])

            elif action == "right_click":
                coord = action_input.get("coordinate")
                if not coord:
                    return {"output": None, "error": "right_click requires coordinate", "base64_image": None}
                self.right_click(coord[0], coord[1])

            elif action == "middle_click":
                coord = action_input.get("coordinate")
                if not coord:
                    return {"output": None, "error": "middle_click requires coordinate", "base64_image": None}
                self.middle_click(coord[0], coord[1])

            elif action == "double_click":
                coord = action_input.get("coordinate")
                if not coord:
                    return {"output": None, "error": "double_click requires coordinate", "base64_image": None}
                self.double_click(coord[0], coord[1])

            elif action == "triple_click":
                coord = action_input.get("coordinate")
                if not coord:
                    return {"output": None, "error": "triple_click requires coordinate", "base64_image": None}
                self.triple_click(coord[0], coord[1])

            elif action == "mouse_move":
                coord = action_input.get("coordinate")
                if not coord:
                    return {"output": None, "error": "mouse_move requires coordinate", "base64_image": None}
                self.mouse_move(coord[0], coord[1])

            elif action == "left_click_drag":
                coord = action_input.get("coordinate")
                start = action_input.get("start_coordinate")
                if not coord or not start:
                    return {"output": None, "error": "left_click_drag requires coordinate and start_coordinate", "base64_image": None}
                self.left_click_drag(start[0], start[1], coord[0], coord[1])

            elif action == "type":
                text = action_input.get("text", "")
                self.type_text(text)

            elif action == "key":
                key = action_input.get("text", "")
                if not key:
                    return {"output": None, "error": "key action requires text", "base64_image": None}
                self.key_press(key)

            elif action == "scroll":
                coord = action_input.get("coordinate", [BROWSER_WIDTH // 2, BROWSER_HEIGHT // 2])
                direction = action_input.get("scroll_direction", "down")
                amount = action_input.get("scroll_amount", 3)
                self.scroll(coord[0], coord[1], direction, amount)

            elif action == "wait":
                duration = action_input.get("duration", 1)
                # Cap at 60s so wait(20) for manual-login flows sleeps the full 20.
                time.sleep(min(duration, 60))
                return {"output": None, "error": None, "base64_image": self.screenshot()}

            elif action == "cursor_position":
                return {
                    "output": f"Cursor position not trackable via CDP. Viewport: {BROWSER_WIDTH}x{BROWSER_HEIGHT}",
                    "error": None,
                    "base64_image": None,
                }

            else:
                return {"output": None, "error": f"Unknown action: {action}", "base64_image": None}

            # Check if a click/key opened a new tab — switch to it.
            # 1.0s gives slow popups time to register before we screenshot.
            if action in ("left_click", "right_click", "middle_click", "double_click",
                          "triple_click", "key"):
                time.sleep(1.0)
                self._check_for_new_tab()

            # For non-screenshot actions, return screenshot so Claude sees the result
            return {"output": None, "error": None, "base64_image": self.screenshot()}

        except Exception as e:
            logger.error(f"Action {action} failed: {e}")
            return {"output": None, "error": str(e), "base64_image": None}
