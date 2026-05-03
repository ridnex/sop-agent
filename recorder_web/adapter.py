"""Convert a web-recorder bundle into the project's standard trace layout.

A bundle is either:
  - a folder produced by the Chrome extension containing
    ``recording.webm``, ``events.json``, ``manifest.json``; or
  - a ``.zip`` of that folder.

The adapter:
  1. Unpacks (if zipped) and validates the bundle.
  2. Converts ``recording.webm`` to ``recording.mp4`` via ffmpeg, because the
     existing ``recorder/screenshot_extractor.py`` uses ``moviepy`` whose
     ``.webm`` support is unreliable across versions.
  3. Synthesises a state/action trace matching ``recorder/models.py``'s
     schema, then runs the existing ``recorder/postprocess.postprocess``
     and ``recorder/screenshot_extractor.extract_screenshots`` so the rest
     of the SOP pipeline (``sop/``, ``execute/``, ``validate/``) ingests it
     unchanged.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

# Reuse the existing trace pipeline — these functions are pure and don't
# care whether the trace came from the system recorder or the web one.
from recorder.postprocess import postprocess
from recorder.screenshot_extractor import extract_screenshots


# MediaRecorder produces variable-frame-rate WebM. When pixels don't
# change for hundreds of ms (cursor still, cross-tab idle), the encoder
# emits no new frame for that window. A tight offset risks landing inside
# an encoder gap and snapping to a stale duplicate (or the click moment).
# 500 ms matches what the system recorder uses and reliably steps backwards
# past any gap onto a real captured frame where the cursor is on target
# before any click animation / navigation has rendered.
WEB_PRE_ACTION_OFFSET_SECS = 0.5


# ── Bundle loading ────────────────────────────────────────────────────


def _resolve_bundle(path: Path) -> Path:
    """Return a directory containing the three bundle files.

    If ``path`` is a zip, extract it to a temp dir and return that.  If it's
    already a directory, return it unchanged.
    """
    path = path.expanduser().resolve()
    if path.is_dir():
        return path
    if path.suffix == ".zip":
        tmp = Path(tempfile.mkdtemp(prefix="recorder_web_"))
        with zipfile.ZipFile(path) as zf:
            zf.extractall(tmp)
        # If the zip contained a single folder, drill in.
        entries = list(tmp.iterdir())
        if len(entries) == 1 and entries[0].is_dir():
            return entries[0]
        return tmp
    raise FileNotFoundError(f"Not a folder or zip: {path}")


def _load(bundle_dir: Path):
    events_path = bundle_dir / "events.json"
    manifest_path = bundle_dir / "manifest.json"
    video_path = bundle_dir / "recording.webm"
    for p in (events_path, manifest_path, video_path):
        if not p.exists():
            raise FileNotFoundError(
                f"Missing {p.name} in bundle {bundle_dir}\n"
                f"Expected: recording.webm, events.json, manifest.json"
            )
    with open(events_path) as f:
        events = json.load(f)
    with open(manifest_path) as f:
        manifest = json.load(f)
    return events, manifest, video_path


# ── Video conversion ──────────────────────────────────────────────────


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _webm_to_mp4(webm: Path, mp4: Path) -> None:
    if not _ffmpeg_available():
        raise RuntimeError(
            "ffmpeg not found on PATH. Install it with `brew install ffmpeg` "
            "(macOS) or `apt install ffmpeg` (Linux). The webm→mp4 conversion "
            "is required because moviepy's webm support is unreliable."
        )
    # ``-vsync passthrough`` preserves every input packet's PTS untouched.
    # We deliberately do NOT force a constant frame rate: that would
    # duplicate the previous encoded frame across encoder gaps, and during
    # cross-tab activity those duplicates show the wrong tab. Keeping the
    # original VFR timeline lets the ffmpeg ``-ss`` extractor below land on
    # the actual captured frame nearest each event timestamp.
    cmd = [
        "ffmpeg", "-y", "-i", str(webm),
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-vsync", "passthrough",
        "-an",  # strip audio if any
        str(mp4),
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise RuntimeError(
            "ffmpeg failed to convert webm → mp4:\n"
            + proc.stderr.decode("utf-8", errors="replace")[-2000:]
        )


def _extract_frame_ffmpeg(video_path: str, secs: float, out_png: str) -> None:
    """Decode a single frame at the given timestamp and write it as PNG.

    Used as the ``frame_extractor`` for web-recorder traces. Called
    once per state by ``recorder.screenshot_extractor.extract_screenshots``.

    We pass ``-ss`` *after* ``-i`` (output-side seek) so ffmpeg fully
    decodes up to the requested timestamp and returns the exact frame
    active at that moment. Input-side seeking would be faster but rounds
    to the nearest keyframe, which is unsuitable for frame-accurate
    keyframe screenshots.
    """
    secs = max(0.0, float(secs))
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(video_path),
        "-ss", f"{secs:.3f}",
        "-frames:v", "1",
        "-q:v", "2",
        str(out_png),
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg frame extraction failed at {secs:.3f}s: "
            + proc.stderr.decode("utf-8", errors="replace")[-1000:]
        )


def _video_duration_secs(video_path: Path) -> float:
    """Return the actual video file duration via ffprobe.

    Used to compensate for the encoder warm-up gap: ``MediaRecorder`` logs
    ``startedAt`` when ``start()`` is called, but the encoder may not commit
    the first frame until hundreds of ms later. The difference between the
    wall-clock ``manifest.duration_ms`` and the file's real duration is that
    gap — we shift the trace's timeline by it so screenshots land on the
    correct frames.
    """
    try:
        proc = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
        )
        return float(proc.stdout.decode("utf-8").strip())
    except (subprocess.CalledProcessError, ValueError, FileNotFoundError):
        return 0.0


# ── Event → trace mapping ─────────────────────────────────────────────


def _hostname(url: str) -> str:
    if not url:
        return "Chrome"
    try:
        h = urlparse(url).hostname or ""
        return h or "Chrome"
    except Exception:
        return "Chrome"


def _state_dict(*, idx_id: int, ts: datetime, secs: float,
                url: str, tab_title: str, viewport: dict) -> dict:
    return {
        "id": idx_id,
        "step": None,
        "timestamp": ts.isoformat(),
        "secs_from_start": round(secs, 6),
        "url": url,
        "tab": tab_title,
        "json_state": "[]",
        "html": "",
        "screenshot_base64": None,
        "path_to_screenshot": None,
        "window_position": {"x": 0, "y": 0},
        "window_size": viewport or {"width": 1280, "height": 720},
        "active_application_name": _hostname(url),
        "screen_size": viewport or {"width": 1280, "height": 720},
        "is_headless": False,
    }


def _element_attrs(ev: dict) -> dict:
    """Flatten the content-script event's `target` + selector into the same
    shape ``recorder/accessibility.py:_element_to_dict`` produces — which is
    what ``sop/data_loader._extract_element`` expects.

    Label-resolution priority (so SOP generation has a meaningful name even
    when the literal click target is a bare <img>/<svg>):
        own text → aria-label → alt → ancestor-inferred label → link text.
    """
    t = ev.get("target") or {}

    own_text = (t.get("text") or "").strip()
    aria = (t.get("aria_label") or "").strip()
    alt = (t.get("alt") or "").strip()
    inferred = (t.get("inferred_label") or "").strip()
    link_text = (t.get("link_text") or "").strip()

    # The "name" of what the user clicked — best available short label.
    label = aria or own_text or alt or inferred or link_text
    label = label[:200] if label else None

    # Free-text representation: prefer own visible text, fall back to label.
    text = (own_text or aria or alt or inferred or link_text)[:200]

    return {
        "xpath": ev.get("xpath", "") or "",
        "tag": t.get("tag") or "",
        "text": text,
        "value": t.get("value") or None,
        "label": label,
        "type": t.get("input_type") or None,
        "placeholder": t.get("placeholder") or None,
        "role": t.get("role") or t.get("tag") or "",
        "x": t.get("x", 0) or 0,
        "y": t.get("y", 0) or 0,
        "width": t.get("width", 0) or 0,
        "height": t.get("height", 0) or 0,
        "link_href": t.get("link_href") or None,
    }


def _format_keystroke(value: str) -> str:
    """Match the format produced by ``recorder/postprocess.merge_consecutive_keystrokes``:
    one space-separated quoted-char string like "'h' 'e' 'l' 'l' 'o'".
    """
    if value is None:
        return ""
    return " ".join(f"'{c}'" for c in value)


_KEY_NAME_MAP = {
    "Enter": "Key.enter",
    "Tab": "Key.tab",
    "Escape": "Key.esc",
    "Backspace": "Key.backspace",
    "Delete": "Key.delete",
    "ArrowUp": "Key.up",
    "ArrowDown": "Key.down",
    "ArrowLeft": "Key.left",
    "ArrowRight": "Key.right",
    "Home": "Key.home",
    "End": "Key.end",
    "PageUp": "Key.page_up",
    "PageDown": "Key.page_down",
    "F1": "Key.f1", "F2": "Key.f2", "F3": "Key.f3", "F4": "Key.f4",
    "F5": "Key.f5", "F6": "Key.f6", "F7": "Key.f7", "F8": "Key.f8",
    "F9": "Key.f9", "F10": "Key.f10", "F11": "Key.f11", "F12": "Key.f12",
}


def _map_key(key: str) -> str:
    if not key:
        return ""
    if key in _KEY_NAME_MAP:
        return _KEY_NAME_MAP[key]
    if len(key) == 1:
        return f"'{key}'"
    return f"Key.{key.lower()}"


def _opening_context(events: list[dict], manifest: dict) -> tuple[str, str]:
    """Best guess at the URL/title that was on screen when the user pressed
    Start. Used by both the just-before-first-action state inside build_trace
    and the t=0 opening keyframe prepended in ``convert`` after postprocess.
    """
    tab_titles: dict[int, str] = {}
    for tab in manifest.get("tabs_seen", []) or []:
        if "tab_id" in tab:
            tab_titles[tab["tab_id"]] = tab.get("title") or ""
    for ev in events or []:
        if (ev.get("kind") or ev.get("type")) == "navigation":
            url = ev.get("url", "") or ""
            tab_id = ev.get("tab_id")
            title = tab_titles.get(tab_id, "")
            if url:
                return url, title
    for ev in events or []:
        if ev.get("url"):
            return ev["url"], tab_titles.get(ev.get("tab_id"), "")
    return "", ""


def build_trace(events: list[dict], manifest: dict) -> list[dict]:
    """Synthesise a ``[{type, data}, ...]`` trace matching the recorder's contract.

    Layout:
        state(initial) → action → state → action → ... → state(final)

    Each state's ``secs_from_start`` is set to the moment the screenshot
    extractor will pull its frame from (next-action timestamp minus the
    pre-action offset for non-final states; the action's own timestamp for
    the final state). This keeps state metadata aligned with what's
    visually on screen — otherwise users see the screenshot showing one
    point in time while the trace claims a totally different timestamp.

    Each state's URL/tab title is taken from the most recent navigation /
    tab_switched event leading up to that state's screenshot moment.
    """
    # Must match the offset passed to extract_screenshots below.
    PRE_ACTION_OFFSET_MS = int(WEB_PRE_ACTION_OFFSET_SECS * 1000)

    started = datetime.fromisoformat(manifest["started_at"].replace("Z", "+00:00"))
    # Drop tz so emitted timestamps are naive ISO strings (matching what the
    # system recorder produces, which is what screenshot_extractor expects).
    if started.tzinfo is not None:
        started = started.replace(tzinfo=None)
    viewport = manifest.get("viewport") or {}
    if isinstance(viewport, dict) and "w" in viewport:
        viewport = {"width": viewport.get("w", 1280) or 1280,
                    "height": viewport.get("h", 720) or 720}
    elif not viewport:
        viewport = {"width": 1280, "height": 720}

    tab_titles: dict[int, str] = {}
    for tab in manifest.get("tabs_seen", []) or []:
        if "tab_id" in tab:
            tab_titles[tab["tab_id"]] = tab.get("title") or ""

    cur_url = ""
    cur_title = ""

    # ── First pass: collect actions in order, capturing the URL/title context
    # that was active at each action's moment. Navigation / tab_switched
    # events update the running context but are NOT emitted as actions.
    collected: list[dict] = []
    for ev in events:
        kind = ev.get("kind") or ev.get("type") or ""
        ts_ms = float(ev.get("ts") or 0)
        ts = started + timedelta(milliseconds=ts_ms)
        secs = ts_ms / 1000.0

        if kind == "navigation":
            cur_url = ev.get("url", cur_url) or cur_url
            tab_id = ev.get("tab_id")
            if tab_id in tab_titles:
                cur_title = tab_titles[tab_id]
            continue
        if kind == "tab_switched":
            cur_url = ev.get("to_url", cur_url) or cur_url
            cur_title = ev.get("to_title") or cur_title
            continue

        if ev.get("url"):
            cur_url = ev["url"]
        if ev.get("tab_id") in tab_titles:
            cur_title = tab_titles[ev["tab_id"]]

        action: Optional[dict] = None
        if kind == "click":
            action = {
                "id": 0,  # filled in second pass
                "step": None,
                "type": "mouseup",
                "timestamp": ts.isoformat(),
                "secs_from_start": round(secs, 6),
                "x": float(ev.get("x", 0) or 0),
                "y": float(ev.get("y", 0) or 0),
                "is_right_click": ev.get("button") == "right",
                "pressed": False,
                "element_attributes": _element_attrs(ev),
            }
        elif kind == "input":
            value = ev.get("value", "") or ""
            formatted = _format_keystroke(value)
            action = {
                "id": 0,
                "step": None,
                "type": "keystroke",
                "timestamp": ts.isoformat(),
                "secs_from_start": round(secs, 6),
                "key": formatted,
                "start_timestamp": ts.isoformat(),
                "end_timestamp": ts.isoformat(),
                "element_attributes": _element_attrs(ev),
            }
        elif kind == "key":
            action = {
                "id": 0,
                "step": None,
                "type": "keypress",
                "timestamp": ts.isoformat(),
                "secs_from_start": round(secs, 6),
                "key": _map_key(ev.get("key", "")),
                "element_attributes": _element_attrs(ev),
            }
        elif kind == "scroll":
            action = {
                "id": 0,
                "step": None,
                "type": "scroll",
                "timestamp": ts.isoformat(),
                "secs_from_start": round(secs, 6),
                "x": 0.0,
                "y": 0.0,
                "dx": 0.0,
                "dy": float(ev.get("scroll_y", 0) or 0),
                "element_attributes": {},
            }
        else:
            continue

        collected.append({
            "action": action,
            "ts_ms": ts_ms,
            "url": cur_url,
            "title": cur_title,
        })

    # ── Second pass: assemble trace with state timestamps aligned to where
    # screenshot_extractor will pull each state's frame from.
    trace: list[dict] = []

    def make_state_at(at_ms: float, url: str, title: str) -> dict:
        secs = at_ms / 1000.0
        ts = started + timedelta(milliseconds=at_ms)
        return {
            "type": "state",
            "data": _state_dict(
                idx_id=len(trace), ts=ts, secs=secs,
                url=url, tab_title=title, viewport=viewport,
            ),
        }

    # Closing keyframe: place 100 ms before recording end so the frame is
    # comfortably inside the video. Use the most recent URL/title seen.
    duration_ms = float(manifest.get("duration_ms") or 0)
    if not duration_ms and collected:
        duration_ms = collected[-1]["ts_ms"] + 100.0
    closing_ms = max(0.0, duration_ms - 100.0)
    closing_url = collected[-1]["url"] if collected else cur_url
    closing_title = collected[-1]["title"] if collected else cur_title

    # NOTE: the opening keyframe at t=0 is *not* added here. postprocess
    # runs ``merge_consecutive_states`` which keeps only the LAST of any
    # adjacent state run, so any opening state we add inside build_trace
    # gets eaten when the just-before-first-action state is also present.
    # The opening keyframe is prepended in ``convert`` *after* postprocess.

    # Just-before-first-action keyframe: shows the cursor on the first click
    # target. Without this the trace would jump straight from session start
    # to the post-action state and we'd lose the visual evidence of where
    # the user clicked.
    if collected:
        first = collected[0]
        pre_first_ms = max(0.0, first["ts_ms"] - PRE_ACTION_OFFSET_MS)
        trace.append(make_state_at(pre_first_ms, first["url"], first["title"]))

    for i, c in enumerate(collected):
        c["action"]["id"] = len(trace)
        trace.append({"type": "action", "data": c["action"]})

        if i + 1 < len(collected):
            nxt = collected[i + 1]
            # Mid-state's screenshot is pulled at next_action.ts - offset,
            # but never earlier than the just-completed action.
            state_ms = max(c["ts_ms"], nxt["ts_ms"] - PRE_ACTION_OFFSET_MS)
            trace.append(make_state_at(state_ms, nxt["url"], nxt["title"]))

    # ── Closing keyframe: shows the result *after* the last action, even
    # when the workflow ends inside an iframe / PDF viewer where we can't
    # capture clicks (e.g. zoom buttons inside Chrome's PDF viewer).
    trace.append(make_state_at(closing_ms, closing_url, closing_title))

    return trace


# ── Post-processing fix-up ───────────────────────────────────────────


def _realign_state_timestamps(trace: list[dict], started: datetime) -> list[dict]:
    """Re-anchor every state's ``timestamp`` / ``secs_from_start`` to the
    moment where ``screenshot_extractor`` will pull its frame from.

    Run AFTER ``postprocess`` because that step can merge consecutive
    actions (e.g. scrolls), shifting the next-action timestamp the
    extractor sees. Without this re-pass the state's metadata says one
    time but the screenshot is from a different moment.
    """
    PRE_OFFSET_MS = int(WEB_PRE_ACTION_OFFSET_SECS * 1000)

    def set_state_secs(state: dict, secs: float) -> None:
        clamped = max(0.0, secs)
        state["data"]["secs_from_start"] = round(clamped, 6)
        state["data"]["timestamp"] = (
            started + timedelta(milliseconds=clamped * 1000)
        ).isoformat()

    state_positions = [i for i, it in enumerate(trace) if it.get("type") == "state"]
    first_state_idx = state_positions[0] if state_positions else None
    last_state_idx = state_positions[-1] if state_positions else None

    for i, item in enumerate(trace):
        if item.get("type") != "state":
            continue
        # Boundary states are explicit opening/closing keyframes — leave
        # their planned timestamps alone (postprocess can't shift them
        # because no action follows / precedes inside the loop).
        if i == first_state_idx or i == last_state_idx:
            continue
        # Find the next action.
        next_action_secs = None
        for j in range(i + 1, len(trace)):
            if trace[j].get("type") == "action":
                d = trace[j]["data"]
                # screenshot_extractor reads start_timestamp for keystrokes.
                ts_field = (
                    d.get("start_timestamp")
                    if d.get("type") == "keystroke" and d.get("start_timestamp")
                    else d.get("timestamp")
                )
                if ts_field:
                    parsed = datetime.fromisoformat(ts_field.replace("Z", "+00:00"))
                    if parsed.tzinfo is not None:
                        parsed = parsed.replace(tzinfo=None)
                    next_action_secs = (parsed - started).total_seconds()
                else:
                    next_action_secs = float(d.get("secs_from_start", 0) or 0)
                break

        if next_action_secs is not None:
            set_state_secs(item, next_action_secs - PRE_OFFSET_MS / 1000.0)
        else:
            # Final state: extractor uses state's own ts. Anchor to the most
            # recent action so the screenshot lands on a meaningful frame.
            prev_action_secs = 0.0
            for j in range(i - 1, -1, -1):
                if trace[j].get("type") == "action":
                    prev_action_secs = float(trace[j]["data"].get("secs_from_start", 0) or 0)
                    break
            set_state_secs(item, prev_action_secs)
    return trace


# ── End-to-end conversion ────────────────────────────────────────────


def convert(bundle_path: str | Path,
            output_base: str | Path = "outputs",
            keep_temp: bool = False) -> Path:
    """Convert a web-recorder bundle into a standard ``outputs/<name> @ <ts>/`` folder.

    Returns the absolute path of the created folder.
    """
    bundle_dir = _resolve_bundle(Path(bundle_path))
    events_doc, manifest, webm_path = _load(bundle_dir)

    task_name = manifest.get("task_name") or "untitled"
    started_iso = manifest["started_at"]
    started_dt = datetime.fromisoformat(started_iso.replace("Z", "+00:00"))
    if started_dt.tzinfo is not None:
        started_dt = started_dt.replace(tzinfo=None)
    ts_str = started_dt.strftime("%Y-%m-%d-%H-%M-%S")
    folder_name = f"{task_name} @ {ts_str}"

    out_dir = (Path(output_base).expanduser().resolve()) / folder_name
    screenshots_dir = out_dir / "screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)

    raw_json_path = out_dir / f"[raw] {folder_name}.json"
    clean_json_path = out_dir / f"{folder_name}.json"
    mp4_path = out_dir / f"{folder_name}.mp4"

    # 1. webm → mp4 (the existing screenshot_extractor uses moviepy)
    print(f"[recorder_web] Converting video → {mp4_path.name} ...")
    _webm_to_mp4(webm_path, mp4_path)

    # 1b. Diagnostic only — confirm whether the encoder added gap at start
    # vs end. Empirically (verified via ``ffprobe`` packet PTS dumps), the
    # first frame in the file is at PTS 0.0 captured at ``startedAt`` with
    # no start warm-up, and ALL the missing wall-clock time accumulates at
    # the END (encoder still flushing buffers after we read ``endedAt``).
    # That means we should NOT shift video_start_time forward — the file's
    # timeline is already aligned with ``startedAt`` from the first frame.
    # We just print the discrepancy for sanity.
    manifest_duration_secs = float(manifest.get("duration_ms") or 0) / 1000.0
    video_duration_secs = _video_duration_secs(mp4_path)
    if manifest_duration_secs > 0 and video_duration_secs > 0:
        tail_gap = max(0.0, manifest_duration_secs - video_duration_secs)
        print(
            f"[recorder_web] Encoder tail-flush gap: "
            f"{tail_gap * 1000:.0f} ms "
            f"(manifest {manifest_duration_secs:.3f}s − video {video_duration_secs:.3f}s) — "
            f"start of video aligned with startedAt, no shift applied."
        )
    video_zero_dt = started_dt

    # 2. Build the synthesised raw trace.
    print("[recorder_web] Building trace from events.json ...")
    raw_trace = build_trace(events_doc.get("events", []), manifest)
    with open(raw_json_path, "w") as f:
        json.dump({"trace": raw_trace}, f, indent=2)

    # 3. Run the existing post-processing pipeline.
    print(f"[recorder_web] Post-processing ({len(raw_trace)} entries) ...")
    clean_trace = postprocess(raw_trace)

    # 3b. Realign state timestamps to wherever the screenshot will actually
    # be pulled from (postprocess can merge actions and shift their ts).
    clean_trace = _realign_state_timestamps(clean_trace, started_dt)

    # 3c. Prepend the t=0 opening keyframe. We do this AFTER postprocess
    # because ``merge_consecutive_states`` would collapse it into the
    # just-before-first-action state if both were emitted from build_trace.
    op_url, op_title = _opening_context(events_doc.get("events", []), manifest)
    viewport = manifest.get("viewport") or {}
    if isinstance(viewport, dict) and "w" in viewport:
        viewport = {"width": viewport.get("w", 1280) or 1280,
                    "height": viewport.get("h", 720) or 720}
    elif not viewport:
        viewport = {"width": 1280, "height": 720}
    opening_state = {
        "type": "state",
        "data": _state_dict(
            idx_id=0,
            ts=started_dt,
            secs=0.0,
            url=op_url,
            tab_title=op_title,
            viewport=viewport,
        ),
    }
    clean_trace = [opening_state] + clean_trace
    # Re-id everything so IDs are sequential (the just-prepended state is 0).
    for new_id, item in enumerate(clean_trace):
        item["data"]["id"] = new_id

    # 4. Extract keyframe screenshots from the mp4 at state timestamps.
    print(f"[recorder_web] Extracting screenshots → {screenshots_dir} ...")
    clean_trace = extract_screenshots(
        clean_trace,
        str(mp4_path),
        str(screenshots_dir),
        output_prefix="./screenshots",
        video_start_time=video_zero_dt,
        pre_action_offset_secs=WEB_PRE_ACTION_OFFSET_SECS,
        use_state_ts_at_boundaries=True,
        frame_extractor=_extract_frame_ffmpeg,
    )

    # 5. Save the cleaned trace.
    with open(clean_json_path, "w") as f:
        json.dump({"trace": clean_trace}, f, indent=2)

    # 6. Drop a prompt.txt seed so the SOP step can pick up the task name.
    prompt_path = out_dir / "prompt.txt"
    if not prompt_path.exists():
        prompt_path.write_text(task_name + "\n", encoding="utf-8")

    # Cleanup the temp extraction dir if we made one.
    if not keep_temp and bundle_dir.parent.name.startswith("recorder_web_"):
        try:
            shutil.rmtree(bundle_dir.parent)
        except Exception:
            pass

    print(f"\n[recorder_web] Done. Output: {out_dir}")
    print(f"  Raw:         {raw_json_path.name}")
    print(f"  Clean:       {clean_json_path.name}")
    print(f"  Video:       {mp4_path.name}")
    print(f"  Screenshots: {len(list(screenshots_dir.glob('*.png')))} files")
    print(f"\nNext: python -m sop.main \"{folder_name}\"")
    return out_dir
