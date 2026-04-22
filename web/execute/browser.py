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
        """Connect to existing Chrome via CDP and open a new tab."""
        try:
            self._browser = self._playwright.chromium.connect_over_cdp(CDP_URL)
        except Exception as e:
            self._playwright.stop()
            raise RuntimeError(
                f"Could not connect to Chrome at {CDP_URL}.\n"
                f"Restart Chrome with remote debugging:\n\n"
                f"  1. Quit Chrome completely\n"
                f"  2. Run:\n"
                f'     /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome --remote-debugging-port=9222\n\n'
                f"Original error: {e}"
            ) from e

        self._owns_browser = False

        # Get the default context (the user's existing browser context)
        contexts = self._browser.contexts
        if contexts:
            self._context = contexts[0]
        else:
            self._context = self._browser.contexts[0]

        # Open a new tab in existing window
        self._page = self._context.new_page()

        if start_url and start_url != "about:blank":
            self._page.goto(start_url, wait_until="domcontentloaded")

        self._page_count = len(self._context.pages)
        logger.info(f"Connected to existing Chrome at {CDP_URL}, opened new tab")

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

        if start_url and start_url != "about:blank":
            self._page.goto(start_url, wait_until="domcontentloaded")

        self._page_count = len(self._context.pages)
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

    def _check_for_new_tab(self) -> None:
        """If a new tab opened, switch to it automatically."""
        if self._context is None:
            return
        pages = self._context.pages
        if len(pages) > self._page_count:
            new_page = pages[-1]
            if new_page != self._page:
                # Wait for new tab to load
                try:
                    new_page.wait_for_load_state("domcontentloaded", timeout=5000)
                except Exception:
                    pass  # Page might already be loaded or timeout
                self._page = new_page
                self._page_count = len(pages)
                logger.info(f"Switched to new tab: {self._page.url}")
        # Also handle tabs being closed
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

    def screenshot(self) -> str:
        """Capture screenshot, return base64 PNG string."""
        png_bytes = self.page.screenshot(type="png")
        return base64.standard_b64encode(png_bytes).decode("utf-8")

    def screenshot_to_file(self, path: str) -> str:
        """Capture screenshot, save to file, return base64 PNG string."""
        png_bytes = self.page.screenshot(type="png")
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
            self.page.goto(url, wait_until="domcontentloaded")
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
                # Cap at 60s so wait(20) in the manual-login loop sleeps the full 20.
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

            # Check if a click/key opened a new tab — switch to it
            if action in ("left_click", "right_click", "middle_click", "double_click",
                          "triple_click", "key"):
                time.sleep(0.5)  # Brief wait for new tab to appear
                self._check_for_new_tab()

            # For non-screenshot actions, return screenshot so Claude sees the result
            return {"output": None, "error": None, "base64_image": self.screenshot()}

        except Exception as e:
            logger.error(f"Action {action} failed: {e}")
            return {"output": None, "error": str(e), "base64_image": None}
