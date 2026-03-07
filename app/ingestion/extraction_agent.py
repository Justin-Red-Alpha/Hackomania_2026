"""
Step 2: Extract raw text and metadata from raw content.
Detects language and translates non-English content to English.
"""

from __future__ import annotations

import json
import logging
from datetime import date as Date, datetime
from io import BytesIO
from typing import Optional

import anthropic

from app.models.schemas import ContentMetadata, IngestionResult, InputType

logger = logging.getLogger(__name__)

_METADATA_SYSTEM_PROMPT = """\
You are a metadata extractor for news articles and web content.
Given article text, extract the following fields and return them as a single JSON object:
{
  "title": string or null,
  "publisher": string or null,
  "author": string or null,
  "date": "YYYY-MM-DD" string or null,
  "section": string or null,
  "is_opinion": boolean,
  "language": "BCP-47 language code, e.g. en, zh, ms, fr"
}

For "language", detect the primary language of the provided text.
For "is_opinion", set true if the content is an editorial, opinion, column, or commentary.
Return ONLY the JSON object with no additional text.
"""

_TRANSLATE_SYSTEM_PROMPT = """\
You are a professional translator.
Translate the following article text to English.
Preserve the original structure, tone, and meaning.
Return ONLY the translated text with no preamble or explanation.
"""


def _approximate_token_count(text: str) -> int:
    """Approximate token count using character-based heuristic (4 chars per token)."""
    return max(1, len(text) // 4)


def _parse_raw_text(raw_content: bytes | str, input_type: InputType) -> str:
    """Parse raw content bytes into plain text based on input type."""
    logger.debug(
        "extraction_agent: parsing content",
        extra={"stage": "parse", "input_type": input_type.value},
    )

    if input_type == InputType.text:
        return raw_content if isinstance(raw_content, str) else raw_content.decode("utf-8", errors="replace")

    if isinstance(raw_content, str):
        raw_bytes = raw_content.encode("utf-8")
    else:
        raw_bytes = raw_content

    if input_type == InputType.pdf:
        return _parse_pdf(raw_bytes)
    if input_type == InputType.docx:
        return _parse_docx(raw_bytes)
    if input_type in (InputType.html, InputType.url):
        return _parse_html(raw_bytes)
    if input_type == InputType.md:
        return _parse_md(raw_bytes)
    if input_type == InputType.rtf:
        return _parse_rtf(raw_bytes)
    if input_type == InputType.image:
        return _parse_image(raw_bytes)

    return raw_bytes.decode("utf-8", errors="replace")


def _parse_pdf(data: bytes) -> str:
    import pypdf

    reader = pypdf.PdfReader(BytesIO(data))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n\n".join(pages).strip()


def _parse_docx(data: bytes) -> str:
    from docx import Document

    doc = Document(BytesIO(data))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    return "\n\n".join(paragraphs)


def _parse_html(data: bytes) -> str:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(data, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    return soup.get_text(separator="\n", strip=True)


def _parse_md(data: bytes) -> str:
    import markdown
    from bs4 import BeautifulSoup

    html = markdown.markdown(data.decode("utf-8", errors="replace"))
    soup = BeautifulSoup(html, "html.parser")
    return soup.get_text(separator="\n", strip=True)


def _parse_rtf(data: bytes) -> str:
    from striprtf.striprtf import rtf_to_text

    return rtf_to_text(data.decode("utf-8", errors="replace"))


def _parse_image(data: bytes) -> str:
    from PIL import Image
    import pytesseract

    image = Image.open(BytesIO(data))
    return pytesseract.image_to_string(image)


async def _extract_metadata(client: anthropic.AsyncAnthropic, text: str) -> dict:
    """Call Claude to extract structured metadata and detect language from article text."""
    logger.debug(
        "extraction_agent: calling Claude for metadata extraction",
        extra={"stage": "metadata_extraction", "text_preview": text[:100]},
    )

    sample = text[:6000]
    response = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        system=_METADATA_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": sample}],
    )

    raw_json = response.content[0].text.strip()
    # Strip markdown code fences that Claude sometimes wraps around JSON
    if raw_json.startswith("```"):
        raw_json = raw_json.split("```", 2)[1]
        if raw_json.startswith("json"):
            raw_json = raw_json[4:]
        raw_json = raw_json.strip()
    logger.debug(
        "extraction_agent: received metadata from Claude",
        extra={"stage": "metadata_extraction", "raw_json": raw_json},
    )

    try:
        return json.loads(raw_json)
    except json.JSONDecodeError:
        logger.warning(
            "extraction_agent: failed to parse Claude metadata JSON, returning empty metadata",
            extra={"stage": "metadata_extraction", "raw": raw_json},
        )
        return {}


# Max characters per translation chunk (~5000 tokens input; leaves headroom for 8192-token output)
_TRANSLATE_CHUNK_CHARS = 20_000


def _split_into_chunks(text: str, max_chars: int) -> list[str]:
    """Split text into chunks at paragraph boundaries without exceeding max_chars."""
    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for para in paragraphs:
        para_len = len(para) + 2  # account for the "\n\n" separator
        if current_len + para_len > max_chars and current:
            chunks.append("\n\n".join(current))
            current = [para]
            current_len = para_len
        else:
            current.append(para)
            current_len += para_len

    if current:
        chunks.append("\n\n".join(current))

    return chunks


async def _translate_chunk(client: anthropic.AsyncAnthropic, text: str) -> str:
    """Translate a single chunk of text to English."""
    response = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8192,
        system=_TRANSLATE_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": text}],
    )
    return response.content[0].text.strip()


