"""
Step 4c: Verify statistics and numerical data cited in article claims.

For each claim containing a verifiable statistic, searches authoritative sources
via Tavily and asks Claude to check whether the cited numbers match.
Returns claims with updated verdicts for statistical claims.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

import anthropic
from dotenv import load_dotenv
from tavily import AsyncTavilyClient

import app.config as cfg
from app.models.schemas import Claim, ClaimVerdict

load_dotenv()
logger = logging.getLogger(__name__)

_STATS_FILTER_SYSTEM = """\
You are a fact-checking analyst. Given a claim, determine whether it contains a specific,
verifiable statistic or numerical data point (percentage, count, measurement, date range, etc.).

Return a JSON object:
{
  "has_statistic": true | false,
  "search_query": "concise query to find an authoritative source for this statistic, or null"
}
Return ONLY the JSON object."""

_STATS_VERDICT_SYSTEM = """\
You are a fact-checking analyst specialising in statistics verification.
Given a claim containing a statistic and search results from authoritative sources,
determine whether the statistic is accurate.

Return a JSON object:
{
  "verdict": "true" | "mostly_true" | "misleading" | "unverified" | "mostly_false" | "false",
  "reason": "Explanation referencing specific numbers found in the sources"
}
Use "unverified" if no authoritative data was found to confirm or deny the statistic.
Return ONLY the JSON object."""


async def _check_statistic(
    client: anthropic.AsyncAnthropic,
    tavily: AsyncTavilyClient,
    claim: Claim,
) -> Claim:
    """Check if a claim contains a statistic and verify it against authoritative sources."""
    logger.debug(
        "statistics_agent: checking claim for statistics",
        extra={"stage": "stats_filter", "claim_id": claim.claim_id},
    )

    # Step 1: Ask Claude whether the claim contains a verifiable statistic
    filter_response = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=256,
        system=_STATS_FILTER_SYSTEM,
        messages=[{"role": "user", "content": f"Claim: {claim.claim_summary}\nExtract: {claim.extract}"}],
    )
    raw = filter_response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    try:
        filter_data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(
            "statistics_agent: failed to parse stats filter JSON",
            extra={"stage": "stats_filter", "claim_id": claim.claim_id, "raw": raw},
        )
        return claim

    if not filter_data.get("has_statistic"):
        logger.debug(
            "statistics_agent: no statistic in claim, skipping",
            extra={"stage": "stats_filter", "claim_id": claim.claim_id},
        )
        return claim

    search_query = filter_data.get("search_query")
    if not search_query:
        return claim

    logger.debug(
        "statistics_agent: statistic found, searching for verification",
        extra={"stage": "stats_search", "claim_id": claim.claim_id, "query": search_query},
    )

    # Step 2: Search for authoritative sources
    try:
        search_response = await tavily.search(
            query=search_query,
            search_depth=cfg.TAVILY_SEARCH_DEPTH,
            max_results=3,
        )
        results = search_response.get("results", [])
    except Exception:
        logger.warning(
            "statistics_agent: Tavily search failed",
            extra={"stage": "stats_search", "claim_id": claim.claim_id},
            exc_info=True,
        )
        return claim

    if not results:
        return claim

    # Step 3: Ask Claude to verify the statistic against the search results
    source_snippets = "\n".join(
        f"- [{r.get('title', r['url'])}]: {r.get('content', '')[:400]}"
        for r in results
    )
    verdict_prompt = (
        f"Claim: {claim.claim_summary}\n"
        f"Extract: {claim.extract}\n\n"
        f"Search results from authoritative sources:\n{source_snippets}"
    )

    try:
        verdict_response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            system=_STATS_VERDICT_SYSTEM,
            messages=[{"role": "user", "content": verdict_prompt}],
        )
        raw_verdict = verdict_response.content[0].text.strip()
        if raw_verdict.startswith("```"):
            raw_verdict = raw_verdict.split("```")[1]
            if raw_verdict.startswith("json"):
                raw_verdict = raw_verdict[4:]
            raw_verdict = raw_verdict.strip()
        if not raw_verdict:
            raise ValueError("empty response from Claude")
        verdict_data = json.loads(raw_verdict)
        try:
            verdict = ClaimVerdict(verdict_data.get("verdict", "unverified"))
        except ValueError:
            verdict = ClaimVerdict.unverified
        reason = verdict_data.get("reason", claim.reason)
        logger.debug(
            "statistics_agent: statistic verdict",
            extra={"stage": "stats_verdict", "claim_id": claim.claim_id, "verdict": verdict.value},
        )
        return claim.model_copy(update={"verdict": verdict, "reason": reason})
    except Exception:
        logger.warning(
            "statistics_agent: verdict generation failed",
            extra={"stage": "stats_verdict", "claim_id": claim.claim_id},
            exc_info=True,
        )
        return claim


async def run(claims: list[Claim]) -> list[Claim]:
    """
    For claims containing verifiable statistics, search authoritative sources
    and update the verdict accordingly.

    Args:
        claims: List of claims from the investigator.

    Returns:
        Claims with updated verdicts for statistical claims; others unchanged.
    """
    api_key = os.environ["ANTHROPIC_API_KEY"]
    tavily_key = os.environ["TAVILY_API_KEY"]
    client = anthropic.AsyncAnthropic(api_key=api_key)
    tavily = AsyncTavilyClient(api_key=tavily_key)

    logger.debug(
        "statistics_agent: starting statistics verification",
        extra={"stage": "run", "claim_count": len(claims)},
    )
    tasks = [_check_statistic(client, tavily, claim) for claim in claims]
    updated_claims = await asyncio.gather(*tasks)
    logger.debug(
        "statistics_agent: statistics verification complete",
        extra={"stage": "run", "claim_count": len(updated_claims)},
    )
    return list(updated_claims)
