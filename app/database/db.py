"""
Database layer (Step 6) - async read/write for content, analysis, and sources.

Uses aiosqlite for local SQLite development. Swap DATABASE_URL in .env to a
postgres+asyncpg:// connection string to use PostgreSQL in production.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import List, Optional

import aiosqlite
from dotenv import load_dotenv

from app.models.schemas import (
    ContentMetadata,
    IngestionResult,
    InputType,
    JudgementResult,
    Source,
)

load_dotenv()

logger = logging.getLogger(__name__)

_DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./analysis.db")

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS content (
    id                TEXT PRIMARY KEY,
    input_type        TEXT NOT NULL,
    source_url        TEXT,
    s3_url            TEXT,
    title             TEXT,
    publisher         TEXT,
    author            TEXT,
    date              TEXT,
    section           TEXT,
    is_opinion        INTEGER NOT NULL DEFAULT 0,
    original_language TEXT NOT NULL DEFAULT 'en',
    original_text     TEXT,
    translated_text   TEXT,
    ingested_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS analysis (
    id           TEXT PRIMARY KEY,
    content_id   TEXT NOT NULL REFERENCES content(id),
    source_url   TEXT,
    analysis     TEXT NOT NULL,
    analysed_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
    id             TEXT PRIMARY KEY,
    content_id     TEXT NOT NULL REFERENCES content(id),
    claim_id       INTEGER,
    name           TEXT,
    url            TEXT,
    source_type    TEXT,
    is_independent INTEGER NOT NULL DEFAULT 1,
    s3_url         TEXT,
    extracted_text TEXT
);

CREATE TABLE IF NOT EXISTS evidence (
    id               TEXT PRIMARY KEY,
    content_id       TEXT NOT NULL REFERENCES content(id),
    claim_id         INTEGER,
    source_id        TEXT REFERENCES sources(id),
    snippet          TEXT,
    supports_claim   INTEGER NOT NULL DEFAULT 1,
    judgement_reason TEXT
);
"""


def _sqlite_path() -> str:
    """Extract the file path from a sqlite+aiosqlite:/// URL."""
    url = _DATABASE_URL
    prefix = "sqlite+aiosqlite:///"
    if url.startswith(prefix):
        return url[len(prefix):]
    raise ValueError(
        f"Unsupported DATABASE_URL scheme for aiosqlite driver: {url!r}. "
        "Expected sqlite+aiosqlite:///path/to/file.db"
    )


def _connect() -> aiosqlite.Connection:
    return aiosqlite.connect(_sqlite_path())


async def _get_content_id(db: aiosqlite.Connection, url: str) -> Optional[str]:
    """Look up the internal UUID for a content record by source URL."""
    cursor = await db.execute(
        "SELECT id FROM content WHERE source_url = ? LIMIT 1", (url,)
    )
    row = await cursor.fetchone()
    return row[0] if row else None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def init_db() -> None:
    """Create all tables if they do not already exist."""
    logger.debug("init_db: initialising database path=%s", _sqlite_path())
    async with _connect() as db:
        await db.executescript(_DDL)
        await db.commit()
    logger.debug("init_db: schema ready")


