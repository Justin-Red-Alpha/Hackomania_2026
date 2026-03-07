"""
Step 4: Identify claims in article text and orchestrate parallel fact-checking agents.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re

import anthropic
from dotenv import load_dotenv

from app.database.db import save_sources
from app.investigation.fakeness_agent import run as run_fakeness
from app.investigation.search_agent import run as run_search
from app.investigation.source_checker import run as run_source_checker
from app.investigation.statistics_agent import run as run_statistics
from app.models.schemas import (
    Claim,
    ClaimVerdict,
    IngestionResult,
    InvestigationResult,
)

load_dotenv()
logger = logging.getLogger(__name__)

_CLAIM_EXTRACTION_SYSTEM = """\
You are a fact-checking analyst. Given a news article, extract all factual claims that can be verified.
Focus on specific, concrete claims: statistics, events, named assertions, cause-effect statements.
Ignore pure opinions, editorials, and subjective judgements.

Return a JSON array of claim objects (up to 10 most significant claims):
[
  {
    "claim_id": 1,
    "claim_summary": "Brief one-sentence summary of the claim",
    "extract": "Direct verbatim quote from the article; replace any double-quote characters in the quote with single quotes",
    "verdict": "unverified",
    "reason": "Pending investigation"
  }
]
Return ONLY the JSON array. No markdown fences, no surrounding text, no explanation."""


async def _extract_claims(client: anthropic.AsyncAnthropic, text: str) -> list[Claim]:
    """Use Claude to identify verifiable factual claims in the article text."""
    logger.debug(
        "investigator: extracting claims from article",
        extra={"stage": "claim_extraction", "text_length": len(text)},
    )
    response = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system=_CLAIM_EXTRACTION_SYSTEM,
        messages=[{"role": "user", "content": text[:16000]}],
    )
    raw = response.content[0].text.strip()
    logger.debug(
        "investigator: received claims from Claude",
        extra={"stage": "claim_extraction", "raw_preview": raw[:200]},
    )
    # Strip markdown code fences if present
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    # Extract outermost JSON array boundary
    start, end = raw.find("["), raw.rfind("]")
    if start != -1 and end > start:
        raw = raw[start : end + 1]
    # Fix unescaped parenthetical abbreviations e.g. ("HC") -> ('HC')
    raw = re.sub(r'\("([^"\n]{1,20})"\)', r"('\1')", raw)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.error(
            "investigator: failed to parse claims JSON — raw: %r",
            raw[:500],
            extra={"stage": "claim_extraction"},
        )
        return []
    claims: list[Claim] = []
    for item in data:
        try:
            try:
                verdict = ClaimVerdict(item.get("verdict", "unverified"))
            except ValueError:
                verdict = ClaimVerdict.unverified
            claims.append(
                Claim(
                    claim_id=item["claim_id"],
                    claim_summary=item["claim_summary"],
                    extract=item["extract"],
                    verdict=verdict,
                    reason=item.get("reason", ""),
                )
            )
        except Exception as e:
            logger.warning(
                "investigator: skipping malformed claim item",
                extra={"stage": "claim_extraction", "error": str(e)},
            )
    logger.debug(
        "investigator: claims extracted",
        extra={"stage": "claim_extraction", "claim_count": len(claims)},
    )
    return claims


async def run_investigation(ingestion_result: IngestionResult) -> InvestigationResult:
    """
    Main investigation entry point.

    1. Extract verifiable claims from the article using Claude.
    2. Run all four sub-agents concurrently:
       - search_agent: find and classify sources for each claim (citation-chasing)
       - fakeness_agent: detect AI-generated content likelihood via GPTZero
       - statistics_agent: verify cited statistics against authoritative sources
       - source_checker: assess publisher / author credibility
    3. Merge results into InvestigationResult.

    Args:
        ingestion_result: Output from the ingestion pipeline.

    Returns:
        InvestigationResult with enriched claims, publisher credibility, and fakeness score.
    """
    api_key = os.environ["ANTHROPIC_API_KEY"]
    client = anthropic.AsyncAnthropic(api_key=api_key)
    article = ingestion_result.content
    text = ingestion_result.text

    logger.debug(
        "investigator: starting investigation",
        extra={
            "stage": "run_investigation",
            "title": article.title,
            "publisher": article.publisher,
            "token_count": ingestion_result.token_count,
        },
    )

    # Step 1: Extract claims
    claims = await _extract_claims(client, text)
    if not claims:
        logger.warning(
            "investigator: no claims extracted from article",
            extra={"stage": "run_investigation", "title": article.title},
        )

    logger.debug(
        "investigator: launching parallel sub-agents",
        extra={"stage": "run_investigation", "claim_count": len(claims)},
    )

    # Step 2: Run all sub-agents in parallel
    enriched_claims, fakeness_score, stats_claims, publisher_credibility = await asyncio.gather(
        run_search(claims, article),
        run_fakeness(text),
        run_statistics(claims),
        run_source_checker(article),
    )

    logger.debug(
        "investigator: all sub-agents complete",
        extra={
            "stage": "run_investigation",
            "fakeness_score": fakeness_score,
            "publisher_score": publisher_credibility.score,
        },
    )

    # Step 3: Merge statistics verdicts into search-enriched claims.
    # Statistics agent has domain authority for numerical claims; its verdict takes precedence
    # when it produces a non-unverified result.
    stats_map = {c.claim_id: c for c in stats_claims}
    final_claims: list[Claim] = []
    for claim in enriched_claims:
        stats_claim = stats_map.get(claim.claim_id)
        if stats_claim and stats_claim.verdict != ClaimVerdict.unverified:
            claim = claim.model_copy(
                update={"verdict": stats_claim.verdict, "reason": stats_claim.reason}
            )
        final_claims.append(claim)

    result = InvestigationResult(
        claims=final_claims,
        publisher_credibility=publisher_credibility,
        fakeness_score=fakeness_score,
    )

    logger.debug(
        "investigator: investigation complete",
        extra={
            "stage": "run_investigation",
            "total_claims": len(final_claims),
            "fakeness_score": fakeness_score,
        },
    )
    # Persist all unique sources across claims to the database before returning
    if article.url:
        seen_urls: set[str] = set()
        all_sources = []
        for claim in final_claims:
            for src in claim.sources:
                if src.url not in seen_urls:
                    seen_urls.add(src.url)
                    all_sources.append(src)
        try:
            await save_sources(str(article.url), all_sources)
            logger.debug(
                "investigator: sources saved to database",
                extra={
                    "stage": "save_sources",
                    "url": article.url,
                    "source_count": len(all_sources),
                },
            )
        except Exception:
            logger.warning(
                "investigator: failed to save sources to database, continuing",
                extra={"stage": "save_sources", "url": article.url},
                exc_info=True,
            )
    else:
        logger.debug(
            "investigator: no URL on article, skipping source database save",
            extra={"stage": "save_sources"},
        )

    return result
