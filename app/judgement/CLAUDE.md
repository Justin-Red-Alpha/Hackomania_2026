# Judgement Team - CLAUDE.md

**Piece:** Step 5 (Aggregate investigation results into a final verdict)
**Main docs:** [../../CLAUDE.md](../../CLAUDE.md)

## Your Scope

| File           | Purpose                                                       |
| -------------- | ------------------------------------------------------------- |
| `judgement.py` | Aggregate agent outputs into Degree of Fakeness (%) + verdict |
| `../config.py` | Source trustworthiness weighting config (create if missing)   |

## Input Contract

Receives both `IngestionResult` and `InvestigationResult` from upstream modules
(defined in `app/models/schemas.py`).

From `IngestionResult`: `content` metadata
From `InvestigationResult`:
- `claims[]` with per-claim verdicts and source details (including `extracted_text` per source)
- `publisher_credibility` score and rating
- `fakeness_score` (0-100)

## Output Contract

Produce `JudgementResult` and add all new models to `app/models/schemas.py`.

```python
class WritingQuality(BaseModel):
    sensationalism_score: int    # 0-100
    uses_named_sources: bool
    uses_anonymous_sources: bool
    emotional_language: bool

class ContentCredibility(BaseModel):     # was: ArticleCredibility
    score: int    # 0-100 overall credibility score
    rating: Literal["credible", "likely_credible", "uncertain", "likely_false", "false"]
    summary: str  # human-readable verdict summary
    total_claims_found: int
    claims_true: int
    claims_likely_true: int
    claims_unverified: int
    claims_likely_false: int
    claims_false: int
    government_source_only_flag: bool
    writing_quality: WritingQuality

class ClaimEvidence(BaseModel):
    source_id: str            # UUID matching sources.id in the database
    source_name: str          # denormalised for convenience
    source_url: str           # denormalised for convenience
    snippet: str              # specific excerpt from source's extracted text that relates to claim
    supports_claim: bool      # True = this evidence supports the claim; False = contradicts it
    judgement_reason: str     # explanation of why this snippet affects the claim verdict

class JudgedClaim(BaseModel):
    claim_id: int
    claim_summary: str
    extract: str              # direct quote from the article being fact-checked
    verdict: Literal["true", "likely_true", "unverified", "likely_false", "false"]
    overall_reason: str       # consolidated reasoning across all evidence
    government_source_only: bool
    sources: List[Source]     # full source records from Investigation (includes extracted_text)
    evidence: List[ClaimEvidence]  # curated evidence snippets identified by judgement agent

class JudgementResult(BaseModel):
    content: ContentMetadata               # from IngestionResult
    publisher_credibility: PublisherCredibility  # from InvestigationResult
    content_credibility: ContentCredibility      # was: article_credibility
    claims: List[JudgedClaim]              # upgraded from List[Claim]
```

## Scoring Algorithm

1. **Evidence identification** - for each claim, review `source.extracted_text` from the
   investigation results. Use Claude to identify the specific excerpt (`snippet`) that most
   directly supports or contradicts the claim. Populate `ClaimEvidence` for each source.
2. **Claim verdict weighting** - each verdict contributes to the overall score:
   - `true` -> +full weight
   - `likely_true` -> +partial weight
   - `unverified` -> neutral
   - `likely_false` -> -partial weight
   - `false` -> -full weight
3. **Source weighting** (from `app/config.py`, not hardcoded):
   - Government/official sources -> increase trustworthiness
   - Known misinformation outlets -> decrease trustworthiness
4. **Publisher credibility factor** - blend `publisher_credibility.score` into final score
5. **Fakeness penalty** - if `fakeness_score > threshold`, reduce overall score
6. **Government-source-only flag** - set `True` when all sources for any claim are govt-owned

## Key Conventions

- Source weighting config lives in `app/config.py`, NOT hardcoded in `judgement.py`
- `excludeGovernmentSourcesOnly` flag: if `True`, flag verdicts backed only by govt sources
- Each `ClaimEvidence.snippet` must be a verbatim excerpt from `source.extracted_text`
- `ClaimEvidence.source_id` must match the corresponding row in the `sources` DB table
- See [DATABASE.md](../../DATABASE.md) for the `analysis` and `evidence` table schemas

## Commands

```bash
pip install -r requirements.txt
pytest tests/test_judgement.py
```