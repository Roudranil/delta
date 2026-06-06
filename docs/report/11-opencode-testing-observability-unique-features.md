# OpenCode: Testing, Observability & Unique Features vs Pi

## 1. What OpenCode Has That Pi Doesn't

### Summary Table

| Feature | Pi | OpenCode |
|---------|----|----|
| **Effect.ts** | Minimal | Full stack (736+ Effect.fn methods) |
| **Testing** | ~250 test files | 400+ with multi-layer patterns |
| **Cloud sync** | None | Workspace sync system |
| **Accounts** | None | Full OAuth + org support |
| **Question/confirm UX** | None | Deferred-based async questions |
| **Slash commands** | None | Extensible registry (config/MCP/skill) |
| **Auto-update** | None | Multi-channel (npm/brew/scoop/chocolatey) |
| **References** | None | Git repo references for context |
| **V2 API** | None | Event-sourced sessions |
| **ID system** | Basic | Prefix + timestamp (sortable, extractable) |
| **DB migrations** | None | Timestamped + schema snapshots |
| **Instance state** | None | Per-directory service isolation |
| **Shell compat** | Basic | Full matrix (10 shells) |
| **Error modeling** | Strings | Schema.TaggedErrorClass |
| **MCP** | None | Full (stdio + HTTP + SSE + OAuth) |
| **LSP** | None | Full integration (9 operations) |
| **PTY** | None | Full pseudo-terminal support |
| **Snapshots** | None | Git-based with rollback |
| **Worktrees** | None | Git worktrees for isolated work |
| **Websearch** | None | Exa + Parallel Search |
| **Webfetch** | None | HTML->Markdown conversion |
| **apply_patch** | None | Custom patch format for GPT models |
| **Plugins** | Script-based | Full npm packages + TUI plugins |
| **IDE integration** | None | VSCode, Cursor, Zed, Windsurf |
| **Desktop app** | None | Tauri (Rust + Node.js sidecar) |
| **Session sharing** | None | Cloud share URLs |
| **Background jobs** | None | Job queue system |
| **AGENTS.md** | Minimal | Full agent behavior file |

---

## 2. Effect.ts Functional Architecture

**Every public method is wrapped in `Effect.fn(...)` which creates a tracing span:**

```typescript
const get = Effect.fn("V2Session.get")(function* (sessionID) {
  const row = yield* Database.use((db) => db.select()...)
  if (!row) return yield* new NotFoundError({ sessionID })
  return fromRow(row)
})
```

**736+ Effect.fn/fnUntraced methods** across codebase = automatic observability with zero manual instrumentation.

**Benefits**:
- Automatic distributed tracing
- Typed error channels (not exceptions)
- Fiber-based structured concurrency
- Guaranteed resource cleanup via Scope
- Layer-based dependency injection

---

## 3. Testing Strategy

### Three Testing Layers

```typescript
const it = testEffect(Layer.mergeAll(...))

it.effect("pure Effect behavior", () => Effect.gen(function* () {
  // Deterministic TestClock/TestConsole, no real I/O
}))

it.live("real I/O", () => Effect.gen(function* () {
  // Real filesystem, child processes, git
}))

it.instance("isolated instance", () => Effect.gen(function* () {
  // Temp directory, full instance context, auto-cleanup
}), { git: true })
```

### Key Testing Utilities

**`tmpdirScoped(options?)`** — Creates temp directory scoped to Effect with auto-cleanup.

**`provideInstance(dir)(effect)`** — Temporarily switches instance context.

**`provideTestInstance(...)`** — Loads instance and runs async function in its context.

**`disposeAllInstances()`** — Cleans up all test instances.

### Mock Services (`test/fake/`)

```
fake/provider.ts    — Fake LLM provider for testing without external APIs
fake/account.ts     — Fake account service
fake/auth.ts        — Fake auth
fake/npm.ts         — Fake npm registry
fake/skill.ts       — Fake skill service
```

### Test Server (`test/lib/llm-server.ts`, 200+ lines)

In-process mock LLM server:
- Queue-based response simulation
- SSE streaming with head/tail control
- Tool calls with streaming args
- Reasoning content support
- Usage tracking
- Configurable delays, errors, hangs

### Multi-Directory Isolation Pattern

