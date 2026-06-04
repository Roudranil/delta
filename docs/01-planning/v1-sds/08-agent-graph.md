# SDS-08: Agent & Graph Design

## Two modes, two graphs

| Mode | Name | What it does | Time | Output |
|------|------|-------------|------|--------|
| EXPLORE | (name TBD — working name: EXPLORE) | ReAct loop, full tool access, conversational, builds library | Seconds–2 min | Inline markdown response with citations |
| DEEP | DEEP | Structured pipeline: clarify → plan → fan-out researchers → synthesize → artifact | 10–20 min | MDX document artifact stored in library |

Both modes use the same `AsyncPostgresSaver` checkpointer, keyed by `session_id = thread_id`. They are separate compiled LangGraph graphs. A session has a fixed mode at creation — `session.mode` is immutable.

After a DEEP session produces its artifact, subsequent messages run through EXPLORE — the artifact is a `user_document` in her library, so `search_library` finds it automatically. No special wiring needed. `session_phase` tracks this: `researching → complete → followup`.

---

## Tool registry

Seven tools. Registered as callables at startup — heavy dependencies lazy-imported on first call. Adding a new tool means adding it to the registry dict and the relevant tool list. No graph recompilation needed.

```python
TOOL_REGISTRY = {
    "search_papers":    search_papers,
    "fetch_paper":      fetch_paper,
    "get_citations":    get_citations,
    "get_referenced_by": get_referenced_by,
    "search_web":       search_web,
    "search_library":   search_library,
    "read_document":    read_document,
}

EXPLORE_TOOLS    = all seven tools
RESEARCHER_TOOLS = search_papers, fetch_paper, get_citations, get_referenced_by, search_web
                   # no search_library or read_document — researchers look outward only
```

| Tool | Signature | Used in | Notes |
|------|-----------|---------|-------|
| `search_papers` | `(query, limit, year_from, year_to)` | EXPLORE, DEEP researchers | S2 + OpenAlex |
| `fetch_paper` | `(doi, arxiv_id, s2_id, query)` | EXPLORE, DEEP researchers | Full waterfall — cache → S2 → OpenAlex → arXiv → full text |
| `get_citations` | `(paper_id, limit)` | EXPLORE, DEEP researchers | Backward snowball — papers this paper cites |
| `get_referenced_by` | `(paper_id, limit)` | EXPLORE, DEEP researchers | Forward snowball — papers citing this paper |
| `search_web` | `(query, date_from, domains)` | EXPLORE, DEEP researchers | Exa → Tavily fallback |
| `search_library` | `(query, limit)` | EXPLORE, DEEP synthesize | RAG over user's saved papers + documents |
| `read_document` | `(document_id)` | EXPLORE, DEEP synthesize | Full text of a saved document from R2 |

Every tool:
- Checks budget before executing — returns a budget-exceeded signal if cap hit, never raises
- Returns a typed result: `ToolResult(status='success'|'partial'|'failed'|'budget_exceeded', data=..., message=...)`
- The agent sees typed outcomes and decides what to do next — never crashes on tool failure

---

## Budget system

Every run starts with a fresh budget object in state. Every tool call checks budget before executing.

```python
@dataclass
class RunBudget:
    papers_fetched: int = 0
    web_searches: int = 0
    llm_calls: int = 0
    start_time: float = field(default_factory=time.time)

    # Caps — differ per mode
    max_papers: int = 15               # EXPLORE default
    max_web_searches: int = 5          # EXPLORE default
    max_llm_calls: int = 20
    max_runtime_seconds: int = 300     # 5 min hard cap for EXPLORE

    def can_fetch_paper(self) -> bool:
        return (self.papers_fetched < self.max_papers
                and time.time() - self.start_time < self.max_runtime_seconds)

    def can_search_web(self) -> bool:
        return self.web_searches < self.max_web_searches

@dataclass
class ResearcherBudget:
    papers_fetched: int = 0
    web_searches: int = 0
    max_papers: int = 15               # per researcher
    max_web_searches: int = 5          # per researcher
    max_runtime_seconds: int = 900     # 15 min per researcher
```

DEEP hard cap: 18 minutes total. If still running at 18 minutes, `synthesize` fires with whatever was collected. Partial report clearly marked as incomplete.

---

## State definitions

### ExploreState

```python
class ExploreState(MessagesState):
    # MessagesState provides: messages: Annotated[list, add_messages]
    user_id: str
    session_id: str
    run_id: str
    user_memory: str
    budget: RunBudget
```

### DeepState

