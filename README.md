# FactGuard

FactGuard is an AI-powered misinformation and fake news detection tool. You submit a news article — by URL, file upload, or pasted text — and a multi-stage pipeline of AI agents analyses it, fact-checks its individual claims against real sources on the web, and returns a credibility verdict with a per-claim evidence breakdown.

The goal is not just to produce a single score but to show *why* content is rated the way it is: which specific claims were checked, what sources were found, whether those sources support or contradict each claim, and how confident the system is in each judgement.

Built for Hackomania 2026.

---

## Table of Contents

1. [How It Works](#how-it-works)
2. [Architecture](#architecture)
3. [Pipeline Stages](#pipeline-stages)
4. [Project Structure](#project-structure)
5. [Tech Stack](#tech-stack)
6. [Setup](#setup)
7. [Running the Service](#running-the-service)
8. [API Reference](#api-reference)
9. [Response Schema](#response-schema)
10. [Scoring Algorithm](#scoring-algorithm)
11. [Configuration Reference](#configuration-reference)
12. [Database Schema](#database-schema)
13. [Development](#development)

---

## How It Works

At a high level, FactGuard does five things:

1. **Reads the article.** It accepts a URL, an uploaded file (PDF, DOCX, image, etc.), or plain pasted text. Non-English content is automatically translated.

2. **Identifies claims.** Claude reads the article and extracts individual checkable factual claims — statements that can in principle be confirmed or refuted by external sources.

3. **Finds evidence.** For each claim, Tavily searches the web for relevant sources. The search agent chases citations back to primary sources (the original data or official statement), discarding sources that merely relay claims without evidence.

4. **Scores each claim.** Claude reads the extracted source text and determines whether each source supports or contradicts the claim. Sources are weighted by quality (primary vs. secondary), citation distance (hop depth), and how many independent sources agree.

5. **Produces a final verdict.** Claim scores are averaged and blended with a publisher credibility score. An AI-generated content penalty is applied if the article appears to be machine-written. The result is a 0–100 credibility score with a human-readable rating and full evidence trail.

A results cache means that repeat requests for the same URL skip the full pipeline and return instantly.

---

## Architecture

The service is a FastAPI application. The frontend is a single-page HTML/JS UI served directly by the same process. All pipeline stages are async; the investigation sub-agents run in parallel.

```
Browser / API client
        |
        v
POST /api/v1/analyse        (JSON — URL or text)
POST /api/v1/analyse/upload (multipart — file)
        |
        v
  [0] DB cache lookup
        |── hit ──> return cached JudgementResult immediately
        |
       miss
        |
        v
  [1] ingestion_agent.py   — accept input, upload raw content to S3
  [2] extraction_agent.py  — parse text, extract metadata, translate if needed
  [3] summariser.py        — summarise if article exceeds ~4000 tokens
        |
        v
  [4] investigator.py  — identify claims, then run all agents in parallel:
        ├── search_agent.py      — Tavily search + citation chasing per claim
        ├── fakeness_agent.py    — AI-generated content detection (GPTZero)
        ├── statistics_agent.py  — verify cited statistics
        └── source_checker.py   — publisher and author credibility
        |
        v
  [5] judgement.py
        — Claude extracts evidence snippets from each source
        — weighted scoring per claim
        — blend claims + publisher scores
        — apply fakeness penalty if needed
        — produce JudgementResult
        |
        v
  [6] db.py — persist analysis, sources, and evidence for future cache hits
        |
        v
  Return JudgementResult to client
```

---

## Pipeline Stages

### Stage 1–3: Ingestion

Handled by `app/ingestion/`. This stage is responsible for turning any supported input format into a clean, English-language text body that the rest of the pipeline can work with.

**Accepted input formats:**

| Format     | How it is parsed |
| ---------- | ---------------- |
| URL        | `trafilatura` fetches and extracts the article body, stripping navigation and ads |
| PDF        | `pypdf` extracts text page by page |
| DOCX       | `python-docx` reads paragraph text |
| HTML       | `beautifulsoup4` strips scripts/styles and extracts body text |
| MD         | `markdown` converts to HTML, then `beautifulsoup4` extracts text |
| RTF        | `striprtf` converts to plain text |
| Image      | Claude vision API performs OCR — useful for screenshots of articles |
| Plain text | Passed through directly with no parsing |

**Steps in detail:**

1. **`ingestion_agent.py`** — The entry point. Accepts a URL, plain text string, or `UploadFile`. For URLs and file uploads, it archives the original content to S3 so it can be audited later. It then delegates to the extraction agent.

2. **`extraction_agent.py`** — Parses the raw content to extract a clean text body. It then calls Claude to extract structured metadata (title, publisher, author, date, section, opinion flag) and detect the source language. If the article is not in English, it is translated using Claude before being passed downstream. Both the original-language text and the English translation are preserved in the result.

3. **`summariser.py`** — If the article body exceeds approximately 4000 tokens, it is summarised by Claude to keep downstream agent prompts within token limits. Shorter articles pass through unchanged. Summarisation only affects the `text` field used for analysis; `original_text` is always the full extracted content.

**Language handling:** `original_text` always holds the source-language content as extracted. `text` holds English — either the translation or the original if it was already in English.

---

### Stage 4: Investigation

Handled by `app/investigation/`. The investigator first uses Claude to identify individual checkable factual claims in the article text. It then runs four specialised sub-agents concurrently via `asyncio.gather`, collecting their outputs before returning a combined `InvestigationResult`.

#### Search Agent (`search_agent.py`)

This is the most complex sub-agent. Its job is to find real, high-quality sources that can confirm or refute each claim — not just any page that mentions the topic.

**The citation-chasing loop**

Rather than treating every Tavily result as equally valid evidence, the search agent classifies each source into one of three tiers:

| Tier | What it means | What happens |
| ---- | ------------- | ------------ |
| **Primary** | The source provides its own original data, analysis, or official statement | Kept as strong evidence (`is_primary_source=True`) |
| **Secondary — with citation** | The source relays a claim and cites a specific URL as its basis | The cited URL is followed and reclassified (up to `MAX_HOP_DEPTH` hops) |
| **Mention-only** | The source mentions the claim but provides no citation trail | Discarded — not useful as evidence |

This means that if a news article says "according to a WHO report [link]", the agent follows the link to the WHO report itself and uses that as the evidence rather than the news article. Primary sources are given higher weight in scoring than secondary sources.

**Binary file extraction**

Some primary sources are published as data files rather than web pages. URLs whose path ends with `.pdf`, `.csv`, `.xlsx`, or `.xls` are downloaded directly via `httpx` and parsed before classification, since `trafilatura` cannot extract content from these formats.

| Extension      | Parser used |
| -------------- | ----------- |
| `.pdf`         | `pypdf` — all pages joined with `\n\n` |
| `.csv`         | stdlib `csv` — rows formatted as comma-separated lines |
| `.xlsx`/`.xls` | `openpyxl` — each sheet's rows formatted as comma-separated lines |

If a download or parse fails, the URL is silently skipped (logged at `DEBUG`).

**Deduplication and retry**

After all citation chasing completes for a claim, sources are deduplicated by URL fingerprint (first 300 characters of extracted text, to catch syndicated copies of the same article published on different sites). If the number of unique sources remaining falls below `MIN_SOURCES_PER_CLAIM`, the agent asks Claude to generate an alternative search query and retries via Tavily, up to `MAX_SEARCH_RETRIES` times.

#### Fakeness Agent (`fakeness_agent.py`)

Calls the GPTZero API (`https://api.gptzero.me/v2/predict/text`) to estimate how likely the article is to have been AI-generated. Returns a `fakeness_score` from 0 (almost certainly human-written) to 100 (almost certainly AI-generated). If the score exceeds the `FAKENESS_THRESHOLD` (default 60), a credibility penalty is applied during judgement.

Requires `GPTZERO_API_KEY` in `.env`.

#### Statistics Agent (`statistics_agent.py`)

Identifies numerical claims and statistics cited in the article and checks them against authoritative data sources to flag figures that appear inaccurate or out of context.

#### Source Checker (`source_checker.py`)

Researches the article's publisher and author using Claude and known credibility databases. Produces a `PublisherCredibility` record with a 0–100 score, a political bias assessment, and any known issues such as a history of publishing misleading content.

---

### Stage 5: Judgement

Handled by `app/judgement/judgement.py`. This stage takes the raw investigation outputs and produces the final, human-readable result.

For each claim, Claude reads the full extracted text of every source and identifies the specific verbatim snippet most directly relevant to the claim. It assesses whether that snippet supports or contradicts the claim, and explains why. These per-source assessments are then fed into a weighted scoring formula (see [Scoring Algorithm](#scoring-algorithm)) to produce a numerical claim score, which maps to a `ClaimVerdict`.

Claude also assesses the article's writing quality — checking for sensationalism, emotional language, anonymous sources, and hedging language — which is displayed in the UI as supplementary information.

The individual claim scores are averaged, blended with the publisher credibility score, and penalised if AI-generated content is detected. The result is clamped to 0–100 and mapped to a content rating.

See [Scoring Algorithm](#scoring-algorithm) for the full formula.

---

### Stage 6: Database

Handled by `app/database/db.py`. After every successful analysis, the full `JudgementResult` is written to the database alongside the individual sources and evidence snippets. On future requests for the same URL, the cached result is returned directly, skipping the entire pipeline.

To bypass the cache and force a fresh analysis — for example, when an article has been updated — set `noCache: true` in the request body.

---

## Project Structure

```
app/
  main.py                       # FastAPI entry point; mounts router and static files
  config.py                     # All scoring weights and tuning constants (no hardcoding in agents)
  api/
    routes.py                   # POST /api/v1/analyse and /analyse/upload endpoints
  models/
    schemas.py                  # All shared Pydantic models — single source of truth for data shapes
  ingestion/
    ingestion_agent.py          # Step 1: accept URL / file / text; archive to S3
    extraction_agent.py         # Step 2: parse, extract metadata, detect language, translate
    summariser.py               # Step 3: summarise if token count > 4000
  investigation/
    investigator.py             # Step 4: identify claims; orchestrate parallel sub-agents
    search_agent.py             # Step 4a: Tavily search, citation chasing, binary file extraction
    fakeness_agent.py           # Step 4b: AI-generated content detection via GPTZero
    statistics_agent.py         # Step 4c: verify cited statistics
    source_checker.py           # Step 4d: publisher and author credibility
  judgement/
    judgement.py                # Step 5: evidence extraction, weighted scoring, final verdict
  database/
    db.py                       # Step 6: cache read/write for analysis results and sources
frontend/
  index.html                    # Single-page UI (form + results display)
  static/
    app.js                      # Result rendering, SSE event handling, mock data
    styles.css                  # Styles
tests/
requirements.txt
.env                            # API keys and secrets — not committed to git
DATABASE.md                     # Full database schema and S3 naming conventions
CHANGELOG.md                    # Record of significant changes
```

**Key convention:** All scoring constants and tuning values live in `app/config.py`. Agent files import them — nothing is hardcoded in business logic. All data shapes are defined once in `app/models/schemas.py` and imported wherever needed.

---

## Tech Stack

| Component | Technology | Why |
| --------- | ---------- | --- |
| Web framework | FastAPI + uvicorn | Async-native, automatic OpenAPI docs, fast |
| AI / LLM | Anthropic Claude (`claude-sonnet-4-6`) | Claim extraction, evidence assessment, metadata, translation, writing quality |
| Web search | Tavily (`tavily-python`) | Purpose-built for AI search; returns full content, not just links |
| Web extraction | `trafilatura` | Strips boilerplate from news pages reliably |
| Binary file download | `httpx` | Async HTTP client for PDF/CSV/Excel source files |
| PDF parsing | `pypdf` | Extracts text from PDF corroboration sources and uploads |
| DOCX parsing | `python-docx` | Handles Word document uploads |
| Excel parsing | `openpyxl` | Handles `.xlsx`/`.xls` corroboration sources |
| HTML/MD parsing | `beautifulsoup4`, `markdown` | Handles HTML and Markdown uploads |
| AI detection | GPTZero API | Dedicated model trained to detect AI-generated text |
| Cloud storage | AWS S3 via `aioboto3` | Archives ingested content and source HTML for auditability |
| Database | ClickHouse (`clickhouse-connect`) | Stores analysis results, sources, and evidence; enables URL-based caching |
| Schema validation | Pydantic v2 | Enforces data contracts between pipeline stages |

---

## Setup

### Prerequisites

- Python 3.11 or later
- API keys for: Anthropic Claude, Tavily, GPTZero
- AWS S3 bucket for content and source archival
- ClickHouse instance (for production) — SQLite works for local development by setting `DATABASE_URL` accordingly

### Install dependencies

```bash
pip install -r requirements.txt
```

### Configure environment

Create a `.env` file in the project root. The server reads API keys from this file at startup — they are never accepted in request bodies.

```dotenv
# ── Required ─────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY=sk-ant-...      # Used by extraction, investigation, and judgement stages
TAVILY_API_KEY=tvly-...           # Used by the search agent for per-claim source searches
GPTZERO_API_KEY=...               # Used by the fakeness agent for AI-content detection

# ── AWS S3 (content and source archival) ─────────────────────────────────────
# Ingestion archives raw article HTML/files here; search_agent archives source pages.
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
S3_BUCKET_NAME=factguard-content
S3_REGION=ap-southeast-1

# ── Database ──────────────────────────────────────────────────────────────────
# SQLite is fine for local development. Switch to ClickHouse for production.
DATABASE_URL=sqlite+aiosqlite:///./analysis.db

# ── Search tuning (optional — defaults shown) ─────────────────────────────────
# advanced search is more thorough but slower; basic is faster
TAVILY_SEARCH_DEPTH=advanced
# How many Tavily results to fetch per claim before citation chasing
TAVILY_MAX_RESULTS=5
# Set to true to bias searches toward sources from COUNTRY
PRIORITISE_LOCAL=false
COUNTRY=Singapore
# Retry search if fewer than this many unique sources are found after dedup
MIN_SOURCES_PER_CLAIM=2
# Max citation hops when chasing a secondary source back to its primary
MAX_HOP_DEPTH=2
# Max retry attempts per claim when source count falls below MIN_SOURCES_PER_CLAIM
MAX_SEARCH_RETRIES=2

# ── Singapore-specific (optional) ────────────────────────────────────────────
# Required for data.gov.sg queries (CKAN API was discontinued Dec 2025)
DATA_GOV_API_KEY=...
```

---

## Running the Service

```bash
# Start the development server with hot reload
uvicorn app.main:app --reload --port 8000
```

Once running:

| URL | What it serves |
| --- | -------------- |
| `http://localhost:8000/` | The FactGuard UI |
| `http://localhost:8000/docs` | Interactive Swagger API documentation |
| `http://localhost:8000/health` | Liveness check — returns `{"status": "ok"}` |

A typical analysis takes 15–45 seconds depending on article length and the number of claims found. The first request for a given URL runs the full pipeline; subsequent requests return from cache immediately.

---

## API Reference

### `POST /api/v1/analyse`

Submits an article for analysis by URL or pasted text. API keys are read from the server's `.env` — they must not be included in the request body.

**Content-Type:** `application/json`

**Request body:**

| Field | Type | Required | Default | Description |
| ----- | ---- | -------- | ------- | ----------- |
| `articleUrl` | string (URI) | One of | — | Public URL of the article to analyse |
| `articleText` | string | One of | — | Raw article text pasted directly. Mutually exclusive with `articleUrl`. |
| `noCache` | boolean | No | `false` | Pass `true` to bypass the result cache and re-run the full pipeline. Useful when an article has been updated since it was last analysed. |

Exactly one of `articleUrl` or `articleText` must be provided. Supplying both, or neither, returns a `422` validation error.

**Example:**

```bash
curl -X POST http://localhost:8000/api/v1/analyse \
  -H "Content-Type: application/json" \
  -d '{"articleUrl": "https://www.channelnewsasia.com/singapore/example-article"}'
```

**Cache bypass example:**

```bash
curl -X POST http://localhost:8000/api/v1/analyse \
  -H "Content-Type: application/json" \
  -d '{"articleUrl": "https://example.com/article", "noCache": true}'
```

---

### `POST /api/v1/analyse/upload`

Submits an article for analysis as a file upload. Useful when the article is not publicly accessible via URL or exists only as a local file.

**Content-Type:** `multipart/form-data`

**Form field:** `file` — accepted formats: PDF, DOCX, HTML, MD, RTF, PNG, JPG, WebP (and other common image formats).

Note: file uploads are not cached by URL (there is no stable cache key). Each upload runs the full pipeline.

**Example:**

```bash
curl -X POST http://localhost:8000/api/v1/analyse/upload \
  -F "file=@/path/to/article.pdf"
```

---

### `GET /health`

Returns `{"status": "ok"}`. Used for container liveness checks.

---

## Response Schema

Both analysis endpoints return a `JudgementResult` object. Every field is described below.

### `JudgementResult` — top-level response

| Field | Type | Description |
| ----- | ---- | ----------- |
| `content` | `ContentMetadata` | Metadata extracted from the article: title, publisher, author, date, etc. |
| `publisher_credibility` | `PublisherCredibility` | Assessment of the publisher's track record and political bias |
| `content_credibility` | `ContentCredibility` | The overall content score, rating, claim verdict counts, and writing quality |
| `claims` | `JudgedClaim[]` | Full per-claim breakdown: verdict, evidence snippets, and sources |
| `is_conflicted` | boolean | `true` when the content score and publisher score diverge by 40 or more points — this indicates the article's claims appear credible but the outlet has a poor track record, or vice versa |

---

### `ContentMetadata`

Extracted by the ingestion pipeline from the article itself.

| Field | Type | Description |
| ----- | ---- | ----------- |
| `input_type` | enum | How the content was submitted: `url` \| `text` \| `pdf` \| `docx` \| `html` \| `md` \| `rtf` \| `image` |
| `url` | string | The original URL, if the input was a URL |
| `s3_url` | string | S3 URL of the archived raw content file |
| `title` | string | Article headline |
| `publisher` | string | Publication name |
| `date` | date | Publication date (YYYY-MM-DD) |
| `author` | string | Byline |
| `section` | string | Section or category (e.g. "Politics", "Opinion") |
| `is_opinion` | boolean | `true` if Claude determined the content is an editorial, opinion column, or commentary — opinion pieces are still analysed but context is noted |
| `original_language` | string | BCP-47 language code of the source content, e.g. `en`, `zh`, `ms`. Non-English articles are translated before analysis. |

---

### `PublisherCredibility`

Produced by `source_checker.py`. Assesses the publisher's overall reputation, not the specific article being analysed.

| Field | Type | Description |
| ----- | ---- | ----------- |
| `score` | integer 0–100 | Publisher credibility score. 80+ = highly credible; below 40 = significant concerns. |
| `rating` | enum | `highly_credible` \| `credible` \| `mixed` \| `low_credibility` \| `not_credible` |
| `summary` | string | Human-readable assessment explaining the rating |
| `bias` | enum | Assessed political lean: `far_left` \| `left` \| `center_left` \| `center` \| `center_right` \| `right` \| `far_right` \| `unknown` |
| `known_issues` | string[] | Documented issues such as past inaccuracies, retractions, or ownership concerns |
| `fact_checker_ratings` | string[] | Ratings from third-party fact-checkers such as PolitiFact or Media Bias/Fact Check |

---

### `ContentCredibility`

The core output of the judgement stage: the overall credibility assessment of the article's content.

| Field | Type | Description |
| ----- | ---- | ----------- |
| `score` | integer 0–100 | The final credibility score. Computed from claim scores (70% weight), publisher score (30%), and a fakeness penalty if AI-generated content is detected. |
| `rating` | enum | `credible` \| `mostly_credible` \| `mixed` \| `low_credibility` \| `not_credible` |
| `summary` | string | Human-readable verdict, including notes on conflict and AI-content detection |
| `total_claims_found` | integer | How many distinct checkable factual claims Claude identified in the article |
| `claims_true` | integer | Claims confirmed by evidence |
| `claims_mostly_true` | integer | Claims largely confirmed with minor caveats |
| `claims_misleading` | integer | Claims technically accurate but framed to mislead |
| `claims_inconclusive` | integer | Claims where evidence was found but is balanced or conflicting — the system cannot determine truth either way |
| `claims_unverified` | integer | Claims for which no usable sources could be found to check against |
| `claims_mostly_false` | integer | Claims largely contradicted by evidence |
| `claims_false` | integer | Claims clearly refuted by evidence |
| `government_source_only_flag` | boolean | `true` if any claim's evidence comes exclusively from government-owned sources, which may warrant independent verification |
| `writing_quality` | `WritingQuality` | Stylistic signals about how the article is written |

**`WritingQuality` — writing style indicators:**

| Field | Type | What `true` means |
| ----- | ---- | ----------------- |
| `sensationalism` | boolean | The article uses sensationalist headlines or emotionally exaggerated language to attract attention |
| `named_sources` | boolean | At least one named human source is cited (generally a positive signal) |
| `anonymous_sources` | boolean | One or more sources are unnamed or described only as "a source said" |
| `emotional_language` | boolean | The article uses charged emotional language rather than neutral reporting |
| `hedging_language` | boolean | The article uses qualifying language such as "allegedly", "reportedly", or "may" — common in early-stage reporting but can also signal uncertainty about accuracy |

---

### `JudgedClaim`

One entry per claim identified in the article. This is where the full evidence trail lives.

| Field | Type | Description |
| ----- | ---- | ----------- |
| `claim_id` | integer | Sequential identifier |
| `claim_summary` | string | Short, neutral description of what the claim asserts |
| `extract` | string | The verbatim sentence or passage from the article that contains the claim |
| `verdict` | `ClaimVerdict` | The final verdict after weighing all evidence (see below) |
| `overall_reason` | string | Consolidated explanation of the verdict across all sources. Notes paywall restrictions, government-only sources, or lack of evidence where applicable. |
| `government_source_only` | boolean | `true` if every source found for this claim is government-owned |
| `sources` | `ClaimSource[]` | Full details of every source consulted, including extracted text |
| `evidence` | `ClaimEvidence[]` | The curated evidence snippets Claude selected from each source |

**`ClaimVerdict` — possible outcomes for a claim:**

| Value | Meaning |
| ----- | ------- |
| `true` | Multiple independent sources confirm the claim; score ≥ 80 |
| `mostly_true` | Evidence largely supports the claim, with minor caveats; score 60–79 |
| `misleading` | The claim may be technically accurate but evidence suggests it misrepresents context; score 30–44 |
| `inconclusive` | Sources were found and evaluated, but they conflict or are evenly balanced; score 45–59 |
| `unverified` | No usable sources could be found, or all sources are paywalled. The system has no evidence either way. |
| `mostly_false` | Evidence largely contradicts the claim; score 15–29 |
| `false` | Evidence clearly refutes the claim; score 0–14 |

The distinction between `inconclusive` and `unverified` is important: *inconclusive* means the system found evidence but could not reach a conclusion; *unverified* means it found no evidence at all.

---

### `ClaimEvidence`

One entry per source consulted for a claim. This is the lowest-level unit of evidence in the system.

| Field | Type | Description |
| ----- | ---- | ----------- |
| `source_id` | string | UUID that links back to the corresponding row in the `sources` database table |
| `source_name` | string | Human-readable name of the publication |
| `source_url` | string | URL of the source page |
| `snippet` | string | The verbatim excerpt that Claude selected as the most relevant passage for this claim |
| `is_relevant` | boolean | `false` when the source cannot confirm or deny the claim — for example, a paywalled page, a source covering a different time period, or a page that only contains metadata. Displayed as "Inconclusive" in the UI. |
| `supports_claim` | boolean | `true` if the snippet supports the claim; `false` if it contradicts it. Only meaningful when `is_relevant=true`. |
| `judgement_reason` | string | Claude's explanation of why this snippet was chosen and how it affects the verdict. For high `hop_depth` sources, notes that the evidence is more distant from the original claim. |

---

### `ClaimSource`

Full record of a source page consulted during investigation.

| Field | Type | Description |
| ----- | ---- | ----------- |
| `source_id` | string | UUID primary key |
| `name` | string | Publication name |
| `url` | string | Source URL |
| `type` | string | Source category: `news`, `government`, `academic`, `fact_checker`, etc. |
| `is_independent` | boolean | Whether the source is editorially independent from the article's publisher |
| `is_primary_source` | boolean | `true` if the source provides its own original data or analysis. Primary sources receive a 1.2× weight multiplier; secondary sources receive 0.8×. |
| `hop_depth` | integer | How many citation hops away this source is from the original Tavily result. 0 = found directly; 1 = followed one citation link; 2 = two citation links. Higher hop depth applies a 0.9× decay per hop. |
| `s3_url` | string | S3 URL of the archived HTML copy of the source page |
| `extracted_text` | string | Full text extracted from the source page, as used during evidence evaluation |

---

## Scoring Algorithm

Understanding the scoring model helps interpret why FactGuard produces the verdicts it does.

### Step 1 — Score each source's contribution to a claim

For every source attached to a claim, Claude first determines whether the source is relevant and whether it supports or contradicts the claim. Irrelevant sources (paywalled, wrong time period, metadata-only) are skipped entirely. For each relevant source:

```
source_value = base_weight
             × direction          (−1 if the source contradicts the claim)
             × quality_multiplier (1.2 if is_primary_source, else 0.8)
             × hop_decay          (0.9 ^ hop_depth)
```

The **base weight** is determined by the verdict the investigation agent assigned to the claim before evidence was evaluated. It reflects the initial confidence level:

| Investigator verdict | Base weight | Rationale |
| -------------------- | ----------- | --------- |
| `true` | +1.0 | High initial confidence; evidence carries full weight |
| `mostly_true` | +0.5 | Moderate initial confidence |
| `inconclusive` | +1.0 | No initial opinion — let the evidence decide |
| `unverified` | +1.0 | No initial opinion — let the evidence decide |
| `misleading` | −0.3 | Mild initial scepticism |
| `mostly_false` | −0.5 | Moderate initial scepticism |
| `false` | −1.0 | High initial scepticism; contradicting evidence carries full weight |

### Step 2 — Normalise to a 0–100 claim score

```
raw_sum          = sum of all per-source source_value contributions
max_possible_sum = sum of abs(source_value) for all relevant sources

if max_possible_sum == 0:
    claim_score = 50              # no usable evidence → neutral, verdict → Unverified
else:
    claim_score = (raw_sum / max_possible_sum + 1) / 2 × 100
```

This formula maps a claim fully supported by all sources to ~100, fully contradicted to ~0, and balanced/conflicting evidence to ~50.

### Step 3 — Apply net source confidence multiplier

The net confidence multiplier adjusts the score based on how many independent sources agree vs. disagree:

```
net = count(sources supporting claim) − count(sources contradicting claim)
```

| Net sources | Multiplier | What it means |
| ----------- | ---------- | ------------- |
| ≤ −2 | 0.6× | Contradicting sources significantly outnumber supporting ones |
| −1 | 0.8× | Slightly more contradiction than support |
| 0 | 1.0× | Balanced — or only one source |
| 1 | 1.0× | Minimum expected baseline; treated same as balanced |
| 2 | 1.1× | Moderate corroboration |
| 3 | 1.2× | Strong corroboration |
| 4 | 1.3× | Very strong corroboration |
| ≥ 5 | 1.4× | Extensive corroboration (capped — diminishing returns) |

This multiplier and the government-only boost (step 3a) are **skipped entirely** when `max_possible_sum == 0`, keeping the score at the neutral 50.

**Step 3a — Government-only boost:** If all sources for a claim come from government-owned outlets, a 1.1× boost is applied. This acknowledges that official sources carry authority for certain claim types, while the `government_source_only_flag` in the response flags it for the reader's attention.

### Step 4 — Map claim score to `ClaimVerdict`

| Score range | Verdict |
| ----------- | ------- |
| 80–100 | `true` |
| 60–79 | `mostly_true` |
| 45–59 | `inconclusive` |
| 30–44 | `misleading` |
| 15–29 | `mostly_false` |
| 0–14 | `false` |
| max_possible_sum = 0 | `unverified` (override regardless of score) |

### Step 5 — Compute the final content score

```
claims_score  = simple average of all per-claim scores
blended_score = (claims_score × 0.7) + (publisher_score × 0.3)

if fakeness_score > 60:
    blended_score ×= 0.8        # 20% penalty for likely AI-generated content

final_score = clamp(round(blended_score), 0, 100)
```

The 70/30 blend means article content drives the score but the publisher's track record has meaningful influence. A strong publisher cannot rescue a poorly-evidenced article, and a weak publisher cannot sink a well-evidenced one — but both matter.

The fakeness penalty is binary: above 60 the full 20% reduction applies; at or below 60 there is no effect.

### Step 6 — Map final score to `ContentRating`

| Score | Rating |
| ----- | ------ |
| 80–100 | `credible` |
| 60–79 | `mostly_credible` |
| 40–59 | `mixed` |
| 20–39 | `low_credibility` |
| 0–19 | `not_credible` |

### Conflict detection

`is_conflicted` is set to `true` when the gap between the claims score and the publisher score is 40 or more points. This flags cases where the content and the source's reputation tell different stories — for example, credible-seeming claims from a publisher with a known history of misinformation, or vice versa.

---

## Configuration Reference

All tuning constants are in `app/config.py`. Edit this file to adjust the pipeline's behaviour without touching agent code.

| Constant | Default | What to change it for |
| -------- | ------- | --------------------- |
| `TAVILY_SEARCH_DEPTH` | `"advanced"` | Switch to `"basic"` to reduce latency at the cost of fewer results |
| `TAVILY_MAX_RESULTS` | `5` | Increase for more thorough coverage; decrease to speed up analysis |
| `PRIORITISE_LOCAL` | `false` | Set `true` to bias searches toward sources from `COUNTRY` |
| `COUNTRY` | `"Singapore"` | The country used when `PRIORITISE_LOCAL` is enabled |
| `MIN_SOURCES_PER_CLAIM` | `2` | Raise to require more corroboration before accepting a result; triggers more retries |
| `MAX_HOP_DEPTH` | `2` | How deep to chase citations. Increase for more primary sources; decreases speed. |
| `MAX_SEARCH_RETRIES` | `2` | How many times to retry a search when source count is too low |
| `PRIMARY_SOURCE_MULTIPLIER` | `1.2` | How much more weight a primary source gets vs. a secondary one |
| `SECONDARY_SOURCE_MULTIPLIER` | `0.8` | Weight for sources that relay claims rather than originating them |
| `HOP_DEPTH_DECAY` | `0.9` | Fraction of weight retained per citation hop. 0.9 means a 2-hop source is worth 0.81× a direct source. |
| `CLAIMS_BLEND_WEIGHT` | `0.7` | Share of the final score driven by claim evidence (must sum to 1.0 with `PUBLISHER_BLEND_WEIGHT`) |
| `PUBLISHER_BLEND_WEIGHT` | `0.3` | Share of the final score driven by publisher credibility |
| `FAKENESS_THRESHOLD` | `60` | GPTZero score above which the AI-content penalty fires |
| `FAKENESS_PENALTY_MULTIPLIER` | `0.8` | How much to reduce the score when AI-generated content is detected (0.8 = 20% reduction) |
| `GOVERNMENT_ONLY_BOOST` | `1.1` | Multiplier applied when all sources for a claim are government-owned |
| `CONFLICT_GAP_THRESHOLD` | `40` | Minimum point gap between claims score and publisher score to set `is_conflicted=true` |

---

## Database Schema

Full schema reference: [DATABASE.md](DATABASE.md)

FactGuard uses four tables. Each is written by a specific pipeline stage, making responsibilities clear and enabling independent querying of each data type.

### Tables

| Table | Written by | Purpose |
| ----- | ---------- | ------- |
| `content` | Ingestion pipeline | Stores extracted text (both original-language and English), metadata, and S3 archive URL for each article processed |
| `analysis` | Judgement pipeline | Stores the full `JudgementResult` as serialised JSON, keyed by article URL for fast cache lookup |
| `sources` | Investigation pipeline | Stores every corroborative source found during investigation, with its full extracted text and an S3-archived HTML copy |
| `evidence` | Judgement pipeline | Stores the specific verbatim snippets that link individual claims to the source text that supports or contradicts them |

### Entity relationships

```
content  (1) ──── (many) analysis
content  (1) ──── (many) sources
content  (1) ──── (many) evidence
sources  (1) ──── (many) evidence
```

### S3 layout

Content files and source archives are stored separately so either can be queried or replayed independently:

```
s3://<S3_BUCKET_NAME>/content/<YYYY-MM-DD>/<uuid>.<ext>   # ingested article content
s3://<S3_BUCKET_NAME>/sources/<YYYY-MM-DD>/<uuid>.html    # archived source pages
```

---

## Development

### Run tests

```bash
pytest                                       # run all tests
pytest tests/test_api.py                    # run a single test file
pytest tests/test_api.py::test_name -v      # run a single test by name
```

### Lint and format

```bash
ruff check .     # check for linting issues
ruff format .    # auto-format code
```

### Interactive API docs

Swagger UI is available at `http://localhost:8000/docs` when the server is running. It lists all endpoints, shows request/response schemas, and lets you send test requests directly from the browser.

### Result caching

Every analysis result is cached by article URL. Repeat requests for the same URL skip the full pipeline (which typically takes 15–45 seconds) and return immediately from the database. Pass `noCache: true` to force a re-run — for example, when an article has been corrected or updated since it was last analysed, or during development to test pipeline changes.

### Adding a new pipeline stage or agent

1. Implement the stage in the appropriate `app/<team>/` module.
2. Define or extend Pydantic models in `app/models/schemas.py` — this is the single source of truth for all data shapes.
3. Wire the stage into `app/api/routes.py` between the relevant existing steps.
4. Add any tuning constants to `app/config.py` — never hardcode numeric values in agent files.
5. Update [DATABASE.md](DATABASE.md) if new tables or columns are required.
6. Record the change in [CHANGELOG.md](CHANGELOG.md) with the date and your name.
