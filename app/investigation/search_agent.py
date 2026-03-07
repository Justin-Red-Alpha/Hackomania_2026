"""
Step 4a: Find corroborating / contradicting sources for each claim.

Implements the bounded citation-chasing loop documented in investigation/CLAUDE.md.
Always searches factually.gov.sg first; archives source HTML pages to S3 (best-effort).

After chasing all citations for a claim:
- Deduplicates sources by URL; logs every removed duplicate for audit/explainability.
- Retries up to MAX_SEARCH_RETRIES times with Claude-generated alternative queries if
  fewer than MIN_SOURCES_PER_CLAIM unique sources remain after deduplication.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from datetime import date

import aioboto3
import anthropic
from dotenv import load_dotenv
from tavily import AsyncTavilyClient

import app.config as cfg
from app.models.schemas import Claim, ClaimSource, ClaimVerdict, ContentMetadata

load_dotenv()
logger = logging.getLogger(__name__)

_CLASSIFY_SYSTEM = """\
You are a source classification assistant for a fact-checking system.
Given a claim and extracted text from a web page, classify the source:

1. "primary"         - Provides its OWN original data, research, official statement, or analysis
                       that directly relates to the claim. Does NOT merely relay another source.
2. "secondary_cited" - Relays the claim AND explicitly cites a specific traceable URL or named source.
3. "mention_only"    - Mentions the claim with no supporting evidence and no traceable citation.

Return a JSON object:
{
  "classification": "primary" | "secondary_cited" | "mention_only",
  "cited_url": "https://..." or null,
  "source_name": "Publication or site name",
  "source_type": "news" | "government" | "academic" | "fact_checker" | "other",
  "is_independent": true | false
}
Return ONLY the JSON object."""

_VERDICT_SYSTEM = """\
You are a fact-checking analyst. Given a claim and primary source evidence, determine whether
the claim is supported, contradicted, or unverifiable.

