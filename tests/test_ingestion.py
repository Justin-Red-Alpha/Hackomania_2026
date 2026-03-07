"""
Tests for the ingestion pipeline (Steps 1-3).

Unit tests mock all external calls (Claude, Tavily, S3) so no API keys are needed.
Run with:  pytest tests/test_ingestion.py -v
"""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ingestion.extraction_agent import (
    _approximate_token_count,
    _parse_raw_text,
    extract,
)
from app.ingestion.ingestion_agent import (
    _detect_input_type_from_filename,
    run_ingestion,
)
from app.ingestion.summariser import summarise
from app.models.schemas import ContentMetadata, IngestionResult, InputType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_result(text: str = "Hello world.", token_count: int = 10) -> IngestionResult:
    return IngestionResult(
        content=ContentMetadata(input_type=InputType.text),
        original_text=text,
        text=text,
        token_count=token_count,
    )


def _claude_response(text: str) -> MagicMock:
    """Build a mock Anthropic messages.create response with a single text block."""
    block = MagicMock()
    block.text = text
    response = MagicMock()
    response.content = [block]
    return response


_SAMPLE_META = json.dumps({
    "title": "Test Article",
    "publisher": "Test Publisher",
    "author": "Jane Doe",
    "date": "2026-03-07",
    "section": "Politics",
    "is_opinion": False,
    "language": "en",
})

_SAMPLE_META_CHINESE = json.dumps({
    "title": "测试文章",
    "publisher": None,
    "author": None,
    "date": None,
    "section": None,
    "is_opinion": False,
    "language": "zh",
})


# ---------------------------------------------------------------------------
# Unit tests: _approximate_token_count
# ---------------------------------------------------------------------------

class TestApproximateTokenCount:
    def test_empty_string_returns_one(self):
        assert _approximate_token_count("") == 1

    def test_four_chars_is_one_token(self):
        assert _approximate_token_count("abcd") == 1

    def test_eight_chars_is_two_tokens(self):
        assert _approximate_token_count("abcdefgh") == 2

    def test_large_text(self):
        text = "a" * 16000
        assert _approximate_token_count(text) == 4000


# ---------------------------------------------------------------------------
# Unit tests: _detect_input_type_from_filename
# ---------------------------------------------------------------------------

class TestDetectInputType:
    @pytest.mark.parametrize("filename,expected", [
        ("article.pdf", InputType.pdf),
        ("document.docx", InputType.docx),
        ("page.html", InputType.html),
        ("page.htm", InputType.html),
        ("readme.md", InputType.md),
        ("notes.markdown", InputType.md),
        ("doc.rtf", InputType.rtf),
        ("photo.jpg", InputType.image),
        ("photo.jpeg", InputType.image),
        ("image.png", InputType.image),
        ("UPPER.PDF", InputType.pdf),
        ("noext", InputType.text),
        ("file.xyz", InputType.text),
    ])
    def test_extension_mapping(self, filename, expected):
        assert _detect_input_type_from_filename(filename) == expected


# ---------------------------------------------------------------------------
# Unit tests: _parse_raw_text
# ---------------------------------------------------------------------------

class TestParseRawText:
    def test_plain_text_string_passthrough(self):
        text = "This is a plain article."
        assert _parse_raw_text(text, InputType.text) == text

    def test_plain_text_bytes_decoded(self):
        text = "This is a plain article."
        assert _parse_raw_text(text.encode("utf-8"), InputType.text) == text

    def test_html_strips_tags(self):
        html = b"<html><body><p>Hello world</p></body></html>"
        result = _parse_raw_text(html, InputType.html)
        assert "Hello world" in result
        assert "<p>" not in result

    def test_html_removes_scripts(self):
        html = b"<html><body><script>alert(1)</script><p>Article text</p></body></html>"
        result = _parse_raw_text(html, InputType.html)
        assert "alert" not in result
        assert "Article text" in result

    def test_md_strips_markdown_syntax(self):
        md = b"# Heading\n\nSome **bold** paragraph."
        result = _parse_raw_text(md, InputType.md)
        assert "Heading" in result
        assert "Some" in result
        assert "**" not in result

    def test_rtf_basic(self):
        # Minimal RTF document
        rtf = rb"{\rtf1\ansi Hello RTF world}"
        result = _parse_raw_text(rtf, InputType.rtf)
        assert "Hello" in result or result.strip() != ""


