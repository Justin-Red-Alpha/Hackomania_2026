# Database Team - CLAUDE.md

**Piece:** Step 6 (Persist and retrieve analysis results)
**Main docs:** [../../CLAUDE.md](../../CLAUDE.md)

## Your Scope

| File    | Purpose                                           |
| ------- | ------------------------------------------------- |
| `db.py` | Async read/write for analysis results and sources |

## Purpose

- **Write:** Store completed analysis results so future requests for the same URL skip the full pipeline
- **Read:** Check if a URL was already analysed; return the cached result if available

## Required Functions

| Function                     | Signature                                  | Description                    |
| ---------------------------- | ------------------------------------------ | ------------------------------ |
| `get_analysis(url)`          | `async (str) -> Optional[JudgementResult]` | Return cached result or None   |
| `save_analysis(result)`      | `async (JudgementResult) -> None`          | Persist a completed analysis   |
| `get_sources(url)`           | `async (str) -> List[Source]`              | Return known sources for a URL |
| `save_sources(url, sources)` | `async (str, List[Source]) -> None`        | Persist corroborative sources  |

## Schema

Store at minimum per article:

| Column        | Type      | Description                               |
| ------------- | --------- | ----------------------------------------- |
| `article_url` | string PK | Primary lookup key                        |
| `analysis`    | JSON      | Full `JudgementResult` serialised as JSON |
| `analysed_at` | timestamp | When the analysis was completed           |
| `sources`     | JSON      | List of corroborative source URLs         |

## Key Conventions

- Use SQLite for local dev; abstract behind a repository layer for easy swap to Postgres
- All DB functions are `async`
- Connection string loaded from `.env` via `python-dotenv` as `DATABASE_URL`
- Default: `DATABASE_URL=sqlite+aiosqlite:///./analysis.db`
- Models from `app/models/schemas.py` are used for serialisation/deserialisation

## Commands

```bash
pip install -r requirements.txt
pytest tests/test_database.py
```