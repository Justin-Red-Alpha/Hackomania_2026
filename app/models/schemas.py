from __future__ import annotations

"""
Shared Pydantic models for the FactGuard pipeline.
All teams add their models to this file.
"""

from datetime import date as _Date
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, HttpUrl, model_validator


# ─────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────

class InputType(str, Enum):
    url   = "url"
    text  = "text"
    pdf   = "pdf"
    docx  = "docx"
    html  = "html"
    md    = "md"
    rtf   = "rtf"
    image = "image"


class SearchDepth(str, Enum):
    basic    = "basic"
    advanced = "advanced"


class PublisherRating(str, Enum):
    highly_credible = "highly_credible"
    credible        = "credible"
    mixed           = "mixed"
    low_credibility = "low_credibility"
    not_credible    = "not_credible"


class PoliticalBias(str, Enum):
    far_left     = "far_left"
    left         = "left"
    center_left  = "center_left"
    center       = "center"
    center_right = "center_right"
    right        = "right"
    far_right    = "far_right"
    unknown      = "unknown"


class ContentRating(str, Enum):
    credible        = "credible"
    mostly_credible = "mostly_credible"
    mixed           = "mixed"
    low_credibility = "low_credibility"
    not_credible    = "not_credible"


class ClaimVerdict(str, Enum):
    true         = "true"
    mostly_true  = "mostly_true"
    misleading   = "misleading"
    inconclusive = "inconclusive"  # evidence exists but conflicts or is balanced
    unverified   = "unverified"    # no sources available to check against
    mostly_false = "mostly_false"
    false        = "false"


# ─────────────────────────────────────────────
# Request  (Frontend team)
# ─────────────────────────────────────────────

class AnalyseRequest(BaseModel):
    """Request body for POST /api/v1/analyse.

    Exactly one of articleUrl or articleText must be provided.
    """

    articleUrl:  Optional[HttpUrl] = None
    articleText: Optional[str]     = None
    noCache:     bool              = False  # set True to bypass DB cache and re-run pipeline

    @model_validator(mode="after")
    def require_one_input(self) -> "AnalyseRequest":
        if not self.articleUrl and not self.articleText:
            raise ValueError("Provide either articleUrl or articleText.")
        if self.articleUrl and self.articleText:
            raise ValueError("Provide only one of articleUrl or articleText.")
        return self


# ─────────────────────────────────────────────
# Ingestion models  (Steps 1-3 – Ingestion team)
# ─────────────────────────────────────────────

class ContentMetadata(BaseModel):
    """Extracted metadata about the submitted article."""

    input_type:        InputType
    url:               Optional[str]  = None
    s3_url:            Optional[str]  = None
    title:             Optional[str]  = None
    publisher:         Optional[str]  = None
    date:              Optional[_Date] = None
    author:            Optional[str]  = None
    section:           Optional[str]  = None
    is_opinion:        bool           = False
    original_language: str            = "en"
    body:              Optional[str]  = None


class IngestionResult(BaseModel):
    content:       ContentMetadata
    original_text: str
    text:          str
    token_count:   int


# ─────────────────────────────────────────────
# Claim sources  (Step 4a – Investigation team)
# ─────────────────────────────────────────────

class ClaimSource(BaseModel):
    """A source article used to evaluate a claim."""

    source_id:         Optional[str]  = None
    name:              Optional[str]  = None
    url:               str
    type:              Optional[str]  = None
    is_independent:    Optional[bool] = None
    is_primary_source: bool           = False
    hop_depth:         int            = 0
    s3_url:            Optional[str]  = None
    extracted_text:    Optional[str]  = None


# ─────────────────────────────────────────────
# Investigation models  (Step 4 – Investigation team)
# ─────────────────────────────────────────────

class Claim(BaseModel):
    claim_id:               int
    claim_summary:          str
    extract:                str
    verdict:                ClaimVerdict
    reason:                 str
    government_source_only: bool              = False
    sources:                List[ClaimSource] = []


class InvestigationResult(BaseModel):
    claims:                List[Claim]
    publisher_credibility: "PublisherCredibility"
    fakeness_score:        int = Field(..., ge=0, le=100)


# ─────────────────────────────────────────────
# Publisher credibility  (Step 4d / 5)
# ─────────────────────────────────────────────

class PublisherCredibility(BaseModel):
    score:                int                     = Field(..., ge=0, le=100)
    rating:               PublisherRating
    summary:              Optional[str]           = None
    bias:                 Optional[PoliticalBias] = None
    known_issues:         List[str]               = []
    fact_checker_ratings: List[str]               = []


# ─────────────────────────────────────────────
# Content credibility  (Step 5 – Judgement team)
# ─────────────────────────────────────────────

class WritingQuality(BaseModel):
    sensationalism:     Optional[bool] = None
    named_sources:      Optional[bool] = None
    anonymous_sources:  Optional[bool] = None
    emotional_language: Optional[bool] = None
    hedging_language:   Optional[bool] = None


class ContentCredibility(BaseModel):
    score:                       int                      = Field(..., ge=0, le=100)
    rating:                      ContentRating
    summary:                     Optional[str]            = None
    total_claims_found:          int                      = 0
    claims_true:                 int                      = 0
    claims_mostly_true:          int                      = 0
    claims_misleading:           int                      = 0
    claims_inconclusive:         int                      = 0
    claims_unverified:           int                      = 0
    claims_mostly_false:         int                      = 0
    claims_false:                int                      = 0
    government_source_only_flag: bool                     = False
    writing_quality:             Optional[WritingQuality] = None


# ─────────────────────────────────────────────
# Claim evidence  (Step 4a – Investigation team)
# ─────────────────────────────────────────────

class ClaimEvidence(BaseModel):
    """A single evidence snippet supporting or contradicting a claim."""

    source_id:        str
    source_name:      str
    source_url:       str
    snippet:          str
    supports_claim:   bool
    is_relevant:      bool           = True   # False when source cannot confirm or deny the claim
    judgement_reason: Optional[str] = None


# ─────────────────────────────────────────────
# Individual judged claim  (Step 4 – Investigation team)
# ─────────────────────────────────────────────

class JudgedClaim(BaseModel):
    claim_id:               int
    claim_summary:          str
    extract:                Optional[str]       = None
    verdict:                ClaimVerdict
    overall_reason:         Optional[str]       = None
    government_source_only: bool                = False
    sources:                List[ClaimSource]   = []
    evidence:               List[ClaimEvidence] = []


# ─────────────────────────────────────────────
# Final analysis response  (Step 5 – Judgement team)
# ─────────────────────────────────────────────

class JudgementResult(BaseModel):
    """Top-level response returned by POST /api/v1/analyse."""

    content:               ContentMetadata
    publisher_credibility: PublisherCredibility
    content_credibility:   ContentCredibility
    claims:                List[JudgedClaim] = []
    is_conflicted:         bool              = False
