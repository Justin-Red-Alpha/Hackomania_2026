# Database Team - CLAUDE.md

**Piece:** Step 6 (Persist and retrieve analysis results and content)
**Main docs:** [../../CLAUDE.md](../../CLAUDE.md)

## Your Scope

| File    | Purpose                                                     |
| ------- | ----------------------------------------------------------- |
| `db.py` | Async read/write for content, analysis results, and sources |

## Technology

- `aiosqlite` / `asyncpg` - async database driver
- `aioboto3` - async AWS S3 client (for reading S3 links; uploads are done by each respective team)

## Purpose

- **Write:** Persist ingested content and completed analysis results so future requests for
  the same URL skip the full pipeline
- **Read:** Check if a URL was already analysed; return the cached result if available

## Required Functions

| Function                  | Signature                                  | Description                          |
| ------------------------- | ------------------------------------------ | ------------------------------------ |
| `get_content(url)`        | `async (str) -> Optional[ContentMetadata]` | Return stored content record or None |
| `save_content(result)`    | `async (IngestionResult) -> None`          | Persist content record + both texts  |
| `get_analysis(url)`       | `async (str) -> Optional[JudgementResult]` | Return cached analysis or None       |
| `save_analysis(result)`   | `async (JudgementResult) -> None`          | Persist a completed analysis         |
| `get_sources(url)`        | `async (str) -> List[Source]`              | Return known sources for a URL       |
| `save_sources(url, srcs)` | `async (str, List[Source]) -> None`        | Persist corroborative sources        |

## Database Schema

### Table: `content`

Stores ingested content and metadata. Primary lookup key is the original URL (or a generated
UUID for non-URL inputs such as file uploads or plain text).

| Column              | Type      | Description                                                      |
| ------------------- | --------- | ---------------------------------------------------------------- |
| `id`                | string PK | UUID; primary lookup key                                         |
| `input_type`        | string    | Enum: `url`, `text`, `pdf`, `docx`, `html`, `md`, `rtf`, `image` |
| `source_url`        | string    | Original URL if input was a URL, else NULL                       |
| `s3_url`            | string    | S3 object URL of stored content file (NULL for plain text input) |
| `title`             | string    | Extracted article title                                          |
| `publisher`         | string    | Extracted publisher name                                         |
| `author`            | string    | Extracted author name                                            |
| `date`              | date      | Extracted publication date                                       |
| `section`           | string    | Article section/category                                         |
| `is_opinion`        | boolean   | True if content is an opinion piece                              |
| `original_language` | string    | BCP-47 language code of the original content, e.g. `en`, `zh`    |
| `original_text`     | text      | Full extracted text in original language                         |
| `translated_text`   | text      | English translation (same as `original_text` if already English) |
| `ingested_at`       | timestamp | When the content was ingested                                    |

### Table: `analysis`

Stores completed judgement results, linked to `content`.

| Column        | Type      | Description                               |
| ------------- | --------- | ----------------------------------------- |
| `id`          | string PK | UUID                                      |
| `content_id`  | string FK | References `content.id`                   |
| `source_url`  | string    | Denormalised URL for fast lookup          |
| `analysis`    | JSON      | Full `JudgementResult` serialised as JSON |
| `analysed_at` | timestamp | When the analysis was completed           |

### Table: `sources`

Stores corroborative sources found during investigation, including their full extracted
content and an S3-hosted HTML copy for archival.

| Column           | Type      | Description                                                  |
| ---------------- | --------- | ------------------------------------------------------------ |
| `id`             | string PK | UUID                                                         |
| `content_id`     | string FK | References `content.id`                                      |
| `claim_id`       | integer   | The claim this source supports or contradicts                |
| `name`           | string    | Source publication name                                      |
| `url`            | string    | Source article URL                                           |
| `source_type`    | string    | Source type: `news`, `government`, `academic`, etc           |
| `is_independent` | boolean   | Whether source is independent of the original publisher      |
| `s3_url`         | string    | S3 object URL of the archived HTML copy of the source page   |
| `extracted_text` | text      | Full text extracted from the source page by `tavily-extract` |

### S3 Naming Convention for Sources

Source HTML archives follow the same convention as ingested content:

```
s3://<S3_BUCKET_NAME>/sources/<YYYY-MM-DD>/<uuid>.html
```

Uploading is performed by `search_agent.py` (Investigation team); this table records the link.

## Key Conventions

- Use SQLite for local dev; abstract behind a repository layer for easy swap to Postgres
- All DB functions are `async`
- Connection string loaded from `.env` via `python-dotenv` as `DATABASE_URL`
- Default: `DATABASE_URL=sqlite+aiosqlite:///./analysis.db`
- S3 credentials from `.env`: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `S3_BUCKET_NAME`
- Models from `app/models/schemas.py` are used for serialisation/deserialisation
- `save_content` is called by the Ingestion pipeline immediately after extraction
- `save_analysis` is called by the Judgement pipeline after scoring is complete
- `save_sources` is called by the Investigation pipeline after all agents complete

## Commands

```bash
pip install -r requirements.txt
pytest tests/test_database.py
```