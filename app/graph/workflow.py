from __future__ import annotations

from collections import OrderedDict
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.config import ENABLE_REFINEMENT, HARD_MAX_QUERIES_PER_SUBQUESTION, MAX_REFINEMENT_LOOPS
from app.agents.planner import run_planner
from app.agents.quality import run_quality_check
from app.agents.researcher import run_research_batch
from app.agents.writer import stream_report_chunks
from app.graph.state import GraphState
from app.models import Citation
from app.services.tracing import trace_end, trace_start, utc_now_iso
from app.tools.tavily_search import normalize_url


async def _emit_event(state: GraphState, event: str, data: dict) -> None:
    runtime = state.get("runtime", {})
    emitter = runtime.get("emit_event")
    if emitter:
        payload = {"thread_id": state.get("thread_id"), **data}
        await emitter(event, payload)


async def _emit_trace(state: GraphState, node: str, status: str, duration_ms: int | None = None) -> None:
    payload: dict[str, Any] = {
        "node": node,
        "status": status,
        "timestamp": utc_now_iso(),
    }
    if duration_ms is not None:
        payload["duration_ms"] = duration_ms
    await _emit_event(state, "trace", payload)


def _dedupe_citations(citations: list[Citation]) -> list[Citation]:
    output: OrderedDict[str, Citation] = OrderedDict()
    for citation in citations:
        output[normalize_url(str(citation.url))] = citation
    return list(output.values())


async def plan_node(state: GraphState) -> GraphState:
    t0 = trace_start(state, "plan")
    try:
        plan = await run_planner(
            query=state["query"],
            history=state.get("history", []),
            prior_context=state.get("prior_context", ""),
        )
        state["plan"] = plan
        await _emit_event(
            state,
            "planning",
            {
                "sub_question_count": len(plan.sub_questions),
                "sub_questions": [sq.model_dump(mode="json") for sq in plan.sub_questions],
            },
        )
        return state
    except Exception as exc:
        state.setdefault("errors", []).append({"stage": "plan", "detail": str(exc)})
        await _emit_event(state, "error", {"stage": "plan", "detail": str(exc)})
        raise
    finally:
        duration_ms = trace_end(state, "plan", t0)
        await _emit_trace(state, "plan", "end", duration_ms=duration_ms)


async def research_node(state: GraphState) -> GraphState:
    t0 = trace_start(state, "research")
    await _emit_trace(state, "research", "start")
    try:
        plan = state["plan"]
        notes, new_citations, errors = await run_research_batch(
            sub_questions=plan.sub_questions,
            emit_event=lambda event, data: _emit_event(state, event, data),
            query=state["query"],
            max_concurrency=state.get("runtime", {}).get("max_concurrency", 4),
            existing_notes=state.get("research_notes"),
        )
        state["research_notes"] = notes
        state["citations"] = _dedupe_citations((state.get("citations") or []) + new_citations)
        state.setdefault("metadata", {})["accepted_source_count"] = len(state["citations"])
        if errors:
            state.setdefault("errors", []).extend(errors)
        return state
    except Exception as exc:
        state.setdefault("errors", []).append({"stage": "research", "detail": str(exc)})
        await _emit_event(state, "error", {"stage": "research", "detail": str(exc)})
        return state
    finally:
        duration_ms = trace_end(state, "research", t0)
        await _emit_trace(state, "research", "end", duration_ms=duration_ms)


def _inject_refinement_queries(state: GraphState, queries: list[str]) -> None:
    if not queries:
        return
    plan = state["plan"]
    for idx, query in enumerate(queries):
        sq = plan.sub_questions[idx % len(plan.sub_questions)]
        if query not in sq.search_queries and len(sq.search_queries) < HARD_MAX_QUERIES_PER_SUBQUESTION:
            sq.search_queries.append(query)
    state["plan"] = plan


async def quality_check_node(state: GraphState) -> GraphState:
    t0 = trace_start(state, "quality_check")
    await _emit_trace(state, "quality_check", "start")
    try:
        quality = await run_quality_check(
            query=state["query"],
            plan=state["plan"],
            research_notes=state.get("research_notes", {}),
        )
        state["quality"] = quality
        refinement_count = int(state.get("metadata", {}).get("refinement_count", 0))
        quota_exhausted = any(
            "quota exceeded" in str(err.get("detail", "")).lower()
            or "usage limit" in str(err.get("detail", "")).lower()
            for err in state.get("errors", [])
        )
        needs_refinement = (
            ENABLE_REFINEMENT
            and (not quality.passed)
            and (refinement_count < MAX_REFINEMENT_LOOPS)
            and (not quota_exhausted)
        )
        state.setdefault("metadata", {})["needs_refinement"] = needs_refinement
        if needs_refinement:
            state["refinement_used"] = True
            state.setdefault("metadata", {})["refinement_count"] = refinement_count + 1
            _inject_refinement_queries(state, quality.refinement_queries)
        await _emit_event(
            state,
            "quality",
            {
                "passed": quality.passed,
                "score": quality.score,
                "issues": quality.issues,
            },
        )
        return state
    except Exception as exc:
        state.setdefault("errors", []).append({"stage": "quality_check", "detail": str(exc)})
        await _emit_event(state, "error", {"stage": "quality_check", "detail": str(exc)})
        state.setdefault("metadata", {})["needs_refinement"] = False
        return state
    finally:
        duration_ms = trace_end(state, "quality_check", t0)
        await _emit_trace(state, "quality_check", "end", duration_ms=duration_ms)


async def write_report_node(state: GraphState) -> GraphState:
    t0 = trace_start(state, "write_report")
    await _emit_event(state, "writing", {"status": "started"})
    await _emit_trace(state, "write_report", "start")
    try:
        final_report = await stream_report_chunks(
            query=state["query"],
            research_notes={
                key: note.model_dump(mode="json")
                for key, note in state.get("research_notes", {}).items()
            },
            citations=state.get("citations", []),
            quality_score=(state.get("quality").score if state.get("quality") else None),
            emit_event=lambda event, data: _emit_event(state, event, data),
        )
        state["final_report"] = final_report
        return state
    except Exception as exc:
        state.setdefault("errors", []).append({"stage": "write_report", "detail": str(exc)})
        await _emit_event(state, "error", {"stage": "write_report", "detail": str(exc)})
        raise
    finally:
        duration_ms = trace_end(state, "write_report", t0)
        await _emit_trace(state, "write_report", "end", duration_ms=duration_ms)


def quality_router(state: GraphState) -> str:
    needs_refinement = state.get("metadata", {}).get("needs_refinement", False)
    if needs_refinement:
        return "research"
    return "write_report"


def build_workflow() -> Any:
    graph = StateGraph(GraphState)
    graph.add_node("plan", plan_node)
    graph.add_node("research", research_node)
    graph.add_node("quality_check", quality_check_node)
    graph.add_node("write_report", write_report_node)
    graph.add_edge(START, "plan")
    graph.add_edge("plan", "research")
    graph.add_edge("research", "quality_check")
    graph.add_conditional_edges(
        "quality_check",
        quality_router,
        {
            "research": "research",
            "write_report": "write_report",
        },
    )
    graph.add_edge("write_report", END)
    return graph.compile()
