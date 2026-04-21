"""Data models for web SOP execution logging."""

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


@dataclass
class StepRecord:
    """One tool-use round-trip."""
    step_number: int
    screenshot_path: str
    page_url: str
    model_action: str
    model_rationale: str
    current_sop_step: Optional[int] = None
    is_completed: bool = False
    error: Optional[str] = None


@dataclass
class RepairRecord:
    """One inline SOP-step rewrite performed during a run."""
    step_number: int
    original_text: str
    new_text: str
    failure_screenshot_path: str
    attempt_count: int
    at_execution_step: int  # which ExecutionLog.step triggered the repair


@dataclass
class ExecutionLog:
    """Full web execution run."""
    sop_text: str
    intent: str
    start_url: str = "about:blank"
    steps: list[StepRecord] = field(default_factory=list)
    completed_successfully: bool = False
    stuck_on_step: Optional[int] = None
    repairs: list[RepairRecord] = field(default_factory=list)
    repaired_sop_path: Optional[str] = None
    effective_sop_text: Optional[str] = None  # sop_text with all repairs applied

    def save(self, path: Path) -> None:
        """Save execution log as JSON."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2, ensure_ascii=False)
