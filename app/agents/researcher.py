from __future__ import annotations

import asyncio
import re
import textwrap
from collections import OrderedDict, defaultdict
from datetime import datetime, timezone
from typing import Awaitable, Callable

from app.config import (
    ACCEPTANCE_SCORE_THRESHOLD,
    HISTORICAL_ACCEPTANCE_SCORE_THRESHOLD,
    HISTORICAL_DOMAIN_SEEDS,
    HISTORICAL_MAX_RESULTS_PER_QUERY,
    MAX_ACCEPTED_PER_SUBQUESTION,
    MAX_ACCEPTED_SOURCES_TOTAL,
    MAX_DOMAIN_REPEAT,
    MAX_QUERIES_PER_SUBQUESTION,
    MAX_RESULTS_PER_QUERY,
    SOURCE_POLICY,
    TAVILY_FAIL_FAST_ON_QUOTA,
    TAVILY_MAX_CALLS_PER_RUN,
    TRUSTED_DOMAIN_SEEDS,
    credibility_score_for_domain,
    query_intent,
    simulated_failure_subquestions,
)
from app.models import Citation, ResearchNote, ResearchSynthesis, SourceFinding, SubQuestion
from app.services.llm import call_openai_typed
from app.tools.tavily_search import SearchToolError, normalize_url, tavily_search
from app.tools.wiki_search import WikiSearchError, wikipedia_search

EmitEvent = Callable[[str, dict], Awaitable[None]]

RESEARCH_SYSTEM = (
    "You are a research synthesis agent. Return ONLY JSON. "
    "Use the provided findings to produce concise evidence bullets, contradictions, and gaps."
)

WORD_RE = re.compile(r"\b[a-zA-Z0-9]{3,}\b")
YEAR_RE = re.compile(r"\b(20\d{2})\b")


class _BudgetManager:
    def __init__(self, existing_notes: dict[str, ResearchNote] | None = None) -> None:
        self._lock = asyncio.Lock()
        self.accepted_urls: set[str] = set()
        self.domain_counts: dict[str, int] = defaultdict(int)
        self.accepted_total = 0
        self.accepted_per_subquestion: dict[str, int] = defaultdict(int)

        if existing_notes:
            for sq_id, note in existing_notes.items():
                for finding in note.findings:
                    url_key = normalize_url(str(finding.url))
                    if url_key in self.accepted_urls:
                        continue
                    self.accepted_urls.add(url_key)
                    self.accepted_total += 1
                    self.accepted_per_subquestion[sq_id] += 1
                    self.domain_counts[finding.source_name] += 1

    def global_exhausted(self) -> bool:
        return self.accepted_total >= MAX_ACCEPTED_SOURCES_TOTAL

    def subquestion_cap_reached(self, sub_question_id: str) -> bool:
        return self.accepted_per_subquestion[sub_question_id] >= MAX_ACCEPTED_PER_SUBQUESTION

    async def try_accept(self, sub_question_id: str, finding: SourceFinding) -> tuple[bool, str]:
        url_key = normalize_url(str(finding.url))
        async with self._lock:
            if url_key in self.accepted_urls:
                return False, "deduped"
            if self.accepted_total >= MAX_ACCEPTED_SOURCES_TOTAL:
                return False, "global_cap"
            if self.accepted_per_subquestion[sub_question_id] >= MAX_ACCEPTED_PER_SUBQUESTION:
                return False, "subquestion_cap"
            if self.domain_counts[finding.source_name] >= MAX_DOMAIN_REPEAT:
                return False, "domain_cap"

            self.accepted_urls.add(url_key)
            self.accepted_total += 1
            self.accepted_per_subquestion[sub_question_id] += 1
            self.domain_counts[finding.source_name] += 1
            return True, "accepted"


class _RunControls:
    def __init__(self, max_calls: int) -> None:
        self._lock = asyncio.Lock()
        self.max_calls = max(1, max_calls)
        self.calls_made = 0
        self.quota_exhausted = False
        self.quota_notified = False
        self.call_cap_notified = False

    async def try_reserve_call(self) -> bool:
        async with self._lock:
            if self.quota_exhausted:
                return False
            if self.calls_made >= self.max_calls:
                return False
            self.calls_made += 1
            return True

    async def mark_quota_exhausted(self) -> bool:
        async with self._lock:
            self.quota_exhausted = True
            if not self.quota_notified:
                self.quota_notified = True
                return True
            return False

    async def is_quota_exhausted(self) -> bool:
        async with self._lock:
            return self.quota_exhausted

    async def mark_call_cap_reached(self) -> bool:
        async with self._lock:
            if not self.call_cap_notified:
                self.call_cap_notified = True
                return True
            return False


