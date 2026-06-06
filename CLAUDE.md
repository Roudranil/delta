# CLAUDE.md

Delta is a personal research agent for literature survey, paper discovery, and synthesis. Two real users: a Physics PhD student (primary) and her boyfriend (developer). Not a product — build for them.

---

## What it does

Two modes, both in the same session-based interface:

- **EXPLORE** — conversational ReAct loop. Full tool access. Responds in seconds. Inline markdown with citations.
- **DEEP** — structured pipeline: clarify -> plan -> fan-out researchers -> synthesize. 10–20 min. Produces a stored MDX artifact (literature review).

A session has a fixed mode. After a DEEP run completes, follow-up questions run as EXPLORE automatically.

---

## Stack

**Backend** (`server/`) — Python, deployed to Render
- FastAPI + uvicorn — HTTP + SSE
- LangGraph — agent graphs (`AsyncPostgresSaver` checkpointer)
- LiteLLM via `langchain_litellm.ChatLiteLLM` — model gateway with fallback chains
- Neon (Postgres + pgvector) — all persistent state
- Upstash Redis — session locks, visited-papers set (2 keys per run)
- Cloudflare R2 — raw PDFs, JSONL archives, MDX artifacts
- LangMem + `AsyncPostgresStore` — cross-session memory
- LangFuse — LLM call tracing and cost tracking
- Clerk — auth (JWT verification only, no custom auth)
- Loguru — structured JSON logging

**Frontend** (`web/`) — React + Vite + TypeScript, deployed to Cloudflare Pages

---

## Folder layout

```
server/app/
  api/          # HTTP layer only — validates, calls graph or db, returns
  graphs/       # LangGraph graph definitions + nodes/
  tools/        # What the graph calls — orchestrates sources, caching, fallbacks
  sources/      # Thin API clients (S2, OpenAlex, arXiv, Exa, Tavily)
  extract/      # PDF/HTML -> clean text -> chunks -> embeddings
  db/           # All Postgres/Redis/R2 clients live here (migrations/ inside)
  models/       # Pydantic: domain models, API contracts, LLM output schemas
  lib/          # llm.py (single LLM entry), cache.py, memory.py, logging setup

web/src/
  pages/        # Route components (no direct API calls)
  components/   # Stateless UI components
  hooks/        # All API interactions (useSession, useStream, etc.)
  api/          # Typed fetch wrappers — called only by hooks/
  store/        # Zustand — minimal client state
  types/        # Mirrors backend Pydantic models
```

**Key invariants:**
- `graphs/` never imports from `sources/` directly — always via `tools/`
- `lib/llm.py` is the only file that calls LiteLLM — nothing else does
- `db/` is the only place storage clients are imported
- Nodes write messages to Postgres directly as they run (not buffered)

### Python

Python packages and dependencies are listed in `./server/pyproject.toml` and not global level `pyproject.toml`.

---

## Models

All open-weight. No OpenAI, no Anthropic, no Gemini.

| Tier | Primary | Fallback 1 | Fallback 2 |
|------|---------|-----------|-----------|
| `main` | DeepSeek-V3 (Together) | Qwen2.5 72B (Nebius) | deepseek-chat (direct) |
| `fast` | Qwen2.5 72B (Nebius) | Llama 3.3 70B (HF) | deepseek-chat (direct) |
| `reasoning` | DeepSeek-R1 (Nebius) | DeepSeek-V3 (Together) | deepseek-reasoner (direct) |
| `cheap` | Mistral Small 3.2 (HF) | Qwen3 30B (Together) | deepseek-chat (direct) |

Nodes use `get_model("main")` — never call LiteLLM directly. DeepSeek-R1 (`synthesize` node) has no system prompt support — all instructions go in the user message.

Embeddings: `BAAI/bge-m3` via Novita ($0.01/1M tokens). All `vector()` columns are 1024-dim.

---

## Data model highlights

