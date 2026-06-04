# SDS-11: API Specification

## Base URL & conventions

- Base path: `/api/v1`
- All request bodies: `application/json`
- All response bodies: `application/json` (except SSE endpoints)
- Auth: `Authorization: Bearer <clerk_jwt>` on every request
- Every error follows the shape from SDS-12: `{"error": {"code": "...", "message": "...", "request_id": "..."}}`
- `request_id` is a UUID generated at middleware and returned in every response header: `X-Request-ID`

---

## Auth

Auth is handled entirely by Clerk. The backend verifies JWTs — it does not issue tokens.

```
POST /auth/* — handled by Clerk SDK, not the backend
```

The backend exposes one auth utility endpoint:

### `GET /api/v1/me`

Returns the current user's profile, creating it in Postgres on first call (upsert).

**Response 200:**
```json
{
  "id": "user_2abc",
  "email": "user@example.com",
  "created_at": "2026-06-01T10:00:00Z"
}
```

**Errors:** `401 INVALID_JWT`, `401 JWT_EXPIRED`

---

## Sessions

### `POST /api/v1/sessions`

Create a new session.

**Request:**
```json
{
  "mode": "explore" | "deep",
  "title": "Topological insulators in 2D materials"   // optional, auto-generated if absent
}
```

**Response 201:**
```json
{
  "id": "uuid",
  "mode": "explore",
  "title": "Topological insulators in 2D materials",
  "status": "active",
  "phase": null,
  "created_at": "2026-06-01T10:00:00Z",
  "updated_at": "2026-06-01T10:00:00Z"
}
```

**Errors:** `400 INVALID_MODE`, `400 MISSING_FIELD`

---

### `GET /api/v1/sessions`

List the current user's sessions, newest first.

**Query params:**
- `mode` — filter by `explore` | `deep` (optional)
- `limit` — default 20, max 100
- `cursor` — pagination cursor (session `updated_at` timestamp, ISO8601)

**Response 200:**
```json
{
  "sessions": [
    {
      "id": "uuid",
      "mode": "explore",
      "title": "...",
      "status": "active",
      "phase": null,
      "last_message_at": "2026-06-01T10:05:00Z",
      "created_at": "2026-06-01T10:00:00Z"
    }
  ],
  "next_cursor": "2026-05-31T09:00:00Z" | null
}
```

---

### `GET /api/v1/sessions/{session_id}`

Get a single session with its messages.

**Response 200:**
```json
{
  "id": "uuid",
  "mode": "explore",
  "title": "...",
  "status": "active",
  "phase": null,
  "messages": [
    {
      "id": "uuid",
      "role": "user" | "assistant" | "tool_call" | "tool_result" | "system",
      "content": "...",
      "content_type": "text" | "mdx",
      "parent_id": "uuid" | null,
      "run_id": "uuid" | null,
      "checkpoint_id": "text" | null,
      "source_type": "user_typed" | "user_edited" | "agent_response" | "tool_output" | "system_context",
      "is_uploaded": false,
      "created_at": "2026-06-01T10:00:00Z"
    }
  ],
  "created_at": "2026-06-01T10:00:00Z",
  "updated_at": "2026-06-01T10:00:00Z"
}
```

**Errors:** `404 SESSION_NOT_FOUND`, `403 SESSION_NOT_OWNED`

---

### `PATCH /api/v1/sessions/{session_id}`

Update session title.

**Request:**
```json
{
  "title": "New title"
}
```

**Response 200:** Full session object (same shape as GET, without messages).

**Errors:** `404 SESSION_NOT_FOUND`, `403 SESSION_NOT_OWNED`

---

### `DELETE /api/v1/sessions/{session_id}`

Soft-delete a session (`status = 'archived'`).

**Response 204:** No body.

**Errors:** `404 SESSION_NOT_FOUND`, `403 SESSION_NOT_OWNED`

---

### `POST /api/v1/sessions/{session_id}/branch`

Create a new session branching from a point in this session's message history.

**Request:**
```json
{
  "branch_from_message_id": "uuid"   // the user message to branch from
}
```

**Response 201:**
```json
{
  "id": "uuid",           // new session id
  "branched_from": {
    "session_id": "uuid",
    "message_id": "uuid"
  },
  "mode": "explore",
  "title": "Branch: Topological insulators...",
  "status": "active",
  "created_at": "2026-06-01T10:00:00Z"
}
```

