# CHANGELOG

All significant changes to this project must be recorded here.
Format: `YYYY-MM-DD | Author | Description`

---

## 2026-03-07 | Justin | Ingestion pipeline implementation

- Implemented ingestion pipeline Steps 1-3:
  - `app/ingestion/ingestion_agent.py`: entry point accepting URL (Tavily extract), plain text, or
    file upload; uploads raw content to S3 (`content/<YYYY-MM-DD>/<uuid>.<ext>`)
  - `app/ingestion/extraction_agent.py`: parses PDF/DOCX/HTML/MD/RTF/image via format-specific
    libraries; calls Claude to extract metadata (title, publisher, author, date, section,
    is_opinion) and detect language; translates non-English content to English
  - `app/ingestion/summariser.py`: summarises English text via Claude when token_count > 4000;
    original_text is never summarised
- Added `app/models/schemas.py` with `InputType`, `ContentMetadata`, and `IngestionResult` Pydantic models
- Added `requirements.txt` with all project dependencies
- Added `app/__init__.py`, `app/models/__init__.py`, `app/ingestion/__init__.py` package init files

## 2026-03-07

- Moved `original_language` from `IngestionResult` into `ContentMetadata`; field now lives
  on the content object where it belongs (aligns with the `content` DB table column).
  Default value is `"en"`. Frontend `content` response schema updated to include `original_language`.

- Added `General Directives` section to `CLAUDE.md`: no emojis, regular commits, SSE streaming,
  verbose logging, and CHANGELOG maintenance.
- Added `evidence` table to database schema (`app/database/CLAUDE.md`).
- Added `ClaimEvidence` and `JudgedClaim` models to judgement output contract
  (`app/judgement/CLAUDE.md`); scoring algorithm now includes evidence identification step.
- Renamed `ArticleCredibility` -> `ContentCredibility` and `article_credibility` ->
  `content_credibility` across all team CLAUDE.md files.
- Renamed DB table `content_metadata` -> `content`; added `s3_url` and `extracted_text` columns
  to `sources` table for archival of corroborative source pages.
- Ingestion updated to return `original_text` (original language) alongside translated `text`;
  renamed `ArticleMetadata` -> `ContentMetadata`; added `input_type` field; S3 upload of content
  files with naming convention `content/<YYYY-MM-DD>/<uuid>.<ext>`.
- Project split into 5 independent team pieces, each with its own `CLAUDE.md`.