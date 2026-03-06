# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A misinformation / fake news detection tool built for Hackomania 2026. Users submit an article; a pipeline of AI agents analyses it and returns a **Degree of Fakeness** score with a summarised verdict.

## Pipeline (from `framework.txt`)

1. **Ingestion** — Accept input as plain text or file upload (PDF, HTML, MD, DOCX, RTF, or image).
2. **Extraction** — Process the ingested input to extract raw text content.
3. **Summarisation** — Extract article context/transcript, timestamp, author, and links to cited statistics.
4. **Investigator** — Analyse the extracted content, identify individual claims or statements that require fact-checking, then orchestrate the parallel agents below to gather evidence for each claim:
   - **Search Agent** — Finds corroborative or contradicting articles.
   - **Fakeness Agent** — Detects AI-generated content.
   - **Statistics Agent** — Verifies data/statistics cited in the article.
   - **Source Checker Agent** — Checks author credibility and timestamp plausibility.
5. **Judgement** — Aggregate agent outputs into a Degree of Fakeness (%) with weighted corroboration (government sources increase trustworthiness; known misinformation outlets decrease it).
6. **Database** — Store article summaries and corroborative articles for future lookups.

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
    ingestion_agent.py     # Step 1:  Accepts plain text or file uploads (PDF, HTML, MD, DOCX, RTF, image)
    extraction_agent.py    # Step 2:  Extracts raw text from ingested input
    summariser.py          # Step 3:  Article summariser (only for large token count input)
    investigator.py        # Step 4:  Identifies claims and orchestrates parallel fact-checking agents
    search_agent.py        # Step 4a: Searches for content that support or debunks claim
    fakeness_agent.py      # Step 4b: AI-generated content detection
    statistics_agent.py    # Step 4c: (optional): Queries data sources and wrangle data
    source_checker.py      # Step 4d: (optional): Researches the source of the claim and the claimer's credibility
  judgement.py             # Step 5:  Analyses findings and aggregates scores with source weighting
  db.py                    # Step 6:  Database persistence
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


## Credibility Reference Sources

Priority is given to Singapore-specific sources. Source trustworthiness weighting in `judgement.py` should reflect this ordering.

### Singapore-Specific (Highest Priority)
| Source | URL |
|---|---|
| CNA | https://www.channelnewsasia.com |
| The Straits Times | https://www.straitstimes.com |
| Data.gov.sg | https://www.data.gov.sg |
| MAS | https://www.mas.gov.sg |
| MOH | https://www.moh.gov.sg |
| Factually | https://www.factually.gov.sg/ |
| POFMA | https://www.pofmaoffice.gov.sg/ |

### General News
| Source | URL |
|---|---|
| Reuters | https://www.reuters.com |
| Associated Press | https://www.apnews.com |
| BBC News | https://www.bbc.com/news |
| NPR | https://www.npr.org |

### Politics & Government / Fact-Checkers
| Source | URL |
|---|---|
| PolitiFact | https://www.politifact.com |
| FactCheck.org | https://www.factcheck.org |
| Snopes | https://www.snopes.com |
| Ballotpedia | https://www.ballotpedia.org |

### Finance & Economics
| Source | URL |
|---|---|
| Bloomberg | https://www.bloomberg.com |
| Financial Times | https://www.ft.com |
| Wall Street Journal | https://www.wsj.com |
| IMF | https://www.imf.org |
| World Bank | https://www.worldbank.org |
| Federal Reserve | https://www.federalreserve.gov |

### Science & Health
| Source | URL |
|---|---|
| WHO | https://www.who.int |
| CDC | https://www.cdc.gov |
| NIH | https://www.nih.gov |
| Nature | https://www.nature.com |
| PubMed | https://pubmed.ncbi.nlm.nih.gov |
| The Lancet | https://www.thelancet.com |

### Statistics & Data
| Source | URL |
|---|---|
| Our World in Data | https://www.ourworldindata.org |
| Statista | https://www.statista.com |
| UN Data | https://data.un.org |
| World Bank Open Data | https://data.worldbank.org |
| Pew Research Center | https://www.pewresearch.org |