```typescript
it.live("questions stay isolated by directory", () =>
  Effect.gen(function* () {
    const one = yield* tmpdirScoped({ git: true })
    const two = yield* tmpdirScoped({ git: true })

    const fiber1 = yield* askEffect({...}).pipe(provideInstance(one), Effect.forkScoped)
    const fiber2 = yield* askEffect({...}).pipe(provideInstance(two), Effect.forkScoped)

    const onePending = yield* waitForPending(1).pipe(provideInstance(one))
    const twoPending = yield* waitForPending(1).pipe(provideInstance(two))

    expect(onePending[0].sessionID).toBe(SessionID.make("ses_one"))
    expect(twoPending[0].sessionID).toBe(SessionID.make("ses_two"))
  }),
)
```

---

## 4. Observability

### Structured Logging

```typescript
const log = Log.create({ service: "question" })

log.info("asking", { id, questions: input.questions.length })
log.warn("reply for unknown request", { requestID: input.requestID })
```

All logs tagged with service name and structured context.

### Error Boundaries

```typescript
export class RejectedError extends Schema.TaggedErrorClass<RejectedError>()(
  "QuestionRejectedError",
  {}
) {
  override get message() {
    return "The user dismissed this question"
  }
}
```

### Cause Formatting

Effect cause chains are pretty-printed:
```typescript
if (Exit.isFailure(exit)) {
  for (const err of Cause.prettyErrors(exit.cause)) {
    yield* Effect.logError(err)  // Full cause chain with context
  }
}
```

---

## 5. Question / Confirmation System

**File**: `packages/opencode/src/question/index.ts` (210+ lines)

```typescript
// Architecture:
// - Deferred-based blocking: ask() returns a Deferred that resolves when user replies
// - Pending request tracking: Map of QuestionID -> (Request, Deferred)
// - Bus event publishing: Asked, Replied, Rejected
// - Session-scoped isolation: Questions tied to session ID
// - Automatic cleanup on instance dispose: Rejects all pending on shutdown

yield* Question.ask({
  sessionID,
  questions: [{
    question: "Should I proceed with deleting these files?",
    header: "Confirm deletion",
    options: [
      { value: "yes", label: "Yes, proceed" },
      { value: "no", label: "Cancel" }
    ],
  }],
  tool: "shell",
})
// Suspends here until user answers
```

Test coverage: 14 tests verifying pending list tracking, concurrent questions, directory isolation, rejection on dispose.

---

## 6. Slash Command System

**File**: `packages/opencode/src/command/index.ts` (180+ lines)

**Sources** (commands come from multiple origins):

1. **Built-in commands**:
   - `init` — Guided AGENTS.md creation
   - `review` — Commit/branch/PR review

2. **Config commands** — User-defined in `AGENTS.md` with templates and hints

3. **MCP prompts** — Model Context Protocol prompts converted to commands

4. **Skills** — Skill definitions become commands

Each command has:
```typescript
interface Command {
  name: string
  description: string
  source: "command" | "mcp" | "skill"
  template: string | (() => Promise<string>)   // lazy for MCP
  hints: string[]                              // $1, $2, $ARGUMENTS
  agent?: string                               // optional agent override
  model?: string
  subtask?: boolean
}
```

---

## 7. Installation & Auto-Update System

**File**: `packages/opencode/src/installation/index.ts` (325+ lines)

**Installation method detection**: npm, yarn, pnpm, bun, brew, scoop, chocolatey, curl

**Multi-channel support**: `latest`, `next`, `preview`, `local`

**Version lookup** per package manager:
- GitHub releases (`tag_name`)
- npm registry
- Brew formula API
- Chocolatey community
- Scoop manifest

**Upgrade commands** generated per detected installation method.

---

## 8. ID Generation System

**File**: `packages/opencode/src/id/id.ts` (80 lines)

**Prefix-based, timestamp-ordered**:

```typescript
// Prefixes: ses_, msg_, que_, evt_, job_, pty_, tool_, wrk_, ...
// Format: prefix + 12 bytes (6 hex) + 14 random base62
// Total: 26 chars

// Monotonic counter: increments per millisecond for ordering within same timestamp
// Descending support: bitwise negation for reverse-order IDs
// Timestamp extraction: timestamp(id) recovers original time
```

---

## 9. Shell Detection & Environment

**File**: `packages/opencode/src/shell/shell.ts` (215 lines)

**Shell compatibility matrix** — metadata per shell:
```typescript
// bash, zsh, fish, ksh, nu, dash, sh, powershell, pwsh, cmd
// Metadata: login requirement, POSIX compliance, PowerShell flag

// Shell-specific arg construction:
// zsh: sources ~/.zshrc before cd
// bash: expand_aliases
// Windows: GitBash detection, taskkill-based process trees
```

