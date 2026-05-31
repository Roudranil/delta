# Pi Agent: AI/LLM Provider Layer

## Overview

The pi AI layer implements a **multi-provider, message-format-agnostic streaming architecture** that normalizes interactions across 9+ LLM provider APIs.

---

## 1. Core Types & Message Format

### Canonical Message Types (`types.ts`)

**UserMessage**: Client input
```
role: "user"
content: string | (TextContent | ImageContent)[]
timestamp: number
```

**AssistantMessage**: LLM output
```
role: "assistant"
content: (TextContent | ThinkingContent | ToolCall)[]
api: Api                           // which API backend
provider: Provider                 // which provider
model: string                      // model ID requested
responseModel?: string             // actual model returned (for routing)
usage: Usage                       // token counts + cost
stopReason: StopReason             // "stop" | "length" | "toolUse" | "error" | "aborted"
errorMessage?: string              // if error/aborted
diagnostics?: AssistantMessageDiagnostic[]
timestamp: number
```

**ToolResultMessage**: Tool execution output
```
role: "toolResult"
toolCallId: string
toolName: string
content: (TextContent | ImageContent)[]
details?: TDetails
isError: boolean
timestamp: number
```

### Content Block Types

- **TextContent**: `{ type: "text", text: string, textSignature?: string }`
- **ThinkingContent**: `{ type: "thinking", thinking: string, thinkingSignature?: string, redacted?: boolean }`
- **ImageContent**: `{ type: "image", data: string (base64), mimeType: string }`
- **ToolCall**: `{ type: "toolCall", id: string, name: string, arguments: Record<string, any>, thoughtSignature?: string }`

### Token Usage & Cost Tracking

```typescript
usage: {
  input: number           // input tokens (excl. cache)
  output: number          // output tokens
  cacheRead: number       // tokens from cache hits
  cacheWrite: number      // tokens written to cache
  totalTokens: number     // sum
  cost: {
    input: number         // (input / 1_000_000) * model.cost.input
    output: number
    cacheRead: number
    cacheWrite: number
    total: number
  }
}
```

### Stop Reasons

```typescript
type StopReason = "stop" | "length" | "toolUse" | "error" | "aborted";
```

All errors are **encoded in the stream, never thrown**. Even on error, you receive a complete `AssistantMessage` with `stopReason: "error"` and `errorMessage`.

---

## 2. API Registry & Model System

### API Provider Registry (`api-registry.ts`)

Pi's registry maps **one Api type → one provider implementation**:

```typescript
interface ApiProvider<TApi extends Api, TOptions extends StreamOptions> {
  api: TApi;  // "anthropic-messages", "openai-completions", etc.
  stream: StreamFunction<TApi, TOptions>;
  streamSimple: StreamFunction<TApi, SimpleStreamOptions>;
}

registerApiProvider({ api: "anthropic-messages", stream: streamAnthropic, ... });
getApiProvider("anthropic-messages")  // Returns the provider
```

### Model Registry (`models.ts`)

Models are organized: Provider → ModelId → Model<Api>

```typescript
getModel("anthropic", "claude-opus-4-1")  // Returns Model<"anthropic-messages">
getProviders()                             // Returns known provider names
getModels("openai")                        // Returns all OpenAI models
```

Model metadata includes:
- API type (routes to provider)
- Base URL, context window, max tokens
- Cost per million tokens (input/output/cache read/cache write)
- Thinking level support & mapping
- Input types (text, image, etc.)

---

## 3. Streaming Architecture

### Event Stream Protocol (`utils/event-stream.ts`)

```typescript
class EventStream<T, R=T> implements AsyncIterable<T> {
  push(event: T): void     // Enqueue event
  end(result?: R): void    // Mark stream done
  result(): Promise<R>     // Get final result (blocks until done)
  [Symbol.asyncIterator]() // Async iteration support
}

class AssistantMessageEventStream extends EventStream<
  AssistantMessageEvent,
  AssistantMessage
> {}
```

### Event Types

```typescript
type AssistantMessageEvent =
  | { type: "start"; partial: AssistantMessage }
  | { type: "text_start"; contentIndex: number; partial: ... }
  | { type: "text_delta"; contentIndex: number; delta: string; partial: ... }
  | { type: "text_end"; contentIndex: number; content: string; partial: ... }
  | { type: "thinking_start"; contentIndex: number; partial: ... }
  | { type: "thinking_delta"; contentIndex: number; delta: string; partial: ... }
  | { type: "thinking_end"; contentIndex: number; content: string; partial: ... }
  | { type: "toolcall_start"; contentIndex: number; partial: ... }
  | { type: "toolcall_delta"; contentIndex: number; delta: string; partial: ... }
  | { type: "toolcall_end"; contentIndex: number; toolCall: ToolCall; partial: ... }
  | { type: "done"; reason: "stop" | "length" | "toolUse"; message: AssistantMessage }
  | { type: "error"; reason: "error" | "aborted"; error: AssistantMessage }
```

Every event includes `partial: AssistantMessage` showing cumulative state.

### Usage Pattern

