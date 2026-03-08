# FactGuard

AI-powered misinformation and fake news detection. Submit a news article by URL, file upload, or pasted text; a multi-agent pipeline analyses it and returns a credibility verdict with per-claim evidence breakdowns.

Built for Hackomania 2026.

---

## Table of Contents

1. [Architecture](#architecture)
2. [Pipeline Stages](#pipeline-stages)
3. [Project Structure](#project-structure)
4. [Tech Stack](#tech-stack)
5. [Setup](#setup)
6. [Running the Service](#running-the-service)
7. [API Reference](#api-reference)
8. [Response Schema](#response-schema)
9. [Scoring Algorithm](#scoring-algorithm)
10. [Configuration Reference](#configuration-reference)
11. [Database Schema](#database-schema)
12. [Development](#development)

---

## Architecture

```
Browser / API client
        |
        v
POST /api/v1/analyse  (JSON body)
POST /api/v1/analyse/upload  (multipart file)
        |
        v
  [0] DB cache lookup  ──── hit ──> return cached JudgementResult
        |
       miss
        |
        v
  [1-3] Ingestion
        ingestion_agent.py  →  extraction_agent.py  →  summariser.py
        |
        v
  [4] Investigation  (parallel via asyncio.gather)
        ├── search_agent.py      — find & rank corroborating/contradicting sources
        ├── fakeness_agent.py    — AI-generated content detection (GPTZero)
        ├── statistics_agent.py  — verify cited statistics
        └── source_checker.py   — publisher/author credibility
        |
        v
  [5] Judgement
        judgement.py  →  JudgementResult (scores, per-claim verdicts, evidence)
        |
        v
  [6] Database
        save analysis + sources + evidence
        |
        v
  Return JudgementResult to client
```

---

## Pipeline Stages

### Stage 1-3: Ingestion

Handled by `app/ingestion/`.

**Input accepted:**

| Format     | Parser                        |
| ---------- | ----------------------------- |
| URL        | `trafilatura`                 |
| PDF        | `pypdf`                       |
| DOCX       | `python-docx`                 |
| HTML       | `beautifulsoup4`              |
| MD         | `markdown` + `beautifulsoup4` |
| RTF        | `striprtf`                    |
| Image      | Claude vision (OCR)           |
| Plain text | (pass-through)                |

**Steps:**
1. `ingestion_agent.py` — accepts URL, uploaded file, or plain text; uploads raw content to S3
2. `extraction_agent.py` — extracts text and metadata (title, publisher, author, date, section, opinion flag); detects language with Claude; translates non-English content to English
3. `summariser.py` — summarises with Claude if token count exceeds 4000; otherwise passes through unchanged

**Language handling:** `original_text` always holds the source language; `text` holds English (translated or identical).

---

### Stage 4: Investigation

Handled by `app/investigation/`. All four sub-agents run concurrently via `asyncio.gather`.

#### Search Agent (`search_agent.py`)

Uses Tavily to search for sources per claim, then chases citations back to primary sources.

**Evidence tiers:**

| Tier | Description | Action |
| ---- | ----------- | ------ |
| Primary | Provides its own data, analysis, or official statement | Keep (`is_primary_source=True`) |
| Secondary — with citation | Relays a claim and cites a specific URL | Follow cited URL up to `MAX_HOP_DEPTH` |
| Mention-only | Relays a claim with no citation trail | Discard |

**Binary file extraction:** URLs ending in `.pdf`, `.csv`, `.xlsx`, or `.xls` are downloaded directly via `httpx` and parsed before classification (trafilatura cannot extract these formats).

| Extension      | Parser                                              |
| -------------- | --------------------------------------------------- |
| `.pdf`         | `pypdf` — pages joined with `\n\n`                  |
| `.csv`         | stdlib `csv` — rows as comma-separated lines        |
| `.xlsx`/`.xls` | `openpyxl` — each sheet's rows as comma-separated lines |

**Deduplication and retry:** After citation chasing, sources are deduplicated by URL. If unique source count is below `MIN_SOURCES_PER_CLAIM`, the agent retries with Claude-generated alternative queries up to `MAX_SEARCH_RETRIES` times.

#### Fakeness Agent (`fakeness_agent.py`)

Calls the GPTZero API to estimate AI-generated content likelihood. Returns `fakeness_score` (0–100; 0 = human-written, 100 = AI-generated). Requires `GPTZERO_API_KEY` in `.env`.

#### Statistics Agent (`statistics_agent.py`)

Verifies cited data and statistics against authoritative sources.

#### Source Checker (`source_checker.py`)

Evaluates publisher and author credibility; checks timestamp plausibility. Returns `PublisherCredibility`.

---

### Stage 5: Judgement

Handled by `app/judgement/judgement.py`. Aggregates all investigation outputs into a final `JudgementResult`.

See [Scoring Algorithm](#scoring-algorithm) for full details.

---

### Stage 6: Database

Handled by `app/database/db.py`. Results are cached by article URL. Future requests for the same URL return the cached result immediately (bypass with `noCache: true`).

---

## Project Structure

```
app/
  main.py                       # FastAPI entry point; mounts router and static files
  config.py                     # Scoring weights and tuning constants
  api/
    routes.py                   # POST /api/v1/analyse and /analyse/upload endpoints
  models/
    schemas.py                  # All shared Pydantic models
  ingestion/
    ingestion_agent.py          # Step 1: accept URL / file / text
    extraction_agent.py         # Step 2: extract text, metadata, translate
    summariser.py               # Step 3: summarise long content
  investigation/
    investigator.py             # Step 4: orchestrate parallel agents
    search_agent.py             # Step 4a: source search and citation chasing
    fakeness_agent.py           # Step 4b: AI-generated content detection
    statistics_agent.py         # Step 4c: statistics verification
    source_checker.py           # Step 4d: publisher/author credibility
  judgement/
    judgement.py                # Step 5: aggregate scores into final verdict
  database/
    db.py                       # Step 6: cache read/write
frontend/
  index.html                    # Single-page UI
  static/
    app.js                      # Result rendering and SSE event handling
    styles.css                  # Styles
tests/
requirements.txt
.env                            # API keys — not committed
DATABASE.md                     # Database schema reference
CHANGELOG.md                    # Change log
```

---

## Tech Stack

| Component | Technology |
| --------- | ---------- |
| Web framework | FastAPI + uvicorn |
| AI / LLM | Anthropic Claude (`claude-sonnet-4-6`) |
| Web search | Tavily (`tavily-python`) |
| Web extraction | `trafilatura` |
| Binary file download | `httpx` |
| PDF parsing | `pypdf` |
| DOCX parsing | `python-docx` |
| Excel parsing | `openpyxl` |
| HTML parsing | `beautifulsoup4` |
| AI detection | GPTZero API |
| Cloud storage | AWS S3 via `aioboto3` |
| Database | ClickHouse (`clickhouse-connect`) |
| Schema validation | Pydantic v2 |

---

## Setup

### Prerequisites

- Python 3.11+
- API keys: Anthropic, Tavily, GPTZero
- AWS S3 bucket (for content and source archival)
- ClickHouse instance (or SQLite for local dev)

### Install dependencies

```bash
pip install -r requirements.txt
```

### Configure environment

Create a `.env` file in the project root:

```dotenv
# Required
ANTHROPIC_API_KEY=sk-ant-...
TAVILY_API_KEY=tvly-...
GPTZERO_API_KEY=...

# AWS S3 (content and source archival)
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
S3_BUCKET_NAME=factguard-content
S3_REGION=ap-southeast-1

# Database
DATABASE_URL=sqlite+aiosqlite:///./analysis.db   # SQLite for local dev

# Optional — search tuning (defaults shown)
TAVILY_SEARCH_DEPTH=advanced    # basic | advanced
TAVILY_MAX_RESULTS=5
PRIORITISE_LOCAL=false
COUNTRY=Singapore
MIN_SOURCES_PER_CLAIM=2
MAX_HOP_DEPTH=2
MAX_SEARCH_RETRIES=2

# Optional — Singapore-specific
DATA_GOV_API_KEY=...            # data.gov.sg REST API (CKAN was discontinued Dec 2025)
```

---

## Running the Service

```bash
# Development server with hot reload
uvicorn app.main:app --reload --port 8000
```

The frontend UI is served at `http://localhost:8000/`.
Interactive API docs are at `http://localhost:8000/docs`.

```bash
# Health check
curl http://localhost:8000/health
# {"status": "ok"}
```

---

## API Reference

### `POST /api/v1/analyse`

Analyse an article by URL or pasted text.

**Content-Type:** `application/json`

**Request body:**

| Field | Type | Required | Default | Description |
| ----- | ---- | -------- | ------- | ----------- |
| `articleUrl` | string (URI) | One of | — | URL of the article to check |
| `articleText` | string | One of | — | Raw article text (mutually exclusive with `articleUrl`) |
| `noCache` | boolean | No | `false` | Set `true` to bypass the DB cache and re-run the full pipeline |

Exactly one of `articleUrl` or `articleText` must be supplied.

**Example:**

```bash
curl -X POST http://localhost:8000/api/v1/analyse \
  -H "Content-Type: application/json" \
  -d '{"articleUrl": "https://example.com/news/article"}'
```

---

### `POST /api/v1/analyse/upload`

Analyse an article submitted as a file upload.

**Content-Type:** `multipart/form-data`

**Form field:** `file` — PDF, DOCX, HTML, MD, RTF, or image.

**Example:**

```bash
curl -X POST http://localhost:8000/api/v1/analyse/upload \
  -F "file=@article.pdf"
```

---

### `GET /health`

Returns `{"status": "ok"}`. Used for liveness checks.

---

**API keys** (`ANTHROPIC_API_KEY`, `TAVILY_API_KEY`) are read server-side from `.env`. They are never sent in request bodies or returned in responses.

---

## Response Schema

All endpoints return a `JudgementResult` object.

### `JudgementResult`

| Field | Type | Description |
| ----- | ---- | ----------- |
| `content` | `ContentMetadata` | Article metadata |
| `publisher_credibility` | `PublisherCredibility` | Publisher score and rating |
| `content_credibility` | `ContentCredibility` | Content score, rating, and per-verdict claim counts |
| `claims` | `JudgedClaim[]` | Per-claim verdicts with evidence |
| `is_conflicted` | boolean | `true` when content and publisher scores diverge by >= 40 points |

---

### `ContentMetadata`

| Field | Type | Description |
| ----- | ---- | ----------- |
| `input_type` | enum | `url` \| `text` \| `pdf` \| `docx` \| `html` \| `md` \| `rtf` \| `image` |
| `url` | string | Original URL (if URL input) |
| `s3_url` | string | S3 URL of archived content |
| `title` | string | |
| `publisher` | string | |
| `date` | date | |
| `author` | string | |
| `section` | string | |
| `is_opinion` | boolean | |
| `original_language` | string | BCP-47 code, e.g. `en`, `zh`, `ms` |

---

### `PublisherCredibility`

| Field | Type | Description |
| ----- | ---- | ----------- |
| `score` | integer 0–100 | Publisher credibility score |
| `rating` | enum | `highly_credible` \| `credible` \| `mixed` \| `low_credibility` \| `not_credible` |
| `summary` | string | Human-readable assessment |
| `bias` | enum | `far_left` \| `left` \| `center_left` \| `center` \| `center_right` \| `right` \| `far_right` \| `unknown` |
| `known_issues` | string[] | Documented credibility issues |
| `fact_checker_ratings` | string[] | Third-party fact-checker assessments |

---

### `ContentCredibility`

| Field | Type | Description |
| ----- | ---- | ----------- |
| `score` | integer 0–100 | Overall content credibility score |
| `rating` | enum | `credible` \| `mostly_credible` \| `mixed` \| `low_credibility` \| `not_credible` |
| `summary` | string | Human-readable verdict |
| `total_claims_found` | integer | Number of checkable claims identified |
| `claims_true` | integer | |
| `claims_mostly_true` | integer | |
| `claims_misleading` | integer | |
| `claims_inconclusive` | integer | Evidence found but balanced or conflicting |
| `claims_unverified` | integer | No sources available to check against |
| `claims_mostly_false` | integer | |
| `claims_false` | integer | |
| `government_source_only_flag` | boolean | `true` when any claim is backed exclusively by government sources |
| `writing_quality` | `WritingQuality` | Writing quality indicators |

**`WritingQuality`:**

| Field | Type |
| ----- | ---- |
| `sensationalism` | boolean |
| `named_sources` | boolean |
| `anonymous_sources` | boolean |
| `emotional_language` | boolean |
| `hedging_language` | boolean |

---

### `JudgedClaim`

| Field | Type | Description |
| ----- | ---- | ----------- |
| `claim_id` | integer | |
| `claim_summary` | string | Short description of the claim |
| `extract` | string | Verbatim quote from the article |
| `verdict` | `ClaimVerdict` | Final verdict (see below) |
| `overall_reason` | string | Consolidated reasoning across all evidence |
| `government_source_only` | boolean | All sources for this claim are government-owned |
| `sources` | `ClaimSource[]` | Full source records |
| `evidence` | `ClaimEvidence[]` | Curated evidence snippets |

**`ClaimVerdict` enum:**

| Value | Meaning |
| ----- | ------- |
| `true` | Claim is accurate |
| `mostly_true` | Claim is largely accurate with minor caveats |
| `misleading` | Claim is technically true but presented misleadingly |
| `inconclusive` | Evidence found but balanced or conflicting |
| `unverified` | No sources available to check against |
| `mostly_false` | Claim is largely inaccurate |
| `false` | Claim is inaccurate |

---

### `ClaimEvidence`

| Field | Type | Description |
| ----- | ---- | ----------- |
| `source_id` | string | UUID matching the `sources` DB record |
| `source_name` | string | Publication name |
| `source_url` | string | Source URL |
| `snippet` | string | Verbatim excerpt from the source that relates to the claim |
| `is_relevant` | boolean | `false` when the source cannot confirm or deny the claim (unreadable, wrong time period, metadata-only). Display as "Inconclusive". |
| `supports_claim` | boolean | `true` = supports; `false` = contradicts. Only meaningful when `is_relevant=true`. |
| `judgement_reason` | string | Why this snippet affects the verdict, or why the source is not relevant |

---

### `ClaimSource`

| Field | Type | Description |
| ----- | ---- | ----------- |
| `source_id` | string | UUID |
| `name` | string | Publication name |
| `url` | string | Source URL |
| `type` | string | `news` \| `government` \| `academic` \| etc. |
| `is_independent` | boolean | Independent of the original publisher |
| `is_primary_source` | boolean | Provides own data/analysis (vs. relaying a claim) |
| `hop_depth` | integer | Citation hops from the original Tavily result (0 = direct) |
| `s3_url` | string | S3 URL of archived HTML copy |
| `extracted_text` | string | Full text extracted from the source page |

---

## Scoring Algorithm

### Per-claim score

For each claim, every source with `is_relevant=true` contributes a weighted score:

```
source_value = base_weight
             × direction          (supports_claim=False → flip sign)
             × quality_multiplier (is_primary_source ? 1.2 : 0.8)
             × hop_decay          (0.9 ^ hop_depth)
```

**Base weights** (`VERDICT_BASE_WEIGHTS` in `config.py`):

| Investigator verdict | Weight |
| -------------------- | ------ |
| `true` | +1.0 |
| `mostly_true` | +0.5 |
| `inconclusive` | +1.0 (let evidence decide) |
| `unverified` | +1.0 (let evidence decide) |
| `misleading` | −0.3 |
| `mostly_false` | −0.5 |
| `false` | −1.0 |

**Aggregation:**

```
raw_sum          = sum of all source_value contributions
max_possible_sum = sum of abs(source_value) contributions

if max_possible_sum == 0:
    claim_score = 50          # no scoreable evidence; verdict → Unverified
else:
    claim_score = (raw_sum / max_possible_sum + 1) / 2 × 100
```

**Net source confidence multiplier** (applied when `max_possible_sum > 0`):

```
net = count(supports_claim=True) − count(supports_claim=False)
```

| Net | Multiplier |
| --- | ---------- |
| ≤ −2 | 0.6× |
| −1 | 0.8× |
| 0 | 1.0× |
| 1 | 1.0× |
| 2 | 1.1× |
| 3 | 1.2× |
| 4 | 1.3× |
| ≥ 5 | 1.4× |

**Government-only boost:** When all sources for a claim are government-owned, apply `GOVERNMENT_ONLY_BOOST` (1.1×) after the net confidence multiplier. Skipped when `max_possible_sum == 0`.

### Claim-to-verdict mapping

| Score range | `ClaimVerdict` |
| ----------- | -------------- |
| 80–100 | `true` |
| 60–79 | `mostly_true` |
| 45–59 | `inconclusive` |
| 30–44 | `misleading` |
| 15–29 | `mostly_false` |
| 0–14 | `false` |
| (max_possible_sum = 0) | `unverified` (override) |

### Final content score

```
claims_score  = average of all per-claim scores
blended_score = (claims_score × 0.7) + (publisher_score × 0.3)

if fakeness_score > 60:
    blended_score × = 0.8      # AI-generated content penalty

final_score = clamp(blended_score, 0, 100)
```

### Content rating thresholds

| Score | `ContentRating` |
| ----- | --------------- |
| 80–100 | `credible` |
| 60–79 | `mostly_credible` |
| 40–59 | `mixed` |
| 20–39 | `low_credibility` |
| 0–19 | `not_credible` |

### Conflict flag

`is_conflicted = true` when `|claims_score − publisher_score| >= 40`.

---

## Configuration Reference

All tuning constants live in `app/config.py`. Nothing is hardcoded in the pipeline agents.

| Constant | Default | Description |
| -------- | ------- | ----------- |
| `TAVILY_SEARCH_DEPTH` | `"advanced"` | `basic` or `advanced` |
| `TAVILY_MAX_RESULTS` | `5` | Max search results per claim |
| `PRIORITISE_LOCAL` | `false` | Boost country-specific sources |
| `COUNTRY` | `"Singapore"` | Country filter for local prioritisation |
| `MIN_SOURCES_PER_CLAIM` | `2` | Retry threshold after deduplication |
| `MAX_HOP_DEPTH` | `2` | Maximum citation hops |
| `MAX_SEARCH_RETRIES` | `2` | Max retry searches per claim |
| `PRIMARY_SOURCE_MULTIPLIER` | `1.2` | Weight boost for primary sources |
| `SECONDARY_SOURCE_MULTIPLIER` | `0.8` | Weight discount for secondary sources |
| `HOP_DEPTH_DECAY` | `0.9` | Decay factor per citation hop |
| `CLAIMS_BLEND_WEIGHT` | `0.7` | Claims score share in final blend |
| `PUBLISHER_BLEND_WEIGHT` | `0.3` | Publisher score share in final blend |
| `FAKENESS_THRESHOLD` | `60` | GPTZero score above which penalty applies |
| `FAKENESS_PENALTY_MULTIPLIER` | `0.8` | Score multiplier for AI-generated content |
| `GOVERNMENT_ONLY_BOOST` | `1.1` | Multiplier when all sources are government-owned |
| `CONFLICT_GAP_THRESHOLD` | `40` | Score gap triggering `is_conflicted=true` |

---

## Database Schema

Full schema reference: [DATABASE.md](DATABASE.md)

### Tables

| Table | Written by | Description |
| ----- | ---------- | ----------- |
| `content` | Ingestion | Extracted text, metadata, and S3 URL |
| `analysis` | Judgement | Serialised `JudgementResult` JSON, keyed by URL |
| `sources` | Investigation | Corroborative sources with extracted text and S3 archive |
| `evidence` | Judgement | Verbatim evidence snippets linking claims to sources |

### S3 layout

```
s3://<S3_BUCKET_NAME>/content/<YYYY-MM-DD>/<uuid>.<ext>   # ingested content
s3://<S3_BUCKET_NAME>/sources/<YYYY-MM-DD>/<uuid>.html    # archived source pages
```

---

## Development

### Run tests

```bash
pytest                                      # all tests
pytest tests/test_api.py                   # single file
pytest tests/test_api.py::test_name -v     # single test
```

### Lint and format

```bash
ruff check .
ruff format .
```

### Interactive API docs

Available at `http://localhost:8000/docs` when the server is running.

### Caching

The pipeline is skipped entirely when a URL has been analysed before. To force a fresh analysis, set `noCache: true` in the request body.

### Adding a new pipeline stage

1. Implement the stage in the appropriate `app/<team>/` module.
2. Add or extend Pydantic models in `app/models/schemas.py`.
3. Wire the stage into `app/api/routes.py`.
4. Add tuning constants to `app/config.py` — never hardcode values in agent files.
5. Update [DATABASE.md](DATABASE.md) if new tables or columns are needed.
6. Update [CHANGELOG.md](CHANGELOG.md).
