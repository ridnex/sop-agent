# SOP Web Recorder

A Chrome extension that records browser workflows (clicks, typing, scrolls
+ video) into a folder of files, plus a Python CLI that imports those files
into the SOP pipeline.

This is the **user-facing** recorder — designed to be handed to non-developers.
For the system-wide macOS recorder (records any app, not just the browser),
see `recorder/`.

## Architecture

```
┌──────────────────────────────────────┐      ┌──────────────────────────┐
│ Chrome extension                     │      │ Python adapter           │
│ (recorder_web/extension/)            │ ───▶ │ (recorder_web/adapter.py)│
│ → 3 files in Downloads/recording_*/  │      │ → outputs/<name> @ <ts>/ │
└──────────────────────────────────────┘      └──────────────────────────┘
```

The extension produces:

```
~/Downloads/recording_<task>_<timestamp>/
├── recording.webm     # one continuous video, follows tab switches
├── events.json        # clicks, inputs, scrolls, key events, navigations
└── manifest.json      # session metadata
```

The adapter unpacks that into the project's standard layout, matching what
`recorder/record.py` produces. The rest of the pipeline (`sop/`, `execute/`,
`validate/`) ingests it unchanged.

## End-user instructions (the part you send to your testers)

### 1. Install the extension (one time)

1. Download `recorder_web/extension/` (zip the folder, unzip on the recipient's machine).
2. Open `chrome://extensions` in Chrome.
3. Toggle **Developer mode** on (top-right).
4. Click **Load unpacked** and pick the unzipped `extension/` folder.
5. The "SOP Web Recorder" icon appears in your toolbar. Pin it (puzzle-piece menu → pin) for easy access.

### 2. Record a workflow

1. Click the pinned extension icon. The screen-share picker appears.
2. Pick **Entire Screen** or a **Chrome window**. Recording starts; the icon shows a red **REC** badge.
3. Do your task. Switch tabs freely — the recording follows what you see on screen.
4. Click the icon again to stop. The badge disappears.
5. Three files land in `~/Downloads/recording_recording_<timestamp>/`. Send that whole folder back.

> Task name: there's no input — files just go into a `recording_…` folder. Rename the folder before sending if you want a specific name, or pass `--task-name` to the import CLI later.

### Common issues

| Symptom | Fix |
|---|---|
| "This site can't be reached" / extension does nothing | The extension needs Chrome 116+. Update Chrome. |
| Picker dialog never appears | Check `chrome://settings/content/all` — block on screen capture? Allow it. |
| Recording starts but stops immediately | You clicked "Stop sharing" in Chrome's screen-share pill at the top. Click the extension's Stop button instead. |
| Downloaded files are 0 bytes | Stopped before any chunks were flushed (< 1 second). Re-record. |

## Developer (you) instructions

### Run the import CLI

From the project root (with the project's normal venv activated):

```bash
# Bundle is the folder Chrome dropped into Downloads
python -m recorder_web import ~/Downloads/recording_test_gmail_2026-04-30-12-00-00

# Or pass a zip
python -m recorder_web import ~/Downloads/recording_bundle.zip

# Custom output base
python -m recorder_web import <bundle> --output ./outputs
```

This produces `outputs/<task> @ <ts>/` with the standard four artifacts:

```
outputs/test_gmail @ 2026-04-30-12-00-00/
├── [raw] test_gmail @ 2026-04-30-12-00-00.json   # synthesised raw trace
├── test_gmail @ 2026-04-30-12-00-00.json         # cleaned trace
├── test_gmail @ 2026-04-30-12-00-00.mp4          # converted from webm
├── prompt.txt                                     # task name as default intent
└── screenshots/0.png, 1.png, ...                 # keyframes
```

Then continue with the existing pipeline:

```bash
python -m sop.main "test_gmail @ 2026-04-30-12-00-00"
```

### Dependencies

```bash
pip install -r recorder_web/requirements.txt
# plus ffmpeg on PATH:
brew install ffmpeg     # macOS
apt install ffmpeg      # Linux
```

The extension itself has zero Python dependencies — these are only for the
adapter that converts the bundle into the existing pipeline's format.

## What gets captured

| Captured | Source | Example use |
|---|---|---|
| Click coords + selector + xpath + element label | `content_script.js` `click` listener | replay clicks; SOP narration |
| Final text per input field | `content_script.js` `input` listener (debounced 400ms) | "Type 'Acme' into First Name" |
| Special keys (Enter, Tab, Esc, arrows, Cmd/Ctrl combos) | `content_script.js` `keydown` listener | "Press Enter to submit" |
| Scroll resting position | `content_script.js` `scroll` listener (debounced 600ms) | scroll instructions |
| Tab switches + URL navigations | `chrome.tabs.onActivated` + `chrome.webNavigation` in `recorder.js` | route SOP across tabs |
| Continuous video of whatever the user picked in the screen-share dialog | `getDisplayMedia` in `recorder.js` | keyframe extraction |

## What does NOT get captured

- Mousemove / hover (noise)
- Per-keystroke typing events (use the merged `input` events instead)
- Network requests (would require `debugger` permission — too invasive)
- Audio (off by default in `getDisplayMedia` config)
- Anything outside the surface the user picked in the share dialog

## Notes on the schema mapping

The extension's events map to the existing trace schema as follows:

| Extension event | Trace action `type` |
|---|---|
| `click` | `mouseup` (with `x`, `y`, `is_right_click`, `element_attributes`) |
| `input` | `keystroke` (key formatted as `'a' 'b' 'c'`, matching `recorder/postprocess.py`) |
| `key` (special only) | `keypress` (key remapped to `Key.enter` / `Key.tab` / etc.) |
| `scroll` | `scroll` (`dy` carries scroll position, `dx`/`x`/`y` zeroed) |
| `tab_switched`, `navigation` | absorbed into the surrounding state's `url` and `tab` fields |

States are synthesised: one before each action, one after. This matches the
alternating layout produced by `recorder/record.py`. The `active_application_name`
field is populated with the URL hostname (so SOP generation can use it as the
"app name", same way the system recorder uses macOS app names).

## Limitations

- Video includes the browser chrome (URL bar, tab strip). For SOP keyframes
  this is actually useful — it gives the model URL context. If you need
  page-content-only video, switch to a `chrome.tabCapture` + canvas pipeline
  (out of scope for v1).
- The extension cannot persist after a Chrome restart mid-session — start
  and stop within the same session.
- Cross-origin iframe selectors include the frame URL, but events from
  inside `<iframe sandbox>` may be missing if the sandbox forbids scripts.
