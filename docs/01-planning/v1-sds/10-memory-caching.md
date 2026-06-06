# SDS-10: Memory & Semantic Caching

## Overview

Three distinct concerns, kept separate:

| Concern | What it solves | V1 or V2 |
|---------|---------------|----------|
| **Semantic cache** | Avoid redundant LLM calls for similar queries | V1 |
| **Session memory** | "What did I research before?" — cross-session knowledge | V1 |
| **Knowledge graph** | Structured claims, concepts, contradiction detection | V1 (simple via Postgres), V2 (full graph) |

---

## Semantic caching

### What it is

Instead of exact-match caching (same string -> same response), semantic caching embeds the query and searches for a similar past query above a similarity threshold. If found, returns the cached response. 40–60% cost reduction expected on a research agent with repeated topic patterns.

### Implementation — DIY with pgvector

**Do NOT use:** LiteLLM SDK (no semantic cache in direct mode, only proxy), LangChain cache (exact match only), GPTCache (active bugs in pgvector path), Mem0/Zep/Letta (too heavy, wrong use case).

**Do use:** DIY ~50-line wrapper using existing Neon pgvector + Upstash Redis.

```python
# server/lib/cache.py
import hashlib
from sentence_transformers import SentenceTransformer
from db.postgres import pool
from db.redis import redis_client

_encoder = SentenceTransformer("BAAI/bge-m3")  # loaded once at startup

async def get_cached_response(query: str, model: str) -> str | None:
    embedding = _encoder.encode(query).tolist()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT response FROM semantic_cache
            WHERE model = $1
            AND 1 - (query_embedding <=> $2::vector) > 0.95
            ORDER BY 1 - (query_embedding <=> $2::vector) DESC
            LIMIT 1
        """, model, embedding)
    return row["response"] if row else None

async def set_cached_response(query: str, model: str, response: str) -> None:
    embedding = _encoder.encode(query).tolist()
    query_hash = hashlib.sha256(f"{model}:{query}".encode()).hexdigest()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO semantic_cache (query_hash, query_embedding, response, model)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (query_hash) DO NOTHING
        """, query_hash, embedding, response, model)
```

### Neon schema addition

```sql
CREATE TABLE semantic_cache (
    id          BIGSERIAL PRIMARY KEY,
    query_hash  TEXT NOT NULL,
    query_embedding vector(1024) NOT NULL,
    response    TEXT NOT NULL,
    model       TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT  unique_query UNIQUE (query_hash)
);
CREATE INDEX ON semantic_cache USING hnsw (query_embedding vector_cosine_ops);
```

### Similarity threshold

Use 0.95. Lower = more cache hits but risk of wrong responses. Higher = fewer hits. Start at 0.95, tune down to 0.92 if hit rate is too low after a week of use.

### What gets cached vs not

**Cache:** synthesis prompts, clarification prompts, plan generation — these are the expensive calls.
**Do not cache:** tool call results (paper fetches, web searches) — these need fresh data.

### Storage estimate

2 users, 3 months: ~500–1000 unique queries. At 1024 dims × 4 bytes = ~4KB per vector. Total: ~4 MB. Well within Neon's 0.5 GB limit.

---

## Session memory — LangMem

### What it is

LangMem is LangGraph's official memory library. It stores structured memories in `AsyncPostgresStore` (your existing Neon connection), extracts key facts from sessions, and retrieves relevant past knowledge via semantic search. This replaces the flat `memory TEXT` field in the `users` table.

### Schema change to SDS-07

Remove `users.memory TEXT`. Replace with LangGraph's `AsyncPostgresStore` which creates its own tables. The store is namespaced per user: `namespace=("user", user_id)`.

LangMem stores entries like:
```json
{
  "type": "research_finding",
  "content": "User has extensively researched topological insulators. Key papers: [P1, P2]. Main finding: surface states are robust against disorder.",
  "created_at": "...",
  "session_id": "..."
}
```

### Setup

```python
# server/lib/memory.py
from langgraph.store.postgres import AsyncPostgresStore
from langmem import create_search_memory_tool, create_manage_memory_tool

store = AsyncPostgresStore.from_conn_string(
    conn_string=settings.DATABASE_URL_DIRECT,
    index={"dims": 1024, "embed": embed_fn}   # uses BGE-M3 via Novita
)

def get_memory_tools(user_id: str):
    namespace = ("user", user_id)
    return [
        create_search_memory_tool(store, namespace=namespace),
        create_manage_memory_tool(store, namespace=namespace),
    ]
```

### How it's used in the graph

**EXPLORE `load_context` node:** Calls `search_memory_tool` with the user's current query to retrieve relevant past findings. Injects into system prompt: "From your past research: [findings]".

**EXPLORE `write_output` node:** Triggers background task — calls `manage_memory_tool` to extract and store key findings from this session. Non-blocking.

**DEEP `load_context_deep` node:** Same search — gives the LLM context on what she already knows so the research plan doesn't duplicate past work.

**DEEP `finalize` node:** Stores findings from the DEEP run as memories. Also stores the artifact reference so future EXPLORE sessions can find it via `search_library`.

### What gets stored as memory

The LLM in `write_output` / `finalize` decides what's worth storing. Prompt instructs: store research findings, key papers, conclusions, user preferences, open questions. Do not store: raw tool outputs, intermediate reasoning, session metadata.

---

## Knowledge graph — V1 via Postgres, V2 via dedicated graph

### V1 — Simple contradiction detection in Postgres

No new infrastructure. Add two tables to Neon:

```sql
CREATE TABLE claims (
    id          BIGSERIAL PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id),
    paper_id    TEXT REFERENCES papers(id),   -- null for claims from web/synthesis
    session_id  TEXT REFERENCES sessions(id),
    text        TEXT NOT NULL,
    claim_embedding vector(1024) NOT NULL,
    confidence  FLOAT NOT NULL DEFAULT 0.8,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE claim_relationships (
    from_claim_id BIGINT REFERENCES claims(id),
    to_claim_id   BIGINT REFERENCES claims(id),
    type          TEXT NOT NULL,              -- 'contradicts' | 'supports' | 'extends'
    strength      FLOAT NOT NULL,
    PRIMARY KEY (from_claim_id, to_claim_id)
);

CREATE INDEX ON claims USING hnsw (claim_embedding vector_cosine_ops);
CREATE INDEX ON claims (user_id);
```

**Contradiction detection query:**
```sql
-- Find contradictory claim pairs for a user
SELECT c1.text as claim_a, c2.text as claim_b,
       1 - (c1.claim_embedding <=> c2.claim_embedding) as similarity
FROM claims c1
CROSS JOIN LATERAL (
    SELECT * FROM claims c2
    WHERE c2.user_id = c1.user_id
    AND c2.id != c1.id
    AND 1 - (c1.claim_embedding <=> c2.claim_embedding) BETWEEN 0.6 AND 0.85
    -- High similarity but not identical = potential contradiction
    ORDER BY c1.claim_embedding <=> c2.claim_embedding
    LIMIT 3
) c2
WHERE c1.user_id = $1
```

Claims are extracted by the `synthesize` node after generating the report. A cheap LLM call extracts 5–10 key claims as structured output and writes them to Postgres.

**When contradictions are surfaced:** The `load_context` and `load_context_deep` nodes query for recent contradictions and include them in the system prompt: "Note: you have previously found conflicting claims about [topic]."

### V2 — Full knowledge graph (defer)

When V1's Postgres approach feels limiting (expect this around month 3–4):
- **FalkorDB** (Redis-based graph DB, self-hostable, free tier) for concept/claim/paper relationships
- Nodes: `Concept`, `Claim`, `Paper`, `Session`
- Edges: `SUPPORTS`, `CONTRADICTS`, `EXTENDS`, `CITED_BY`, `RELATED_TO`
- Graph traversal queries instead of SQL joins
- Visualisation in the frontend (concept map)

Do not build this now.

---

## What Mem0, Zep, Letta are — and why we're not using them

**Mem0:** Cloud-only managed memory service, not self-hostable for free. $0.10–$1 per message. Skip.

**Zep:** Deprecated. Community edition moved to legacy folder, unmaintained. Skip.

**Letta (MemGPT):** Full agent framework with memory blocks. Overkill — it's an alternative to LangGraph, not a library you add to it. Skip.

---

## Blaxel — what it is and how to use the $250 credits

**Blaxel is not an LLM provider.** It's an agent infrastructure platform (YC-backed). What it offers:

| Feature | Description | Cost | Use for Delta |
|---------|-------------|------|---------------|
| **Batch Jobs** | Background async jobs, up to 24hr, configurable RAM | $0.000006/GB-second | DEEP research runs |
| **Cron Jobs** | Scheduled Python jobs | **FREE** | Memory updates, JSONL archives, embedding generation |
| **MCP Servers** | Host custom MCP tool servers | $0.000007/GB-second | Custom tools (later) |
| **Sandboxes** | Persistent microVMs, sub-25ms resume | $0.000023–$0.000368/second | Not needed V1 |

### The key architectural shift Blaxel enables

Currently: Render runs everything — HTTP server + LangGraph graph. The 10–20 min DEEP run risks Render's request timeout and hogs the 0.1 vCPU.

**With Blaxel Batch Jobs:**
- Render handles only HTTP, SSE, auth, short queries (EXPLORE). Stays lean.
- DEEP research graph runs on Blaxel Batch as a background job.
- Render triggers the batch job, returns a `run_id` to the frontend.
- Frontend polls `GET /sessions/{id}` for status. When `run.status = 'complete'`, fetches artifact.
- No timeout risk. No OOM risk. Render never runs long-running graph code.

### Cost reality check

A 20-min DEEP run on 4GB Blaxel: 4 GB × 20 min × 60 sec × $0.000006 = **$0.029 per run**.

$250 credits = ~8,600 DEEP research runs. You will never exhaust this on 2 users.

### FREE cron job uses

Background tasks that currently run as `asyncio.create_task()` inside Render can move to Blaxel cron (free):
- Hourly: chunk + embed new papers
- After each session: write JSONL archive to R2
- Daily: LangGraph checkpoint cleanup (delete checkpoints older than 7 days)
- Daily: memory consolidation (merge similar memories via LangMem)

This removes background work from Render entirely, making the API server more responsive.

### Important caveat

Blaxel sandbox connections do not persist across suspend/resume. Design all Blaxel workers as **stateless jobs**: open fresh DB connections on start, do work, close connections, exit. This is fine for LangGraph since each run reconstructs state from Postgres anyway.

### V1 decision: run DEEP on Render or Blaxel?

For MVP, keep DEEP on Render. The 18-minute hard cap + `asyncio.create_task()` pattern is sufficient. Move to Blaxel Batch only if Render timeouts become a real problem in production.

Use Blaxel **free cron jobs from day one** for background tasks — zero cost, immediate value.

---

## Updated environment variables

```bash
# Blaxel
BLAXEL_API_KEY=...
BLAXEL_WORKSPACE=...          # from Blaxel dashboard

# Novita (embeddings)
NOVITA_API_KEY=...             # for BGE-M3 embeddings

# LangMem / LangGraph Store uses DATABASE_URL_DIRECT (already in SDS-01)
```
