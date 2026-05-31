# Report 14: GSD Redux — SDK, Hooks & Runtime Infrastructure

Source: `reference-repos/get-shit-done-redux`

---

## Executive Summary

The GSD SDK is a TypeScript library (`sdk/src/`) that provides programmatic access to the GSD workflow engine. It handles workstream isolation, event streaming, configuration, phase orchestration, and runtime dispatch. The hooks layer (`hooks/`) integrates with Claude Code's PreToolUse/PostToolUse event system to inject advisories, track context usage, and guard against prompt injection — without ever blocking tool execution.

---

## 1. SDK Architecture & Core Types

### 1.1 Type System (`sdk/src/types.ts`)

**Plan structures:**
```typescript
PlanFrontmatter {
  phase: string
  plan_name: string
  type: string
  wave: number
  dependencies: string[]
  autonomous: boolean
  requirements: string[]
  must_haves: { truths, artifacts, key_links }
}

PlanTask {
  type: string
  name: string
  files: string[]
  read_first: string[]
  action: string
  verify: string
  acceptance_criteria: string
  done: boolean
}

ParsedPlan { frontmatter, objective, execution_context, context_refs, tasks }
```

**Phase lifecycle types:**
```typescript
PhaseType: Discuss | Research | Plan | Execute | Verify | Repair
PhaseStepType: extends PhaseType with PlanCheck | Advance

GSDEventType: 25+ types including:
  SessionInit, PhaseStart, PhaseStepComplete, PhaseComplete
  ToolCall, ToolProgress, ToolUseSummary
  TaskStarted, MilestoneComplete
  StateMutation, ConfigMutation, FrontmatterMutation, GitCommit
  CostUpdate, APIRetry, RateLimit
  WaveStart, RuntimeBridgeHotpath
```

**Execution options:**
```typescript
SessionOptions {
  maxTurns: number    // default 50
  budget: number      // default $5
  model: string
  cwd: string
  allowedTools: string[]
}
```

### 1.2 GSD Class API (`sdk/src/index.ts`)

```typescript
class GSD {
  constructor(options: GSDOptions) {
    projectDir: string        // required
    workstream?: string       // routes to .planning/workstreams/<name>/
    strictSdk?: boolean       // fail fast on unknown commands
    autoMode?: boolean        // force auto_advance=true
    maxBudgetUsd?: number     // default 5.0
    maxTurns?: number         // default 50
  }

  // Execute a single plan file
  executePlan(planPath, options?) → PlanResult {
    success, costUsd, durationMs, tokenBreakdown
  }

  // Run full phase lifecycle: discuss → research → plan → check → execute → verify → advance
  runPhase(phaseNumber, options?: PhaseRunnerOptions) → PhaseRunnerResult {
    phaseNumber, phaseName
    steps: PhaseStepResult[]    // per-step success/failure/cost/duration
    success, totalCostUsd, totalDurationMs
  }

  // Multi-phase milestone orchestration
  run(prompt, options?: MilestoneRunnerOptions) → MilestoneRunnerResult
    // Discovers phases → runs incomplete phases in order
    // Re-discovers after each completion (handles dynamically inserted phases)
    // Supports onPhaseComplete callback for stop/continue decisions
}
```

### 1.3 PhaseRunner Lifecycle

```typescript
class PhaseRunner {
  async run(phaseNumber: string, options?: PhaseRunnerOptions)

  // Lifecycle (each step configurable via flags):
  1. Discuss    (skip if skip_discuss=true)
  2. Research   (skip if workflow.research=false)
  3. Plan       (skip if not needed)
  4. PlanCheck  (skip if plan_check=false)
  5. Execute    (core step — never skipped)
  6. Verify     (skip if verifier=false)
  7. Advance    (mark complete, update roadmap)

  // Human gate callbacks (optional):
  onDiscussApproval: (context) → 'approve' | 'reject' | 'modify'
  onVerificationReview: (results) → 'accept' | 'reject' | 'retry'
  onBlockerDecision: (blocker) → 'retry' | 'skip' | 'stop'
}
```

### 1.4 Verification Outcome Logic

```typescript
type VerificationOutcome =
  | 'passed'
  | 'human_needed'
  | 'gaps_found'
  | 'architectural_debt'
  | 'status_unreadable'

// Checks performed:
1. Plan completion (all tasks marked done)
2. Architectural debt markers (TODO, FIXME, XXX, HACK scanning)
3. UAT pass predicate (isPhaseUatPassed)
4. Gap closure (re-execute if gaps found, up to maxGapRetries)
```

