"""Anthropic Claude Computer Use API client."""

import logging
import time

import anthropic

from web.execute.config import (
    ANTHROPIC_API_KEY,
    MODEL,
    MAX_TOKENS,
    BROWSER_WIDTH,
    BROWSER_HEIGHT,
    RETRY_COUNT,
    RETRY_DELAYS,
)

logger = logging.getLogger(__name__)

# Map models to the correct Computer Use tool version and beta flag
_TOOL_V2_MODELS = {"claude-opus-4-6", "claude-sonnet-4-6", "claude-opus-4-5"}


def _get_tool_version(model: str) -> tuple[str, str]:
    """Return (tool_type, beta_flag) for the given model."""
    for prefix in _TOOL_V2_MODELS:
        if prefix in model:
            return "computer_20251124", "computer-use-2025-11-24"
    return "computer_20250124", "computer-use-2025-01-24"


def _build_tools(model: str) -> tuple[list[dict], str]:
    """Build tools list and beta flag for the given model."""
    tool_type, beta_flag = _get_tool_version(model)
    tools = [
        {
            "type": tool_type,
            "name": "computer",
            "display_width_px": BROWSER_WIDTH,
            "display_height_px": BROWSER_HEIGHT,
        },
    ]
    return tools, beta_flag


_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


def call_claude(
    messages: list[dict],
    system: str = "",
    model: str | None = None,
) -> anthropic.types.beta.BetaMessage:
    """Call Claude Computer Use API with retry logic.

    Args:
        messages: Conversation messages.
        system: System prompt.
        model: Model override (defaults to config.MODEL).

    Returns:
        Full BetaMessage response object.
    """
    model = model or MODEL
    client = _get_client()
    tools, beta_flag = _build_tools(model)

    kwargs = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "tools": tools,
        "messages": messages,
        "betas": [beta_flag],
    }
    if system:
        kwargs["system"] = system

    last_error = None
    for attempt in range(RETRY_COUNT + 1):
        try:
            response = client.beta.messages.create(**kwargs)
            return response
        except (anthropic.RateLimitError, anthropic.APIError) as e:
            last_error = e
            if attempt < RETRY_COUNT:
                wait = RETRY_DELAYS[attempt] if attempt < len(RETRY_DELAYS) else RETRY_DELAYS[-1]
                logger.warning(f"API error (attempt {attempt + 1}/{RETRY_COUNT + 1}): {e}. Retrying in {wait}s...")
                time.sleep(wait)
            else:
                logger.error(f"API error after {RETRY_COUNT + 1} attempts: {e}")
                raise

    raise last_error  # unreachable, but satisfies type checkers
