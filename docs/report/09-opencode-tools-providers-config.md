# OpenCode: Tools, Provider Layer, Model Fallback & Config

## 1. Tool Definition System

Tools use a strongly-typed **Effect-based** system.

**Foundation** (`packages/opencode/src/tool/tool.ts`):

```typescript
interface Def<Parameters, M extends Metadata> {
  id: string
  description: string
  parameters: Parameters              // Effect Schema for runtime validation
  jsonSchema?: JSONSchema7            // explicit JSON Schema override
  execute(args, ctx): Effect<ExecuteResult>
  formatValidationError?(error): string
}

interface ExecuteResult<M extends Metadata> {
  title: string
  metadata: M                         // custom metadata per tool
  output: string                      // main text result
  attachments?: FilePart[]            // optional file/image attachments
}

interface Context {
  sessionID: SessionID
  messageID: MessageID
  agent: string
  abort: AbortSignal
  callID?: string
  messages: MessageV2.WithParts[]    // full conversation context
  metadata(input): Effect<void>      // update output metadata
  ask(permission): Effect<void>      // request user permission
}
```

---

## 2. Complete Tool Catalog (21 Built-in Tools)

### FILE OPERATIONS

**READ** — Line-based with pagination
- Parameters: `filePath` (absolute), `offset?` (1-indexed), `limit?` (default 2000)
- Binary file detection (48+ formats by extension + heuristic)
- Image/PDF as base64 attachments
- Directory listing with sorted entries
- LSP file warming (async)
- "Did you mean?" suggestions on 404
- System reminders from Instruction service
- Max output: 50KB, max line: 2000 chars
- Metadata: `{ preview, truncated, loaded: string[] }`

**WRITE** — BOM-aware with auto-formatting
- Parameters: `filePath`, `content`
- BOM-aware (preserves byte order marks)
- Auto-formatting via Format service (prettier, eslint, etc.)
- LSP diagnostics (file + 5 related files)
- Creates parent directories
- File watcher events
- Metadata: `{ diagnostics, filepath, exists }`

**EDIT** — Fuzzy matching with 9 replacers
- Parameters: `filePath`, `oldString`, `newString`, `replaceAll?`
- Matching strategy (9 replacers, applied in order):
  1. `SimpleReplacer`: exact match
  2. `LineTrimmedReplacer`: trim lines
  3. `BlockAnchorReplacer`: anchor on first/last + Levenshtein similarity
  4. `WhitespaceNormalizedReplacer`: normalize whitespace runs
  5. `IndentationFlexibleReplacer`: ignore indentation
  6. `EscapeNormalizedReplacer`: unescape `\\n`, `\\t`
  7. `TrimmedBoundaryReplacer`: trim boundaries
  8. `ContextAwareReplacer`: context-based with 50% similarity threshold
  9. `MultiOccurrenceReplacer`: find all matches
- Semaphore-based file locking, line ending normalization (CRLF ↔ LF)
- Metadata: `{ diff, filediff, diagnostics }`

**GLOB** — Ripgrep-backed file search
- Parameters: `pattern` (glob), `path?`
- Results by mtime (newest first), limit 100 files
- Metadata: `{ count, truncated }`

**GREP** — Ripgrep-backed content search
- Parameters: `pattern` (regex), `path?`, `include?` (file glob)
- Limit 100 matches, sorted by mtime
- Metadata: `{ matches, truncated }`

### SHELL EXECUTION

**SHELL** — Full pseudo-terminal execution
- Parameters: `command`, `workdir?`, `timeout?` (default 2 min), `description?`
- Tree-sitter parsing (bash/PowerShell) for path extraction
- Permission scanning: detects `cd`, `rm`, `cp`, `mv`, `mkdir`, `touch`, `chmod`
- Concurrent environments support
- Output: streams to 2× truncation limit, tail kept, over-limit to temp file
  - Max: 1000 lines, 50KB tail
- Exit code tracking, timeout metadata
- Metadata: `{ output, exit, description, truncated, outputPath? }`

### SEARCH & WEB

**WEBSEARCH** — Multi-provider web search
- Parameters: `query`, `numResults?` (default 8), `livecrawl?`, `type?`, `contextMaxCharacters?`
- Providers: Exa API or Parallel Search (selected by flag or session ID hash)
- Override: `OPENCODE_WEBSEARCH_PROVIDER` env var
- Metadata: `{ provider }`

