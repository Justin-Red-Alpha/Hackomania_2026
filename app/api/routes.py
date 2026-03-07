"""
API routes for the FactGuard pipeline.

POST /api/v1/analyse
  1. Check DB cache for existing result by URL
  2. If not cached: run ingestion → investigation → judgement pipeline
  3. Store result in DB
  4. Return JudgementResult
"""

from __future__ import annotations

import logging
import os

from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException

from app.models.schemas import AnalyseRequest, JudgementResult

load_dotenv()

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")


@router.post("/analyse", response_model=JudgementResult)
async def analyse_article(request: AnalyseRequest) -> JudgementResult:
    """
    Main analysis endpoint.

    Accepts an article URL and API keys, runs the full fact-checking pipeline,
    and returns a JudgementResult with credibility scores and per-claim verdicts.
    """
    article_url   = str(request.articleUrl) if request.articleUrl else None
    article_text  = request.articleText or None
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    tavily_key    = os.environ.get("TAVILY_API_KEY", "")

    if not anthropic_key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY is not configured on the server.")
    if not tavily_key:
        raise HTTPException(status_code=500, detail="TAVILY_API_KEY is not configured on the server.")

    logger.info("Analyse request received — url=%s text=%s",
                article_url, bool(article_text))

    # ── Step 0: DB cache lookup (URL only — text has no stable cache key) ───
    if article_url:
        try:
            from app.database.db import get_cached_result
            cached = await get_cached_result(article_url)
            if cached:
                logger.info("Cache hit for: %s", article_url)
                return cached
        except ImportError:
            logger.debug("Database module not yet available — skipping cache lookup")
        except Exception as exc:
            logger.warning("Cache lookup failed: %s", exc)

    # ── Step 1–3: Ingestion ─────────────────────────────────────────────────
    try:
        from app.ingestion.ingestion_agent import run_ingestion

        content = await run_ingestion(
            url=article_url,
            text=article_text,
        )
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Ingestion pipeline is not yet implemented: {exc}",
        )
    except Exception as exc:
        logger.exception("Ingestion failed for %s", article_url)
        raise HTTPException(status_code=500, detail=f"Ingestion error: {exc}")

    # ── Step 4: Investigation ───────────────────────────────────────────────
    try:
        from app.investigation.investigator import run_investigation

        investigation = await run_investigation(ingestion_result=content)
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Investigation pipeline is not yet implemented: {exc}",
        )
    except Exception as exc:
        logger.exception("Investigation failed for %s", article_url)
        raise HTTPException(status_code=500, detail=f"Investigation error: {exc}")

    # ── Step 5: Judgement ───────────────────────────────────────────────────
    try:
        from app.judgement.judgement import judge

        result: JudgementResult = await judge(
            content=content,
            investigation=investigation,
        )
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="Judgement pipeline is not yet implemented.",
        )
    except Exception as exc:
        logger.exception("Judgement failed for %s", article_url)
        raise HTTPException(status_code=500, detail=f"Judgement error: {exc}")

    # ── Step 6: Store in DB ─────────────────────────────────────────────────
    try:
        from app.database.db import store_result
        await store_result(article_url, result)
    except ImportError:
        logger.debug("Database module not yet available — skipping store")
    except Exception as exc:
        logger.warning("Failed to cache result: %s", exc)

    return result
