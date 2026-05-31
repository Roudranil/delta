# Pi Agent: Harness Architecture & Agent Loop

## 1. Harness Architecture

**AgentHarness** (`packages/agent/src/harness/agent-harness.ts:119-248`) is the top-level orchestrator. It owns:

- **Agent instance**: Manages conversation state and event emission
- **Session**: Persists conversation history with tree structure supporting branching
- **Resources**: Skills and prompt templates available to the agent
- **Stream options**: HTTP headers, retries, timeouts, cache settings
- **System prompt**: Can be static string or dynamic callback receiving turn context
- **Execution environment**: Filesystem and shell access (ExecutionEnv)
- **Event listeners & hooks**: Extensibility points for middleware-style interception

**State fields**:
- `phase`: `"idle" | "turn" | "compaction" | "branch_summary" | "retry"`
- `pendingSessionWrites`: Batches writes to prevent partial updates if errors occur
- `appliedStreamOptions`, `appliedSessionId`: Snapshotted per turn

**Initialization** wraps `streamSimple` from pi-ai to:
- Merge auth headers with stream options
- Emit `before_provider_request` hook for modification
- Emit `before_provider_payload` hook for interception
- Emit `after_provider_response` event

---

## 2. Agent Loop

**Two layers** (`agent-loop.ts`, `agent.ts`):

- **Low-level loop** (`agentLoop`, `agentLoopContinue`): Turn-by-turn orchestration of LLM calls, tool execution, streaming
- **Stateful Agent wrapper**: Manages transcript, event emission, queuing (steering/follow-up)

**Core cycle**:
1. Prompt messages added to context
2. LLM streams response (partial message updated in context during streaming)
3. Tool calls extracted and executed (sequential or parallel mode)
4. Steering messages can inject mid-run after tool execution
5. Follow-up messages process only after agent would stop

**Nested loop structure** (`agent-loop.ts:170-253`):

```
Outer loop (follow-up handling):
  Inner loop (steering & tool execution):
    Process pending steering messages
    Stream assistant response
    Execute tool calls
    Check for more steering
  After inner stops:
    Check for follow-up; if found, loop again
```

**Tool execution modes**:
- **Sequential**: Each tool prepared, executed, finalized before next
- **Parallel**: All tools prepared sequentially, then execute concurrently; `tool_execution_end` emitted in completion order

**Early termination**: When **every** tool result in a batch has `terminate: true`, the agent stops after that batch instead of continuing LLM calls.

---

## 3. Event Flow & Streaming

**Agent loop events** (`types.ts:395-411`):
- `agent_start`, `agent_end`
- `turn_start`, `turn_end`
- `message_start`, `message_update` (streaming deltas), `message_end`
- `tool_execution_start`, `tool_execution_update`, `tool_execution_end`

**Harness-specific events** (`harness/types.ts`):
- Queue updates, save points, abort, settled
- Provider request/payload/response interception
- Tool call/result interception (hooks)
- Compaction and tree navigation events
- Model/thinking level changes

**Streaming** (`agent-loop.ts:275-368`):
1. Apply `transformContext` if configured (AgentMessage[] → AgentMessage[])
2. Convert to LLM format with `convertToLlm` (AgentMessage[] → Message[])
3. Stream from LLM, updating partial message in context as deltas arrive
4. On completion, replace partial or add to context, emit `message_end`

---

## 4. Session Management

**Tree structure** (`session.ts`):
- Each entry has `id`, `parentId` (or null), `timestamp`, `type`
- Supports branching and tree navigation
- **Leaf**: Current position in the tree

**Entry types**:
- MessageEntry (user/assistant/toolResult)
- ThinkingLevelChangeEntry, ModelChangeEntry
- CompactionEntry, BranchSummaryEntry
- CustomEntry, CustomMessageEntry
- LabelEntry, SessionInfoEntry

**JSONL Format** (`storage/jsonl.ts`):
```jsonl
{"type":"session","version":3,"id":"...","timestamp":"...","cwd":"..."}
{"type":"message","id":"...","parentId":null,"timestamp":"...","message":{...}}
```

