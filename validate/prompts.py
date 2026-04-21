"""Prompt template for post-execution validation."""

VALIDATION_PROMPT = """\
You are a task completion validator. You are given:
1. A task description (the intent/goal)
2. An SOP (Standard Operating Procedure) that was executed
3. The execution history showing all actions taken and their outcomes
4. The final screenshot showing the current state of the screen

Your job is to determine whether the task was successfully completed.

## Task Description
{intent}

## SOP That Was Executed
{sop_text}

## Execution History
{execution_summary}
{stuck_info}
## Instructions

Analyze the final screenshot and the execution history. Determine whether the original task/intent has been fulfilled.

Consider:
- Does the final screen state reflect successful completion of the task?
- Were all critical SOP steps executed without errors?
- Is there evidence of the expected outcome (e.g., a confirmation message, the correct data displayed, the correct application state)?
- If the agent got stuck on a particular step, identify WHY it got stuck (element not found, wrong app state, ambiguous instruction, unexpected dialog, etc.)

Respond with a JSON object (no markdown fences):
{{
  "thinking": "<step-by-step reasoning about whether the task was completed, examining the screenshot and execution history>",
  "was_completed": <true or false>,
  "failed_step": <integer SOP step number where the agent failed or got stuck, or null if completed successfully>,
  "failure_reason": "<concrete reason why this step failed: element not found, wrong app state, ambiguous instruction, etc. Null if completed successfully>"
}}
"""