def _tokenize(text: str) -> set[str]:
    return {token.lower() for token in WORD_RE.findall(text)}


def _relevance_score(sub_question: SubQuestion, query: str, finding: SourceFinding) -> float:
    target_tokens = _tokenize(f"{sub_question.question} {query}")
    if not target_tokens:
        return 0.5
    content_tokens = _tokenize(f"{finding.title} {finding.snippet}")
    if not content_tokens:
        return 0.0
    overlap = len(target_tokens & content_tokens)
    return min(overlap / max(len(target_tokens), 1), 1.0)


def _recency_proxy(finding: SourceFinding) -> float:
    current_year = datetime.now(timezone.utc).year
    matches = [int(year) for year in YEAR_RE.findall(f"{finding.title} {finding.snippet}")]
    if not matches:
        return 0.5
    min_delta = min(abs(current_year - year) for year in matches)
    if min_delta <= 1:
        return 1.0
    if min_delta <= 3:
        return 0.75
    return 0.45


def _acceptance_score(sub_question: SubQuestion, query: str, finding: SourceFinding) -> float:
    credibility = credibility_score_for_domain(finding.source_name)
    relevance = _relevance_score(sub_question, query, finding)
    recency = _recency_proxy(finding)
    return (0.55 * credibility) + (0.35 * relevance) + (0.10 * recency)


def _fallback_synthesis(findings: list[SourceFinding]) -> ResearchSynthesis:
    bullets = [f.title for f in findings[:8]]
    if len(bullets) < 4:
        bullets += ["Insufficient evidence volume for this sub-question."] * (4 - len(bullets))
    return ResearchSynthesis(
        evidence_bullets=bullets[:8],
        contradictions=[],
        gaps=["Need additional high-quality sources for stronger confidence."],
    )


async def _synthesize_research(sub_question: SubQuestion, findings: list[SourceFinding]) -> ResearchSynthesis:
    raw_findings = "\n".join(
        f"- title: {f.title}\n  url: {f.url}\n  snippet: {f.snippet[:320]}" for f in findings[:12]
    )
    prompt = textwrap.dedent(
        f"""
        Sub-question:
        {sub_question.question}

        Findings:
        {raw_findings or "- none"}

        Return keys:
        - evidence_bullets (4-8)
        - contradictions (0-4)
        - gaps (1-5)
        """
    ).strip()
    try:
        synthesis = await call_openai_typed(
            system_prompt=RESEARCH_SYSTEM,
            user_prompt=prompt,
            schema=ResearchSynthesis,
        )
        if len(synthesis.evidence_bullets) < 4:
            return _fallback_synthesis(findings)
        return ResearchSynthesis(
            evidence_bullets=synthesis.evidence_bullets[:8],
            contradictions=synthesis.contradictions[:4],
            gaps=synthesis.gaps[:5],
        )
    except Exception:
        return _fallback_synthesis(findings)


async def _collect_candidates(
    *,
    sq: SubQuestion,
    query: str,
    phase: str,
    intent: str,
) -> list[SourceFinding]:
    if phase == "wikipedia":
        return await tavily_search(
            query=query,
            max_results=min(HISTORICAL_MAX_RESULTS_PER_QUERY, 3),
            include_domains=HISTORICAL_DOMAIN_SEEDS,
        )

    if intent == "historical":
        return await tavily_search(query=query, max_results=HISTORICAL_MAX_RESULTS_PER_QUERY)

    if SOURCE_POLICY == "hybrid_trusted_first" and phase == "trusted":
        return await tavily_search(
            query=query,
            max_results=MAX_RESULTS_PER_QUERY,
            include_domains=TRUSTED_DOMAIN_SEEDS,
        )
    return await tavily_search(query=query, max_results=MAX_RESULTS_PER_QUERY)


async def _collect_fallback_candidates(query: str, intent: str) -> list[SourceFinding]:
    if intent == "historical":
        return await wikipedia_search(query=query, max_results=6)
    return await wikipedia_search(query=query, max_results=4)


