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