Append-only, line-delimited JSON. On load, builds in-memory indices (`byId`, `labelsById`, current `leafId`).

**Session context building** (`session.ts:20-75`):
- Traces the path from leaf to root, collecting entries
- Handles compaction specially: includes summary, skips old messages before `firstKeptEntryId`
- Converts entries to AgentMessage[] for LLM context

**Repository** (`repo/jsonl.ts`):
- Manages multiple session files per working directory
- Encodes cwd as `--{path-with-dashes}--` for directory structure
- Supports: create, open, list (by recency), delete, fork

---

## 5. Context Management

**Token estimation** (`compaction/compaction.ts:239-297`):
- Heuristic: `chars / 4 ≈ tokens` (conservative overestimate)
- Images estimated as 1200 tokens (4800 chars)
- Uses LLM usage data when available for accuracy

**Context transformation hook** (`agent-harness.ts:204-207`):
- Called before each LLM call
- Apps can prune old messages, inject context, reorder
- Returns transformed AgentMessage[]

**Token tracking** (`compaction/compaction.ts:193-221`):
- Combines actual LLM usage (via `totalTokens` field) with estimated tokens for new messages
- Provides accurate token count for context window management

---

## 6. Compaction

**Trigger** (`compaction.ts:226-229`):
```typescript
contextTokens > contextWindow - reserveTokens
```
Default: reserve 16k tokens, keep recent 20k tokens

**Process** (`agent-harness.ts:534-582`):
1. Get preparation via `prepareCompaction()` (identifies messages to summarize)
2. Emit `session_before_compact` hook (can provide custom compaction or cancel)
3. Call `compact()` LLM function to generate summary if no hook result
4. Append CompactionEntry with:
   - Summary text
   - `firstKeptEntryId` (first entry NOT summarized)
   - `tokensBefore` (pre-compaction context size)
   - Details (file operations, etc.)
   - `fromHook` flag

**Message serialization** (`compaction/utils.ts:109-150`) — prevents model from treating summarization as continuation:
```
[User]: content
[Assistant thinking]: ...
[Assistant]: ...
[Assistant tool calls]: tool1(...), tool2(...)
[Tool result]: truncated to 2000 chars
```

**File operation tracking**:
- Extracts read/write/edit calls from tool arguments
- Stores in summary as XML tags for context in future summaries

---

## 7. Branch Summarization & Tree Navigation

**`navigateTree()`** (`agent-harness.ts:584-691`):
1. Collect entries being left (from old leaf back to common ancestor with target)
2. Emit `session_before_tree` hook
3. Generate summary via LLM if requested (similar to compaction)
4. Move leaf to target entry
5. Optionally create BranchSummaryEntry with summary

**Entry collection** (`branch-summarization.ts:98-136`):
- Finds common ancestor between old and new positions
- Collects entries in chronological order
- **Does not stop at compaction boundaries** (summaries become context)

---

## 8. Fault Tolerance

**Error handling**:
- **LLM errors** (`stopReason: "error"/"aborted"`): Loop exits, error encoded in message
- **Tool errors**: Caught, encoded as error tool results with content and details
- **Tool execution blocking**: `beforeToolCall` hook can block with reason
- **Session consistency**: Writes batched in `pendingSessionWrites`, flushed at turn end

**Pending writes** (`agent-harness.ts:370-390`):
- Messages, model changes, thinking level changes, custom entries, labels all batched
- Flushed in `finally` block at turn end
- If error occurs mid-turn, writes not flushed → session remains consistent

**Abort/cancellation** (`agent.ts:290-298`):
- AbortSignal threaded through execution
- `AgentHarness.abort()` clears queues, calls agent abort, waits for idle
- Returns cleared steer/follow-up queues

---

## 9. Execution Environment

