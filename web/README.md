# Web SOP Executor

CDP-based browser automation that uses **Claude Computer Use** to execute SOPs in Chrome. Replaces the desktop executor's pyautogui + YOLO/DINO + GPT-4o stack with a browser-native approach.

## How It Works

```
You provide an SOP file
        |
        v
Chrome launches with persistent profile (--launch mode)
  - Cookies/sessions saved across runs
  - Log into sites once, stays logged in
        |
        v
SOP is sent to Claude Computer Use API as system prompt
        |
        v
Claude drives the loop:
  1. Takes screenshot --> CDP captures the browser page, returns image
  2. Claude reads screenshot + SOP, decides next action
  3. Requests click/type/scroll/key --> Playwright executes via CDP
  4. Takes screenshot to verify --> back to step 1
        |
        v
Claude says "SOP_COMPLETED" --> execution_log.json saved --> done
Browser window stays open (you can inspect the final state)
```

**Key difference from `execute/`:** Claude drives the loop (it requests tools), not our code. Our code just executes what Claude asks and returns results.

### What Replaces What

| Desktop executor (`execute/`) | Web executor (`web/execute/`) |
|-------------------------------|-------------------------------|
| pyautogui (OS mouse/keyboard) | Playwright + CDP (browser-native) |
| YOLO/DINO element detection | Dropped (Claude sees screenshots natively) |
| GPT-4o | Claude Computer Use API |
| macOS `screencapture` | CDP `Page.captureScreenshot` |
| Our code runs observe-think-act | Claude drives the loop via tool requests |

## Setup

```bash
# Install dependencies
pip install -r web/requirements.txt

# Install Chromium for Playwright
playwright install chromium
```

Add your Anthropic API key to `.env` in the project root:

```
ANTHROPIC_API_KEY=sk-ant-...
```