Return a JSON object:
{
  "verdict": "true" | "mostly_true" | "misleading" | "unverified" | "mostly_false" | "false",
  "reason": "Concise explanation referencing specific source evidence"
}
Return ONLY the JSON object."""

_ALT_QUERY_SYSTEM = """\
You are a search query specialist. Given a factual claim that needs verification and a retry
attempt number, generate an alternative search query that may surface different sources.
Vary the angle, keywords, or framing from the original claim. Be concise and factual.
Return ONLY the search query string with no explanation or punctuation."""

_GOVT_DOMAINS = (
    ".gov.sg", ".gov.uk", ".gov.au", ".gov.us", ".gov", ".gc.ca",
    "who.int", "un.org", "worldbank.org", "imf.org",
)


async def _upload_source_to_s3(html_content: str, source_url: str) -> str | None:
    """Archive source text to S3. Returns S3 URL or None if upload fails."""
    try:
        bucket = os.environ.get("S3_BUCKET_NAME", "")
        region = os.environ.get("S3_REGION", "")
        if not bucket or not region:
            return None
        today = date.today().isoformat()
        key = f"sources/{today}/{uuid.uuid4()}.html"
        session = aioboto3.Session(
            aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID", ""),
            aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY", ""),
            region_name=region,
        )
        async with session.client("s3") as s3:
            await s3.put_object(
                Bucket=bucket,
                Key=key,
                Body=html_content.encode("utf-8"),
                ContentType="text/html",
            )
        s3_url = f"https://{bucket}.s3.{region}.amazonaws.com/{key}"
        logger.debug(
            "search_agent: archived source to S3",
            extra={"stage": "s3_upload", "s3_url": s3_url, "source_url": source_url},
        )
        return s3_url
    except Exception:
        logger.warning(
            "search_agent: S3 upload failed for source",
            extra={"stage": "s3_upload", "source_url": source_url},
            exc_info=True,
        )
        return None


async def _classify_source(
    client: anthropic.AsyncAnthropic,
    claim_summary: str,
    extracted_text: str,
    source_url: str,
) -> dict:
    """Call Claude to classify a source as primary, secondary_cited, or mention_only."""
    logger.debug(
        "search_agent: classifying source",
        extra={"stage": "classify_source", "source_url": source_url},
    )
    prompt = (
        f"Claim: {claim_summary}\n\n"
        f"Source URL: {source_url}\n\n"
        f"Extracted text:\n{extracted_text[:4000]}"
    )
    response = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        system=_CLASSIFY_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = response.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        raw = raw[start : end + 1]
    raw = re.sub(r'\("([^"\n]{1,20})"\)', r"('\1')", raw)
    try:
        result = json.loads(raw)
        logger.debug(
            "search_agent: source classified",
            extra={
                "stage": "classify_source",
                "source_url": source_url,
                "classification": result.get("classification"),
            },
        )
        return result
    except json.JSONDecodeError:
        logger.warning(
            "search_agent: failed to parse classification JSON — raw: %r",
            raw[:400],
            extra={"stage": "classify_source", "source_url": source_url},
        )
        return {
            "classification": "mention_only",
            "cited_url": None,
            "source_name": source_url,
            "source_type": "other",
            "is_independent": True,
        }


async def _generate_alt_query(
    client: anthropic.AsyncAnthropic,
    claim_summary: str,
    attempt: int,
) -> str:
    """Use Claude to generate an alternative Tavily search query for a retry attempt."""
    try:
        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=128,
            system=_ALT_QUERY_SYSTEM,
            messages=[{
                "role": "user",
                "content": (
                    f"Claim: {claim_summary}\n"
                    f"Retry attempt: {attempt}\n"
                    f"Generate an alternative search query:"
                ),
            }],
        )
        alt_query = response.content[0].text.strip()
        logger.debug(
            "search_agent: alternative query generated",
            extra={"stage": "alt_query", "attempt": attempt, "alt_query": alt_query},
        )
        return alt_query
    except Exception:
        logger.warning(
            "search_agent: failed to generate alternative query, using original claim",
            extra={"stage": "alt_query", "attempt": attempt},
            exc_info=True,
        )
        return claim_summary

async def _process_url(
    client: anthropic.AsyncAnthropic,
    claim_summary: str,
    url: str,
    hop_depth: int,
) -> list[ClaimSource]:
    """
    Extract content from a URL, classify it, and optionally follow citations.

    Returns a list of ClaimSource objects (may include sources found via citation chasing).
    Discards mention-only sources. Archives kept sources to S3 (best-effort).
    """
    import trafilatura

    if hop_depth > cfg.MAX_HOP_DEPTH:
        return []

    logger.debug(
        "search_agent: extracting URL",
        extra={"stage": "trafilatura_extract", "url": url, "hop_depth": hop_depth},
    )

    # Straits Times is paywalled - use as credibility signal only, skip extraction
    if "straitstimes.com" in url:
        logger.debug(
            "search_agent: Straits Times URL - skipping extraction (paywalled)",
            extra={"stage": "trafilatura_extract", "url": url},
        )
        return [
            ClaimSource(
                source_id=str(uuid.uuid4()),
                name="The Straits Times",
                url=url,
                type="news",
                is_independent=True,
                is_primary_source=False,
                hop_depth=hop_depth,
                s3_url=None,
                extracted_text=None,
            )
        ]

    raw_html: str | None = await asyncio.to_thread(trafilatura.fetch_url, url)
    if not raw_html:
        logger.debug(
            "search_agent: trafilatura could not fetch URL",
            extra={"stage": "trafilatura_extract", "url": url},
        )
        return []
    extracted_text: str = (
        await asyncio.to_thread(
            trafilatura.extract, raw_html, include_comments=False, include_tables=True
        )
    ) or ""
    if not extracted_text:
        logger.debug(
            "search_agent: trafilatura extracted no content",
            extra={"stage": "trafilatura_extract", "url": url},
        )
        return []

    classification = await _classify_source(client, claim_summary, extracted_text, url)
    cls = classification.get("classification", "mention_only")

    if cls == "mention_only":
        logger.debug(
            "search_agent: discarding mention-only source",
            extra={"stage": "classify_source", "url": url},
        )
        return []

    s3_url = await _upload_source_to_s3(extracted_text, url)
    source_id = str(uuid.uuid4())
    is_primary = cls == "primary"

    source = ClaimSource(
        source_id=source_id,
        name=classification.get("source_name"),
        url=url,
        type=classification.get("source_type"),
        is_independent=classification.get("is_independent", True),
        is_primary_source=is_primary,
        hop_depth=hop_depth,
        s3_url=s3_url,
        extracted_text=extracted_text,
    )

    if is_primary:
        logger.debug(
            "search_agent: accepted primary source",
            extra={"stage": "classify_source", "url": url, "hop_depth": hop_depth},
        )
        return [source]

    # secondary_cited: keep this source and follow the cited URL if within hop limit
    sources: list[ClaimSource] = [source]
    cited_url = classification.get("cited_url")
    if cited_url and hop_depth < cfg.MAX_HOP_DEPTH:
        logger.debug(
            "search_agent: following citation",
            extra={
                "stage": "citation_chase",
                "from_url": url,
                "cited_url": cited_url,
                "new_hop_depth": hop_depth + 1,
            },
        )
        cited_sources = await _process_url(
            client, claim_summary, cited_url, hop_depth + 1
        )
        sources.extend(cited_sources)
    return sources

async def _investigate_claim(
    client: anthropic.AsyncAnthropic,
    tavily: AsyncTavilyClient,
    claim: Claim,
    article: ContentMetadata,
) -> Claim:
    """Search for sources, chase citations, deduplicate, retry if needed, and produce a verdict."""
    logger.debug(
        "search_agent: investigating claim",
        extra={
            "stage": "investigate_claim",
            "claim_id": claim.claim_id,
            "claim_summary": claim.claim_summary,
        },
    )

    # Always search Factually first for Singapore government fact-checks
    factually_sources: list[ClaimSource] = []
    try:
        factually_resp = await tavily.search(
            query=f"site:factually.gov.sg {claim.claim_summary}",
            search_depth="basic",
            max_results=3,
        )
        factually_urls = [r["url"] for r in factually_resp.get("results", [])]
        if factually_urls:
            logger.debug(
                "search_agent: Factually results found",
                extra={"stage": "factually_search", "claim_id": claim.claim_id, "count": len(factually_urls)},
            )
            factually_tasks = [
                _process_url(client, claim.claim_summary, url, 0)
                for url in factually_urls
            ]
            for result_list in await asyncio.gather(*factually_tasks):
                factually_sources.extend(result_list)
    except Exception:
        logger.warning(
            "search_agent: Factually search failed",
            extra={"stage": "factually_search", "claim_id": claim.claim_id},
            exc_info=True,
        )

    # General search
    query = claim.claim_summary
    if cfg.PRIORITISE_LOCAL and cfg.COUNTRY:
        query = f"{query} {cfg.COUNTRY}"
    result_urls: list[str] = []
    try:
        search_resp = await tavily.search(
            query=query,
            search_depth=cfg.TAVILY_SEARCH_DEPTH,
            max_results=cfg.TAVILY_MAX_RESULTS,
        )
        result_urls = [r["url"] for r in search_resp.get("results", [])]
        logger.debug(
            "search_agent: Tavily search complete",
            extra={"stage": "tavily_search", "claim_id": claim.claim_id, "result_count": len(result_urls)},
        )
    except Exception:
        logger.warning(
            "search_agent: Tavily search failed",
            extra={"stage": "tavily_search", "claim_id": claim.claim_id},
            exc_info=True,
        )

    # Process all URLs in parallel at hop_depth=0
    url_tasks = [
        _process_url(client, claim.claim_summary, url, 0)
        for url in result_urls
    ]
    all_sources: list[ClaimSource] = list(factually_sources)
    for result_list in await asyncio.gather(*url_tasks):
        all_sources.extend(result_list)

    # Deduplicate by URL; log each removed duplicate for audit and explainability
    seen_urls: set[str] = set()
    unique_sources: list[ClaimSource] = []
    duplicate_count = 0
    for src in all_sources:
        if src.url not in seen_urls:
            seen_urls.add(src.url)
            unique_sources.append(src)
        else:
            duplicate_count += 1
            logger.debug(
                "search_agent: duplicate source removed",
                extra={
                    "stage": "deduplication",
                    "claim_id": claim.claim_id,
                    "duplicate_url": src.url,
                },
            )
    if duplicate_count > 0:
        logger.debug(
            "search_agent: deduplication complete",
            extra={
                "stage": "deduplication",
                "claim_id": claim.claim_id,
                "removed_count": duplicate_count,
                "remaining_count": len(unique_sources),
            },
        )

    # Retry with Claude-generated alternative queries if insufficient unique sources found
    for retry_num in range(1, cfg.MAX_SEARCH_RETRIES + 1):
        if len(unique_sources) >= cfg.MIN_SOURCES_PER_CLAIM:
            break
        logger.debug(
            "search_agent: insufficient sources after deduplication, retrying",
            extra={
                "stage": "retry_search",
                "claim_id": claim.claim_id,
                "sources_found": len(unique_sources),
                "min_required": cfg.MIN_SOURCES_PER_CLAIM,
                "retry_attempt": retry_num,
                "max_retries": cfg.MAX_SEARCH_RETRIES,
            },
        )
        alt_query = await _generate_alt_query(client, claim.claim_summary, retry_num)
        logger.debug(
            "search_agent: retry with alternative query",
            extra={
                "stage": "retry_search",
                "claim_id": claim.claim_id,
                "retry_attempt": retry_num,
                "alt_query": alt_query,
            },
        )
        try:
            retry_resp = await tavily.search(
                query=alt_query,
                search_depth=cfg.TAVILY_SEARCH_DEPTH,
                max_results=cfg.TAVILY_MAX_RESULTS,
            )
            retry_urls = [
                r["url"]
                for r in retry_resp.get("results", [])
                if r["url"] not in seen_urls
            ]
        except Exception:
            logger.warning(
                "search_agent: retry search failed",
                extra={"stage": "retry_search", "claim_id": claim.claim_id, "retry_attempt": retry_num},
                exc_info=True,
            )
            break
        retry_tasks = [
            _process_url(client, claim.claim_summary, url, 0)
            for url in retry_urls
        ]
        for result_list in await asyncio.gather(*retry_tasks):
            for src in result_list:
                if src.url not in seen_urls:
                    seen_urls.add(src.url)
                    unique_sources.append(src)
                else:
                    logger.debug(
                        "search_agent: duplicate source removed during retry",
                        extra={
                            "stage": "deduplication",
                            "claim_id": claim.claim_id,
                            "duplicate_url": src.url,
                            "retry_attempt": retry_num,
                        },
                    )
        logger.debug(
            "search_agent: retry search complete",
            extra={
                "stage": "retry_search",
                "claim_id": claim.claim_id,
                "retry_attempt": retry_num,
                "total_sources": len(unique_sources),
            },
        )

    # Determine government-source-only flag (recalculated after all retries)
    gov_only = bool(unique_sources) and all(
        any(d in src.url.lower() for d in _GOVT_DOMAINS) for src in unique_sources
    )

    # Generate verdict via Claude using primary sources
    verdict = claim.verdict
    reason = claim.reason
    primary_sources = [s for s in unique_sources if s.is_primary_source]
    if primary_sources:
        source_summaries = "\n".join(
            f"- [{s.name or s.url}] ({s.type or 'unknown'}, hop={s.hop_depth}):\n  {(s.extracted_text or '')[:600]}"
            for s in primary_sources
        )
        raw_v = ""
        try:
            verdict_prompt = (
                f"Claim: {claim.claim_summary}\n\nArticle extract: {claim.extract}"
                f"\n\nPrimary source evidence:\n{source_summaries}"
            )
            verdict_resp = await client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=512,
                system=_VERDICT_SYSTEM,
                messages=[{"role": "user", "content": verdict_prompt}],
            )
            raw_v = verdict_resp.content[0].text.strip()
            raw_v = re.sub(r"^```(?:json)?\s*", "", raw_v)
            raw_v = re.sub(r"\s*```$", "", raw_v)
            start, end = raw_v.find("{"), raw_v.rfind("}")
            if start != -1 and end > start:
                raw_v = raw_v[start : end + 1]
            raw_v = re.sub(r'\("([^"\n]{1,20})"\)', r"('\1')", raw_v)
            verdict_data = json.loads(raw_v)
            try:
                verdict = ClaimVerdict(verdict_data.get("verdict", "unverified"))
            except ValueError:
                verdict = ClaimVerdict.unverified
            reason = verdict_data.get("reason", "")
            logger.debug(
                "search_agent: verdict determined",
                extra={"stage": "verdict", "claim_id": claim.claim_id, "verdict": verdict.value},
            )
        except Exception:
            logger.warning(
                "search_agent: verdict generation failed claim_id=%s — raw_v: %r",
                claim.claim_id,
                raw_v[:400],
                extra={"stage": "verdict", "claim_id": claim.claim_id},
                exc_info=True,
            )

    return Claim(
        claim_id=claim.claim_id,
        claim_summary=claim.claim_summary,
        extract=claim.extract,
        verdict=verdict,
        reason=reason,
        government_source_only=gov_only,
        sources=unique_sources,
    )


async def run(claims: list[Claim], article: ContentMetadata) -> list[Claim]:
    """
    For each claim, search for and classify sources using the bounded citation-chasing loop.
    Deduplicates results and retries with alternative queries if sources are insufficient.

    Args:
        claims: List of claims extracted by the investigator.
        article: Content metadata from ingestion (used for localisation context).

    Returns:
        Claims enriched with sources and initial verdicts.
    """
    api_key = os.environ["ANTHROPIC_API_KEY"]
    tavily_key = os.environ["TAVILY_API_KEY"]
    client = anthropic.AsyncAnthropic(api_key=api_key)
    tavily = AsyncTavilyClient(api_key=tavily_key)

    logger.debug(
        "search_agent: starting source search",
        extra={"stage": "run", "claim_count": len(claims)},
    )
    tasks = [_investigate_claim(client, tavily, claim, article) for claim in claims]
    enriched = await asyncio.gather(*tasks)
    logger.debug(
        "search_agent: source search complete",
        extra={"stage": "run", "claim_count": len(enriched)},
    )
    return list(enriched)