# ---------------------------------------------------------------------------
# Unit tests: summarise (no API call when below threshold)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestSummarise:
    async def test_below_threshold_returns_unchanged(self):
        result = _make_result(text="Short text.", token_count=100)
        out = await summarise(result)
        assert out is result  # same object, not copied

    async def test_at_threshold_returns_unchanged(self):
        result = _make_result(token_count=4000)
        out = await summarise(result)
        assert out is result

    async def test_above_threshold_calls_claude(self):
        long_text = "word " * 20000
        result = _make_result(text=long_text, token_count=5000)

        mock_response = _claude_response("Summarised text.")

        with patch("app.ingestion.summariser.anthropic.AsyncAnthropic") as mock_cls, \
             patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            mock_client = AsyncMock()
            mock_client.messages.create = AsyncMock(return_value=mock_response)
            mock_cls.return_value = mock_client

            out = await summarise(result)

        assert out.text == "Summarised text."
        assert out.original_text == long_text  # original_text never modified
        mock_client.messages.create.assert_awaited_once()

    async def test_above_threshold_updates_token_count(self):
        result = _make_result(text="x" * 40000, token_count=10000)
        summary = "Short summary here."

        mock_response = _claude_response(summary)

        with patch("app.ingestion.summariser.anthropic.AsyncAnthropic") as mock_cls, \
             patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            mock_client = AsyncMock()
            mock_client.messages.create = AsyncMock(return_value=mock_response)
            mock_cls.return_value = mock_client

            out = await summarise(result)

        assert out.token_count == _approximate_token_count(summary)
        assert out.token_count < result.token_count


# ---------------------------------------------------------------------------
# Unit tests: extract (mocked Claude)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestExtract:
    async def test_extract_english_text(self):
        meta_response = _claude_response(_SAMPLE_META)

        with patch("app.ingestion.extraction_agent.anthropic.AsyncAnthropic") as mock_cls, \
             patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            mock_client = AsyncMock()
            mock_client.messages.create = AsyncMock(return_value=meta_response)
            mock_cls.return_value = mock_client

            result = await extract(
                raw_content="This is an English article about politics.",
                input_type=InputType.text,
            )

        assert result.content.title == "Test Article"
        assert result.content.author == "Jane Doe"
        assert result.content.publisher == "Test Publisher"
        assert result.content.date == date(2026, 3, 7)
        assert result.content.original_language == "en"
        assert result.content.is_opinion is False
        assert result.text == result.original_text
        assert result.token_count >= 1
        # No translation call for English
        assert mock_client.messages.create.await_count == 1

    async def test_extract_non_english_triggers_translation(self):
        meta_response = _claude_response(_SAMPLE_META_CHINESE)
        translation_response = _claude_response("Translated English text.")

        with patch("app.ingestion.extraction_agent.anthropic.AsyncAnthropic") as mock_cls, \
             patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            mock_client = AsyncMock()
            mock_client.messages.create = AsyncMock(
                side_effect=[meta_response, translation_response]
            )
            mock_cls.return_value = mock_client

            result = await extract(
                raw_content="这是一篇中文文章。",
                input_type=InputType.text,
            )

        assert result.content.original_language == "zh"
        assert result.text == "Translated English text."
        assert result.original_text == "这是一篇中文文章。"
        assert mock_client.messages.create.await_count == 2  # metadata + translation

    async def test_extract_sets_source_url(self):
        meta_response = _claude_response(_SAMPLE_META)

        with patch("app.ingestion.extraction_agent.anthropic.AsyncAnthropic") as mock_cls, \
             patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            mock_client = AsyncMock()
            mock_client.messages.create = AsyncMock(return_value=meta_response)
            mock_cls.return_value = mock_client

            result = await extract(
                raw_content="<html><body><p>Article</p></body></html>".encode(),
                input_type=InputType.url,
                source_url="https://example.com/article",
                s3_url="https://bucket.s3.us-east-1.amazonaws.com/content/2026-03-07/abc.html",
            )

        assert result.content.url == "https://example.com/article"
        assert result.content.s3_url == "https://bucket.s3.us-east-1.amazonaws.com/content/2026-03-07/abc.html"

    async def test_extract_malformed_metadata_json_gracefully_handled(self):
        """Claude returning malformed JSON should not crash - metadata fields are all None."""
        bad_response = _claude_response("not valid json at all")

        with patch("app.ingestion.extraction_agent.anthropic.AsyncAnthropic") as mock_cls, \
             patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            mock_client = AsyncMock()
            mock_client.messages.create = AsyncMock(return_value=bad_response)
            mock_cls.return_value = mock_client

            result = await extract(raw_content="Some article text.", input_type=InputType.text)

        assert result.content.title is None
        assert result.content.original_language == "en"  # falls back to default
        assert result.original_text == "Some article text."

    async def test_extract_invalid_date_is_ignored(self):
        meta = json.dumps({**json.loads(_SAMPLE_META), "date": "not-a-date"})
        meta_response = _claude_response(meta)

        with patch("app.ingestion.extraction_agent.anthropic.AsyncAnthropic") as mock_cls, \
             patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            mock_client = AsyncMock()
            mock_client.messages.create = AsyncMock(return_value=meta_response)
            mock_cls.return_value = mock_client

            result = await extract(raw_content="Article.", input_type=InputType.text)

        assert result.content.date is None


