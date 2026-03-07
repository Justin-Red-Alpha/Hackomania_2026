"""
Step 4d: Check author / publisher credibility and timestamp plausibility.

Searches for the publisher via Tavily, then uses Claude to produce a
PublisherCredibility assessment based on known ratings, bias, and issues.
"""

from __future__ import annotations

import json
import logging
import os

import anthropic
from dotenv import load_dotenv
from tavily import AsyncTavilyClient

from app.models.schemas import ContentMetadata, PoliticalBias, PublisherCredibility, PublisherRating

load_dotenv()
logger = logging.getLogger(__name__)

_SOURCE_CHECK_SYSTEM = """\
You are a media credibility analyst. Given information about a news publisher or author,
assess their credibility based on:
- Known track record of accuracy and corrections history
- Fact-checker ratings from Media Bias/Fact Check, AllSides, or similar
- Known political or commercial bias
- Independence from government or corporate influence
- History of publishing misinformation or retractions

Return a JSON object:
{
  "score": <0-100 integer; 100 = most credible>,
  "rating": "highly_credible" | "credible" | "mixed" | "low_credibility" | "not_credible",
  "summary": "2-3 sentence assessment of the publisher",
  "bias": "far_left" | "left" | "center_left" | "center" | "center_right" | "right" | "far_right" | "unknown",
  "known_issues": ["list of known problems, controversies, or fact-checker flags"],
  "fact_checker_ratings": ["list of ratings from known fact-checkers if available"]
}
Return ONLY the JSON object."""


async def run(article: ContentMetadata) -> PublisherCredibility:
    """
    Assess the credibility of the article publisher and author.

    Searches Tavily for publisher credibility information, then uses Claude to
    produce a structured PublisherCredibility rating based on the findings.

    Args:
        article: Content metadata including publisher and author.

    Returns:
        PublisherCredibility with score, rating, bias, and known issues.
        Falls back to score=50 / mixed rating if assessment fails.
    """
    api_key = os.environ["ANTHROPIC_API_KEY"]
    tavily_key = os.environ["TAVILY_API_KEY"]
    client = anthropic.AsyncAnthropic(api_key=api_key)
    tavily = AsyncTavilyClient(api_key=tavily_key)

    publisher = article.publisher or "Unknown publisher"
    author = article.author

    logger.debug(
        "source_checker: checking publisher credibility",
        extra={"stage": "run", "publisher": publisher, "author": author},
    )

    # Search for credibility information about the publisher
    search_context = ""
    try:
        query = f"{publisher} media credibility fact-checker rating bias reliability"
        search_resp = await tavily.search(
            query=query,
            search_depth="basic",
            max_results=3,
        )
        results = search_resp.get("results", [])
        if results:
            search_context = "\n".join(
                f"- {r.get('title', '')}: {r.get('content', '')[:300]}"
                for r in results
            )
            logger.debug(
                "source_checker: retrieved publisher context",
                extra={"stage": "tavily_search", "publisher": publisher, "result_count": len(results)},
            )
    except Exception:
        logger.warning(
            "source_checker: Tavily search failed, proceeding without search context",
            extra={"stage": "tavily_search", "publisher": publisher},
            exc_info=True,
        )

    # Build prompt for Claude
    prompt_parts = [f"Publisher: {publisher}"]
    if author:
        prompt_parts.append(f"Author: {author}")
    if article.url:
        prompt_parts.append(f"Article URL: {article.url}")
    if article.date:
        prompt_parts.append(f"Publication date: {article.date}")
    if search_context:
        prompt_parts.append(f"\nSearch results about this publisher:\n{search_context}")
    prompt = "\n".join(prompt_parts)

    logger.debug(
        "source_checker: calling Claude for credibility assessment",
        extra={"stage": "credibility_assessment", "publisher": publisher},
    )

    try:
        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=_SOURCE_CHECK_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        data = json.loads(raw)

        try:
            rating = PublisherRating(data.get("rating", "mixed"))
        except ValueError:
            rating = PublisherRating.mixed

        try:
            bias = PoliticalBias(data.get("bias", "unknown"))
        except ValueError:
            bias = PoliticalBias.unknown

        score = max(0, min(100, int(data.get("score", 50))))
        credibility = PublisherCredibility(
            score=score,
            rating=rating,
            summary=data.get("summary"),
            bias=bias,
            known_issues=data.get("known_issues", []),
            fact_checker_ratings=data.get("fact_checker_ratings", []),
        )
        logger.debug(
            "source_checker: credibility assessment complete",
            extra={"stage": "run", "publisher": publisher, "score": score, "rating": rating.value},
        )
        return credibility

    except Exception:
        logger.warning(
            "source_checker: credibility assessment failed, returning default",
            extra={"stage": "run", "publisher": publisher},
            exc_info=True,
        )
        return PublisherCredibility(
            score=50,
            rating=PublisherRating.mixed,
            summary=f"Unable to assess credibility for {publisher}.",
            bias=PoliticalBias.unknown,
            known_issues=[],
            fact_checker_ratings=[],
        )
