import type { EventType } from "../types";

interface Handlers {
  onEvent: (type: EventType, data: any) => void;
  onError: (message: string) => void;
  onDone: () => void;
}

interface StreamPayload {
  message: string;
  thread_id?: string;
}

export async function startChatSSE(
  payload: StreamPayload,
  handlers: Handlers
): Promise<void> {
  const apiBase = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8000";
  const response = await fetch(`${apiBase}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });

  if (!response.ok || !response.body) {
    handlers.onError(`Request failed with status ${response.status}`);
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let doneSeen = false;

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    while (true) {
      const splitIndex = buffer.indexOf("\n\n");
      if (splitIndex < 0) break;
      const rawEvent = buffer.slice(0, splitIndex);
      buffer = buffer.slice(splitIndex + 2);
      const parsed = parseSseEvent(rawEvent);
      if (!parsed) continue;
      handlers.onEvent(parsed.event, parsed.data);
      if (parsed.event === "done") {
        doneSeen = true;
      }
    }
  }

  if (!doneSeen) {
    handlers.onError("Stream ended before `done` event.");
  }
  handlers.onDone();
}

function parseSseEvent(raw: string): { event: EventType; data: any } | null {
  if (!raw.trim()) return null;
  const lines = raw.split(/\r?\n/);
  let event = "message";
  const dataLines: string[] = [];

  for (const line of lines) {
    if (line.startsWith("event:")) {
      event = line.slice("event:".length).trim();
      continue;
    }
    if (line.startsWith("data:")) {
      dataLines.push(line.slice("data:".length).trim());
    }
  }

  try {
    return {
      event: event as EventType,
      data: JSON.parse(dataLines.join("\n") || "{}")
    };
  } catch {
    return null;
  }
}
