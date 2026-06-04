# SDS-12: Error Handling Conventions

## Core principle

Errors are typed return values, not exceptions — except at system boundaries. Inside the agent, a failed tool call is a `ToolResult(status='failed', ...)` that the LLM sees and reasons about. Outside the agent, unhandled exceptions become structured JSON error responses to the client.

---

## Tool errors — typed results, never raise

Every tool returns a `ToolResult`. The agent sees the status and decides what to do next.

```python
@dataclass
class ToolResult:
    status: Literal['success', 'partial', 'failed', 'budget_exceeded']
    data: Any           # populated on success/partial
    message: str        # human-readable, shown to agent in ToolMessage
    source: str         # which API/step produced this result
```

| Status | Meaning | Agent behaviour |
|--------|---------|-----------------|
| `success` | Full result returned | Use it |
| `partial` | Got something but not everything (e.g. abstract only, no full text) | Use what's there, note the limitation |
| `failed` | Nothing returned — API error, timeout, no results | Try a different approach or proceed without |
| `budget_exceeded` | Hit run cap before executing | Stop fetching, synthesise what's already collected |

No tool ever raises. If an API call throws, the tool catches it, logs it, returns `failed`.

---

## API errors — structured JSON, always

Every error response from FastAPI follows the same shape:

```json
{
  "error": {
    "code": "SESSION_NOT_FOUND",
    "message": "Session abc123 does not exist",
    "request_id": "req_xyz"
  }
}
```

Error codes are SCREAMING_SNAKE_CASE strings. HTTP status codes map to categories:

| HTTP | Category | Example codes |
|------|----------|---------------|
| 400 | Bad request | `INVALID_MODE`, `MISSING_FIELD` |
| 401 | Auth failed | `INVALID_JWT`, `JWT_EXPIRED` |
| 403 | Forbidden | `SESSION_NOT_OWNED` |
| 404 | Not found | `SESSION_NOT_FOUND`, `PAPER_NOT_FOUND` |
| 409 | Conflict | `RUN_ALREADY_ACTIVE`, `DEEP_LOCK_HELD` |
| 422 | Validation | `INVALID_REQUEST_BODY` (FastAPI default) |
| 429 | Rate limit | `USER_RATE_LIMITED` |
| 500 | Server error | `INTERNAL_ERROR` |
| 503 | Unavailable | `GRAPH_UNAVAILABLE` |

Every request gets a `request_id` (UUID generated at middleware). Logged on every request, returned in every error response. Use this to find the full trace in logs.

---

## Node errors — log, update run status, surface to user

If a LangGraph node raises an unhandled exception:

1. LangGraph's retry policy re-runs the node (configure `retry=RetryPolicy(max_attempts=2)` on nodes that call external APIs)
2. If retry exhausted: exception propagates out of the graph runner
3. Graph runner catches it → `runs.status = 'failed'`, logs full traceback with `run_id`
4. SSE stream receives: `{"type": "error", "code": "RUN_FAILED", "message": "..."}`
5. Frontend renders error state + retry button

Nodes that should have retry: `clarify`, `plan`, `synthesize`, `compress_findings`, `researcher_agent`, `agent` (EXPLORE). These call LLMs or external APIs.

Nodes that should NOT retry: `load_context`, `load_context_deep`, `write_output`, `finalize`. These write to Postgres — idempotency is handled by upsert logic, not retry.

---

## SSE error events

Three SSE event types that indicate problems:

```
event: error
data: {"type": "error", "code": "RUN_FAILED", "message": "The research run failed. You can retry."}

event: error
data: {"type": "budget_exceeded", "message": "Paper fetch limit reached. Synthesising with collected results."}

event: error
data: {"type": "timeout", "message": "Research is taking longer than expected. Partial results will be saved."}
```

The frontend must handle all three. Never leave the user on a spinner — every terminal state has an explicit event.

---

## Logging conventions

Every log line is structured JSON (via loguru JSON sink). Four fields always present:

```json
{
  "level": "INFO",
  "message": "...",
  "user_id": "user_2abc",
  "session_id": "uuid",
  "run_id": "uuid",
  "timestamp": "2026-06-02T10:00:00Z"
}
```

Additional fields added per context:
- Tool calls: `tool_name`, `tool_status`, `duration_ms`
- API calls to external services: `api_name`, `status_code`, `duration_ms`
- LLM calls: `model`, `input_tokens`, `output_tokens`, `cost_usd` (via LangFuse callback — not logged manually)
- Errors: `error_code`, `traceback`

Log levels:
- `DEBUG` — tool call details, API responses (development only)
- `INFO` — node entry/exit, run start/end, cache hits
- `WARNING` — partial results, budget approaching limit, API fallbacks triggered
- `ERROR` — node failure, external API down, unhandled exception

---

## External API failure hierarchy

When an external API fails inside a tool, the waterfall handles it silently. What gets logged vs surfaced:

| Failure | Logged | Surfaced to agent | Surfaced to user |
|---------|--------|-------------------|------------------|
| S2 API 429 (rate limit) | WARNING | No — semaphore prevents this | No |
| S2 API 5xx | WARNING | Via `partial`/`failed` ToolResult | No |
| Full waterfall exhausted (all sources failed) | ERROR | `failed` ToolResult with message | Only if agent decides to mention it |
| Exa down, Tavily fallback succeeds | INFO | No — transparent | No |
| Both web search APIs down | ERROR | `failed` ToolResult | Agent mentions it |
| Neon connection lost | ERROR | Raises — node retries | `RUN_FAILED` via SSE |
| R2 write failed (non-critical) | ERROR | Logged, run continues | No |
| R2 write failed (artifact save) | ERROR | Raises — node retries once | `RUN_FAILED` if retry fails |

R2 failures for non-critical writes (JSONL archive, memory update) are logged but swallowed — the run is not failed over them. The artifact save is critical — retry once, then fail the run.

---

## What is never swallowed silently

- Any exception in a graph node that exhausts retries
- Any Postgres write failure for a `messages`, `runs`, or `usage_events` row
- Any Sentry-reported exception (Sentry captures everything at `ERROR` level automatically)
- Any JWT verification failure