```python
class DeepState(TypedDict):
    user_id: str
    session_id: str
    run_id: str
    user_memory: str
    messages: Annotated[list, add_messages]
    clarification_qa: list[dict]       # [{question, answer}, ...]
    research_plan: ResearchPlan
    researcher_findings: Annotated[list[ResearcherFindings], operator.add]
    artifact_id: str
    artifact_r2_key: str
    budget: DeepBudget
    session_phase: Literal['researching', 'complete', 'followup']

class ResearchPlan(BaseModel):
    title: str
    background: str                    # 2-3 sentences of context
    subtopics: list[Subtopic]          # 3-5 subtopics

class Subtopic(BaseModel):
    name: str
    rationale: str
    search_queries: list[str]          # 3-5 specific queries per subtopic
```

### ResearcherState

```python
class ResearcherState(TypedDict):
    user_id: str
    session_id: str
    run_id: str
    subtopic: str
    search_queries: list[str]
    messages: Annotated[list, add_messages]
    budget: ResearcherBudget
    findings: str                      # compressed markdown — written back to parent
    papers_found: list[str]            # paper_ids fetched — written back to parent

class ResearcherOutputState(TypedDict):
    # Only these fields flow back to DeepState.researcher_findings
    findings: ResearcherFindings

class ResearcherFindings(BaseModel):
    subtopic: str
    summary: str                       # compressed findings markdown
    papers: list[str]                  # paper_ids found
    web_sources: list[dict]            # [{id, url, title}, ...]
    gaps: str                          # what was not found
```

---

## EXPLORE graph

```
START
  ↓
load_context
  ↓
agent  ←───────────────────┐
  ↓ (tools_condition)      │
  ├── tool_calls → tools ──┘
  └── no tool_calls → write_output
                       ↓
                      END
```

### Nodes

**`load_context`**
- Queries `messages` table for all prior messages in this session (ordered by `sequence_number`)
- Loads `users.memory`
- Populates: `messages` (history), `user_memory`, fresh `budget`
- Writes nothing to Postgres

**`agent`**
- Builds system prompt: user memory + EXPLORE mode instructions
- Calls LiteLLM via `lib/llm` — `usage_events` row written automatically
- If budget exceeded before call: returns final answer noting budget reached, skips LLM call
- If response has tool calls: writes `thinking` message row to Postgres (reasoning content before tool calls), returns AIMessage to state
- If no tool calls: does NOT write here — `write_output` handles final message

**`tools`** — LangGraph's `ToolNode(EXPLORE_TOOLS)`
- Receives all tool calls from last AIMessage
- Executes them in parallel via `executor.map()`
- Each tool checks budget, returns typed `ToolResult`
- ToolMessages added to state via `add_messages` reducer

**`write_output`**
- Writes final assistant message row to Postgres
- Updates `runs.status = 'complete'`
- Triggers background tasks (non-blocking, do not await):
  - Update `users.memory` via cheap LLM summarisation
  - Write session JSONL archive to R2
- Writes `user_papers` rows for any new papers fetched this run

### Graph construction

```python
explore_builder = StateGraph(ExploreState)
explore_builder.add_node("load_context", load_context)
explore_builder.add_node("agent", agent_node)
explore_builder.add_node("tools", ToolNode(EXPLORE_TOOLS))
explore_builder.add_node("write_output", write_output)

explore_builder.add_edge(START, "load_context")
explore_builder.add_edge("load_context", "agent")
explore_builder.add_conditional_edges(
    "agent",
    tools_condition,           # LangGraph built-in: checks tool_calls on last AIMessage
    {"tools": "tools", "__end__": "write_output"}
)
explore_builder.add_edge("tools", "agent")
explore_builder.add_edge("write_output", END)

explore_graph = explore_builder.compile(checkpointer=async_postgres_saver)
```

---

## DEEP graph

```
START
  ↓
load_context_deep
  ↓
clarify  ──→ [INTERRUPT: questions for user]
  ↓ [RESUME: user answers]
plan
  ↓
confirm_plan  ──→ [INTERRUPT: show plan to user]
  ↓ [RESUME: approved or feedback]
  │
  ├── feedback → plan (loop back, re-plan with feedback)
  └── approved → fan_out_researchers
                      ↓ [Send × N — one per subtopic, parallel]
              researcher[0..N]  ← ReAct mini-loop
                      ↓ [findings merge via operator.add]
              synthesize
                      ↓
              finalize
                      ↓
                     END
```

### Nodes

**`load_context_deep`**
- Loads `users.memory` ONLY — no message history
- DEEP always starts with clean context. Cross-turn memory comes from user_memory, not messages.
- Initialises `DeepBudget`: `max_papers_per_researcher=15`, `max_researchers=5`

