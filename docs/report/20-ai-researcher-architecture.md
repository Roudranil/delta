# Report 20: AI-Researcher — End-to-End Scientific Research Automation

Source: `reference-repos/AI-Researcher`

---

## Executive Summary

AI-Researcher (HKUDS, NeurIPS 2025 Spotlight) is a system for end-to-end autonomous scientific research: literature review → idea generation → algorithm design → code implementation → experimentation → paper writing. It uses Docker-containerized execution environments, the `MetaChain` agent loop (LiteLLM-based), and a multi-agent pipeline orchestrated by a global state machine. It is the most ambitious reference repo but the least applicable to Delta — it automates paper *writing*, not paper *discovery*. However, MetaChain and the fn-call conversion layer are directly useful.

---

## 1. Architecture Overview

```
main_ai_researcher.py / web_ai_researcher.py
    │
    ▼
global_state.py          ← shared state across all agents
    │
    ├── research_agent/
    │     ├── run_infer_plan.py     ← Level 1: given idea, implement it
    │     ├── run_infer_idea.py     ← Level 2: given papers, generate idea + implement
    │     └── inno/
    │           ├── core.py         ← MetaChain: the agent loop
    │           ├── types.py        ← Agent, Response, Result types
    │           ├── fn_call_converter.py  ← non-fn-call model compatibility
    │           └── main.py         ← research agent orchestration
    │
    └── paper_agent/
          ├── writing.py            ← paper writing orchestration
          ├── section_composer.py   ← writes individual sections
          ├── tex_writer.py         ← LaTeX output
          └── abstract_composing.py
```

**Key context:** AI-Researcher is designed for ML researchers who want to implement a new algorithm from papers. The "workplace" is a Docker container where the agent writes code, runs experiments, analyzes results. This is irrelevant for Delta's use case (paper discovery and literature review for experimental physics, not ML implementation).

---

## 2. MetaChain — The Agent Loop (`research_agent/inno/core.py`)

MetaChain is AI-Researcher's custom agent loop, essentially a reimplementation of the OpenAI Swarm pattern using LiteLLM:

```python
class MetaChain:
    def run(
        self,
        agent: Agent,
        messages: List,
        context_variables: dict = {},
        model_override: str = None,
        max_turns: int = float("inf"),
        execute_tools: bool = True,
    ) -> Response:
        active_agent = agent
        history = copy.deepcopy(messages)
        
        while len(history) - init_len < max_turns and active_agent:
            completion = self.get_chat_completion(
                agent=active_agent,
                history=history,
                context_variables=context_variables,
                ...
            )
            message = completion.choices[0].message
            history.append(json.loads(message.model_dump_json()))
            
            if not message.tool_calls or not execute_tools:
                break
            
            partial_response = self.handle_tool_calls(
                message.tool_calls, active_agent.functions, context_variables, ...
            )
            history.extend(partial_response.messages)
            context_variables.update(partial_response.context_variables)
            if partial_response.agent:
                active_agent = partial_response.agent  # agent handoff
        
        return Response(messages=history[init_len:], agent=active_agent, context_variables=context_variables)
```

**What this is:** A ReAct loop with:
- Agent handoff (switch active_agent mid-conversation)
- Context variables (shared mutable state across turns)
- Tool call handling with error recovery
- Both sync (`run`) and async (`run_async`) variants

**For Delta:** LangGraph replaces all of this. LangGraph is better because:
- State is typed and explicit
- The graph structure is visible and debuggable
- Checkpointing is built in
- Fan-out with `Send` is first-class
- No custom loop maintenance

Do NOT copy MetaChain. Use LangGraph.

---

## 3. Function Call Converter (`fn_call_converter.py`)

This is the one genuinely useful utility in AI-Researcher for Delta:

```python
# For models that don't support native function calling (some DeepSeek modes, older models)

def convert_tools_to_description(tools: list) -> str:
    """Convert OpenAI tool schema to text description for models without fn-call support."""
    # Returns: "Available tools:\n1. tool_name(param1, param2): description\n..."

def convert_fn_messages_to_non_fn_messages(messages: list) -> list:
    """Convert tool_call messages to plain text format for non-fn-call models."""
    # ToolCall messages → "I called X(args) and got: result"

def convert_non_fncall_messages_to_fncall_messages(messages: list, tools: list) -> list:
    """Parse text tool calls from non-fn-call model output back into ToolCall format."""
    # Reverse: model's text output → ToolCall objects
```

