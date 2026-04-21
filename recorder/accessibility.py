"""macOS Accessibility API wrapper for system-wide element detection.

Uses pyobjc bindings to AXUIElement APIs to inspect UI elements
across any application, not just Chrome.

Requires Accessibility permission in System Preferences > Privacy & Security.
"""

import ApplicationServices as AX
from Cocoa import NSWorkspace
import Quartz


def check_accessibility_permission() -> bool:
    """Check if this process has Accessibility permission.

    Returns:
        True if Accessibility access is granted, False otherwise.
    """
    return AX.AXIsProcessTrusted()


def _ax_get_attribute(element, attribute: str):
    """Get a single AX attribute value from an element. Returns None on failure."""
    err, value = AX.AXUIElementCopyAttributeValue(element, attribute, None)
    if err == 0:
        return value
    return None


def _ax_get_str(element, attribute: str) -> str:
    """Get a string AX attribute, returning '' on failure."""
    val = _ax_get_attribute(element, attribute)
    if val is not None:
        return str(val)
    return ""


def _build_ax_xpath(element) -> str:
    """Build an xpath-like string by walking up the AX parent chain.

    Example: "AXApplication[Finder]/AXWindow[1]/AXGroup[2]/AXButton[3]"
    """
    parts = []
    current = element
    max_depth = 30  # safety limit

    for _ in range(max_depth):
        role = _ax_get_str(current, "AXRole")
        if not role:
            break

        # Try to get a meaningful identifier
        title = _ax_get_str(current, "AXTitle")
        description = _ax_get_str(current, "AXDescription")
        identifier = title or description

        # Get sibling index for disambiguation
        parent = _ax_get_attribute(current, "AXParent")
        index = ""
        if parent is not None:
            err, children = AX.AXUIElementCopyAttributeValue(parent, "AXChildren", None)
            if err == 0 and children:
                same_role = [c for c in children if _ax_get_str(c, "AXRole") == role]
                if len(same_role) > 1:
                    for idx, sibling in enumerate(same_role):
                        if sibling == current:
                            index = str(idx + 1)
                            break

        if identifier:
            parts.append(f"{role}[{identifier}]")
        elif index:
            parts.append(f"{role}[{index}]")
        else:
            parts.append(role)

        if parent is None or role == "AXApplication":
            break
        current = parent

    parts.reverse()
    return "/".join(parts)


def _element_to_dict(element) -> dict:
    """Convert an AXUIElement to a dict matching the browser recorder format.

    Maps AX attributes to the same field names used by the browser recorder:
        xpath, tag, text, value, label, type, placeholder, role, x, y, width, height
    """
    if element is None:
        return {}

    role = _ax_get_str(element, "AXRole")
    if not role:
        return {}

    # Position and size
    x, y, width, height = 0, 0, 0, 0
    pos = _ax_get_attribute(element, "AXPosition")
    if pos is not None:
        try:
            point = AX.AXValueGetValue(pos, Quartz.kAXValueTypeCGPoint, None)
            if point:
                x, y = point[1].x, point[1].y
        except Exception:
            pass
    size = _ax_get_attribute(element, "AXSize")
    if size is not None:
        try:
            sz = AX.AXValueGetValue(size, Quartz.kAXValueTypeCGSize, None)
            if sz:
                width, height = sz[1].width, sz[1].height
        except Exception:
            pass

    title = _ax_get_str(element, "AXTitle")
    value = _ax_get_str(element, "AXValue")

    return {
        "xpath": _build_ax_xpath(element),
        "tag": role,
        "text": title or value,
        "value": value or None,
        "label": _ax_get_str(element, "AXDescription") or None,
        "type": _ax_get_str(element, "AXSubrole") or None,
        "placeholder": _ax_get_str(element, "AXPlaceholderValue") or None,
        "role": role,
        "x": x,
        "y": y,
        "width": width,
        "height": height,
    }


def get_element_at_position(x: float, y: float) -> dict:
    """Get the UI element at the given screen coordinates.

    Uses AXUIElementCopyElementAtPosition on the system-wide AXUIElement.

    Args:
        x: Screen X coordinate.
        y: Screen Y coordinate.

    Returns:
        Dict with element attributes, or {} on failure.
    """
    try:
        system_wide = AX.AXUIElementCreateSystemWide()
        err, element = AX.AXUIElementCopyElementAtPosition(system_wide, x, y, None)
        if err != 0 or element is None:
            return {}
        return _element_to_dict(element)
    except Exception:
        return {}