# ---------------------------------------------------------------------------
# Unit tests: run_ingestion (mocked Claude + Tavily, no S3)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestRunIngestion:
    async def test_exactly_one_input_required(self):
        with pytest.raises(ValueError, match="Exactly one"):
            await run_ingestion()

        with pytest.raises(ValueError, match="Exactly one"):
            await run_ingestion(url="https://x.com", text="also text")

    async def test_plain_text_input(self):
        meta_response = _claude_response(_SAMPLE_META)

        with patch("app.ingestion.extraction_agent.anthropic.AsyncAnthropic") as mock_cls, \
             patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            mock_client = AsyncMock()
            mock_client.messages.create = AsyncMock(return_value=meta_response)
            mock_cls.return_value = mock_client

            result = await run_ingestion(text="A short English article.")

        assert isinstance(result, IngestionResult)
        assert result.content.input_type == InputType.text
        assert result.content.s3_url is None  # no S3 upload for plain text

    async def test_url_input_calls_tavily_and_skips_s3_on_failure(self):
        meta_response = _claude_response(_SAMPLE_META)
        tavily_response = {"results": [{"raw_content": "<html><p>Article body</p></html>"}]}

        with patch("tavily.AsyncTavilyClient") as mock_tavily_cls, \
             patch("anthropic.AsyncAnthropic") as mock_cls, \
             patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key", "TAVILY_API_KEY": "test-tavily"}):
            mock_tavily = AsyncMock()
            mock_tavily.extract = AsyncMock(return_value=tavily_response)
            mock_tavily_cls.return_value = mock_tavily

            mock_client = AsyncMock()
            mock_client.messages.create = AsyncMock(return_value=meta_response)
            mock_cls.return_value = mock_client

            # S3 env vars absent - S3 upload will fail and be skipped gracefully
            result = await run_ingestion(url="https://example.com/article")

        assert result.content.input_type == InputType.url
        assert result.content.url == "https://example.com/article"
        mock_tavily.extract.assert_awaited_once_with(urls=["https://example.com/article"])

    async def test_file_upload_html(self):
        meta_response = _claude_response(_SAMPLE_META)
        html_bytes = b"<html><body><p>File upload content</p></body></html>"

        mock_file = MagicMock()
        mock_file.filename = "article.html"
        mock_file.read = AsyncMock(return_value=html_bytes)

        with patch("app.ingestion.extraction_agent.anthropic.AsyncAnthropic") as mock_cls, \
             patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            mock_client = AsyncMock()
            mock_client.messages.create = AsyncMock(return_value=meta_response)
            mock_cls.return_value = mock_client

            result = await run_ingestion(file=mock_file)

        assert result.content.input_type == InputType.html
        assert "File upload content" in result.original_text

    async def test_summarisation_triggered_when_long(self):
        long_text = "word " * 25000  # ~25k tokens
        meta = json.dumps({**json.loads(_SAMPLE_META), "language": "en"})

        meta_response = _claude_response(meta)
        summary_response = _claude_response("Brief summary of the long article.")

        # Both extraction_agent and summariser share the same anthropic module object,
        # so patch it once and control per-call returns via side_effect.
        with patch("anthropic.AsyncAnthropic") as mock_cls, \
             patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            mock_client = AsyncMock()
            mock_client.messages.create = AsyncMock(
                side_effect=[meta_response, summary_response]
            )
            mock_cls.return_value = mock_client

            result = await run_ingestion(text=long_text)

        assert result.text == "Brief summary of the long article."
        assert result.original_text == long_text
        assert mock_client.messages.create.await_count == 2

    async def test_summarisation_skipped_when_short(self):
        short_text = "A short article."
        meta_response = _claude_response(_SAMPLE_META)

        with patch("anthropic.AsyncAnthropic") as mock_cls, \
             patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            mock_client = AsyncMock()
            mock_client.messages.create = AsyncMock(return_value=meta_response)
            mock_cls.return_value = mock_client

            result = await run_ingestion(text=short_text)

        assert result.text == short_text
        assert mock_client.messages.create.await_count == 1  # only metadata, no summarisation
