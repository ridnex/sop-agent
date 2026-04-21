"""Web execution configuration."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

BASE_DIR = Path(__file__).parent.parent.parent  # project root
OUTPUTS_DIR = BASE_DIR / "outputs" / "web_executions"

# Browser profile persists cookies/sessions across runs (only used with --launch)
BROWSER_PROFILE_DIR = BASE_DIR / "web" / ".browser_profile"

# CDP connection to existing Chrome (default mode)
CDP_URL = "http://localhost:9222"

# Browser viewport — device_scale_factor=1 so CDP pixels = Claude coordinates
BROWSER_WIDTH = 1280
BROWSER_HEIGHT = 800

# Claude model
MODEL = "claude-sonnet-4-20250514"
MAX_TOKENS = 4096

# Execution
MAX_STEPS = 50
ACTION_DELAY = 2.0  # seconds after each action for page settling

# Retry / rate-limit
RETRY_COUNT = 3
RETRY_DELAYS = [5, 10, 20]  # exponential backoff seconds
