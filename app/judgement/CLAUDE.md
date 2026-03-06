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

From `IngestionResult`: `article` metadata
From `InvestigationResult`:
- `claims[]` with per-claim verdicts and source details
- `publisher_credibility` score and rating
- `fakeness_score` (0-100)

## Output Contract

Produce a `JudgementResult` Pydantic model and add it to `app/models/schemas.py`.

```python
class WritingQuality(BaseModel):
    sensationalism_score: int    # 0-100
    uses_named_sources: bool
    uses_anonymous_sources: bool
    emotional_language: bool

class ArticleCredibility(BaseModel):
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

class JudgementResult(BaseModel):
    article: ArticleMetadata           # from IngestionResult
    publisher_credibility: PublisherCredibility  # from InvestigationResult
    article_credibility: ArticleCredibility
    claims: List[Claim]                # from InvestigationResult
```

## Scoring Algorithm

1. **Claim verdict weighting** - each verdict contributes to the overall score:
   - `true` -> +full weight
   - `likely_true` -> +partial weight
   - `unverified` -> neutral
   - `likely_false` -> -partial weight
   - `false` -> -full weight
2. **Source weighting** (from `app/config.py`, not hardcoded):
   - Government/official sources -> increase trustworthiness
   - Known misinformation outlets -> decrease trustworthiness
3. **Publisher credibility factor** - blend `publisher_credibility.score` into final score
4. **Fakeness penalty** - if `fakeness_score > threshold`, reduce overall score
5. **Government-source-only flag** - set `True` when all sources for any claim are govt-owned

## Key Conventions

- Source weighting config lives in `app/config.py`, NOT hardcoded in `judgement.py`
- `excludeGovernmentSourcesOnly` flag: if `True`, flag verdicts backed only by govt sources
- Each claim verdict contributes individually to the aggregate score

## Commands

```bash
pip install -r requirements.txt
pytest tests/test_judgement.py
```