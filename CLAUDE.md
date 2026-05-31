# CLAUDE.md

Delta is a personal research agent for literature survey, paper discovery, and research synthesis.

## What the agent does

1. **ASK** — understand the user's research question, gather context, clarify
2. **PLAN** — draft a structured research plan (questions, subquestions, keywords)
3. **EXECUTE** — run the plan: search papers, fetch abstracts/PDFs, synthesize findings
4. **OUTPUT** — write a structured literature review in markdown

## Tech stack

### Backend
- Python 3.10+
- LangChain + LangGraph (agent loop and graph orchestration)
- LiteLLM (model gateway — DeepSeek as primary, cheap and capable)
- FastAPI + uvicorn (API server, SSE streaming)
- SQLite via aiosqlite (session storage)
- Pydantic (schemas)
- Loguru (logging)

### Frontend
- Hosted web app — browser only, no local setup required
- Primary user is non-technical — modes are buttons, not slash commands
- Must be giftwrapped: she opens a URL and it works

### Hosting (future, after MVP works)
- Render (free tier web app)
- Upstash Redis (if needed)
- Free tier object storage (TBD)

## Build order (MVP)

1. `tools/semantic_scholar.py` — search, paper details, recommendations
2. `graphs/execution.py` — runs research plan, calls tools, synthesizes
3. Wire ASK → PLAN → EXECUTE in master graph
4. CLI runner
5. FastAPI + SSE
6. Frontend

## What NOT to build in v1

- MCP integration
- Skills/rules/modes config system
- NotebookLM-style features (audio, document upload)
- LLM wiki / knowledge graph
- Multi-user support
- Redis / Postgres (SQLite is fine)

## Reference material

- `reference-repos/lattice/` — previous attempt by the developer. Good ideas in ASK/PLAN prompts and virtual filesystem. Do not continue it, but copy useful code selectively.
- `reference-repos/feynman/` — open source research agent
- `reference-repos/open_deep_research/` — LangChain deep research agent
- `reference-repos/open-notebook/` — open source NotebookLM
- `reference-repos/llm_wiki/` — Karpathy-style LLM wiki

## Behavioural rules

- Keep it simple. No abstractions until there's a concrete reason.
- Two users total. Build for them, not for a product.
- If a feature doesn't make research easier, defer it.
- Budget: ~$30/month on LLM API costs. Be efficient with tokens.
