from __future__ import annotations

from datetime import date as Date
from enum import Enum
from typing import List, Literal, Optional

from pydantic import BaseModel


class InputType(str, Enum):
    url = "url"
    text = "text"
    pdf = "pdf"
    docx = "docx"
    html = "html"
    md = "md"
    rtf = "rtf"
    image = "image"


class ContentMetadata(BaseModel):
    input_type: InputType
    url: Optional[str] = None
    s3_url: Optional[str] = None
    title: Optional[str] = None
    publisher: Optional[str] = None
    date: Optional[Date] = None
    author: Optional[str] = None
    section: Optional[str] = None
    is_opinion: bool = False
    original_language: str = "en"


class IngestionResult(BaseModel):
    content: ContentMetadata
    original_text: str
    text: str
    token_count: int


# ---------------------------------------------------------------------------
# Investigation models (Step 4)
# ---------------------------------------------------------------------------


class Source(BaseModel):
    name: str
    url: str
    source_type: str
    is_independent: bool
    s3_url: Optional[str] = None
    extracted_text: Optional[str] = None


class Claim(BaseModel):
    claim_id: int
    claim_summary: str
    extract: str
    verdict: Literal["true", "likely_true", "unverified", "likely_false", "false"]
    reason: str
    government_source_only: bool
    sources: List[Source]


class PublisherCredibility(BaseModel):
    score: int
    rating: Literal["highly_credible", "credible", "mixed", "questionable", "not_credible"]
    summary: str
    bias: Literal["far_left", "left", "centre_left", "centre", "centre_right", "right", "far_right", "unknown"]
    known_issues: List[str]
    fact_checker_ratings: List[str]


class InvestigationResult(BaseModel):
    claims: List[Claim]
    publisher_credibility: PublisherCredibility
    fakeness_score: int


# ---------------------------------------------------------------------------
# Judgement models (Step 5)
# ---------------------------------------------------------------------------


class WritingQuality(BaseModel):
    sensationalism_score: int
    uses_named_sources: bool
    uses_anonymous_sources: bool
    emotional_language: bool


class ContentCredibility(BaseModel):
    score: int
    rating: Literal["credible", "likely_credible", "uncertain", "likely_false", "false"]
    summary: str
    total_claims_found: int
    claims_true: int
    claims_likely_true: int
    claims_unverified: int
    claims_likely_false: int
    claims_false: int
    government_source_only_flag: bool
    writing_quality: WritingQuality


class ClaimEvidence(BaseModel):
    source_id: str
    source_name: str
    source_url: str
    snippet: str
    supports_claim: bool
    judgement_reason: str


class JudgedClaim(BaseModel):
    claim_id: int
    claim_summary: str
    extract: str
    verdict: Literal["true", "likely_true", "unverified", "likely_false", "false"]
    overall_reason: str
    government_source_only: bool
    sources: List[Source]
    evidence: List[ClaimEvidence]


class JudgementResult(BaseModel):
    content: ContentMetadata
    publisher_credibility: PublisherCredibility
    content_credibility: ContentCredibility
    claims: List[JudgedClaim]
