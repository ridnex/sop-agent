"""Post-processing functions to clean a raw trace into a final trace.

Raw trace: every single OS event (mousedown, mouseup, keypress, keyrelease, scroll).
Clean trace: meaningful actions only (mouseup, keystroke, keypress of special keys, scroll).

Pipeline order matters:
1. merge_consecutive_scrolls
2. remove_esc_key
3. remove_action_type("keyrelease")
4. remove_action_type("mousedown")
5. merge_consecutive_keystrokes
6. merge_consecutive_states
7. re-number IDs
"""

from copy import deepcopy
from datetime import datetime
from typing import List

# Special keys that should NOT be merged into keystrokes — they stay as individual keypress events
SPECIAL_KEYS = {
    "Key.enter", "Key.tab", "Key.esc", "Key.escape",
    "Key.f1", "Key.f2", "Key.f3", "Key.f4", "Key.f5",
    "Key.f6", "Key.f7", "Key.f8", "Key.f9", "Key.f10",
    "Key.f11", "Key.f12",
    "Key.up", "Key.down", "Key.left", "Key.right",
    "Key.home", "Key.end", "Key.page_up", "Key.page_down",
    "Key.delete", "Key.insert",
    "Key.cmd", "Key.cmd_l", "Key.cmd_r",
    "Key.ctrl", "Key.ctrl_l", "Key.ctrl_r",
    "Key.alt", "Key.alt_l", "Key.alt_r", "Key.alt_gr",
}

# Keys that are part of text input and should be merged into keystrokes
MERGEABLE_MODIFIER_KEYS = {
    "Key.space", "Key.shift", "Key.shift_l", "Key.shift_r",
    "Key.backspace", "Key.caps_lock",
}


def _get_xpath(entry: dict) -> str:
    """Extract xpath from an action's element_attributes.

    Handles both flat format {xpath, tag, ...} and legacy nested {element: {xpath, ...}}.
    """
    ea = entry.get("data", {}).get("element_attributes", {})
    if not isinstance(ea, dict):
        return ""
    # Flat format (new): element_attributes.xpath
    if "xpath" in ea:
        return ea.get("xpath", "") or ""
    # Nested format (legacy): element_attributes.element.xpath
    elem = ea.get("element", {})
    return elem.get("xpath", "") if isinstance(elem, dict) else ""


def _is_action(entry: dict) -> bool:
    return entry.get("type") == "action"


def _is_state(entry: dict) -> bool:
    return entry.get("type") == "state"


def _action_type(entry: dict) -> str:
    return entry.get("data", {}).get("type", "")


def merge_consecutive_scrolls(trace: list) -> list:
    """Merge consecutive scroll actions at the same location (within 5px).

    Sums dx and dy values. Uses timestamp of the last scroll in the group.
    Skips over states between scroll actions so they get properly merged.
    """
    result = []
    i = 0
    while i < len(trace):
        entry = trace[i]

        if not (_is_action(entry) and _action_type(entry) == "scroll"):
            result.append(entry)
            i += 1
            continue

        # Start of a scroll group
        merged = deepcopy(entry)
        total_dx = merged["data"].get("dx", 0) or 0
        total_dy = merged["data"].get("dy", 0) or 0
        base_x = merged["data"].get("x", 0) or 0
        base_y = merged["data"].get("y", 0) or 0

        j = i + 1
        while j < len(trace):
            next_entry = trace[j]
            # Skip states between scroll actions — they'll be cleaned up later
            if _is_state(next_entry):
                j += 1
                continue
            if not (_is_action(next_entry) and _action_type(next_entry) == "scroll"):
                break
            nx = next_entry["data"].get("x", 0) or 0
            ny = next_entry["data"].get("y", 0) or 0
            # Only merge if at approximately the same location
            if abs(nx - base_x) <= 5 and abs(ny - base_y) <= 5:
                total_dx += next_entry["data"].get("dx", 0) or 0
                total_dy += next_entry["data"].get("dy", 0) or 0
                # Update timestamp to last scroll
                merged["data"]["timestamp"] = next_entry["data"]["timestamp"]
                merged["data"]["secs_from_start"] = next_entry["data"]["secs_from_start"]
                j += 1
            else:
                break

        merged["data"]["dx"] = total_dx
        merged["data"]["dy"] = total_dy
        result.append(merged)
        i = j

    return result


def remove_esc_key(trace: list) -> list:
    """Remove all keypress/keyrelease events where key == 'Key.esc'."""
    return [
        entry for entry in trace
        if not (
            _is_action(entry)
            and _action_type(entry) in ("keypress", "keyrelease")
            and entry["data"].get("key") in ("Key.esc", "Key.escape", "Escape")
        )
    ]


