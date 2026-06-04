# SDS-01: Hosting & Infrastructure — LOCKED

Free-tier stack for 2–5 users. Every service has no time-limited expiry and no egress cost surprise.

---

## Full Stack

| Component | Service | Free Tier Limit |
|-----------|---------|-----------------|
| App server (FastAPI) | Render free web service | 512 MB RAM, 0.1 vCPU, cold starts, ephemeral disk |
| Frontend (React + Vite SPA) | Cloudflare Pages | Unlimited requests, global CDN, no cold starts |
| Auth (login, sessions, Google OAuth) | Clerk free | 50K MRU, Google + email/password, 7-day session max |
| Postgres (sessions, metadata, vectors) | Neon free | 0.5 GB storage, 100 CU-hours/month, no expiry |
| File storage (JSONL session archives) | Cloudflare R2 | 10 GB, 1M writes/month, zero egress fees |
| Redis (session cache, rate limiting) | Upstash free | 256 MB, 500K commands/month, persistent |
| Vector search | pgvector on Neon | Included in Neon, no separate service needed |
| CI/CD | GitHub Actions + Render/CF native | Free for public repos; Render + CF Pages deploy on push |
| Error monitoring | Sentry free | 5K errors/month, 5M spans, 30-day retention, 1 seat |
| Uptime / cold start prevention | UptimeRobot free | Pings `/health` every 5 min to keep Render warm |
| LLM observability | LangFuse hosted free | 50K events/month, 30-day retention, 2 users |

---

## Interaction Diagram

```mermaid
graph TD
    Browser["Browser\n(React + Vite SPA)"]
    CF_Pages["Cloudflare Pages\n(static file host)"]
    Clerk_UI["Clerk\n(auth UI + session cookie)"]
    Render["Render\n(FastAPI + uvicorn)"]
    Neon["Neon Postgres\n(sessions, metadata,\nvectors via pgvector,\nLangGraph checkpoints)"]
    R2["Cloudflare R2\n(JSONL session archives)"]
    Redis["Upstash Redis\n(active session state,\nvisited_paper_ids,\nrate limit counters)"]
    LiteLLM["LiteLLM\n→ DeepSeek API\n(+ fallback model)"]
    PaperAPIs["Paper APIs\n(Semantic Scholar,\nOpenAlex, Unpaywall,\narXiv, CORE, Crossref)"]
    Sentry["Sentry\n(error monitoring)"]
    UptimeRobot["UptimeRobot\n(health pings)"]
    GitHub["GitHub Actions\n(CI/CD)"]

    CF_Pages -->|"serves HTML/JS/CSS"| Browser
    Browser -->|"login flow"| Clerk_UI
    Clerk_UI -->|"JWT on auth"| Browser
    Browser -->|"HTTPS + JWT\n(every API call)"| Render
    Render -->|"verify JWT"| Clerk_UI
    Render -->|"asyncpg\n(pgBouncer endpoint)"| Neon
    Render -->|"boto3\n(S3-compatible)"| R2
    Render -->|"upstash-redis\n(HTTP REST)"| Redis
    Render -->|"acompletion"| LiteLLM
    Render -->|"httpx async"| PaperAPIs
    Render -->|"SSE stream"| Browser
    Render -->|"exceptions"| Sentry
    UptimeRobot -->|"GET /health\nevery 5 min"| Render
    GitHub -->|"push to main\n→ auto deploy"| Render
    GitHub -->|"push to main\n→ auto deploy"| CF_Pages
```

---

## Service Details

### Render (App Server)

- 512 MB RAM, 0.1 vCPU
- **Ephemeral disk** — no persistent local storage. All files go to R2, never local.
- **Cold starts** — spins down after inactivity, ~30s to wake. Frontend loads instantly from CF Pages so she sees the UI; cold start only hits on first API call.
- 5 GB bandwidth/month free, $0.15/GB after
- **What NOT to use:** Render Postgres (deleted at 90 days), Render Key Value (25 MB)

### Cloudflare Pages (Frontend)

- Serves the React + Vite static bundle
- No cold start — files served from global CDN edge
- Custom domain free, auto-deploy on git push
- "Static" refers to how files are served, not how the app behaves — the SPA is fully dynamic via API calls to Render

### Clerk (Auth)

- Handles login UI, password storage, Google OAuth, session cookies
- Passwords never touch your infrastructure — Clerk owns all auth data
- Your DB stores only `clerk_user_id` as a foreign key
- 7-day session max on free tier — users re-authenticate weekly
- Invite-only: disable public signup in Clerk dashboard (toggle, no code)
- Branding on login page on free tier — acceptable for a personal tool

### Neon (Postgres)

- **Storage (0.5 GB)** and **compute (100 CU-hours/month)** are independent
  - Storage: data sitting on disk — rows, indexes, embeddings. Always there.
  - Compute: Postgres process running and executing queries. Suspends after 5 min idle.
  - At 0.25 CU minimum: 100 CU-hours = 400 hours of active query time per month. Fine for 2 users.
- pgBouncer pooling included — use the pooled connection string, not direct
- pgvector included — `CREATE EXTENSION vector;` is all you need
- **No expiry** — data persists indefinitely
- LangGraph `AsyncPostgresSaver` checkpoints land here — run periodic cleanup to prevent storage bloat

### Cloudflare R2 (File Storage)

- 10 GB storage, zero egress fees — key differentiator vs S3
- Stores JSONL session archives: `sessions/{session_id}.jsonl`
- R2 has no append operation — buffer writes in Redis, flush to R2 on session end
- Access via `boto3` with `endpoint_url=https://{account_id}.r2.cloudflarestorage.com`

