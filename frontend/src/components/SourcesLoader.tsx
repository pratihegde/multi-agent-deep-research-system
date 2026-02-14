import type { SourceFetchEvent } from "../types";

interface Props {
  sources: SourceFetchEvent[];
}

export function SourcesLoader({ sources }: Props) {
  return (
    <section className="panel">
      <div className="panel-header">
        <h3>Researching Sources</h3>
        <span className="muted">{sources.length} events</span>
      </div>
      <div className="source-list">
        {sources.length === 0 ? (
          <p className="muted">Waiting for research events...</p>
        ) : (
          sources
            .slice(-20)
            .reverse()
            .map((src, idx) => (
              <div className="source-item" key={`${src.url}-${idx}`}>
                <span className={`dot dot-${src.status}`} />
                <div>
                  <div className="source-name">{src.source_name}</div>
                  <div className="source-title">{src.title}</div>
                </div>
              </div>
            ))
        )}
      </div>
    </section>
  );
}
