"""Configuration for the RL data collection pipeline."""

from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
RL_DATA_DIR = BASE_DIR / "outputs" / "rl_data"
SOPS_DIR = RL_DATA_DIR / "sops"
EXECUTIONS_DIR = RL_DATA_DIR / "executions"
RUNS_JSONL = RL_DATA_DIR / "runs.jsonl"

MAX_REGEN_DEPTH = 2
MAX_STEPS = 25
ACTION_DELAY = 2.0
