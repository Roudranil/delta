# Pi Agent: Tools, MCP, Skills, Rules, Extensions, Configuration

## Executive Summary

The pi coding agent is built on a **layered architecture**:
- **Base layer** (`agent` package): Low-level agent loop with tool calling, hooks, and session management
- **Application layer** (`coding-agent` package): Extension system, built-in tools, UI modes, configuration

**Key Finding:** There is **NO MCP (Model Context Protocol) integration**. The system uses direct tool calling with TypeScript objects.

---

## 1. Tool Calling System

### Core Tool Interface (`AgentTool`)

**File:** `packages/agent/src/types.ts:352-376`

```typescript
export interface AgentTool<TParameters extends TSchema = TSchema, TDetails = any> {
  name: string;
  description: string;
  label: string;  // UI display label

  parameters: TParameters;  // TypeBox schema

  execute(
    toolCallId: string,
    params: Static<TParameters>,
    signal?: AbortSignal,
    onUpdate?: AgentToolUpdateCallback<TDetails>,
  ): Promise<AgentToolResult<TDetails>>;

  prepareArguments?: (args: unknown) => Static<TParameters>;  // compat shim
  executionMode?: ToolExecutionMode;  // "sequential" | "parallel"
}
```

### Tool Result Format

**File:** `packages/agent/src/types.ts:337-350`

```typescript
export interface AgentToolResult<T> {
  content: (TextContent | ImageContent)[];  // Returned to model
  details: T;                               // Structured metadata for UI/logging
  terminate?: boolean;                      // Stop batch if all tools set true
}
```

### Tool Execution Modes

- **`"sequential"`**: One tool at a time; next doesn't start until previous is finalized
- **`"parallel"`**: All tools prepared sequentially, then allowed tools execute concurrently; results emitted in completion order

### Request → Call → Result Cycle

```
1. beforeToolCall hook
   └─ AgentHarness emits "tool_call" event
   └─ Extensions can return { block: true } to prevent execution

2. Argument validation
   └─ validateToolArguments() from pi-ai
   └─ prepareArguments() shim if provided

3. Tool.execute() called
   └─ Direct implementation (built-in tools)
   └─ Wrapped by extension layer (custom tools from extensions)

4. Result captured as AgentToolResult<T>

5. afterToolCall hook
   └─ AgentHarness emits "tool_result" event
   └─ Extensions can modify: content, details, isError, terminate

6. ToolResultMessage appended to context
```

---

## 2. MCP (Model Context Protocol) — NOT IMPLEMENTED IN PI

**Finding:** Zero MCP support in the pi codebase.
- No `*mcp*` files found anywhere
- No MCP server discovery
- No MCP resource handling
- No MCP tool adapters

The system uses **direct tool calling only** where tools are defined as TypeScript objects. See the opencode reports (08-11) for a full MCP implementation reference.

---

## 3. Skills System

### Skill Format (`SKILL.md`)

**File:** `packages/agent/src/harness/skills.ts:12-23`

Skills are discovered from `SKILL.md` files with YAML frontmatter:

```markdown
---
name: my-skill
description: Short description of when to use this skill
disable-model-invocation: false
---

# Full instructions for the model
Detailed guidance on how and when to use this skill.
```

**Validation Rules:**
- Name: lowercase alphanumeric with hyphens only, max 64 chars, must match parent directory name
- Description: required, max 1024 chars
- No leading/trailing hyphens, no consecutive hyphens

### Skill Loading

**File:** `packages/agent/src/harness/skills.ts:40-54`

```typescript
export async function loadSkills(
  env: ExecutionEnv,
  dirs: string | string[],
): Promise<{ skills: Skill[]; diagnostics: SkillDiagnostic[] }> {
  // Traverses directories recursively
  // Discovers SKILL.md files
  // Honors .gitignore, .ignore, .fdignore
}
```

**Discovery Rules:**
- If dir contains `SKILL.md` → treat as skill root, don't recurse
- Otherwise → load `.md` files from root directory
- Recurse into subdirectories to find `SKILL.md` files

### Skill Injection into System Prompt

Skills are formatted as XML blocks injected into the system prompt:

```typescript
// packages/agent/src/harness/skills.ts:29-32
export function formatSkillInvocation(skill: Skill, additionalInstructions?: string): string {
  return `<skill name="${skill.name}" location="${skill.filePath}">
References are relative to ${dirnameEnvPath(skill.filePath)}.

${skill.content}
</skill>`;
}
```

### Sourced Skills (Provenance Tracking)

Applications can load skills with source metadata:

