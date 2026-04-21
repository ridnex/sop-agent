"""Data classes for the system-wide interaction recorder.

State: represents the screen at a moment in time.
UserAction: represents one user interaction (click, keypress, scroll).
Trace: stores alternating sequence of State and UserAction.
"""

import json
from datetime import datetime
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class State:
    """Represents the screen at a moment in time."""
    url: str = ""
    tab: str = ""
    json_state: str = "[]"
    html: str = ""
    screenshot_base64: Optional[str] = None
    path_to_screenshot: str = ""
    window_position: dict = field(default_factory=lambda: {"x": 0, "y": 0})
    window_size: dict = field(default_factory=lambda: {"width": 1440, "height": 900})
    screen_size: dict = field(default_factory=lambda: {"width": 1792, "height": 1120})
    active_application_name: str = ""
    is_headless: bool = False
    timestamp: Optional[datetime] = None

    def to_dict(self, id_: int, step: int, secs_from_start: float) -> dict:
        return {
            "id": id_,
            "step": step,
            "timestamp": self.timestamp.isoformat() if self.timestamp else "",
            "secs_from_start": round(secs_from_start, 6),
            "url": self.url,
            "tab": self.tab,
            "json_state": self.json_state,
            "html": self.html,
            "screenshot_base64": self.screenshot_base64,
            "path_to_screenshot": self.path_to_screenshot,
            "window_position": self.window_position,
            "window_size": self.window_size,
            "active_application_name": self.active_application_name,
            "screen_size": self.screen_size,
            "is_headless": self.is_headless,
        }


@dataclass
class UserAction:
    """Represents one user interaction."""
    type: str  # "mouseup", "mousedown", "keypress", "keyrelease", "keystroke", "scroll"
    timestamp: Optional[datetime] = None
    x: Optional[float] = None
    y: Optional[float] = None
    dx: Optional[float] = None  # scroll only
    dy: Optional[float] = None  # scroll only
    key: Optional[str] = None  # keypress/keystroke
    is_right_click: bool = False
    pressed: bool = False  # mousedown=True, mouseup=False
    element_attributes: dict = field(default_factory=dict)
    start_timestamp: Optional[datetime] = None  # for merged keystrokes
    end_timestamp: Optional[datetime] = None  # for merged keystrokes

    def to_dict(self, id_: int, step: int, secs_from_start: float) -> dict:
        d = {
            "type": self.type,
            "timestamp": self.timestamp.isoformat() if self.timestamp else "",
            "secs_from_start": round(secs_from_start, 6),
            "step": step,
            "id": id_,
        }

        if self.type in ("mouseup", "mousedown"):
            d["x"] = self.x
            d["y"] = self.y
            d["is_right_click"] = self.is_right_click
            d["pressed"] = self.pressed
        elif self.type == "keystroke":
            d["key"] = self.key
            if self.start_timestamp:
                d["start_timestamp"] = self.start_timestamp.isoformat()
            if self.end_timestamp:
                d["end_timestamp"] = self.end_timestamp.isoformat()
        elif self.type == "keypress":
            d["key"] = self.key
        elif self.type == "keyrelease":
            d["key"] = self.key
        elif self.type == "scroll":
            d["x"] = self.x
            d["y"] = self.y
            d["dx"] = self.dx
            d["dy"] = self.dy

        if self.element_attributes:
            d["element_attributes"] = self.element_attributes

        # start_timestamp for non-keystroke types
        if self.type != "keystroke":
            d["start_timestamp"] = self.timestamp.isoformat() if self.timestamp else ""

        return d


class Trace:
    """Stores alternating sequence of State and UserAction entries.

    The log alternates: state, action, state, action, ..., state.
    """

    def __init__(self):
        self.log: list = []  # [{"type": "state"|"action", "data": State|UserAction}, ...]
        self._start_time: Optional[datetime] = None

    def log_state(self, state: State):
        if self._start_time is None and state.timestamp:
            self._start_time = state.timestamp
        self.log.append({"type": "state", "data": state})

    def log_action(self, action: UserAction):
        if self._start_time is None and action.timestamp:
            self._start_time = action.timestamp
        self.log.append({"type": "action", "data": action})

    def _secs_from_start(self, ts: Optional[datetime]) -> float:
        if ts is None or self._start_time is None:
            return 0.0
        return (ts - self._start_time).total_seconds()

    def to_json(self) -> list:
        """Convert the entire trace to a JSON-serializable list of dicts.

        Output format matches the ECLAIR dataset:
        [
            {"type": "state", "data": {...}},
            {"type": "action", "data": {...}},
            ...
        ]
        """
        result = []
        state_step = 0
        action_step = 0
        for i, entry in enumerate(self.log):
            obj = entry["data"]
            if entry["type"] == "state":
                secs = self._secs_from_start(obj.timestamp)
                data = obj.to_dict(id_=i, step=state_step, secs_from_start=secs)
                result.append({"type": "state", "data": data})
                state_step += 1
            elif entry["type"] == "action":
                secs = self._secs_from_start(obj.timestamp)
                data = obj.to_dict(id_=i, step=action_step, secs_from_start=secs)
                result.append({"type": "action", "data": data})
                action_step += 1
        return result

    def to_json_raw(self) -> list:
        """Same as to_json but uses a single sequential ID counter."""
        return self.to_json()
