"""Data models for SOP execution logging."""

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


@dataclass
class StepRecord:
    """One observe → think → act iteration."""
    step_number: int
    screenshot_path: str
    active_app: str
    model_action: str
    model_rationale: str
    current_sop_step: Optional[int] = None
    is_completed: bool = False
    error: Optional[str] = None


@dataclass
class ExecutionLog:
    """Full execution run."""
    sop_text: str
    intent: str
    steps: list[StepRecord] = field(default_factory=list)
    completed_successfully: bool = False
    stuck_on_step: Optional[int] = None

    def save(self, path: Path) -> None:
        """Save execution log as JSON."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2, ensure_ascii=False)
