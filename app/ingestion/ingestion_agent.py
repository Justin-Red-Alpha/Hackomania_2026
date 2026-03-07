"""
Step 1: Accept input (URL, plain text, or file upload) and orchestrate the ingestion pipeline.
Uploads raw content to S3 for URL and file inputs, then delegates to extraction_agent
and summariser.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import date
from typing import Optional

import aioboto3
from dotenv import load_dotenv
from fastapi import UploadFile

from app.database.db import save_content
from app.ingestion.extraction_agent import extract
from app.ingestion.summariser import summarise
from app.models.schemas import IngestionResult, InputType

load_dotenv()

logger = logging.getLogger(__name__)

_FILE_EXTENSION_MAP: dict[str, InputType] = {
    ".pdf": InputType.pdf,
    ".docx": InputType.docx,
    ".html": InputType.html,
    ".htm": InputType.html,
    ".md": InputType.md,
    ".markdown": InputType.md,
    ".rtf": InputType.rtf,
    ".jpg": InputType.image,
    ".jpeg": InputType.image,
    ".png": InputType.image,
    ".gif": InputType.image,
    ".bmp": InputType.image,
    ".tiff": InputType.image,
    ".tif": InputType.image,
    ".webp": InputType.image,
}


def _detect_input_type_from_filename(filename: str) -> InputType:
    """Determine InputType from a filename's extension."""
    import pathlib

    ext = pathlib.Path(filename).suffix.lower()
    return _FILE_EXTENSION_MAP.get(ext, InputType.text)


async def _upload_to_s3(content: bytes, extension: str) -> str:
    """
    Upload raw content bytes to S3.

    Naming convention: content/<YYYY-MM-DD>/<uuid>.<ext>
    Returns the S3 object URL.
    """
    bucket = os.environ["S3_BUCKET_NAME"]
    region = os.environ["S3_REGION"]
    today = date.today().isoformat()
    key = f"content/{today}/{uuid.uuid4()}{extension}"

    logger.debug(
        "ingestion_agent: uploading content to S3",
        extra={"stage": "s3_upload", "bucket": bucket, "key": key},
    )

    session = aioboto3.Session(
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
        region_name=region,
    )

    async with session.client("s3") as s3:
        await s3.put_object(Bucket=bucket, Key=key, Body=content)

    s3_url = f"https://{bucket}.s3.{region}.amazonaws.com/{key}"
    logger.debug(
        "ingestion_agent: S3 upload complete",
        extra={"stage": "s3_upload", "s3_url": s3_url},
    )
    return s3_url


async def _scrape_url(url: str) -> str:
    """Use Tavily extract to scrape and clean article text from a URL."""
    import os
    from tavily import AsyncTavilyClient

    api_key = os.environ["TAVILY_API_KEY"]
    logger.debug(
        "ingestion_agent: scraping URL with Tavily",
        extra={"stage": "tavily_extract", "url": url},
    )

    client = AsyncTavilyClient(api_key=api_key)
    response = await client.extract(urls=[url])

    results = response.get("results", [])
    if not results:
        raise ValueError(f"Tavily extract returned no results for URL: {url}")

    raw_content = results[0].get("raw_content", "")
    logger.debug(
        "ingestion_agent: Tavily extract complete",
        extra={"stage": "tavily_extract", "url": url, "content_length": len(raw_content)},
    )
    return raw_content


async def run_ingestion(
    url: Optional[str] = None,
    text: Optional[str] = None,
    file: Optional[UploadFile] = None,
) -> IngestionResult:
    """
    Main ingestion entry point. Accepts a URL, plain text string, or file upload.

    Exactly one of url, text, or file must be provided.

    Args:
        url: A URL string to scrape and ingest.
        text: A plain text article string.
        file: A FastAPI UploadFile (PDF, DOCX, HTML, MD, RTF, or image).

    Returns:
        IngestionResult with extracted text, metadata, and token count.
    """
    if sum(x is not None for x in (url, text, file)) != 1:
        raise ValueError("Exactly one of url, text, or file must be provided.")

    logger.debug(
        "ingestion_agent: starting ingestion",
        extra={
            "stage": "run_ingestion",
            "input_mode": "url" if url else "text" if text else "file",
        },
    )

    s3_url: Optional[str] = None

    if url is not None:
        # Scrape URL with Tavily, upload raw HTML to S3
        raw_html = await _scrape_url(url)
        raw_bytes = raw_html.encode("utf-8")

        try:
            s3_url = await _upload_to_s3(raw_bytes, ".html")
        except Exception:
            logger.warning(
                "ingestion_agent: S3 upload failed for URL content, continuing without S3",
                extra={"stage": "s3_upload", "url": url},
                exc_info=True,
            )

        result = await extract(
            raw_content=raw_bytes,
            input_type=InputType.url,
            source_url=url,
            s3_url=s3_url,
        )

    elif text is not None:
        # Plain text - no S3 upload
        result = await extract(
            raw_content=text,
            input_type=InputType.text,
        )

    else:
        # File upload
        filename = file.filename or "upload"
        input_type = _detect_input_type_from_filename(filename)
        file_bytes = await file.read()

        import pathlib
        extension = pathlib.Path(filename).suffix.lower() or ".bin"

        try:
            s3_url = await _upload_to_s3(file_bytes, extension)
        except Exception:
            logger.warning(
                "ingestion_agent: S3 upload failed for file, continuing without S3",
                extra={"stage": "s3_upload", "upload_filename": filename},
                exc_info=True,
            )

        result = await extract(
            raw_content=file_bytes,
            input_type=input_type,
            s3_url=s3_url,
        )

    # Set s3_url on content metadata if not already set by extract
    if s3_url and not result.content.s3_url:
        result.content.s3_url = s3_url

    logger.debug(
        "ingestion_agent: extraction complete, proceeding to summarisation",
        extra={
            "stage": "run_ingestion",
            "token_count": result.token_count,
            "language": result.content.original_language,
        },
    )

    # Summarise if needed
    result = await summarise(result)

    logger.debug(
        "ingestion_agent: ingestion pipeline complete",
        extra={
            "stage": "run_ingestion",
            "final_token_count": result.token_count,
            "title": result.content.title,
        },
    )

    # Persist to database
    try:
        await save_content(result)
        logger.debug(
            "ingestion_agent: content saved to database",
            extra={"stage": "run_ingestion", "title": result.content.title},
        )
    except Exception:
        logger.warning(
            "ingestion_agent: failed to save content to database, continuing",
            extra={"stage": "run_ingestion"},
            exc_info=True,
        )

    return result
