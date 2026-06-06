# Pi Agent: Model Handoff, Multi-Gateway Support, Provider Abstractions

## 1. Provider Abstraction Interface

**File:** `packages/ai/src/api-registry.ts:23-27`

```typescript
export interface ApiProvider<TApi extends Api = Api, TOptions extends StreamOptions = StreamOptions> {
  api: TApi;
  stream: StreamFunction<TApi, TOptions>;
  streamSimple: StreamFunction<TApi, SimpleStreamOptions>;
}
```

**Method signatures** (`types.ts:206-210`):

```typescript
export type StreamFunction<TApi extends Api, TOptions extends StreamOptions> = (
  model: Model<TApi>,
  context: Context,
  options?: TOptions,
) => AssistantMessageEventStream;
```

**Contract**: Failures must be encoded in the returned stream, NOT thrown. Error termination produces `AssistantMessage` with `stopReason: "error"|"aborted"`.

**Model interface** (`types.ts:528-558`):

```typescript
export interface Model<TApi extends Api> {
  id: string;                          // "claude-sonnet-4-5"
  name: string;
  api: TApi;                           // "anthropic-messages"
  provider: Provider;                  // "anthropic"
  baseUrl: string;
  reasoning: boolean;                  // supports extended thinking
  thinkingLevelMap?: ThinkingLevelMap; // maps pi levels to provider-specific values
  input: ("text" | "image")[];         // modalities supported
  cost: { input, output, cacheRead, cacheWrite };  // $/million tokens
  contextWindow: number;
  maxTokens: number;
  headers?: Record<string, string>;    // default headers (model-specific)
  compat?: TApi extends "openai-completions" ? OpenAICompletionsCompat : ...;
}
```

---

## 2. Provider Registry & Model Resolution

**Registry** (`api-registry.ts:40-98`):

```typescript
const apiProviderRegistry = new Map<string, RegisteredApiProvider>();

registerApiProvider({ api: "anthropic-messages", stream: ..., streamSimple: ... });
getApiProvider("anthropic-messages")  // Returns the provider
```

**Model string format**: `provider/modelId` or `provider/modelId:thinkingLevel`

**Parser** (`model-resolver.ts:189-242`):
1. Tries exact match first
2. If pattern contains colon and suffix is valid thinking level, splits on **last colon**
3. Recursively parses prefix, applies thinking level if match found
4. Prioritizes **aliases** (e.g., `claude-sonnet-4-5`) over dated versions

**Example resolution**:
- Input: `"anthropic/claude-sonnet-4-5:high"`
- Parsed: `{ provider: "anthropic", modelId: "claude-sonnet-4-5", thinkingLevel: "high" }`
- API lookup: `getApiProvider("anthropic-messages")` -> Anthropic stream handler

**Stream dispatch** (`stream.ts:25-31`):

```typescript
export function stream<TApi extends Api>(
  model: Model<TApi>,
  context: Context,
  options?: ProviderStreamOptions,
): AssistantMessageEventStream {
  const provider = resolveApiProvider(model.api);
  return provider.stream(model, context, options as StreamOptions);
}
```

---

## 3. Cross-Provider Handoff

**Handoff is explicit (not automatic)** — pi does NOT automatically fall back to a different provider on failure. Instead:
1. Request fails with `stopReason: "error"` and `errorMessage`
2. User/agent decides to switch models via `/model <new-provider>/<new-model>` or CLI flag
3. Session state is preserved and replayed to the new model

The system validates cross-provider compatibility via automated E2E tests (`test/cross-provider-handoff.test.ts`):
- Generates fixture for each provider/model pair
- Tests cross-handoff: concatenates messages from ALL other providers + asks target to "say hi"

**State Preserved vs. Reset**:
- **Preserved**: User messages, text content, tool results, syntactic message structure
- **Dropped cross-model**: Redacted thinking blocks (encrypted, model-specific), `thoughtSignature` on tool calls (Google-specific), thinking blocks (converted to text)
- **Normalized**: Tool call IDs

**`transformMessages()`** (`transform-messages.ts:64-220`):
```typescript
export function transformMessages<TApi extends Api>(
  messages: Message[],
  model: Model<TApi>,
  normalizeToolCallId?: (id, model, source) => string,
): Message[] {
  // 1. Downgrade unsupported images to placeholders
  // 2. Transform assistant messages (thinking, tool IDs)
  // 3. Insert synthetic empty tool results for orphaned tool calls
  // 4. Skip errored/aborted assistant messages
}
```

---

## 4. Gateway Support

### OpenRouter

Gateway that routes to ANY upstream provider.

```typescript
// Model's api = "openai-completions"
// Model's baseUrl = "https://openrouter.ai/api/v1"
// OpenRouter-specific routing preferences in request body:

params.provider = {
  allow_fallbacks: true,
  require_parameters: false,
  data_collection: "allow" | "deny",
  order: ["anthropic", "openai"],     // Try in order
  only: ["anthropic"],                 // Whitelist
  ignore: ["local-models"],            // Blacklist
  max_price: { prompt: 0.01, completion: 0.05 },
};
```

### Azure OpenAI

Deployment name resolution:
```typescript
// AZURE_OPENAI_DEPLOYMENT_NAME_MAP=gpt-4o-mini=my-deployment-1,gpt-5.4=my-deployment-2
function resolveDeploymentName(model, options): string {
  if (options?.azureDeploymentName) return options.azureDeploymentName;
  const mapped = parseDeploymentNameMap(process.env.AZURE_OPENAI_DEPLOYMENT_NAME_MAP).get(model.id);
  return mapped || model.id;
}
```

### Amazon Bedrock