def get_focused_element() -> dict:
    """Get the currently focused UI element from the frontmost application.

    Uses AXFocusedUIElement attribute from the frontmost app's AXUIElement.

    Returns:
        Dict with element attributes, or {} on failure.
    """
    try:
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None:
            return {}
        pid = app.processIdentifier()
        app_element = AX.AXUIElementCreateApplication(pid)
        focused = _ax_get_attribute(app_element, "AXFocusedUIElement")
        if focused is None:
            return {}
        return _element_to_dict(focused)
    except Exception:
        return {}


INTERACTIVE_ROLES = {
    "AXButton", "AXTextField", "AXTextArea", "AXCheckBox",
    "AXRadioButton", "AXPopUpButton", "AXComboBox", "AXSlider",
    "AXMenuItem", "AXMenuBarItem", "AXTab", "AXLink",
    "AXIncrementor", "AXImage", "AXStaticText", "AXCell",
    "AXGroup", "AXDockItem",
}

MAX_ELEMENTS = 100


def _get_dock_elements() -> list[dict]:
    """Get interactive elements from the macOS Dock.

    The Dock is a separate process (com.apple.dock) that is not returned
    by ``get_all_interactive_elements`` because that function only queries
    the frontmost application.  This helper queries the Dock's AX tree
    directly and estimates icon positions from the Dock window bounds.

    Returns:
        List of element dicts (without ``id`` — caller assigns IDs).
    """
    from Cocoa import NSRunningApplication

    results: list[dict] = []

    try:
        dock_apps = NSRunningApplication.runningApplicationsWithBundleIdentifier_(
            "com.apple.dock"
        )
        if not dock_apps or len(dock_apps) == 0:
            return results
        dock_app = dock_apps[0]
        dock_pid = dock_app.processIdentifier()
        dock_ax = AX.AXUIElementCreateApplication(dock_pid)
    except Exception:
        return results

    # Try to get the Dock window bounds via CGWindowListCopyWindowInfo
    # so we can estimate icon positions.
    dock_bounds = None
    try:
        window_list = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionOnScreenOnly
            | Quartz.kCGWindowListExcludeDesktopElements,
            Quartz.kCGNullWindowID,
        )
        if window_list:
            for win in window_list:
                if win.get(Quartz.kCGWindowOwnerPID, 0) == dock_pid:
                    bounds = win.get(Quartz.kCGWindowBounds, {})
                    if bounds:
                        dock_bounds = {
                            "x": int(bounds.get("X", 0)),
                            "y": int(bounds.get("Y", 0)),
                            "width": int(bounds.get("Width", 0)),
                            "height": int(bounds.get("Height", 0)),
                        }
                        break
    except Exception:
        pass

    # Walk the Dock AX tree: AXApplication → AXList → children (dock items)
    try:
        children = _ax_get_attribute(dock_ax, "AXChildren")
        if not children:
            return results

        dock_items = []
        for child in children:
            role = _ax_get_str(child, "AXRole")
            if role == "AXList":
                items = _ax_get_attribute(child, "AXChildren")
                if items:
                    dock_items = list(items)
                break

        if not dock_items:
            return results

        for idx, item in enumerate(dock_items):
            title = _ax_get_str(item, "AXTitle")
            subrole = _ax_get_str(item, "AXSubrole")
            role = _ax_get_str(item, "AXRole") or "AXDockItem"

            # Try to get position/size directly from the element
            x, y, width, height = 0, 0, 0, 0
            pos = _ax_get_attribute(item, "AXPosition")
            if pos is not None:
                try:
                    point = AX.AXValueGetValue(pos, Quartz.kAXValueTypeCGPoint, None)
                    if point:
                        x, y = int(point[1].x), int(point[1].y)
                except Exception:
                    pass
            size = _ax_get_attribute(item, "AXSize")
            if size is not None:
                try:
                    sz = AX.AXValueGetValue(size, Quartz.kAXValueTypeCGSize, None)
                    if sz:
                        width, height = int(sz[1].width), int(sz[1].height)
                except Exception:
                    pass

            # If direct position unavailable, estimate from Dock window bounds
            if width == 0 and height == 0 and dock_bounds and dock_bounds["width"] > 0:
                n = len(dock_items)
                icon_w = dock_bounds["width"] // max(n, 1)
                icon_h = dock_bounds["height"]
                x = dock_bounds["x"] + idx * icon_w
                y = dock_bounds["y"]
                width = icon_w
                height = icon_h

            # Include items even without position — model can cross-reference screenshot
            if title or subrole:
                results.append({
                    "role": "AXDockItem",
                    "text": title or "",
                    "label": subrole or "",
                    "value": "",
                    "placeholder": "",
                    "x": x,
                    "y": y,
                    "width": width,
                    "height": height,
                })
    except Exception:
        pass

    return results