```typescript
const stream = stream(model, context, options);
for await (const event of stream) {
  if (event.type === "text_delta") console.log(event.delta);
}
const finalMessage = await stream.result();
```

---

## 4. Provider Implementations

### Anthropic Messages (`anthropic.ts`)

**API**: `anthropic-messages`

Features:
- **Auth modes**: API key, OAuth token, GitHub Copilot Bearer token, Cloudflare AI Gateway
- **Extended Thinking**: Adaptive (Opus 4.6+) or Budget-based (older models)
- **Tool Streaming**: via `fine-grained-tool-streaming-2025-05-14` beta header
- **Prompt Caching**: Ephemeral + 24-hour retention
- **Thinking Display**: `"summarized"` (default) or `"omitted"` (encrypted signature preserved)

Stop reason mapping:
```
"end_turn" → "stop"
"max_tokens" → "length"
"tool_use" → "toolUse"
"refusal" / "sensitive" → "error"
```

Custom SSE parser handles CRLF, CR, LF line endings, multi-line data values.

### OpenAI Chat Completions (`openai-completions.ts`)

**API**: `openai-completions`

**Compat auto-detection** from baseUrl + provider name enables ONE implementation to support 30+ OpenAI-compatible services:

```typescript
type ResolvedOpenAICompletionsCompat = {
  supportsStore?: boolean
  supportsDeveloperRole?: boolean
  supportsReasoningEffort?: boolean
  maxTokensField?: "max_completion_tokens" | "max_tokens"
  requiresToolResultName?: boolean
  requiresAssistantAfterToolResult?: boolean
  thinkingFormat?: "openai" | "openrouter" | "deepseek" | "zai" | "qwen"
  cacheControlFormat?: "anthropic"  // for OpenRouter Anthropic passthrough
}
```

Reasoning support per provider:
- OpenAI: `reasoning_effort: "minimal" | "low" | "medium" | "high" | "xhigh"`
- DeepSeek: `thinking: { type: "enabled" | "disabled" }` + `reasoning_effort`
- OpenRouter: `reasoning: { effort: "..." }`
- Together AI: `reasoning: { enabled: bool }` + optional `reasoning_effort`

### Google Generative AI (`google.ts`)

**API**: `google-generative-ai`

- **Thought Signatures**: Base64-encoded opaque payloads for context replay
- Tool calls: Auto-generates IDs if missing
- Thinking via `thinkingConfig: { budgetTokens, level? }`

### Mistral Conversations (`mistral.ts`)

**API**: `mistral-conversations`

- Tool call IDs: 9 characters max
- Reasoning via `reasoningEffort: "none" | "high"`

### Amazon Bedrock Converse (`amazon-bedrock.ts`)

**API**: `bedrock-converse-stream`

Auth modes:
1. SigV4 (default)
2. Bearer token: `AWS_BEARER_TOKEN_BEDROCK` env var
3. AWS_PROFILE, IAM keys
4. ECS task roles, IRSA

- Extended Thinking: Budget-based (adjusts `maxTokens` dynamically)
- Prompt Caching: `cachePointType` + `cacheTTL`
- HTTP Proxy Support

### Other Providers

- **Azure OpenAI Responses**: Deployment name mapping via `AZURE_OPENAI_DEPLOYMENT_NAME_MAP`
- **OpenAI Codex Responses**: Legacy model support, WebSocket option
- **Google Vertex AI**: ADC auth, identical to google-generative-ai otherwise
- **Cloudflare**: Template URL substitution `{CLOUDFLARE_ACCOUNT_ID}` from env vars

---

## 5. Message Transformation

### Cross-Provider Normalization (`transform-messages.ts`)

When replaying messages across providers, `transformMessages()` handles:

#### Image Downgrade
If `model.input` doesn't include `"image"`, replace image blocks with placeholders.

#### Thinking Block Handling

| Condition | Action |
|-----------|--------|
| Redacted thinking, same provider+model | Keep as redacted_thinking block |
| Redacted thinking, different provider | Drop |
| Thinking with signature, same provider+model | Keep thinking block + signature |
| Thinking with signature, different provider | Convert to text |
| Empty thinking | Always drop |
| Thinking without signature (aborted) | Convert to text without tags |

#### Tool Call ID Normalization

- Anthropic: `^[a-zA-Z0-9_-]+$` max 64 chars
- Mistral: 9 chars
- OpenAI Responses: 450+ chars with `|` characters → hash-based shortening

#### Synthetic Tool Results for Orphaned Calls

If assistant message has tool calls but no corresponding tool result:
```typescript
{
  role: "toolResult",
  toolCallId: toolCall.id,
  toolName: toolCall.name,
  content: [{ type: "text", text: "No result provided" }],
  isError: true,
  timestamp: Date.now(),
}
```

#### Error/Aborted Message Filtering

Assistant messages with `stopReason === "error"` or `"aborted"` are **skipped entirely** — replaying incomplete turns causes API errors.

---

## 6. Tool Definitions & Validation

### TypeBox Schema Integration

