# SDS-13: Local Development Setup

## Goal

Fully local stack for development and testing. Only LLM calls and web search (Exa/Tavily) hit external APIs. Everything else — Postgres, Redis, file storage — runs on your machine. Switching between local and production requires only changing which `.env` file is active.

---

## Service mapping

| Service | Production | Local |
|---------|-----------|-------|
| FastAPI server | Render | `uvicorn` on localhost — same command |
| Frontend | Cloudflare Pages | `vite dev` — built into Vite |
| Postgres + pgvector | Neon | Docker: `pgvector/pgvector:pg16` |
| Redis | Upstash (HTTP REST) | Docker: `redis:7-alpine` |
| File storage | Cloudflare R2 | Docker: MinIO (S3-compatible) |
| Auth | Clerk | Bypassed — hardcoded `user_id` |
| LangFuse | langfuse.com | Skip locally |
| Sentry | sentry.io | Skip locally |
| UptimeRobot | uptimerobot.com | Irrelevant locally |

---

## docker-compose.yml

Place at repo root. One command starts the full local stack.

```yaml
services:
  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_DB: delta
      POSTGRES_USER: delta
      POSTGRES_PASSWORD: delta
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"

  minio:
    image: minio/minio
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: minioadmin
      MINIO_ROOT_PASSWORD: minioadmin
    ports:
      - "9000:9000"   # S3 API — used as R2_ENDPOINT_URL
      - "9001:9001"   # MinIO web console for inspecting buckets
    volumes:
      - minio_data:/data

volumes:
  postgres_data:
  minio_data:
```

```bash
docker compose up -d      # start everything, data persists in named volumes
docker compose down       # stop (data kept)
docker compose down -v    # stop and wipe all data
```

---

## Environment files

Two env files, never committed to git:

```
.env.local        # local dev
.env.production   # production — Neon, Upstash, R2, Clerk
```

`pydantic-settings` loads whichever is set. Set `APP_ENV=local` or `APP_ENV=production` at the top of each file.

### .env.local

```bash
APP_ENV=local
PORT=8000
LOG_LEVEL=DEBUG
CORS_ORIGINS=http://localhost:5173

# Postgres (local Docker)
DATABASE_URL=postgresql://delta:delta@localhost:5432/delta
DATABASE_URL_DIRECT=postgresql://delta:delta@localhost:5432/delta

# Redis (local Docker)
REDIS_URL=redis://localhost:6379

# MinIO as R2 (local Docker)
R2_ENDPOINT_URL=http://localhost:9000
R2_ACCESS_KEY_ID=minioadmin
R2_SECRET_ACCESS_KEY=minioadmin
R2_BUCKET_NAME=delta

# LLM (still external)
TOGETHER_AI_API_KEY=...
NEBIUS_API_KEY=...
HUGGINGFACE_API_KEY=...
DEEPSEEK_API_KEY=...
NOVITA_API_KEY=...

# Web search (still external)
EXA_API_KEY=...
TAVILY_API_KEY=...

# Leave these empty locally — auth and observability are bypassed
CLERK_SECRET_KEY=
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
SENTRY_DSN=
```

---

## Auth bypass

In production, every request verifies a Clerk JWT. Locally, the middleware short-circuits and injects a hardcoded `user_id`. Routes and graph nodes never know — they only see `request.state.user_id`.

```python
# server/app/api/middleware/auth.py
async def auth_middleware(request: Request, call_next):
    if settings.APP_ENV == "local":
        request.state.user_id = "local_dev_user"
        return await call_next(request)

    # production: verify Clerk JWT
    token = request.headers.get("Authorization", "").removeprefix("Bearer ")
    if not token:
        return JSONResponse(status_code=401, content={"error": {"code": "INVALID_JWT", ...}})
    request.state.user_id = verify_clerk_jwt(token)
    return await call_next(request)
```

---

## Redis client abstraction

Upstash uses HTTP REST (production). Local Redis uses TCP. Wrap both behind one interface in `db/redis.py` so nothing else in the codebase cares which is running.

```python
# server/app/db/redis.py
from app.config import settings

if settings.APP_ENV == "local":
    import redis.asyncio as redis
    redis_client = redis.from_url(settings.REDIS_URL)
else:
    from upstash_redis.asyncio import Redis
    redis_client = Redis(url=settings.UPSTASH_REDIS_REST_URL, token=settings.UPSTASH_REDIS_REST_TOKEN)
```

Everything else calls `from app.db.redis import redis_client` — no conditional logic outside this file.

Add `redis` as a dev dependency (not needed in production):

```bash
uv add --package delta --dev redis
```

---

## MinIO bucket setup

MinIO starts empty. Create the bucket once after first `docker compose up`:

```bash
# Install mc (MinIO client) once
brew install minio/stable/mc

# Point it at your local MinIO
mc alias set local http://localhost:9000 minioadmin minioadmin

# Create the bucket
mc mb local/delta
```

Or use the MinIO web console at `http://localhost:9001` (login: `minioadmin` / `minioadmin`).

The `boto3` code in `db/r2.py` works unchanged — only `endpoint_url` and credentials differ between environments, both set via env vars.

---

## Running the full stack locally

```bash
# 1. Start backing services
docker compose up -d

# 2. Run migrations against local Postgres
uv run alembic upgrade head

# 3. Start the API server
uv run uvicorn server.app.main:app --reload --port 8000

# 4. Start the frontend (separate terminal)
cd web && npm run dev
```

Frontend runs at `http://localhost:5173`, API at `http://localhost:8000`.

---

## What stays external even locally

- **LLM providers** (Together, Nebius, HuggingFace, DeepSeek, Novita) — no local alternative that fits in 512 MB RAM
- **Exa and Tavily** — web search, no local equivalent needed

All other API keys can be left blank or dummy values locally.
