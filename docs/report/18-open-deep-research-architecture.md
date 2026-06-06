# Report 18: Open Deep Research — LangGraph Multi-Agent Architecture

Source: `reference-repos/open_deep_research`

---

## Executive Summary

Open Deep Research is a production LangGraph implementation of a multi-agent research system from LangChain. It has the cleanest LangGraph architecture of all the reference repos and is the most directly applicable to Delta. The core pattern: a Supervisor agent orchestrates multiple parallel Researcher sub-agents via `Send`, each Researcher uses ReAct tool calling with a configurable search backend, results are compressed and aggregated, and a final report is written. Delta should treat this as the primary architecture reference.

---

## 1. Architecture Overview

```
User query
    │
    ▼
clarify_with_user (optional)
    │
    ▼
write_research_brief        ← transforms query into structured research brief
    │
    ▼
research_supervisor         ← orchestrates parallel sub-agents
    │ Send × N
    ▼
individual_researcher × N   ← each gets a subtopic, runs ReAct tool loop
    │
    ▼
(compress sub-agent output)
    │
    ▼
write_final_report          ← synthesizes all researcher outputs
```

---

## 2. State Definitions (`state.py`)

### 2.1 `override_reducer` — the key pattern

```python
def override_reducer(current_value, new_value):
    """Allows overriding accumulated list values in state."""
    if isinstance(new_value, dict) and new_value.get("type") == "override":
        return new_value.get("value", new_value)
    else:
        return operator.add(current_value, new_value)  # default: append
```

Used in `AgentState` for `raw_notes` and `notes`:
```python
class AgentState(MessagesState):
    supervisor_messages: Annotated[list, override_reducer]
    research_brief: Optional[str]
    raw_notes: Annotated[list[str], override_reducer] = []
    notes: Annotated[list[str], override_reducer] = []
    final_report: str
```

**Why this matters:** LangGraph reducers normally append. When you want to replace a list (e.g., supervisor revises notes after reflection), you send `{"type": "override", "value": new_list}`. This pattern solves the problem of accumulated stale notes. Copy this directly.

### 2.2 State separation by role

```python
class SupervisorState(TypedDict):
    supervisor_messages: Annotated[list, override_reducer]
    research_brief: str
    notes: Annotated[list[str], override_reducer]
    research_iterations: int
    raw_notes: Annotated[list[str], override_reducer]

class ResearcherState(TypedDict):
    researcher_messages: Annotated[list, operator.add]  # append-only per researcher
    tool_call_iterations: int
    research_topic: str
    compressed_research: str
    raw_notes: Annotated[list[str], override_reducer]

class ResearcherOutputState(BaseModel):
    compressed_research: str
    raw_notes: Annotated[list[str], override_reducer]
```

Each researcher has its own isolated state. Only `compressed_research` and `raw_notes` are returned to the supervisor. This prevents message history explosion when running 5+ parallel researchers.

---

## 3. Configuration (`configuration.py`)

This is the cleanest configuration pattern in all the reference repos:

```python
class Configuration(BaseModel):
    # Concurrency
    max_concurrent_research_units: int = 5   # how many parallel researchers
    max_researcher_iterations: int = 6        # how many tool-calling rounds per researcher
    max_react_tool_calls: int = 10            # tool calls per iteration

    # Search
    search_api: SearchAPI = SearchAPI.TAVILY  # ANTHROPIC | OPENAI | TAVILY | NONE

    # Models (4 separate models for 4 roles)
    summarization_model: str = "openai:gpt-4.1-mini"
    research_model: str = "openai:gpt-4.1"
    compression_model: str = "openai:gpt-4.1"
    final_report_model: str = "openai:gpt-4.1"

    # Behavior
    allow_clarification: bool = True

    # MCP (not needed for Delta v1)
    mcp_config: Optional[MCPConfig] = None

    @classmethod
    def from_runnable_config(cls, config: Optional[RunnableConfig]) -> "Configuration":
        # Reads from env vars or RunnableConfig["configurable"]
        configurable = config.get("configurable", {}) if config else {}
        field_names = list(cls.model_fields.keys())
        values = {k: os.environ.get(k.upper(), configurable.get(k)) for k in field_names}
        return cls(**{k: v for k, v in values.items() if v is not None})
```