The new session is created and the messages table is populated up to the branch point. The graph in the new session starts fresh — `load_context` reconstructs state from those messages.

**Errors:** `404 SESSION_NOT_FOUND`, `404 MESSAGE_NOT_FOUND`, `403 SESSION_NOT_OWNED`, `409 MESSAGE_NOT_IN_SESSION`

---

## EXPLORE chat

### `POST /api/v1/sessions/{session_id}/chat`

Send a message and receive a streaming response via SSE.

The response is `Content-Type: text/event-stream`. The connection stays open until the agent finishes (or times out). Each SSE event has a `type` field.

**Request:**
```json
{
  "content": "What are the main experimental signatures of topological surface states?",
  "parent_id": "uuid" | null    // for edits: id of the message being replaced
}
```

**If `parent_id` is set:** The backend creates a new branch of the conversation from that message's parent, then sends the new user message. The old thread is preserved in the messages table but the session's active branch advances.

**Response:** `200 text/event-stream`

---

#### SSE event types — EXPLORE

All events are JSON in the `data` field.

**`token`** — a streamed text chunk from the assistant:
```
event: token
data: {"type": "token", "content": "The main experimental"}
```

**`tool_call`** — the agent is invoking a tool:
```
event: tool_call
data: {"type": "tool_call", "tool": "search_papers", "args": {"query": "topological surface states ARPES", "limit": 5}, "call_id": "call_abc"}
```

**`tool_result`** — tool returned a result:
```
event: tool_result
data: {"type": "tool_result", "call_id": "call_abc", "status": "success", "summary": "Found 5 papers. Top result: 'Observation of a Large-Gap...' (2009)"}
```

**`message_saved`** — the completed assistant message has been written to the messages table:
```
event: message_saved
data: {"type": "message_saved", "message_id": "uuid", "run_id": "uuid"}
```

**`done`** — stream is complete, connection will close:
```
event: done
data: {"type": "done"}
```

**`error`** — something went wrong:
```
event: error
data: {"type": "error", "code": "RUN_FAILED", "message": "The agent encountered an error. You can retry."}
```

**`budget_exceeded`** — hit paper/web/time cap:
```
event: budget_exceeded
data: {"type": "budget_exceeded", "message": "Paper fetch limit reached. Synthesising with collected results."}
```

**`timeout`** — run exceeded the hard time cap:
```
event: timeout
data: {"type": "timeout", "message": "Research is taking longer than expected. Partial results will be saved."}
```

The frontend must handle every event type. `done`, `error`, `timeout` are all terminal — the stream will close after any of them. Never leave the user on a spinner.

**Errors (HTTP, before SSE starts):** `404 SESSION_NOT_FOUND`, `403 SESSION_NOT_OWNED`, `400 WRONG_MODE` (session is DEEP), `409 RUN_ALREADY_ACTIVE`

---

## DEEP research

### `POST /api/v1/sessions/{session_id}/runs`

Start a DEEP research run. Returns immediately with a `run_id`. The graph runs in the background via `asyncio.create_task()`.

**Request:**
```json
{
  "content": "Survey the literature on topological insulators in 2D van der Waals heterostructures."
}
```

**Response 202:**
```json
{
  "run_id": "uuid",
  "session_id": "uuid",
  "status": "active",
  "phase": "clarifying",
  "created_at": "2026-06-01T10:00:00Z"
}
```

**Errors:** `404 SESSION_NOT_FOUND`, `403 SESSION_NOT_OWNED`, `400 WRONG_MODE` (session is EXPLORE), `409 RUN_ALREADY_ACTIVE`, `409 DEEP_LOCK_HELD`

---

### `GET /api/v1/sessions/{session_id}/runs/{run_id}`

Poll for run status and progress. The frontend calls this every 5 seconds.

