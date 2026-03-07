"""
Tests for the database layer (Step 6).

All tests use a temporary SQLite file so no persistent state is created.
Run with:  pytest tests/test_database.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add project root to sys.path so `import app` works when this file is run
# directly (e.g. via VSCode "Run Python File") as well as via pytest.
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import date

import pytest

import app.database.db as db_module
from app.models.schemas import (
    ClaimEvidence,
    ContentCredibility,
    ContentMetadata,
    IngestionResult,
    InputType,
    JudgedClaim,
    JudgementResult,
    PublisherCredibility,
    Source,
    WritingQuality,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def use_temp_db(tmp_path, monkeypatch):
    """Point the db module at a fresh temporary SQLite file for every test."""
    db_path = str(tmp_path / "test.db").replace("\\", "/")
    monkeypatch.setattr(db_module, "_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")


@pytest.fixture
def sample_ingestion() -> IngestionResult:
    return IngestionResult(
        content=ContentMetadata(
            input_type=InputType.url,
            url="https://example.com/article",
            title="Test Article",
            publisher="Example Publisher",
            author="Jane Doe",
            date=date(2026, 3, 7),
            is_opinion=False,
            original_language="en",
        ),
        original_text="Original article text.",
        text="Translated article text.",
        token_count=5,
    )


@pytest.fixture
def sample_sources() -> list[Source]:
    return [
        Source(
            name="Reuters",
            url="https://reuters.com/story/1",
            source_type="news",
            is_independent=True,
            extracted_text="Reuters report on the topic.",
        ),
        Source(
            name="BBC",
            url="https://bbc.com/story/2",
            source_type="news",
            is_independent=True,
        ),
    ]


@pytest.fixture
def sample_judgement(sample_ingestion: IngestionResult) -> JudgementResult:
    evidence = ClaimEvidence(
        source_id="placeholder-id",
        source_name="Reuters",
        source_url="https://reuters.com/story/1",
        snippet="Reuters confirms the event.",
        supports_claim=True,
        judgement_reason="Direct corroboration from Reuters.",
    )
    claim = JudgedClaim(
        claim_id=1,
        claim_summary="The event occurred on 2026-03-07.",
        extract="The event occurred on 2026-03-07.",
        verdict="true",
        overall_reason="Corroborated by Reuters.",
        government_source_only=False,
        sources=[
            Source(
                name="Reuters",
                url="https://reuters.com/story/1",
                source_type="news",
                is_independent=True,
            )
        ],
        evidence=[evidence],
    )
    return JudgementResult(
        content=sample_ingestion.content,
        publisher_credibility=PublisherCredibility(
            score=80,
            rating="credible",
            summary="Generally reliable source.",
            bias="centre",
            known_issues=[],
            fact_checker_ratings=[],
        ),
        content_credibility=ContentCredibility(
            score=85,
            rating="likely_credible",
            summary="Article is mostly accurate.",
            total_claims_found=1,
            claims_true=1,
            claims_likely_true=0,
            claims_unverified=0,
            claims_likely_false=0,
            claims_false=0,
            government_source_only_flag=False,
            writing_quality=WritingQuality(
                sensationalism_score=5,
                uses_named_sources=True,
                uses_anonymous_sources=False,
                emotional_language=False,
            ),
        ),
        claims=[claim],
    )


# ---------------------------------------------------------------------------
# init_db
# ---------------------------------------------------------------------------


async def test_init_db_creates_tables():
    await db_module.init_db()
    # Calling again must be idempotent (CREATE TABLE IF NOT EXISTS)
    await db_module.init_db()


# ---------------------------------------------------------------------------
# content
# ---------------------------------------------------------------------------


async def test_get_content_miss():
    await db_module.init_db()
    result = await db_module.get_content("https://notfound.example.com")
    assert result is None


async def test_save_and_get_content(sample_ingestion: IngestionResult):
    await db_module.init_db()
    await db_module.save_content(sample_ingestion)

    meta = await db_module.get_content("https://example.com/article")
    assert meta is not None
    assert meta.url == "https://example.com/article"
    assert meta.title == "Test Article"
    assert meta.publisher == "Example Publisher"
    assert meta.author == "Jane Doe"
    assert meta.is_opinion is False
    assert meta.original_language == "en"


async def test_save_content_upsert(sample_ingestion: IngestionResult):
    """Saving the same URL twice should not raise and should update the record."""
    await db_module.init_db()
    await db_module.save_content(sample_ingestion)

    updated = IngestionResult(
        content=ContentMetadata(
            input_type=InputType.url,
            url="https://example.com/article",
            title="Updated Title",
            publisher="Example Publisher",
            author="Jane Doe",
            is_opinion=False,
            original_language="en",
        ),
        original_text="Updated original text.",
        text="Updated translated text.",
        token_count=5,
    )
    await db_module.save_content(updated)

    meta = await db_module.get_content("https://example.com/article")
    assert meta is not None
    assert meta.title == "Updated Title"


# ---------------------------------------------------------------------------
# sources
# ---------------------------------------------------------------------------


async def test_get_sources_empty():
    await db_module.init_db()
    sources = await db_module.get_sources("https://notfound.example.com")
    assert sources == []


async def test_save_and_get_sources(
    sample_ingestion: IngestionResult, sample_sources: list[Source]
):
    await db_module.init_db()
    await db_module.save_content(sample_ingestion)
    await db_module.save_sources("https://example.com/article", sample_sources)

    retrieved = await db_module.get_sources("https://example.com/article")
    assert len(retrieved) == 2
    names = {s.name for s in retrieved}
    assert "Reuters" in names
    assert "BBC" in names


async def test_save_sources_preserves_fields(
    sample_ingestion: IngestionResult, sample_sources: list[Source]
):
    await db_module.init_db()
    await db_module.save_content(sample_ingestion)
    await db_module.save_sources("https://example.com/article", sample_sources)

    retrieved = await db_module.get_sources("https://example.com/article")
    reuters = next(s for s in retrieved if s.name == "Reuters")
    assert reuters.source_type == "news"
    assert reuters.is_independent is True
    assert reuters.extracted_text == "Reuters report on the topic."


# ---------------------------------------------------------------------------
# analysis
# ---------------------------------------------------------------------------


async def test_get_analysis_miss():
    await db_module.init_db()
    result = await db_module.get_analysis("https://notfound.example.com")
    assert result is None


async def test_save_and_get_analysis(
    sample_ingestion: IngestionResult, sample_judgement: JudgementResult
):
    await db_module.init_db()
    await db_module.save_content(sample_ingestion)
    await db_module.save_analysis(sample_judgement)

    retrieved = await db_module.get_analysis("https://example.com/article")
    assert retrieved is not None
    assert retrieved.content.url == "https://example.com/article"
    assert retrieved.content_credibility.score == 85
    assert retrieved.content_credibility.rating == "likely_credible"
    assert len(retrieved.claims) == 1
    assert retrieved.claims[0].claim_id == 1
    assert retrieved.claims[0].verdict == "true"


async def test_get_analysis_returns_most_recent(
    sample_ingestion: IngestionResult, sample_judgement: JudgementResult
):
    """When multiple analysis rows exist, the most recent should be returned."""
    await db_module.init_db()
    await db_module.save_content(sample_ingestion)
    await db_module.save_analysis(sample_judgement)

    updated_judgement = sample_judgement.model_copy(deep=True)
    updated_judgement.content_credibility.score = 50
    await db_module.save_analysis(updated_judgement)

    retrieved = await db_module.get_analysis("https://example.com/article")
    assert retrieved is not None
    assert retrieved.content_credibility.score == 50


async def test_save_analysis_persists_evidence(
    sample_ingestion: IngestionResult,
    sample_sources: list[Source],
    sample_judgement: JudgementResult,
):
    """Evidence rows should be written alongside the analysis record."""
    await db_module.init_db()
    await db_module.save_content(sample_ingestion)
    await db_module.save_sources("https://example.com/article", sample_sources)
    await db_module.save_analysis(sample_judgement)

    import app.database.db as _db

    async with _db._connect() as conn:
        cursor = await conn.execute("SELECT COUNT(*) FROM evidence")
        row = await cursor.fetchone()

    assert row[0] == 1
