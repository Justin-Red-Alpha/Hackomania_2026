# Frontend Team - CLAUDE.md

**Piece:** API routes, shared Pydantic schemas, and frontend UI
**Main docs:** [../CLAUDE.md](../CLAUDE.md)

## Your Scope

| Path                    | Purpose                                             |
| ----------------------- | --------------------------------------------------- |
| `app/api/routes.py`     | `POST /api/v1/analyse` endpoint and request routing |
| `app/models/schemas.py` | Shared Pydantic models (all teams add theirs here)  |
| `frontend/index.html`   | Article submission form + results display           |
| `frontend/static/`      | JS, CSS assets                                      |

## API Endpoint

`POST /api/v1/analyse`

### Request Body

| Field                          | Type            | Required | Default    | Description                                |
| ------------------------------ | --------------- | -------- | ---------- | ------------------------------------------ |
| `articleUrl`                   | string (URI)    | REQ      | -          | URL of the news article to check           |
| `anthropicApiKey`              | string (secret) | REQ      | -          | Claude API key                             |
| `tavilyApiKey`                 | string (secret) | REQ      | -          | Tavily API key for search + extraction     |
| `tavilySearchDepth`            | enum            | OPT      | `advanced` | `basic` = faster, `advanced` = thorough    |
| `tavilyMaxResults`             | integer         | OPT      | `5`        | Max search results per claim               |
| `prioritiseLocalSources`       | boolean         | OPT      | `false`    | Prioritise country-specific sources        |
| `country`                      | string          | OPT      | -          | e.g. `Singapore`, `USA`                    |
| `minimumSourcesPerClaim`       | integer         | OPT      | `2`        | Min independent sources required per claim |
| `excludeGovernmentSourcesOnly` | boolean         | OPT      | `true`     | Flag verdicts backed only by govt sources  |

### Response Body

Returns `JudgementResult` (defined in `app/models/schemas.py`):

**`content`** - Content Metadata

| Field        | Type    |
| ------------ | ------- |
| `url`        | string  |
| `title`      | string  |
| `publisher`  | string  |
| `date`       | date    |
| `author`     | string  |
| `section`    | string  |
| `is_opinion` | boolean |

**`publisher_credibility`** - Publisher Credibility Score

| Field                    | Type          | Notes                               |
| ------------------------ | ------------- | ----------------------------------- |
| `score`                  | integer 0-100 |                                     |
| `rating`                 | enum          | `highly_credible` to `not_credible` |
| `summary`                | string        |                                     |
| `bias`                   | enum          | `far_left` to `far_right`           |
| `known_issues[]`         | array         |                                     |
| `fact_checker_ratings[]` | array         |                                     |

**`article_credibility`** - Article Accuracy Score

| Field                         | Type          | Notes                               |
| ----------------------------- | ------------- | ----------------------------------- |
| `score`                       | integer 0-100 |                                     |
| `rating`                      | enum          | `credible` to `false`               |
| `summary`                     | string        |                                     |
| `total_claims_found`          | integer       |                                     |
| `claims_true / false / etc`   | integer       | One field per verdict bucket        |
| `government_source_only_flag` | boolean       |                                     |
| `writing_quality{}`           | object        | sensationalism, named sources, etc. |

**`claims[]`** - Individual Claim Breakdown

| Field                    | Type    | Notes                           |
| ------------------------ | ------- | ------------------------------- |
| `claim_id`               | integer |                                 |
| `claim_summary`          | string  |                                 |
| `extract`                | string  | Direct quote from article       |
| `verdict`                | enum    | `true` to `false`               |
| `reason`                 | string  |                                 |
| `government_source_only` | boolean |                                 |
| `sources[]`              | array   | name, url, type, is_independent |

## Shared Schemas (`app/models/schemas.py`)

This file is the central schema file. All teams add their Pydantic models here.
Your team is responsible for:
- `AnalyseRequest` - request body for `POST /api/v1/analyse`
- Ensuring all response models compose into a final `AnalysisResponse`

## Frontend Requirements

- Simple form accepting a URL or file upload
- Display results clearly:
  - Overall credibility score (large, prominent percentage)
  - Publisher credibility section
  - Per-claim breakdown (expandable list)
  - Writing quality indicators
- Loading state while analysis runs (can take 10-30 seconds)

## Key Conventions

- All API routes are prefixed `/api/v1/`
- API keys from the request body are passed through to agents; they are NOT stored
- CORS should be configured for local dev (`localhost:*`)
- `app.main` mounts the router from `app/api/routes.py`

## Commands

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
pytest tests/test_api.py
```