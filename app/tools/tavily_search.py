from __future__ import annotations

import os
from urllib.parse import urlparse, urlunparse

import httpx

from app.models import SourceFinding


class SearchToolError(RuntimeError):
    pass


def _sanitize_query(query: str) -> str:
    return " ".join(query.split()).strip()[:450]


def _sanitize_domains(domains: list[str] | None) -> list[str]:
    if not domains:
        return []
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in domains:
        host = raw.lower().strip()
        if not host:
            continue
        if host.startswith("http://") or host.startswith("https://"):
            host = urlparse(host).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        # include_domains expects real hostnames, not wildcard suffixes.
        if "." not in host:
            continue
        if host in seen:
            continue
        seen.add(host)
        cleaned.append(host)
    return cleaned[:20]


def extract_source_name(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host or "unknown"


def normalize_url(url: str) -> str:
    parsed = urlparse(url.strip())
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    normalized = parsed._replace(
        scheme="https",
        netloc=netloc,
        params="",
        query="",
        fragment="",
    )
    return urlunparse(normalized).rstrip("/")


async def tavily_search(
    query: str,
    max_results: int = 5,
    include_domains: list[str] | None = None,
) -> list[SourceFinding]:
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        # Stub mode for local development without key.
        return [
            SourceFinding(
                title=f"Stub source for {query[:40]}",
                url="https://example.com/stub-result",
                snippet="Stub result because TAVILY_API_KEY is not set.",
                source_name="example.com",
            )
        ]

    sanitized_query = _sanitize_query(query)
    sanitized_domains = _sanitize_domains(include_domains)

    base_payload: dict[str, object] = {
        "query": sanitized_query,
        "search_depth": "advanced",
        "max_results": max_results,
        "include_answer": False,
        "include_images": False,
        "include_raw_content": False,
    }
    if sanitized_domains:
        base_payload["include_domains"] = sanitized_domains
    timeout = httpx.Timeout(25.0, connect=10.0)
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    # Tavily behavior can vary by account/config. Try a small retry ladder:
    # 1) advanced + include_domains
    # 2) advanced without include_domains
    # 3) basic without include_domains
    attempts: list[dict[str, object]] = []
    first = dict(base_payload)
    attempts.append(first)

    if "include_domains" in base_payload:
        no_domains = dict(base_payload)
        no_domains.pop("include_domains", None)
        attempts.append(no_domains)

    basic = dict(base_payload)
    basic.pop("include_domains", None)
    basic["search_depth"] = "basic"
    attempts.append(basic)

    errors: list[str] = []
    data: dict = {}
    async with httpx.AsyncClient(timeout=timeout) as client:
        for idx, payload in enumerate(attempts, start=1):
            request_payload = dict(payload)
            # Backward-compatible body auth for older API behavior.
            request_payload["api_key"] = api_key
            try:
                response = await client.post(
                    "https://api.tavily.com/search",
                    json=request_payload,
                    headers=headers,
                )
                if response.status_code >= 400:
                    body = response.text.strip()
                    if len(body) > 240:
                        body = body[:240] + "..."
                    errors.append(
                        f"attempt={idx} status={response.status_code} depth={payload.get('search_depth')} "
                        f"domains={'include_domains' in payload} body={body or '(empty)'}"
                    )
                    continue
                data = response.json()
                break
            except httpx.HTTPError as exc:
                errors.append(f"attempt={idx} exception={exc}")
                continue

    if not data:
        raise SearchToolError("Tavily request failed after retries: " + " | ".join(errors))

    results = data.get("results", [])
    findings: list[SourceFinding] = []
    for item in results:
        url = item.get("url")
        title = item.get("title")
        snippet = item.get("content") or item.get("snippet") or ""
        if not snippet:
            snippet = f"Summary unavailable for {title}"
        if not url or not title:
            continue
        findings.append(
            SourceFinding(
                title=title[:300],
                url=url,
                snippet=snippet[:1200],
                source_name=extract_source_name(url),
            )
        )
    return findings