**WEBFETCH** — URL fetch with markdown conversion
- Parameters: `url` (https://), `format?` (`"text"|"markdown"|"html"`), `timeout?` (max 120s)
- HTML → Markdown (TurndownService)
- Image fetching as base64 data URL
- Content-Length limit: 5MB
- Cloudflare challenge retry with fallback UA

### LANGUAGE SERVER

**LSP** — Real-time code navigation
- Parameters: `operation` (goToDefinition|findReferences|hover|documentSymbol|workspaceSymbol|goToImplementation|prepareCallHierarchy|incomingCalls|outgoingCalls), `filePath`, `line` (1-based), `character` (1-based), `query?`
- Real-time code navigation, file availability check
- Gate: `flags.experimentalLspTool`
- Metadata: `{ result }`

### PATCHING

**APPLY_PATCH** — Custom patch format
- Parameters: `patchText` (full patch format)
- Patch format:
  ```
  *** Begin Patch
  *** Add File: path
  +content
  *** Update File: path
  *** Move to: newpath
  @@context@@
   unchanged
  -old
  +new
  *** Delete File: path
  *** End Patch
  ```
- Matching (4 passes): exact, right-strip, trim, Unicode normalization
- Gate: Used for GPT models instead of edit (`model.includes("gpt-") && !model.includes("oss")`)
- Metadata: `{ diff, files, diagnostics }`

### REPOSITORY (Experimental)

**REPO_CLONE** — Clone git repos (gate: `experimentalScout` flag)

**REPO_OVERVIEW** — Summarize repo structure (gate: `experimentalScout` flag)

### SUBAGENT & META

**TASK** — Launch subagent sessions
- Parameters: `description`, `prompt`, `subagent_type`, `task_id?` (resume), `command?`
- Permission-based subagent selection
- Session inheritance with permission derivation
- Task resumption by ID
- Returns `task_id` for continuation
- Metadata: `{ sessionId, model }`

**SKILL** — Inject skill content
- Parameters: `name`
- Skill lookup, content injection, file sampling (10 files)
- Metadata: `{ name, dir }`

**QUESTION** — Interactive user question
- Gate: `questionEnabled` flag (CLI/App/Desktop only)

**PLAN** — Plan mode enter/exit
- Gate: `experimentalPlanMode && client === "cli"`

**TODO** — Record TODO items (always available)

**INVALID** — Reject invalid tool calls (always first in tool list)

---

## 3. Tool Registry & Dynamic Loading

**File**: `packages/opencode/src/tool/registry.ts`

```typescript
interface ToolRegistry {
  ids(): Effect<string[]>
  all(): Effect<Tool.Def[]>
  named(): Effect<{ task, read }>
  tools(model): Effect<Tool.Def[]>    // filtered by model
}
```

**Discovery** (line 188-201):
1. Scans `{tool,tools}/*.{js,ts}` in config directories
2. Dynamic imports with `file://` URL handling (Windows compatibility)
3. Plugin tools via Plugin.Service

**Feature Gates**:
```typescript
questionEnabled = ["app", "cli", "desktop"].includes(flags.client) || flags.enableQuestionTool
websearch = webSearchEnabled(providerID, flags)
apply_patch vs edit = model.includes("gpt-") && !model.includes("oss")
repo_clone/overview = flags.experimentalScout
lsp = flags.experimentalLspTool
plan = flags.experimentalPlanMode && flags.client === "cli"
```

---

## 4. LLM / Provider Layer

**File**: `packages/opencode/src/provider/provider.ts` (1839 lines)

### Provider Service Interface

```typescript
interface Provider {
  list(): Effect<Record<ProviderID, Info>>
  getProvider(id): Effect<Info>
  getModel(providerID, modelID): Effect<Model>
  getLanguage(model): Effect<LanguageModelV3>    // initializes SDK
  closest(providerID, query[]): Effect<{providerID, modelID}?>
  getSmallModel(providerID): Effect<Model?>
  defaultModel(): Effect<{providerID, modelID}>
}
```

### Bundled Providers (21 packages)

All dynamically loaded via factory functions: Amazon Bedrock, Anthropic, Azure, Google, Google Vertex, OpenAI, OpenAI Compatible, OpenRouter, xAI, Mistral, Groq, DeepInfra, Cerebras, Cohere, Gateway, TogetherAI, Perplexity, Vercel, Alibaba, GitLab, Venice, GitHub Copilot.

### Model Schema

```typescript
Model: {
  id, name, family, release_date
  attachment, reasoning, temperature, tool_call, interleaved
  cost: { input, output, cache: {read, write}, tiers[], experimentalOver200K? }
  limit: { context, input?, output }
  modalities: { input: [text,audio,image,video,pdf], output: [...] }
  status: "alpha"|"beta"|"deprecated"|"active"
  provider: { npm?, api? }
  options: Record<string, any>
  headers: Record<string, string>
  variants?: Record<string, Record<string, any>>
}
```

---

## 5. Error Classification & Retry

**File**: `packages/opencode/src/provider/error.ts`

### Overflow Detection (30+ patterns)

```typescript
const OVERFLOW_PATTERNS = [
  "prompt is too long",           // Anthropic
  "exceeds the context window",   // OpenAI
  "context_length_exceeded",      // generic
  // status 413
  // "400/413 (no body)" (Cerebras, Mistral)
  // ... 25+ more
]
```

### Retryable Errors

```typescript
// 429, 503, 504, 529 (rate limit, service issues)
// OpenAI 404 (often available despite 404)
```

### Retry Logic (`packages/llm/src/route/executor.ts:334-353`)

```typescript
const MAX_RETRIES = 2
const BASE_DELAY_MS = 500
const MAX_DELAY_MS = 10_000

// Exponential backoff: [0.8, 1.2] * 500 * 2^attempt
// Respects Retry-After header (ms and seconds formats)
```

**Note**: Retry is automatic on 429/5xx. **Model fallback is NOT automatic** — errors surface to caller; session layer decides strategy.

---

## 6. Advanced Tool Features

### LSP Integration

**File**: `packages/opencode/src/lsp/index.ts`

- Manages LSP servers per language/project
- Starts language servers as child processes
- Tracks open documents, sends `textDocument/didOpen` on file read
- Provides real-time diagnostics after file write
- Supports: goToDefinition, findReferences, hover, documentSymbol, workspaceSymbol, goToImplementation, callHierarchy

### Snapshot System

**File**: `packages/opencode/src/snapshot/index.ts`

```typescript
track(): string | undefined          // current git hash
patch(hash): { hash, files[] }       // diff since snapshot
restore(snapshot): void              // rollback
revert(patches[]): void              // undo patches
diff(hash): string                   // git diff text
diffFull(from, to): FileDiff[]       // structured diffs
```

- Respects `.gitignore`
- Ignores large files (>2MB)
- Garbage collection (prune 7 days)
- Concurrent-safe (semaphores)

### Worktree Management

**File**: `packages/opencode/src/worktree/index.ts`

```typescript
create(input?): Info            // Auto-named, auto-branched
list(): WorktreeInfo[]
remove(input): boolean
reset(input): boolean
```

- Auto-generated names with collision avoidance (26 attempts)
- Auto-branch creation: `opencode/{name}`
- Git submodule handling
- FSMonitor cleanup

### PTY (Pseudo-Terminal) Support

**File**: `packages/opencode/src/pty/index.ts`

- Full PTY for interactive commands (REPL, vim, etc.)
- WebSocket-based IPC to TUI
- Resize handling
- Input forwarding from UI to process

---

## 7. Configuration System

### Config File Format

**Location**: `~/.opencode/config.yml` or `$OPENCODE_CONFIG`

```yaml
model: "openai/gpt-4"
small_model: "openai/gpt-4-mini"

provider:
  openai:
    name: "OpenAI"
    env: ["OPENAI_API_KEY"]
    api: "https://api.openai.com/v1"
    options:
      timeout: 300000
      chunkTimeout: 30000
    models:
      gpt-4:
        name: "GPT-4"
        cost: { input: 0.03, output: 0.06 }
        limit: { context: 8192, output: 2048 }

lsp:
  typescript:
    cmd: "typescript-language-server"
    args: ["--stdio"]

mcp:
  - name: "sqlite"
    cmd: "mcp-sqlite"
    args: ["./database.db"]

permissions:
  - permission: "shell"
    patterns: ["npm *"]
    action: "allow"

skills:
  - path: "./skills/react"

snapshot: true
```

### Config Loading Precedence (highest → lowest)

1. Environment variables (`OPENCODE_MODEL`, etc.)
2. Auth service (stored credentials)
3. Inline options in config file
4. Env vars in `provider.env` list
5. Defaults from models.dev

### Key Env Vars

```bash
OPENCODE_MODEL=provider/model_id
OPENCODE_CONFIG=/path/to/config.yml
OPENCODE_WEBSEARCH_PROVIDER=exa|parallel
OPENCODE_ENABLE_EXPERIMENTAL_MODELS=1
OPENCODE_EXPERIMENTAL_BASH_DEFAULT_TIMEOUT_MS=120000
```

---

## 8. Key Differences from Pi

| Aspect | Pi | OpenCode |
|--------|----|----|
| **Tools** | ~7 basic | 21+ with dynamic loading |
| **LSP** | No | Full integration (9 operations) |
| **Providers** | Fixed 9+ | 21+ dynamic with factory functions |
| **Worktrees** | No | Git worktrees + subagents |
| **Snapshots** | No | Full git-based with diffing |
| **Config** | settings.json | config.yml + env vars + auth service |
| **Patching** | Edit only | apply_patch format (GPT) + edit (Anthropic) |
| **Model Fallback** | Manual | Error classification only (no auto-fallback) |
| **Permissions** | Extension hooks | Ruleset + wildcard matching + deferred approval |
| **Subagents** | Separate process spawn | Built-in agents with permission derivation |
| **Effect Framework** | No | Pervasive (736+ methods) |
| **Tool Validation** | TypeBox | Effect Schema |
| **Auto-format on write** | No | Yes (prettier, eslint, etc.) |
