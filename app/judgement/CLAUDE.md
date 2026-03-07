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

Produce `JudgementResult`. All models are defined in `app/models/schemas.py`
(source of truth — do not duplicate definitions here).

Current model shapes for reference:

```python
class WritingQuality(BaseModel):
    sensationalism:     Optional[bool] = None
    named_sources:      Optional[bool] = None
    anonymous_sources:  Optional[bool] = None
    emotional_language: Optional[bool] = None
    hedging_language:   Optional[bool] = None

class ContentCredibility(BaseModel):
    score:                       int                      # 0-100
    rating:                      ContentRating            # credible → not_credible
    summary:                     Optional[str]            # human-readable verdict; include conflict note when is_conflicted=True
    total_claims_found:          int
    claims_true:                 int
    claims_mostly_true:          int
    claims_misleading:           int
    claims_unverified:           int
    claims_mostly_false:         int
    claims_false:                int
    government_source_only_flag: bool
    writing_quality:             Optional[WritingQuality]

class ClaimEvidence(BaseModel):
    source_id:        str            # UUID matching sources.id in the database
    source_name:      str            # denormalised for convenience
    source_url:       str            # denormalised for convenience
    snippet:          str            # verbatim excerpt from source.extracted_text
    is_relevant:      bool           # False when source cannot confirm or deny the claim (unreadable, wrong time period, metadata-only, etc.)
    supports_claim:   bool           # True = supports; False = contradicts; only meaningful when is_relevant=True
    judgement_reason: Optional[str]  # why this snippet affects the verdict, or why the source is not relevant

class JudgedClaim(BaseModel):
    claim_id:               int
    claim_summary:          str
    extract:                Optional[str]       # direct quote from the article
    verdict:                ClaimVerdict        # re-evaluated after evidence
    overall_reason:         Optional[str]       # consolidated reasoning across all evidence
    government_source_only: bool
    sources:                List[ClaimSource]   # full source records (includes extracted_text)
    evidence:               List[ClaimEvidence]

class JudgementResult(BaseModel):
    content:               ContentMetadata
    publisher_credibility: PublisherCredibility
    content_credibility:   ContentCredibility
    claims:                List[JudgedClaim]
    is_conflicted:         bool = False         # True when |claims_score - publisher_score| >= CONFLICT_GAP_THRESHOLD
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
   - `is_primary_source=True` -> higher weight than secondary sources
   - `hop_depth` -> apply a small configurable decay per hop (e.g. 0.9 per hop); a primary
     source reached via 2 citation hops is still more valuable than a mention-only source
4. **Publisher credibility factor** - blend `publisher_credibility.score` into final score
5. **Fakeness penalty** - if `fakeness_score > threshold`, reduce overall score
6. **Government-source-only flag** - set `True` when all sources for any claim are govt-owned

## Evidence Evaluation

For each claim received from InvestigationResult:

1. Read all source.extracted_text values attached to the claim

2. Deduplicate sources — if two or more sources contain identical or
   near-identical snippets (indicating syndicated content), silently
   discard duplicates and keep only one. Do not log discarded URLs.

3. Use Claude to identify the specific verbatim snippet from each
   deduplicated source that most directly supports or contradicts
   the claim. All sources for a claim are evaluated concurrently
   via asyncio.gather. Be aware of hop_depth when evaluating
   snippets — snippets from higher hop_depth sources are inherently
   weaker evidence and should be noted in judgement_reason.

4. Populate ClaimEvidence for each source with:
   - snippet: verbatim excerpt from extracted_text
   - is_relevant: False if the source is unreadable (e.g. binary PDF),
     covers a different time period, contains only metadata/citations,
     or otherwise cannot confirm or deny the claim. True otherwise.
   - supports_claim: True if evidence supports, False if it contradicts.
     Only meaningful when is_relevant=True.
   - judgement_reason: explanation of why this snippet affects the
     verdict (or why the source is not relevant), including notes on
     hop_depth weakness

5. Apply is_primary_source weighting during evidence evaluation:
   - is_primary_source=True → evidence carries PRIMARY_SOURCE_MULTIPLIER (1.2x)
   - is_primary_source=False → evidence carries SECONDARY_SOURCE_MULTIPLIER (0.8x)
   - This applies in ALL cases, not just when evidence conflicts

6. Apply hop_depth awareness during evidence evaluation:
   - hop_depth=0 → full evidence weight
   - hop_depth=1 → note as one citation hop away from original source
   - hop_depth=2 → note as two citation hops away, treat with reduced confidence
   - Flag high hop_depth in judgement_reason

7. If a source has extracted_text = None (e.g. paywalled source), skip
   snippet extraction for that source and note the limitation in overall_reason

8. Re-evaluate the claim verdict based on the cumulative weight and
   direction of all deduplicated evidence found

---

## Scoring Weights

Base verdict weights are defined in config.py as VERDICT_BASE_WEIGHTS.
Do not hardcode them in judgement.py.

Per-source score calculation:
0. Skip source entirely if is_relevant=False (source cannot confirm or deny)
1. Start with base verdict weight from VERDICT_BASE_WEIGHTS
2. Apply direction:
   - supports_claim=True  → keep weight as positive
   - supports_claim=False → flip sign, weight becomes negative
   (a contradicting source actively pulls the claim score down)
3. × source quality multiplier (PRIMARY_SOURCE_MULTIPLIER or SECONDARY_SOURCE_MULTIPLIER)
4. × hop depth decay (HOP_DEPTH_DECAY ^ hop_depth)

Aggregation and normalization:
- Sum all per-source signed weighted values for the claim
- Guard: if max_possible_sum == 0 (no sources with extracted text),
  skip normalization and return 50 (neutral) directly
- Otherwise normalize the raw sum to 0–100 using:
    normalized = (raw_sum / max_possible_sum + 1) / 2 * 100
  where max_possible_sum = sum of abs(weight) across all sources for this claim
- This maps a fully contradicted claim to ~0 and a fully supported claim to ~100
- Balanced evidence maps to ~50

Scoring chain order:
1. Base verdict weight (VERDICT_BASE_WEIGHTS in config.py)
2. × direction (supports_claim=False flips sign)
3. × source quality multiplier (is_primary_source)
4. × hop depth decay
   → aggregate (sum) all per-source values
   → normalize raw sum to 0–100
   → if max_possible_sum == 0, return 50 directly
5. × net source confidence multiplier (NET_CONFIDENCE_MULTIPLIERS)
   Clamp per-claim score to 0–100 immediately after this step
   SKIP steps 5 and 5a entirely when max_possible_sum == 0 (all sources inconclusive)
5a. if government_source_only=True: × GOVERNMENT_ONLY_BOOST, re-clamp to 0–100
   SKIP when max_possible_sum == 0 (no relevant scoring evidence)
6. → average all claim scores = claims_score
7. blend: (claims_score × CLAIMS_BLEND_WEIGHT) + (publisher_score × PUBLISHER_BLEND_WEIGHT)
8. if fakeness_score > FAKENESS_THRESHOLD: × FAKENESS_PENALTY_MULTIPLIER
9. clamp final score to 0–100

---

## Net Source Confidence Multiplier

After all sources for a claim are aggregated and normalized, apply a net
source confidence multiplier. This reflects how many independent sources
support vs contradict the claim.

Net sources = count of sources where supports_claim=True minus count where
supports_claim=False (after deduplication, excluding is_relevant=False sources).

Clamp net to boundaries before lookup:
- If net < NET_CONFIDENCE_MIN, use NET_CONFIDENCE_MIN
- If net > NET_CONFIDENCE_MAX, use NET_CONFIDENCE_MAX

Multiplier table (defined in config.py as NET_CONFIDENCE_MULTIPLIERS):
- net ≤ -2 → 0.6x (contradicting evidence outweighs)
- net = -1 → 0.8x
- net = 0  → 1.0x (balanced, baseline)
- net = 1  → 1.0x (baseline, minimum sources met)
- net = 2  → 1.1x
- net = 3  → 1.2x
- net = 4  → 1.3x
- net ≥ 5  → 1.4x (cap, diminishing returns)

Note: net=0 and net=1 both use 1.0x intentionally — net=1 is the minimum
expected baseline. A comment in config.py clarifies this.

---

## Source Weighting

All source weighting config must live in app/config.py, not hardcoded in judgement.py.

- Government/official sources → increase trustworthiness
- Known misinformation outlets → decrease trustworthiness
- is_primary_source=True → PRIMARY_SOURCE_MULTIPLIER (1.2x)
- is_primary_source=False → SECONDARY_SOURCE_MULTIPLIER (0.8x)
- Hop depth decay: HOP_DEPTH_DECAY (0.9) per hop
- Syndicated sources: silently deduplicate, keep one, discard the rest

Government source only modifier (defined in config.py):
- When government_source_only=True for a claim → apply GOVERNMENT_ONLY_BOOST (1.1x)
  at step 5a, after net source multiplier, before averaging claims
- Re-clamp per-claim score to 0–100 after applying boost
- AND surface a UI warning flag in JudgementResult

---

## Rating Thresholds

Map final score to ContentRating as follows (fixed, does not shift):
- 80–100 → credible
- 60–79 → mostly_credible
- 40–59 → mixed
- 20–39 → low_credibility
- 0–19  → not_credible

---

## AI-Generated Content Penalty

- fakeness_score (0–100) is produced upstream by fakeness_agent.py using the GPTZero API
- It represents the likelihood that the article was AI-generated, not human-written
- 0 = likely human written, 100 = almost certainly AI-generated
- If fakeness_score > FAKENESS_THRESHOLD (60), apply a credibility penalty:
  - Multiply the final blended score by FAKENESS_PENALTY_MULTIPLIER (0.8)
- Apply this penalty after blending claims and publisher scores (step 8)
- Always clamp after penalty is applied (step 9)

---

## Edge Cases

- Zero claims found (e.g. opinion pieces, satire):
  - Output rating = mixed
  - Include a human-readable note in summary explaining no checkable
    claims were identified

- No sources with extracted text (max_possible_sum = 0):
  - Skip normalization entirely
  - Return 50 (neutral) as the claim score
  - This applies whenever all sources have extracted_text = None for any reason

- Paywalled sources (extracted_text = None):
  - Skip snippet extraction for that source
  - If ALL sources for a claim have extracted_text = None, mark that
    claim as unverified
  - Include a note in overall_reason explaining the paywall limitation

- Conflicted scores:
  - If the absolute gap between claim score and publisher score is
    CONFLICT_GAP_THRESHOLD (40) points or more, set is_conflicted=True
    in JudgementResult
  - Include a human-readable explanation of the conflict in
    ContentCredibility.summary
  - Do not add separate score fields — summary note is sufficient

- Government source only:
  - When government_source_only=True for a claim, apply GOVERNMENT_ONLY_BOOST
    (1.1x) at step 5a
  - Re-clamp per-claim score to 0–100 after boost
  - AND surface a UI warning flag in JudgementResult

---

## Configuration Decisions

All of the following values must live in app/config.py. Nothing in this
list should be hardcoded in judgement.py:

```python
# Verdict base weights — tied to ClaimVerdict enum
VERDICT_BASE_WEIGHTS = {
    "true":         +1.0,
    "mostly_true":  +0.5,
    "unverified":    0.0,  # neutral, no penalty
    "misleading":   -0.3,
    "mostly_false": -0.5,
    "false":        -1.0,
}

PRIMARY_SOURCE_MULTIPLIER   = 1.2
SECONDARY_SOURCE_MULTIPLIER = 0.8
HOP_DEPTH_DECAY             = 0.9
CLAIMS_BLEND_WEIGHT         = 0.7
PUBLISHER_BLEND_WEIGHT      = 0.3
FAKENESS_THRESHOLD          = 60
FAKENESS_PENALTY_MULTIPLIER = 0.8
GOVERNMENT_ONLY_BOOST       = 1.1
CONFLICT_GAP_THRESHOLD      = 40

# net=0 and net=1 both use 1.0x intentionally — net=1 is the minimum expected baseline
NET_CONFIDENCE_MULTIPLIERS = {
    -2: 0.6, -1: 0.8, 0: 1.0, 1: 1.0,
     2: 1.1,  3: 1.2, 4: 1.3, 5: 1.4,
}
NET_CONFIDENCE_MIN = -2   # floor — any net below -2 uses 0.6x
NET_CONFIDENCE_MAX = 5    # cap — any net above 5 uses 1.4x
```

---

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