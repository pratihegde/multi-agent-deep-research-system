from __future__ import annotations

import textwrap

from app.config import QUALITY_MIN_TOTAL_SOURCES, QUALITY_MIN_TRUSTED_RATIO, domain_is_trusted, query_intent
from app.models import Plan, QualityCheck, ResearchNote
from app.services.llm import call_openai_typed
from app.tools.tavily_search import normalize_url

QUALITY_SYSTEM = (
    "You are a report quality gate. Return ONLY JSON. "
    "Score coverage, evidence quality, contradiction handling, and practical usefulness."
)


def _source_quality_stats(research_notes: dict[str, ResearchNote]) -> tuple[int, int]:
    unique_urls: set[str] = set()
    trusted_count = 0

    for note in research_notes.values():
        for finding in note.findings:
            url_key = normalize_url(str(finding.url))
            if url_key in unique_urls:
                continue
            unique_urls.add(url_key)
            if domain_is_trusted(finding.source_name):
                trusted_count += 1

    return len(unique_urls), trusted_count


def _fallback_quality(plan: Plan, research_notes: dict[str, ResearchNote], intent: str) -> QualityCheck:
    covered = len([sq for sq in plan.sub_questions if sq.id in research_notes])
    total = len(plan.sub_questions)
    total_sources, trusted_sources = _source_quality_stats(research_notes)
    trusted_ratio = (trusted_sources / total_sources) if total_sources else 0.0
    required_trusted_ratio = 0.25 if intent == "historical" else QUALITY_MIN_TRUSTED_RATIO

    issues: list[str] = []
    if covered < total:
        issues.append("Not all sub-questions are covered.")
    if total_sources < QUALITY_MIN_TOTAL_SOURCES:
        issues.append(f"Only {total_sources} accepted sources; expected at least {QUALITY_MIN_TOTAL_SOURCES}.")
    if trusted_ratio < required_trusted_ratio:
        issues.append(
            f"Trusted source ratio is {trusted_ratio:.0%}; expected at least {required_trusted_ratio:.0%}."
        )

    hard_pass = (covered == total) and (total_sources >= QUALITY_MIN_TOTAL_SOURCES) and (
        trusted_ratio >= required_trusted_ratio
    )
    score = min(100, int((covered / max(total, 1)) * 40 + min(total_sources, 12) * 4 + trusted_ratio * 35))

    if hard_pass:
        return QualityCheck(passed=True, score=max(score, 75), issues=[])
    return QualityCheck(
        passed=False,
        score=max(35, min(score, 74)),
        issues=issues,
        refinement_queries=_default_refinement_queries(intent),
    )


def _default_refinement_queries(intent: str) -> list[str]:
    if intent == "historical":
        return [
            "wikipedia historical overview",
            "encyclopedia cultural history",
            "scholar historical background",
        ]
    return [
        "latest official statistics site:worldbank.org",
        "policy analysis site:oecd.org",
        "central bank publication site:federalreserve.gov",
    ]


async def run_quality_check(
    query: str,
    plan: Plan,
    research_notes: dict[str, ResearchNote],
) -> QualityCheck:
    intent = query_intent(query)
    required_trusted_ratio = 0.25 if intent == "historical" else QUALITY_MIN_TRUSTED_RATIO

    coverage_lines: list[str] = []
    covered = 0
    for sq in plan.sub_questions:
        note = research_notes.get(sq.id)
        if not note:
            coverage_lines.append(f"- {sq.id}: missing")
            continue
        covered += 1
        coverage_lines.append(
            f"- {sq.id}: bullets={len(note.evidence_bullets)}, findings={len(note.findings)}, gaps={len(note.gaps)}"
        )

    total_sources, trusted_sources = _source_quality_stats(research_notes)
    trusted_ratio = (trusted_sources / total_sources) if total_sources else 0.0

    prompt = textwrap.dedent(
        f"""
        Original query:
        {query}

        Coverage summary:
        {chr(10).join(coverage_lines)}

        Source quality summary:
        - total_unique_sources: {total_sources}
        - trusted_sources: {trusted_sources}
        - trusted_ratio: {trusted_ratio:.2f}

        Return:
        - passed (bool)
        - score (0-100)
        - issues (list)
        - refinement_queries (list up to 5)

        Strictness:
        - pass only if all sub-questions have meaningful evidence
        - pass only if total sources >= {QUALITY_MIN_TOTAL_SOURCES}
        - pass only if trusted ratio >= {required_trusted_ratio:.2f}
        """
    ).strip()

    hard_pass = (covered == len(plan.sub_questions)) and (total_sources >= QUALITY_MIN_TOTAL_SOURCES) and (
        trusted_ratio >= required_trusted_ratio
    )

    hard_issues: list[str] = []
    if covered < len(plan.sub_questions):
        hard_issues.append("Coverage gap: one or more sub-questions are missing sufficient findings.")
    if total_sources < QUALITY_MIN_TOTAL_SOURCES:
        hard_issues.append(
            f"Evidence volume below threshold: {total_sources} < {QUALITY_MIN_TOTAL_SOURCES}."
        )
    if trusted_ratio < required_trusted_ratio:
        hard_issues.append(
            f"Trusted source ratio below threshold: {trusted_ratio:.0%} < {required_trusted_ratio:.0%}."
        )

    try:
        llm_quality = await call_openai_typed(
            system_prompt=QUALITY_SYSTEM,
            user_prompt=prompt,
            schema=QualityCheck,
        )
    except Exception:
        return _fallback_quality(plan, research_notes, intent)

    score = int((0.6 * llm_quality.score) + (0.4 * (90 if hard_pass else 55)))
    issues = list(dict.fromkeys(llm_quality.issues + hard_issues))
    passed = hard_pass and llm_quality.passed

    refinement_queries = llm_quality.refinement_queries
    if not passed and not refinement_queries:
        refinement_queries = _default_refinement_queries(intent)

    return QualityCheck(
        passed=passed,
        score=max(0, min(100, score)),
        issues=issues,
        refinement_queries=refinement_queries[:5],
    )
