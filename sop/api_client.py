"""LLM client for SOP generation, inline repair, and validation.

Exposes a single entry point `call_openai(messages, ...)` (kept as the name
for backwards compatibility with the many existing call sites). Depending
on `config.LLM_PROVIDER` it dispatches to either OpenAI GPT-4o or to
Anthropic Claude. Both return plain response text.

The input `messages` list uses the OpenAI-style schema:

    [
        {"role": "system", "content": "..."},
        {"role": "user", "content": [
            {"type": "text", "text": "..."},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}},
        ]},
    ]

For the Anthropic backend we convert in place — pulling the system message
out, turning each `image_url` block into `{"type": "image", "source": {...}}`.
"""

import base64
import logging
import re
import time

from config import (
    LLM_PROVIDER,
    OPENAI_API_KEY,
    ANTHROPIC_API_KEY,
    MODEL,
    CLAUDE_MODEL,
    MAX_TOKENS,
    TEMPERATURE,
    RATE_LIMIT_DELAY,
    RETRY_COUNT,
    RETRY_DELAYS,
)

logger = logging.getLogger(__name__)

_openai_client = None
_anthropic_client = None


# ---------- OpenAI path ----------

def _get_openai_client():
    global _openai_client
    if _openai_client is None:
        from openai import OpenAI
        _openai_client = OpenAI(api_key=OPENAI_API_KEY)
    return _openai_client


def _call_openai(messages, model, max_tokens, temperature):
    from openai import RateLimitError, APIError
    client = _get_openai_client()
    for attempt in range(RETRY_COUNT):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            time.sleep(RATE_LIMIT_DELAY)
            return response.choices[0].message.content
        except (RateLimitError, APIError) as e:
            delay = RETRY_DELAYS[attempt] if attempt < len(RETRY_DELAYS) else RETRY_DELAYS[-1]
            logger.warning(f"OpenAI error (attempt {attempt + 1}/{RETRY_COUNT}): {e}. Retrying in {delay}s...")
            time.sleep(delay)
    raise RuntimeError(f"OpenAI call failed after {RETRY_COUNT} retries")


# ---------- Anthropic path ----------

_DATA_URL_RE = re.compile(r"^data:(?P<media>image/[a-zA-Z0-9.+-]+);base64,(?P<data>.+)$", re.DOTALL)


def _get_anthropic_client():
    global _anthropic_client
    if _anthropic_client is None:
        import anthropic
        _anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _anthropic_client


def _openai_parts_to_anthropic(content):
    """Convert an OpenAI-style content (str or list of parts) to Anthropic parts.

    Returns a list of Anthropic content blocks. Handles text and image_url
    (data URLs). Non-image URL references are passed through as text so we
    do not silently drop them.
    """
    if isinstance(content, str):
        return [{"type": "text", "text": content}]

    out = []
    for part in content:
        ptype = part.get("type")
        if ptype == "text":
            out.append({"type": "text", "text": part.get("text", "")})
        elif ptype == "image_url":
            url = (part.get("image_url") or {}).get("url", "")
            m = _DATA_URL_RE.match(url)
            if m:
                out.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": m.group("media"),
                        "data": m.group("data"),
                    },
                })
            else:
                # Non-data URL — surface as text so the model at least sees it.
                logger.warning(f"Non-data image URL passed to Anthropic adapter: {url[:80]}...")
                out.append({"type": "text", "text": f"[image reference: {url}]"})
        else:
            logger.warning(f"Unknown content part type {ptype!r}; dropping.")
    return out


def _split_system(messages):
    """Anthropic takes `system` as a separate arg. Pull system messages out."""
    system_parts = []
    rest = []
    for msg in messages:
        if msg.get("role") == "system":
            content = msg.get("content", "")
            if isinstance(content, str):
                system_parts.append(content)
            else:
                for part in content:
                    if part.get("type") == "text":
                        system_parts.append(part.get("text", ""))
        else:
            rest.append({
                "role": msg.get("role", "user"),
                "content": _openai_parts_to_anthropic(msg.get("content", "")),
            })
    system = "\n\n".join(s for s in system_parts if s)
    return system, rest


def _call_anthropic(messages, model, max_tokens, temperature):
    import anthropic
    client = _get_anthropic_client()

    system, converted = _split_system(messages)

    kwargs = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": converted,
    }
    if system:
        kwargs["system"] = system

    for attempt in range(RETRY_COUNT):
        try:
            response = client.messages.create(**kwargs)
            time.sleep(RATE_LIMIT_DELAY)
            # Concatenate text blocks in the response
            return "".join(b.text for b in response.content if getattr(b, "type", None) == "text")
        except (anthropic.RateLimitError, anthropic.APIError) as e:
            delay = RETRY_DELAYS[attempt] if attempt < len(RETRY_DELAYS) else RETRY_DELAYS[-1]
            logger.warning(f"Anthropic error (attempt {attempt + 1}/{RETRY_COUNT}): {e}. Retrying in {delay}s...")
            time.sleep(delay)
    raise RuntimeError(f"Anthropic call failed after {RETRY_COUNT} retries")


# ---------- Public entry point ----------

def call_openai(
    messages: list[dict],
    model: str | None = None,
    max_tokens: int = MAX_TOKENS,
    temperature: float = TEMPERATURE,
) -> str:
    """Send a chat request and return the assistant's response text.

    Name is retained for backwards compatibility. Routes to OpenAI or
    Anthropic depending on `LLM_PROVIDER` in config.
    """
    if LLM_PROVIDER == "anthropic":
        return _call_anthropic(messages, model or CLAUDE_MODEL, max_tokens, temperature)
    return _call_openai(messages, model or MODEL, max_tokens, temperature)