```typescript
const parameters = Type.Object({
  path: Type.String({ description: "File path" }),
  content: Type.String({ description: "File content" }),
}, { additionalProperties: false });
```

### Type Coercion Rules (`utils/validation.ts`)

| Target Type | Source | Result |
|-----------|--------|--------|
| number | "123" | 123 |
| boolean | "true" | true |
| boolean | 1 | true |
| string | 123 | "123" |
| null | "" | null |

Coercion happens before validation, ensuring LLM argument quirks don't break tool execution.

---

## 7. Context Overflow Detection (`utils/overflow.ts`)

20+ regex patterns for detecting overflow across providers:

```typescript
const OVERFLOW_PATTERNS = [
  /prompt is too long/i,                       // Anthropic
  /request_too_large/i,                        // Anthropic 413
  /exceeds the context window/i,               // OpenAI
  /input token count.*exceeds the maximum/i,   // Google
  /maximum prompt length is \d+/i,             // xAI
  /reduce the length of the messages/i,        // Groq
  /maximum context length is \d+ tokens/i,     // OpenRouter
  // ... 12+ more
];
```

Three detection cases:
1. **Error message pattern match** — explicit provider error
2. **Silent overflow** (z.ai) — `usage.input > contextWindow`
3. **Length-stop overflow** (Xiaomi MiMo) — `stopReason === "length"` + `output === 0` + input fills context

---

## 8. Thinking/Reasoning Support

### Adaptive vs Budget-Based

**Adaptive** (Anthropic Opus 4.6+, Sonnet 4.6):
```typescript
params.thinking = { type: "adaptive", display: "summarized" | "omitted" };
params.output_config = { effort: "low" | "medium" | "high" | "xhigh" | "max" };
```

**Budget-Based** (older models):
```typescript
params.thinking = {
  type: "enabled",
  budget_tokens: 1024 | 2048 | 8192 | 16384,
  display: "summarized" | "omitted",
};
```

### Default Token Budgets (`simple-options.ts`)

```typescript
const defaultBudgets = {
  minimal: 1024,
  low: 2048,
  medium: 8192,
  high: 16384,
};
```

---

## 9. Environment & Credentials (`env-api-keys.ts`)

API key discovery per provider:

| Provider | Env Var(s) |
|----------|------------|
| anthropic | `ANTHROPIC_OAUTH_TOKEN` (priority), `ANTHROPIC_API_KEY` |
| openai | `OPENAI_API_KEY` |
| google | `GEMINI_API_KEY` |
| google-vertex | `GOOGLE_CLOUD_API_KEY`, ADC, `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION` |
| amazon-bedrock | `AWS_PROFILE`, `AWS_ACCESS_KEY_ID`/`SECRET`, `AWS_BEARER_TOKEN_BEDROCK` |
| github-copilot | `COPILOT_GITHUB_TOKEN`, `GH_TOKEN`, `GITHUB_TOKEN` |
| mistral | `MISTRAL_API_KEY` |

Returns `"<authenticated>"` for credential-only auth (no explicit API key).

---

## 10. Lazy Provider Loading (`register-builtins.ts`)

Large providers are lazily loaded to optimize startup:

```typescript
function createLazyStream<TApi extends Api>(
  loadModule: () => Promise<LazyProviderModule<TApi>>,
): StreamFunction<TApi> {
  return (model, context, options) => {
    const outer = new AssistantMessageEventStream();
    loadModule()
      .then((module) => {
        const inner = module.stream(model, context, options);
        forwardStream(outer, inner);
      })
      .catch((error) => {
        outer.push({ type: "error", ... });
        outer.end(message);
      });
    return outer;
  };
}
```

Benefits: Startup time unaffected by optional provider SDKs; only pay cost for providers actually used.

---

## 11. JSON Repair for Streaming (`utils/json-parse.ts`)

```typescript
export function parseStreamingJson<T>(partialJson: string): T {
  // Try 1: Standard JSON parse
  // Try 2: JSON parse with repair
  // Try 3: Partial JSON parser
  // Try 4: Partial JSON on repaired JSON
  // Fallback: {}
}
```

Always returns valid object — ensures streaming JSON assembly never crashes even on incomplete/malformed input.

---

## Python Port Checklist

1. **Message class hierarchy**: `UserMessage`, `AssistantMessage`, `ToolResultMessage`
2. **Content block types**: `TextContent`, `ThinkingContent`, `ImageContent`, `ToolCall`
3. **Async streaming**: `async def` generators matching event protocol
4. **Provider registry**: Dict-based with lazy loading
5. **TypeBox → pydantic**: Use pydantic for JSON schema generation + validation
6. **SSE parsing**: Line-by-line parser for Anthropic event stream
7. **Model registry**: JSON-based catalog
8. **Stop reason mapping**: Per-provider mappers to canonical `StopReason` enum
9. **Cost calculation**: `(model_cost_per_million / 1_000_000) * token_count`
10. **Context overflow detection**: All 20+ regex patterns ported as-is
11. **Message transformation**: Cross-provider normalization logic
12. **Tool validation**: Pydantic-based validation with type coercion
