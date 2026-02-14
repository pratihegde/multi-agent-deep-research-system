from __future__ import annotations

import time
from datetime import datetime, timezone


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def trace_start(state: dict, node: str) -> float:
    if "trace_events" not in state:
        state["trace_events"] = []
    state["trace_events"].append({"node": node, "status": "start", "timestamp": utc_now_iso()})
    return time.perf_counter()


def trace_end(
    state: dict,
    node: str,
    t0: float,
    *,
    status: str = "end",
    extra: dict | None = None,
) -> int:
    duration_ms = int((time.perf_counter() - t0) * 1000)
    event = {
        "node": node,
        "status": status,
        "timestamp": utc_now_iso(),
        "duration_ms": duration_ms,
        "extra": extra or {},
    }
    state.setdefault("trace_events", []).append(event)
    state.setdefault("metadata", {}).setdefault("timings_ms", {})[node] = duration_ms
    return duration_ms
