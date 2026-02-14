from __future__ import annotations

import asyncio
import uuid

from app.graph.state import GraphState


class ThreadStore:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._threads: dict[str, dict] = {}

    async def get_or_create(self, thread_id: str | None) -> tuple[str, GraphState]:
        async with self._lock:
            resolved_id = thread_id or str(uuid.uuid4())
            if resolved_id not in self._threads:
                self._threads[resolved_id] = {
                    "history": [],
                    "state": {},
                    "report_memories": [],
                }
            state = self._threads[resolved_id]["state"]
            history = self._threads[resolved_id]["history"]
            report_memories = self._threads[resolved_id].get("report_memories", [])
            merged_state: GraphState = dict(state)
            merged_state["history"] = list(history)
            merged_state["report_memories"] = list(report_memories)
            merged_state["thread_id"] = resolved_id
            return resolved_id, merged_state

    async def append_message(self, thread_id: str, role: str, content: str) -> None:
        async with self._lock:
            self._threads.setdefault(
                thread_id,
                {"history": [], "state": {}, "report_memories": []},
            )
            self._threads[thread_id]["history"].append({"role": role, "content": content})

    async def append_report_memory(self, thread_id: str, memory: dict) -> None:
        async with self._lock:
            self._threads.setdefault(
                thread_id,
                {"history": [], "state": {}, "report_memories": []},
            )
            memories = self._threads[thread_id]["report_memories"]
            memories.append(memory)
            # Keep recent history bounded for in-memory usage.
            self._threads[thread_id]["report_memories"] = memories[-12:]

    async def save_state(self, thread_id: str, state: GraphState) -> None:
        async with self._lock:
            self._threads.setdefault(
                thread_id,
                {"history": [], "state": {}, "report_memories": []},
            )
            safe_state = dict(state)
            # Runtime objects must not be persisted in thread state.
            safe_state.pop("runtime", None)
            self._threads[thread_id]["state"] = safe_state

    async def get_state(self, thread_id: str) -> GraphState:
        async with self._lock:
            thread = self._threads.get(
                thread_id,
                {"history": [], "state": {}, "report_memories": []},
            )
            out: GraphState = dict(thread.get("state", {}))
            out["history"] = list(thread.get("history", []))
            out["report_memories"] = list(thread.get("report_memories", []))
            out["thread_id"] = thread_id
            return out


thread_store = ThreadStore()
