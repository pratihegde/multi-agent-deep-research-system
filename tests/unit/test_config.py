from app import config as c


def test_caps_respect_global_limit():
    assert c.MAX_ACCEPTED_PER_SUBQUESTION <= c.MAX_ACCEPTED_SOURCES_TOTAL


def test_search_budget_positive():
    assert c.SEARCH_MAX_CALLS_PER_RUN > 0


def test_min_unique_domains_nonzero():
    assert c.MIN_UNIQUE_DOMAINS_PER_SUBQUESTION >= 1