async def research_sub_question(
    sq: SubQuestion,
    emit_event: EmitEvent,
    budget: _BudgetManager,
    controls: _RunControls,
    *,
    simulated_failures: set[str],
    intent: str,
) -> tuple[ResearchNote, list[Citation], list[dict]]:
    errors: list[dict] = []
    collected: OrderedDict[str, SourceFinding] = OrderedDict()

    await emit_event(
        "research_progress",
        {
            "sub_question_id": sq.id,
            "status": "started",
            "message": sq.question,
            "evidence_count": 0,
        },
    )

    if sq.id in simulated_failures:
        detail = f"Simulated Tavily failure for {sq.id}."
        errors.append({"stage": "research", "sub_question_id": sq.id, "detail": detail})
        await emit_event("error", {"stage": "research", "sub_question_id": sq.id, "detail": detail})

    if intent == "historical":
        phases = ["wikipedia", "broad"]
    else:
        phases = ["wikipedia", "trusted", "broad"] if SOURCE_POLICY == "hybrid_trusted_first" else ["wikipedia", "broad"]

    for query in sq.search_queries[:MAX_QUERIES_PER_SUBQUESTION]:
        if budget.global_exhausted() or budget.subquestion_cap_reached(sq.id):
            break

        for phase in phases:
            if budget.global_exhausted() or budget.subquestion_cap_reached(sq.id):
                break

            if sq.id in simulated_failures:
                continue

            use_fallback = await controls.is_quota_exhausted()
            findings: list[SourceFinding] = []
            if not use_fallback:
                if not await controls.try_reserve_call():
                    should_emit_cap = await controls.mark_call_cap_reached()
                    if should_emit_cap:
                        await emit_event(
                            "error",
                            {
                                "stage": "research",
                                "sub_question_id": sq.id,
                                "detail": (
                                    f"Tavily call cap reached ({controls.calls_made}/{controls.max_calls}); "
                                    "switching to Wikipedia fallback for this run."
                                ),
                            },
                        )
                    use_fallback = True

            if not use_fallback:
                try:
                    findings = await _collect_candidates(sq=sq, query=query, phase=phase, intent=intent)
                except SearchToolError as exc:
                    detail = str(exc)
                    quota_hit = "exceeds your plan's set usage limit" in detail.lower()
                    if quota_hit and TAVILY_FAIL_FAST_ON_QUOTA:
                        should_emit_quota = await controls.mark_quota_exhausted()
                        quota_msg = (
                            "Tavily quota exceeded; switching to Wikipedia fallback for this run."
                        )
                        if should_emit_quota:
                            await emit_event(
                                "error",
                                {
                                    "stage": "research",
                                    "sub_question_id": sq.id,
                                    "detail": quota_msg,
                                },
                            )
                            errors.append(
                                {"stage": "research", "sub_question_id": sq.id, "detail": quota_msg}
                            )
                        use_fallback = True
                    else:
                        errors.append({"stage": "research", "sub_question_id": sq.id, "detail": detail})
                        await emit_event(
                            "error",
                            {
                                "stage": "research",
                                "sub_question_id": sq.id,
                                "detail": detail,
                            },
                        )
                        continue

            if use_fallback:
                try:
                    findings = await _collect_fallback_candidates(query=query, intent=intent)
                except WikiSearchError as exc:
                    errors.append({"stage": "research", "sub_question_id": sq.id, "detail": str(exc)})
                    await emit_event(
                        "error",
                        {
                            "stage": "research",
                            "sub_question_id": sq.id,
                            "detail": str(exc),
                        },
                    )
                    continue

            scored = sorted(
                [(_acceptance_score(sq, query, finding), finding) for finding in findings],
                key=lambda item: item[0],
                reverse=True,
            )

            accepted_in_phase = 0
            for score, finding in scored:
                threshold = (
                    HISTORICAL_ACCEPTANCE_SCORE_THRESHOLD
                    if intent == "historical"
                    else ACCEPTANCE_SCORE_THRESHOLD
                )
                if intent == "historical" and finding.source_name == "wikipedia.org":
                    threshold = min(threshold, 0.35)
                if score < threshold:
                    continue

                accepted, reason = await budget.try_accept(sq.id, finding)
                if not accepted:
                    if reason == "deduped":
                        await emit_event(
                            "source_fetch",
                            {
                                "sub_question_id": sq.id,
                                "source_name": finding.source_name,
                                "title": finding.title,
                                "url": str(finding.url),
                                "status": "deduped",
                            },
                        )
                    continue

                url_key = normalize_url(str(finding.url))
                collected[url_key] = finding
                accepted_in_phase += 1
                await emit_event(
                    "source_fetch",
                    {
                        "sub_question_id": sq.id,
                        "source_name": finding.source_name,
                        "title": finding.title,
                        "url": str(finding.url),
                        "status": "fetched",
                    },
                )

                if budget.global_exhausted() or budget.subquestion_cap_reached(sq.id):
                    break

            # If wikipedia already gave one high-relevance source, continue to verification phases.
            if phase == "wikipedia":
                continue
            # In trusted/broad phases, stop once we have enough accepted findings.
            if phase in {"trusted", "broad"} and len(collected) >= 3:
                break
            if phase == "broad" and accepted_in_phase > 0 and len(collected) >= 3:
                break

    if not collected:
        try:
            emergency = await _collect_fallback_candidates(
                query=f"{sq.question} {query}",
                intent=intent,
            )
            for finding in emergency[:3]:
                accepted, _ = await budget.try_accept(sq.id, finding)
                if not accepted:
                    continue
                url_key = normalize_url(str(finding.url))
                collected[url_key] = finding
                await emit_event(
                    "source_fetch",
                    {
                        "sub_question_id": sq.id,
                        "source_name": finding.source_name,
                        "title": finding.title,
                        "url": str(finding.url),
                        "status": "fetched",
                    },
                )
        except WikiSearchError as exc:
            errors.append({"stage": "research", "sub_question_id": sq.id, "detail": str(exc)})
            await emit_event(
                "error",
                {
                    "stage": "research",
                    "sub_question_id": sq.id,
                    "detail": str(exc),
                },
            )

    findings_list = list(collected.values())
    synthesis = await _synthesize_research(sq, findings_list)
    note = ResearchNote(
        sub_question_id=sq.id,
        evidence_bullets=synthesis.evidence_bullets,
        findings=findings_list,
        contradictions=synthesis.contradictions,
        gaps=synthesis.gaps,
    )
    citations = [Citation(title=f.title, url=f.url, source_name=f.source_name) for f in findings_list]

    await emit_event(
        "research_progress",
        {
            "sub_question_id": sq.id,
            "status": "completed",
            "message": f"Completed {sq.id}",
            "evidence_count": len(note.evidence_bullets),
        },
    )
    return note, citations, errors