**`clarify`**
- LLM reads the user's research question
- Structured output: `ClarificationQuestions(questions: list[str], needs_clarification: bool)`
- LLM decides whether clarification is needed — 0, 1, 2, or 3 targeted questions max. No padding.
- If `needs_clarification=False`: skips interrupt, goes directly to `plan`
- If questions: calls `interrupt({"questions": questions, "type": "clarification"})`
- On resume: `Command(resume={"answers": [...]})` — writes `clarification_qa` to state
- Writes `thinking` message row to Postgres

**`plan`**
- LLM generates `ResearchPlan` structured output using user query + `clarification_qa`
- 3–5 subtopics, each with 3–5 specific search queries
- Writes `research_plan` to state
- Writes plan as `thinking` message row to Postgres (user sees it)

**`confirm_plan`**
- Formats plan as readable markdown, writes as `assistant` message row to Postgres
- Calls `interrupt({"plan": formatted_plan, "type": "confirm_plan"})`
- Resume value: `{approved: true}` or `{feedback: "..."}`
- If feedback: conditional edge routes back to `plan` (re-plans with feedback as additional context)
- If approved: routes to `fan_out_researchers`

**`fan_out_researchers`** (conditional edge function, not a node)
```python
def fan_out_researchers(state: DeepState) -> list[Send]:
    return [
        Send("researcher", ResearcherState(
            user_id=state["user_id"],
            session_id=state["session_id"],
            run_id=state["run_id"],
            subtopic=subtopic.name,
            search_queries=subtopic.search_queries,
            messages=[],
            budget=ResearcherBudget(),
            findings="",
            papers_found=[],
        ))
        for subtopic in state["research_plan"].subtopics
    ]
```

**Researcher subgraph** — ReAct mini-loop (runs N times in parallel)
```
START
  ↓
researcher_agent  ←───────────────────┐
  ↓ (tools_condition)                  │
  ├── tool_calls → researcher_tools ──┘
  └── no tool_calls → compress_findings
                          ↓
                         END
```
- `researcher_agent`: same ReAct pattern as EXPLORE, RESEARCHER_TOOLS only
- `researcher_tools`: `ToolNode(RESEARCHER_TOOLS)`
- `compress_findings`: LLM compresses all findings into `ResearcherFindings`. If nothing found, explicitly flags gaps. This is the only output that flows back to parent via `operator.add` on `researcher_findings`.

Warning: 5 researchers × compression call each = 5 parallel LLM calls. Each compression prompt can be 5–10K tokens. Monitor costs here.

**`synthesize`**
- Receives all `researcher_findings` merged by `operator.add` reducer
- Calls `search_library` to check existing knowledge — avoids duplicating what she already knows
- LLM writes full MDX report with:
  - Structured sections (introduction, per-subtopic, synthesis, gaps, bibliography)
  - Inline `[P1]`, `[P2]` paper citations (resolved by code, never hallucinated)
  - `[W1]`, `[W2]` web source citations
  - Direct quotes for every major claim: `<Quote paper_id="P1" text="exact excerpt" />`
  - Custom components: `<PaperCard id="P1" />`, `<Citation id="P1" />`
- If total finding tokens > 40K: truncate researcher findings proportionally before synthesis prompt

**`finalize`**
- Stores MDX in R2: `documents/{user_id}/{artifact_id}.mdx`
- Creates `user_documents` row: `source_type='deep_research_artifact'`
- Chunks + embeds artifact into `document_chunks` (future EXPLORE sessions can search it)
- Writes `user_papers` rows for all papers found across all researchers
- Updates `runs.status = 'complete'`, sets `artifact_id` on session
- Sets `session_phase = 'complete'`
- Triggers background tasks (non-blocking): memory update, JSONL archive
- Writes final `assistant` message row pointing to artifact

### Graph construction