---

## 2. Configuration System

### 2.1 Layered Config (`sdk/src/configuration/index.ts`)

Single source of truth via manifest files:
- `shared/config-defaults.manifest.json` — canonical defaults
- `shared/config-schema.manifest.json` — all allowed keys
- `RUNTIME_STATE_KEYS` — keys that persist runtime state (`workflow._auto_chain_active`)
- `DYNAMIC_KEY_PATTERNS` — extensible keys (`agent_skills.*`, `model_profile_overrides.*`)

**API:**
```typescript
loadConfig(cwd, workstream?) → MergedConfig
  // Reads .planning/config.json (or workstream variant)
  // Merges with defaults
  // Never writes disk (pure read)

normalizeLegacyKeys(parsed) → { parsed, normalizations[] }
  // Idempotent legacy key migration
  // Does NOT write disk

mergeDefaults(parsed) → MergedConfig
  // Deep-merge: explicit null overrides defaults ("unset this key")
  // Arrays: replaced, not merged

migrateOnDisk(cwd) → MigrationReport
  // Explicit opt-in disk writeback
  // Applied normalizations + wrote path (or null if no changes)
```

### 2.2 Config Schema

```json
{
  "model_profile": "balanced",
  "workflow": {
    "research": true,
    "plan_check": true,
    "verifier": true,
    "auto_advance": false,
    "skip_discuss": false,
    "human_verify_mode": "end-of-phase",
    "security_enforcement": true,
    "security_asvs_level": 1,
    "tdd_mode": false,
    "mvp_mode": false,
    "parallelization": true,
    "context_window": 200000
    // ...40+ more flags
  },
  "git": {
    "branching_strategy": "none",
    "base_branch": null,
    "phase_branch_template": "gsd/phase-{phase}-{slug}"
  },
  "planning": {
    "granularity": "standard",
    "commit_docs": true,
    "sub_repos": []
  },
  "hooks": {
    "context_warnings": true,
    "workflow_guard": false
  },
  "agent_skills": {},      // extensible per-agent
  "model_overrides": {}    // extensible per-agent-id
}
```

---

## 3. Query System & Command Registry

### 3.1 Command Registry (`sdk/src/query/registry.ts`)

```typescript
class QueryRegistry {
  register(command: string, handler: QueryHandler)
  has(command: string): boolean
  dispatch(command, args, projectDir) → QueryResult
  commands(): string[]
  extractField(obj, fieldPath): unknown  // supports a.b.c + items[0] notation
}
```

`createRegistry()` assembles ~50+ native handlers.

### 3.2 Command Families

| Family | Examples |
|--------|---------|
| `state.*` | state.load, state.set-fields, state.record-session, state.archive |
| `verify.*` | verify.check-completion, verify.check-ship-ready, verify.check-gates |
| `init.*` | init.new-project, init.phase-op, init.codebase-scan, init.roadmap-discover |
| `phase.*` | phase.discuss-phase N, phase.execute-phase N, phase.verify-phase N |
| `phases.*` | phases.list, phases.discover, phases.roadmap-analyze |
| `validate.*` | validate.check-gates, validate.check-auto-mode |
| `roadmap.*` | roadmap.analyze, roadmap.extract-milestone, roadmap.update-progress |

### 3.3 Query Handler Interface

```typescript
type QueryHandler = (
  args: string[],
  projectDir: string,
  workstream?: string
) => Promise<QueryResult>

interface QueryResult {
  data: unknown
  raw?: string    // for output_mode: 'raw'
}
```

---

## 4. Runtime Bridge

### 4.1 Purpose

The Runtime Bridge is the seam between the TypeScript SDK and the CJS tool runtime (`gsd-tools.cjs`). It routes queries through either native SDK handlers or subprocess fallbacks.

### 4.2 Async Bridge (`sdk/src/query-runtime-bridge.ts`)

```typescript
class QueryRuntimeBridge {
  execute(input: RuntimeBridgeExecuteInput) → unknown
    // Route: native QueryRegistry → subprocess gsd-tools.cjs
    // Emits RuntimeBridgeEvent { type, command, mode, dispatchMode, reason, outcome, errorKind }

  dispatchHotpath(legacyCmd, legacyArgs, registryCmd, registryArgs, mode)
    // Fast path for hot commands (generate-slug, state.load, etc.)
    // Native-first with subprocess fallback
    // Emits RuntimeBridgeHotpathEvent

interface RuntimeBridgeExecuteInput {
  legacyCommand: string    // CJS command name
  legacyArgs: string[]
  registryCommand: string  // SDK canonical command
  registryArgs: string[]
  mode: 'json' | 'raw'
  projectDir: string
  workstream?: string
}
```