**Response 200:**
```json
{
  "run_id": "uuid",
  "session_id": "uuid",
  "status": "active" | "interrupted" | "complete" | "failed",
  "phase": "clarifying" | "planning" | "researching" | "synthesizing" | "finalizing" | null,
  "progress": {
    "researchers_total": 5,
    "researchers_done": 2,
    "papers_found": 18,
    "message": "Researcher 2/5 complete. Found 18 papers so far."
  },
  "interrupt": null | {
    "type": "clarification",
    "questions": [
      {"id": "q1", "text": "Are you focused on experimental or theoretical work?"},
      {"id": "q2", "text": "Which material systems are most relevant — MoS₂, graphene, or others?"}
    ]
  },
  "artifact_id": null | "uuid",
  "error": null | {"code": "RUN_FAILED", "message": "..."},
  "started_at": "2026-06-01T10:00:00Z",
  "completed_at": null | "2026-06-01T10:20:00Z"
}
```

When `status = "interrupted"`, the `interrupt` field is populated and the frontend should render the clarification form. When `status = "complete"`, `artifact_id` is populated.

**Errors:** `404 RUN_NOT_FOUND`, `403 SESSION_NOT_OWNED`

---

### `POST /api/v1/sessions/{session_id}/runs/{run_id}/resume`

Resume a paused run after human-in-the-loop clarification. Only valid when `run.status = "interrupted"`.

**Request:**
```json
{
  "answers": [
    {"question_id": "q1", "answer": "Both, but especially experimental ARPES results."},
    {"question_id": "q2", "answer": "Primarily MoS₂ and WSe₂."}
  ]
}
```

**Response 200:**
```json
{
  "run_id": "uuid",
  "status": "active",
  "phase": "planning"
}
```

**Errors:** `404 RUN_NOT_FOUND`, `403 SESSION_NOT_OWNED`, `409 RUN_NOT_INTERRUPTED` (run is not waiting for input)

---

### `DELETE /api/v1/sessions/{session_id}/runs/{run_id}`

Cancel an active or interrupted run.

**Response 204:** No body. The background task is cancelled, `runs.status` set to `failed`.

**Errors:** `404 RUN_NOT_FOUND`, `403 SESSION_NOT_OWNED`, `409 RUN_NOT_ACTIVE`

---

## Library

### `GET /api/v1/library/papers`

List the current user's saved papers.

**Query params:**
- `q` — keyword search over titles + abstracts (optional)
- `limit` — default 20, max 100
- `cursor` — pagination cursor

**Response 200:**
```json
{
  "papers": [
    {
      "id": "s2_abc123",
      "title": "Observation of a Large-Gap Topological-Insulator...",
      "authors": ["Zhang H", "Liu C"],
      "year": 2009,
      "abstract_snippet": "We report the observation of...",
      "source": "semantic_scholar",
      "added_at": "2026-06-01T10:05:00Z"
    }
  ],
  "next_cursor": "..." | null,
  "total": 42
}
```

---

### `GET /api/v1/library/papers/{paper_id}`

Get full paper details.

**Response 200:**
```json
{
  "id": "s2_abc123",
  "title": "...",
  "authors": [...],
  "year": 2009,
  "abstract": "Full abstract text...",
  "doi": "10.1126/...",
  "arxiv_id": "0904.1607",
  "s2_id": "...",
  "openalex_id": "...",
  "venue": "Science",
  "citation_count": 4812,
  "has_full_text": true,
  "source": "semantic_scholar",
  "added_at": "2026-06-01T10:05:00Z"
}
```

**Errors:** `404 PAPER_NOT_FOUND`

---

### `GET /api/v1/library/documents`

List user-uploaded documents.

**Response 200:**
```json
{
  "documents": [
    {
      "id": "uuid",
      "filename": "lecture_notes.pdf",
      "title": "QFT Lecture Notes",
      "size_bytes": 204800,
      "status": "ready" | "processing" | "failed",
      "created_at": "2026-06-01T10:00:00Z"
    }
  ]
}
```

---

### `POST /api/v1/library/documents`

Upload a document. Multipart form upload.

**Request:** `Content-Type: multipart/form-data`
- `file` — the file (PDF, TXT, or MD)
- `title` — display title (optional, defaults to filename)

**Response 202:**
```json
{
  "id": "uuid",
  "filename": "lecture_notes.pdf",
  "title": "QFT Lecture Notes",
  "status": "processing",
  "created_at": "2026-06-01T10:00:00Z"
}
```

Processing is async: the file is stored in R2, chunked, and embedded in the background. Poll `GET /library/documents/{id}` for status.

**Errors:** `400 UNSUPPORTED_FILE_TYPE`, `400 FILE_TOO_LARGE` (> 50MB)

