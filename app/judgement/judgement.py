"""
Step 5 – Judgement

Aggregates InvestigationResult + IngestionResult into a final JudgementResult,
including per-claim evidence extraction via Claude and weighted credibility scoring.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

import anthropic

from app.config import (
    CLAIMS_BLEND_WEIGHT,
    CONFLICT_GAP_THRESHOLD,
    FAKENESS_PENALTY_MULTIPLIER,
    FAKENESS_THRESHOLD,
    GOVERNMENT_ONLY_BOOST,
    HOP_DEPTH_DECAY,
    NET_CONFIDENCE_MAX,
    NET_CONFIDENCE_MIN,
    NET_CONFIDENCE_MULTIPLIERS,
    PRIMARY_SOURCE_MULTIPLIER,
    PUBLISHER_BLEND_WEIGHT,
    SECONDARY_SOURCE_MULTIPLIER,
    VERDICT_BASE_WEIGHTS,
)
from app.models.schemas import (
    ClaimEvidence,
    ClaimSource,
    ClaimVerdict,
    ContentCredibility,
    ContentRating,
    IngestionResult,
    InvestigationResult,
    JudgedClaim,
    JudgementResult,
    WritingQuality,
)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def score_to_rating(score: float) -> ContentRating:
    """Map a 0-100 credibility score to a ContentRating enum value."""
    if score >= 80:
        return ContentRating.credible
    if score >= 60:
        return ContentRating.mostly_credible
    if score >= 40:
        return ContentRating.mixed
    if score >= 20:
        return ContentRating.low_credibility
    return ContentRating.not_credible


def score_to_verdict(score: float) -> ClaimVerdict:
    """Map a 0-100 per-claim score to a ClaimVerdict enum value."""
    if score >= 80:
        return ClaimVerdict.true
    if score >= 60:
        return ClaimVerdict.mostly_true
    if score >= 45:
        return ClaimVerdict.unverified
    if score >= 30:
        return ClaimVerdict.misleading
    if score >= 15:
        return ClaimVerdict.mostly_false
    return ClaimVerdict.false


def deduplicate_sources(sources: list[ClaimSource]) -> list[ClaimSource]:
    """
    Silently remove syndicated sources that share the same extracted_text fingerprint.
    Fingerprint = first 300 characters of extracted_text (stripped).
    Sources without extracted_text are never considered duplicates of each other.
    """
    seen: set[str] = set()
    unique: list[ClaimSource] = []
    for src in sources:
        if src.extracted_text:
            fp = src.extracted_text.strip()[:300]
            if fp in seen:
                continue
            seen.add(fp)
        unique.append(src)
    return unique


def source_quality_weight(source: ClaimSource) -> float:
    """
    Returns the combined quality × hop-decay multiplier for a source.
    Does NOT include verdict weight or direction — those are applied separately.
    """
    quality = (
        PRIMARY_SOURCE_MULTIPLIER
        if source.is_primary_source
        else SECONDARY_SOURCE_MULTIPLIER
    )
    decay = HOP_DEPTH_DECAY ** source.hop_depth
    return quality * decay


def net_confidence_multiplier(net: int) -> float:
    """Clamp net to [NET_CONFIDENCE_MIN, NET_CONFIDENCE_MAX] then look up multiplier."""
    clamped = max(NET_CONFIDENCE_MIN, min(NET_CONFIDENCE_MAX, net))
    return NET_CONFIDENCE_MULTIPLIERS[clamped]


# ---------------------------------------------------------------------------
# Claude-powered helpers
# ---------------------------------------------------------------------------

async def identify_evidence(
    client: anthropic.AsyncAnthropic,
    claim_summary: str,
    source: ClaimSource,
) -> Optional[ClaimEvidence]:
    """
    Ask Claude to locate the most relevant verbatim snippet from source.extracted_text
    and determine whether it supports or contradicts the claim.

    Returns None if the source has no extracted_text.
    """
    if not source.extracted_text:
        return None

    hop_note = (
        f" (hop_depth={source.hop_depth}, treat with reduced confidence)"
        if source.hop_depth > 0
        else ""
    )

    prompt = f"""You are a fact-checking assistant. Given a claim and source text, extract the most relevant verbatim snippet and determine if it supports or contradicts the claim.

