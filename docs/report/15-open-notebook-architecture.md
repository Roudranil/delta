# Report 15: Open Notebook — Architecture, LangGraph Workflows & Patterns

Source: `reference-repos/open-notebook`

---

## Executive Summary

Open Notebook is an open-source NotebookLM clone built with FastAPI + LangGraph + SurrealDB. It processes multi-modal content (PDFs, URLs, audio, video), generates AI-powered notes and insights, supports semantic search via vector embeddings, and produces podcast-style audio summaries. The architecture is three-tier: Next.js frontend -> FastAPI backend -> SurrealDB graph database. The LangGraph workflows are thin, clean, and directly copyable for Delta's source ingestion and chat patterns.

---

## 1. Three-Tier Architecture

```
Frontend (Next.js 16 / React 19)   port 3000
  Zustand state, TanStack Query, shadcn/ui, Tailwind
         │ HTTP REST
API (FastAPI)                        port 5055
  LangGraph orchestration, async job queue
         │ SurrealQL
Database (SurrealDB)                 port 8000
  Graph model: Notebook -> Source -> Note -> ChatSession
  Vector embeddings for semantic search
```

Key choice: SurrealDB as graph DB. For Delta, SQLite is correct (2 users, no graph traversal needed). Do not copy the SurrealDB layer.

---

## 2. Domain Models (`open_notebook/domain/`)

```python
# base.py — all records have auto-managed timestamps
class BaseModel:
    id: Optional[str]
    created_at: Optional[datetime]
    updated_at: Optional[datetime]

# notebook.py
class Notebook:
    name: str
    description: Optional[str]
    # relationships: has many Source, Note, ChatSession

class Source:
    title: Optional[str]
    full_text: Optional[str]          # extracted content
    asset: Optional[Asset]            # url or file_path
    # has many SourceInsight (AI-generated summaries)

class Note:
    title: str
    content: str                      # markdown
    note_type: str                    # "human" or "ai"
    source_ids: list[str]             # sources this note references

class SourceInsight:
    title: str
    content: str
    transformation_name: str          # which transformation produced this
```

The `vector_search()` function on Source queries SurrealDB's built-in ANN index. For Delta: replicate with SQLite + sqlite-vec or just use Semantic Scholar's own relevance ranking.

---

## 3. LangGraph Workflows (`open_notebook/graphs/`)

### 3.1 `chat.py` — Conversational agent with SQLite checkpointing

```python
class ThreadState(TypedDict):
    messages: Annotated[list, add_messages]   # LangGraph message reducer
    notebook: Optional[Notebook]              # context injected per session
    context: Optional[str]                    # retrieved source text
    model_override: Optional[str]

# single-node graph — model call + thinking content strip
agent_state = StateGraph(ThreadState)
agent_state.add_node("agent", call_model_with_messages)
agent_state.add_edge(START, "agent")
agent_state.add_edge("agent", END)
graph = agent_state.compile(checkpointer=SqliteSaver(conn))
```

**Key patterns:**
- `SqliteSaver` for persistence: `conn = sqlite3.connect(CHECKPOINT_FILE, check_same_thread=False)`
- `clean_thinking_content()` strips `<think>...</think>` tags from DeepSeek/extended-thinking models
- Async/sync bridging: LangGraph nodes are sync but `provision_langchain_model()` is async -> `ThreadPoolExecutor` workaround

**Directly applicable to Delta:** This is almost exactly what Delta needs for chat. Copy the `ThreadState` + `SqliteSaver` pattern.

### 3.2 `source.py` — Content ingestion with fan-out transformations

```python
class SourceState(TypedDict):
    content_state: ProcessSourceState    # from content-core library
    apply_transformations: List[Transformation]
    source_id: str
    source: Source
    transformation: Annotated[list, operator.add]  # accumulates results
    embed: bool

# Graph: content_process -> save_source -> [transform_content × N] (parallel fan-out)
workflow.add_conditional_edges(
    "save_source", trigger_transformations, ["transform_content"]
)
```

**`trigger_transformations` uses `Send`:**
```python
def trigger_transformations(state: SourceState, config: RunnableConfig) -> List[Send]:
    return [
        Send("transform_content", {"source": state["source"], "transformation": t})
        for t in state["apply_transformations"]
    ]
```

This `Send` fan-out pattern is directly applicable to Delta when running parallel sub-searches across Semantic Scholar, CORE, OpenAlex for the same query.

### 3.3 `ask.py` — Search + synthesis

```python
# Generates multiple search terms -> parallel vector searches -> LLM synthesis
# Not deeply useful for Delta since we have external search APIs, not a local vector store
```

### 3.4 `transformation.py` — Single-node LLM transformation

```python
class TransformationState(TypedDict):
    input_text: str
    transformation: Transformation
    output: str

# One node: call LLM with prompt template -> return output
# Uses ai_prompter.Prompter for Jinja2 template rendering
```

For Delta: the pattern of defining named transformations (summarize, extract_entities, etc.) and running them via a reusable graph node is worth copying.

---

