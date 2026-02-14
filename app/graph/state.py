from __future__ import annotations

from typing import Any, TypedDict

from app.models import Citation, FinalReport, Plan, QualityCheck, ResearchNote


class GraphState(TypedDict, total=False):
    query: str
    thread_id: str
    history: list[dict[str, str]]
    report_memories: list[dict[str, Any]]
    prior_context: str
    plan: Plan
    research_notes: dict[str, ResearchNote]
    citations: list[Citation]
    quality: QualityCheck
    final_report: FinalReport
    errors: list[dict[str, Any]]
    trace_events: list[dict[str, Any]]
    metadata: dict[str, Any]
    refinement_used: bool
    runtime: dict[str, Any]
