# SOP Agent

Enterprise workflow automation: **Record → Generate SOP → Execute → Validate**.

Based on [ECLAIR](https://github.com/HazyResearch/eclair-agents). macOS-only.

## Setup

```bash
pip install -r requirements.txt
# add OPENAI_API_KEY to .env
```

## Usage

```bash
# 1. Record a workflow
python -m recorder.record --name "my_task"

# 2. Generate an SOP
python -m sop.main <experiment_name>

# 3. Execute it autonomously
python -m execute.main <experiment_name> --validate
```

See `CLAUDE.md` for full architecture and commands.