async def _translate_to_english(client: anthropic.AsyncAnthropic, text: str) -> str:
    """Translate article text to English, chunking long texts to stay within output token limits."""
    logger.debug(
        "extraction_agent: calling Claude for translation",
        extra={"stage": "translation", "text_length": len(text)},
    )

    if len(text) <= _TRANSLATE_CHUNK_CHARS:
        translated = await _translate_chunk(client, text)
        logger.debug(
            "extraction_agent: translation complete",
            extra={"stage": "translation", "translated_length": len(translated)},
        )
        return translated

    chunks = _split_into_chunks(text, _TRANSLATE_CHUNK_CHARS)
    logger.debug(
        "extraction_agent: text too long for single translation, splitting into chunks",
        extra={"stage": "translation", "chunk_count": len(chunks), "text_length": len(text)},
    )
    translated_chunks: list[str] = []
    for i, chunk in enumerate(chunks):
        logger.debug(
            "extraction_agent: translating chunk",
            extra={"stage": "translation", "chunk_index": i + 1, "total_chunks": len(chunks)},
        )
        translated_chunks.append(await _translate_chunk(client, chunk))

    translated = "\n\n".join(translated_chunks)
    logger.debug(
        "extraction_agent: chunked translation complete",
        extra={
            "stage": "translation",
            "chunk_count": len(chunks),
            "translated_length": len(translated),
        },
    )
    return translated


async def extract(
    raw_content: bytes | str,
    input_type: InputType,
    source_url: Optional[str] = None,
    s3_url: Optional[str] = None,
) -> IngestionResult:
    """
    Parse raw content, extract metadata via Claude, detect and translate language.

    Args:
        raw_content: Raw bytes or string content to process.
        input_type: The type of input content.
        source_url: Original URL if input was a URL.
        s3_url: S3 object URL of the stored content file (if uploaded).

    Returns:
        IngestionResult with extracted text, metadata, and token count.
    """
    import os
    from dotenv import load_dotenv

    load_dotenv()
    api_key = os.environ["ANTHROPIC_API_KEY"]

    logger.debug(
        "extraction_agent: starting extraction",
        extra={"stage": "extract", "input_type": input_type.value, "source_url": source_url},
    )

    # Step 1: Parse raw content to plain text
    raw_text = _parse_raw_text(raw_content, input_type)
    logger.debug(
        "extraction_agent: raw text extracted",
        extra={"stage": "parse", "raw_text_length": len(raw_text)},
    )

    # Step 2: Extract metadata and detect language using Claude
    async_client = anthropic.AsyncAnthropic(api_key=api_key)
    meta = await _extract_metadata(async_client, raw_text)

    language = meta.get("language", "en") or "en"
    logger.debug(
        "extraction_agent: detected language",
        extra={"stage": "language_detection", "language": language},
    )

    # Step 3: Translate to English if needed
    if language.lower().startswith("en"):
        english_text = raw_text
    else:
        logger.debug(
            "extraction_agent: non-English content detected, translating",
            extra={"stage": "translation", "language": language},
        )
        english_text = await _translate_to_english(async_client, raw_text)

    # Step 4: Parse publication date
    pub_date: Optional[Date] = None
    raw_date = meta.get("date")
    if raw_date:
        try:
            pub_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
        except ValueError:
            logger.warning(
                "extraction_agent: could not parse date string",
                extra={"stage": "metadata_extraction", "raw_date": raw_date},
            )

    content = ContentMetadata(
        input_type=input_type,
        url=source_url,
        s3_url=s3_url,
        title=meta.get("title"),
        publisher=meta.get("publisher"),
        date=pub_date,
        author=meta.get("author"),
        section=meta.get("section"),
        is_opinion=bool(meta.get("is_opinion", False)),
        original_language=language,
    )

    token_count = _approximate_token_count(english_text)

    logger.debug(
        "extraction_agent: extraction complete",
        extra={
            "stage": "extract",
            "input_type": input_type.value,
            "language": language,
            "token_count": token_count,
            "title": content.title,
        },
    )

    return IngestionResult(
        content=content,
        original_text=raw_text,
        text=english_text,
        token_count=token_count,
    )