def _merge_note(old: ResearchNote, new: ResearchNote) -> ResearchNote:
    merged_findings: OrderedDict[str, SourceFinding] = OrderedDict()
    for item in old.findings + new.findings:
        merged_findings[normalize_url(str(item.url))] = item
    evidence = (old.evidence_bullets + new.evidence_bullets)[:8]
    contradictions = list(dict.fromkeys(old.contradictions + new.contradictions))[:6]
    gaps = list(dict.fromkeys(old.gaps + new.gaps))[:6]
    return ResearchNote(
        sub_question_id=old.sub_question_id,
        evidence_bullets=evidence,
        findings=list(merged_findings.values()),
        contradictions=contradictions,
        gaps=gaps,
    )


async def run_research_batch(
    sub_questions: list[SubQuestion],
    emit_event: EmitEvent,
    *,
    query: str,
    max_concurrency: int = 4,
    existing_notes: dict[str, ResearchNote] | None = None,
) -> tuple[dict[str, ResearchNote], list[Citation], list[dict]]:
    sem = asyncio.Semaphore(max_concurrency)
    budget = _BudgetManager(existing_notes=existing_notes)
    controls = _RunControls(max_calls=TAVILY_MAX_CALLS_PER_RUN)
    simulated_failures = simulated_failure_subquestions()
    intent = query_intent(query)

    async def _worker(sq: SubQuestion):
        async with sem:
            return await research_sub_question(
                sq=sq,
                emit_event=emit_event,
                budget=budget,
                controls=controls,
                simulated_failures=simulated_failures,
                intent=intent,
            )

    tasks = [_worker(sq) for sq in sorted(sub_questions, key=lambda s: s.priority)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    merged_notes = dict(existing_notes or {})
    citations_map: OrderedDict[str, Citation] = OrderedDict()
    errors: list[dict] = []

    for result in results:
        if isinstance(result, Exception):
            errors.append({"stage": "research", "detail": str(result)})
            await emit_event("error", {"stage": "research", "detail": str(result)})
            continue

        note, citations, note_errors = result
        if note.sub_question_id in merged_notes:
            merged_notes[note.sub_question_id] = _merge_note(merged_notes[note.sub_question_id], note)
        else:
            merged_notes[note.sub_question_id] = note

        for citation in citations:
            citations_map[normalize_url(str(citation.url))] = citation
        errors.extend(note_errors)

    return merged_notes, list(citations_map.values()), errors
