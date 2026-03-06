# Ingestion Team - CLAUDE.md

**Piece:** Steps 1-3 (Ingestion, Extraction, Summarisation)
**Main docs:** [../../CLAUDE.md](../../CLAUDE.md)

## Your Scope

| Step | File                  | Purpose                                                                   |
| ---- | --------------------- | ------------------------------------------------------------------------- |
| 1    | `ingestion_agent.py`  | Accept plain text string or file upload (PDF, HTML, MD, DOCX, RTF, image) |
| 2    | `extraction_agent.py` | Extract raw text and article metadata; translate to English if needed     |
| 3    | `summariser.py`       | Summarise if token count > 4000; otherwise pass through unchanged         |

## Technology

- `tavily-extract` (Tavily MCP) - scrape and clean article text from URLs
- Claude `claude-sonnet-4-6` - extract metadata, translate non-English content to English

## Input

Either a URL string or a direct file upload:

| Format | Parser                         |
| ------ | ------------------------------ |
| URL    | `tavily-extract`               |
| PDF    | `pypdf2` or `pdfminer`         |
| DOCX   | `python-docx`                  |
| HTML   | `beautifulsoup4`               |
| MD     | `markdown` + `beautifulsoup4`  |
| RTF    | `striprtf`                     |
| Image  | `Pillow` + `pytesseract` (OCR) |

API keys come from `.env`: `ANTHROPIC_API_KEY`, `TAVILY_API_KEY`

## Output Contract

Produce an `IngestionResult` Pydantic model and add it to `app/models/schemas.py`.
This is passed directly to the Investigation module.

```python
class ArticleMetadata(BaseModel):
    url: Optional[str] = None
    title: Optional[str] = None
    publisher: Optional[str] = None
    date: Optional[date] = None
    author: Optional[str] = None
    section: Optional[str] = None
    is_opinion: bool = False

class IngestionResult(BaseModel):
    article: ArticleMetadata
    text: str        # cleaned, extracted, English-language text
    token_count: int # approximate token count of extracted text
```

## Key Decisions

- For URL input: use `tavily-extract` to scrape and clean the page
- Translate non-English content to English using Claude before passing downstream
- Only summarise if `token_count > 4000`; otherwise pass the full text unchanged
- The `article.is_opinion` flag should be inferred from section/metadata where possible

## Commands

```bash
pip install -r requirements.txt
pytest tests/test_ingestion.py
```