```python
# Researcher subgraph
researcher_builder = StateGraph(ResearcherState, output=ResearcherOutputState)
researcher_builder.add_node("researcher_agent", researcher_agent_node)
researcher_builder.add_node("researcher_tools", ToolNode(RESEARCHER_TOOLS))
researcher_builder.add_node("compress_findings", compress_findings)
researcher_builder.add_edge(START, "researcher_agent")
researcher_builder.add_conditional_edges(
    "researcher_agent", tools_condition,
    {"tools": "researcher_tools", "__end__": "compress_findings"}
)
researcher_builder.add_edge("researcher_tools", "researcher_agent")
researcher_builder.add_edge("compress_findings", END)
researcher_subgraph = researcher_builder.compile()

# DEEP graph
def route_after_confirm(state: DeepState) -> Literal["plan", "fan_out"]:
    last_resume = state.get("last_resume", {})
    return "plan" if last_resume.get("feedback") else "fan_out"

deep_builder = StateGraph(DeepState)
deep_builder.add_node("load_context_deep", load_context_deep)
deep_builder.add_node("clarify", clarify)
deep_builder.add_node("plan", plan)
deep_builder.add_node("confirm_plan", confirm_plan)
deep_builder.add_node("researcher", researcher_subgraph)
deep_builder.add_node("synthesize", synthesize)
deep_builder.add_node("finalize", finalize)

deep_builder.add_edge(START, "load_context_deep")
deep_builder.add_edge("load_context_deep", "clarify")
deep_builder.add_edge("clarify", "plan")
deep_builder.add_edge("plan", "confirm_plan")
deep_builder.add_conditional_edges(
    "confirm_plan", route_after_confirm,
    {"plan": "plan", "fan_out": "fan_out"}
)
deep_builder.add_conditional_edges("fan_out", fan_out_researchers, ["researcher"])
deep_builder.add_edge("researcher", "synthesize")
deep_builder.add_edge("synthesize", "finalize")
deep_builder.add_edge("finalize", END)

deep_graph = deep_builder.compile(checkpointer=async_postgres_saver)
```

---

## SSE + interrupt API contract

Every `POST /sessions/{id}/messages` call:

```python
# 1. Write user message to Postgres immediately — before graph runs
await db.write_message(session_id, run_id, role='user', content=user_input, sequence_number=N)

# 2. Check if session has a pending interrupt
run = await db.get_active_run(session_id)
if run and run.status == 'interrupted':
    input = Command(resume=parse_resume_value(user_input, run.interrupt_type))
else:
    input = {"messages": [HumanMessage(content=user_input)]}

# 3. Launch graph as background asyncio task — NOT tied to SSE connection
asyncio.create_task(run_graph(session_id, run_id, input, stream_queue))

# 4. Return SSE stream from queue
return EventSourceResponse(stream_from_queue(stream_queue))
```

When graph hits `interrupt()`:
- LangGraph saves checkpoint, raises `GraphInterrupt`
- Graph runner catches it → updates `runs.status = 'interrupted'`, stores `interrupt_type`
- Streams interrupt event to SSE: `{"type": "interrupt", "interrupt_type": "clarification", "questions": [...]}`
- Graph runner exits — SSE stream closes naturally
- Frontend renders the questions as a form

On reconnect / next user message:
- `run.status == 'interrupted'` → resume with `Command(resume=answers)`
- Graph resumes from saved checkpoint, continues execution

DEEP disconnect resilience: `asyncio.create_task()` keeps running after SSE drops. On reconnect, `GET /sessions/{id}` returns `run.status` and `artifact_id` if complete. Frontend fetches artifact independently.

One concurrent DEEP run at a time (system-wide): Redis key `deep:lock` with 20-minute TTL. Set on DEEP run start, deleted on finalize. If lock exists, new DEEP run returns a 409 with a message.

---

## MDX component contract

Fixed component set. Backend produces only these. Frontend must implement all of them before DEEP mode ships.

| Component | Props | Renders |
|-----------|-------|---------|
| `<PaperCard id="P1" />` | `id` | Title, authors, year, venue, abstract preview, DOI link |
| `<Citation id="P1" />` | `id` | Inline superscript citation linking to bibliography |
| `<Quote paper_id="P1" text="..." />` | `paper_id`, `text` | Blockquote with paper attribution |
| `<WebSource id="W1" />` | `id` | Inline web citation with URL and title |

Paper and web source manifests are appended at the end of every MDX document as frontmatter-style JSON blocks that the renderer uses to resolve IDs.

---

## Known limitations

| Limitation | Impact | Mitigation |
|-----------|--------|------------|
| DEEP runs 10–20 min | User must wait or come back | SSE streams progress; hard 18-min cap → partial report |
| 5 parallel researchers × 15 papers = up to 75 paper fetches | Slow under rate limits | Per-researcher time budget; S2 semaphore (1 req/sec) enforces pacing |
| Synthesize prompt can be 20–40K tokens | Cost, latency | Truncate researcher findings proportionally if total > 40K tokens |
| One DEEP run at a time system-wide | Can't run two DEEP sessions simultaneously | Redis lock; clear error message; upgrade services if needed |
| EXPLORE context window fills after long sessions | Old context lost | Sliding window via `trim_messages`; artifacts pinned by reference not by content |
| MDX renderer must implement all components | Frontend dependency | Component contract frozen here — don't add components without updating both sides |