**`ExecutionEnv` interface** (`types.ts:139-174`):
- `cwd`: Current working directory
- `exec()`: Shell command execution with stdout/stderr capture, timeout
- File I/O: read/write (text/binary), file info, list dir, symlink resolution
- `createDir()`, `remove()`, `exists()`, `realPath()`
- Temp: `createTempDir()`, `createTempFile()`
- `cleanup()`: Release resources

**`NodeExecutionEnv`** (`env/nodejs.ts`):
- Finds bash/sh shell on system
- Spawns process, captures output, enforces timeout
- Kills process tree on timeout
- Returns exitCode

**File error codes** (`types.ts:77-84`):
Maps Node.js errno to stable codes: `not_found`, `permission_denied`, `not_directory`, `is_directory`, `invalid`, `not_supported`, `unknown`

---

## 10. System Prompt Construction

**Sources** (`agent-harness.ts:329-341`):
1. Static string
2. Dynamic callback with turn context:
   - ExecutionEnv (read files for instructions)
   - Session (access conversation history)
   - Model, thinking level, active tools, resources

**Skill formatting** (`system-prompt.ts`):
```xml
<available_skills>
  <skill>
    <name>skill-name</name>
    <description>When to use this skill</description>
    <location>/path/to/SKILL.md</location>
  </skill>
</available_skills>
```

Relative paths in skill instructions resolved against skill directory.

---

## 11. Proxy Stream Function

**Purpose** (`proxy.ts`): Route LLM calls through a server that manages auth and provider access

**Events** (`ProxyAssistantMessageEvent`):
- Server strips `partial` field from deltas to reduce bandwidth
- Client reconstructs partial message client-side
- Events: start, text_start/delta/end, thinking_start/delta/end, toolcall_start/delta/end, done, error

**Options** (`ProxyStreamOptions`):
- Inherits from SimpleStreamOptions (temperature, maxTokens, reasoning, cache, etc.)
- Adds: signal, authToken, proxyUrl

---

## 12. Queue Management

**Three queues** (`agent.ts:165-166`):
1. **Steering**: Injected mid-turn after tool execution
2. **Follow-up**: Processed only after agent would stop
3. **Next-turn** (AgentHarness only): Messages to run after harness becomes idle

**Queue modes** (`QueueMode = "all" | "one-at-a-time"`):
- `"all"`: Drain all queued messages at once
- `"one-at-a-time"`: Drain one message per cycle (allows interleaving)

---

## 13. Key Types

**`AgentMessage`** (`types.ts:301`):
```typescript
type AgentMessage = Message | CustomAgentMessages[keyof CustomAgentMessages];
```
Standard LLM messages + custom extensions (BashExecutionMessage, CustomMessage, BranchSummaryMessage, CompactionSummaryMessage)

**`AgentTool`** (`types.ts:353-376`):
- `label`: Display name
- `prepareArguments()`: Compatibility shim for raw tool arguments
- `execute()`: Execute tool call, can stream partial results via onUpdate
- `executionMode`: `"sequential" | "parallel"`
- Returns `AgentToolResult<T>` with content, details, optional terminate flag

**`AgentHarnessOptions`** (`harness/types.ts:617-650`):
- env, session, tools, resources
- systemPrompt (string or callback)
- getApiKeyAndHeaders (auth resolution)
- streamOptions, model, thinkingLevel
- activeToolNames, steeringMode, followUpMode

---

## Key Insights for Python Port

1. **Layered design**: Low-level loop → Agent wrapper → Harness orchestration
2. **Event-driven**: All changes flow through event system; extend via listeners and hooks
3. **Append-only persistence**: JSONL sessions are crash-safe, auditable
4. **Batched writes**: Session writes batched during turns, flushed at turn end for consistency
5. **Token accuracy**: Combines LLM usage data with heuristic estimation
6. **Middleware hooks**: Critical interception points (before_provider_request, context transform, tool_call, etc.)
7. **Tree navigation**: Sessions support branching; navigating branches triggers summarization
8. **Error encoding**: Errors become tool results or failure messages, not exceptions
9. **Flexible queueing**: Support for both immediate and buffered steering/follow-up