**For Delta:** Copy this exact pattern. Define a `Configuration(BaseModel)` with:
- `max_concurrent_searches`: how many APIs to query in parallel
- `summarization_model`: cheap model (DeepSeek haiku equivalent) for paper summaries
- `research_model`: capable model (DeepSeek v3) for synthesis
- `final_report_model`: capable model for final output
- `search_backends`: list of sources to query (semantic_scholar, core, openalex, arxiv)

Configuration is passed via `RunnableConfig["configurable"]`, readable in any node. This avoids global state.

---

## 4. The Parallel Researcher Pattern (Core of Delta's Architecture)

```python
# In research_supervisor node:
async def research_supervisor(state: AgentState, config: RunnableConfig) -> Command:
    cfg = Configuration.from_runnable_config(config)
    
    # Supervisor decides which subtopics to research
    subtopics = await decide_subtopics(state.research_brief, cfg)
    
    # Fan out to parallel researchers via Send
    researchers = [
        Send("individual_researcher", {
            "research_topic": topic,
            "researcher_messages": [],
            "tool_call_iterations": 0,
        })
        for topic in subtopics[:cfg.max_concurrent_research_units]
    ]
    
    return Command(goto=researchers)

# Each researcher runs independently:
async def individual_researcher(state: ResearcherState, config: RunnableConfig) -> ResearcherOutputState:
    cfg = Configuration.from_runnable_config(config)
    
    while state["tool_call_iterations"] < cfg.max_researcher_iterations:
        # ReAct: think -> call tool -> observe -> repeat
        response = await research_model.invoke(state["researcher_messages"])
        if response.tool_calls:
            tool_results = await execute_tools(response.tool_calls)
            state["researcher_messages"].extend(tool_results)
            state["tool_call_iterations"] += 1
        else:
            break  # researcher decided it's done
    
    # Compress before returning to supervisor
    compressed = await compress_research(state, cfg)
    return ResearcherOutputState(compressed_research=compressed, raw_notes=state["raw_notes"])
```

**For Delta, this maps to:**
- Each "researcher" = searches one topic across Semantic Scholar + supplementary sources
- Each researcher runs `max_iterations` search rounds (broader -> narrower queries)
- Compression = summarize the found papers before returning to supervisor
- Supervisor collects all compressed summaries -> final report writer

---

## 5. Research Brief Generation

Before dispatching researchers, a brief is generated:
```python
class ResearchQuestion(BaseModel):
    research_brief: str = Field(
        description="A research question that will be used to guide the research."
    )
```

The brief transforms a vague user query into a structured research direction. For "thin film deposition of MoS2 for photovoltaics", the brief would specify: key subtopics, target journals, time range, specific techniques to cover.

**For Delta's ASK mode:** This is the output of the ASK->PLAN transition. The user talks to Delta, Delta generates a research brief, then the EXECUTE phase fans out based on the brief.

---

## 6. Note-Taking and Compression

Each researcher takes notes while searching:
```python
# In utils.py
def get_notes_from_tool_calls(messages: list) -> list[str]:
    """Extract structured notes from tool call results in message history."""
    notes = []
    for msg in messages:
        if isinstance(msg, ToolMessage):
            # Parse paper metadata from tool response
            notes.append(format_paper_as_note(msg.content))
    return notes
```

After a researcher finishes, its full message history (potentially thousands of tokens) is compressed:
```python
async def compress_research(state: ResearcherState, cfg: Configuration) -> str:
    # Uses cheaper compression_model (not the expensive research_model)
    prompt = compress_research_system_prompt + "\n\n" + format_notes(state["raw_notes"])
    response = await compression_model.invoke(prompt)
    return response.content  # ~500-1000 tokens summary instead of 5000+ raw
```

**For Delta:** This is the memory management pattern. Raw paper data (abstracts, metadata) is expensive. Compress to key findings before aggregating. Use a cheap model (DeepSeek haiku) for compression.

---

## 7. Clarification Before Research

