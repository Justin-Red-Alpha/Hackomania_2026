# DATABASE.md

Central reference for the project's database schema and S3 storage conventions.
All teams must consult this file when working with data persistence.
The Database team's implementation details are in [app/database/CLAUDE.md](app/database/CLAUDE.md).

## Tables

### Table: `content`

Stores ingested content and metadata. Primary lookup key is the original URL (or a generated
UUID for non-URL inputs such as file uploads or plain text).

| Column              | Type      | Description                                                       |
| ------------------- | --------- | ----------------------------------------------------------------- |
| `id`                | string PK | UUID; primary lookup key                                          |
| `input_type`        | string    | Enum: `url`, `text`, `pdf`, `docx`, `html`, `md`, `rtf`, `image` |
| `source_url`        | string    | Original URL if input was a URL, else NULL                        |
| `s3_url`            | string    | S3 object URL of stored content file (NULL for plain text input)  |
| `title`             | string    | Extracted article title                                           |
| `publisher`         | string    | Extracted publisher name                                          |
| `author`            | string    | Extracted author name                                             |
| `date`              | date      | Extracted publication date                                        |
| `section`           | string    | Article section/category                                          |
| `is_opinion`        | boolean   | True if content is an opinion piece                               |
| `original_language` | string    | BCP-47 language code of the original content, e.g. `en`, `zh`    |
| `original_text`     | text      | Full extracted text in original language                          |
| `translated_text`   | text      | English translation (same as `original_text` if already English)  |
| `ingested_at`       | timestamp | When the content was ingested                                     |

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

### Table: `evidence`

Stores the specific evidence snippets identified by the judgement agent that link
individual claims to the source text that supports or contradicts them.

| Column             | Type      | Description                                               |
| ------------------ | --------- | --------------------------------------------------------- |
| `id`               | string PK | UUID                                                      |
| `content_id`       | string FK | References `content.id`                                   |
| `claim_id`         | integer   | Matches `JudgedClaim.claim_id`                            |
| `source_id`        | string FK | References `sources.id`                                   |
| `snippet`          | text      | Verbatim excerpt from `sources.extracted_text`            |
| `supports_claim`   | boolean   | True = snippet supports the claim; False = contradicts it |
| `judgement_reason` | text      | Explanation of how this snippet affects the claim verdict |

## Entity Relationships

```
content  (1) ---- (many) analysis
content  (1) ---- (many) sources
content  (1) ---- (many) evidence
sources  (1) ---- (many) evidence
```

## S3 Naming Conventions

### Ingested content

```
s3://<S3_BUCKET_NAME>/content/<YYYY-MM-DD>/<uuid>.<ext>
```

| Input type      | Extension                             | Written by       |
| --------------- | ------------------------------------- | ---------------- |
| URL (scraped)   | `.html`                               | Ingestion team   |
| File upload     | Original extension (`.pdf`, `.docx`)  | Ingestion team   |
| Plain text      | No upload; `s3_url` is NULL           | -                |

### Source HTML archives

```
s3://<S3_BUCKET_NAME>/sources/<YYYY-MM-DD>/<uuid>.html
```

Uploading is performed by `search_agent.py` (Investigation team).
The resulting URL is stored in `sources.s3_url`.

## Write Responsibilities

| Table      | Written by             | When                                                   |
| ---------- | ---------------------- | ------------------------------------------------------ |
| `content`  | Ingestion pipeline     | Immediately after extraction completes                 |
| `analysis` | Judgement pipeline     | After scoring and verdict generation complete          |
| `sources`  | Investigation pipeline | After all agents complete                              |
| `evidence` | Judgement pipeline     | Alongside `analysis`, one row per evidence snippet     |

## Environment Variables

| Variable                | Default                                   | Description                           |
| ----------------------- | ----------------------------------------- | ------------------------------------- |
| `DATABASE_URL`          | `sqlite+aiosqlite:///./analysis.db`       | SQLite (dev) or Postgres (prod) URL   |
| `AWS_ACCESS_KEY_ID`     | -                                         | AWS credentials for S3 access         |
| `AWS_SECRET_ACCESS_KEY` | -                                         | AWS credentials for S3 access         |
| `S3_BUCKET_NAME`        | -                                         | Target S3 bucket name                 |
| `S3_REGION`             | -                                         | AWS region for the S3 bucket          |

## Key Conventions

- Use SQLite for local dev; abstract behind a repository layer for easy swap to Postgres
- All database functions are `async`
- Connection string loaded from `.env` via `python-dotenv`
- Models from `app/models/schemas.py` are used for serialisation/deserialisation