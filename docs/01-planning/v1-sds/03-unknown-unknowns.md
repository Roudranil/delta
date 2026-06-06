# SDS-03: Unknown Unknowns — What You're Missing

Things not yet accounted for in the current plan. Roughly ordered by how badly they'll bite you.

---

## 1. PDF Extraction Quality Is Unreliable

**Problem:** Most physics papers are two-column LaTeX PDFs with equations, figures, tables, footnotes. Standard PDF extraction (`pdfminer`, `pypdf`) produces garbage output for multi-column layouts — columns get merged, equations become `\delta E = \sum_{}^{}...` or worse. The text you feed the LLM may be unreadable.

**Mitigation options (in cost order):**
- `pymupdf` (fitz) — better column handling than pdfminer; try this first
- `marker` (open source, free) — ML-based PDF-to-markdown, designed for academic papers, handles columns and equations well. Runs on CPU, slow but accurate.
- `mathpix` — best for equations, but $0.004/page (paid)
- `Grobid` — Java service, academic-grade PDF parsing, free self-hosted

**Decision for v1:** Use `pymupdf` first. Add `marker` as a fallback for PDFs where pymupdf yields malformed text. Don't use Mathpix (cost) or Grobid (ops overhead) in v1.

---

## 2. Semantic Scholar Embeddings Are Not Text Embeddings

**Problem:** Semantic Scholar returns SPECTER2 embeddings (768 dims, trained on paper citation graphs). These are good for "find similar papers" but not the same as text embeddings generated from the full paper content. If you store SPECTER2 embeddings from S2 alongside your own `text-embedding-3-small` embeddings, you can't mix them in a single cosine search.

**Decision:** Keep two separate embedding columns or tables:
- `specter_embedding vector(768)` — from Semantic Scholar, used for paper-to-paper similarity ("find me papers like this one")
- `content_embedding vector(1536)` — your own embedding of extracted chunks, used for "which chunks are relevant to this query?"

---

## 3. Neon 0.5 GB Limit Will Get Hit

**Math:**
- 10K papers × 1,536 dims × 4 bytes = ~60 MB (content embeddings)
- 10K papers × 768 dims × 4 bytes = ~30 MB (SPECTER embeddings)
- Session metadata + messages: ~50 MB for heavy use
- **Total: ~140 MB comfortably within 0.5 GB for 6 months**

**Risk:** If you store full paper chunks as text in Postgres (for FTS alongside vector search), the storage explodes. A 10-page paper -> ~20 chunks × ~500 tokens × ~3 bytes = ~30 KB per paper. 10K papers = ~300 MB just in chunk text.

**Decision:** Store chunk text in R2 (as part of the JSONL session file or a separate paper cache file), not in Neon. Neon stores only embeddings + metadata. This keeps Neon within the 0.5 GB limit even at scale.

---

## 4. Rate Limiting Across Multiple Async Requests

**Problem:** Semantic Scholar allows 1 req/sec with an API key. If you fan out to 5 parallel researchers (per the LangGraph Send pattern), each researcher tries to call S2 simultaneously — you'll hit 429s immediately.

**Fix:** A shared rate limiter (token bucket or semaphore) across all async tasks:

```python
import asyncio
from asyncio import Semaphore

# Global semaphore per API
S2_SEMAPHORE = Semaphore(1)   # 1 concurrent call
CORE_SEMAPHORE = Semaphore(1)  # 10 req/min = ~0.17/sec; be conservative

async def semantic_scholar_search(query: str) -> list:
    async with S2_SEMAPHORE:
        await asyncio.sleep(1.1)  # ~1 req/sec
        # ... actual API call
```

This needs to be a process-global semaphore (not per-request), which means it lives on the FastAPI app object.

---

## 5. LangGraph SqliteSaver Won't Work on Render

**Problem:** LangGraph's `SqliteSaver` checkpointer writes to a local SQLite file. Render's filesystem is ephemeral. The checkpointer data is gone on every deploy/restart.

**Fix:** Use LangGraph's `PostgresSaver` (or `AsyncPostgresSaver`) with the Neon connection string. This requires `langgraph-checkpoint-postgres` package.

```python
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

async with AsyncPostgresSaver.from_conn_string(DATABASE_URL) as checkpointer:
    graph = builder.compile(checkpointer=checkpointer)
```

**Neon gotcha:** Use the pgBouncer (pooler) connection string, not the direct one. The direct connection has limited slots; pgBouncer handles concurrency.

---

## 6. SSE Streaming + Uvicorn + Render Timeout

**Problem:** Deep Research runs 2–5 minutes. Render's free tier has a default request timeout. Long-running HTTP requests may be killed mid-stream.

**Render timeout:** The free tier has a 30-second HTTP request timeout by default (this needs to be verified — their docs say "idle connection timeout", not total request timeout). SSE keeps the connection alive with heartbeat events, so the idle timer resets on each event.