CLAIM: {claim_summary}

SOURCE NAME: {source.name or source.url}
SOURCE URL: {source.url}
HOP DEPTH: {source.hop_depth}{hop_note}

SOURCE TEXT:
{source.extracted_text[:8000]}

Respond with a JSON object only. No markdown, no code fences, no surrounding text.
Escape any double-quote characters inside string values with \".
{{
  "snippet": "<verbatim excerpt from the source text; replace any double-quote characters in the excerpt with single quotes>",
  "is_relevant": <true if the source contains data that can confirm or deny the claim; false if the source is unreadable, covers a different time period, or simply cannot confirm or deny>,
  "supports_claim": <true if the snippet supports the claim, false if it contradicts; only meaningful when is_relevant is true>,
  "judgement_reason": "<brief explanation of why this snippet affects the verdict, or why the source is not relevant; mention hop_depth weakness if hop_depth > 1>"
}}"""

    response = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()
    # Strip markdown code fences if present
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    # Extract outermost JSON object in case Claude added surrounding text
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        raw = raw[start : end + 1]
    # Fix unescaped parenthetical abbreviations e.g. ("HC") -> ('HC')
    raw = re.sub(r'\("([^"\n]{1,20})"\)', r"('\1')", raw)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(
            "judgement: failed to parse evidence JSON for source %s — raw: %r — skipping",
            source.url,
            raw[:400],
            extra={"stage": "identify_evidence"},
        )
        return None
    source_id = source.source_id or source.url
    is_relevant = bool(data.get("is_relevant", True))
    return ClaimEvidence(
        source_id=source_id,
        source_name=source.name or source.url,
        source_url=source.url,
        snippet=data["snippet"],
        is_relevant=is_relevant,
        supports_claim=bool(data["supports_claim"]) if is_relevant else False,
        judgement_reason=data.get("judgement_reason"),
    )


async def assess_writing_quality(
    client: anthropic.AsyncAnthropic,
    body: str,
) -> WritingQuality:
    """Ask Claude to assess the writing quality signals of the article body."""
    prompt = f"""Analyse the following article for writing quality signals. Respond with a JSON object only (no markdown):
{{
  "sensationalism": <true if the article uses sensationalist headlines or language>,
  "named_sources": <true if at least one named human source is cited>,
  "anonymous_sources": <true if any sources are anonymous or unnamed>,
  "emotional_language": <true if the article uses emotionally charged language>,
  "hedging_language": <true if the article uses hedging phrases like "allegedly", "reportedly", "may">
}}

