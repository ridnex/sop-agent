"""Load hand-authored SOPs from outputs/rl_data/sops/."""

import json
import re

from rl_data.config import SOPS_DIR
from rl_data.models import SOPEntry


def load_sop(sop_id: str) -> SOPEntry:
    txt_path = SOPS_DIR / f"{sop_id}.txt"
    json_path = SOPS_DIR / f"{sop_id}.json"
    if not txt_path.exists():
        raise FileNotFoundError(f"SOP text not found: {txt_path}")
    if not json_path.exists():
        raise FileNotFoundError(f"SOP metadata not found: {json_path}")

    sop_text = txt_path.read_text(encoding="utf-8").strip()
    meta = json.loads(json_path.read_text(encoding="utf-8"))

    return SOPEntry(
        id=meta["id"],
        sop_text=sop_text,
        task_intent=meta.get("task_intent", ""),
        ui_name=meta.get("ui_name", ""),
        start_url=meta.get("start_url", "about:blank"),
        variant="original",
        parent_sop_id=None,
    )


def load_all_sops() -> list[SOPEntry]:
    """Load every sop_*.txt with a matching .json, sorted by id."""
    entries: list[SOPEntry] = []
    for txt_path in sorted(SOPS_DIR.glob("sop_*.txt")):
        if "__regen_" in txt_path.stem or "__repair_" in txt_path.stem:
            continue  # regen/repair children are run-time artifacts, not standalone SOPs
        entries.append(load_sop(txt_path.stem))
    return entries


def count_sop_steps(sop_text: str) -> int:
    """Count numbered steps in an SOP (leading 'N.' patterns)."""
    return len(re.findall(r"(?m)^\s*\d+\.\s", sop_text))
