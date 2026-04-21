import base64
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from config import EXPERIMENTS_DIR, BASE_DIR


@dataclass
class StateEntry:
    index: int  # 0-based index among states (maps to screenshots/{index}.png)
    url: Optional[str]
    screenshot_path: Optional[Path]
    active_app_name: Optional[str] = None
    window_title: Optional[str] = None


@dataclass
class ActionEntry:
    index: int  # 0-based index among actions
    action_type: str  # mouseup, keystroke, keypress, scroll
    x: Optional[float] = None
    y: Optional[float] = None
    dx: Optional[float] = None
    dy: Optional[float] = None
    key: Optional[str] = None
    element_tag: Optional[str] = None
    element_text: Optional[str] = None
    element_role: Optional[str] = None
    element_label: Optional[str] = None
    element_value: Optional[str] = None
    element_placeholder: Optional[str] = None
    element_subrole: Optional[str] = None
    element_xpath: Optional[str] = None
    raw: dict = field(default_factory=dict)


@dataclass
class Experiment:
    folder: str
    intent: str
    ui_name: str
    states: list[StateEntry] = field(default_factory=list)
    actions: list[ActionEntry] = field(default_factory=list)
    ground_truth: str = ""
    has_screenshots: bool = True


def _extract_element(ea: dict) -> dict:
    """Extract element info from element_attributes, handling both formats.

    VLDB nested: element_attributes.element.{tag, text, ...}
    Recorder flat: element_attributes.{tag, text, ...}
    """
    if not isinstance(ea, dict):
        return {}
    # Nested format (VLDB): has "element" sub-dict
    if "element" in ea and isinstance(ea["element"], dict):
        return ea["element"]
    # Flat format (recorder): tag/xpath at top level
    if "tag" in ea or "xpath" in ea:
        return ea
    return {}


def load_experiment(folder_path: Path, intent: str = "", ui_name: str = "") -> Experiment:
    """Load a single experiment from its folder.

    Handles both VLDB format (with webarena metadata) and recorder format (without).

    Args:
        folder_path: Path to the experiment folder.
        intent: Override intent (task description). If empty, reads from webarena metadata.
        ui_name: Override UI name. If empty, reads from webarena metadata.
    """
    folder_name = folder_path.name
    json_file = folder_path / f"{folder_name}.json"

    with open(json_file) as f:
        data = json.load(f)

    # Extract intent and ui_name from webarena metadata if available
    if not intent:
        webarena = data.get("webarena", {})
        intent = webarena.get("intent", folder_name)
    if not ui_name:
        webarena = data.get("webarena", {})
        sites = webarena.get("sites", [])
        ui_name = sites[0] if sites else "Application"

    screenshots_dir = folder_path / "screenshots"
    has_screenshots = screenshots_dir.is_dir()

    states = []
    actions = []
    state_idx = 0
    action_idx = 0

    for entry in data["trace"]:
        if entry["type"] == "state":
            d = entry.get("data", entry)  # data wrapper or flat
            screenshot_path = None
            if has_screenshots:
                png = screenshots_dir / f"{state_idx}.png"
                if png.exists():
                    screenshot_path = png
            states.append(StateEntry(
                index=state_idx,
                url=d.get("url"),
                screenshot_path=screenshot_path,
                active_app_name=d.get("active_application_name"),
                window_title=d.get("tab"),
            ))
            state_idx += 1

        elif entry["type"] == "action":
            d = entry.get("data", entry)
            elem = _extract_element(d.get("element_attributes", {}))
            actions.append(ActionEntry(
                index=action_idx,
                action_type=d["type"],
                x=d.get("x"),
                y=d.get("y"),
                dx=d.get("dx"),
                dy=d.get("dy"),
                key=d.get("key"),
                element_tag=elem.get("tag") if elem else None,
                element_text=elem.get("text") if elem else None,
                element_role=elem.get("role") if elem else None,
                element_label=elem.get("label") if elem else None,
                element_value=elem.get("value") if elem else None,
                element_placeholder=elem.get("placeholder") if elem else None,
                element_subrole=elem.get("type") if elem else None,
                element_xpath=elem.get("xpath") if elem else None,
                raw=d,
            ))
            action_idx += 1

    # Load ground truth SOP
    gt_file = folder_path / f"SOP - {folder_name}.txt"
    ground_truth = ""
    if gt_file.exists():
        ground_truth = gt_file.read_text(encoding="utf-8")

    return Experiment(
        folder=folder_name,
        intent=intent,
        ui_name=ui_name,
        states=states,
        actions=actions,
        ground_truth=ground_truth,
        has_screenshots=has_screenshots,
    )


def load_all_experiments() -> list[Experiment]:
    """Load all experiments from the experiments directory."""
    experiments = []
    for entry in sorted(EXPERIMENTS_DIR.iterdir()):
        if not entry.is_dir():
            continue
        json_file = entry / f"{entry.name}.json"
        if not json_file.exists():
            continue
        # Skip [gt] and [raw] prefixed folders
        if entry.name.startswith("["):
            continue
        experiments.append(load_experiment(entry))
    return experiments


def encode_screenshot_base64(path: Path) -> str:
    """Read a PNG file and return its base64 encoding."""
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")