def get_all_interactive_elements(max_depth: int = 15) -> list[dict]:
    """Recursively enumerate visible interactive elements in the frontmost app.

    Walks the AX tree of the frontmost application's windows and collects
    elements whose role is in INTERACTIVE_ROLES and that have a non-zero
    bounding box.  Each element is assigned a sequential ``id`` (1, 2, 3, …).

    Args:
        max_depth: Maximum recursion depth when walking AXChildren.

    Returns:
        List of dicts, each with keys: id, role, text, label, value,
        placeholder, x, y, width, height.
    """
    try:
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None:
            return []
        pid = app.processIdentifier()
        app_element = AX.AXUIElementCreateApplication(pid)
    except Exception:
        return []

    windows = _ax_get_attribute(app_element, "AXWindows")
    if not windows:
        return []

    results: list[dict] = []

    def _walk(element, depth: int) -> None:
        if depth > max_depth or len(results) >= MAX_ELEMENTS:
            return

        role = _ax_get_str(element, "AXRole")
        if not role:
            return

        if role in INTERACTIVE_ROLES:
            # Extract bounding box
            x, y, width, height = 0, 0, 0, 0
            pos = _ax_get_attribute(element, "AXPosition")
            if pos is not None:
                try:
                    point = AX.AXValueGetValue(pos, Quartz.kAXValueTypeCGPoint, None)
                    if point:
                        x, y = point[1].x, point[1].y
                except Exception:
                    pass
            size = _ax_get_attribute(element, "AXSize")
            if size is not None:
                try:
                    sz = AX.AXValueGetValue(size, Quartz.kAXValueTypeCGSize, None)
                    if sz:
                        width, height = sz[1].width, sz[1].height
                except Exception:
                    pass

            # Only include elements with a non-zero bounding box
            if width > 0 and height > 0:
                title = _ax_get_str(element, "AXTitle")
                value = _ax_get_str(element, "AXValue")
                results.append({
                    "id": len(results) + 1,
                    "role": role,
                    "text": title or value or "",
                    "label": _ax_get_str(element, "AXDescription") or "",
                    "value": value or "",
                    "placeholder": _ax_get_str(element, "AXPlaceholderValue") or "",
                    "x": int(x),
                    "y": int(y),
                    "width": int(width),
                    "height": int(height),
                })

        if len(results) >= MAX_ELEMENTS:
            return

        children = _ax_get_attribute(element, "AXChildren")
        if children:
            for child in children:
                _walk(child, depth + 1)
                if len(results) >= MAX_ELEMENTS:
                    return

    for window in windows:
        _walk(window, 0)
        if len(results) >= MAX_ELEMENTS:
            break

    # Merge Dock elements into results
    if len(results) < MAX_ELEMENTS:
        try:
            dock_elements = _get_dock_elements()
            for el in dock_elements:
                if len(results) >= MAX_ELEMENTS:
                    break
                el["id"] = len(results) + 1
                results.append(el)
        except Exception:
            pass

    return results


def get_frontmost_app_info() -> dict:
    """Get information about the frontmost application and its active window.

    Returns:
        Dict with keys: app_name, bundle_id, pid, window_title,
        window_x, window_y, window_width, window_height.
    """
    info = {
        "app_name": "",
        "bundle_id": "",
        "pid": 0,
        "window_title": "",
        "window_x": 0,
        "window_y": 0,
        "window_width": 1440,
        "window_height": 900,
    }

    try:
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None:
            return info
        info["app_name"] = str(app.localizedName() or "")
        info["bundle_id"] = str(app.bundleIdentifier() or "")
        info["pid"] = app.processIdentifier()
    except Exception:
        return info

    # Get active window info via CGWindowListCopyWindowInfo
    try:
        window_list = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
            Quartz.kCGNullWindowID,
        )
        if window_list:
            for win in window_list:
                owner_pid = win.get(Quartz.kCGWindowOwnerPID, 0)
                if owner_pid == info["pid"]:
                    # Get window title
                    title = win.get(Quartz.kCGWindowName, "")
                    if title:
                        info["window_title"] = str(title)
                    # Get window bounds
                    bounds = win.get(Quartz.kCGWindowBounds, {})
                    if bounds:
                        info["window_x"] = int(bounds.get("X", 0))
                        info["window_y"] = int(bounds.get("Y", 0))
                        info["window_width"] = int(bounds.get("Width", 1440))
                        info["window_height"] = int(bounds.get("Height", 900))
                    break
    except Exception:
        pass

    return info
