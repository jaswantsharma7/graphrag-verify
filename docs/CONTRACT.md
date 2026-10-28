# Implementation contracts

Application root: `graphrag-verify`. Python package: `backend/app`; run backend commands in `backend` after installing the root project with pip.

## Shared configuration and models (T1)

`Settings` uses pydantic-settings, loads root `.env`, ignores unknown variables, and has uppercase environment names for lowercase Python attributes.

Required attributes: `app_mode` (`demo` by default, or `live`), `data_dir` (absolute root `data` default), `api_key` (optional), `cors_origins` (comma-separated), `max_upload_mb` (20), `max_document_chars` (500000), `chunk_size` (512), `chunk_overlap` (64), `neo4j_uri`, `neo4j_username`, `neo4j_password`, `neo4j_database`, `llm_base_url`, `llm_api_key`, `extraction_model`, `generation_model`, `evaluation_model`, `embedding_base_url`, `embedding_api_key`, `embedding_model`, `embedding_dimensions`, `request_timeout_seconds`, `max_context_chars` (24000), `max_chunks_per_document` (1000). Never return credentials through health or settings routes.

Shared pydantic models in `app.models`:

- `Chunk`: `id`, `document_id`, `document_name`, `text`, `page` (int or null), `position` (int), `embedding` (list of floats, default empty).
- `Evidence`: `id` (chunk id), `document_id`, `document_name`, `page` (int or null), `text`, `score` (float), `retrieval` (`vector`, `graph`, or `lexical`), `entity_ids` (list of strings).
- `Claim`: `text`, `status` (`supported`, `unsupported`, or `contradicted`), `citations` (list of evidence ids), `reason`.
- `GraphNode`: `id`, `label`, `kind`; `GraphEdge`: `id`, `source`, `target`, `label`, `evidence_ids` (list).
- `GraphView`: `nodes` (list), `edges` (list).
- `QueryRequest`: `question` (trimmed, 1..2000 chars), `document_ids` (list, default empty meaning all indexed documents), `top_k` (2..20, default 6), `hops` (1..3, default 2).
- `QueryResult`: `id`, `question`, `answer`, `draft`, `status` (`grounded`, `partial`, `ungrounded`, `no_evidence`), `score` (0..1), `claims` (list), `evidence` (list), `graph` (`GraphView`), `latency_ms`, `mode` (`demo` or `live`).

## Pipeline interface (T1)

`Pipeline(settings: Settings)` exposes async methods:

- `initialize() -> None`: creates schema and validates live configuration, but does not seed data.
- `ingest(document_id: str, name: str, chunks: list[Chunk]) -> dict`: persists embeddings, entity/relation extraction, and source links; returns `chunks`, `entities`, `relationships` integer counts. Must replace the same document id idempotently and not expose half-ingested documents to retrieval.
- `ask(request: QueryRequest) -> QueryResult`: semantic plus scoped graph retrieval, generation, independent claim evaluation, citation validation, safe answer gating. Empty evidence abstains. Demo mode is openly labeled, deterministic and extractive, not a simulated LLM.
- `delete(document_id: str) -> None`: removes document and orphan graph nodes, idempotently.
- `graph(document_ids: list[str] | None = None, limit: int = 100) -> GraphView`: capped scoped graph with chunk-level provenance.
- `stats() -> dict`: counts `chunks`, `entities`, `relationships`.
- `close() -> None`.

## API contract (T2)

All paths below are under `/api`, except `/healthz` (unauthenticated liveness). Optional `X-API-Key` protects all `/api` routes. Return `{"detail": "Readable error"}` errors without credentials or internal traces.

- `GET /health` -> `{status: "ok"|"degraded", mode, version, pipeline_ready: bool, message: str, stats: {documents, chunks, entities, relationships}}`.
- `GET /documents` -> `{documents: Document[]}`. Document: `{id, name, status: "queued"|"processing"|"ready"|"failed", created_at, chunks: int, entities: int, relationships: int, error: str|null, job_id: str|null}`.
- `POST /documents` multipart `file`, optional `name` -> `{document: Document, job: Job}` with HTTP 202. Supports PDF, TXT, MD, DOCX. Hash-based duplicate detection returns the existing record/job without another ingestion. Never seed uploaded report automatically.
- `POST /documents/text` -> body `{name: str, text: str}` -> same 202 response; validate empty, oversized and invalid names.
- `DELETE /documents/{id}` -> 204; a missing id returns 404; active ingestion returns 409. Remove chunks and graph provenance too.
- `POST /documents/{id}/retry` -> 202 `{document, job}`; only failed documents.
- `GET /jobs/{id}` -> Job: `{id, document_id, status: "queued"|"processing"|"completed"|"failed", stage: str, progress: float (0..1), error: str|null, created_at, updated_at}`.
- `POST /demo/seed` -> `{documents: Document[], jobs: Job[]}`; ingests public fixture files from root `samples/`; explicit action only and idempotent.
- `POST /query` -> `QueryRequest` -> `QueryResult`; reject filters containing missing or non-ready documents.
- `GET /queries?limit=20` -> `{queries: QueryResult[]}` newest first, capped to 100.
- `GET /graph?limit=100&document_ids=id1,id2` -> `GraphView`.

Serve built `frontend/dist` at `/` if present, with SPA fallback that never masks `/api` 404s. Vite proxies `/api` and `/healthz` to `http://127.0.0.1:8000` for development.

## Durable jobs (T2)

Use a SQLite metadata repository in the configured data directory and one managed async ingestion worker per process. Persist before enqueueing, claim jobs atomically, mark interrupted jobs failed on restart with an actionable retry message. Only one Uvicorn worker is supported. No untracked fire-and-forget tasks. Files have generated storage names outside static assets; validate upload size while streaming. Source bytes and metadata never reach logs. Store results in query history. Clean uploaded bytes when documents are removed.

## UI (T3)

React + TypeScript + Vite, minimal bright blue/white, responsive and accessible. Three screens: Ask, Documents, Knowledge graph; health/mode indicators. Upload file or paste text, seed fixtures explicitly, poll jobs, retry failures and confirm deletions. Question flow shows safe answer, status/score as evidence support (not truth probability), claim audit, collapsible draft and sources with document/page; show a scoped graph and history. Optional API key is held in sessionStorage only; never send keys to third parties. Explain demo mode and settings failures. No mock results or synthetic success states. Render arbitrary uploaded text as text, never unsanitized HTML. Include loading, empty, invalid and failure states, and production build scripts.
