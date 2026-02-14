import type { DoneEvent } from "../types";

interface Props {
  streamReport: string;
  doneEvent: DoneEvent | null;
}

export function ReportView({ streamReport, doneEvent }: Props) {
  const reportText = doneEvent?.report ?? streamReport;
  const sectionHeaders = new Set([
    "Context",
    "Findings by Sub-Question",
    "Contradictions and Gaps",
    "Actionable Takeaways",
    "Limitations and Assumptions"
  ]);

  function renderReportBody(text: string) {
    return text.split("\n").map((line, idx) => {
      const trimmed = line.trim();
      if (sectionHeaders.has(trimmed)) {
        return (
          <div className="report-section-title" key={`sec-${idx}`}>
            {trimmed}
          </div>
        );
      }
      if (/^-{3,}$/.test(trimmed)) {
        return <div className="report-divider" key={`div-${idx}`} />;
      }
      return (
        <div className="report-line" key={`line-${idx}`}>
          {line || "\u00A0"}
        </div>
      );
    });
  }

  return (
    <section className="report-card">
      <div className="panel-header">
        <h2>Research Report</h2>
        {doneEvent ? (
          <span className="pill success">Complete</span>
        ) : (
          <span className="pill">Streaming</span>
        )}
      </div>

      {doneEvent?.executive_summary ? (
        <div className="summary-block">
          <h3>Executive Summary</h3>
          <p>{doneEvent.executive_summary}</p>
        </div>
      ) : null}

      <div className="report-body">{renderReportBody(reportText || "Waiting for report chunks...")}</div>

      {doneEvent?.key_takeaways?.length ? (
        <div className="takeaways">
          <h3>Key Takeaways</h3>
          <ul>
            {doneEvent.key_takeaways.map((item, i) => (
              <li key={`${i}-${item.slice(0, 16)}`}>{item}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {doneEvent?.citations?.length ? (
        <div className="citations">
          <h3>Citations</h3>
          <div className="citation-list">
            {doneEvent.citations.map((c, idx) => (
              <a
                key={`${c.url}-${idx}`}
                href={c.url}
                target="_blank"
                rel="noreferrer"
                title={c.title}
              >
                [{`S${idx + 1}`}] {c.source_name}
              </a>
            ))}
          </div>
        </div>
      ) : null}
    </section>
  );
}
