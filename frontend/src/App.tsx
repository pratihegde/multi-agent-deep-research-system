import { FormEvent, useEffect, useMemo, useRef, useState } from "react";

import { startChatSSE } from "./api/chat";
import { LiveLogsPanel } from "./components/LiveLogsPanel";
import { ReportView } from "./components/ReportView";
import { SourcesLoader } from "./components/SourcesLoader";
import { TraceTimeline } from "./components/TraceTimeline";
import type { DoneEvent, EventType, SourceFetchEvent, TraceEvent } from "./types";

interface ThreadSummary {
  id: string;
  title: string;
  updatedAt: string;
  queries: string[];
  lastDone: DoneEvent | null;
}

interface UpsertOptions {
  addQuery?: boolean;
}

function App() {
  const [message, setMessage] = useState(
    "Should company X expand into Southeast Asian markets in 2026? Consider regulatory risks, key competitors, and infrastructure requirements."
  );
  const [threadId, setThreadId] = useState<string | undefined>(undefined);
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [isRunning, setIsRunning] = useState(false);
  const [streamReport, setStreamReport] = useState("");
  const [doneEvent, setDoneEvent] = useState<DoneEvent | null>(null);
  const [logs, setLogs] = useState<string[]>([]);
  const [sources, setSources] = useState<SourceFetchEvent[]>([]);
  const [traces, setTraces] = useState<TraceEvent[]>([]);
  const activeThreadRef = useRef<string | undefined>(threadId);

  useEffect(() => {
    activeThreadRef.current = threadId;
  }, [threadId]);

  const statusText = useMemo(() => {
    if (isRunning) return "Research in progress";
    if (doneEvent) return "Last run complete";
    return "Ready";
  }, [isRunning, doneEvent]);

  function pushLog(line: string) {
    setLogs((prev) => [...prev, `${new Date().toLocaleTimeString()}  ${line}`]);
  }

  function upsertThread(
    id: string,
    query: string,
    done: DoneEvent | null = null,
    options: UpsertOptions = {}
  ) {
    const addQuery = options.addQuery ?? true;
    setThreads((prev) => {
      const existing = prev.find((item) => item.id === id);
      const nextQueries = existing
        ? addQuery
          ? existing.queries[existing.queries.length - 1] === query
            ? existing.queries
            : [...existing.queries, query]
          : existing.queries
        : [query];
      const updated: ThreadSummary = existing
        ? {
            ...existing,
            updatedAt: new Date().toISOString(),
            queries: nextQueries,
            lastDone: done ?? existing.lastDone
          }
        : {
            id,
            title: query.slice(0, 72),
            updatedAt: new Date().toISOString(),
            queries: nextQueries,
            lastDone: done
          };
      if (!existing) {
        return [updated, ...prev].slice(0, 12);
      }
      return [updated, ...prev.filter((item) => item.id !== id)].slice(0, 12);
    });
  }

  function switchThread(nextThreadId: string) {
    setThreadId(nextThreadId);
    setMessage("");
    const selected = threads.find((item) => item.id === nextThreadId);
    setDoneEvent(selected?.lastDone ?? null);
    setStreamReport("");
    setSources([]);
    setTraces([]);
    setLogs([`${new Date().toLocaleTimeString()}  Switched to thread ${nextThreadId}`]);
  }

  function createNewThread() {
    setThreadId(undefined);
    setMessage("");
    setDoneEvent(null);
    setStreamReport("");
    setSources([]);
    setTraces([]);
    setLogs([`${new Date().toLocaleTimeString()}  Created new thread context`]);
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!message.trim() || isRunning) return;
    const outgoingMessage = message.trim();
    const requestedThreadId = activeThreadRef.current;
    setIsRunning(true);
    setDoneEvent(null);
    setStreamReport("");
    setSources([]);
    setTraces([]);
    setLogs([]);
    setMessage("");
    if (requestedThreadId) {
      upsertThread(requestedThreadId, outgoingMessage, null, { addQuery: true });
    }
    pushLog("Submitting query to /chat");

    try {
      await startChatSSE(
        { message: outgoingMessage, thread_id: requestedThreadId },
        {
          onEvent: (event: EventType, data: any) => {
            switch (event) {
              case "thread_id":
                setThreadId(data.thread_id);
                upsertThread(data.thread_id, outgoingMessage, null, { addQuery: false });
                if (requestedThreadId && requestedThreadId !== data.thread_id) {
                  pushLog(
                    `Warning: backend returned different thread_id (${data.thread_id}) than requested (${requestedThreadId})`
                  );
                }
                pushLog(`Thread assigned: ${data.thread_id}`);
                break;
              case "planning":
                pushLog(`Planner created ${data.sub_question_count} sub-questions`);
                break;
              case "research_progress":
                pushLog(`${data.sub_question_id}: ${data.status} (${data.evidence_count ?? 0} evidence)`);
                break;
              case "source_fetch":
                setSources((prev) => [...prev, data as SourceFetchEvent]);
                pushLog(`Source ${data.status}: ${data.source_name}`);
                break;
              case "trace":
                setTraces((prev) => [...prev, data as TraceEvent]);
                break;
              case "quality":
                pushLog(`Quality score: ${data.score} (${data.passed ? "pass" : "needs refinement"})`);
                break;
              case "writing":
                pushLog("Writer started generating report");
                break;
              case "message":
                if (typeof data.chunk === "string") {
                  setStreamReport((prev) => prev + data.chunk);
                }
                break;
              case "error":
                pushLog(`Error at ${data.stage}: ${data.detail}`);
                break;
              case "done":
                setDoneEvent(data as DoneEvent);
                if (data.thread_id) {
                  upsertThread(data.thread_id, data.query ?? outgoingMessage, data as DoneEvent, {
                    addQuery: false
                  });
                }
                pushLog(`Done. citations=${data.citations?.length ?? 0}`);
                break;
            }
          },
          onError: (msg: string) => {
            pushLog(`Stream error: ${msg}`);
          },
          onDone: () => {
            setIsRunning(false);
          }
        }
      );
    } catch (err) {
      setIsRunning(false);
      pushLog(`Unhandled error: ${String(err)}`);
    }
  }

  return (
    <div className="app-shell">
      <div className="bg-gradient" />
      <header className="topbar">
        <div>
          <h1>Astra Deep Research Studio</h1>
          <p className="muted">
            Multi-agent intelligence cockpit with live tracing, source confidence, and threaded memory
          </p>
        </div>
        <div className="status-chip">{statusText}</div>
      </header>

      <main className="layout">
        <section className="left-col">
          <section className="panel thread-panel">
            <div className="panel-header">
              <h3>Conversations</h3>
              <button
                type="button"
                className="secondary-btn"
                onClick={createNewThread}
                disabled={isRunning}
              >
                New Thread
              </button>
            </div>
            <div className="thread-list">
              {threads.length === 0 ? (
                <p className="muted">No threads yet. Run your first query.</p>
              ) : (
                threads.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    className={`thread-item ${item.id === threadId ? "active" : ""}`}
                    onClick={() => switchThread(item.id)}
                    disabled={isRunning}
                  >
                    <div className="thread-title">{item.title}</div>
                    <div className="thread-meta">
                      {item.id.slice(0, 12)}... | {item.queries.length} prompts
                    </div>
                  </button>
                ))
              )}
            </div>
          </section>

          <form className="query-form" onSubmit={onSubmit}>
            <label htmlFor="query">{threadId ? "Follow-up Query" : "Research Query"}</label>
            <textarea
              id="query"
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              rows={4}
              placeholder={threadId ? "Ask a follow-up in this thread..." : "Ask a deep research question..."}
            />
            <div className="form-row">
              <div className="thread-box">
                <span className="muted">Thread ID</span>
                <code>{threadId ?? "new thread will be created"}</code>
              </div>
              <button type="submit" disabled={isRunning}>
                {isRunning ? "Running..." : "Run Research"}
              </button>
            </div>
          </form>

          {threadId ? (
            <section className="panel thread-history-panel">
              <div className="panel-header">
                <h3>Thread Prompts</h3>
                <span className="muted">
                  {threads.find((item) => item.id === threadId)?.queries.length ?? 0} entries
                </span>
              </div>
              <div className="thread-prompt-list">
                {(threads.find((item) => item.id === threadId)?.queries ?? []).map((query, idx) => (
                  <div className="thread-prompt-item" key={`${idx}-${query.slice(0, 24)}`}>
                    {idx + 1}. {query}
                  </div>
                ))}
              </div>
            </section>
          ) : null}

          <ReportView streamReport={streamReport} doneEvent={doneEvent} />
        </section>

        <aside className="right-col">
          <SourcesLoader sources={sources} />
          <TraceTimeline traces={traces} />
          <LiveLogsPanel logs={logs} />
        </aside>
      </main>
    </div>
  );
}

export default App;
