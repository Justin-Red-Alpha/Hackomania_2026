"""
Shared Pydantic models for the FactGuard pipeline.
All teams add their models to this file.
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, HttpUrl, model_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class InputType(str, Enum):
    url   = "url"
    text  = "text"
    pdf   = "pdf"
    docx  = "docx"
    html  = "html"
    md    = "md"
    rtf   = "rtf"
    image = "image"


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
    unverified   = "unverified"
    mostly_false = "mostly_false"
    false        = "false"


# ---------------------------------------------------------------------------
# API request  (Frontend team)
# ---------------------------------------------------------------------------

class AnalyseRequest(BaseModel):
    articleUrl:  Optional[HttpUrl] = None
    articleText: Optional[str]     = None

    @model_validator(mode="after")
    def require_one_input(self) -> "AnalyseRequest":
        if not self.articleUrl and not self.articleText:
            raise ValueError("Provide either articleUrl or articleText.")
        if self.articleUrl and self.articleText:
            raise ValueError("Provide only one of articleUrl or articleText, not both.")
        return self


# ---------------------------------------------------------------------------
# Ingestion models (Steps 1-3)
# ---------------------------------------------------------------------------

class ContentMetadata(BaseModel):
    input_type:        InputType
    url:               Optional[str]  = None
    s3_url:            Optional[str]  = None
    title:             Optional[str]  = None
    publisher:         Optional[str]  = None
    date:              Optional[date] = None
    author:            Optional[str]  = None
    section:           Optional[str]  = None
    is_opinion:        bool           = False
    original_language: str            = "en"


class IngestionResult(BaseModel):
    content:       ContentMetadata
    original_text: str
    text:          str
    token_count:   int

# ---------------------------------------------------------------------------
# Investigation models (Step 4)
# ---------------------------------------------------------------------------

class PublisherCredibility(BaseModel):
    score:                int                      = Field(..., ge=0, le=100)
    rating:               PublisherRating
    summary:              Optional[str]            = None
    bias:                 Optional[PoliticalBias]  = None
    known_issues:         List[str]                = []
    fact_checker_ratings: List[str]                = []


class ClaimSource(BaseModel):
    source_id:         Optional[str]  = None   # UUID; assigned when saved to DB
    name:              Optional[str]  = None
    url:               str
    type:              Optional[str]  = None   # e.g. "government", "news", "journal"
    is_independent:    Optional[bool] = None
    is_primary_source: bool           = False  # True = own data/analysis; False = relays claim
    hop_depth:         int            = 0      # 0 = direct result; 1+ = citation chased
    s3_url:            Optional[str]  = None   # archived copy in S3
    extracted_text:    Optional[str]  = None   # full extracted body


class Claim(BaseModel):
    claim_id:               int
    claim_summary:          str
    extract:                str               # direct quote from the article
    verdict:                ClaimVerdict
    reason:                 str
    government_source_only: bool              = False
    sources:                List[ClaimSource] = []


class InvestigationResult(BaseModel):
    claims:                List[Claim]
    publisher_credibility: PublisherCredibility
    fakeness_score:        int = Field(..., ge=0, le=100)

# ---------------------------------------------------------------------------
# Judgement models (Step 5)
# ---------------------------------------------------------------------------

class WritingQuality(BaseModel):
    sensationalism:     Optional[bool] = None
    named_sources:      Optional[bool] = None
    anonymous_sources:  Optional[bool] = None
    emotional_language: Optional[bool] = None
    hedging_language:   Optional[bool] = None


class ContentCredibility(BaseModel):
    score:                       int            = Field(..., ge=0, le=100)
    rating:                      ContentRating
    summary:                     Optional[str]  = None
    total_claims_found:          int            = 0
    claims_true:                 int            = 0
    claims_mostly_true:          int            = 0
    claims_misleading:           int            = 0
    claims_unverified:           int            = 0
    claims_mostly_false:         int            = 0
    claims_false:                int            = 0
    government_source_only_flag: bool           = False
    writing_quality:             Optional[WritingQuality] = None


class ClaimEvidence(BaseModel):
    source_id:        str            # UUID of the corresponding ClaimSource
    source_name:      str
    source_url:       str
    snippet:          str            # verbatim excerpt from the source
    supports_claim:   bool           # True = supports, False = contradicts
    judgement_reason: Optional[str]  = None


class JudgedClaim(BaseModel):
    claim_id:               int
    claim_summary:          str
    extract:                Optional[str]       = None
    verdict:                ClaimVerdict
    overall_reason:         Optional[str]       = None
    government_source_only: bool                = False
    sources:                List[ClaimSource]   = []
    evidence:               List[ClaimEvidence] = []


class JudgementResult(BaseModel):
    content:               ContentMetadata
    publisher_credibility: PublisherCredibility
    content_credibility:   ContentCredibility
    claims:                List[JudgedClaim] = []