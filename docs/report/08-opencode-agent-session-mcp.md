# OpenCode: Agent Architecture, Session, MCP, Subagents, Bus, Permissions

## 1. Agent Architecture

**File**: `packages/opencode/src/agent/agent.ts`

### Five Built-in Agents

```typescript
// Agent.Info schema (lines 28-48):
{
  name: string,
  mode: "primary" | "subagent" | "all",
  permission: Ruleset,    // permission overrides
  model?: string,
  temperature?: number,
  topP?: number,
  prompt?: string,        // custom system prompt
}
```

Built-in agents:
- **`build`**: Primary, full access — the default coding agent
- **`plan`**: Primary, read-only mode — denies all edit operations
- **`general`**: Subagent for multistep work
- **`explore`**: Subagent for codebase search (grep/glob/read only)
- **`scout`**: Experimental docs/dependency specialist
- **`compaction`**, **`title`**, **`summary`**: Internal hidden agents

### Permission Merging

Defaults + config overrides + per-agent rules with explicit precedence (lines 103-322).

---

## 2. Agent Loop (Turn Cycle)

**Key Files**: `session/prompt.ts`, `session/processor.ts`, `session/llm.ts`

**Flow**:
1. User message -> `MessageV2` with parts (text, files)
2. System prompt assembly (model-specific + agent override + instructions + max steps + mode reminders)
3. LLM call via AI SDK: `streamObject()` with tools, model, temperature overrides
4. Stream events (tool calls, text deltas, step finish)
5. **Tool dispatch**: Check permissions -> execute -> capture output -> feed back to LLM
6. Stop conditions: `end_turn`, permission denied, doom loop (3+ failures), token overflow, max iterations

**Permission Check** (`processor.ts ~line 115-140`): Before each tool execution, call `permission.ask()` which evaluates ruleset. Returns `DeniedError`/`RejectedError`/`CorrectedError` on denial.

**Message Structure**:
- `MessageV2.Assistant`: id, sessionID, role, tokens, modelID, providerID
- Parts: TextPart, ToolPart (state: pending/running/completed/error), ReasoningPart, FilePart, CompactionPart

---

## 3. Session Management

**File**: `packages/opencode/src/session/session.ts` (993 lines)

**Session.Info Schema**:
```
id, slug, projectID, workspaceID, directory, path, parentID (branching),
title, agent, model (with variant), version, cost, tokens (input/output/reasoning/cache),
share URL, time (created/updated/compacting/archived), permission ruleset, revert info
```

**Storage**: SQLite with Drizzle ORM
- `SessionTable`: Metadata rows
- `PartTable`: One row per message part with JSON data field
- `PermissionTable`: Approval rules per project

**Key Operations**:
- `create()`: New session with title, agent, model, permission overrides
- `fork()`: Branch from message point, copy messages and parts, update tool refs
- `messages()`: Cursor-based pagination (default 100 items/page)
- `setTitle()`, `setPermission()`, `setArchived()`, `setSummary()`, `setRevert()`

**Events**: `Event.Created`, `Event.Updated` (SyncEvent), `Event.Deleted`, `Event.Diff`, `Event.Error`

---

## 4. MCP (Model Context Protocol) — Full Implementation

**File**: `packages/opencode/src/mcp/index.ts` (959 lines)

This is the key feature pi lacks entirely.

### Three Transport Types

**1. StdioClientTransport (Local Process)**
```typescript
// Spawns subprocess with command + args (line 416-445)
// stdin/stdout communication
// Environment variables passable
// Process lifecycle managed (kill descendants on shutdown)
```

**2. StreamableHTTPClientTransport (Remote HTTP)**
```typescript
// Tries HTTP first, falls back to SSE (line 330-345)
// OAuth support via McpOAuthProvider (lines 309-328)
// Custom headers, timeouts configurable
// Retry logic with tolerant schema fallback
```

**3. SSEClientTransport (Server-Sent Events)**
```typescript
// Fallback for remote connections
// Streaming subscriptions
// Same OAuth support
```

### Connection Flow (lines 296-445)

