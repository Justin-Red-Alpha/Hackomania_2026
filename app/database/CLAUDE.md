# Database Team - CLAUDE.md

**Piece:** Step 6 (Persist and retrieve analysis results and content)
**Main docs:** [../../CLAUDE.md](../../CLAUDE.md)
**Database schema:** [../../DATABASE.md](../../DATABASE.md)

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

See [DATABASE.md](../../DATABASE.md) for the full table schemas, S3 naming conventions,
entity relationships, and write responsibilities for each pipeline stage.

## Key Conventions

- Use SQLite for local dev; abstract behind a repository layer for easy swap to Postgres
- All DB functions are `async`
- Connection string loaded from `.env` via `python-dotenv` as `DATABASE_URL`
- Default: `DATABASE_URL=sqlite+aiosqlite:///./analysis.db`
- S3 credentials from `.env`: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `S3_BUCKET_NAME`
- Models from `app/models/schemas.py` are used for serialisation/deserialisation

## Commands

```bash
pip install -r requirements.txt
pytest tests/test_database.py
```