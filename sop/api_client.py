import time
import logging

from openai import OpenAI, RateLimitError, APIError

from config import OPENAI_API_KEY, MODEL, MAX_TOKENS, TEMPERATURE, RATE_LIMIT_DELAY, RETRY_COUNT, RETRY_DELAYS

logger = logging.getLogger(__name__)

_client = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=OPENAI_API_KEY)
    return _client


def call_openai(
    messages: list[dict],
    model: str = MODEL,
    max_tokens: int = MAX_TOKENS,
    temperature: float = TEMPERATURE,
) -> str:
    """Call OpenAI chat completions with retry logic.

    Returns the assistant's response text.
    """
    client = _get_client()

    for attempt in range(RETRY_COUNT):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            # Rate limit delay after successful call
            time.sleep(RATE_LIMIT_DELAY)
            return response.choices[0].message.content

        except (RateLimitError, APIError) as e:
            delay = RETRY_DELAYS[attempt] if attempt < len(RETRY_DELAYS) else RETRY_DELAYS[-1]
            logger.warning(f"API error (attempt {attempt + 1}/{RETRY_COUNT}): {e}. Retrying in {delay}s...")
            time.sleep(delay)

    raise RuntimeError(f"Failed after {RETRY_COUNT} retries")