**Why this matters for Delta:** DeepSeek v3 via LiteLLM supports function calling natively. But if you ever switch to a cheaper model that doesn't (e.g., some smaller models), this converter layer lets you maintain the same tool interface. LiteLLM actually handles most of this automatically, but understanding the pattern is useful.

---

## 4. Agent Types and Handoff

```python
@dataclass
class Agent:
    name: str = "Agent"
    model: str = "gpt-4o"
    instructions: Union[str, Callable] = "You are a helpful agent."
    functions: List[AgentFunction] = field(default_factory=list)
    tool_choice: str = "auto"             # or "required"
    parallel_tool_calls: bool = True
    examples: Optional[Union[list, Callable]] = None  # few-shot examples

# Agent handoff: a tool returns another Agent
def transfer_to_writer_agent() -> Agent:
    return WriterAgent()
```

When a tool function returns an `Agent` object instead of a string, MetaChain switches the active agent. This is how AI-Researcher hands off from ResearchAgent → WriterAgent → ReviewAgent.

**For Delta:** LangGraph's `Command(goto=...)` and conditional edges are cleaner than this handoff pattern. Don't copy the Agent dataclass pattern.

---

## 5. Context Variables

```python
# Shared mutable dict passed to all agents and tools
context_variables = {
    "workplace_path": "/workplace",
    "experiment_results": {},
    "paper_title": "...",
    "research_field": "vq",
}

# Tool functions can read/modify context_variables:
def run_experiment(experiment_name: str, context_variables: dict) -> Result:
    path = context_variables["workplace_path"]
    # ... run experiment ...
    context_variables["experiment_results"][experiment_name] = results
    return Result(value="Experiment complete", context_variables=context_variables)
```

**For Delta:** This is equivalent to LangGraph's state dict. The key difference: LangGraph state is typed, versioned, and checkpointed. Context variables are a mutable dict with no structure. Use LangGraph state instead.

---

## 6. The Research Pipeline (What It Actually Does)

### Level 1: Implement a given idea
```
run_infer_plan.py
  → Literature Review Agent: fetch and analyze reference papers
  → Design Agent: plan algorithm architecture
  → Implementation Agent: write code in Docker container
  → Experiment Agent: run experiments, collect metrics
  → Analysis Agent: interpret results
  → Refinement Agent: fix bugs, improve performance
```

### Level 2: Generate idea then implement
```
run_infer_idea.py
  → Same as Level 1 but with:
  → Idea Generation Agent: given papers, propose novel approach
```

### Paper Writing (separate pipeline)
```
paper_agent/writing.py
  → Abstract Agent: write abstract from results
  → Introduction Agent: write intro from background
  → Related Work Agent: write related work section
  → Methodology Agent: write method description
  → Experiments Agent: write experiments section
  → Conclusion Agent: write conclusion
  → LaTeX compilation and PDF generation
```

**For Delta:** The paper writing pipeline's section-by-section approach is worth noting. When Delta generates a literature review, break it into sections and write each separately: Background → Current Work → Methods Comparison → Open Problems → Recommendations. Each section gets its own prompt with the relevant paper subset.

---

## 7. Docker Execution Environment

AI-Researcher runs all experiments inside Docker containers:
```python
# docker/tcp_server.py — TCP server inside the container
# Receives commands from the host research agent
# Executes Python code, returns stdout/stderr

# global_state.py
DOCKER_WORKPLACE_NAME = "workplace_paper"
BASE_IMAGES = "tjbtech1/airesearcher:v1"
CONTAINER_NAME = "paper_eval"
```

**For Delta:** Completely irrelevant. Delta doesn't run experiments. Skip entirely.

---

## 8. Benchmark Suite

AI-Researcher includes a benchmark for evaluating autonomous research quality:
- Categories: CV, NLP, DM, IR (not physics)
- Tasks: implement given idea (L1), generate idea from papers + implement (L2)
- Metrics: Novelty, Experimental Comprehensiveness, Theoretical Foundation, Result Analysis, Writing Quality
- 5 evaluation dimensions using specialized evaluator agents

