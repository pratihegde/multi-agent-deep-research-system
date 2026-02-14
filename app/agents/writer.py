from __future__ import annotations

import json
import textwrap
from typing import Awaitable, Callable

from app.config import MAX_ACCEPTED_SOURCES_TOTAL
from app.models import Citation, FinalReport
from app.services.llm import call_openai_typed, stream_openai_text

EmitEvent = Callable[[str, dict], Awaitable[None]]

WRITER_SYSTEM = (
    "You are a report generation agent. Return ONLY JSON with keys: "
    "executive_summary, report, key_takeaways, limitations."
)

REPORT_SYSTEM = (
    "You are a research report writer. Produce a concise, well-structured plain-text report. "
    "Follow the exact section headers provided."
)


def _chunk_text(text: str, chunk_size: int = 450) -> list[str]:
    return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)] or [""]


def _compress_research_notes(research_notes: dict) -> dict:
    compressed: dict[str, dict] = {}
    for sub_id in sorted(research_notes.keys()):
        note = research_notes[sub_id]
        findings = note.get("findings", [])[:3]
        compressed[sub_id] = {
            "evidence_bullets": note.get("evidence_bullets", [])[:5],
            "findings": [
                {
                    "title": finding.get("title", ""),
                    "url": finding.get("url", ""),
                    "snippet": (finding.get("snippet", "") or "")[:220],
                    "source_name": finding.get("source_name", "unknown"),
                }
                for finding in findings
            ],
            "contradictions": note.get("contradictions", [])[:3],
            "gaps": note.get("gaps", [])[:3],
        }
    return compressed


def _build_citation_anchors(citations: list[Citation]) -> list[dict]:
    selected = citations[:MAX_ACCEPTED_SOURCES_TOTAL]
    anchored: list[dict] = []
    for idx, citation in enumerate(selected, start=1):
        anchored.append(
            {
                "anchor": f"S{idx}",
                "title": citation.title,
                "url": str(citation.url),
                "source_name": citation.source_name,
            }
        )
    return anchored


def _fallback_report(query: str, packet: dict, anchored_citations: list[dict]) -> FinalReport:
    sections: list[str] = []
    sections.append("Context")
    sections.append("-------")
    sections.append(f"Research completed with partial synthesis for query: {query}")

    sections.append("\nFindings by Sub-Question")
    sections.append("------------------------")
    for sub_id, note in packet.items():
        sections.append(f"{sub_id.upper()}")
        for bullet in note.get("evidence_bullets", [])[:3]:
            sections.append(f"- {bullet}")

    sections.append("\nContradictions and Gaps")
    sections.append("-----------------------")
    for sub_id, note in packet.items():
        contradictions = note.get("contradictions", [])
        gaps = note.get("gaps", [])
        if contradictions:
            sections.append(f"{sub_id.upper()} contradictions: " + "; ".join(contradictions[:2]))
        if gaps:
            sections.append(f"{sub_id.upper()} gaps: " + "; ".join(gaps[:2]))

    sections.append("\nActionable Takeaways")
    sections.append("--------------------")
    sections.append("- Prioritize decisions with strongest cross-source support.")
    sections.append("- Validate high-impact assumptions with primary institutional sources.")

    sections.append("\nLimitations and Assumptions")
    sections.append("---------------------------")
    sections.append("- Writer fallback was used, so narrative quality may be reduced.")
    sections.append("- Some sub-questions may require additional source coverage.")

    if anchored_citations:
        sections.append("\nSource Anchors")
        sections.append("--------------")
        for source in anchored_citations:
            sections.append(f"[{source['anchor']}] {source['source_name']} - {source['title']}")

    return FinalReport(
        executive_summary="Partial synthesis generated using fallback formatter due to writer model failure.",
        report="\n".join(sections),
        key_takeaways=[
            "Evidence has been condensed into actionable sections.",
            "Use source anchors [S#] for quick citation checks.",
            "Review limitations before making definitive recommendations.",
        ],
        limitations="Writer fallback was used; final narrative should be reviewed for depth and completeness.",
    )


async def _stream_report_text(
    *,
    query: str,
    compressed_notes: dict,
    anchored_citations: list[dict],
    emit_event: EmitEvent,
) -> str:
    notes_json = json.dumps(compressed_notes, ensure_ascii=False, default=str)
    citations_json = json.dumps(anchored_citations, ensure_ascii=False)

    prompt = textwrap.dedent(
        f"""
        Query:
        {query}

        Evidence packet (JSON):
        {notes_json}

        Citation anchors (JSON):
        {citations_json}

        Output requirements:
        - Plain text only
        - Max 850 words total
        - Short paragraphs; prefer bullets where possible
        - Use EXACT section headers and separators:
          Context\n-------
          Findings by Sub-Question\n------------------------
          Contradictions and Gaps\n-----------------------
          Actionable Takeaways\n--------------------
          Limitations and Assumptions\n---------------------------
        - Within findings, max 3 bullets per sub-question
        - Use citation anchors like [S1], [S2] inline
        """
    ).strip()

    chunks: list[str] = []
    async for token in stream_openai_text(
        system_prompt=REPORT_SYSTEM,
        user_prompt=prompt,
    ):
        chunks.append(token)
        await emit_event("message", {"chunk": token})

    return "".join(chunks).strip()


async def stream_report_chunks(
    query: str,
    research_notes: dict,
    citations: list[Citation],
    quality_score: int | None,
    emit_event: EmitEvent,
) -> FinalReport:
    compressed_notes = _compress_research_notes(research_notes)
    anchored_citations = _build_citation_anchors(citations)

    report_text = ""
    try:
        report_text = await _stream_report_text(
            query=query,
            compressed_notes=compressed_notes,
            anchored_citations=anchored_citations,
            emit_event=emit_event,
        )
    except Exception:
        report_text = ""

    summary_prompt = textwrap.dedent(
        f"""
        Query:
        {query}

        Report body:
        {report_text[:4000]}

        Output requirements:
        - executive_summary: 5-8 concise lines
        - key_takeaways: 4-8 actionable bullets
        - limitations: include ambiguity + gaps + any failures
        """
    ).strip()

    try:
        summary = await call_openai_typed(
            system_prompt=WRITER_SYSTEM,
            user_prompt=summary_prompt,
            schema=FinalReport,
        )
        return FinalReport(
            executive_summary=summary.executive_summary,
            report=report_text or summary.report,
            key_takeaways=summary.key_takeaways,
            limitations=summary.limitations,
        )
    except Exception:
        if not report_text:
            return _fallback_report(query, compressed_notes, anchored_citations)
        return FinalReport(
            executive_summary="Executive summary unavailable due to summarization failure.",
            report=report_text,
            key_takeaways=[
                "Review the findings and action items by section.",
                "Validate high-impact assumptions with primary sources.",
            ],
            limitations="Summarization failed; use report body and citations as primary evidence.",
        )
