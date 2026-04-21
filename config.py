import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

BASE_DIR = Path(__file__).parent
EXPERIMENTS_DIR = BASE_DIR / "experiments"
RESULTS_DIR = BASE_DIR / "results"

# Provider for SOP generation / repair / validation.
# "openai"   -> GPT-4o via OpenAI SDK
# "anthropic" -> Claude via Anthropic SDK (reuses ANTHROPIC_API_KEY)
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "anthropic")

# OpenAI settings (used when LLM_PROVIDER == "openai")
MODEL = "gpt-4o"

# Anthropic settings (used when LLM_PROVIDER == "anthropic")
# Same family as the browser executor — strong enough for SOP text work and
# guaranteed to exist on accounts that already run Computer Use.
CLAUDE_MODEL = "claude-sonnet-4-20250514"

TEMPERATURE = 0.2
MAX_TOKENS = 4096

RATE_LIMIT_DELAY = 2  # seconds between API calls
RETRY_COUNT = 3
RETRY_DELAYS = [5, 10, 20]  # seconds for exponential backoff
