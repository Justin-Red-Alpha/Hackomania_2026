---
name: ingestion
description: >
  Implement or run the ingestion pipeline (Steps 1-3) for the Hackomania 2026
  misinformation detection tool. Handles accepting content in any format, extracting
  text and metadata, detecting and translating non-English content, and summarising
  long articles. Trigger this skill whenever working on ingestion_agent.py,
  extraction_agent.py, or summariser.py, or when the user asks to ingest, extract,
  process, translate, or summarise an article, file, or URL for fact-checking.
---

# Ingestion Pipeline

Implements Steps 1-3 of the misinformation detection pipeline:

1. **Accept** - receive content as plain text, file, or URL
2. **Extract** - parse text and metadata using format-appropriate tools
3. **Summarise** - condense if token count exceeds 4000

Files to produce (or modify if they exist):
- `app/ingestion/ingestion_agent.py` - Step 1
- `app/ingestion/extraction_agent.py` - Step 2
- `app/ingestion/summariser.py` - Step 3

Output model lives in `app/models/schemas.py`. Consult `app/ingestion/CLAUDE.md` and
`DATABASE.md` for the full spec before writing any code.

---

## Step 1 - Determine Input Type

Classify the incoming input. Use `InputType` enum values from `app/models/schemas.py`.

| Condition | InputType |
|-----------|-----------|
| Starts with `http://` or `https://` and extension is `.pdf`, `.docx`, `.html`, `.md`, `.rtf` | Use that extension's type |
| Starts with `http://` or `https://` (no file extension, or `.htm`) | `url` (webpage) |
| File path with known extension | Match extension to enum |
| Raw string with no path or URL | `text` |

Produce a UUID for the record. All assets saved to S3 use this UUID.

---

## Step 2 - Save the Source Asset to S3

S3 path convention (from `DATABASE.md`):
```
s3://<S3_BUCKET_NAME>/content/<YYYY-MM-DD>/<uuid>.<ext>
```

| Input type | What to save | Extension |
|------------|-------------|-----------|
| `url` (webpage) | Raw HTML string returned by `tavily-extract` | `.html` |
| `pdf`, `docx`, `rtf`, `md`, `html` (file) | Original file bytes | Original extension |
| URL to a file | Download bytes, save as original extension | Original extension |
| `image` | Original image bytes | Original extension |
| `text` | Nothing — `s3_url` stays `None` | — |

Use `aioboto3` for all uploads. Set `content.s3_url` to the resulting object URL after upload.

---

## Step 3 - Extract Text

Use the appropriate parser for each format. All parsers return a plain-text string.

| InputType | Library | Notes |
|-----------|---------|-------|
| `url` | `tavily-extract` (Tavily MCP) | Also sets `title`, `publisher`, `date`, `author` from returned metadata |
| `pdf` | `pdfminer.six` (`extract_text`) or `pypdf2` | Fall back to pypdf2 if pdfminer fails |
| `docx` | `python-docx` (`Document.paragraphs`) | Join paragraphs with newlines |
| `html` | `beautifulsoup4` (`get_text(separator="\n")`) | Strip scripts and styles first |
| `md` | `markdown` library → HTML → `beautifulsoup4` | |
| `rtf` | `striprtf` (`rtf_to_text`) | |
| `image` | Claude vision (`claude-sonnet-4-6`) | Send image bytes; prompt: "Extract all text visible in this image. Return only the extracted text, nothing else." If no text detected, return empty string. |
| `text` | Pass through unchanged | |

For `url` inputs, if `tavily-extract` returns structured metadata (title, author, publish date,
publisher), populate the corresponding `ContentMetadata` fields from that data.

---

## Step 4 - Detect Language and Translate

Use `claude-sonnet-4-6` with a single prompt that both detects and (if needed) translates.

Prompt pattern:
```
Detect the language of the following text and return a JSON object with two fields:
- "language": BCP-47 language code (e.g. "en", "zh", "ms", "ta")
- "translation": the full English translation of the text, or the original text verbatim if it is already English

Text:
<text>
```

Set `content.original_language` to the detected BCP-47 code.
Store the raw extracted text as `original_text` (never modify this).
Store the English result as `text` (either the translation or the original if already English).

If content is partially non-English (e.g., mixed-language article), translate the whole thing.

---

## Step 5 - Count Tokens and Summarise

Estimate token count:
```python
token_count = len(text.split()) * 4 // 3   # rough approximation
```

If `token_count > 4000`, summarise using `claude-sonnet-4-6`:

```
Summarise the following article in clear, factual English. Preserve all specific claims,
statistics, names, dates, and source attributions. Do not add opinions or interpretation.
Target length: 800-1200 words.

Article:
<text>
```

Replace `text` with the summary. Keep `original_text` and `token_count` unchanged —
`token_count` reflects the pre-summary length so downstream agents know the article was long.

Do NOT summarise `original_text`. It must always be the full extracted original.

---

## Step 6 - Populate ContentMetadata

```python
content = ContentMetadata(
    input_type=input_type,
    url=source_url,           # None unless input was a URL
    s3_url=s3_url,            # None for plain text input
    title=title,              # from tavily metadata or None
    publisher=publisher,      # from tavily metadata or None
    date=pub_date,            # from tavily metadata or None
    author=author,            # from tavily metadata or None
    section=section,          # from tavily metadata or None
    is_opinion=is_opinion,    # infer from section/tags if possible
    original_language=lang,   # BCP-47 code from Step 4
)
```

`is_opinion` should be `True` when the section/category field contains words like
"opinion", "editorial", "commentary", "letters", "analysis".

---

## Output Contract

Return an `IngestionResult` (defined in `app/models/schemas.py`):

```python
class InputType(str, Enum):
    url   = "url"
    text  = "text"
    pdf   = "pdf"
    docx  = "docx"
    html  = "html"
    md    = "md"
    rtf   = "rtf"
    image = "image"

class ContentMetadata(BaseModel):
    input_type: InputType
    url: Optional[str] = None
    s3_url: Optional[str] = None
    title: Optional[str] = None
    publisher: Optional[str] = None
    date: Optional[date] = None
    author: Optional[str] = None
    section: Optional[str] = None
    is_opinion: bool = False
    original_language: str = "en"      # BCP-47 code

class IngestionResult(BaseModel):
    content: ContentMetadata
    original_text: str                 # full extracted text, original language, never summarised
    text: str                          # English text, summarised if token_count > 4000
    token_count: int                   # token count of `text` BEFORE summarisation
```

---

## Key Conventions

- All functions in these files must be `async`.
- Load all secrets from `.env` via `python-dotenv`: `ANTHROPIC_API_KEY`, `TAVILY_API_KEY`,
  `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `S3_BUCKET_NAME`, `S3_REGION`.
- Log every step at `DEBUG` level using Python's `logging` module with timestamps and the
  content UUID, e.g.: `logger.debug("[ingestion] %s: extracted %d chars", uuid, len(text))`
- Use `claude-sonnet-4-6` for all Claude calls.
- Do not hardcode any configuration values; read from `.env` or `app/config.py`.