### Technology
| Source | URL |
|---|---|
| MIT Technology Review | https://www.technologyreview.com |
| Wired | https://www.wired.com |
| TechCrunch | https://www.techcrunch.com |
| IEEE | https://www.ieee.org |

### Sports
| Source | URL |
|---|---|
| ESPN | https://www.espn.com |
| BBC Sport | https://www.bbc.com/sport |
| Sky Sports | https://www.skysports.com |
| FIFA | https://www.fifa.com |
| NBA | https://www.nba.com |
| UEFA | https://www.uefa.com |

### Environment & Climate
| Source | URL |
|---|---|
| IPCC | https://www.ipcc.ch |
| NOAA | https://www.noaa.gov |
| NASA Climate | https://climate.nasa.gov |
| Carbon Brief | https://www.carbonbrief.org |


## Technology Stack

- **Claude** (via Anthropic API) — claim identification and scoring
- **Tavily MCP** — article scraping (`tavily-extract`) and source search (`tavily-search`)
- **Apify Actor** — orchestration runtime

## API Schema

### Input (`POST /api/v1/analyse`)

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `articleUrl` | string (URI) | REQ | — | URL of the news article to check |
| `anthropicApiKey` | string (secret) | REQ | — | Claude API key |
| `tavilyApiKey` | string (secret) | REQ | — | Tavily API key for search + extraction |
| `tavilySearchDepth` | enum | OPT | `advanced` | `basic` = faster, `advanced` = thorough |
| `tavilyMaxResults` | integer | OPT | `5` | Max search results per claim |
| `prioritiseLocalSources` | boolean | OPT | `false` | Prioritise country-specific sources |
| `country` | string | OPT | — | e.g. `Singapore`, `USA` |
| `minimumSourcesPerClaim` | integer | OPT | `2` | Min independent sources required per claim |
| `excludeGovernmentSourcesOnly` | boolean | OPT | `true` | Flag verdicts backed only by govt sources |

### Processing Pipeline

| Step | Tool | Action |
|---|---|---|
| 1 | `tavily-extract` | Scrape & clean article text |
| 2 | Claude | Read article & identify claims |
| 3 | `tavily-search` | Search for sources per claim |
| 4 | `tavily-extract` | Pull full content from source pages |
| 5 | Claude | Analyse, score & generate output |

### Output Schema

**`article`** — Article Metadata *(extracted by `tavily-extract`)*
| Field | Type |
|---|---|
| `url` | string |
| `title` | string |
| `publisher` | string |
| `date` | date |
| `author` | string |
| `section` | string |
| `is_opinion` | boolean |

**`publisher_credibility`** — Publisher Credibility Score *(post-check)*
| Field | Type | Notes |
|---|---|---|
| `score` | integer 0–100 | |
| `rating` | enum | `highly_credible` → `not_credible` |
| `summary` | string | |
| `bias` | enum | `far_left` → `far_right` |
| `known_issues[]` | array | |
| `fact_checker_ratings[]` | array | |

**`article_credibility`** — Article Accuracy Score *(post-check)*
| Field | Type | Notes |
|---|---|---|
| `score` | integer 0–100 | |
| `rating` | enum | `credible` → `false` |
| `summary` | string | |
| `total_claims_found` | integer | |
| `claims_true / false / etc` | integer | |
| `government_source_only_flag` | boolean | |
| `writing_quality{}` | object | sensationalism, named sources, etc. |

**`claims[]`** — Individual Claim Breakdown *(post-check)*
| Field | Type | Notes |
|---|---|---|
| `claim_id` | integer | |
| `claim_summary` | string | |
| `extract` | string | Direct quote from article |
| `verdict` | enum | `true` → `false` |
| `reason` | string | |
| `government_source_only` | boolean | |
| `sources[]` | array | name, url, type, is_independent |

## Key Conventions

- All API routes are prefixed `/api/v1/`.
- Each agent returns a structured Pydantic model consumed by `judgement.py`.
- Agents run concurrently using `asyncio.gather` where possible.
- Secrets (LLM API keys, search API keys) are loaded from `.env` via `python-dotenv`.
- Source trustworthiness weighting lives in a config dict/file, not hardcoded in agents.