**Process tree killing**: `SIGTERM` -> sleep -> `SIGKILL` with proper cleanup

---

## 10. V2 API (Event-Sourced Sessions)

**File**: `packages/opencode/src/v2/session.ts` (340+ lines)

```typescript
// Differences from V1:
// - Message cursor pagination (forward/backward with stable timestamps)
// - Event-sourced (session events tracked separately)
// - Workspace integration (sessions scoped to workspaces)
// - Model/agent switching mid-session
// - Subagents (create child sessions automatically)
// - Session path (organize sessions hierarchically)

const get = Effect.fn("V2Session.get")(function* (sessionID) {
  const row = yield* Database.use(...)
  if (!row) return yield* new NotFoundError({ sessionID })
  return fromRow(row)
})
```

---

## 11. Per-Instance State Pattern

Services that should NOT be shared across directories:

```typescript
const state = yield* InstanceState.make<State>(
  Effect.fn("Service.state")(function* (ctx) {
    // ctx.directory changes per instance
    // state is cached per directory
    // disposed when instance unloads

    yield* Effect.acquireRelease(setup(), cleanup())
    yield* subscriptions.pipe(Effect.forkScoped)  // Background work

    return loadInitialState()
  })
)
```

Used for: file watchers, background jobs, session state, plugin managers, config loaders.

This pattern enables running multiple opencode instances for different projects simultaneously without state leakage.

---

## 12. Code Quality Patterns

### Service Shape Template (50+ services follow this)

```typescript
export interface Interface {
  readonly method1: (...args) => Effect.Effect<Return, Error>
}

export class Service extends Context.Service<Service, Interface>()("@opencode/ServiceName") {}

export const layer = Layer.effect(
  Service,
  Effect.gen(function* () {
    const dependency = yield* DependencyService

    const method1 = Effect.fn("ServiceName.method1")(function* (...args) {
      // Named span, typed errors, structured logging
    })

    return Service.of({ method1 })
  }),
)

export const defaultLayer = layer.pipe(Layer.provide(Dependency.defaultLayer))
```

### Error Modeling

```typescript
// Expected failures -> error channel
const get = Effect.fn("Session.get")(function* (id) {
  const row = yield* Storage.get(id).pipe(
    Effect.catchTag("NotFound", () => new NotFoundError())
  )
})

// Unknown/impossible failures -> defects (crashes)
if (!row || !row.id) {
  return yield* Effect.die(new Error("Impossible state"))
}
```

### Schema as Source of Truth

```typescript
// Single definition for validation + JSON schema + type safety:
export class Info extends Schema.Class<Info>("QuestionInfo")({
  question: Schema.String.annotate({ description: "Complete question" }),
  header: Schema.String.annotate({ description: "Very short label (max 30 chars)" }),
  options: Schema.Array(Option),
  custom: Schema.optional(Schema.Boolean),
}) {}
```

### Style from AGENTS.md

- Functional array methods over imperative loops
- Early returns, no else branches
- `const` preferred over `let`
- No comments unless truly non-obvious
- Schema-first all domain objects

---

## 13. Database Schema Migrations

**15+ timestamped migrations** in `packages/opencode/migration/`:

```
20260323234822_events/
20260428004200_add_session_path/
20260510033149_session_usage/
20260511000411_data_migration_state/
...
```

Each migration:
- `migration.sql` — DDL/DML
- `snapshot.json` — Full schema state for drift detection and rollback

---

## Key Patterns Worth Porting to Python

1. **Effect.fn tracing at every public boundary** -> Python: `@trace("ServiceName.method")` decorator + OpenTelemetry
2. **Schema.TaggedErrorClass for domain errors** -> Python: `@dataclass` error types with discriminator field
3. **InstanceState for per-directory services** -> Python: `contextvar` + dict keyed by directory, with cleanup hooks
4. **Three-tier testing strategy** -> Python: `pytest` with `pure/live/instance` fixture scopes
5. **Bus event publishing** -> Python: `asyncio.Queue` + typed event dataclasses
6. **Schema as source of truth** -> Python: Pydantic models (generate JSON Schema, validate, type-safe)
7. **Service/Layer pattern** -> Python: dependency injection via `__init__` or a DI framework
8. **Question/confirmation UX** -> Python: `asyncio.Future` + event publication + await in tool execution
9. **Slash command registry** -> Python: dict of command name -> handler, with sources (config/MCP/skill)
10. **Feature flags via env vars** -> Python: `os.environ.get("DELTA_FEATURE_X", "false")` consistently
