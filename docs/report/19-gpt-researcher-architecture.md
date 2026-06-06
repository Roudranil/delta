# Report 19: GPT Researcher — Production Research Agent Architecture

Source: `reference-repos/gpt-researcher`

---

## Executive Summary

GPT Researcher is a mature, production-grade research agent (3+ years old, 20k+ GitHub stars). It's the most feature-complete of the reference repos but also the most complex. The core is a `GPTResearcher` class that orchestrates: agent role selection, parallel sub-query search, content scraping, context management, and report generation. It does NOT use LangGraph — it's a custom async Python loop. Delta should study its data flow and copy specific utilities (context management, source deduplication, cost tracking), but use LangGraph instead of GPT Researcher's custom loop.

---

## 1. Architecture Overview

```
GPTResearcher (main class)
  ├── Config               — settings from env vars + config files
  ├── Memory               — vector store wrapper for retrieved content
  ├── ResearchConductor    — orchestrates search and scraping
  ├── ReportGenerator      — writes sections, conclusions, TOC
  ├── ContextManager       — manages context window budget
  ├── BrowserManager       — web scraping, content extraction
  ├── SourceCurator        — deduplicates and ranks sources
  ├── DeepResearchSkill    — recursive depth-first research tree
  └── ImageGenerator       — optional image search + embed
```

**Key design choice:** Everything is a skill/manager attached to the main class instance. State is stored on `self.*`. No LangGraph, no state machines — just async Python with explicit orchestration.

---

## 2. GPTResearcher Class Init

```python
class GPTResearcher:
    def __init__(
        self,
        query: str,
        report_type: str = ReportType.ResearchReport.value,   # research, detailed, deep, custom, ...
        report_source: str = ReportSource.Web.value,           # web, local, hybrid
        tone: Tone = Tone.Objective,
        source_urls: list[str] | None = None,    # constrain to specific URLs
        query_domains: list[str] | None = None,  # constrain to specific domains
        visited_urls: set | None = None,          # dedup across recursive calls
        context: list | None = None,             # pre-loaded research context
        max_subtopics: int = 5,
        mcp_configs: list[dict] | None = None,
        mcp_strategy: str = "fast",              # fast | deep | disabled
        ...
    ):
        self.research_costs = 0.0
        self.step_costs: dict[str, float] = {}
        self._current_step: str = "general"
        
        # Component initialization
        self.research_conductor = ResearchConductor(self)
        self.report_generator = ReportGenerator(self)
        self.context_manager = ContextManager(self)
        self.scraper_manager = BrowserManager(self)
        self.source_curator = SourceCurator(self)
        if report_type == ReportType.DeepResearch.value:
            self.deep_researcher = DeepResearchSkill(self)
```

**For Delta:** The `visited_urls` set passed to child researchers prevents re-fetching the same paper in recursive calls. Equivalent for Delta: `visited_paper_ids: set[str]` — deduplicate by DOI or Semantic Scholar paper ID across parallel searchers.

---

## 3. Research Workflow

```python
async def conduct_research(self, on_progress=None):
    # Deep research is separate
    if self.report_type == ReportType.DeepResearch.value:
        return await self._handle_deep_research(on_progress)
    
    # 1. Agent selection: what kind of researcher should I be?
    if not (self.agent and self.role):
        self.agent, self.role = await choose_agent(
            query=self.query, cfg=self.cfg, ...
        )
    
    # 2. Run research
    self.context = await self.research_conductor.conduct_research()
    
    # 3. Pre-generate images if enabled
    if self.image_generator.is_enabled():
        self.available_images = await self.image_generator.plan_and_generate_images(...)
    
    return self.context
```

**Agent selection (`choose_agent`):** GPT Researcher first asks the LLM "what kind of expert would research this topic?" The LLM returns `agent="Finance Analyst"` or `agent="Materials Scientist"` and a `role` description. This role is injected into all subsequent researcher prompts. For Delta: inject the physics domain context directly into system prompts — her domain is always experimental physics, so don't waste a call on agent selection.

---

## 4. Report Types

```python
class ReportType(Enum):
    ResearchReport = "research_report"    # standard single-topic
    DetailedReport = "detailed_report"    # deeper, longer
    SubtopicReport = "subtopic_report"    # one section of a larger report
    DeepResearch = "deep_research"        # recursive multi-level
    CustomReport = "custom_report"        # user-defined prompt
    ReportFromSources = "report_from_sources"  # from provided URLs only
```

For Delta, the relevant types are:
- `ResearchReport` -> standard literature survey
- `DeepResearch` -> comprehensive review (expensive)
- `ReportFromSources` -> if the user uploads PDFs directly

---

## 5. DeepResearchSkill — Recursive Research Tree

```python
class DeepResearchSkill:
    def __init__(self, researcher: GPTResearcher):
        self.breadth = 4      # how many subtopics at each level
        self.depth = 2        # how many levels deep
        self.concurrency_limit = 4  # parallel researchers

    async def run(self, on_progress=None):
        # Level 1: generate `breadth` subtopics from main query
        # Level 2: for each subtopic, generate `breadth` sub-subtopics
        # Each leaf node is a separate GPTResearcher instance
        # All share the same `visited_urls` set for deduplication
```

This creates up to `breadth^depth` = 16 parallel researchers for a `depth=2, breadth=4` run. Each researcher independently fetches and processes content. Results are aggregated bottom-up.

**For Delta:** This recursive tree is powerful but expensive. For her use case (find papers on a specific technique), depth=1 with breadth=5 subtopics is sufficient. Don't implement depth > 1 in v1.

