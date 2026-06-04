# SDS-09: LLM & Model Configuration

## Philosophy

Every LLM call in the codebase goes through `lib/llm.py`. Nothing calls LiteLLM directly except this file. Fallback routing, cost tracking, LangFuse instrumentation, and structured output all live here. Nodes call `get_model("main")` or `get_model("reasoning")` — they never know which provider actually served the request.

---

## Model needs

The system has five distinct inference tasks with different quality/cost tradeoffs:

| Task | Nodes | Quality need | Call frequency |
|------|-------|-------------|----------------|
| Agentic tool-calling loop | `agent` (EXPLORE), `researcher_agent` | High tool calling reliability | Every user turn |
| Structured output (planning) | `clarify`, `plan` | Reliable JSON schema adherence | Once per DEEP run |
| Compress findings | `compress_findings` × N | Medium — summarisation | 5× per DEEP run |
| Long-context synthesis | `synthesize` | Highest — this IS the product | Once per DEEP run |
| Cheap background tasks | memory update, session title | Low | After every session |
| Embeddings | `extract/chunk.py`, semantic cache | Must match vector dimension | Every new paper/doc |

---

## Embedding model — BGE-M3 via Novita

**Model:** `BAAI/bge-m3`
**Provider:** Novita AI
**Price:** $0.01 per 1M tokens
**Dimensions:** 1024
**Why:** Best open multilingual embedding model, excellent retrieval quality, handles physics terminology, extremely cheap.

**Important schema implication:** All `vector()` columns in Neon use dimension 1024:
- `paper_chunks.content_embedding` → `vector(1024)`
- `paper_chunks.specter_embedding` → `vector(768)` (from S2, unchanged)
- `document_chunks.content_embedding` → `vector(1024)`
- `user_documents.content_embedding` → `vector(1024)`
- `semantic_cache.query_embedding` → `vector(1024)`

---

## LLM model candidates — open source only

All models below are open-weight. No OpenAI, no Anthropic, no Gemini.