```typescript
// packages/agent/src/harness/skills.ts:62-80
export async function loadSourcedSkills<TSource>(
  env: ExecutionEnv,
  inputs: Array<{ path: string; source: TSource }>,
  mapSkill?: (skill: Skill, source: TSource) => TSkill,
): Promise<{
  skills: Array<{ skill: TSkill; source: TSource }>;
  diagnostics: Array<SkillDiagnostic & { source: TSource }>;
}>
```

Source types: `"user"` (from `~/.pi/`), `"project"` (from `./.pi/`), `"path"` (arbitrary path)

---

## 4. Rules System — NOT FORMALIZED IN PI

No dedicated rules engine exists. Instead:

1. **System Prompt Guidelines** — Textual rules in the system prompt
2. **Tool Constraints** — Schema validation, execution modes
3. **Extension Hooks** — Event-based enforcement
4. **Agent Configuration** — Tool selection, model choice, thinking level

### Guidelines in System Prompt

**File:** `packages/coding-agent/src/core/system-prompt.ts:95-129`

Guidelines are conditionally added based on available tools:

```typescript
if (hasBash && !hasGrep && !hasFind && !hasLs) {
  addGuideline("Use bash for file operations like ls, rg, find");
} else if (hasBash && (hasGrep || hasFind || hasLs)) {
  addGuideline("Prefer grep/find/ls tools over bash for file exploration");
}
```

---

## 5. Extensions System

### Extension Factory and API

**File:** `packages/coding-agent/src/core/extensions/types.ts:1084-1311`

Extensions are TypeScript modules with a factory function:

```typescript
export type ExtensionFactory = (pi: ExtensionAPI) => void | Promise<void>;

// Example:
export default async (pi) => {
  pi.registerTool({ name: "my-tool", ... });
  pi.on("before_agent_start", async (event, ctx) => { ... });
  pi.registerCommand("my-command", { ... });
};
```

### Extension Context (for event handlers)

```typescript
// packages/coding-agent/src/core/extensions/types.ts:298-327
export interface ExtensionContext {
  ui: ExtensionUIContext;
  cwd: string;
  sessionManager: SessionManager;
  modelRegistry: ModelRegistry;
  model: Model<any> | undefined;
  signal: AbortSignal | undefined;
  isIdle(): boolean;
  getContextUsage(): ContextUsage;
  compact(options?: CompactOptions): void;
  getSystemPrompt(): string;
}
```

### Tool Registration from Extensions

```typescript
// packages/coding-agent/src/core/extensions/types.ts:423-473
export interface ToolDefinition<TParams extends TSchema = TSchema, TDetails = unknown, TState = any> {
  name: string;
  label: string;
  description: string;
  parameters: TParams;
  promptSnippet?: string;       // Shown in system prompt
  promptGuidelines?: string[];  // Added to guidelines section
  executionMode?: ToolExecutionMode;

  execute(
    toolCallId: string,
    params: Static<TParams>,
    signal: AbortSignal | undefined,
    onUpdate: AgentToolUpdateCallback<TDetails> | undefined,
    ctx: ExtensionContext,
  ): Promise<AgentToolResult<TDetails>>;

  renderCall?: (...) => Component;    // UI for tool call display
  renderResult?: (...) => Component;  // UI for result display
}
```

**Key Difference from AgentTool:** Extension tools receive `ctx: ExtensionContext` with full access to session, model registry, and UI.

### Extension Events

**File:** `packages/coding-agent/src/core/extensions/types.ts:950-972`

Major event categories:
- **Session**: `session_start`, `session_before_switch`, `session_before_fork`, `session_before_compact`, `session_compact`, `session_before_tree`, `session_tree`
- **Agent**: `before_agent_start`, `agent_start`, `agent_end`, `turn_start`, `turn_end`, `context`
- **Messages**: `message_start`, `message_update`, `message_end`
- **Tools**: `tool_call`, `tool_result`, `tool_execution_start`, `tool_execution_update`, `tool_execution_end`
- **Model**: `model_select`, `thinking_level_select`
- **Input**: `input`, `user_bash`
- **Resources**: `resources_discover`

### Extension Loading

Extensions are loaded with `jiti` (runtime TypeScript loader) with:
- Virtual modules for bundled packages (typebox, pi-ai, pi-tui, pi-coding-agent)
- Module aliases for workspace resolution
- Support for Bun compiled binary with custom filesystem

**Loading directories:**
- `~/.pi/extensions/*.ts` (user extensions)
- `./.pi/extensions/*.ts` (project extensions)

---

## 6. Configuration System

### Configuration Paths

**Default Locations:**
```
~/.pi/                          # CONFIG_DIR_NAME
~/.pi/agent/                    # getAgentDir()
~/.pi/agent/models.json         # getModelsPath()
~/.pi/agent/auth.json           # getAuthPath()
~/.pi/agent/settings.json       # getSettingsPath()
~/.pi/agent/themes/             # getCustomThemesDir()
~/.pi/agent/prompts/            # getPromptsDir()
~/.pi/agent/sessions/           # getSessionsDir()
~/.pi/extensions/               # Extensions discovery
```

