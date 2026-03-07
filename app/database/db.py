"""
Database layer (Step 6) - async read/write for content, analysis, and sources.

Uses clickhouse-connect for ClickHouse Cloud. Credentials are loaded from .env:
CLICKHOUSE_HOST, CLICKHOUSE_PORT, CLICKHOUSE_USER, CLICKHOUSE_PASSWORD.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import List, Optional

import clickhouse_connect
from dotenv import load_dotenv

from app.models.schemas import (
    ContentMetadata,
    IngestionResult,
    InputType,
    JudgementResult,
    ClaimSource,
)

load_dotenv()

logger = logging.getLogger(__name__)

_CLICKHOUSE_HOST: str = os.getenv("CLICKHOUSE_HOST", "localhost")
_CLICKHOUSE_PORT: int = int(os.getenv("CLICKHOUSE_PORT", "8443"))
_CLICKHOUSE_USER: str = os.getenv("CLICKHOUSE_USER", "default")
_CLICKHOUSE_PASSWORD: str = os.getenv("CLICKHOUSE_PASSWORD", "")

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

_DDL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS content (
        id                String,
        input_type        String,
        source_url        Nullable(String),
        s3_url            Nullable(String),
        title             Nullable(String),
        publisher         Nullable(String),
        author            Nullable(String),
        date              Nullable(String),
        section           Nullable(String),
        is_opinion        UInt8 DEFAULT 0,
        original_language String DEFAULT 'en',
        original_text     Nullable(String),
        translated_text   Nullable(String),
        ingested_at       String
    ) ENGINE = ReplacingMergeTree()
    ORDER BY id
    """,
    """
    CREATE TABLE IF NOT EXISTS analysis (
        id           String,
        content_id   String,
        source_url   Nullable(String),
        analysis     String,
        analysed_at  String
    ) ENGINE = ReplacingMergeTree()
    ORDER BY id
    """,
    """
    CREATE TABLE IF NOT EXISTS sources (
        id             String,
        content_id     String,
        claim_id       Nullable(Int32),
        name           Nullable(String),
        url            Nullable(String),
        source_type    Nullable(String),
        is_independent UInt8 DEFAULT 1,
        s3_url         Nullable(String),
        extracted_text Nullable(String)
    ) ENGINE = ReplacingMergeTree()
    ORDER BY id
    """,
    """
    CREATE TABLE IF NOT EXISTS evidence (
        id               String,
        content_id       String,
        claim_id         Nullable(Int32),
        source_id        Nullable(String),
        snippet          Nullable(String),
        supports_claim   UInt8 DEFAULT 1,
        judgement_reason Nullable(String)
    ) ENGINE = ReplacingMergeTree()
    ORDER BY id
    """,
]

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _get_client() -> clickhouse_connect.driver.AsyncClient:
    return await clickhouse_connect.get_async_client(
        host=_CLICKHOUSE_HOST,
        port=_CLICKHOUSE_PORT,
        username=_CLICKHOUSE_USER,
        password=_CLICKHOUSE_PASSWORD,
        secure=True,
    )


async def _get_content_id(client, url: str) -> Optional[str]:
    """Look up the internal UUID for a content record by source URL."""
    result = await client.query(
        "SELECT id FROM content WHERE source_url = {url:String} LIMIT 1",
        parameters={"url": url},
    )
    rows = result.result_rows
    return rows[0][0] if rows else None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def init_db() -> None:
    """Create all tables if they do not already exist."""
    logger.debug(
        "init_db: connecting to ClickHouse host=%s port=%d",
        _CLICKHOUSE_HOST,
        _CLICKHOUSE_PORT,
    )
    client = await _get_client()
    for stmt in _DDL_STATEMENTS:
        await client.command(stmt)
    logger.debug("init_db: schema ready")


async def get_content(url: str) -> Optional[ContentMetadata]:
    """Return stored ContentMetadata for *url*, or None if not found."""
    logger.debug("get_content: url=%s", url)
    client = await _get_client()
    result = await client.query(
        """
        SELECT input_type, source_url, s3_url, title, publisher, author,
               date, section, is_opinion, original_language
        FROM content
        WHERE source_url = {url:String}
        LIMIT 1
        """,
        parameters={"url": url},
    )
    rows = result.result_rows

    if not rows:
        logger.debug("get_content: miss url=%s", url)
        return None

    row = rows[0]
    meta = ContentMetadata(
        input_type=InputType(row[0]),
        url=row[1],
        s3_url=row[2],
        title=row[3],
        publisher=row[4],
        author=row[5],
        date=row[6],
        section=row[7],
        is_opinion=bool(row[8]),
        original_language=row[9] or "en",
    )
    logger.debug("get_content: hit url=%s title=%r", url, meta.title)
    return meta


async def save_content(result: IngestionResult) -> None:
    """Persist an IngestionResult (content record + both text variants)."""
    content = result.content
    record_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    logger.debug(
        "save_content: url=%s id=%s input_type=%s",
        content.url,
        record_id,
        content.input_type,
    )
    client = await _get_client()
    await client.insert(
        "content",
        [[
            record_id,
            content.input_type.value,
            content.url,
            content.s3_url,
            content.title,
            content.publisher,
            content.author,
            str(content.date) if content.date else None,
            content.section,
            int(content.is_opinion),
            content.original_language,
            result.original_text,
            result.text,
            now,
        ]],
        column_names=[
            "id", "input_type", "source_url", "s3_url", "title",
            "publisher", "author", "date", "section", "is_opinion",
            "original_language", "original_text", "translated_text", "ingested_at",
        ],
    )
    logger.debug("save_content: committed id=%s", record_id)


