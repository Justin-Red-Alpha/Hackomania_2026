"""
Application configuration.
Non-secret tuning values live here. Secrets (API keys, credentials) stay in .env.

Usage:
    from app.config import TAVILY_SEARCH_DEPTH, TAVILY_MAX_RESULTS, COUNTRY
"""

# ---------------------------------------------------------------------------
# Tavily search settings
# ---------------------------------------------------------------------------

TAVILY_SEARCH_DEPTH: str = "advanced"
TAVILY_MAX_RESULTS: int = 5

# ---------------------------------------------------------------------------
# Investigation settings
# ---------------------------------------------------------------------------

PRIORITISE_LOCAL: bool = False
COUNTRY: str = "Singapore"
MIN_SOURCES_PER_CLAIM: int = 2
MAX_HOP_DEPTH: int = 2
MAX_SEARCH_RETRIES: int = 2


# ---------------------------------------------------------------------------
# Judgement scoring weights
# ---------------------------------------------------------------------------

# Verdict base weights — tied to ClaimVerdict enum values
VERDICT_BASE_WEIGHTS: dict = {
    "true":         +1.0,
    "mostly_true":  +0.5,
    "inconclusive": +1.0,  # conflicting evidence — let judgement re-evaluate; must be non-zero
    "unverified":   +1.0,  # no investigator opinion — let evidence decide; must be non-zero
    "misleading":   -0.3,
    "mostly_false": -0.5,
    "false":        -1.0,
}

PRIMARY_SOURCE_MULTIPLIER:   float = 1.2
SECONDARY_SOURCE_MULTIPLIER: float = 0.8
HOP_DEPTH_DECAY:             float = 0.9
CLAIMS_BLEND_WEIGHT:         float = 0.7
PUBLISHER_BLEND_WEIGHT:      float = 0.3
FAKENESS_THRESHOLD:          int   = 60
FAKENESS_PENALTY_MULTIPLIER: float = 0.8
GOVERNMENT_ONLY_BOOST:       float = 1.1
CONFLICT_GAP_THRESHOLD:      int   = 40

# net=0 and net=1 both use 1.0x intentionally — net=1 is the minimum expected baseline
NET_CONFIDENCE_MULTIPLIERS: dict = {
    -2: 0.6, -1: 0.8, 0: 1.0, 1: 1.0,
     2: 1.1,  3: 1.2, 4: 1.3, 5: 1.4,
}
NET_CONFIDENCE_MIN: int = -2   # floor — any net below -2 uses 0.6x
NET_CONFIDENCE_MAX: int = 5    # cap  — any net above 5 uses 1.4x
