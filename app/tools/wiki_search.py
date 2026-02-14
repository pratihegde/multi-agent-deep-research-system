from __future__ import annotations

import os
import re
from urllib.parse import quote

import httpx

from app.models import SourceFinding


class WikiSearchError(RuntimeError):
    pass


HTML_TAG_RE = re.compile(r"<[^>]+>")


def _wiki_headers() -> dict[str, str]:
    # Wikimedia commonly rejects generic/no UA traffic. Use a stable, explicit UA.
    return {
        "User-Agent": os.getenv(
            "WIKIPEDIA_USER_AGENT",
            "AstraDeepResearchStudio/1.0 (research-assistant; contact: local-dev)",
        ),
        "Accept": "application/json",
    }


def _clean_snippet(value: str) -> str:
    return " ".join(HTML_TAG_RE.sub("", value).split())


async def _search_mediawiki(query: str, max_results: int, client: httpx.AsyncClient) -> list[SourceFinding]:
    params = {
        "action": "query",
        "list": "search",
        "srsearch": query,
        "format": "json",
        "srlimit": max(1, min(max_results, 8)),
    }
    response = await client.get(
        "https://en.wikipedia.org/w/api.php",
        params=params,
        headers=_wiki_headers(),
    )
    response.raise_for_status()
    payload = response.json()

    hits = payload.get("query", {}).get("search", [])
    findings: list[SourceFinding] = []
    for hit in hits[:max_results]:
        title = str(hit.get("title", "")).strip()
        if not title:
            continue
        snippet = _clean_snippet(str(hit.get("snippet", "")))
        url_title = quote(title.replace(" ", "_"), safe=":_()")
        findings.append(
            SourceFinding(
                title=title[:300],
                url=f"https://en.wikipedia.org/wiki/{url_title}",
                snippet=(snippet or f"Wikipedia entry for {title}")[:1200],
                source_name="wikipedia.org",
            )
        )
    return findings


async def _search_opensearch(query: str, max_results: int, client: httpx.AsyncClient) -> list[SourceFinding]:
    params = {
        "action": "opensearch",
        "search": query,
        "limit": max(1, min(max_results, 8)),
        "namespace": 0,
        "format": "json",
    }
    response = await client.get(
        "https://en.wikipedia.org/w/api.php",
        params=params,
        headers=_wiki_headers(),
    )
    response.raise_for_status()
    payload = response.json()
    # opensearch schema: [query, titles[], descriptions[], urls[]]
    if not isinstance(payload, list) or len(payload) < 4:
        return []
    titles = payload[1] if isinstance(payload[1], list) else []
    descriptions = payload[2] if isinstance(payload[2], list) else []
    urls = payload[3] if isinstance(payload[3], list) else []

    findings: list[SourceFinding] = []
    for idx, title in enumerate(titles[:max_results]):
        safe_title = str(title).strip()
        url = str(urls[idx]).strip() if idx < len(urls) else ""
        description = str(descriptions[idx]).strip() if idx < len(descriptions) else ""
        if not safe_title or not url:
            continue
        findings.append(
            SourceFinding(
                title=safe_title[:300],
                url=url,
                snippet=(description or f"Wikipedia entry for {safe_title}")[:1200],
                source_name="wikipedia.org",
            )
        )
    return findings


async def wikipedia_search(query: str, max_results: int = 5) -> list[SourceFinding]:
    timeout = httpx.Timeout(12.0, connect=5.0)
    safe_query = " ".join(query.split()).strip()[:350]
    errors: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            # Primary mode: rich snippet search
            findings = await _search_mediawiki(safe_query, max_results, client)
            if findings:
                return findings
            # Secondary mode: opensearch fallback (often more permissive)
            findings = await _search_opensearch(safe_query, max_results, client)
            if findings:
                return findings
            errors.append("empty_results")
    except Exception as exc:
        errors.append(str(exc))

    raise WikiSearchError("Wikipedia search failed: " + " | ".join(errors))
