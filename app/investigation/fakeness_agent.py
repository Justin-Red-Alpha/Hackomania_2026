"""
Step 4b: Detect AI-generated content likelihood using the GPTZero API.

Returns a fakeness_score (0-100) representing probability the article is AI-generated.
Requires GPTZERO_API_KEY in .env. Returns 0 if the key is missing or the call fails.
"""

from __future__ import annotations

import logging
import os

import httpx
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

_GPTZERO_ENDPOINT = "https://api.gptzero.me/v2/predict/text"


async def run(text: str) -> int:
    """
    Call GPTZero API to detect AI-generated content.

    Args:
        text: The article text to analyse.

    Returns:
        fakeness_score: 0-100 integer. Higher = more likely AI-generated.
        Returns 0 if GPTZERO_API_KEY is missing or the API call fails.
    """
    api_key = os.environ.get("GPTZERO_API_KEY", "")
    if not api_key:
        logger.warning(
            "fakeness_agent: GPTZERO_API_KEY not set, skipping AI detection",
            extra={"stage": "fakeness"},
        )
        return 0

    logger.debug(
        "fakeness_agent: calling GPTZero API",
        extra={"stage": "fakeness", "text_length": len(text)},
    )

    payload = {"document": text[:50000]}
    headers = {
        "x-api-key": api_key,
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(_GPTZERO_ENDPOINT, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()

        documents = data.get("documents", [])
        if not documents:
            logger.warning(
                "fakeness_agent: GPTZero returned no documents",
                extra={"stage": "fakeness", "response_keys": list(data.keys())},
            )
            return 0

        prob = documents[0].get("completely_generated_prob", 0.0)
        score = int(round(prob * 100))

        logger.debug(
            "fakeness_agent: GPTZero result",
            extra={
                "stage": "fakeness",
                "completely_generated_prob": prob,
                "fakeness_score": score,
            },
        )
        return score

    except httpx.HTTPStatusError as e:
        logger.warning(
            "fakeness_agent: GPTZero API returned error status",
            extra={"stage": "fakeness", "status_code": e.response.status_code},
            exc_info=True,
        )
        return 0
    except Exception:
        logger.warning(
            "fakeness_agent: GPTZero call failed",
            extra={"stage": "fakeness"},
            exc_info=True,
        )
        return 0