1. `connectRemote()`: Parse URL -> create OAuth provider if needed -> try StreamableHTTP -> fall back to SSE
2. `connectLocal()`: Create StdioClientTransport with command, env, stderr logging
3. Instantiate `Client` and call `client.connect(transport)`
4. Call `listTools()` with error handling (retry with tolerant schema if outputSchema validation fails)

### MCP Tool Registration

```typescript
// listTools() fetches definitions with timeout and error recovery
// convertMcpTool() wraps each tool in AI SDK dynamicTool()
// Tool name: sanitize(clientName) + "_" + sanitize(toolName)
// Execution wrapped in client.callTool() with resetTimeoutOnProgress
```

### MCP Resources & Prompts

```typescript
// listResources(): Available resources (context injection)
// listPrompts(): Dynamic prompts that can be evaluated with args
// getPrompt(name, args): Evaluate prompt -> inject into system prompt
// readResource(uri): Fetch resource content
```

### MCP OAuth Flow

1. `startAuth()`: Generate state, create auth provider, open browser to authorization URL
2. Wait for callback: `McpOAuthCallback.waitForCallback(oauthState, mcpName)`
3. `finishAuth(authorizationCode)`: Call `transport.finishAuth()`, store tokens
4. Tokens stored in `~/.opencode/data/mcp-auth.json`

### MCP Tool Hot Reload

- MCP server emits `tools/list_changed` notification
- OpenCode re-fetches tools, updates cache
- Publishes `ToolsChanged` event to UI

### MCP Error Handling

```typescript
// outputSchema validation error -> retry with tolerant schema (omit outputSchema)
// UnauthorizedError -> status needs_auth + toast notification
// Connection errors -> retry fallback -> final status "failed"
// Cleanup: Kill subprocess descendants, close clients, clear pending OAuth transports
```

### MCP Configuration (config.yml)

```yaml
mcp:
  - name: "sqlite"
    cmd: "mcp-sqlite"
    args: ["./database.db"]
  - name: "remote-tool"
    url: "https://mcp.example.com"
    headers:
      Authorization: "Bearer ${API_TOKEN}"
```

---

## 5. Event Bus Architecture

**File**: `packages/opencode/src/bus/index.ts` (204 lines)

### Two-tier event model

- **Bus**: In-memory typed pub/sub using Effect.js `PubSub`
- **SyncEvent**: Durable persistence + bus publication

**Payload Structure**:
```typescript
{ id: string, type: string, properties: T }
```

**Subscription Modes**:
```typescript
subscribe<D>(eventDef) -> Stream<Payload<D>>
subscribeCallback<D>(def, callback) -> Effect<() => void>
subscribeAll()  // for all event types
```

**Key Events**:
- Session: `created`, `updated` (SyncEvent), `deleted`, `diff`, `error`
- Permission: `asked`, `replied`
- MCP: `tools.changed`, `browser.open.failed`
- Bus: `server.instance.disposed`

**GlobalBus Bridge** (`bus/global.ts`):
Emits per-instance events to global stream: `{ directory, project, workspace, payload }` — used for remote clients.

---

## 6. ACP (Agent Client Protocol)

**Files**: `acp/agent.ts` (1971 lines), `acp/session.ts`, `acp/README.md`

ACP enables remote clients (Zed, etc.) to control OpenCode agent via JSON-RPC.

### ACPAgent Interface

```typescript
initialize(InitializeRequest)
newSession(NewSessionRequest)
loadSession(LoadSessionRequest)
listSessions(ListSessionsRequest)
closeSession(CloseSessionRequest)
prompt(PromptRequest)
setSessionMode(SetSessionModeRequest)
setSessionModel(SetSessionModelRequest)
cancel(CancelNotification)
```

### Prompt Handling

```typescript
// Parse ACP blocks (text, image, resource_link, resource)
// Convert to OpenCode parts
// Check for commands (/compact, /command_name, etc.)
// Call SDK session.prompt() or session.command()
// Return { stopReason: "end_turn", usage }
```

### Event Streaming to Client