ARTICLE:
{body[:6000]}"""

    response = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=256,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        raw = raw[start : end + 1]

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(
            "judgement: failed to parse writing quality JSON — returning defaults",
            extra={"stage": "assess_writing_quality", "raw": raw[:200]},
        )
        return WritingQuality()
    return WritingQuality(
        sensationalism=data.get("sensationalism"),
        named_sources=data.get("named_sources"),
        anonymous_sources=data.get("anonymous_sources"),
        emotional_language=data.get("emotional_language"),
        hedging_language=data.get("hedging_language"),
    )


# ---------------------------------------------------------------------------
# Per-claim scoring
# ---------------------------------------------------------------------------

async def judge_claim(
    client: anthropic.AsyncAnthropic,
    claim,  # app.models.schemas.Claim
) -> tuple[JudgedClaim, float]:
    """
    Evaluate a single claim end-to-end.

    Returns (JudgedClaim, per_claim_score_0_100).
    """
    # 1. Deduplicate sources
    unique_sources = deduplicate_sources(claim.sources)

    # 2. Extract evidence for each source that has text — all in parallel
    paywalled_count = sum(1 for s in unique_sources if s.extracted_text is None)
    sources_with_text = [s for s in unique_sources if s.extracted_text is not None]

    evidence_results = await asyncio.gather(
        *[identify_evidence(client, claim.claim_summary, s) for s in sources_with_text]
    )
    evidence_list = [ev for ev in evidence_results if ev is not None]

    # 3. Build a lookup from source_url → (source, evidence) for scoring
    evidence_by_url: dict[str, ClaimEvidence] = {
        ev.source_url: ev for ev in evidence_list
    }

    # 4. Score each source
    raw_sum = 0.0
    max_possible_sum = 0.0

    for source in unique_sources:
        if source.extracted_text is None:
            continue  # skip paywalled

        ev = evidence_by_url.get(source.url)
        if ev is None or not ev.is_relevant:
            continue  # skip irrelevant (source could not confirm or deny)

        base_weight = VERDICT_BASE_WEIGHTS.get(claim.verdict.value, 0.0)
        direction = 1.0 if ev.supports_claim else -1.0
        quality_decay = source_quality_weight(source)

        per_source_value = base_weight * direction * quality_decay
        raw_sum += per_source_value
        max_possible_sum += abs(base_weight) * quality_decay

    # 5. Normalize
    if max_possible_sum == 0:
        # No sources with extracted text — return neutral
        claim_score = 50.0
    else:
        normalized = (raw_sum / max_possible_sum + 1) / 2 * 100
        claim_score = normalized

    # 5. Apply net source confidence multiplier and government boost only when
    #    there is actual relevant scoring evidence; skip both when all sources
    #    were inconclusive (max_possible_sum == 0) to keep the score at 50.
    government_source_only = claim.government_source_only
    if max_possible_sum > 0:
        supports_count = sum(1 for ev in evidence_list if ev.supports_claim and ev.is_relevant)
        contradicts_count = sum(1 for ev in evidence_list if not ev.supports_claim and ev.is_relevant)
        net = supports_count - contradicts_count
        multiplier = net_confidence_multiplier(net)
        claim_score = claim_score * multiplier
        claim_score = max(0.0, min(100.0, claim_score))

        # 5a. Government source only boost
        if government_source_only:
            claim_score = claim_score * GOVERNMENT_ONLY_BOOST
            claim_score = max(0.0, min(100.0, claim_score))

    # Build overall_reason note
    overall_reason_parts: list[str] = []
    if paywalled_count > 0:
        overall_reason_parts.append(
            f"{paywalled_count} source(s) could not be evaluated due to paywall restrictions."
        )
    if max_possible_sum == 0:
        overall_reason_parts.append(
            "No source text was available; claim marked as unverified (neutral score)."
        )
    if government_source_only and max_possible_sum > 0:
        overall_reason_parts.append(
            "All sources for this claim are government-owned; applied government source boost."
        )

    overall_reason = " ".join(overall_reason_parts) if overall_reason_parts else None

    # Re-derive verdict from numerical score
    verdict = score_to_verdict(claim_score)

    # If all sources paywalled, force unverified
    if paywalled_count > 0 and len(evidence_list) == 0:
        verdict = ClaimVerdict.unverified
        if not overall_reason:
            overall_reason = "All sources are paywalled; claim cannot be verified."

    judged = JudgedClaim(
        claim_id=claim.claim_id,
        claim_summary=claim.claim_summary,
        extract=claim.extract,
        verdict=verdict,
        overall_reason=overall_reason,
        government_source_only=government_source_only,
        sources=unique_sources,
        evidence=evidence_list,
    )

    return judged, claim_score


# ---------------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------------

async def judge(
    ingestion: IngestionResult,
    investigation: InvestigationResult,
    anthropic_api_key: str,
) -> JudgementResult:
    """
    Aggregate InvestigationResult into a final JudgementResult.

    Steps:
    1. Per-claim evidence extraction and scoring
    2. Blend claims score with publisher score
    3. Apply fakeness penalty
    4. Detect conflict
    5. Assess writing quality
    6. Build ContentCredibility and JudgementResult
    """
    client = anthropic.AsyncAnthropic(api_key=anthropic_api_key)

    claims = investigation.claims
    publisher_score = float(investigation.publisher_credibility.score)

    # ── Zero claims edge case ─────────────────────────────────────────────
    if not claims:
        content_credibility = ContentCredibility(
            score=50,
            rating=ContentRating.mixed,
            summary=(
                "No checkable factual claims were identified in this article "
                "(it may be an opinion piece or satire)."
            ),
            total_claims_found=0,
            government_source_only_flag=False,
            writing_quality=None,
        )
        return JudgementResult(
            content=ingestion.content,
            publisher_credibility=investigation.publisher_credibility,
            content_credibility=content_credibility,
            claims=[],
            is_conflicted=False,
        )

    # ── Judge each claim ──────────────────────────────────────────────────
    tasks = [judge_claim(client, claim) for claim in claims]
    results: list[tuple[JudgedClaim, float]] = await asyncio.gather(*tasks)

    judged_claims = [r[0] for r in results]
    claim_scores = [r[1] for r in results]

    # ── claims_score = average of per-claim scores ────────────────────────
    claims_score = sum(claim_scores) / len(claim_scores)

    # ── Blend with publisher score ────────────────────────────────────────
    blended = (claims_score * CLAIMS_BLEND_WEIGHT) + (publisher_score * PUBLISHER_BLEND_WEIGHT)

    # ── Fakeness penalty ─────────────────────────────────────────────────
    if investigation.fakeness_score > FAKENESS_THRESHOLD:
        blended = blended * FAKENESS_PENALTY_MULTIPLIER

    # ── Clamp ─────────────────────────────────────────────────────────────
    final_score = int(max(0.0, min(100.0, round(blended))))

    # ── Conflict detection ────────────────────────────────────────────────
    is_conflicted = abs(claims_score - publisher_score) >= CONFLICT_GAP_THRESHOLD

    # ── Claim verdict counts ──────────────────────────────────────────────
    verdict_counts: dict[str, int] = {
        "true": 0,
        "mostly_true": 0,
        "misleading": 0,
        "unverified": 0,
        "mostly_false": 0,
        "false": 0,
    }
    government_any = False
    for jc in judged_claims:
        key = jc.verdict.value
        if key in verdict_counts:
            verdict_counts[key] += 1
        if jc.government_source_only:
            government_any = True

    # ── Writing quality ───────────────────────────────────────────────────
    writing_quality: Optional[WritingQuality] = None
    if ingestion.content.body:
        try:
            writing_quality = await assess_writing_quality(client, ingestion.content.body)
        except Exception:
            writing_quality = None

    # ── Summary ───────────────────────────────────────────────────────────
    rating = score_to_rating(final_score)
    summary_parts: list[str] = [
        f"Overall credibility score: {final_score}/100 ({rating.value.replace('_', ' ')})."
    ]
    if investigation.fakeness_score > FAKENESS_THRESHOLD:
        summary_parts.append(
            f"AI-generated content detected (fakeness score {investigation.fakeness_score}); "
            "credibility score reduced."
        )
    if is_conflicted:
        summary_parts.append(
            f"Conflict detected: claim evidence score ({claims_score:.0f}) and "
            f"publisher credibility score ({publisher_score:.0f}) diverge by "
            f"{abs(claims_score - publisher_score):.0f} points."
        )
    if government_any:
        summary_parts.append(
            "Warning: one or more claims are backed exclusively by government sources."
        )

    content_credibility = ContentCredibility(
        score=final_score,
        rating=rating,
        summary=" ".join(summary_parts),
        total_claims_found=len(claims),
        claims_true=verdict_counts["true"],
        claims_mostly_true=verdict_counts["mostly_true"],
        claims_misleading=verdict_counts["misleading"],
        claims_unverified=verdict_counts["unverified"],
        claims_mostly_false=verdict_counts["mostly_false"],
        claims_false=verdict_counts["false"],
        government_source_only_flag=government_any,
        writing_quality=writing_quality,
    )

    return JudgementResult(
        content=ingestion.content,
        publisher_credibility=investigation.publisher_credibility,
        content_credibility=content_credibility,
        claims=judged_claims,
        is_conflicted=is_conflicted,
    )
