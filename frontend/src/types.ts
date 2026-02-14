export type EventType =
  | "thread_id"
  | "planning"
  | "research_progress"
  | "source_fetch"
  | "quality"
  | "writing"
  | "message"
  | "trace"
  | "error"
  | "done";

export interface Citation {
  title: string;
  url: string;
  source_name: string;
}

export interface DoneEvent {
  thread_id: string;
  query: string;
  executive_summary: string;
  report: string;
  key_takeaways: string[];
  limitations: string;
  citations: Citation[];
  metadata: {
    sub_question_count: number;
    sources_analyzed: number;
    completion_timestamp: string;
    quality_score?: number;
    refinement_used: boolean;
    timings_ms?: Record<string, number>;
  };
}

export interface SourceFetchEvent {
  thread_id: string;
  sub_question_id: string;
  source_name: string;
  title: string;
  url: string;
  status: "fetched" | "deduped" | "failed";
}

export interface TraceEvent {
  thread_id: string;
  node: string;
  status: "start" | "end" | "detail";
  timestamp: string;
  duration_ms?: number;
  extra?: Record<string, unknown>;
}