---

## 6. Context Manager (`skills/context_manager.py`)

The ContextManager handles the critical problem: too much scraped content, limited context window.

```python
class ContextManager:
    async def get_similar_written_contents_by_draft_section_titles(
        self,
        current_subtopic: str,
        draft_section_titles: list[str],
        written_contents: list[dict],
        max_results: int = 10,
    ) -> list[str]:
        # Uses vector similarity to find previously written content
        # that's relevant to the current section being written
        # Prevents duplication across sections of a long report
```

Key: when writing a multi-section report, each section writer gets only the previously written sections that are similar to the current one — not all sections. This prevents context bloat in long reports.

**For Delta:** When writing a literature review with multiple sections (Background -> Current State -> Open Problems -> Recommendations), pass only relevant prior sections to each new section's prompt.

---

## 7. Source Curator (`skills/curator.py`)

```python
class SourceCurator:
    async def curate_sources(
        self,
        query: str,
        sources: list[dict],
        max_results: int = 10,
    ) -> list[dict]:
        # Deduplicate by URL
        # Score sources by relevance to query
        # Return top-K after deduplication
```

For Delta: replace URL-based deduplication with DOI-based deduplication. Papers appear across multiple sources (Semantic Scholar, CORE, OpenAlex) with the same DOI.

---

## 8. Cost Tracking

```python
def add_costs(self, cost: float) -> None:
    """Attribute cost to current step."""
    self.research_costs += cost
    step = self._current_step
    self.step_costs[step] = self.step_costs.get(step, 0.0) + cost
```

Usage:
```python
self._current_step = "summarization"
# ... call cheap model ...
self._current_step = "report_writing"
# ... call expensive model ...

print(researcher.get_step_costs())
# {"agent_selection": 0.001, "research": 0.08, "report_writing": 0.02}
```

**For Delta:** Copy this per-step cost tracking. Essential for staying within the $30/month budget. Log costs per session so the developer can see where money is going.

---

## 9. MCP Strategy

```python
mcp_strategy: str = "fast"   # fast | deep | disabled

# "fast": run MCP once for original query (1 MCP call)
# "deep": run MCP for all sub-queries (N MCP calls)  
# "disabled": skip MCP entirely

def _resolve_mcp_strategy(self, mcp_strategy, mcp_max_iterations):
    # Backwards compatible resolution with deprecation warnings
    # priority: parameter > legacy param > config > default "fast"
```

GPT Researcher added MCP support as an optional retriever. For Delta v1: ignore MCP entirely. The Semantic Scholar API is better for her domain than any general MCP search.

---

## 10. Prompt Family System

```python
self.prompt_family = get_prompt_family(
    prompt_family or self.cfg.prompt_family, self.cfg
)
```

GPT Researcher supports multiple prompt families (different research styles) selectable via config. This allows: academic style, journalism style, legal research style, etc.

**For Delta:** One prompt family: academic physics literature review. No need for a family abstraction in v1.

---

## 11. WebSocket Streaming

```python
class GPTResearcher:
    def __init__(self, ..., websocket=None, ...):
        self.websocket = websocket
    
    async def _log_event(self, event_type: str, **kwargs):
        if self.log_handler:
            await self.log_handler.on_research_step(kwargs.get('step'), kwargs.get('details'))
```

GPT Researcher streams progress via WebSocket. Each research step emits a structured event. For Delta: SSE (Server-Sent Events) via FastAPI is simpler than WebSocket and sufficient for streaming research progress to the browser.

---

## 12. Key Utilities Worth Copying

### 12.1 Research ID generation
```python
def _generate_research_id(self) -> str:
    unique_str = f"{self.query}_{time.time()}"
    return f"research_{hashlib.md5(unique_str.encode()).hexdigest()[:12]}"
```

Useful for linking SSE stream IDs to session state.

### 12.2 Report type enum
```python
class ReportType(Enum):
    ResearchReport = "research_report"
    DeepResearch = "deep_research"
    ReportFromSources = "report_from_sources"
```

Copy this enum for Delta's mode system.

### 12.3 Tone enum
```python
class Tone(Enum):
    Objective = "objective"
    Formal = "formal"
    Analytical = "analytical"
    Informative = "informative"
```

Inject tone into final report prompts. For her: always "objective" + "academic".

---

## 13. What Delta Should Copy from GPT Researcher

| Feature | Application |
|---------|-------------|
| Per-step cost tracking | `add_costs()` + `step_costs` dict |
| `visited_paper_ids` deduplication | DOI-based, passed to parallel searchers |
| `ContextManager` section similarity | Prevent duplication in multi-section reports |
| `SourceCurator` deduplication logic | DOI-based, not URL-based |
| `on_progress` callback pattern | Hook for SSE streaming |
| `ReportType` enum | Delta's mode taxonomy |
| `Tone` enum + injection | Report style control |
| `breadth` + `depth` limits | Bound research scope |

---

## 14. What Delta Should Ignore

- Custom async loop (use LangGraph instead)
- `choose_agent()` call (waste for a domain-specific agent)
- WebSocket (use SSE)
- Image generator (irrelevant for paper research)
- BrowserManager / content scraping (use APIs)
- Multi-source URL support (use paper APIs)
- MCP configs (v1: skip)
- Prompt family system (one family is fine)
- LangChain document loaders (GPT Researcher doesn't use LangGraph but uses other LangChain utilities — Delta's tools should be simpler custom functions)
