# Investigation Team - CLAUDE.md

**Piece:** Step 4 (Investigator orchestrator + parallel fact-checking agents)
**Main docs:** [../../CLAUDE.md](../../CLAUDE.md)

## Your Scope

| File                  | Purpose                                                        |
| --------------------- | -------------------------------------------------------------- |
| `investigator.py`     | Identify claims; orchestrate agents via `asyncio.gather`       |
| `search_agent.py`     | Search for corroborating/contradicting sources (tavily-search) |
| `fakeness_agent.py`   | Detect AI-generated content likelihood                         |
| `statistics_agent.py` | Verify cited data/statistics against authoritative sources     |
| `source_checker.py`   | Check author/publisher credibility and timestamp plausibility  |

## Technology

- Claude `claude-sonnet-4-6` - identify claims in text, evaluate source evidence
- `tavily-search` (Tavily MCP) - find sources per claim
- `tavily-extract` (Tavily MCP) - pull full content from source pages

## Tavily Configuration (from `.env`)

| Variable                | Default    | Description                             |
| ----------------------- | ---------- | --------------------------------------- |
| `TAVILY_SEARCH_DEPTH`   | `advanced` | `basic` = faster, `advanced` = thorough |
| `TAVILY_MAX_RESULTS`    | `5`        | Max search results per claim            |
| `PRIORITISE_LOCAL`      | `false`    | Boost country-specific sources          |
| `COUNTRY`               | unset      | Country filter, e.g. `Singapore`        |
| `MIN_SOURCES_PER_CLAIM` | `2`        | Minimum independent sources per claim   |

## Input Contract

Receives `IngestionResult` from the Ingestion module (defined in `app/models/schemas.py`):
- `article` - metadata (url, publisher, author, date, etc.)
- `text` - cleaned English article text

## Output Contract

Produce an `InvestigationResult` Pydantic model and add it to `app/models/schemas.py`.

```python
class Source(BaseModel):
    name: str
    url: str
    type: str             # e.g. "news", "government", "academic"
    is_independent: bool

class Claim(BaseModel):
    claim_id: int
    claim_summary: str
    extract: str          # direct quote from the article
    verdict: Literal["true", "likely_true", "unverified", "likely_false", "false"]
    reason: str
    government_source_only: bool
    sources: List[Source]

class PublisherCredibility(BaseModel):
    score: int            # 0-100
    rating: Literal["highly_credible", "credible", "mixed", "questionable", "not_credible"]
    summary: str
    bias: Literal["far_left", "left", "centre_left", "centre", "centre_right", "right", "far_right", "unknown"]
    known_issues: List[str]
    fact_checker_ratings: List[str]

class InvestigationResult(BaseModel):
    claims: List[Claim]
    publisher_credibility: PublisherCredibility
    fakeness_score: int   # 0-100 (AI-generated content likelihood)
```

## Parallelism

All four sub-agents run concurrently in `investigator.py`:

```python
results = await asyncio.gather(
    search_agent.run(claims, article),
    fakeness_agent.run(text),
    statistics_agent.run(claims),
    source_checker.run(article),
)
```

## Key Conventions

- Source trustworthiness weighting is read from `app/config.py` (not hardcoded in agents)
- `government_source_only` is `True` when all sources for a claim are government-owned
- Each agent returns a structured Pydantic model; `investigator.py` merges them

## Credibility Reference Sources

Source trustworthiness weighting in `app/config.py` should reflect this ordering.
Priority is given to Singapore-specific sources.

### Singapore-Specific (Highest Priority)

| Source            | URL                             |
| ----------------- | ------------------------------- |
| CNA               | https://www.channelnewsasia.com |
| The Straits Times | https://www.straitstimes.com    |
| Data.gov.sg       | https://www.data.gov.sg         |
| MAS               | https://www.mas.gov.sg          |
| MOH               | https://www.moh.gov.sg          |
| Factually         | https://www.factually.gov.sg/   |
| POFMA             | https://www.pofmaoffice.gov.sg/ |

### General News

| Source           | URL                      |
| ---------------- | ------------------------ |
| Reuters          | https://www.reuters.com  |
| Associated Press | https://www.apnews.com   |
| BBC News         | https://www.bbc.com/news |
| NPR              | https://www.npr.org      |

### Politics & Government / Fact-Checkers

| Source        | URL                         |
| ------------- | --------------------------- |
| PolitiFact    | https://www.politifact.com  |
| FactCheck.org | https://www.factcheck.org   |
| Snopes        | https://www.snopes.com      |
| Ballotpedia   | https://www.ballotpedia.org |

### Finance & Economics

| Source              | URL                            |
| ------------------- | ------------------------------ |
| Bloomberg           | https://www.bloomberg.com      |
| Financial Times     | https://www.ft.com             |
| Wall Street Journal | https://www.wsj.com            |
| IMF                 | https://www.imf.org            |
| World Bank          | https://www.worldbank.org      |
| Federal Reserve     | https://www.federalreserve.gov |

### Science & Health

| Source     | URL                             |
| ---------- | ------------------------------- |
| WHO        | https://www.who.int             |
| CDC        | https://www.cdc.gov             |
| NIH        | https://www.nih.gov             |
| Nature     | https://www.nature.com          |
| PubMed     | https://pubmed.ncbi.nlm.nih.gov |
| The Lancet | https://www.thelancet.com       |

### Statistics & Data

| Source               | URL                            |
| -------------------- | ------------------------------ |
| Our World in Data    | https://www.ourworldindata.org |
| Statista             | https://www.statista.com       |
| UN Data              | https://data.un.org            |
| World Bank Open Data | https://data.worldbank.org     |
| Pew Research Center  | https://www.pewresearch.org    |

### Technology

| Source                | URL                              |
| --------------------- | -------------------------------- |
| MIT Technology Review | https://www.technologyreview.com |
| Wired                 | https://www.wired.com            |
| TechCrunch            | https://www.techcrunch.com       |
| IEEE                  | https://www.ieee.org             |

### Sports

| Source     | URL                       |
| ---------- | ------------------------- |
| ESPN       | https://www.espn.com      |
| BBC Sport  | https://www.bbc.com/sport |
| Sky Sports | https://www.skysports.com |
| FIFA       | https://www.fifa.com      |
| NBA        | https://www.nba.com       |
| UEFA       | https://www.uefa.com      |

### Environment & Climate

| Source       | URL                         |
| ------------ | --------------------------- |
| IPCC         | https://www.ipcc.ch         |
| NOAA         | https://www.noaa.gov        |
| NASA Climate | https://climate.nasa.gov    |
| Carbon Brief | https://www.carbonbrief.org |

## Commands

```bash
pip install -r requirements.txt
pytest tests/test_investigation.py
```