| Model | HF link | Size | Input $/1M | Output $/1M | Context | Tools | JSON | Reasoning | Agentic score |
|-------|---------|------|-----------|------------|---------|-------|------|-----------|---------------|
| **DeepSeek-V3** | [deepseek-ai/DeepSeek-V3](https://huggingface.co/deepseek-ai/DeepSeek-V3) | 671B MoE (37B active) | $0.27 | $1.10 | 128K | YES | YES | Strong | **8.8/10** |
| **DeepSeek-R1** | [deepseek-ai/DeepSeek-R1](https://huggingface.co/deepseek-ai/DeepSeek-R1) | 671B MoE (37B active) | $0.55 | $2.19 | 128K | Limited | YES | Exceptional (o1-class) | **8.9/10** |
| **Qwen2.5 72B Instruct** | [Qwen/Qwen2.5-72B-Instruct](https://huggingface.co/Qwen/Qwen2.5-72B-Instruct) | 72B | $0.35 | $0.65 | 131K | YES | YES (strict) | Strong | **8.5/10** |
| **Qwen3 72B Instruct** | [Qwen/Qwen3-72B](https://huggingface.co/Qwen/Qwen3-72B) | 72B | $0.40 | $0.80 | 128K | YES | YES (strict) | Strong | **8.6/10** |
| **Qwen3 30B Instruct** | [Qwen/Qwen3-30B](https://huggingface.co/Qwen/Qwen3-30B) | 30B | $0.20 | $0.40 | 128K | YES | YES | Moderate | **8.1/10** |
| **Llama 3.3 70B Instruct** | [meta-llama/Llama-3.3-70B-Instruct](https://huggingface.co/meta-llama/Llama-3.3-70B-Instruct) | 70B | $0.54 | $0.81 | 128K | YES | YES | Light | **8.2/10** |
| **Mistral Small 3.2** | [mistralai/Mistral-Small-3.2-24B-Instruct-2506](https://huggingface.co/mistralai/Mistral-Small-3.2-24B-Instruct-2506) | 24B | $0.05 | $0.15 | 128K | YES | YES | Light | **7.5/10** |

### Agentic score methodology

```
Score = tool_calling×0.35 + structured_output×0.20 + reasoning×0.25 + context_length×0.10 + cost_efficiency×0.10
```

### Known gotchas

**DeepSeek-R1:**
- No system prompt support — all instructions must be in the user prompt. The `synthesize` node must be written accordingly.
- Very long chain-of-thought increases output tokens significantly — budget accordingly.
- Temperature: use 0.5–0.7, not 0.0.

**Qwen3:** Spotty provider availability as of mid-2026. Qwen2.5 72B is the safe fallback.

**Llama 3.3:** Occasionally hallucinates optional tool parameters (fills in defaults not in schema). Needs stricter tool definitions.

**Mistral Small 3.2:** Cheapest with solid tool calling. Use only for background tasks — not for complex agentic loops.

---

## Model assignments per node

| Node | Primary | Fallback 1 | Fallback 2 | Model tier |
|------|---------|-----------|-----------|------------|
| `agent` (EXPLORE) | DeepSeek-V3 (Together) | Qwen2.5 72B (Nebius) | deepseek-chat (direct) | `main` |
| `clarify`, `plan` | DeepSeek-V3 (Together) | Qwen2.5 72B (Nebius) | deepseek-chat (direct) | `main` |
| `researcher_agent` × N | Qwen2.5 72B (Nebius) | Llama 3.3 70B (HF) | deepseek-chat (direct) | `fast` |
| `compress_findings` × N | Qwen2.5 72B (Nebius) | Mistral Small 3.2 (HF) | deepseek-chat (direct) | `fast` |
| `synthesize` | DeepSeek-R1 (Nebius) | DeepSeek-V3 (Together) | deepseek-reasoner (direct) | `reasoning` |
| memory update, title | Mistral Small 3.2 (HF) | Qwen3 30B (Together) | deepseek-chat (direct) | `cheap` |

### Provider priority during credit phases

**Phase 1 — HuggingFace $25 + Nebius $50:**
- DeepSeek-V3: Together AI ($0.27/$1.10)
- DeepSeek-R1: Nebius ($0.40/$1.60)
- Qwen2.5 72B: Nebius or HuggingFace
- Mistral Small 3.2: HuggingFace Inference API

**Phase 2 — After credits exhaust:**
- Main: `deepseek/deepseek-chat` direct ($0.27/$1.10)
- Reasoning: `deepseek/deepseek-reasoner` direct ($0.55/$2.19)
- Fast/cheap: `deepseek/deepseek-chat` (cache hits at $0.07/1M)

---

## LiteLLM through LangChain

`langchain_litellm.ChatLiteLLM` gives full LangChain compatibility: `.with_structured_output()`, `.bind_tools()`, callback handlers. LiteLLM handles provider routing and fallback transparently — nodes never know which provider served the request.

```python
# server/lib/llm.py
from langchain_litellm import ChatLiteLLM
from langfuse.callback import CallbackHandler

langfuse_handler = CallbackHandler()  # wired once here, applies everywhere

MODELS = {
    "main": {
        "model": "together_ai/deepseek-ai/DeepSeek-V3",
        "fallbacks": [
            "nebius/Qwen/Qwen2.5-72B-Instruct",
            "deepseek/deepseek-chat",
        ],
    },
    "fast": {
        "model": "nebius/Qwen/Qwen2.5-72B-Instruct",
        "fallbacks": [
            "huggingface/meta-llama/Llama-3.3-70B-Instruct",
            "deepseek/deepseek-chat",
        ],
    },
    "reasoning": {
        "model": "nebius/deepseek-ai/DeepSeek-R1",
        "fallbacks": [
            "together_ai/deepseek-ai/DeepSeek-V3",
            "deepseek/deepseek-reasoner",
        ],
    },
    "cheap": {
        "model": "huggingface/mistralai/Mistral-Small-3.2-24B-Instruct-2506",
        "fallbacks": [
            "together_ai/Qwen/Qwen3-30B",
            "deepseek/deepseek-chat",
        ],
    },
}

def get_model(tier: str = "main") -> ChatLiteLLM:
    cfg = MODELS[tier]
    return ChatLiteLLM(
        model=cfg["model"],
        fallbacks=cfg["fallbacks"],
        callbacks=[langfuse_handler],
        max_retries=2,
        streaming=True,
    )
```

### Structured output pattern

Nodes that produce structured data use `.with_structured_output()`. This wraps the model in LangChain's structured output pipeline using JSON mode under the hood.

```python
# In clarify node
model = get_model("main").with_structured_output(ClarificationQuestions)
result: ClarificationQuestions = await model.ainvoke([HumanMessage(content=prompt)])
```

**DeepSeek-R1 exception:** R1 does not support system prompts. The `synthesize` node must use a user-only prompt structure:

```python
# Normal nodes
messages = [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]

# synthesize node (R1 only)
messages = [HumanMessage(content=f"{system_instructions}\n\n{user_prompt}")]
```

### Tool-calling pattern

Nodes in ReAct loops use `.bind_tools()`:

```python
# In agent node (EXPLORE) and researcher_agent node (DEEP)
model = get_model("main").bind_tools(EXPLORE_TOOLS)
response = await model.ainvoke(state["messages"])
```

---

## Cost estimates

### Per EXPLORE turn (average)
- 1 LLM call: ~2K input + ~1K output = $0.27 + $1.10 per 1M = ~$0.003 per turn
- 2–4 tool calls: adds ~$0.002 in embedding/API costs
- **Total per EXPLORE turn: ~$0.005**

### Per DEEP run (average)
- Clarify + plan: ~$0.003
- 5 researchers × ReAct loop (~3 turns each): ~$0.05
- 5 compress calls: ~$0.02
- Synthesize (R1, ~30K tokens): ~$0.08
- **Total per DEEP run: ~$0.15**

### Monthly estimate (2 users, moderate use)
- 100 EXPLORE turns/month: ~$0.50
- 10 DEEP runs/month: ~$1.50
- Background tasks: ~$0.20
- Embeddings (BGE-M3): ~$0.01
- **Total: ~$2.20/month after credits**

---

## Environment variables

```bash
# LiteLLM providers
TOGETHER_AI_API_KEY=...
NEBIUS_API_KEY=...
HUGGINGFACE_API_KEY=...
DEEPSEEK_API_KEY=...           # fallback after credits

# Embeddings
NOVITA_API_KEY=...             # for BGE-M3 embeddings

# LangFuse (already in SDS-01)
LANGFUSE_PUBLIC_KEY=...
LANGFUSE_SECRET_KEY=...
LANGFUSE_HOST=https://cloud.langfuse.com
```
