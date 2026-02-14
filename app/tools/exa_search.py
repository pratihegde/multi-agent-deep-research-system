from __future__ import annotations

import os

import httpx

from app.models import SourceFinding
from app.tools.tavily_search import extract_source_name


class ExaSearchError(RuntimeError):
    pass


def _clean_query(query: str) -> str:
    return " ".join(query.split()).strip()[:450]


async def exa_search(
    query: str,
    max_results: int = 5,
    include_domains: list[str] | None = None,
) -> list[SourceFinding]:
    api_key = os.getenv("EXA_API_KEY")
    if not api_key:
        raise ExaSearchError("EXA_API_KEY is not set.")

    payload: dict[str, object] = {
        "query": _clean_query(query),
        "type": "auto",
        "numResults": max(1, min(max_results, 10)),
        "contents": {"text": True},
    }
    if include_domains:
        payload["includeDomains"] = include_domains[:10]

    timeout = httpx.Timeout(20.0, connect=8.0)
    headers = {
        "x-api-key": api_key,
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post("https://api.exa.ai/search", json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        raise ExaSearchError(f"Exa request failed: {exc}") from exc

    findings: list[SourceFinding] = []
    for item in data.get("results", [])[: max_results * 2]:
        url = item.get("url")
        title = item.get("title")
        snippet = item.get("text") or item.get("summary") or ""
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