### Upstash Redis (Cache)

- Active session state (60-min TTL per session)
- `visited_paper_ids` dedup set per research run
- API rate limiting counters (Semantic Scholar: 1 req/sec)
- R2 write buffer (flush on session end)
- Use `upstash-redis` Python client (HTTP REST), not `redis-py` TCP client

### pgvector on Neon (Vector Search)

- No separate vector DB needed at this scale (10K papers ≈ 60 MB at 1,536 dims)
- Two embedding types in separate columns:
  - `specter_embedding vector(768)` — from Semantic Scholar, paper-to-paper similarity
  - `content_embedding vector(1536)` — your own embeddings of extracted chunks, query-to-chunk search
- HNSW index for fast ANN search

### Sentry (Error Monitoring)

- 5K errors/month — sufficient for low-traffic personal tool
- 1 seat (you only) — fine, she doesn't need dashboard access
- 30-day retention
- Add `sentry-sdk[fastapi]` to backend, `@sentry/react` to frontend

### UptimeRobot (Warm Pings)

- Free: 50 monitors, 5-minute check interval
- Single monitor on `GET https://delta-api.onrender.com/health`
- Prevents Render cold starts during active use hours
- Also alerts you by email if the service goes down

---

## What's Explicitly Out of Scope

| Feature | Why excluded |
|---------|--------------|
| Separate vector DB (Qdrant, Pinecone) | pgvector on Neon handles this scale |
| Cloudflare Workers | 10ms CPU limit too tight for server logic |
| Cloudflare D1 | Neon is better; D1 would be redundant |
| Email notifications | SSE streaming means she watches it live |
| Automated billing | Manual invoicing via usage_events table |
| Render Postgres | Deleted at 90 days — not usable |
| Render Key Value | 25 MB — useless |

---

## Environment Variables

Two separate places to set these:
- **Render dashboard** → Environment → Secret Files or Environment Variables (backend)
- **Cloudflare Pages dashboard** → Settings → Environment Variables (frontend, injected at build time)

Do not commit any of these to git. Use `.env` locally (gitignored).

```bash
# ============================================================
# BACKEND — set in Render dashboard
# ============================================================

# --- App ---
APP_ENV=production                # "production" | "development"
PORT=8000                         # Render sets this automatically; keep as fallback
LOG_LEVEL=INFO                    # "DEBUG" | "INFO" | "WARNING" | "ERROR"
CORS_ORIGINS=https://delta.pages.dev,https://yourdomain.com
                                  # comma-separated; must match frontend domain exactly

# --- Neon Postgres ---
# Always use the pgBouncer (pooled) endpoint — never the direct endpoint
# Direct endpoint has limited slots; pgBouncer handles concurrency
DATABASE_URL=postgresql://user:pass@ep-xxx.pooler.neon.tech/neondb?sslmode=require
# Direct endpoint — only for running migrations (Alembic needs it; pgBouncer breaks DDL)
DATABASE_URL_DIRECT=postgresql://user:pass@ep-xxx.neon.tech/neondb?sslmode=require

# --- Cloudflare R2 ---
R2_ACCOUNT_ID=...                 # found in Cloudflare dashboard → R2 overview
R2_ACCESS_KEY_ID=...              # R2 API token → Access Key ID
R2_SECRET_ACCESS_KEY=...          # R2 API token → Secret Access Key
R2_BUCKET_NAME=delta-sessions
R2_ENDPOINT_URL=https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com
                                  # boto3 needs this as endpoint_url

# --- Upstash Redis ---
UPSTASH_REDIS_REST_URL=https://xxx.upstash.io
UPSTASH_REDIS_REST_TOKEN=...      # from Upstash dashboard → REST API section
# Session state TTL (seconds)
REDIS_SESSION_TTL=3600            # 60 minutes

# --- Clerk (backend JWT verification) ---
CLERK_SECRET_KEY=sk_live_...      # never expose this; backend only
CLERK_PUBLISHABLE_KEY=pk_live_... # needed by backend to resolve JWKS endpoint

# --- Sentry (backend) ---
SENTRY_DSN=https://...@sentry.io/...
SENTRY_ENVIRONMENT=production     # "production" | "development"
SENTRY_TRACES_SAMPLE_RATE=0.1    # 10% of requests traced; keep low on free tier

# --- LangFuse (LLM observability) ---
LANGFUSE_PUBLIC_KEY=...
LANGFUSE_SECRET_KEY=...
LANGFUSE_HOST=https://cloud.langfuse.com

# ============================================================
# FRONTEND — set in Cloudflare Pages dashboard
# All must be prefixed with VITE_ to be injected into the bundle
# These are embedded in the JS bundle — treat as public, never put secrets here
# ============================================================

VITE_API_URL=https://delta-api.onrender.com
                                  # no trailing slash; all fetch() calls use this as base
VITE_CLERK_PUBLISHABLE_KEY=pk_live_...
                                  # non-secret; safe in browser bundle
VITE_SENTRY_DSN=https://...@sentry.io/...
                                  # can be same Sentry project as backend or separate
VITE_SENTRY_ENVIRONMENT=production

# ============================================================
# LOCAL DEVELOPMENT — .env file at repo root (gitignored)
# Copy all backend vars above, plus these overrides:
# ============================================================

# APP_ENV=development
# DATABASE_URL=postgresql://user:pass@ep-xxx.pooler.neon.tech/neondb?sslmode=require
# CORS_ORIGINS=http://localhost:5173   # Vite dev server default port
# LOG_LEVEL=DEBUG
```
