# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Enterprise workflow automation system that captures human workflows, generates SOPs (Standard Operating Procedures) using GPT-4o, replays them autonomously, and validates completion. Four-stage pipeline: **Record → Generate SOP → Execute → Validate**.

Based on the [ECLAIR](https://github.com/HazyResearch/eclair-agents) research project. macOS-only (relies on Accessibility API and `screencapture`).

## Running Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Set up .env with OPENAI_API_KEY
```

### Recording

**System-wide recorder with video** (works with any app):
```bash
python -m recorder.record --name "my_task"
python -m recorder.record --name "my_task" --output ./outputs
```

### SOP Generation

```bash
python -m sop.main <experiment_name>
python -m sop.main <experiment_name> --methods wd wd_kf wd_kf_act
python -m sop.main <experiment_name> --intent "do X" --ui-name "App"
python -m sop.main <experiment_name> --dry-run   # no API call
python -m sop.main --source outputs --experiments exp1 exp2  # batch
```

### SOP Execution

**System-level execution** (pyautogui, works with any app):
```bash
python -m execute.main <experiment_name>
python -m execute.main --sop-file /path/to/sop.txt --yes
python -m execute.main <experiment_name> --max-steps 30 --delay 1.5
python -m execute.main <experiment_name> --yes --validate
python -m execute.main <experiment_name> --detector dino   # use DINO instead of YOLO
```

### Validation

**System-level validation** (screenshot-only):
```bash
python -m validate.main <execution_dir_or_name>
python -m validate.main exec_test_video4_2026-03-11-10-32-25
```

## Architecture

### Recorder (`recorder/`)

System-wide recorder with video. Uses pynput listeners on the main thread (macOS requirement), captures screen state via the Observer for each event. Continuous video recording via macOS `screencapture`. After recording stops, screenshots are extracted from video at state timestamps. macOS Accessibility API for element detection.

### Post-processing Pipeline

Raw traces go through an ordered pipeline (order matters):
1. Merge consecutive scrolls → 2. Remove ESC key → 3. Remove keyrelease events → 4. Remove mousedown events → 5. Merge consecutive keystrokes → 6. Merge consecutive states → 7. Re-number IDs

### SOP Generation (`sop/`)

Three methods of increasing detail sent to GPT-4o:
- **wd**: Text-only workflow description
- **wd_kf**: Text + key frame screenshots
- **wd_kf_act**: Text + screenshots + interleaved semantic action descriptions (primary method, default)

Actions in the DSL use semantic element descriptions (e.g., `CLICK on button labeled 'Save'`) instead of coordinates. The `ui_name` is auto-detected from `active_application_name` in traces.

### Execution (`execute/`)

Observe → Think → Act loop using macOS screencapture + pyautogui. At each step:
1. **Observe**: Detect active display (per-step, supports multi-display), take screenshot, run UI element detection (YOLO or DINO)
2. **Think**: Send annotated screenshot + detected elements JSON + SOP + action history to GPT-4o. FM can use `CLICK_ELEMENT(id)` for precise element clicks or `CLICK(x, y)` as fallback
3. **Act**: Resolve element coordinates (pixel→point conversion with display offset), execute via pyautogui

Coordinate-based actions (CLICK(x,y), TYPE('text'), etc.). Works with any application. Multi-display support: display origin offset applied to all coordinate actions so clicks land on the correct monitor. Stuck detection stops after 10 consecutive attempts on the same SOP step.

`--detector` flag selects between `yolo` (default) and `dino` element detectors.

### Validation (`validate/`)

Sends final screenshot + task description + execution history to GPT-4o. Returns `{"thinking": "...", "was_completed": true/false}`.

### Group RL (`group_RL/`)

Foundation for an inference-time, gradient-free pipeline that generates G candidate SOPs per intent, ranks them by group consensus, executes the winner, and writes validated SOPs back to a growing memory. No fine-tuning, no GPU.

Currently shipped: `group_RL/embedding.py` — the shared text-embedding primitive used by every downstream component (intent retrieval, group-consensus ranking, etc.).

- Local model: `BAAI/bge-small-en-v1.5` (384-dim, ~140 MB, CPU-only).
- Lazy-loaded module-level singleton; one `pip install sentence-transformers`.
- L2-normalized output → cosine similarity reduces to a dot product.
- Public API: `get_embedder()`, `embed_text(str)`, `embed_texts(list[str])`, `cosine_similarity(a, b)`.

Smoke test (downloads weights to `~/.cache/huggingface/` on first run):
```bash
python -m group_RL.test_embedding
```

Downstream components (memory store, group-consensus ranker, retrieval-then-adapt, full pipeline) are not built yet and are tracked in the plan file.

### Element Detection (`yolo/`, `dino/`)

UI element detection with two backends (selectable via `--detector`):
- **YOLO** (`yolo/`): YOLO model (OmniParser weights) + EasyOCR + heuristic classifier. Public API: `from yolo import detect_elements`.
- **DINO** (`dino/`): Grounding DINO-based detection. Public API: `from dino import detect_elements`.

Both return the same interface: `(elements_list, annotated_image_path)` where each element has `id`, `class`, `label`, `confidence`, `bbox_pixels`, and `center_points` (logical point coordinates ready for pyautogui).

### Data Format

**Recording traces** are JSON with alternating `state` and `action` entries. Each experiment produces:
```
<name> @ <timestamp>/
├── [raw] <name> @ <timestamp>.json   # Unfiltered events
├── <name> @ <timestamp>.json         # Cleaned trace
├── <name> @ <timestamp>.mp4          # Screen recording
├── screenshots/                       # Extracted frames (0.png, 1.png, ...)
├── prompt.txt                         # Task intent
├── method_wd.txt                     # Generated SOPs
├── method_wd_kf.txt
└── method_wd_kf_act.txt
```

**Execution runs** are saved to `outputs/executions/`:
```
exec_<name>_<timestamp>/
├── execution_screenshots/             # Raw screenshots per step
│   ├── step_001.png
│   └── ...
├── yolo/  (or dino/)                  # Element detection outputs
│   ├── step_001_annotated.png         # Screenshot with bounding boxes + IDs
│   ├── step_001_elements.json         # Detected elements (id, label, class, bbox, center_points)
│   └── ...
├── execution_log.json                 # Full step-by-step log
└── validation_result.json             # (if --validate)
```

## Key Configuration

`config.py` holds global settings: model (`gpt-4o`), temperature (0.2), max tokens (4096), rate limiting, and retry logic. API key loaded from `.env`.

Ground truth experiments live in `experiments/`. Generated outputs go to `outputs/`.