---

### `GET /api/v1/library/documents/{document_id}`

Get document status and metadata.

**Response 200:**
```json
{
  "id": "uuid",
  "filename": "lecture_notes.pdf",
  "title": "...",
  "size_bytes": 204800,
  "chunk_count": 48,
  "status": "ready",
  "created_at": "2026-06-01T10:00:00Z"
}
```

**Errors:** `404 DOCUMENT_NOT_FOUND`

---

### `DELETE /api/v1/library/documents/{document_id}`

Delete a document and all its chunks.

**Response 204:** No body.

**Errors:** `404 DOCUMENT_NOT_FOUND`, `403 DOCUMENT_NOT_OWNED`

---

## Artifacts

DEEP runs produce MDX artifacts stored as user_documents in R2.

### `GET /api/v1/artifacts/{artifact_id}`

Get a completed DEEP research artifact.

**Response 200:**
```json
{
  "id": "uuid",
  "session_id": "uuid",
  "run_id": "uuid",
  "title": "Survey: Topological Insulators in 2D van der Waals Heterostructures",
  "content": "# Survey: Topological Insulators...\n\n<PaperCard ...>",
  "content_type": "mdx",
  "word_count": 4200,
  "paper_count": 24,
  "created_at": "2026-06-01T10:20:00Z"
}
```

The `content` field is the full MDX string. The frontend renders this with the MDX component library.

**Errors:** `404 ARTIFACT_NOT_FOUND`, `403 ARTIFACT_NOT_OWNED`

---

## Usage

### `GET /api/v1/usage`

Current user's usage summary. Useful for frontend to show token/cost stats if desired.

**Response 200:**
```json
{
  "period": "2026-06",
  "llm_calls": 142,
  "input_tokens": 284000,
  "output_tokens": 71000,
  "estimated_cost_usd": 0.48,
  "papers_fetched": 203,
  "deep_runs": 4,
  "explore_turns": 89
}
```

---

## Middleware summary

Every request goes through:

1. **Auth middleware** — verifies Clerk JWT, extracts `user_id`, rejects with `401` if invalid
2. **Request ID middleware** — generates UUID, attaches to request context, adds `X-Request-ID` response header
3. **Logging middleware** — structured JSON log on every request/response with `user_id`, `request_id`, method, path, status, duration_ms
4. **Rate limit middleware** — 60 req/min per user (Upstash Redis token bucket). Returns `429 USER_RATE_LIMITED`

CORS is configured for the Cloudflare Pages origin only.

---

## Complete endpoint index

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/v1/me` | Yes | Get/create current user |
| POST | `/api/v1/sessions` | Yes | Create session |
| GET | `/api/v1/sessions` | Yes | List sessions |
| GET | `/api/v1/sessions/{id}` | Yes | Get session + messages |
| PATCH | `/api/v1/sessions/{id}` | Yes | Update title |
| DELETE | `/api/v1/sessions/{id}` | Yes | Archive session |
| POST | `/api/v1/sessions/{id}/branch` | Yes | Branch from message |
| POST | `/api/v1/sessions/{id}/chat` | Yes | EXPLORE: send message (SSE) |
| POST | `/api/v1/sessions/{id}/runs` | Yes | DEEP: start run |
| GET | `/api/v1/sessions/{id}/runs/{run_id}` | Yes | DEEP: poll run status |
| POST | `/api/v1/sessions/{id}/runs/{run_id}/resume` | Yes | DEEP: submit clarification answers |
| DELETE | `/api/v1/sessions/{id}/runs/{run_id}` | Yes | DEEP: cancel run |
| GET | `/api/v1/library/papers` | Yes | List saved papers |
| GET | `/api/v1/library/papers/{paper_id}` | Yes | Get paper details |
| GET | `/api/v1/library/documents` | Yes | List user documents |
| POST | `/api/v1/library/documents` | Yes | Upload document |
| GET | `/api/v1/library/documents/{document_id}` | Yes | Get document status |
| DELETE | `/api/v1/library/documents/{document_id}` | Yes | Delete document |
| GET | `/api/v1/artifacts/{artifact_id}` | Yes | Get DEEP artifact (MDX) |
| GET | `/api/v1/usage` | Yes | Usage summary |
