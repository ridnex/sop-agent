"""Dataclasses for the RL data collection pipeline."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SOPEntry:
    """One SOP candidate to execute (original or regenerated)."""
    id: str
    sop_text: str
    task_intent: str
    ui_name: str = ""
    start_url: str = "about:blank"
    variant: str = "original"       # "original" | "regen_N" | "repair_N"
    parent_sop_id: Optional[str] = None


@dataclass
class RunRecord:
    """Outcome of executing one SOPEntry."""
    sop_id: str
    variant: str
    parent_sop_id: Optional[str]
    task_intent: str
    sop_text: str
    execution_dir: str
    was_completed: bool
    label: str                      # "good" | "bad"
    failed_step: Optional[int]
    failure_reason: Optional[str]
    n_sop_steps: int
    created_at: str
    # optional extras
    validation_thinking: str = ""
    stuck_on_step: Optional[int] = None
    n_exec_steps: int = 0
    # inline-repair bookkeeping
    inline_repair_count: int = 0
    repair_of_step: Optional[int] = None
