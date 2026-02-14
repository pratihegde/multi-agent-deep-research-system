from __future__ import annotations

import os

# Fast research defaults (assignment-safe).
MAX_SUBQUESTIONS = 6
HARD_MAX_SUBQUESTIONS = 6
MAX_QUERIES_PER_SUBQUESTION = 2
HARD_MAX_QUERIES_PER_SUBQUESTION = 4
MAX_RESULTS_PER_QUERY = 3
MAX_ACCEPTED_SOURCES_TOTAL = 15
MAX_ACCEPTED_PER_SUBQUESTION = 4
MAX_DOMAIN_REPEAT = 2
MIN_UNIQUE_DOMAINS_PER_SUBQUESTION = 3
SOURCE_POLICY = "hybrid_trusted_first"
HISTORICAL_SOURCE_POLICY = "wikipedia_plus_top5"

ENABLE_REFINEMENT = True
MAX_REFINEMENT_LOOPS = 1
# Global search request budget across all providers for one workflow run.
# Backward compatible with older env name TAVILY_MAX_CALLS_PER_RUN.
SEARCH_MAX_CALLS_PER_RUN = int(
    os.getenv("SEARCH_MAX_CALLS_PER_RUN", os.getenv("TAVILY_MAX_CALLS_PER_RUN", "40"))
)
TAVILY_FAIL_FAST_ON_QUOTA = os.getenv("TAVILY_FAIL_FAST_ON_QUOTA", "true").lower() == "true"
USE_EXA_PRIMARY = os.getenv("USE_EXA_PRIMARY", "true").lower() == "true"
USE_FIRECRAWL_PRIMARY = os.getenv("USE_FIRECRAWL_PRIMARY", "true").lower() == "true"

QUALITY_MIN_TOTAL_SOURCES = 8
QUALITY_MIN_TRUSTED_RATIO = 0.60

TIER_A_DOMAINS = {
    "imf.org",
    "worldbank.org",
    "bis.org",
    "oecd.org",
    "federalreserve.gov",
    "ecb.europa.eu",
}

TIER_B_DOMAINS = {
    "reuters.com",
    "bloomberg.com",
    "ft.com",
    "wsj.com",
    "mckinsey.com",
    "weforum.org",
    "wikipedia.org",
}

TRUSTED_SUFFIXES = (".gov", ".edu")

# Used for trusted-domain seed retrieval in pass 1.
TRUSTED_DOMAIN_SEEDS = sorted(TIER_A_DOMAINS | TIER_B_DOMAINS)
HISTORICAL_DOMAIN_SEEDS = ["wikipedia.org"]
HISTORICAL_MAX_RESULTS_PER_QUERY = 5

# Retrieval scoring now prioritizes relevance + recency (not domain credibility).
ACCEPTANCE_SCORE_THRESHOLD = 0.44
HISTORICAL_ACCEPTANCE_SCORE_THRESHOLD = 0.38


def _normalize_domain(domain: str) -> str:
    host = domain.lower().strip()
    if host.startswith("www."):
        host = host[4:]
    return host


def domain_is_tier_a(domain: str) -> bool:
    host = _normalize_domain(domain)
    return host in TIER_A_DOMAINS or host.endswith(".gov") or host.endswith(".edu")


def domain_is_tier_b(domain: str) -> bool:
    host = _normalize_domain(domain)
    return host in TIER_B_DOMAINS


def domain_is_trusted(domain: str) -> bool:
    return domain_is_tier_a(domain) or domain_is_tier_b(domain)


def credibility_score_for_domain(domain: str) -> float:
    host = _normalize_domain(domain)
    if domain_is_tier_a(host):
        return 1.0
    if host == "wikipedia.org":
        return 0.72
    if domain_is_tier_b(host):
        return 0.78
    if any(host.endswith(suffix) for suffix in TRUSTED_SUFFIXES):
        return 0.9
    return 0.35


def simulated_failure_subquestions() -> set[str]:
    # Deterministic failure injector for Test Case 3:
    # SIMULATE_RESEARCH_FAILURE_SUBQS=sq2,sq4
    raw = os.getenv("SIMULATE_RESEARCH_FAILURE_SUBQS", "").strip()
    if not raw:
        return set()
    return {part.strip() for part in raw.split(",") if part.strip()}


def query_intent(query: str) -> str:
    text = query.lower()
    historical_terms = {
        "history",
        "historical",
        "origin",
        "community",
        "culture",
        "linguistic",
        "ethnographic",
        "biography",
        "who are",
        "background",
        "tradition",
    }
    business_terms = {
        "market",
        "expand",
        "investment",
        "competitor",
        "regulatory",
        "infrastructure",
        "strategy",
        "risk",
        "gdp",
        "inflation",
        "central bank",
    }

    hist_hits = sum(1 for term in historical_terms if term in text)
    biz_hits = sum(1 for term in business_terms if term in text)

    if hist_hits > biz_hits:
        return "historical"
    return "business"
