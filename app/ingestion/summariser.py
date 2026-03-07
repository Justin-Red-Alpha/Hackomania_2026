"""
Step 3: Summarise long content.
Only summarises if token_count > 4000; otherwise passes through unchanged.
original_text is never summarised.
"""

from __future__ import annotations

import logging
import os

import anthropic
from dotenv import load_dotenv

from app.ingestion.extraction_agent import _approximate_token_count
from app.models.schemas import IngestionResult

load_dotenv()

logger = logging.getLogger(__name__)

_SUMMARISE_TOKEN_THRESHOLD = 4000

_SUMMARISE_SYSTEM_PROMPT = """\
You are an expert summariser for news articles and web content.
Summarise the following article, preserving:
- All key facts, claims, and statistics
- Named entities (people, organisations, places)
- The overall narrative and conclusion
- Any quoted statements

Write in clear, neutral, third-person prose. Do not add analysis or opinions.
Return ONLY the summary with no preamble or labels.
"""


async def summarise(result: IngestionResult) -> IngestionResult:
    """
    Summarise the English text in the IngestionResult if token_count > 4000.

    original_text is never modified.

    Args:
        result: IngestionResult from the extraction step.

    Returns:
        Updated IngestionResult with summarised text and revised token_count,
        or the original result unchanged if summarisation is not needed.
    """
    if result.token_count <= _SUMMARISE_TOKEN_THRESHOLD:
        logger.debug(
            "summariser: token count within threshold, skipping summarisation",
            extra={
                "stage": "summarise",
                "token_count": result.token_count,
                "threshold": _SUMMARISE_TOKEN_THRESHOLD,
            },
        )
        return result

    logger.debug(
        "summariser: token count exceeds threshold, summarising",
        extra={
            "stage": "summarise",
            "token_count": result.token_count,
            "threshold": _SUMMARISE_TOKEN_THRESHOLD,
        },
    )

    api_key = os.environ["ANTHROPIC_API_KEY"]
    client = anthropic.AsyncAnthropic(api_key=api_key)

    response = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        system=_SUMMARISE_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": result.text}],
    )

    summarised_text = response.content[0].text.strip()
    new_token_count = _approximate_token_count(summarised_text)

    logger.debug(
        "summariser: summarisation complete",
        extra={
            "stage": "summarise",
            "original_token_count": result.token_count,
            "summarised_token_count": new_token_count,
        },
    )

    return IngestionResult(
        content=result.content,
        original_text=result.original_text,
        text=summarised_text,
        token_count=new_token_count,
    )
