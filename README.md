# Multi-Agent Deep Research System

LangGraph-based multi-agent research assistant with:
- Planner, Researcher, Writer, and Quality nodes
- SSE streaming (`thread_id`, `planning`, `research_progress`, `writing`, `message`, `done`, `error`)
- Threaded in-memory context and shared memory across agents
- Parallel web search router (Exa + Firecrawl, Tavily fallback)

## Architecture Document

- Deliverable file: `docs/Architecture_Doc.md`
- Includes Deliverable 2 and Deliverable 3 sections with Mermaid diagrams.

## Why Exa + Firecrawl + Tavily

- `Exa` is used as a primary provider for high-quality semantic web search with direct content extraction.
- `Firecrawl` runs in parallel with Exa to improve coverage and provide richer page-level content when summaries are thin.
- `Tavily` is fallback-only to preserve resilience if primary providers fail or return no useful results.

This strategy improves:
- source diversity
- reliability under provider/API failures
- cost control (fallback invoked only when needed)

Code locations:
- Router: `app/tools/web_search_router.py`
- Retrieval + selection: `app/agents/researcher.py`
- Budget/config: `app/config.py`

## Shared Memory Model

The system keeps one thread-level memory object so all agents share context:
- `history`: recent user/assistant messages
- `report_memories`: compact snapshots from prior completed runs
- `shared_memory`: normalized cross-agent context (recent messages, recent reports, open gaps)

Flow:
1. `app/services/sse.py` loads thread state and builds `shared_memory`.
2. `planner` uses shared memory for follow-up awareness and possible `SKIP_WEB_RESEARCH`.
3. `writer` uses shared memory + current evidence packet for coherent multi-turn answers.
4. Final state is persisted back to thread store.

## Local Run (without Docker)

Backend:
```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Frontend:
```bash
cd frontend
npm install
npm run dev
```

## Docker Deployment

### Prerequisites
- Docker Desktop
- `.env` file populated at repo root

### Start
```bash
docker compose up --build
```

### Endpoints
- Frontend: `http://localhost:5173`
- Backend API: `http://localhost:8000`
- Health check: `http://localhost:8000/healthz`

### Stop
```bash
docker compose down
```

## Notes
- Frontend build arg `VITE_API_BASE` is set in `docker-compose.yml`.
- For production, replace in-memory thread store with persistent storage (see deliverable section).

## Provider References

- Exa docs: https://exa.ai/docs
- Exa search API: https://exa.ai/docs/reference/search
- Firecrawl search API: https://docs.firecrawl.dev/api-reference/v1-endpoint/search
- Firecrawl search features: https://docs.firecrawl.dev/features/search
- Tavily docs: https://docs.tavily.com/
- Tavily search API: https://docs.tavily.com/documentation/api-reference/endpoint/search