```python
class ClarifyWithUser(BaseModel):
    need_clarification: bool
    question: str
    verification: str   # "I'll start research after you respond"

async def clarify_with_user(state: AgentState, config: RunnableConfig) -> Command:
    cfg = Configuration.from_runnable_config(config)
    if not cfg.allow_clarification:
        return Command(goto="write_research_brief")
    
    response = await model.with_structured_output(ClarifyWithUser).invoke(state.messages)
    
    if response.need_clarification:
        # Pause graph, ask user, resume on response
        return Command(goto="__end__", update={"messages": [AIMessage(response.question)]})
    else:
        return Command(goto="write_research_brief")
```

**For Delta:** This is the ASK mode. Before running EXECUTE, ask clarifying questions. The graph pauses at `__end__` when clarification is needed, and resumes when the user responds. With LangGraph's `SqliteSaver` checkpointer, the state is persisted across the pause.

---

## 8. Token Budget Management

```python
def is_token_limit_exceeded(messages: list, model: str, buffer: int = 1000) -> bool:
    """Check if messages exceed model's context limit."""
    token_count = get_buffer_string(messages)  # LangChain utility
    limit = get_model_token_limit(model)
    return token_count + buffer > limit

# In individual_researcher:
if is_token_limit_exceeded(state["researcher_messages"], cfg.research_model):
    # Compress current history before continuing
    state["researcher_messages"] = compress_history(state["researcher_messages"])
```

**For Delta:** Physics papers are long. If DeepSeek's context window fills up during research, compress mid-run rather than crashing. This is a real failure mode.

---

## 9. Tool Infrastructure

Open Deep Research uses Tavily (web search) as the primary tool. For Delta, replace with:

```python
# Delta's tool registry
tools = [
    semantic_scholar_search,     # primary: covers her journals
    semantic_scholar_paper_details,
    semantic_scholar_citations,  # forward citation chaining
    arxiv_search,                # fallback for preprints
    unpaywall_lookup,            # check open access PDF availability
]
```

The ReAct loop pattern is identical — each tool is a Python function registered with LangChain's tool decorator, and the researcher model calls them iteratively.

---

## 10. What Delta Should Copy Directly

| Pattern | File | Notes |
|---------|------|-------|
| `Configuration(BaseModel)` with 4 model roles | `configuration.py` | Copy structure exactly |
| `override_reducer` for state management | `state.py:55-60` | Needed for note accumulation |
| `Send` fan-out to parallel researchers | `deep_researcher.py` | Core of EXECUTE phase |
| `ClarifyWithUser` structured output | `state.py:31-48` | ASK mode interaction |
| `ResearcherOutputState` isolation | `state.py:92-96` | Prevent history explosion |
| Compression after researcher finishes | `deep_researcher.py` | Cheap model + token budget |
| `is_token_limit_exceeded` check | `utils.py` | Prevent context overflow |
| `from_runnable_config` config injection | `configuration.py:236-247` | Config through graph |

---

## 11. What Delta Should Not Copy

- Tavily search (replace with Semantic Scholar)
- Anthropic/OpenAI native web search (expensive and general, not paper-focused)
- MCP config (skip for v1)
- LangGraph Studio / langgraph.json deployment (use Render directly)
- LangSmith evaluation harness (skip for 2 users)

---

## 12. The Supervision Loop — Delta's Core Research Loop

```
ASK: clarify_with_user -> write_research_brief
PLAN: research_supervisor decides subtopics
EXECUTE: Send × N -> individual_researcher × N (parallel)
  Each researcher: search loop with max_iterations limit
  After each researcher: compress output
AGGREGATE: supervisor collects compressed outputs
  -> iterates if gaps remain (up to max_researcher_iterations)
OUTPUT: write_final_report with inline citations
```

This is the exact loop Delta needs. The only Delta-specific changes:
1. Replace Tavily with Semantic Scholar tools
2. Add Unpaywall/CORE for open access PDF links
3. Replace OpenAI models with DeepSeek via LiteLLM
4. Add a "forward citation" tool (check for papers that cite key found papers)
5. Enforce that all citations come from the retrieved set (not hallucinated)