async def get_content(url: str) -> Optional[ContentMetadata]:
    """Return stored ContentMetadata for *url*, or None if not found."""
    logger.debug("get_content: url=%s", url)
    async with _connect() as db:
        cursor = await db.execute(
            """
            SELECT input_type, source_url, s3_url, title, publisher, author,
                   date, section, is_opinion, original_language
            FROM content
            WHERE source_url = ?
            LIMIT 1
            """,
            (url,),
        )
        row = await cursor.fetchone()

    if row is None:
        logger.debug("get_content: miss url=%s", url)
        return None

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
    async with _connect() as db:
        await db.execute(
            """
            INSERT OR REPLACE INTO content
                (id, input_type, source_url, s3_url, title, publisher, author,
                 date, section, is_opinion, original_language,
                 original_text, translated_text, ingested_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
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
            ),
        )
        await db.commit()
    logger.debug("save_content: committed id=%s", record_id)


async def get_analysis(url: str) -> Optional[JudgementResult]:
    """Return the most recent cached JudgementResult for *url*, or None."""
    logger.debug("get_analysis: url=%s", url)
    async with _connect() as db:
        cursor = await db.execute(
            """
            SELECT analysis
            FROM analysis
            WHERE source_url = ?
            ORDER BY analysed_at DESC
            LIMIT 1
            """,
            (url,),
        )
        row = await cursor.fetchone()

    if row is None:
        logger.debug("get_analysis: miss url=%s", url)
        return None

    result = JudgementResult.model_validate_json(row[0])
    logger.debug("get_analysis: hit url=%s", url)
    return result


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
    async with _connect() as db:
        content_id = await _get_content_id(db, source_url)
        if content_id is None:
            logger.warning(
                "save_analysis: no content row found for url=%s; "
                "generating placeholder content_id",
                source_url,
            )
            content_id = str(uuid.uuid4())

        await db.execute(
            """
            INSERT OR REPLACE INTO analysis
                (id, content_id, source_url, analysis, analysed_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                analysis_id,
                content_id,
                source_url,
                result.model_dump_json(),
                now,
            ),
        )

        # Persist evidence rows - one per ClaimEvidence across all claims.
        for claim in result.claims:
            for ev in claim.evidence:
                cursor = await db.execute(
                    "SELECT id FROM sources WHERE url = ? AND content_id = ? LIMIT 1",
                    (ev.source_url, content_id),
                )
                src_row = await cursor.fetchone()
                source_id = src_row[0] if src_row else None

                ev_id = str(uuid.uuid4())
                logger.debug(
                    "save_analysis: evidence ev_id=%s claim_id=%d source_id=%s supports=%s",
                    ev_id,
                    claim.claim_id,
                    source_id,
                    ev.supports_claim,
                )
                await db.execute(
                    """
                    INSERT OR REPLACE INTO evidence
                        (id, content_id, claim_id, source_id, snippet,
                         supports_claim, judgement_reason)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ev_id,
                        content_id,
                        claim.claim_id,
                        source_id,
                        ev.snippet,
                        int(ev.supports_claim),
                        ev.judgement_reason,
                    ),
                )

        await db.commit()
    logger.debug("save_analysis: committed analysis_id=%s", analysis_id)


async def get_sources(url: str) -> List[Source]:
    """Return all stored sources for the content identified by *url*."""
    logger.debug("get_sources: url=%s", url)
    async with _connect() as db:
        cursor = await db.execute(
            """
            SELECT s.name, s.url, s.source_type, s.is_independent,
                   s.s3_url, s.extracted_text
            FROM sources s
            JOIN content c ON s.content_id = c.id
            WHERE c.source_url = ?
            """,
            (url,),
        )
        rows = await cursor.fetchall()

    sources = [
        Source(
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


async def save_sources(url: str, srcs: List[Source]) -> None:
    """Persist corroborative sources for the content identified by *url*."""
    logger.debug("save_sources: url=%s count=%d", url, len(srcs))
    async with _connect() as db:
        content_id = await _get_content_id(db, url)
        if content_id is None:
            logger.warning(
                "save_sources: no content row found for url=%s; "
                "generating placeholder content_id",
                url,
            )
            content_id = str(uuid.uuid4())

        for src in srcs:
            src_id = str(uuid.uuid4())
            logger.debug(
                "save_sources: src_id=%s name=%r url=%s",
                src_id,
                src.name,
                src.url,
            )
            await db.execute(
                """
                INSERT OR REPLACE INTO sources
                    (id, content_id, claim_id, name, url, source_type,
                     is_independent, s3_url, extracted_text)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    src_id,
                    content_id,
                    None,  # claim_id not available via this interface
                    src.name,
                    src.url,
                    src.source_type,
                    int(src.is_independent),
                    src.s3_url,
                    src.extracted_text,
                ),
            )

        await db.commit()
    logger.debug("save_sources: committed %d sources for url=%s", len(srcs), url)