### Environment Variable Overrides

```
<APP_NAME>_CODING_AGENT_DIR          # Override agent dir
<APP_NAME>_CODING_AGENT_SESSION_DIR  # Override session dir
PI_SHARE_VIEWER_URL                  # Share viewer URL base
PI_PACKAGE_DIR                       # Override package asset dir
```

### Configuration Files

**`models.json`**: Defines available LLM providers and models with model ID, name, API type, base URL, API key env var, cost, context window, max tokens, reasoning support.

**`auth.json`**: Stores per-provider API keys, OAuth tokens, credentials metadata.

**`settings.json`**: User-selected settings — preferred model, thinking level, active tool set, shell path, theme selection, custom keybindings.

### Resource Loading Precedence (Highest → Lowest)

1. CLI flags and environment variables
2. `settings.json` (user selections)
3. `auth.json` (credentials)
4. `models.json` (model definitions)
5. Built-in package defaults
6. Extension-provided defaults (via `resources_discover` hook)

### Resource Discovery Paths (for each resource type)

1. Project-level: `./.pi/`
2. User-level: `~/.pi/`
3. Extension-provided: Via `resources_discover` event
4. Built-in: Shipped with package

---

## 7. Modes

The coding-agent supports three run modes:

### Interactive Mode (TUI)
**File:** `packages/coding-agent/src/modes/interactive/interactive-mode.ts`
- Terminal User Interface with pi-tui
- Real-time streaming display
- Keyboard shortcuts and theme support
- Custom editor component support
- Full interactive control

### Print Mode
**File:** `packages/coding-agent/src/modes/print-mode.ts`
- Non-interactive output to stdout
- Useful for scripting and shell piping
- No UI components

### RPC Mode
**File:** `packages/coding-agent/src/modes/rpc/rpc-mode.ts`
- JSON-RPC protocol for remote clients
- Suitable for IDE plugins, web interfaces
- Full protocol in `packages/coding-agent/src/modes/rpc/rpc-types.ts`

---

## 8. Built-in Tool Catalog

**File:** `packages/coding-agent/src/core/tools/index.ts:83-196`

```typescript
export type ToolName = "read" | "bash" | "edit" | "write" | "grep" | "find" | "ls";

// Predefined tool sets:
createCodingTools(cwd, options)    // [read, bash, edit, write]
createReadOnlyTools(cwd, options)  // [read, grep, find, ls]
createAllTools(cwd, options)       // All 7 tools
```

| Tool | Input Schema | Purpose |
|------|-------------|---------|
| **read** | `{ filePath: string }` | Read file contents with truncation |
| **bash** | `{ command: string; timeout?: number }` | Execute shell command |
| **edit** | `{ filePath: string; changes: EditOperation[] }` | Apply edits to file |
| **write** | `{ filePath: string; content: string \| Uint8Array; overwrite?: boolean }` | Write/create file |
| **grep** | `{ pattern: string; globs?: string[]; ignoreCase?: boolean }` | Regex file search |
| **find** | `{ pattern: string; globs?: string[] }` | Path pattern search |
| **ls** | `{ path: string; long?: boolean }` | List directory |

### Bash Tool Execution

**File:** `packages/coding-agent/src/core/tools/bash.ts:65-127`

```typescript
export interface BashOperations {
  exec(
    command: string,
    cwd: string,
    options: {
      onData: (data: Buffer) => void;
      signal?: AbortSignal;
      timeout?: number;
      env?: NodeJS.ProcessEnv;
    },
  ): Promise<{ exitCode: number | null }>;
}
```

Features:
- Detached process groups (Unix)
- Timeout with SIGTERM + SIGKILL escalation
- Abort signal support
- Streaming stdout/stderr

---

## 9. System Prompt Construction

**File:** `packages/coding-agent/src/core/system-prompt.ts:28-80`

```typescript
export interface BuildSystemPromptOptions {
  customPrompt?: string;
  selectedTools?: string[];
  toolSnippets?: Record<string, string>;
  promptGuidelines?: string[];
  appendSystemPrompt?: string;
  cwd: string;
  contextFiles?: Array<{ path: string; content: string }>;
  skills?: Skill[];
}
```

**System Prompt Structure (if no custom prompt):**
1. Standard preamble explaining pi and agent role
2. "Available tools:" section (only tools with snippets shown)
3. "Guidelines:" section with conditional rules based on tool set
4. References to pi documentation paths
5. Project context files
6. Skills section (as XML skill blocks)
7. Current date and working directory
