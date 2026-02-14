import type { TraceEvent } from "../types";

interface Props {
  traces: TraceEvent[];
}

export function TraceTimeline({ traces }: Props) {
  function formatTime(value: string | undefined) {
    if (!value) return "-";
    const dt = new Date(value);
    return Number.isNaN(dt.getTime()) ? "-" : dt.toLocaleTimeString();
  }

  return (
    <section className="panel">
      <div className="panel-header">
        <h3>Trace Timeline</h3>
        <span className="muted">{traces.length} events</span>
      </div>
      <div className="trace-list">
        {traces.length === 0 ? (
          <p className="muted">No trace yet.</p>
        ) : (
          traces
            .slice(-24)
            .reverse()
            .map((trace, idx) => (
              <div className="trace-item" key={`${trace.node}-${trace.timestamp}-${idx}`}>
                <div className="trace-node">{trace.node}</div>
                <div className="trace-meta">
                  <span>{trace.status}</span>
                  <span>{trace.duration_ms ? `${trace.duration_ms}ms` : ""}</span>
                  <span>{formatTime(trace.timestamp)}</span>
                </div>
              </div>
            ))
        )}
      </div>
    </section>
  );
}
