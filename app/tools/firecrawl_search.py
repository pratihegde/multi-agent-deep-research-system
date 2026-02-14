from __future__ import annotations

import os

import httpx

from app.models import SourceFinding
from app.tools.tavily_search import extract_source_name


class FirecrawlSearchError(RuntimeError):
    pass


def _clean_query(query: str) -> str:
    return " ".join(query.split()).strip()[:450]


def _domain_hint_query(query: str, include_domains: list[str] | None) -> str:
    if not include_domains:
        return query
    # Firecrawl search does not expose a documented includeDomains filter in all plans.
    # Use lightweight query hinting for domain preference.
    hints = " OR ".join(f"site:{host}" for host in include_domains[:3] if host)
    if not hints:
        return query
    return f"{query} ({hints})"


async def firecrawl_search(
    query: str,
    max_results: int = 5,
    include_domains: list[str] | None = None,
) -> list[SourceFinding]:
    api_key = os.getenv("FIRECRAWL_API_KEY")
    if not api_key:
        raise FirecrawlSearchError("FIRECRAWL_API_KEY is not set.")

    payload: dict[str, object] = {
        "query": _domain_hint_query(_clean_query(query), include_domains),
        "limit": max(1, min(max_results, 10)),
    }

    timeout = httpx.Timeout(20.0, connect=8.0)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                "https://api.firecrawl.dev/v1/search",
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        raise FirecrawlSearchError(f"Firecrawl request failed: {exc}") from exc

    findings: list[SourceFinding] = []
    for item in data.get("data", [])[: max_results * 2]:
        url = item.get("url")
        title = item.get("title") or item.get("metadata", {}).get("title")
        snippet = item.get("description") or item.get("content") or ""
        if not url or not title:
            continue
        findings.append(
            SourceFinding(
                title=str(title)[:300],
                url=str(url),
                snippet=str(snippet or f"Summary unavailable for {title}")[:1200],
                source_name=extract_source_name(str(url)),
            )
        )
    return findings