**Dispatch modes:**
- `native` — QueryRegistry handler (fastest, always available for registered commands)
- `native_hotpath` — optimized for high-volume commands
- `subprocess` — shell out to `gsd-tools.cjs` (fallback for unregistered commands)

### 4.3 Synchronous Bridge (`sdk/src/runtime-bridge-sync/index.ts`)

For CJS callers that cannot use async/await:

```typescript
executeForCjs(input: RuntimeBridgeExecuteInput) → RuntimeBridgeSyncResult
  // Uses synckit (Atomics.wait + SharedArrayBuffer + worker_threads)
  // Spawns worker lazily, reuses across calls
  // CRITICAL: Must NOT be called from async context (deadlock)

// Result discriminated union:
{ ok: true,  data, exitCode: 0 }
{ ok: false, exitCode, errorKind, errorDetails?, stderrLines[] }

// SyncErrorKind:
'unknown_command' | 'native_failure' | 'native_timeout' |
'fallback_failure' | 'validation_error' | 'internal_error'
```

Worker overhead: ~80ms first call (startup), ~0.1ms steady state.

**DEADLOCK WARNING:**
```typescript
// ❌ DEADLOCK: main thread blocked by Atomics.wait, cannot process worker response
async function bad() {
  const result = executeForCjs({ ... })  // DEADLOCK!
}

// ✅ SAFE: synchronous module initialization
const result = executeForCjs({ ... })  // OK
```

---

## 5. Workstream Inventory System

### 5.1 Workstream Concept

Workstreams = isolated task tracks within a project:
- Each has own `.planning/workstreams/<name>/` directory
- Separate STATE.md, ROADMAP.md, REQUIREMENTS.md, phases/, config.json
- Allows parallel work streams without collision
- Active workstream tracked in `.planning/active-workstream` file

### 5.2 Workstream Inventory Builder (Pure Projection)

```typescript
interface WorkstreamInventory {
  name: string
  path: string
  active: boolean
  files: { roadmap, state, requirements }
  status: string
  current_phase: string | null
  last_activity: string | null
  phases: WorkstreamPhaseInventory[]
    // directory, status (complete|in_progress|pending), plan_count, summary_count
  phase_count, completed_phases, roadmap_phase_count
  total_plans, completed_plans
  progress_percent: number    // calculated: completed_plans / total_plans * 100
}

buildWorkstreamInventory(inputs: BuilderInputs) → WorkstreamInventory
  // Stateless transformation: caller does I/O, builder does calculation
  // No filesystem access in builder (pure function)
```

### 5.3 Security: Path Traversal Prevention (#3589)

```typescript
validateWorkstreamName(name: string) → boolean
  // Rejects '.', '..', path separators, non-ASCII

relPlanningPath(projectDir, workstream?, file?) → string
  // Constructs path: .planning/ or .planning/workstreams/<name>/
  // Validates workstream name before constructing path
  // Used by ALL planning operations as single seam

// Env-sourced workstreams silently fall back to root .planning/
// (rather than throwing — per #2791 contract)
```

---

## 6. Event Stream

### 6.1 Event Bus (`sdk/src/event-stream.ts`)

```typescript
class GSDEventStream extends EventEmitter {
  addTransport(handler: (event: GSDEvent) => void)
  removeTransport(handler)
  closeAll()
}
```

- Maps SDK messages to typed GSDEvents
- Per-session cost tracking (CostBucket + CostTracker)
- Single emit point → all listeners and transports see identical event sequence

### 6.2 Cost Tracking

```typescript
interface CostBucket {
  inputTokens: number
  outputTokens: number
  cacheCreationTokens: number
  cacheReadTokens: number
  costUsd: number
}

CostTracker.update(event) → CostUpdateEvent
  // Cumulative totals per session
  // Emitted as CostUpdateEvent after each turn
```

---

## 7. Hooks — Agent Loop Integration

### 7.1 Hook Architecture

Claude Code fires hooks as subprocess invocations:
- **PreToolUse**: Before tool execution → can inject `additionalContext` advisory
- **PostToolUse**: After tool completes → for observability/cleanup

