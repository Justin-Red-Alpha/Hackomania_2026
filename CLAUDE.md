# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A misinformation / fake news detection tool built for Hackomania 2026. Users submit an article; a pipeline of AI agents analyses it and returns a **Degree of Fakeness** score with a summarised verdict.

## Pipeline (from `framework.txt`)

1. **Summarisation** — Extract article context/transcript, timestamp, author, and links to cited statistics.
2. **Agent Analysis** (parallel agents):
   - **Search Agent** — Finds corroborative articles.
   - **Fakeness Agent** — Detects AI-generated content.
   - **Statistics Agent** — Verifies data/statistics cited in the article.
   - **Source Checker Agent** — Checks author credibility and timestamp plausibility.
3. **Judgement** — Aggregate agent outputs into a Degree of Fakeness (%) with weighted corroboration (government sources increase trustworthiness; known misinformation outlets decrease it).
4. **Database** — Store article summaries and corroborative articles for future lookups.

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
  main.py                  # FastAPI entry point, mounts routers
  agents/
    summariser.py          # Step 1:  Article summariser (only for large token count input)
    search_agent.py        # Step 2a: Searches for content that support or debunks claim
    fakeness_agent.py      # Step 2b: AI-generated content detection
    statistics_agent.py    # Step 2c: (optional): Queries data sources and wrangle data
    source_checker.py      # Step 2d: (optional): Researches the source of the claim and the claimer's credibility
  judgement.py             # Step 3:  Analyses findings and aggregates scores with source weighting
  db.py                    # Step 4:  Database persistence
  api/
    routes.py              # POST /analyse endpoint
  models/
    schemas.py             # Pydantic request/response models
frontend/
  index.html               # Article submission UI + results display
  static/                  # JS, CSS
tests/
.env                       # API keys (not committed)
```

## Key Conventions

- All API routes are prefixed `/api/v1/`.
- Each agent returns a structured Pydantic model consumed by `judgement.py`.
- Agents run concurrently using `asyncio.gather` where possible.
- Secrets (LLM API keys, search API keys) are loaded from `.env` via `python-dotenv`.
- Source trustworthiness weighting lives in a config dict/file, not hardcoded in agents.
