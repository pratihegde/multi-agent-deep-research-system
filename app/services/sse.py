from __future__ import annotations

import asyncio
import json

from app.graph.workflow import build_workflow
from app.models import DoneMetadata, DonePayload
from app.services.thread_store import thread_store
from app.services.tracing import utc_now_iso


def sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def chat_event_stream(message: str, thread_id: str | None):
    resolved_thread_id, prior_state = await thread_store.get_or_create(thread_id)
    await thread_store.append_message(resolved_thread_id, "user", message)
    yield sse_event("thread_id", {"thread_id": resolved_thread_id})

    queue: asyncio.Queue[str] = asyncio.Queue()

    async def emit(event: str, data: dict) -> None:
        # If upstream already passed thread_id, keep it; otherwise include it.
        payload = data if "thread_id" in data else {"thread_id": resolved_thread_id, **data}
        await queue.put(sse_event(event, payload))

    prior_context = ""
    report_memories = prior_state.get("report_memories", []) or []
    if report_memories:
        recent = report_memories[-3:]
        memory_lines: list[str] = []
        for idx, item in enumerate(recent, start=1):
            query_part = str(item.get("query", "")).strip()
            summary_part = str(item.get("executive_summary", "")).strip()
            if query_part:
                memory_lines.append(f"R{idx} query: {query_part[:140]}")
            if summary_part:
                memory_lines.append(f"R{idx} summary: {summary_part[:220]}")
            takeaways = item.get("key_takeaways", []) or []
            if takeaways:
                memory_lines.append(f"R{idx} takeaway: {str(takeaways[0])[:120]}")
        prior_context = "\n".join(memory_lines)[:900]
    else:
        prior_final_report = prior_state.get("final_report")
        if prior_final_report and getattr(prior_final_report, "executive_summary", None):
            prior_context = prior_final_report.executive_summary
        else:
            assistant_msgs = [
                m.get("content", "")
                for m in prior_state.get("history", [])
                if m.get("role") == "assistant"
            ]
            if assistant_msgs:
                prior_context = assistant_msgs[-1][:280]

    initial_state = {
        "query": message,
        "thread_id": resolved_thread_id,
        "history": prior_state.get("history", []),
        "report_memories": report_memories,
        "prior_context": prior_context[:900],
        "research_notes": {},
        "citations": [],
        "errors": [],
        "trace_events": [],
        "refinement_used": False,
        "metadata": {
            "start_timestamp": utc_now_iso(),
            "run_count": int(prior_state.get("metadata", {}).get("run_count", 0)) + 1,
        },
        "runtime": {
            "emit_event": emit,
            "max_concurrency": 4,
        },
    }

    workflow = build_workflow()
    workflow_task = asyncio.create_task(workflow.ainvoke(initial_state))

    while True:
        if workflow_task.done() and queue.empty():
            break
        try:
            item = await asyncio.wait_for(queue.get(), timeout=0.25)
            yield item
        except asyncio.TimeoutError:
            continue

    try:
        final_state = await workflow_task
    except Exception as exc:
        yield sse_event(
            "error",
            {
                "thread_id": resolved_thread_id,
                "stage": "workflow",
                "detail": str(exc),
            },
        )
        return

    final_report = final_state.get("final_report")
    if not final_report:
        yield sse_event(
            "error",
            {
                "thread_id": resolved_thread_id,
                "stage": "workflow",
                "detail": "Workflow completed without final_report.",
            },
        )
        return

    citations = final_state.get("citations", [])
    metadata = DoneMetadata(
        sub_question_count=len(final_state.get("plan").sub_questions) if final_state.get("plan") else 0,
        sources_analyzed=len(citations),
        completion_timestamp=utc_now_iso(),
        quality_score=final_state.get("quality").score if final_state.get("quality") else None,
        refinement_used=final_state.get("refinement_used", False),
        timings_ms=final_state.get("metadata", {}).get("timings_ms", {}),
    )
    done = DonePayload(
        thread_id=resolved_thread_id,
        query=message,
        executive_summary=final_report.executive_summary,
        report=final_report.report,
        key_takeaways=final_report.key_takeaways,
        limitations=final_report.limitations,
        citations=citations,
        metadata=metadata,
    )
    await thread_store.append_message(resolved_thread_id, "assistant", final_report.report)
    await thread_store.append_report_memory(
        resolved_thread_id,
        {
            "query": message,
            "executive_summary": final_report.executive_summary,
            "key_takeaways": final_report.key_takeaways,
            "limitations": final_report.limitations,
            "citations": [
                {
                    "title": citation.title,
                    "url": str(citation.url),
                    "source_name": citation.source_name,
                }
                for citation in citations[:8]
            ],
            "completion_timestamp": metadata.completion_timestamp,
        },
    )
    await thread_store.save_state(resolved_thread_id, final_state)
    yield sse_event("done", done.model_dump(mode="json"))
