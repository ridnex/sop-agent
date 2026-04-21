import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

BASE_DIR = Path(__file__).parent
EXPERIMENTS_DIR = BASE_DIR / "experiments"
RESULTS_DIR = BASE_DIR / "results"

MODEL = "gpt-4o"
TEMPERATURE = 0.2
MAX_TOKENS = 4096

RATE_LIMIT_DELAY = 2  # seconds between API calls
RETRY_COUNT = 3
RETRY_DELAYS = [5, 10, 20]  # seconds for exponential backoff