Contract:
1. Claude Code detects tool call → emits event
2. Reads `.claude/hooks/` for matching executable scripts
3. Passes JSON to hook's stdin
4. Hook writes JSON to stdout (with optional `additionalContext`)
5. Claude Code merges hook output into agent context
6. Hook must exit within timeout (3–10s) or Claude Code terminates it

**Critical design rule**: All hooks wrap everything in try-catch + silent fail on error. A hook must NEVER block tool execution.

### 7.2 gsd-statusline.js (PreToolUse)

**Input from Claude Code stdin:**
```json
{ "model": "...", "workspace": {"current_dir": "..."}, "session_id": "...", "context_window": {...}, "transcript_path": "..." }
```

**Functions:**
- `readGsdState(dir)` — walks up to `.planning/STATE.md`, parses frontmatter + body
- `formatGsdState()` — renders as `"milestone · phase · next_action"`
- `readGsdConfig(dir)` — loads `.planning/config.json`
- `readLastSlashCommand(transcriptPath)` — scans transcript tail for `<command-name>` tag
- `renderProgressBar(percent)` — 10-segment progress visualization

**Phase-lifecycle scenes** (#2833):
```
"Phase X.Y <status>"
"next execute-phase 4.5"
"milestone complete"
```

**Context bridge file:** `/tmp/claude-ctx-{session_id}.json`
- Shared with gsd-context-monitor
- Contains: remaining_percentage, used_pct, timestamp

### 7.3 gsd-context-monitor.js (PostToolUse)

**Thresholds:**
- WARNING: remaining_context <= 35%
- CRITICAL: remaining_context <= 25%

**Behavior:**
- Debounce: 5 tool uses between warnings
- CRITICAL bypasses debounce
- On CRITICAL + GSD active: fire-and-forget `gsd state record-session --stopped-at <context% date>` to write breadcrumb to STATE.md

**Injected advisory (goes into agent's conversation):**
```
CRITICAL: "Context nearly exhausted. Do NOT start new work. GSD state tracked in STATE.md."
WARNING:  "Context getting limited. Avoid new complex work."
```

Config gate: `hooks.context_warnings` in `.planning/config.json` (default: `true`)

### 7.4 gsd-prompt-guard.js (PreToolUse)

Scans Write/Edit tool calls targeting `.planning/` files for prompt injection:

**Detection patterns:**
- `"ignore.*previous.*instructions"`, `"you are now a"`, `"act as"`, `"reveal your prompt"`
- Invisible Unicode (ZWJ, BOM, soft hyphens)
- XML-style tags: `<system>`, `<assistant>`, `[INST]`, `[SYSTEM]`

**Advisory (non-blocking):**
```
"PROMPT INJECTION WARNING: triggered N patterns ... Review for embedded instructions"
```

Goal: surface suspicious content before it enters agent context. Never blocks.

### 7.5 gsd-read-guard.js (PreToolUse)

**Problem**: Non-Claude runtimes (MiniMax, OpenCode) don't enforce "read before edit" natively → model attempts Write/Edit on existing file without reading → runtime rejects → infinite retry loop.

**Solution**: Inject PreToolUse advisory BEFORE tool call reaches runtime.
- Detects Claude Code (skips — CC enforces read-before-edit natively)
- For other runtimes: `"READ-BEFORE-EDIT REMINDER: file exists, must Read first"`

Result: Model sees advisory next turn and issues Read call naturally. Prevents usage burnout from retry loops.

### 7.6 gsd-workflow-guard.js (PreToolUse, optional)

Soft guard detecting direct file edits outside `/gsd-*` skill context:
- Fires on Write/Edit to non-`.planning/` files
- Skips if inside subagent context (Task orchestrator, is_subagent flag)
- Skips if editing config/docs (`.gitignore`, `CLAUDE.md`, settings)

Config gate: `hooks.workflow_guard` (default: `false`)

**Advisory:**
```
"WORKFLOW ADVISORY: editing X directly without GSD command. Edit not tracked in STATE.md."
```

Nudges toward `/gsd:fast` or `/gsd:quick` for state tracking. Never blocks.

### 7.7 gsd-check-update.js (background check)

- Periodically checks npm for new GSD version
- Caches result in `~/.cache/gsd/gsd-update-check.json`
- Statusline displays: `"⬆ /gsd:update"` if update available
- Flags `stale_hooks` when installed version is ahead of npm latest (dev install)

---

## 8. Installer & Bootstrap

### 8.1 Installation Flow (`bin/install.js`)

1. **Runtime detection**: Identifies Claude Code, OpenCode, Gemini, Codex, Hermes, Qwen, etc.
2. **Hook installation**: Copies `gsd-*.js` hooks to `~/.claude/hooks/` (or runtime equivalent); manages `.toml` hook registration for Codex
3. **Agent installation**: Installs `gsd-executor.md`; normalizes slash-command namespace per runtime (#3677)
4. **Skill registration**: Installs `/gsd-*` skills in `~/.claude/skills/gsd-*/SKILL.md`
5. **Settings merge**: Merges GSD settings into `~/.claude/settings.json`, preserving user-local settings

### 8.2 Shell Command Projection

For Windows compatibility (#3597):
- Generates `.bat` shims for PowerShell
- Portable `/usr/bin/env bash` shebang
- Normalizes paths to forward slashes
- Handles argv overflow on Windows

### 8.3 Namespace Normalization (#3677, #3583)

Runtimes register hyphen-form command names (`/gsd-plan-phase`) but agent bodies reference colon form (`/gsd:plan-phase`). At install time for Claude/Qwen/Hermes:
```javascript
transformContentToHyphen(agentBody, commandNames)
  // Replaces /gsd:<cmd> → /gsd-<cmd> in agent definition
  // Preserves colon refs for runtimes that self-convert
```

---

## 9. Testing Patterns

### 9.1 Test Structure

**Unit tests**: Config schema, frontmatter mutation, workstream inventory builder, registry dispatch
**Integration tests**: End-to-end phase execution with real `gsd-tools.cjs` subprocess + filesystem fixtures
**E2E tests**: Full project initialization, multi-phase milestone runs, concurrent wave execution

### 9.2 Fixture Pattern

```typescript
beforeEach(async () => {
  tmpDir = createTempDir()
  await mkdir(join(tmpDir, '.planning'), { recursive: true })
})
afterEach(async () => {
  await rm(tmpDir, { recursive: true, force: true })
})
```

### 9.3 Adversarial Fixtures

**Config schema parity** (`sdk/src/golden/golden-policy.test.ts`):
- SDK must exactly match CJS schema — no drift
- Every valid key in manifest must have a test
- Unknown keys rejected

**Workstream path traversal** (#3589):
- `validateWorkstreamName('../../../outside')` → rejected
- `relPlanningPath(projectDir, '../../../outside')` → throws

**Mutation event decorators**:
- All mutation commands emit compatible event structures
- Cost tracking validated per session

**Transport policy tests** (`query-execution-policy.test.ts`):
- Native-preferred when available
- Subprocess fallback when native unavailable
- Timeout handling verified

### 9.4 Adversarial Test Fixtures (`tests/fixtures/adversarial/`)

- `frontmatter/` — malformed YAML frontmatter that could break plan parsing
- `roadmap/` — adversarial ROADMAP.md layouts (phase numbering gaps, circular deps)
- `security/` — prompt injection payloads in `.planning/` files

---

## 10. Key Architectural Decisions

### SDK-First Architecture (#3312)

Shared CJS/SDK helpers reduce drift via manifest files:
- Configuration module (shared manifest)
- Workstream inventory builder (pure projection, no I/O)
- STATE.md transforms
- Planning path routing
- Command definition registry

**Consequence**: Schema/default changes must update manifest files → parity tests catch drift.

### Transport Mode Abstraction

`gsd-transport-policy.ts` decouples from specific transports:
- Policies can swap implementations without changing callers
- `setTransportPolicy()` / `clearTransportPolicy()` for test isolation
- Observability via `onTransportDecision` callback

### Hooks as Soft Guards (Core Philosophy)

"Hooks advise, never block":
- Prompt injection → Warning, not rejection
- Read-before-edit → Guidance, not enforcement
- Workflow guard → Advisory, not gate
- Result: Zero false-positive deadlocks, preserved tool execution reliability

### Context Reduction Contract (#1614)

Large files injected into agent prompts are truncated using a deterministic algorithm:
- Preserves headings + first paragraph per section
- ROADMAP narrowed to current milestone
- Keeps prompts cache-friendly for repeated invocations (same content = cache hit)

### Workstream Security Boundary (#3589)

All planning path construction routes through `relPlanningPath()` — single seam for path traversal prevention. Env-sourced workstream names fail silently to root `.planning/` rather than throwing (per backward compatibility contract #2791).