def remove_action_type(trace: list, type_name: str) -> list:
    """Remove all actions of the given type (e.g., 'keyrelease', 'mousedown')."""
    return [
        entry for entry in trace
        if not (_is_action(entry) and _action_type(entry) == type_name)
    ]


def merge_consecutive_keystrokes(trace: list) -> list:
    """Merge consecutive keypress events into keystroke events.

    Rules:
    - Only merge if consecutive keypresses are in the SAME input field (same xpath)
    - Special keys (Enter, Tab, arrows, etc.) are NOT merged — kept as individual keypress
    - Mergeable modifiers (space, shift, backspace, caps_lock) are included in merges
    - Result type is "keystroke" with key formatted as "'k' 'e' 'y' ..."
    - Records start_timestamp (first key) and end_timestamp (last key)
    """
    result = []
    i = 0
    while i < len(trace):
        entry = trace[i]

        if not (_is_action(entry) and _action_type(entry) == "keypress"):
            result.append(entry)
            i += 1
            continue

        key = entry["data"].get("key", "")

        # Special keys are kept as individual keypress events
        if key in SPECIAL_KEYS:
            result.append(entry)
            i += 1
            continue

        # Start a keystroke group
        keys_in_group = [key]
        start_entry = deepcopy(entry)
        xpath = _get_xpath(entry)
        last_timestamp = entry["data"].get("timestamp", "")
        last_secs = entry["data"].get("secs_from_start", 0)

        j = i + 1
        while j < len(trace):
            next_entry = trace[j]

            # Skip states between keypresses (they'll be handled by merge_consecutive_states)
            if _is_state(next_entry):
                j += 1
                continue

            if not (_is_action(next_entry) and _action_type(next_entry) == "keypress"):
                break

            next_key = next_entry["data"].get("key", "")

            # Don't merge special keys
            if next_key in SPECIAL_KEYS:
                break

            # Only merge if same input field
            next_xpath = _get_xpath(next_entry)
            if xpath and next_xpath and xpath != next_xpath:
                break

            keys_in_group.append(next_key)
            last_timestamp = next_entry["data"].get("timestamp", "")
            last_secs = next_entry["data"].get("secs_from_start", 0)
            j += 1

        # Format key string: "'k' 'e' 'y' 'c' 'l' 'o' 'a' 'k'"
        formatted_keys = " ".join(f"'{k}'" for k in keys_in_group)

        merged = deepcopy(start_entry)
        merged["data"]["type"] = "keystroke"
        merged["data"]["key"] = formatted_keys
        merged["data"]["start_timestamp"] = start_entry["data"].get("timestamp", "")
        merged["data"]["end_timestamp"] = last_timestamp
        merged["data"]["timestamp"] = last_timestamp
        merged["data"]["secs_from_start"] = last_secs

        result.append(merged)
        i = j

    return result


def merge_consecutive_states(trace: list) -> list:
    """When multiple states appear consecutively, keep only the LAST one.

    The last state is closest to the following action (most accurate representation
    of what the screen looked like when the action happened).
    """
    result = []
    i = 0
    while i < len(trace):
        entry = trace[i]

        if not _is_state(entry):
            result.append(entry)
            i += 1
            continue

        # Find the last consecutive state
        last_state = entry
        j = i + 1
        while j < len(trace) and _is_state(trace[j]):
            last_state = trace[j]
            j += 1

        result.append(last_state)
        i = j

    return result


def renumber_ids(trace: list) -> list:
    """Re-number all IDs sequentially starting from 0."""
    for i, entry in enumerate(trace):
        entry["data"]["id"] = i
    # Re-number step counters separately for states and actions
    state_step = 0
    action_step = 0
    for entry in trace:
        if _is_state(entry):
            entry["data"]["step"] = state_step
            state_step += 1
        elif _is_action(entry):
            entry["data"]["step"] = action_step
            action_step += 1
    return trace


def postprocess(raw_trace: list) -> list:
    """Run the full post-processing pipeline on a raw trace.

    Args:
        raw_trace: List of {"type": "state"|"action", "data": {...}} dicts.

    Returns:
        Cleaned trace with merged keystrokes, merged scrolls, removed noise.
    """
    trace = deepcopy(raw_trace)

    # Pipeline order matters!
    trace = merge_consecutive_scrolls(trace)
    trace = remove_esc_key(trace)
    trace = remove_action_type(trace, "keyrelease")
    trace = remove_action_type(trace, "mousedown")
    trace = merge_consecutive_keystrokes(trace)
    trace = merge_consecutive_states(trace)
    trace = renumber_ids(trace)

    return trace
