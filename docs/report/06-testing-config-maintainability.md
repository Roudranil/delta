# Pi Agent: Testing, Configuration & Maintainability

## 1. Test Framework Setup

**Vitest** across all packages with Node.js environment:
- `globals: true`, `environment: "node"`, `testTimeout: 30000`
- ~249 total test files: 82 in `ai/`, 149 in `coding-agent/`, 15 in `agent/`, 20 in `tui/`
- Test naming: `{feature}.test.ts`
- Each package defines its own `vitest.config.ts`
- Path aliases to resolve cross-package imports during tests

---

## 2. Unit Test Patterns

**Mock Stream Pattern**: Custom `MockAssistantStream` extends `EventStream`, simulating provider responses without real API calls.

**Test Utilities** (`session-test-utils.ts`):
- `createUserMessage()`, `createAssistantMessage()` factory functions
- `createTempDir()` with automatic cleanup in `afterEach`
- Temp directory tracking prevents test pollution

**Setup/Teardown**: Explicit `afterEach` blocks restore process state (env vars, file descriptors, file system).

---

## 3. Integration Test Patterns

**Agent Loop Integration** (`agent-loop.test.ts`):
- Tests full message flow: input → context → LLM call → response
- Uses `for await` to iterate event streams
- Verifies both event sequences AND final state
- Multi-turn simulation via `callIndex` state variable

**Tool Execution Integration**:
- Tools implemented with real execute() logic (not mocked)
- Tracks execution side effects in arrays
- Verifies `tool_execution_start`/`end` events

---

## 4. E2E Test Patterns

**With Faux Provider** (`e2e.test.ts`):
- Extracts test logic into helper functions (basicPrompt, toolExecution, etc.)
- Faux responses set via `.setResponses()` queue
- Contextual responses use factory functions examining prior messages

---

## 5. Faux Provider (Mock LLM)

**Registration API**:
```typescript
registerFauxProvider(options?: {
  api?: string;
  provider?: string;
  models?: FauxModelDefinition[];
  tokensPerSecond?: number;
  tokenSize?: { min?: number; max?: number };
})
```

Returns `FauxProviderRegistration` with:
- `getModel()` — Get mock model
- `setResponses()` — Queue responses
- `appendResponses()` — Add to queue
- `state.callCount` — Track calls
- `unregister()` — Cleanup

**Helper Functions**:
- `fauxText(text)` — Text content block
- `fauxThinking(thinking)` — Reasoning block
- `fauxToolCall(name, args, {id})` — Tool call
- `fauxAssistantMessage(content, options)` — Full message

**Response Factories**: Second-class responses can be async functions receiving `(context, options, state, model)` for conditional/contextual responses.

**Cleanup Pattern**:
```typescript
const registrations: FauxProviderRegistration[] = [];

afterEach(() => {
  for (const registration of registrations.splice(0)) {
    registration.unregister();
  }
});
```

---

## 6. Configuration System

### Locations

- Global: `~/.pi/settings.json` (user agent directory)
- Project: `./.pi/settings.json` (per-project, optional)

### `SettingsManager` API

```typescript
SettingsManager.create(projectDir, agentDir): SettingsManager
  .getTheme()
  .getDefaultModel()
  .getExtensionPaths()
  .getPackages()
  .setTheme()
  .setDefaultModel()
  .setDefaultThinkingLevel()
  .flush()   // Write to disk
  .reload()  // Reload from disk
  .drainErrors()  // Get accumulated errors
```

### Merge Strategy

1. Load global settings
2. Load project settings (if exist)
3. In-memory state tracks explicit changes
4. On flush: in-memory changes win conflicts, **unknown fields preserved**

**Lazy Directory Creation**: `.pi` directory only created on first write (preserves clean projects).

### Installation Detection

```typescript
detectInstallMethod() → "npm" | "pnpm" | "yarn" | "bun" | "unknown"
getSelfUpdateCommand() → Command object with args/display
```

---

## 7. Schema Validation (TypeBox)