Get your key at [console.anthropic.com/settings/keys](https://console.anthropic.com/settings/keys).

## Usage

### Basic (recommended)

```bash
python -m web.execute.main --sop-file path/to/sop.txt --yes --launch
```

This launches a dedicated Chrome window with a persistent profile. First run, log into any sites you need (Gmail, etc.) — sessions are saved for future runs.

### All Options

```bash
# Skip confirmation, custom step limit and delay
python -m web.execute.main --sop-file sop.txt --yes --launch --max-steps 30 --delay 3

# Use a cheaper/faster model (helps with rate limits)
python -m web.execute.main --sop-file sop.txt --yes --launch --model claude-haiku-4-5-20251001

# Higher delay to avoid API rate limits
python -m web.execute.main --sop-file sop.txt --yes --launch --delay 5

# Custom output directory
python -m web.execute.main --sop-file sop.txt --yes --launch --output-dir ./my_output

# Verbose logging (debug)
python -m web.execute.main --sop-file sop.txt --yes --launch -v
```

### CLI Flags

| Flag | Default | Description |
|------|---------|-------------|
| `--sop-file` | (required) | Path to the SOP text file |
| `--launch` | `false` | Launch a dedicated Chrome window (recommended) |
| `--url` | `about:blank` | Initial URL (usually not needed — SOP contains navigation) |
| `--intent` | `""` | High-level goal description |
| `--max-steps` | `50` | Max tool-use steps before stopping |
| `--delay` | `2.0` | Seconds to wait after each action for page settling |
| `--yes` / `-y` | `false` | Skip confirmation prompt |
| `--headless` | `false` | No visible window (only with `--launch`) |
| `--verbose` / `-v` | `false` | Enable debug logging |
| `--model` | `claude-sonnet-4-20250514` | Claude model to use |
| `--output-dir` | auto-generated | Custom output directory |

## Browser Behavior

### Persistent Profile (`--launch`)

Uses Playwright with a dedicated profile at `web/.browser_profile/`:

- **First run**: Fresh Chrome window. Log into sites you need (Gmail, etc.) manually.
- **After that**: All cookies/sessions are preserved. Already logged in.
- **Browser stays open** after execution finishes — you can inspect the result.
- Completely separate from your personal Chrome.

### New Tab Detection

If Claude clicks a link that opens a new tab, the executor **automatically switches** to the new tab. Claude continues working on the new page without interruption.

### Navigation

Since CDP screenshots only show page content (no address bar), Claude navigates by pressing `Ctrl+L` (focus address bar), typing the URL, then pressing Enter. This is intercepted and converted to a `page.goto()` call.

### Safety

Press **ESC** at any time to abort execution. The partial log is saved.

## Example

Create `test_gmail_sop.txt`:

```
1. Navigate to gmail.com
2. Click the "Compose" button
3. In the "To" field, type "someone@gmail.com"
4. In the "Subject" field, type "Test from Claude"
5. In the message body, type "Hello! This is an automated test email."
6. Click the "Send" button
```

Run it:

```bash
python -m web.execute.main --sop-file test_gmail_sop.txt --yes --launch
```

Chrome opens, Claude executes each step. You watch in real-time. Browser stays open when done.

## Output

Results are saved to `outputs/web_executions/`:

```
outputs/web_executions/exec_<sop_name>_<timestamp>/
  execution_screenshots/
    step_001.png        # screenshot after each action
    step_002.png
    ...
  execution_log.json    # full step-by-step log
```

### execution_log.json format

```json
{
  "sop_text": "1. Navigate to gmail.com\n2. ...",
  "intent": "",
  "start_url": "about:blank",
  "steps": [
    {
      "step_number": 1,
      "screenshot_path": ".../step_001.png",
      "page_url": "https://www.google.com/",
      "model_action": "screenshot",
      "model_rationale": "Taking initial screenshot...",
      "current_sop_step": null,
      "is_completed": false,
      "error": null
    }
  ],
  "completed_successfully": true,
  "stuck_on_step": null
}
```

## Troubleshooting

### 429 Too Many Requests / Rate Limit

Your Anthropic API tier limits requests per minute. Computer Use is token-heavy (screenshots).

**Fixes:**
- Increase delay: `--delay 5` (more time between API calls)
- Use a cheaper model: `--model claude-haiku-4-5-20251001`
- Check your tier at [console.anthropic.com/settings/limits](https://console.anthropic.com/settings/limits)
- Request a tier upgrade or add more credits

### Claude Gets Stuck on Blank Page

The prompt tells Claude it's inside a browser and to use `Ctrl+L` to navigate. If it still gets stuck, provide a start URL: `--url https://gmail.com`

### Browser Closes Unexpectedly

The browser should stay open after execution. If it doesn't, make sure you're using `--launch` mode.

### Screenshots Are Blank/White

This means the page is `about:blank`. Claude needs to navigate first. The SOP should start with a navigation step like "Navigate to gmail.com".

## Module Structure

```
web/
  __init__.py
  README.md
  requirements.txt
  .browser_profile/        <-- persistent browser data (cookies, sessions)
  execute/
    __init__.py
    main.py                <-- CLI entry point
    agent.py               <-- Core loop: Claude requests tools, we execute via CDP
    browser.py             <-- Playwright + CDP (click, type, scroll, screenshot, tab switch)
    api_client.py          <-- Claude Computer Use API client with retry logic
    prompts.py             <-- System prompt template + SOP_COMPLETED sentinel
    models.py              <-- StepRecord + ExecutionLog dataclasses
    config.py              <-- API key, model, browser dimensions, retry config
```

## Supported Actions

Claude can request any of these via the Computer Use tool:

| Action | What happens |
|--------|-------------|
| `screenshot` | CDP captures the current page as PNG |
| `left_click` | Click at (x, y) coordinates |
| `right_click` | Right-click at (x, y) |
| `double_click` | Double-click at (x, y) |
| `triple_click` | Triple-click (select line/paragraph) |
| `middle_click` | Middle-click at (x, y) |
| `mouse_move` | Move cursor to (x, y) |
| `left_click_drag` | Drag from start to end coordinates |
| `type` | Insert text (handles unicode) |
| `key` | Key press or combo (`Return`, `ctrl+a`, `ctrl+l`) |
| `scroll` | Scroll up/down/left/right at position |
| `wait` | Pause for N seconds |

All actions go through CDP — no OS-level mouse/keyboard simulation. If a click opens a new tab, the executor automatically follows it.

## Supported Models

The executor auto-detects the correct Computer Use tool version based on the model:

| Model | Tool Version | Notes |
|-------|-------------|-------|
| `claude-sonnet-4-20250514` (default) | `computer_20250124` | Good balance of speed and quality |
| `claude-haiku-4-5-20251001` | `computer_20250124` | Fastest, cheapest, good for simple SOPs |
| `claude-opus-4-20250115` | `computer_20250124` | Most capable, slowest, most expensive |
| `claude-opus-4-6-*` | `computer_20251124` | Latest, with zoom support |
