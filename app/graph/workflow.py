from __future__ import annotations

from collections import OrderedDict
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.config import ENABLE_REFINEMENT, MAX_REFINEMENT_LOOPS
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


async def _emit_trace(
    state: GraphState,
    node: str,
    status: str,
    duration_ms: int | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "node": node,
        "status": status,
        "timestamp": utc_now_iso(),
    }
    if duration_ms is not None:
        payload["duration_ms"] = duration_ms
    if extra:
        payload["extra"] = extra
    await _emit_event(state, "trace", payload)


def _dedupe_citations(citations: list[Citation]) -> list[Citation]:
    output: OrderedDict[str, Citation] = OrderedDict()
    for citation in citations:
        output[normalize_url(str(citation.url))] = citation
    return list(output.values())


def _ensure_shared_memory(state: GraphState) -> dict[str, Any]:
    shared = state.get("shared_memory") or {}
    shared.setdefault("thread_id", state.get("thread_id"))
    shared.setdefault("recent_messages", state.get("history", [])[-12:])
    shared.setdefault("recent_reports", state.get("report_memories", [])[-6:])
    shared.setdefault("open_gaps", [])
    state["shared_memory"] = shared
    return shared


async def plan_node(state: GraphState) -> GraphState:
    t0 = trace_start(state, "plan")
    await _emit_trace(state, "plan", "start")
    try:
        shared_memory = _ensure_shared_memory(state)
        plan = await run_planner(
            query=state["query"],
            history=state.get("history", []),
            prior_context=state.get("prior_context", ""),
            shared_memory=shared_memory,
        )
        state["plan"] = plan
        skip_web = any(
            "SKIP_WEB_RESEARCH" in str(assumption).upper()
            for assumption in (plan.assumptions or [])
        )
        state.setdefault("metadata", {})["skip_web_research"] = skip_web
        await _emit_event(
            state,
            "planning",
            {
                "sub_question_count": len(plan.sub_questions),
                "sub_questions": [sq.model_dump(mode="json") for sq in plan.sub_questions],
                "skip_web_research": skip_web,
            },
        )
        await _emit_trace(
            state,
            "plan",
            "detail",
            extra={
                "sub_questions": [
                    {
                        "id": sq.id,
                        "priority": sq.priority,
                        "question": sq.question,
                        "search_queries": sq.search_queries,
                    }
                    for sq in plan.sub_questions
                ]
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
        _ensure_shared_memory(state)
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


async def quality_check_node(state: GraphState) -> GraphState:
    t0 = trace_start(state, "quality_check")
    await _emit_trace(state, "quality_check", "start")
    try:
        _ensure_shared_memory(state)
        quality = await run_quality_check(
            query=state["query"],
            report=state.get("final_report").report if state.get("final_report") else "",
            executive_summary=(
                state.get("final_report").executive_summary if state.get("final_report") else ""
            ),
            citations=state.get("citations", []),
        )
        state["quality"] = quality
        quality_iterations = int(state.get("metadata", {}).get("quality_iterations", 0))
        needs_rewrite = ENABLE_REFINEMENT and (not quality.passed) and (
            quality_iterations < MAX_REFINEMENT_LOOPS
        )
        state.setdefault("metadata", {})["needs_rewrite"] = needs_rewrite
        if needs_rewrite:
            state["refinement_used"] = True
            state.setdefault("metadata", {})["quality_iterations"] = quality_iterations + 1
            state["quality_feedback"] = quality.issues[:4]
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
        state.setdefault("metadata", {})["needs_rewrite"] = False
        return state
    finally:
        duration_ms = trace_end(state, "quality_check", t0)
        await _emit_trace(state, "quality_check", "end", duration_ms=duration_ms)


async def write_report_node(state: GraphState) -> GraphState:
    t0 = trace_start(state, "write_report")
    rewrite_iteration = int(state.get("metadata", {}).get("quality_iterations", 0))
    await _emit_event(
        state,
        "writing",
        {
            "status": "started",
            "rewrite_iteration": rewrite_iteration,
        },
    )
    await _emit_trace(state, "write_report", "start")
    try:
        shared_memory = _ensure_shared_memory(state)
        final_report = await stream_report_chunks(
            query=state["query"],
            research_notes={
                key: note.model_dump(mode="json")
                for key, note in state.get("research_notes", {}).items()
            },
            citations=state.get("citations", []),
            history=state.get("history", []),
            shared_memory=shared_memory,
            quality_score=(state.get("quality").score if state.get("quality") else None),
            quality_feedback=state.get("quality_feedback", []),
            rewrite_iteration=rewrite_iteration,
            emit_event=lambda event, data: _emit_event(state, event, data),
        )
        state["final_report"] = final_report

        latest_gaps: list[str] = []
        for note in state.get("research_notes", {}).values():
            latest_gaps.extend(note.gaps[:2])
        shared_memory["open_gaps"] = list(dict.fromkeys(latest_gaps))[:8]
        return state
    except Exception as exc:
        state.setdefault("errors", []).append({"stage": "write_report", "detail": str(exc)})
        await _emit_event(state, "error", {"stage": "write_report", "detail": str(exc)})
        raise
    finally:
        duration_ms = trace_end(state, "write_report", t0)
        await _emit_trace(state, "write_report", "end", duration_ms=duration_ms)


def quality_router(state: GraphState) -> str:
    needs_rewrite = state.get("metadata", {}).get("needs_rewrite", False)
    if needs_rewrite:
        return "write_report"
    return "end"


def plan_router(state: GraphState) -> str:
    if state.get("metadata", {}).get("skip_web_research", False):
        return "write_report"
    return "research"


def build_workflow() -> Any:
    graph = StateGraph(GraphState)
    graph.add_node("plan", plan_node)
    graph.add_node("research", research_node)
    graph.add_node("quality_check", quality_check_node)
    graph.add_node("write_report", write_report_node)
    graph.add_edge(START, "plan")
    graph.add_conditional_edges(
        "plan",
        plan_router,
        {
            "research": "research",
            "write_report": "write_report",
        },
    )
    graph.add_edge("research", "write_report")
    graph.add_edge("write_report", "quality_check")
    graph.add_conditional_edges(
        "quality_check",
        quality_router,
        {
            "write_report": "write_report",
            "end": END,
        },
    )
    return graph.compile()