**Fix:** Send a heartbeat SSE event every 15 seconds during research to keep the connection alive:
```python
async def heartbeat():
    while True:
        yield "event: heartbeat\ndata: {}\n\n"
        await asyncio.sleep(15)
```

Also: Render's free tier might kill long connections regardless. If this becomes a problem, the solution is async job dispatch (push to a queue, poll for results) rather than a single long-lived SSE connection.

---

## 7. Cold Start UX Problem

**Problem:** Render free web service cold-starts after inactivity. First request after idle period takes 30+ seconds. The non-technical primary user will think the app is broken.

**Fix options:**
- Loading screen with "Delta is waking up..." message
- A cron job (e.g., Better Uptime free tier) that pings `/health` every 14 minutes to keep the service warm
- Upgrade to Render's $7/month "Starter" tier for always-on (this was the original plan in the PRD)

**Decision:** Use a free uptime monitor (UptimeRobot free: 5-minute intervals, or Better Uptime free: 3-minute intervals) to ping the service and prevent cold starts. Zero cost.

---

## 8. DeepSeek API Reliability

**Problem:** DeepSeek has had well-documented outages and rate limit events, especially during Chinese working hours. A 5-minute research run can fail mid-way on a 429 or 503.

**Fix:** Tenacity retry with exponential backoff (copy from AI-Researcher report):

```python
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(
    stop=stop_after_attempt(6),
    wait=wait_exponential(multiplier=2, min=30, max=1200),
    retry=lambda e: "rate limit" in str(e).lower() or "429" in str(e) or "503" in str(e)
)
async def llm_call(...):
    ...
```

Also: configure LiteLLM fallback model (e.g., `gpt-4o-mini` via OpenAI) as a cold fallback if DeepSeek is down for >10 minutes. Cost guard: only use fallback for cheap model calls, not the full synthesis.

---

## 9. R2 Access From Python

**Problem:** boto3 works with R2 (it's S3-compatible), but requires `endpoint_url` and specific config. Not obvious.

```python
import boto3

r2 = boto3.client(
    "s3",
    endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
    aws_access_key_id=R2_ACCESS_KEY_ID,
    aws_secret_access_key=R2_SECRET_ACCESS_KEY,
    region_name="auto",
)

# Append to a JSONL file in R2: read -> append -> write (R2 has no append operation)
# For true append-only, write each message as a separate object with a sequential key:
# sessions/{session_id}/{timestamp}_{message_id}.json
# Then list + sort to reconstruct the session thread
```

**R2 has no append operation.** Options:
1. Read-modify-write the whole JSONL file on every message (wasteful, race conditions)
2. Write each message as a separate object with a sortable key, reconstruct by listing and sorting
3. Keep a write buffer in Redis, flush to R2 on session end

**Decision for v1:** Option 3 — buffer writes in Upstash Redis (key: `session:{id}:messages`, value: append-only list), flush to R2 as a single JSONL file when session ends or every N messages. Neon stores session metadata. R2 stores the archived JSONL.

---

## 10. What Else You're Missing

**Auth / access control:**
- No auth in v1 as stated. But the app will be public. Add a simple `API_SECRET` env var that both frontend and backend share as a static bearer token. Obscures the URL without building real auth.

**Paper deduplication across sessions:**
- The same paper might be retrieved in 10 different sessions. Without a `papers_seen` table in Neon, you re-embed it every time.
- Add a `papers` table: `(doi TEXT PRIMARY KEY, paper_id TEXT, title TEXT, abstract TEXT, embedding vector(1536), specter_embedding vector(768), fetched_at TIMESTAMPTZ)`
- Check this table before calling Semantic Scholar. Cache hits skip the API call.

**LangGraph version pinning:**
- LangGraph releases breaking changes frequently. Pin `langgraph==0.2.x` or whatever stable version you start with. Don't use `langgraph>=0.2` — you'll get broken on the next 0.3 release.

**asyncio + FastAPI + LangGraph event loop:**
- LangGraph async graphs share the asyncio event loop with FastAPI. This is fine with uvicorn. The gotcha: don't call `asyncio.run()` inside a FastAPI endpoint — you're already in an async context. Use `await graph.ainvoke(...)`.

**Loguru vs standard logging:**
- `loguru` is in the tech stack. It does not integrate natively with uvicorn's access logs. Add a `loguru` sink for uvicorn:
  ```python
  import logging
  class InterceptHandler(logging.Handler):
      def emit(self, record):
          logger.opt(depth=6, exception=record.exc_info).log(record.levelname, record.getMessage())
  logging.basicConfig(handlers=[InterceptHandler()], level=0)
  ```

**Frontend hosting:**
- Not yet decided. Options: Render static site (free, no cold start for static), Cloudflare Pages (free, global CDN, zero cold start). **Cloudflare Pages is better** — free, no cold start, global CDN, custom domain included.
