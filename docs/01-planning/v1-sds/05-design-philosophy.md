# SDS-05: Design Philosophy

Principles that resolve ambiguous decisions without debate.

---

## Core Principles

**1. Minimal by default.**
Write the least code that works. No abstractions until there are 3+ concrete cases. No helper functions for things used once. No config systems for things that don't change. If it can be a constant, it's a constant.

**2. As few tools as possible.**
The agent has the minimum number of tools needed. `fetch_paper`, `web_search`, `list_sessions`, `get_session_summary`. That's the surface area. Every tool added is a decision point for the LLM and a failure mode.

**3. Only what's needed.**
No feature gets built speculatively. If it doesn't make research easier for her today, it doesn't exist. This is not a platform. It's a personal tool for 2 people.

**4. Everything is logged.**
Every LangGraph node entry and exit. Every external API call. Every LLM call with token counts. Every cache hit and miss. Every error. Log lines are structured JSON with `user_id`, `session_id`, `run_id` always present. If something breaks in production, the logs must tell the full story without guessing.

**5. Deterministic over LLM wherever possible.**
Mode selection, deduplication, citation assembly, API calls, chunking, ranking — all deterministic code. The LLM decides what to search for and what to say. Code decides everything else.

**6. Fail visibly, not silently.**
A paper not found is flagged `abstract_only`. A failed API call is logged and skipped, not swallowed. A broken SSE stream shows an error state, not a spinner forever. She should never wonder if something is working.

**7. The user never waits for non-critical work.**
Session title generation, memory updates, session summary bullets — these run after the response is sent, not before. They never block the main response path.

**8. Every citation is real.**
The LLM references papers by `paper_id` only. Code resolves to full citation. If the ID doesn't exist in the retrieved set, it's silently dropped. No hallucinated citations ever reach the user.
