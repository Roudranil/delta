# SDS-06: Monorepo Structure

One repo, two deployable units: `server/` and `web/`. Deployed independently — Render deploys server, Cloudflare Pages deploys web.

---

```
delta/
│
├── server/                         # Python — deployed to Render
│   ├── app/
│   │   │
│   │   ├── api/                    # HTTP layer only — no business logic
│   │   │                           # One file per route group (sessions, messages,
│   │   │                           # papers, health). Validates requests, calls
│   │   │                           # graph or db, returns responses. Nothing else.
│   │   │
│   │   ├── graphs/                 # LangGraph graph definitions
│   │   │   │                       # Owns the agent loop and node wiring.
│   │   │   │                       # No HTTP concerns, no DB queries directly —
│   │   │   │                       # calls tools and lib/ only.
│   │   │   └── nodes/              # One folder per graph node
│   │   │                           # Each node is a plain async function.
│   │   │                           # Independently testable. Writes messages
│   │   │                           # to Postgres directly via db/postgres.
│   │   │
│   │   ├── tools/                  # Graph-facing tools — what the graph calls
│   │   │                           # Orchestrates sources, handles fallbacks,
│   │   │                           # deduplication, caching, R2 writes.
│   │   │                           # The graph never imports from sources/ directly.
│   │   │
│   │   ├── sources/                # Thin API clients — one folder per external service
│   │   │                           # Each just wraps HTTP calls and returns typed data.
│   │   │                           # No business logic, no fallback logic.
│   │   │                           # Called only by tools/, never by graphs/ directly.
│   │   │
│   │   ├── extract/                # Full text extraction pipeline
│   │   │                           # Converts raw content into clean text.
│   │   │                           # arXiv HTML -> clean text (beautifulsoup4)
│   │   │                           # PDF bytes -> text (pymupdf)
│   │   │                           # Text -> chunks -> embeddings
│   │   │                           # Called by tools/fetch_paper only.
│   │   │
│   │   ├── db/                     # Storage layer
│   │   │   │                       # All reads and writes to Postgres, Redis, R2
│   │   │   │                       # go through this folder. Nothing outside db/
│   │   │   │                       # imports storage clients directly.
│   │   │   └── migrations/         # Alembic migrations
│   │   │                           # Every schema change goes through here.
│   │   │                           # Run against DATABASE_URL_DIRECT (never pooled).
│   │   │                           # Committed to git — full history of every change.
│   │   │
│   │   ├── models/                 # Pydantic schemas
│   │   │                           # Three concerns kept separate:
│   │   │                           # - Domain models (Paper, Session, Message)
│   │   │                           # - API request/response bodies
│   │   │                           # - LLM structured output contracts
│   │   │                           # All LLM output schemas live here so every
│   │   │                           # LLM contract is in one place.
│   │   │
│   │   └── lib/                    # Utilities with no better home
│   │                               # LiteLLM wrapper — every LLM call in the
│   │                               # codebase goes through lib/llm, nowhere else.
│   │                               # Rate limiting semaphores (process-global).
│   │                               # DOI normalisation.
│   │                               # Structured JSON logging setup.
│   │
│   ├── tests/
│   │   ├── unit/                   # Fast, no network, no DB
│   │   │                           # Tests for pure functions: DOI parsing,
│   │   │                           # chunking logic, dedup logic.
│   │   │                           # Must run in CI on every PR.
│   │   │
│   │   └── integration/            # Hits real APIs — run manually or on merge
│   │                               # Tests for source clients and fetch_paper waterfall.
│   │                               # Not in CI by default (API keys, network, cost).
│   │
│   └── scripts/                    # One-off CLI utilities
│                                   # CLI test runner — invoke graph without API or UI.
│                                   # DB seed script — create test users and sessions.
│                                   # Not deployed, not imported by app code.
│
├── web/                            # React + Vite SPA — deployed to Cloudflare Pages
│   └── src/
│       │
│       ├── pages/                  # Top-level route components
│       │                           # One file per page: Chat, Login, Usage.
│       │                           # Owns layout and data orchestration for that route.
│       │                           # No direct API calls — uses hooks/.
│       │
│       ├── components/             # Reusable UI components
│       │                           # Stateless where possible.
│       │                           # Each component owns its own rendering logic.
│       │                           # No business logic, no API calls.
│       │
│       ├── hooks/                  # Data fetching and stateful logic
│       │                           # All API interactions go through hooks.
│       │                           # useSession, useStream (SSE), useUsage.
│       │                           # Pages call hooks, never api/ directly.
│       │
│       ├── api/                    # Typed fetch wrappers
│       │                           # One file — typed wrappers for every backend endpoint.
│       │                           # Handles auth headers (Clerk JWT), base URL,
│       │                           # error parsing. Called only by hooks/.
│       │
│       ├── store/                  # Client-side state (Zustand)
│       │                           # Active session, message list, UI state.
│       │                           # Kept minimal — server is source of truth.
│       │
│       └── types/                  # TypeScript types
│                                   # Mirrors backend Pydantic models exactly.
│                                   # Single source of type truth for the frontend.
│                                   # Updated whenever backend models change.
│
├── docs/                           # Planning and reference documentation
│   ├── 01-planning/                # SDS docs and PRD — written before code
│   └── report/                     # Research outputs (not app code)
│
├── .github/
│   └── workflows/                  # CI pipelines
│                                   # backend.yml — ruff + mypy + unit tests on PR
│                                   # frontend.yml — tsc + vitest on PR
│
├── .gitignore
└── README.md
```

---

## Key conventions

**`sources/` vs `tools/`**
`sources/` = thin HTTP wrappers, one per external service, typed return values, no logic.
`tools/` = what the graph calls. Orchestrates sources, handles fallbacks, caching, dedup. The graph never reaches into `sources/` directly.

**`graphs/nodes/` — one folder per node**
Each node is a plain async function. Writes messages to Postgres directly as it runs — not buffered. Independently testable.

**`lib/llm` — single LLM entry point**
Every `acompletion` call in the codebase goes through here. Handles retry, cost tracking (writes `usage_events`), structured output parsing, LangFuse callback injection. Nothing calls LiteLLM directly except this file.

**`db/` — single storage entry point**
All Postgres, Redis, and R2 clients live here. Nothing outside `db/` imports `asyncpg`, `upstash_redis`, or `boto3` directly.

**`models/` — three concerns, kept separate**
Domain models, API contracts, LLM output schemas. All LLM structured output contracts (research brief, subtopic list, relevance score, session title) live in one place so every prompt/schema pair is findable together.

**`scripts/` — not app code**
CLI runner invokes the graph directly with a hardcoded query. Used for development without needing the API or frontend running. Never imported by app code.
