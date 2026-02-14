interface Props {
  logs: string[];
}

export function LiveLogsPanel({ logs }: Props) {
  return (
    <section className="panel">
      <div className="panel-header">
        <h3>Live Logs</h3>
        <span className="muted">{logs.length} entries</span>
      </div>
      <div className="log-list">
        {logs.length === 0 ? (
          <p className="muted">No logs yet.</p>
        ) : (
          logs
            .slice(-80)
            .reverse()
            .map((line, idx) => (
              <div className="log-item" key={`${idx}-${line.slice(0, 20)}`}>
                {line}
              </div>
            ))
        )}
      </div>
    </section>
  );
}
