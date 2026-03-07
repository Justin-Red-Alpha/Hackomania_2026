# Ingestion Team - CLAUDE.md

**Piece:** Steps 1-3 (Ingestion, Extraction, Summarisation)
**Main docs:** [../../CLAUDE.md](../../CLAUDE.md)

## Your Scope

| Step | File                  | Purpose                                                                   |
| ---- | --------------------- | ------------------------------------------------------------------------- |
| 1    | `ingestion_agent.py`  | Accept plain text string or file upload (PDF, HTML, MD, DOCX, RTF, image) |
| 2    | `extraction_agent.py` | Extract raw text and metadata; upload content to S3; translate if needed  |
| 3    | `summariser.py`       | Summarise if token count > 4000; otherwise pass through unchanged         |

## Technology

- `trafilatura` - fetch and extract article text from URLs
- Claude `claude-sonnet-4-6` - extract metadata, detect language, translate to English
- `aioboto3` - async upload of content files to AWS S3

## Input

Either a URL string or a direct file upload:

| Format     | Parser                         |
| ---------- | ------------------------------ |
| URL        | `trafilatura`                  |
| PDF        | `pypdf2` or `pdfminer`         |
| DOCX       | `python-docx`                  |
| HTML       | `beautifulsoup4`               |
| MD         | `markdown` + `beautifulsoup4`  |
| RTF        | `striprtf`                     |
| Image      | `Pillow` + `pytesseract` (OCR) |
| Plain text | (no parser needed)             |

API keys come from `.env`: `ANTHROPIC_API_KEY`, `TAVILY_API_KEY`, `AWS_ACCESS_KEY_ID`,
`AWS_SECRET_ACCESS_KEY`, `S3_BUCKET_NAME`, `S3_REGION`

## Output Contract

Produce an `IngestionResult` Pydantic model and add it to `app/models/schemas.py`.
This is passed directly to the Investigation module.

```python
from enum import Enum

class InputType(str, Enum):
    url   = "url"
    text  = "text"
    pdf   = "pdf"
    docx  = "docx"
    html  = "html"
    md    = "md"
    rtf   = "rtf"
    image = "image"

class ContentMetadata(BaseModel):       # was: ArticleMetadata
    input_type: InputType               # how the content was provided
    url: Optional[str] = None           # original URL if input was a URL
    s3_url: Optional[str] = None        # S3 link to stored content file
    title: Optional[str] = None
    publisher: Optional[str] = None
    date: Optional[date] = None
    author: Optional[str] = None
    section: Optional[str] = None
    is_opinion: bool = False
    original_language: str = "en"       # BCP-47 language code, e.g. "en", "zh", "ms"

class IngestionResult(BaseModel):
    content: ContentMetadata            # was: article: ArticleMetadata
    original_text: str                  # extracted text in its original language
    text: str                           # cleaned English-language text (translated if needed)
    token_count: int                    # approximate token count of `text`
```

## Key Decisions

### Input handling
- For URL input: use `trafilatura` to fetch and extract text; save the raw HTML to S3
- For file input: save the original file bytes directly to S3
- For plain text input: no S3 upload (no file to store; `s3_url` remains `None`)
- Set `content.s3_url` to the resulting S3 object URL after upload

### S3 naming convention

See [DATABASE.md](../../DATABASE.md) for the full S3 naming and storage conventions.
Content is stored at `s3://<S3_BUCKET_NAME>/content/<YYYY-MM-DD>/<uuid>.<ext>`.
Use `.html` for URL-scraped pages and the original file extension for uploads.

### Language and translation
- Detect the language of the extracted text using Claude
- Store the detected language in `content.original_language` (BCP-47 code)
- If not English, translate to English and store both:
  - `original_text` - the text as extracted (original language)
  - `text` - the English translation
- If already English, both `original_text` and `text` hold the same value

### Summarisation
- Only summarise if `token_count > 4000`; otherwise `text` is passed through unchanged
- `original_text` is never summarised - always the full extracted original
- The `article.is_opinion` flag should be inferred from section/metadata where possible

## Commands

```bash
pip install -r requirements.txt
pytest tests/test_ingestion.py
```