- **`papers` / `paper_chunks`** — global dedup. `user_papers` junction owns per-user library membership.
- **`paper_chunks`** — stores `char_start`/`char_end` offsets into R2 files. No raw text in Neon.
- **`messages`** — canonical message store (not LangGraph checkpoints). Written from inside nodes with idempotent upsert. Links to LangGraph via `checkpoint_id` (no FK — LangGraph blobs are opaque BYTEA).
- **`runs`** — tracks DEEP run lifecycle and phase.
- **`semantic_cache`** — DIY pgvector cache (cosine > 0.95). Caches synthesis/planning LLM calls.
- **`claims` + `claim_relationships`** — V1 contradiction detection via pgvector similarity range (0.6–0.85).
- **LangMem `AsyncPostgresStore`** — replaces `users.memory TEXT`. Namespaced per user.

R2 layout: `{user_id}/papers/{paper_id}/full.pdf`, `artifacts/{session_id}/{run_id}/report.mdx`, `archives/{session_id}/{run_id}.jsonl`

---

## API surface

REST + SSE, all under `/api/v1`. Auth: `Authorization: Bearer <clerk_jwt>`.

Key patterns:
- **EXPLORE**: `POST /sessions/{id}/chat` -> `text/event-stream`. Events: `token`, `tool_call`, `tool_result`, `message_saved`, `done`, `error`, `budget_exceeded`, `timeout`.
- **DEEP**: `POST /sessions/{id}/runs` (starts background task, returns `run_id`) -> poll `GET /sessions/{id}/runs/{run_id}` every 5s -> if `status=interrupted`, submit answers via `POST /runs/{id}/resume`.
- **Branching**: `POST /sessions/{id}/branch` creates a new session with messages reconstructed up to the branch point.

Full spec: `docs/01-planning/v1-sds/11-api-spec.md`

---

## Error handling

- Tools return `ToolResult(status='success'|'partial'|'failed'|'budget_exceeded')` — never raise
- API errors always: `{"error": {"code": "SCREAMING_SNAKE", "message": "...", "request_id": "..."}}`
- LangGraph node failures: retry 2× on external-API nodes, then `runs.status='failed'` + SSE `error` event
- No silent swallowing of: node exceptions after retries, Postgres write failures for `messages`/`runs`/`usage_events`, JWT failures

---

## Planning docs

All architecture decisions are written before code. Read these before touching the relevant area:

| Doc | What it covers |
|-----|---------------|
| `docs/01-planning/v1-sds/01-hosting-infra.md` | Render, Neon, R2, Upstash, Cloudflare Pages, Clerk |
| `docs/01-planning/v1-sds/02-paper-apis.md` | S2, OpenAlex, arXiv — paper fetch waterfall |
| `docs/01-planning/v1-sds/04-web-search.md` | Exa -> Tavily fallback |
| `docs/01-planning/v1-sds/07-data-model.md` | Full Postgres schema, R2 structure, Redis keys |
| `docs/01-planning/v1-sds/08-agent-graph.md` | EXPLORE + DEEP graph design, state shapes, SSE contract |
| `docs/01-planning/v1-sds/09-llm-model-config.md` | Model tiers, `get_model()`, cost estimates |
| `docs/01-planning/v1-sds/10-memory-caching.md` | Semantic cache, LangMem, contradiction detection |
| `docs/01-planning/v1-sds/11-api-spec.md` | Every endpoint, request/response shapes, SSE events |
| `docs/01-planning/v1-sds/12-error-handling.md` | ToolResult, logging, retry policy, SSE error events |
| `docs/01-planning/v1-sds/13-local-dev.md` | Docker Compose stack, env files, auth bypass, Redis abstraction |
| `docs/01-planning/v1-sds/14-frontend.md` | React + TS stack, library choices, folder structure, Vite proxy setup |

---

## Constraints

- Two users. Never over-engineer for scale.
- Budget ~$2–3/month on LLMs after free credits.
- No abstractions until there's a concrete reason.
- If a feature doesn't make research easier, defer it.
- Do not add V2 features (FalkorDB graph, Blaxel batch jobs, knowledge graph visualisation) until V1 is in production.
- Do not read `.env.dev`, `.env.prod`. Read `.env.local` only if you want to know the keys for the variables.
- Always surface unknown unknowns and caveats to the user proactively. Let them proactively know of alternatives, consider different use cases, consider proactive looking at documentation to suggest recommendations with foresight