Auth flow: `AWS_PROFILE` -> `AWS_ACCESS_KEY_ID`/`SECRET` -> `AWS_BEARER_TOKEN_BEDROCK` -> ECS task roles -> IRSA

### Google Vertex AI

Auth: Explicit API key via `GOOGLE_CLOUD_API_KEY`, or Application Default Credentials (ADC).

Requires: `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`

### Cloudflare

Template URL substitution:
```typescript
export function resolveCloudflareBaseUrl(model: Model<Api>): string {
  // https://gateway.ai.cloudflare.com/v1/{CLOUDFLARE_ACCOUNT_ID}/{CLOUDFLARE_GATEWAY_ID}/...
  return url.replace(/\{([A-Z_][A-Z0-9_]*)\}/g, (_match, name) => {
    const value = process.env[name];
    if (!value) throw new Error(`${name} required for provider ${model.provider}`);
    return value;
  });
}
```

---

## 5. Auth / API Key Management (`env-api-keys.ts`)

```typescript
// OAuth token precedence for Anthropic:
if (provider === "anthropic") {
  return ["ANTHROPIC_OAUTH_TOKEN", "ANTHROPIC_API_KEY"];  // OAuth first!
}

export function getEnvApiKey(provider: string): string | undefined {
  const envKeys = findEnvKeys(provider);
  if (envKeys?.[0]) return process.env[envKeys[0]] || getProcEnv(envKeys[0]);

  // Special case: Vertex AI supports ADC
  if (provider === "google-vertex" && hasVertexAdcCredentials()) return "<authenticated>";

  // Special case: Bedrock supports AWS credentials
  if (provider === "amazon-bedrock" && (process.env.AWS_PROFILE || ...)) return "<authenticated>";
}
```

**Config sources** (priority order):
1. OAuth credentials (stored in auth storage, refreshed on use)
2. Explicit API key in `models.json` for provider
3. Environment variable
4. Ambient credentials (AWS profile, Vertex ADC, Bedrock SDK defaults)

---

## 6. Retry & Fallback

**SDK-level retries**: OpenAI/Anthropic SDKs handle retries internally for rate limits (429) and transient errors (5xx) via `maxRetries` option.

If server requests delay > `maxRetryDelayMs`, request fails immediately, allowing higher-level retry logic with user visibility.

**No automatic cross-model fallback** — pi does not automatically switch to a different provider on failure. This is a deliberate design choice (user controls model selection).

---

## 7. Capability Negotiation

```typescript
// models.ts
export function getSupportedThinkingLevels<TApi>(model: Model<TApi>): ModelThinkingLevel[] {
  if (!model.reasoning) return ["off"];
  return EXTENDED_THINKING_LEVELS.filter((level) => {
    const mapped = model.thinkingLevelMap?.[level];
    if (mapped === null) return false;  // explicitly unsupported
    if (level === "xhigh") return mapped !== undefined;  // xhigh requires mapping
    return true;
  });
}

export function clampThinkingLevel<TApi>(model: Model<TApi>, level: ModelThinkingLevel): ModelThinkingLevel {
  const availableLevels = getSupportedThinkingLevels(model);
  if (availableLevels.includes(level)) return level;
  // Find nearest supported level (prefer higher, then lower)
  for (let i = requestedIndex; i < EXTENDED_THINKING_LEVELS.length; i++) {
    if (availableLevels.includes(EXTENDED_THINKING_LEVELS[i])) return EXTENDED_THINKING_LEVELS[i];
  }
}
```

Model capabilities stored in `Model` object:
- `reasoning: boolean` — supports extended thinking
- `thinkingLevelMap` — maps pi levels to provider values
- `input: ("text" | "image")[]` — vision support
- `contextWindow: number` — token budget
- `maxTokens: number` — output limit
- `cost: { input, output, cacheRead, cacheWrite }` — pricing

---

## 8. Dynamic Provider Registration (Extensions)

Extensions can register their own providers at runtime:

```typescript
// model-registry.ts:791-923
registerProvider(providerName: string, config: ProviderConfigInput): void {
  // 1. Validate config
  // 2. Register OAuth provider if provided
  // 3. Register API stream handler if streamSimple provided
  // 4. Replace models for this provider
  // 5. Call OAuth.modifyModels() if credentials exist
}

unregisterProvider(providerName: string): void {
  // Remove provider, reload models from disk (restores built-in models)
}
```

---

## 9. Lazy Provider Loading

```typescript
// register-builtins.ts
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

Benefits: Startup time unaffected; only pay cost for providers actually used.

---

## 10. Provider Architecture Summary

| Component | File | Purpose |
|-----------|------|---------|
| `StreamFunction` | `types.ts:206-210` | Interface each provider must implement |
| `ApiProvider` | `api-registry.ts:23-27` | Registry entry with stream + streamSimple |
| `ApiRegistry` | `api-registry.ts:40-98` | Global map of api -> provider |
| `Model<TApi>` | `types.ts:528-558` | Model metadata (id, provider, capabilities) |
| `parseModelPattern()` | `model-resolver.ts:189-242` | Parse `"provider/id:level"` -> Model + ThinkingLevel |
| `transformMessages()` | `transform-messages.ts:64-220` | Normalize messages for cross-provider replay |
| `stream()` / `streamSimple()` | `stream.ts:25-59` | Entry points that dispatch to provider |
| `register-builtins.ts` | `providers/register-builtins.ts` | Lazy-load & register all built-in providers |
| `ModelRegistry` | `model-registry.ts:331-923` | Loads/manages models, resolves auth |
| `getEnvApiKey()` | `env-api-keys.ts:158-210` | Discover API keys from env + ambient credentials |
