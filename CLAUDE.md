# CLAUDE.md

Delta is a personal research agent for literature survey, paper discovery, and synthesis. Two real users: a Physics PhD student (primary) and her boyfriend (developer). Not a product — build for them.

## What it does

Two modes, both in the same session-based interface:

- **EXPLORE** — conversational ReAct loop. Full tool access. Responds in seconds. Inline markdown with citations.
- **DEEP** — structured pipeline: clarify -> plan -> fan-out researchers -> synthesize. 10–20 min. Produces a stored MDX artifact (literature review).

A session has a fixed mode. After a DEEP run completes, follow-up questions run as EXPLORE automatically.

## Design philosophy

These principles resolve ambiguous decisions without debate.

- **Minimal by default.** Write the least code that works. No abstractions until there are 3+ concrete cases. If it can be a constant, it's a constant.
- **Deterministic over LLM wherever possible.** Mode selection, dedup, citation assembly, API calls, chunking, ranking — all deterministic. The LLM decides what to search for and what to say. Code decides everything else.
- **Fail visibly, not silently.** A paper not found is flagged `abstract_only`. A broken SSE stream shows an error state, not a spinner forever. She should never wonder if something is working.
- **Everything is logged.** Every node entry/exit, every external API call, every LLM call with token counts, every cache hit and miss. Logs are structured JSON with `user_id`, `session_id`, `run_id` always present.
- **The user never waits for non-critical work.** Title generation, memory updates, summary bullets — run after the response is sent, never on the critical path.
- **Every citation is real.** The LLM references papers by `paper_id` only. Code resolves to full citation. If the ID doesn't exist in the retrieved set, it's dropped. No hallucinated citations.

## Stack

**Backend** (`server/`) — Python, deployed to Render
- FastAPI + uvicorn — HTTP + SSE
- LangGraph — agent graphs (`AsyncPostgresSaver` checkpointer)
- LiteLLM via `langchain_litellm.ChatLiteLLMRouter` — model router with weighted failover
- Neon (Postgres + pgvector) — all persistent state
- Upstash Redis — session locks, visited-papers set (2 keys per run)
- Cloudflare R2 — raw PDFs, JSONL archives, MDX artifacts
- LangMem + `AsyncPostgresStore` — cross-session memory
- LangFuse — LLM call tracing and cost tracking
- Clerk — auth (JWT verification only, no custom auth)
- Loguru — structured JSON logging

**Frontend** (`web/`) — React + Vite + TypeScript, deployed to Cloudflare Pages

## Folder layout

```
server/
├── src/delta/
│   ├── main.py           # entry point — bootstraps config; no HTTP server yet
│   ├── app/
│   │   ├── config.py     # Pydantic settings for all env vars (AppSettings, LLMSettings, etc.)
│   │   ├── db/           # all Postgres/Redis/R2 clients — nothing outside here imports storage
│   │   └── tracing/      # logging setup (Loguru, structured JSON)
│   ├── llm/
│   │   ├── models.py     # single source of truth for model variants, deployments, tier groups
│   │   ├── providers.py  # provider credentials and env-var loading (Nebius today)
│   │   └── runtime.py    # get_model(tier) — process-level singleton router, never call LiteLLM directly
│   ├── schemas/
│   │   └── llm.py        # Pydantic schemas for ModelVariant, ModelDeployment, ModelTier
│   ├── api/              # HTTP layer only — validates, calls graph or db, returns. No business logic.
│   ├── graphs/           # LangGraph graph definitions + nodes/ (one async function per node)
│   └── tools/            # graph-facing tools — orchestrates sources, caching, fallbacks
└── tests/
    ├── unit/             # fast, no network, no DB
    └── integration/      # hits real APIs — run manually, not in CI

web/
└── src/
    ├── pages/            # route components — no direct API calls, uses hooks/
    ├── components/       # stateless UI components
    ├── hooks/            # all API interactions (useSession, useStream, etc.)
    ├── api/              # typed fetch wrappers — called only by hooks/
    ├── store/            # Zustand — minimal client state, server is source of truth
    └── types/            # mirrors backend Pydantic models exactly
```

**What doesn't exist yet** (planned, not built): `sources/` (paper API clients), `extract/` (PDF/HTML → chunks → embeddings), `models/` (domain + API + LLM output schemas), `scripts/` (dev CLI runner).

**Key invariants:**
- `graphs/` never imports from `sources/` directly — always via `tools/`
- `llm/runtime.py` is the only file that calls LiteLLM — nothing else does
- `app/db/` is the only place storage clients are imported
- Nodes write messages to Postgres directly as they run, not buffered

### Python

Packages and dependencies are in `./server/pyproject.toml`, not the root.

## Models

All open-weight. No OpenAI, no Anthropic, no Gemini. Currently all hosted on Nebius.

| Tier | Models | Use |
|------|--------|-----|
| `light` | Nemotron-3 Nano 30B (w=2), Qwen3 30B A3B (w=1) | quick lookups, cheap calls |
| `medium` | Qwen3 235B A22B Thinking | synthesis, analysis, complex reasoning |

Nodes call `get_model("light")` or `get_model("medium")` — the router handles weighted selection, retries, and cooldowns. Never call LiteLLM directly.

Embeddings: `BAAI/bge-m3` via Novita. All `vector()` columns are 1024-dim.

## Planning docs

Read these before touching the relevant area:

| Doc | What it covers |
|-----|---------------|
| `docs/01-planning/v1-sds/01-hosting-infra.md` | Render, Neon, R2, Upstash, Cloudflare Pages, Clerk |
| `docs/01-planning/v1-sds/02-paper-apis.md` | S2, OpenAlex, arXiv — paper fetch waterfall |
| `docs/01-planning/v1-sds/04-web-search.md` | Exa -> Tavily fallback |
| `docs/01-planning/v1-sds/05-design-philosophy.md` | Full design principles |
| `docs/01-planning/v1-sds/06-monorepo-structure.md` | Canonical folder layout and conventions |
| `docs/01-planning/v1-sds/07-data-model.md` | Full Postgres schema, R2 structure, Redis keys |
| `docs/01-planning/v1-sds/08-agent-graph.md` | EXPLORE + DEEP graph design, state shapes, SSE contract |
| `docs/01-planning/v1-sds/09-llm-model-config.md` | Model tiers, `get_model()`, cost estimates |
| `docs/01-planning/v1-sds/10-memory-caching.md` | Semantic cache, LangMem, contradiction detection |
| `docs/01-planning/v1-sds/11-api-spec.md` | Every endpoint, request/response shapes, SSE events |
| `docs/01-planning/v1-sds/12-error-handling.md` | ToolResult, logging, retry policy, SSE error events |
| `docs/01-planning/v1-sds/13-local-dev.md` | Docker Compose stack, env files, auth bypass |
| `docs/01-planning/v1-sds/14-frontend.md` | React + TS stack, library choices, Vite proxy setup |

## Constraints

- Two users. Never over-engineer for scale.
- Budget ~$2–3/month on LLMs after free credits.
- No abstractions until there's a concrete reason.
- If a feature doesn't make research easier, defer it.
- Do not read `.env.dev`, `.env.prod`. Read `.env.local` only if you want to know the keys for the variables.
- Always surface unknown unknowns and caveats proactively. Flag alternatives, edge cases, and documentation gaps before they become problems.
