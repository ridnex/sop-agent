import re
from sop.data_loader import ActionEntry


def _parse_keystroke(key_str: str) -> str:
    """Parse keystroke format "'k' 'e' 'y'" into "key"."""
    chars = re.findall(r"'(.)'", key_str)
    return "".join(chars)


def _truncate(text: str, max_len: int = 80) -> str:
    """Truncate text to max_len characters."""
    if not text:
        return ""
    text = text.strip().replace("\n", " ")
    if len(text) > max_len:
        return text[:max_len] + "..."
    return text


def _describe_element(action: ActionEntry) -> str:
    """Build a human-readable element description from accessibility data."""
    parts = []

    # Role/tag as element type (prefer role, fall back to tag)
    role = action.element_role or action.element_tag or ""
    # Clean up AX prefix for readability: "AXButton" -> "button"
    if role.startswith("AX"):
        role = role[2:].lower()

    # Label or text as identifier
    label = action.element_label or action.element_text or ""

    if role and label:
        parts.append(f"{role} labeled '{_truncate(label)}'")
    elif role:
        parts.append(role)
    elif label:
        parts.append(f"element '{_truncate(label)}'")

    # Add placeholder hint for text fields
    if action.element_placeholder:
        parts.append(f"(placeholder: '{_truncate(action.element_placeholder, 40)}')")

    # Add value hint if present and different from label
    if action.element_value and action.element_value != label:
        parts.append(f"(value: '{_truncate(action.element_value, 40)}')")

    return " ".join(parts) if parts else "unknown element"


def format_action_dsl(action: ActionEntry) -> str:
    """Convert an ActionEntry to a semantic DSL string (no coordinates)."""
    if action.action_type == "mouseup":
        elem_desc = _describe_element(action)
        return f"CLICK on {elem_desc}"

    elif action.action_type == "keystroke":
        typed = _parse_keystroke(action.key) if action.key else ""
        return f"TYPE('{typed}')"

    elif action.action_type == "keypress":
        key = action.key or ""
        # Strip "Key." prefix
        if key.startswith("Key."):
            key = key[4:]
        return f"KEYPRESS({key.capitalize()})"

    elif action.action_type == "scroll":
        dy = round(action.dy) if action.dy is not None else 0
        if dy < 0:
            direction = "down"
        elif dy > 0:
            direction = "up"
        else:
            direction = "horizontally"
        return f"SCROLL {direction}"

    return f"UNKNOWN_ACTION({action.action_type})"
