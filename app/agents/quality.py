from __future__ import annotations

import re
import textwrap

from pydantic import BaseModel, Field

from app.models import Citation, QualityCheck
from app.services.llm import call_openai_typed

QUALITY_SYSTEM = (
    "You are a strict quality checker for research reports. "
    "Fail reports that lack balanced perspective, explicit limitations, or citation grounding."
)

SECTION_HEADERS = (
    "Context",
    "Findings by Sub-Question",
    "Contradictions and Gaps",
    "Actionable Takeaways",
    "Limitations and Assumptions",
)

CITATION_ANCHOR_RE = re.compile(r"\[S\d+\]")


class _LLMQualityOutput(BaseModel):
    pass_check: bool
    feedback: list[str] = Field(default_factory=list)


def _deterministic_quality(
    *,
    report: str,
    executive_summary: str,
    citations: list[Citation],
) -> tuple[int, list[str]]:
    issues: list[str] = []
    score = 100

    if len(report.strip()) < 900:
        issues.append("Report body is too short; add more concrete findings.")
        score -= 18

    missing_sections = [header for header in SECTION_HEADERS if header not in report]
    if missing_sections:
        issues.append("Missing required sections: " + ", ".join(missing_sections) + ".")
        score -= 25

    summary_lines = [line for line in executive_summary.splitlines() if line.strip()]
    if len(summary_lines) < 4:
        issues.append("Executive summary is too thin; target 5-8 concise lines.")
        score -= 12

    text_lower = report.lower()
    if "limitations" not in text_lower and "assumption" not in text_lower:
        issues.append("Limitations/assumptions are not explicit.")
        score -= 15

    anchor_count = len(CITATION_ANCHOR_RE.findall(report))
    if anchor_count < 3 and len(citations) < 4:
        issues.append("Citation grounding is weak; include inline anchors like [S1].")
        score -= 18

    balance_markers = ("risk", "opportunit", "trade-off", "counter")
    balance_hits = sum(1 for marker in balance_markers if marker in text_lower)
    if balance_hits < 2:
        issues.append("Analysis appears one-sided; include balanced perspective.")
        score -= 12

    return max(0, min(100, score)), issues


def _default_rewrite_guidance() -> list[str]:
    return [
        "Improve balance: cover both upside and downside explicitly.",
        "Strengthen limitations and assumptions with concrete caveats.",
        "Add citation anchors [S#] in key claims.",
        "Tighten executive summary to 5-8 specific lines.",
    ]


async def run_quality_check(
    query: str,
    report: str,
    executive_summary: str,
    citations: list[Citation],
) -> QualityCheck:
    base_score, deterministic_issues = _deterministic_quality(
        report=report,
        executive_summary=executive_summary,
        citations=citations,
    )

    llm_pass = True
    llm_feedback: list[str] = []
    prompt = textwrap.dedent(
        f"""
        Query:
        {query}

        Executive Summary:
        {executive_summary}

        Report:
        {report[:7000]}

        Return JSON:
        - pass_check: boolean
        - feedback: concise list of issues to fix (max 5)
        """
    ).strip()
    try:
        llm_out = await call_openai_typed(
            system_prompt=QUALITY_SYSTEM,
            user_prompt=prompt,
            schema=_LLMQualityOutput,
        )
        llm_pass = llm_out.pass_check
        llm_feedback = llm_out.feedback[:5]
    except Exception:
        llm_pass = True
        llm_feedback = []

    combined_issues = list(dict.fromkeys(deterministic_issues + llm_feedback))[:8]
    passed = (base_score >= 72) and llm_pass and (len(deterministic_issues) <= 1)
    if passed:
        return QualityCheck(passed=True, score=max(72, base_score), issues=[], refinement_queries=[])

    return QualityCheck(
        passed=False,
        score=min(base_score, 71),
        issues=combined_issues,
        refinement_queries=(combined_issues[:4] or _default_rewrite_guidance()),
    )
