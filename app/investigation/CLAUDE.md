# Investigation Team - CLAUDE.md

**Piece:** Step 4 (Investigator orchestrator + parallel fact-checking agents)
**Main docs:** [../../CLAUDE.md](../../CLAUDE.md)

## Your Scope

| File                  | Purpose                                                       |
| --------------------- | ------------------------------------------------------------- |
| `investigator.py`     | Identify claims; orchestrate agents via `asyncio.gather`      |
| `search_agent.py`     | Search for sources; extract full content; archive HTML to S3  |
| `fakeness_agent.py`   | Detect AI-generated content likelihood                        |
| `statistics_agent.py` | Verify cited data/statistics against authoritative sources    |
| `source_checker.py`   | Check author/publisher credibility and timestamp plausibility |

## Technology

- Claude `claude-sonnet-4-6` - identify claims in text, evaluate source evidence
- `tavily-search` (Tavily MCP) - find sources per claim
- `tavily-extract` (Tavily MCP) - pull full content from source pages
- `aioboto3` - async upload of source HTML pages to AWS S3
- **GPTZero API** (`fakeness_agent.py` only) - AI-generated text detection; no Tavily/Claude equivalent. Add `GPTZERO_API_KEY` to `.env`. Endpoint: `https://api.gptzero.me/v2/predict/text`

## Tavily Configuration (from `.env`)

| Variable                | Default    | Description                             |
| ----------------------- | ---------- | --------------------------------------- |
| `TAVILY_SEARCH_DEPTH`   | `advanced` | `basic` = faster, `advanced` = thorough |
| `TAVILY_MAX_RESULTS`    | `5`        | Max search results per claim            |
| `PRIORITISE_LOCAL`      | `false`    | Boost country-specific sources          |
| `COUNTRY`               | unset      | Country filter, e.g. `Singapore`        |
| `MIN_SOURCES_PER_CLAIM` | `2`        | Minimum independent sources per claim   |
| `MAX_HOP_DEPTH`         | `2`        | Max citation hops when chasing primary sources  |

## Input Contract

Receives `IngestionResult` from the Ingestion module (defined in `app/models/schemas.py`):
- `article` - metadata (url, publisher, author, date, etc.)
- `text` - cleaned English article text

## Output Contract

Produce an `InvestigationResult` Pydantic model and add it to `app/models/schemas.py`.

```python
class ClaimSource(BaseModel):
    name: str
    url: str
    source_type: str              # e.g. "news", "government", "academic"
    is_independent: bool
    is_primary_source: bool       # True = own data/analysis; False = relays claim from elsewhere
    hop_depth:         int            = 0      # 0 = direct result; 1+ = citation chased
    s3_url: Optional[str] = None  # S3 link to archived HTML copy of source page
    extracted_text: Optional[str] = None  # full text extracted from source page

class Claim(BaseModel):
    claim_id: int
    claim_summary: str
    extract: str          # direct quote of the claim or statement from the article
    verdict: ClaimVerdict  # true | mostly_true | misleading | unverified | mostly_false | false
    reason: str
    government_source_only: bool
    sources: List[ClaimSource]

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

## Search Agent Architecture

`search_agent.py` uses a bounded agentic loop to chase citations back to primary sources rather
than accepting mere mentions as evidence.

### Evidence tiers

| Tier | Description | `is_primary_source` | Action |
| ---- | ----------- | ------------------- | ------ |
| **Primary** | Source provides its own data, analysis, or official statement | `True` | Keep |
| **Secondary - with citation** | Relays a claim and cites a specific URL | `False` | Follow cited URL; re-classify |
| **Mention-only** | Relays a claim with no citation trail | `False` | Discard |

### Loop (per claim, simplified)

```
tavily-search(claim)
  for each result URL (parallel via asyncio.gather):
    tavily-extract(url)
      Claude classifies: primary | secondary-with-citation | mention-only
        primary                  -> keep (is_primary_source=True, hop_depth=current)
        secondary-with-citation  -> if hop_depth < MAX_HOP_DEPTH:
                                      tavily-extract(cited_url, hop_depth+1) -> re-classify
        mention-only             -> discard
```

- The Claude classification prompt must return both the tier and any cited URL found in the text.
- `MAX_HOP_DEPTH` (default 2) caps recursion; most primary sources are one hop away.
- Hops on independent cited URLs are parallelised with `asyncio.gather`.

## Key Conventions

- Source trustworthiness weighting is read from `app/config.py` (not hardcoded in agents)
- `government_source_only` is `True` when all sources for a claim are government-owned
- Each agent returns a structured Pydantic model; `investigator.py` merges them
- See [DATABASE.md](../../DATABASE.md) for the `sources` table schema and S3 archival conventions

## Credibility Reference Sources

Source trustworthiness weighting in `app/config.py` should reflect this ordering.
Priority is given to Singapore-specific sources.

### Singapore-Specific (Highest Priority)

| Source              | URL                              | Access method                              |
| ------------------- | -------------------------------- | ------------------------------------------ |
| CNA                 | https://www.channelnewsasia.com  | Tavily search/extract; RSS available       |
| The Straits Times   | https://www.straitstimes.com     | **Paywalled** - snippet/signal only        |
| Data.gov.sg         | https://www.data.gov.sg          | New REST API (key in .env); Tavily fallback |
| MAS                 | https://www.mas.gov.sg           | Direct REST API - no Tavily needed         |
| MOH                 | https://www.moh.gov.sg           | Tavily search/extract on moh.gov.sg        |
| SingStat            | https://www.singstat.gov.sg      | Tavily search/extract; some datasets via API |
| Factually           | https://www.factually.gov.sg/    | Tavily search restricted to domain         |
| POFMA               | https://www.pofmaoffice.gov.sg/  | Use Factually (mirrors POFMA); PDF cache   |

### Singapore Source Notes

- **Factually first**: Always search `factually.gov.sg` before other sources. A hit is definitive government rebuttal of the claim.
- **POFMA PDF cache**: At startup, download and parse `pofmaoffice.gov.sg/files/tabulation_of_pofma_cases_and_actions.pdf` (88 cases as of Sep 2025) into a local lookup table for fast claim cross-referencing.
- **MAS API**: Call `eservices.mas.gov.sg/apimg-portal/` directly for financial/monetary statistics. The `DATA_GOV_API_KEY` in `.env` is for data.gov.sg only; MAS endpoints are unauthenticated.
- **Straits Times**: Do not attempt full-text extraction. Log `extracted_text = None` and treat the URL as a credibility signal only.
- **Data.gov.sg**: CKAN API was discontinued Dec 2025. Use the new AWS-backed REST API with the `DATA_GOV_API_KEY` from `.env`.

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