```typescript
import { Type } from "typebox";

const parameters = Type.Object({
  path: Type.String({ description: "File path" }),
}, { additionalProperties: false });

// Generic tool with full type safety:
interface AgentTool<TParameters extends TSchema, TDetails> {}
```

**Coercion Rules**:
- `"42"` → `42` (string to number) ✓
- `true` → `1` (bool to number) ✓
- `"1"` → `true` (MUST be `"true"`/`"false"`) ✗ unless exact string
- Coercion happens **before** validation
- Failures throw `ValidationError`

---

## 8. Code Organization (Monorepo)

**Package Structure**:
```
packages/
  ai/              (82 tests)  — Provider abstraction, no agent logic
  agent/           (15 tests)  — Core loop, harness, generic
  coding-agent/   (149 tests)  — CLI, tools, settings, UI integration
  tui/             (20 tests)  — Terminal rendering
```

**Dependency Graph**:
- `coding-agent` depends on `agent`, `ai`, `tui`
- `agent` depends on `ai`
- `ai` is standalone (no dependencies on other packages)

**Module Exports**: Index files re-export all public APIs organized by category.

**Path Aliases** (`tsconfig.json`):
- `@earendil-works/pi-ai` → `packages/ai/src/index.ts`
- Resolved during tests via vitest alias config

---

## 9. Error Types & Handling

**`FileError`**: Backend-independent error codes:
- `"not_found"`, `"permission_denied"`, `"not_directory"`, `"is_directory"`, `"invalid"`, `"not_supported"`, `"unknown"`

**`StopReason`** (AssistantMessage.stopReason):
- `"stop"` — Normal completion
- `"length"` — Max tokens reached
- `"toolUse"` — Stopped for tool invocation
- `"error"` — Provider/runtime error
- `"aborted"` — User cancelled

**Tool Error Handling**: Tools return results with error in `content` (not thrown). Parent loop:
1. Sets `isError: true` on ToolResultMessage
2. Model receives error in conversation for recovery
3. Can optionally terminate via `terminate: true`

**Stream Error Protocol**: Streams never throw; errors emitted as `{type: "error", reason, error}` events.

---

## 10. Logging & Observability

**Event Types**:
- `agent_start` / `agent_end` — Lifecycle
- `turn_start` / `turn_end` — Turn boundaries
- `message_start` / `message_update` / `message_end` — Message flow
- `tool_execution_start` / `tool_execution_end` — Tool execution

**Usage Tracking**:
```typescript
interface Usage {
  input: number;
  output: number;
  cacheRead: number;
  cacheWrite: number;
  totalTokens: number;
  cost: { input, output, cacheRead, cacheWrite, total };
}
```

**Subscription Model**:
- `agent.subscribe(listener)` returns unsubscribe function
- Listeners can be async
- Agent waits for all listeners before settling

---

## 11. TypeScript Patterns

**Discriminated Unions**: `type: string` field enables type narrowing.

**Generic Constraints**: `AgentTool<TParameters extends TSchema, TDetails>` — Schema and details type decoupled.

**Declaration Merging**: Base library defines empty `CustomAgentMessages` interface; consuming code extends via module declaration.

**Async Generators**: Single object provides both `for await` iteration and `.result()` promise.

---

## Python Port Guidance

**Separation of Concerns**:
1. `pi_ai/` — Pure LLM provider abstraction (no agent logic)
2. `pi_agent_core/` — Generic agent loop + harness (no CLI, no UI)
3. `pi_coding_agent/` — CLI, config, built-in tools
4. `pi_tui/` — Terminal UI rendering

**Testing Best Practices**:
- Faux providers instead of mocking libraries
- Event-based testing (verify sequences)
- Explicit cleanup in teardown blocks
- Factory functions for test data
- Integration tests exercise real tool implementations

**Suggested Python Structure**:
```
delta/
  ai/              # Provider abstraction
  agent/           # Core loop
  coding_agent/    # CLI & tools
  tests/
    conftest.py    # Pytest fixtures
    fixtures.py    # Test factories
    fake/          # Mock services
```
