# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A misinformation / fake news detection tool built for Hackomania 2026. Users submit an article;
a pipeline of AI agents analyses it and returns a **Degree of Fakeness** score with a summarised verdict.

## Pipeline

1. **Ingestion** - Accept input as plain text or file upload (PDF, HTML, MD, DOCX, RTF, or image).
2. **Extraction** - Extract raw text and metadata; translate non-English content to English.
3. **Summarisation** - Summarise content if token count > 4000; otherwise pass through unchanged.
4. **Investigator** - Identify individual claims, then orchestrate parallel agents:
   - **Search Agent** - Finds corroborative or contradicting articles.
   - **Fakeness Agent** - Detects AI-generated content.
   - **Statistics Agent** - Verifies data/statistics cited in the article.
   - **Source Checker Agent** - Checks author credibility and timestamp plausibility.
5. **Judgement** - Aggregate agent outputs into a Degree of Fakeness (%) with source weighting.
6. **Database** - Store results for future lookups (cache by URL).

## Team Breakdown

The project is split into 5 independent pieces. Each team has its own `CLAUDE.md` with full details.

| Team              | Scope                                                   | CLAUDE.md                                                  |
| ----------------- | ------------------------------------------------------- | ---------------------------------------------------------- |
| **Ingestion**     | Steps 1-3: ingest, extract, summarise                   | [app/ingestion/CLAUDE.md](app/ingestion/CLAUDE.md)         |
| **Investigation** | Step 4: identify claims, run parallel fact-check agents | [app/investigation/CLAUDE.md](app/investigation/CLAUDE.md) |
| **Judgement**     | Step 5: aggregate scores into final verdict             | [app/judgement/CLAUDE.md](app/judgement/CLAUDE.md)         |
| **Database**      | Step 6: cache and retrieve analysis results             | [app/database/CLAUDE.md](app/database/CLAUDE.md)           |
| **Frontend**      | API routes, Pydantic schemas, and UI                    | [frontend/CLAUDE.md](frontend/CLAUDE.md)                   |

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run dev server (with hot reload)
uvicorn app.main:app --reload --port 8000

# Run tests
pytest

# Run a single test file
pytest tests/test_agents.py

# Run a single test by name
pytest tests/test_agents.py::test_function_name -v

# Lint / format
ruff check .
ruff format .
```

## Intended Project Structure

```
app/
  main.py                       # FastAPI entry point, mounts routers
  config.py                     # Source trustworthiness weighting config
  ingestion/
    CLAUDE.md                   # Team: Ingestion
    ingestion_agent.py          # Step 1:  Accept plain text or file uploads
    extraction_agent.py         # Step 2:  Extract text and metadata
    summariser.py               # Step 3:  Summarise long content
  investigation/
    CLAUDE.md                   # Team: Investigation
    investigator.py             # Step 4:  Orchestrates parallel fact-checking agents
    search_agent.py             # Step 4a: Finds corroborating/contradicting sources
    fakeness_agent.py           # Step 4b: AI-generated content detection
    statistics_agent.py         # Step 4c: Verifies cited statistics
    source_checker.py           # Step 4d: Checks author/publisher credibility
  judgement/
    CLAUDE.md                   # Team: Judgement
    judgement.py                # Step 5:  Aggregates scores into final verdict
  database/
    CLAUDE.md                   # Team: Database
    db.py                       # Step 6:  Database read/write and cache
  api/
    routes.py                   # POST /api/v1/analyse endpoint (Frontend team)
  models/
    schemas.py                  # Shared Pydantic models (all teams contribute)
frontend/
  CLAUDE.md                     # Team: Frontend
  index.html                    # Article submission UI + results display
  static/                       # JS, CSS
tests/
.env                            # API keys (not committed)
```

## Technology Stack

- **Claude** (`claude-sonnet-4-6`) - claim identification and scoring
- **Tavily MCP** - article scraping (`tavily-extract`) and source search (`tavily-search`)
- **FastAPI** - web framework
- **Pydantic** - data validation and shared schemas

## Key Conventions

- All API routes are prefixed `/api/v1/`.
- Each agent returns a structured Pydantic model; models are defined in `app/models/schemas.py`.
- Agents run concurrently using `asyncio.gather` where possible.
- Secrets (API keys) are loaded from `.env` via `python-dotenv`.
- Source trustworthiness weighting lives in `app/config.py`, not hardcoded in agents.
- All DB functions are `async`.
- Database schema, table definitions, S3 conventions, and write responsibilities are in [DATABASE.md](DATABASE.md).

## General Directives

- **No emojis.** Keep all output, UI, and code comments professional and emoji-free.
- **Commit and push regularly** to https://github.com/Justin-Red-Alpha/Hackomania_2026.git
  after each meaningful unit of work.
- **Use Server-Sent Events (SSE)** for the `/api/v1/analyse` endpoint so that intermediate pipeline
  events (agent start, agent complete, partial results) are streamed to the frontend in real time.
  All pipeline stages must emit SSE events; the frontend must consume and display them progressively.
- **Log verbosely.** Every pipeline stage, tool call, agent activation, and SSE event emitted must
  be logged at `DEBUG` level using structured logging so that logs can be parsed for both debugging
  and real-time event notification. Include timestamps, stage name, and relevant identifiers.
- **Maintain `CHANGELOG.md`** at the repo root. Record every significant change (new feature, schema
  change, renamed field, new table, breaking change) with the date and your name so all team members
  stay informed of what has changed.
- **Start each feature in a new branch.** Branch off `main` before beginning any new feature or
  fix. Use descriptive branch names (e.g. `feat/ingestion-pdf`, `fix/judgement-weighting`). Merge
  back to `main` via pull request once the work is complete and tested.