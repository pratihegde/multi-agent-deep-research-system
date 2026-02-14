from __future__ import annotations

import asyncio
import logging
import os
from collections import OrderedDict

from app.models import SourceFinding
from app.tools.exa_search import exa_search
from app.tools.firecrawl_search import firecrawl_search
from app.tools.tavily_search import SearchToolError, normalize_url, tavily_search

logger = logging.getLogger("app.search")


def _enabled(value: str | None, default: bool = True) -> bool:
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _source_name(url: str) -> str:
    host = normalize_url(str(url)).split("/")[2]
    return host or "unknown"


async def search_web_parallel(
    *,
    query: str,
    max_results: int = 5,
    include_domains: list[str] | None = None,
) -> list[SourceFinding]:
    max_per_domain = max(1, int(os.getenv("SEARCH_MAX_CANDIDATES_PER_DOMAIN", "2")))
    use_exa = _enabled(os.getenv("USE_EXA_PRIMARY"), default=True) and bool(os.getenv("EXA_API_KEY"))
    use_firecrawl = _enabled(os.getenv("USE_FIRECRAWL_PRIMARY"), default=True) and bool(
        os.getenv("FIRECRAWL_API_KEY")
    )
    logger.info(
        "search.start query=%r max_results=%s include_domains=%s exa=%s firecrawl=%s",
        query[:120],
        max_results,
        bool(include_domains),
        use_exa,
        use_firecrawl,
    )

    tasks: list[asyncio.Future] = []
    labels: list[str] = []

    if use_exa:
        labels.append("exa")
        tasks.append(
            asyncio.create_task(
                exa_search(query=query, max_results=max_results, include_domains=include_domains)
            )
        )
    if use_firecrawl:
        labels.append("firecrawl")
        tasks.append(
            asyncio.create_task(
                firecrawl_search(query=query, max_results=max_results, include_domains=include_domains)
            )
        )

    findings_by_url: OrderedDict[str, SourceFinding] = OrderedDict()
    provider_findings: dict[str, list[SourceFinding]] = {}
    errors: list[str] = []

    if tasks:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for idx, result in enumerate(results):
            if isinstance(result, Exception):
                errors.append(f"{labels[idx]}: {result}")
                logger.warning("search.provider_error provider=%s error=%s", labels[idx], result)
                continue
            logger.info("search.provider_ok provider=%s results=%s", labels[idx], len(result))
            provider_findings[labels[idx]] = list(result)

    # Interleave provider results to reduce one-provider/domain dominance.
    interleaved: list[SourceFinding] = []
    if provider_findings:
        max_len = max(len(items) for items in provider_findings.values())
        for i in range(max_len):
            for label in labels:
                items = provider_findings.get(label, [])
                if i < len(items):
                    interleaved.append(items[i])

    domain_counts: dict[str, int] = {}
    for finding in interleaved:
        normalized = normalize_url(str(finding.url))
        if normalized in findings_by_url:
            continue
        domain = finding.source_name or _source_name(str(finding.url))
        count = domain_counts.get(domain, 0)
        if count >= max_per_domain:
            continue
        findings_by_url[normalized] = finding
        domain_counts[domain] = count + 1

    if findings_by_url:
        logger.info("search.primary_complete deduped_results=%s", len(findings_by_url))
        return list(findings_by_url.values())[: max(3, max_results * 2)]

    # Tavily is explicit fallback only.
    try:
        logger.info("search.fallback provider=tavily reason=no_primary_results")
        return await tavily_search(query=query, max_results=max_results, include_domains=include_domains)
    except SearchToolError as exc:
        errors.append(f"tavily: {exc}")
        logger.warning("search.provider_error provider=tavily error=%s", exc)

    if errors:
        logger.error("search.failed query=%r errors=%s", query[:120], errors)
        raise SearchToolError("All providers failed: " + " | ".join(errors))
    logger.error("search.failed query=%r reason=no_providers_configured", query[:120])
    raise SearchToolError("No search providers are configured.")