## 4. AI Provider Abstraction (`open_notebook/ai/`)

### 4.1 Model provisioning

```python
# provision.py
async def provision_langchain_model(
    context: str,
    model_id: Optional[str],
    use_case: str,
    max_tokens: int = 4096,
) -> BaseChatModel:
    # 1. Look up model by ID from DB
    # 2. Decrypt credential associated with model
    # 3. Return LangChain ChatModel with provider-specific config
```

Open Notebook uses the `Esperanto` library (8+ providers: OpenAI, Anthropic, Google, Groq, Ollama, Mistral, DeepSeek, xAI). Delta uses LiteLLM for the same purpose. The abstraction shape is similar — model ID -> provider -> credentials -> LangChain compatible object.

### 4.2 Error classification

```python
# utils/error_classifier.py
def classify_error(e: Exception) -> tuple[type[OpenNotebookError], str]:
    # Maps raw provider exceptions to typed errors with user-friendly messages
    # AuthenticationError, RateLimitError, ExternalServiceError, etc.
```

Worth copying this pattern for Delta. Raw LLM provider errors are cryptic; classify them at the boundary.

### 4.3 Thinking content stripping

```python
# utils/__init__.py
def clean_thinking_content(content: str) -> str:
    # Strips <think>...</think> blocks from DeepSeek / extended-thinking models
    import re
    return re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
```

**Copy this directly.** Delta uses DeepSeek v3/v4 which emits `<think>` tags. Strip them before displaying to the user.

---

## 5. API Layer (`api/`)

### 5.1 Async job queue for long-running tasks

Podcast generation is slow. Open Notebook uses `surreal-commands` for an async job queue:
- `podcast_service.py` submits jobs (non-blocking)
- Client polls `/commands/{command_id}` for status
- `max_attempts: 1` to prevent duplicate records on retry

For Delta: SSE streaming is better than polling for real-time research progress. But the pattern of separating "submit task" from "await result" is applicable for long research runs.

### 5.2 FastAPI router pattern

```python
# api/routers/sources.py
router = APIRouter(prefix="/sources", tags=["sources"])

@router.post("/")
async def create_source(data: SourceCreate):
    source = await source_service.create(data)
    background_tasks.add_task(source_graph.ainvoke, {...})  # non-blocking
    return source
```

LangGraph graph invoked as a `background_task` — the API returns immediately, the graph runs in the background. For Delta, SSE endpoints can stream graph output as it runs instead.

---

## 6. Content Processing

Uses `content-core` library for extraction:
- Supports 50+ file types (PDF, DOCX, video, audio, web URLs)
- Returns `ProcessSourceState` with `url`, `file_path`, `content`, `title`
- Speech-to-text via configurable model for audio/video

For Delta: `content-core` is worth considering for PDF/URL extraction instead of building custom. It handles the messy edge cases.

---

## 7. Database: SurrealDB vs SQLite

Open Notebook chose SurrealDB for:
- Built-in graph relationships (Notebook -> Source -> Note)
- Built-in vector search (ANN index on embeddings)
- Schema migrations via SurrealQL

**Delta should NOT use SurrealDB.** Two users, no graph relationships needed, SQLite is fine. The ORM/repository pattern from Open Notebook is worth copying, not the database itself.

---

## 8. What Delta Should Copy

| Pattern | Source file | Why |
|---------|-------------|-----|
| `ThreadState` + `SqliteSaver` chat graph | `graphs/chat.py` | Chat with persistence, ~30 lines |
| `Send` fan-out for parallel work | `graphs/source.py:131-146` | Parallel search across APIs |
| `clean_thinking_content()` | `utils/__init__.py` | DeepSeek `<think>` stripping |
| `classify_error()` pattern | `utils/error_classifier.py` | User-friendly LLM errors |
| Named transformation graph | `graphs/transformation.py` | Reusable LLM transformation node |
| Background task + non-blocking API | `api/routers/sources.py` | Async research invocation |

---

## 9. What Delta Should Ignore

- SurrealDB (overkill for 2 users)
- Esperanto library (Delta uses LiteLLM)
- Podcast generation (irrelevant)
- Multi-provider credential DB (use env vars)
- `content-core` library for now (add later if needed)
- Frontend (build Delta's own)

---

## 10. Unknown Unknowns / Things to Watch

**Async/sync bridging is fragile.** Open Notebook's `chat.py` uses `ThreadPoolExecutor` + `asyncio.new_event_loop()` because LangGraph nodes are sync but model calls are async. This pattern works but is brittle. LangGraph 0.2+ supports async nodes natively — use `async def` node functions and `await graph.ainvoke()` instead.

**SqliteSaver thread safety.** The connection is shared across all graph invocations: `conn = sqlite3.connect(file, check_same_thread=False)`. Fine for 2 users; will break under concurrent load. For Delta, fine.

**`add_messages` reducer is not a list append.** It deduplicates by message ID. If you construct messages manually without IDs, you may get unexpected behavior. Use `HumanMessage`, `AIMessage` etc. from `langchain_core.messages` which auto-generate IDs.