async def get_analysis(url: str) -> Optional[JudgementResult]:
    """Return the most recent cached JudgementResult for *url*, or None."""
    logger.debug("get_analysis: url=%s", url)
    client = await _get_client()
    result = await client.query(
        """
        SELECT analysis
        FROM analysis
        WHERE source_url = {url:String}
        ORDER BY analysed_at DESC
        LIMIT 1
        """,
        parameters={"url": url},
    )
    rows = result.result_rows

    if not rows:
        logger.debug("get_analysis: miss url=%s", url)
        return None

    judgement = JudgementResult.model_validate_json(rows[0][0])
    logger.debug("get_analysis: hit url=%s", url)
    return judgement


async def save_analysis(result: JudgementResult) -> None:
    """Persist a completed JudgementResult plus all evidence snippets."""
    source_url = result.content.url
    analysis_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    logger.debug(
        "save_analysis: url=%s analysis_id=%s claims=%d",
        source_url,
        analysis_id,
        len(result.claims),
    )
    client = await _get_client()
    content_id = await _get_content_id(client, source_url)
    if content_id is None:
        logger.warning(
            "save_analysis: no content row found for url=%s; "
            "generating placeholder content_id",
            source_url,
        )
        content_id = str(uuid.uuid4())

    await client.insert(
        "analysis",
        [[analysis_id, content_id, source_url, result.model_dump_json(), now]],
        column_names=["id", "content_id", "source_url", "analysis", "analysed_at"],
    )

    # Collect all evidence rows across claims, then insert in one batch.
    evidence_rows = []
    for claim in result.claims:
        for ev in claim.evidence:
            src_result = await client.query(
                "SELECT id FROM sources WHERE url = {url:String} AND content_id = {cid:String} LIMIT 1",
                parameters={"url": ev.source_url, "cid": content_id},
            )
            src_rows = src_result.result_rows
            source_id = src_rows[0][0] if src_rows else None

            ev_id = str(uuid.uuid4())
            logger.debug(
                "save_analysis: evidence ev_id=%s claim_id=%d source_id=%s supports=%s",
                ev_id,
                claim.claim_id,
                source_id,
                ev.supports_claim,
            )
            evidence_rows.append([
                ev_id,
                content_id,
                claim.claim_id,
                source_id,
                ev.snippet,
                int(ev.supports_claim),
                ev.judgement_reason,
            ])

    if evidence_rows:
        await client.insert(
            "evidence",
            evidence_rows,
            column_names=[
                "id", "content_id", "claim_id", "source_id",
                "snippet", "supports_claim", "judgement_reason",
            ],
        )

    logger.debug("save_analysis: committed analysis_id=%s", analysis_id)


async def get_cached_result(url: str) -> Optional[JudgementResult]:
    """Alias for get_analysis — returns cached JudgementResult for *url* or None."""
    return await get_analysis(url)


async def store_result(url: Optional[str], result: JudgementResult) -> None:
    """Persist a completed JudgementResult (called by routes.py after judgement)."""
    await save_analysis(result)


async def get_sources(url: str) -> List[ClaimSource]:
    """Return all stored sources for the content identified by *url*."""
    logger.debug("get_sources: url=%s", url)
    client = await _get_client()
    result = await client.query(
        """
        SELECT s.name, s.url, s.source_type, s.is_independent,
               s.s3_url, s.extracted_text
        FROM sources s
        JOIN content c ON s.content_id = c.id
        WHERE c.source_url = {url:String}
        """,
        parameters={"url": url},
    )
    rows = result.result_rows

    sources = [
        ClaimSource(
            name=row[0],
            url=row[1],
            source_type=row[2],
            is_independent=bool(row[3]),
            s3_url=row[4],
            extracted_text=row[5],
        )
        for row in rows
    ]
    logger.debug("get_sources: url=%s count=%d", url, len(sources))
    return sources


async def save_sources(url: str, srcs: List[ClaimSource]) -> None:
    """Persist corroborative sources for the content identified by *url*."""
    logger.debug("save_sources: url=%s count=%d", url, len(srcs))
    client = await _get_client()
    content_id = await _get_content_id(client, url)
    if content_id is None:
        logger.warning(
            "save_sources: no content row found for url=%s; "
            "generating placeholder content_id",
            url,
        )
        content_id = str(uuid.uuid4())

    rows = []
    for src in srcs:
        src_id = str(uuid.uuid4())
        logger.debug(
            "save_sources: src_id=%s name=%r url=%s",
            src_id,
            src.name,
            src.url,
        )
        rows.append([
            src_id,
            content_id,
            None,  # claim_id not available via this interface
            src.name,
            src.url,
            src.source_type,
            int(src.is_independent),
            src.s3_url,
            src.extracted_text,
        ])

    if rows:
        await client.insert(
            "sources",
            rows,
            column_names=[
                "id", "content_id", "claim_id", "name", "url",
                "source_type", "is_independent", "s3_url", "extracted_text",
            ],
        )
    logger.debug("save_sources: committed %d sources for url=%s", len(srcs), url)