The benchmark construction pipeline is open-source: `benchmark_collection/` contains scripts for crawling papers, creating innovation graphs, and generating tasks.

**For Delta:** The evaluation framework is interesting academically. For Delta's practical use case (helping a PhD student find papers), the right evaluation is simpler: did she find the papers she needed? Did the citations come from real papers in her domain? Not for v1.

---

## 9. LiteLLM Integration Pattern

```python
# research_agent/constant.py
API_BASE_URL = "https://openrouter.ai/api/v1"  # or custom

# core.py
from litellm import completion, acompletion

create_params = {
    "model": model_override or agent.model,
    "messages": messages,
    "tools": tools or None,
    "tool_choice": agent.tool_choice,
    "stream": stream,
    "base_url": API_BASE_URL,
}
completion_response = await acompletion(**create_params)
```

This is exactly how Delta should call LiteLLM. The `base_url` override allows routing through OpenRouter or a custom proxy. For Delta with DeepSeek: `base_url = "https://api.deepseek.com"`.

---

## 10. Retry Logic

```python
@retry(
    stop=stop_after_attempt(6),
    wait=wait_exponential(multiplier=2, min=30, max=1200),
    retry=should_retry_error,
)
async def get_chat_completion_async(self, ...):
    ...

def should_retry_error(retry_state: RetryCallState):
    exception = retry_state.outcome.exception()
    error_msg = str(exception).lower()
    return any([
        "connection error" in error_msg,
        "rate limit" in error_msg,
        "too many requests" in error_msg,
        "overloaded" in error_msg,   # Anthropic-specific
        "error code: 429" in error_msg,
    ])
```

**Copy this retry logic for Delta.** DeepSeek has rate limits; without retry logic, a long research run will fail mid-way on a 429. Use `tenacity` with exponential backoff + jitter.

---

## 11. Context Truncation Under Pressure

```python
async def try_completion_with_truncation(self, agent, history, ...):
    try:
        return await self.get_chat_completion_async(agent, history, ...)
    except (ContextWindowExceededError, BadRequestError) as e:
        if "context length" in str(e).lower():
            # Truncate the last message to 10,000 tokens
            last_message = history[-1]
            tokens = encode_string_by_tiktoken(last_message['content'])
            last_message['content'] = decode_tokens_by_tiktoken(tokens[:10000])
            # Retry with truncated message
            return await self.get_chat_completion_async(agent, history, ...)
        raise e
```

**For Delta:** This is a real failure mode. A paper abstract that's unusually long, or a cluster of abstracts pasted together, can overflow the context. Handle `ContextWindowExceededError` gracefully by truncating rather than crashing.

---

## 12. What Delta Should Copy from AI-Researcher

| Pattern | Application |
|---------|-------------|
| `tenacity` retry with `should_retry_error` | Resilient DeepSeek calls |
| Context truncation on `ContextWindowExceededError` | Graceful overflow handling |
| LiteLLM `acompletion` with `base_url` | Delta's LLM call pattern |
| Section-by-section report writing | Literature review structure |
| `fn_call_converter.py` pattern | Fallback for models without native fn-call |

---

## 13. What Delta Should Ignore

- MetaChain agent loop (use LangGraph)
- Agent handoff pattern (use LangGraph edges)
- Context variables dict (use LangGraph typed state)
- Docker execution environment (irrelevant)
- Paper writing LaTeX pipeline (wrong use case)
- Benchmark suite (irrelevant for 2 users)
- Idea generation pipeline (wrong use case)
- ML-specific research domains (CV, NLP, DM — wrong field)

---

## 14. Summary Assessment

AI-Researcher is impressive for what it does (fully autonomous ML research), but it solves a different problem than Delta. Delta is a literature survey and paper discovery tool for a non-technical user in experimental physics. AI-Researcher is an autonomous ML researcher that writes papers.

The useful extractions:
1. **Retry logic with tenacity** — copy directly
2. **Context truncation handling** — copy directly  
3. **LiteLLM acompletion pattern** — already planned for Delta
4. **Section-by-section report writing** — apply to Delta's output stage

The rest is scope creep for Delta's actual use case.