- Subscribe to `sdk.global.event()` stream
- Handle events:
  - `permission.asked`: Request from client via `connection.requestPermission()`, handle reply
  - `message.part.updated`: Send tool updates as they progress (pending -> running -> completed)
  - `message.part.delta`: Stream text and reasoning chunks

---

## 7. Permission System

**File**: `packages/opencode/src/permission/index.ts` (307 lines)

### Ruleset Model

```typescript
Rule { permission, pattern, action: "allow" | "deny" | "ask" }
Ruleset = Rule[]
```

### Evaluation (lines 161-196)

1. Check each pattern against rulesets
2. If deny -> fail with `DeniedError`
3. If allow -> continue
4. If ask -> publish `permission.asked` event, wait for user reply
5. On "always" -> add to approved rules (persisted)

### Default Rules (agent.ts lines 103-122)

```typescript
{ permission: "*", action: "allow" }
{ permission: "doom_loop", action: "ask" }
{ permission: "external_directory", action: "allow", patterns: [skillDirs, tempDir, ...] }
{ permission: "question", action: "deny" }
{ permission: "read", action: "ask", pattern: ".env*" }  // ask for .env files
{ permission: "read", action: "ask", pattern: "*.env.*" }
```

### Subagent Permission Derivation

**File**: `agent/subagent-permissions.ts`

```typescript
deriveSubagentSessionPermission({
  parentSessionPermission,
  parentAgent,
  subagent
}): Ruleset
```

- Inherits parent agent's edit denies (prevents plan mode bypass via subagent)
- Inherits parent session's denies and external_directory rules
- Adds defaults for todowrite/task if not permitted

---

## 8. Background Jobs

**File**: `packages/opencode/src/background/job.ts` (201 lines)

```typescript
// Job lifecycle:
start({ id?, type, title?, metadata?, run: Effect<string> }): JobInfo
wait({ id, timeout? }): Effect<JobResult>
cancel(id): void
list(): JobInfo[]
get(id): JobInfo | undefined

// Status: "running" | "completed" | "error" | "cancelled"
```

Background jobs are forked Effects managed with proper resource cleanup.

---

## 9. Skill System

**File**: `packages/opencode/src/skill/index.ts` (324 lines)

**Discovery Patterns**:
- Global external: `~/.claude/skills/**/SKILL.md`, `~/.agents/skills/**/SKILL.md`
- Project external: `.claude/skills/**/SKILL.md`, `.agents/skills/**/SKILL.md`
- Configured: `config.skills.paths` and `config.skills.urls`
- Built-in: `skill/` or `skills/` in opencode config dir

**Built-in Skill**: `customize-opencode` — teaches model about opencode config schema.

**Availability**: Filtered by agent permission (deny rules remove skills).

---

## 10. Subagent System

### Invocation

Via `@subagent_name` syntax in prompts, or the `task` tool.

### Built-in Subagent Agents

- **general**: Multistep work, full tool access
- **explore**: Codebase search only (fast, read-only, grep/glob/read)
- **scout**: Docs and dependencies specialist

### Permission Inheritance

Parent agent's edit denies propagate to subagents (prevents privilege escalation via subagent). Example: A `plan` mode session spawns a subagent — the subagent cannot edit files even if general allows edits.

### Isolation

- New session ID
- Separate message history
- Same project/worktree access
- Permissions derived from parent

---

## Key Architectural Notes for Python Port

1. **MCP complexity**: Three transport types (stdio, HTTP, SSE), OAuth PKCE flow, subprocess management, tool hot-reload — this is a significant implementation effort
2. **Permission system**: Wildcard/glob matching, ruleset merging with precedence, deferred approval via async events
3. **Session persistence**: SQLite schema with cursor-based pagination
4. **Two-tier event model**: In-memory bus + durable sync events + global bridge
5. **Agent configuration**: Three-level merging (built-in -> config -> per-agent)
6. **ACP protocol**: Remote client control via JSON-RPC with bi-directional updates
7. **Token tracking**: Separate counters for input/output/reasoning/cache with context-size-aware cost tiers
