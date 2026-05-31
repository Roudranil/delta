# Pi Agent: Subagents, TUI, Web UI, IPC, CLI

## 1. Process Model

```
Main Pi Process (CLI entry point)
├── AgentSession (core session class)
├── Agent (from @earendil-works/pi-ai)
├── Mode Layer (one of: interactive, print, rpc)
│   └── Interactive: TUI rendering → events → user input
│   └── Print: JSON/text output → exit
│   └── RPC: stdin/stdout JSON-RPC protocol
└── Subagent Tool
    └── spawn("pi", ["--mode", "json", "-p", ...])  ← separate process
```

All modes share the same `AgentSession` and `Agent` classes. Modes are just different I/O layers.

---

## 2. CLI Entry Points & Mode Selection

**Main Entry**: `packages/coding-agent/src/cli.ts` (shebang: `#!/usr/bin/env node`)

### Argument Parsing

`parseArgs(args: string[])` returns `Args` with:
- Model/provider: `--model`, `--provider`, `--api-key`
- Mode: `--mode text|json|rpc`
- Session: `--session <path|id>`, `--fork`, `--resume`, `--continue`
- Tools: `--tools <list>`, `--no-tools`, `--no-builtin-tools`
- Extensions: `--extension <path>`, `--no-extensions`
- And 20+ more options

### Mode Resolution

```typescript
resolveAppMode(parsed, stdinIsTTY):
  if --mode rpc        → RPC
  if --mode json|text  → Print
  if --print or !isTTY → Print
  else                 → Interactive (TUI)
```

### Session Management

- `--no-session`: Ephemeral (in-memory only)
- `--session <path|id>`: Open specific session
- `--fork <path|id>`: Fork into new session
- `--resume`: TUI picker for previous sessions
- `--continue`: Auto-continue most recent session
- Default: Create new session in current directory

---

## 3. Application Modes

### Mode 1: Interactive (TUI)

**File**: `src/modes/interactive/interactive-mode.ts`

Architecture:
1. Instantiate TUI with ProcessTerminal
2. Create TUI components (Editor, Markdown, Loader)
3. Subscribe to AgentSession events
4. Forward keyboard input to components
5. Components send to AgentSession methods (prompt, abort, etc.)
6. AgentSession emits events → components re-render → differential update

### Mode 2: Print (Headless)

**File**: `src/modes/print-mode.ts`

- `text`: Markdown-like human-readable output
- `json`: Streaming JSON events (one per line)
- Use cases: Scripting, piping, batch processing, CI/CD

### Mode 3: RPC (Headless with JSON-RPC)

**File**: `src/modes/rpc/rpc-mode.ts`

- Reads JSON commands from stdin
- Outputs JSON responses + events to stdout
- Supports async operations with id correlation
- Extension UI requests/responses for dialogs

Commands: `prompt`, `steer`, `follow_up`, `abort`, `set_model`, `cycle_model`, `bash`, `get_messages`, etc.

---

## 4. TUI Architecture

### Component Model

```typescript
export interface Component {
  render(width: number): string[];     // Return array of terminal lines
  handleInput?(data: string): void;    // Handle keyboard input
  wantsKeyRelease?: boolean;           // Opt into key release events
  invalidate(): void;                  // Clear cached state
}

export interface Focusable {
  focused: boolean;
}
```

### TUI Class (`packages/tui/src/tui.ts:239-1319`)

```typescript
export class TUI extends Container {
  terminal: Terminal;

  start(): void                          // Start terminal, request render
  stop(): void                           // Cleanup, show cursor
  setFocus(component: Component | null): void
  showOverlay(component, options?): OverlayHandle
  requestRender(force?: boolean): void
  addInputListener(listener): () => void
}
```

### Differential Rendering Algorithm

**Key Steps**:
1. Call `render(width)` on all components
2. Composite overlays on top of base content
3. Extract `CURSOR_MARKER` for IME positioning
4. Apply line resets (ANSI color codes)
5. Compare with previous lines (find first/last changed)
6. Only redraw changed line range
7. Use synchronized output (CSI 2026) to reduce flicker

**16ms debounce** (60fps cap) via `requestRender()` → `scheduleRender()` → `doRender()`

**`CURSOR_MARKER = "\x1b_pi:c\x07"`** (APC sequence, zero-width):
- Components emit this at cursor position when focused
- TUI finds marker, positions hardware cursor there
- Enables proper IME candidate window placement

### NOT React/Ink

This is a **custom differential rendering engine** built from scratch. No React, no Ink. The key properties:
- Efficient: only changed lines are written to terminal
- Component tree: flat hierarchy, each component returns `string[]`
- Focus management: single focused component receives input
- Overlays: stacked modals with positioning logic

### Overlay System

```typescript
export interface OverlayOptions {
  width?: SizeValue;           // Absolute or "50%"
  maxHeight?: SizeValue;
  anchor?: OverlayAnchor;      // "center", "top-left", etc.
  offsetX?: number; offsetY?: number;
  row?: SizeValue; col?: SizeValue;
  margin?: OverlayMargin | number;
  visible?: (width, height) => boolean;
  nonCapturing?: boolean;      // Don't steal focus
}
```

Focus Stack: Overlays in stack; top visible one gets keyboard input.

### Available Components

In `packages/tui/src/components/`:
- **Box**: Bordered container with padding
- **Editor**: Multi-line editor (Emacs-like keybindings, kill ring, undo)
- **Input**: Single-line input with autocomplete
- **Markdown**: Render markdown to terminal (colors, bold, etc.)
- **SelectList**: Scrollable list with selection
- **SettingsList**: Key-value display with toggles
- **Text**: Colored text output
- **Loader**: Animated spinner
- **CancellableLoader**: Loader with cancel button
- **Image**: Terminal image (Kitty, iTerm2 protocols)
- **Container**: Group of children
- **TruncatedText**: Auto-truncate to width

### Keybindings

```typescript
// packages/tui/src/keybindings.ts
export const TUI_KEYBINDINGS = {
  "tui.editor.cursorUp": { defaultKeys: "up" },
  "tui.editor.cursorLeft": { defaultKeys: ["left", "ctrl+b"] },
  // 20+ more
};
```

Supports **Kitty keyboard protocol** (modern) + legacy CSI sequences.

**Emacs keybindings built-in**: Ctrl+A/E, Ctrl+K/U, Ctrl+Y (yank), Alt+Y (yank-pop), Ctrl+- (undo).

**Kill ring implementation** for Emacs-style text operations.

### Terminal Abstraction

```typescript
export interface Terminal {
  columns: number;
  rows: number;
  start(onInput: (data) => void, onResize: () => void): void;
  stop(): void;
  write(data: string): void;
  showCursor(): void;
  hideCursor(): void;
}

export class ProcessTerminal implements Terminal {
  // Uses stdin/stdout with raw mode
  // Detects Kitty protocol for better compatibility
  // Handles SIGWINCH for resize events
}
```

---

## 5. Web UI Architecture

### Main Component: AgentInterface

**File**: `packages/web-ui/src/components/AgentInterface.ts`

```typescript
@customElement("agent-interface")
export class AgentInterface extends LitElement {
  @property({ attribute: false }) session?: Agent;
  @property({ type: Boolean }) enableAttachments = true;
  @property({ type: Boolean }) enableModelSelector = true;
  @property({ attribute: false }) onBeforeSend?: () => void | Promise<void>;
  @property({ attribute: false }) onBeforeToolCall?: (toolName, args) => boolean | Promise<boolean>;

  public setInput(text: string, attachments?: Attachment[]): void;
  public setAutoScroll(enabled: boolean): void;

  private setupSessionSubscription(): void;
}
```

**LitElement (Web Components)** — not React/Svelte.

Sub-components:
- **MessageEditor**: Input box with attachment support
- **MessageList**: Chat history display
- **StreamingMessageContainer**: Live message rendering
- **ModelSelector**: Dialog for model selection
- **SandboxedIframe**: Secure artifact execution (HTML, SVG, etc.)

### Artifact Rendering

- **MarkdownArtifact**: Markdown preview
- **HtmlArtifact**: HTML preview (in SandboxedIframe)
- **SvgArtifact**: SVG rendering
- **TextArtifact**: Code with syntax highlighting
- **ImageArtifact**: Image display
- **PdfArtifact**: PDF viewer
- **ExcelArtifact**: Spreadsheet viewer
- **DocxArtifact**: Word document viewer

### Storage

```typescript
class AppStorage {
  saveSessions(sessions): Promise<void>;
  getSessions(): Promise<SessionData[]>;
  setProviderKey(provider, key): Promise<void>;
  getProviderKey(provider): Promise<string | undefined>;
  saveSettings(settings): Promise<void>;
  getSettings(): Promise<SettingsData>;
  // Backend: IndexedDB in browser
}
```

---

## 6. IPC / Communication Protocols

### RPC Protocol (JSON-RPC via stdin/stdout)

**File**: `src/modes/rpc/rpc-types.ts`, `rpc-mode.ts`, `jsonl.ts`

#### Commands (stdin)

```json
{"id": "uuid", "type": "prompt", "message": "List files", "images": [...]}
{"id": "uuid", "type": "bash", "command": "ls -la"}
{"id": "uuid", "type": "get_state"}
{"id": "uuid", "type": "set_model", "provider": "anthropic", "modelId": "claude-3-sonnet"}
```

#### Events (stdout, streamed)

```json
{"type": "message_start", "message": {...}}
{"type": "text_delta", "delta": "hello", "contentIndex": 0}
{"type": "tool_call_start", "id": "call-1", "name": "bash", "contentIndex": 0}
{"type": "tool_call_arg_delta", "delta": "{\"command\":\"ls\"}"}
{"type": "tool_call_end", "contentIndex": 0}
{"type": "tool_result_end", "message": {...}}
{"type": "message_end", "message": {...}}
```

#### Extension UI Requests (stdout)

```json
{"type": "extension_ui_request", "id": "uuid", "method": "select", "title": "Choose model", "options": [...]}
{"type": "extension_ui_request", "id": "uuid", "method": "confirm", "title": "Confirm?", "message": "..."}
{"type": "extension_ui_request", "id": "uuid", "method": "input", "title": "Enter name"}
{"type": "extension_ui_request", "id": "uuid", "method": "notify", "message": "Done!", "notifyType": "info"}
{"type": "extension_ui_request", "id": "uuid", "method": "setWidget", "widgetKey": "status", "widgetLines": [...]}
```

### JSONL Framing (`src/modes/rpc/jsonl.ts`)

```typescript
export function serializeJsonLine(value: unknown): string {
  return `${JSON.stringify(value)}\n`;  // LF-only, no Unicode separators
}
```

**Why not readline**: `readline` splits on Unicode separators like U+2028 (valid in JSON strings). JSONL spec requires LF-only framing.

### Interactive Mode (In-Process)

```
User Input (keyboard)
    ↓
TUI.handleInput(data)
    ↓
Component.handleInput(data)
    ↓
AgentSession.prompt() / etc.
    ↓
AgentSession emits event
    ↓
Component subscribes: event → update state
    ↓
Component.render() → new lines
    ↓
TUI differential rendering → terminal output
```

---

## 7. Subagent System

### Overview

Subagents are separate `pi` processes for delegated tasks:
- Isolated context window
- Task-based execution
- Three modes: single, parallel, chain
- Can be killed via AbortSignal

**File**: `examples/extensions/subagent/index.ts`

### Three Execution Modes

**Single**: One agent, one task
```typescript
{ agent: "CodeReviewer", task: "Review code", cwd: "/path" }
```

**Parallel**: Multiple tasks, concurrent execution
```typescript
{
  tasks: [
    { agent: "UnitTester", task: "Run tests" },
    { agent: "LinterBot", task: "Lint code" },
  ]
}
// Max parallel: 8 tasks, concurrency limit: 4
```

**Chain**: Sequential tasks, passing output
```typescript
{
  chain: [
    { agent: "Analyzer", task: "Analyze code" },
    { agent: "TestGen", task: "Generate tests for {previous}" },
  ]
}
```

### Process Spawning

```typescript
// Spawn child pi process in print/json mode
const args = ["--mode", "json", "-p", "--no-session"];
if (agent.model) args.push("--model", agent.model);
if (agent.tools) args.push("--tools", agent.tools.join(","));

const proc = spawn(invocation.command, invocation.args, {
  cwd: cwd ?? defaultCwd,
  shell: false,
  stdio: ["ignore", "pipe", "pipe"],  // stdin ignored, stdout/stderr captured
});

// Parse JSONL output line-by-line
proc.stdout.on("data", (data) => {
  buffer += data.toString();
  for (const line of buffer.split("\n")) {
    const event = JSON.parse(line);
    if (event.type === "message_end") {
      currentResult.messages.push(event.message);
    }
  }
});

// Handle AbortSignal
signal.addEventListener("abort", () => {
  proc.kill("SIGTERM");
  setTimeout(() => proc.kill("SIGKILL"), 5000);
}, { once: true });
```

### Concurrency Control

```typescript
async function mapWithConcurrencyLimit<TIn, TOut>(
  items: TIn[],
  concurrency: number,
  fn: (item: TIn, index: number) => Promise<TOut>
): Promise<TOut[]> {
  const limit = Math.min(concurrency, items.length);
  // Worker pool pattern — up to `limit` workers running in parallel
}
```

---

## 8. Shell Execution (Bash Tool)

**File**: `src/core/bash-executor.ts`

```typescript
export interface BashResult {
  output: string;               // Combined stdout + stderr (sanitized, truncated)
  exitCode: number | undefined; // Exit code or undefined if killed
  cancelled: boolean;
  truncated: boolean;
  fullOutputPath?: string;      // Path to temp file if truncated
}
```

**Output processing**:
1. Strip ANSI codes (`stripAnsi()`)
2. Replace binary garbage (`sanitizeBinaryOutput()`)
3. Normalize newlines
4. Keeps rolling buffer of `DEFAULT_MAX_BYTES` (4MB)
5. If exceeds threshold, writes to temp file

---

## 9. AgentSession Class

**File**: `src/core/agent-session.ts`

```typescript
export class AgentSession {
  agent: Agent;
  model?: Model<any>;
  thinkingLevel: ThinkingLevel;
  messages: AgentMessage[];
  isStreaming: boolean;
  isCompacting: boolean;
  scopedModels: ScopedModel[];   // Models available via Ctrl+P

  prompt(message, images?, options?): Promise<void>;
  steer(message, images?): Promise<void>;
  followUp(message, images?): Promise<void>;
  abort(): Promise<void>;
  setModel(model): Promise<void>;
  cycleModel(): Promise<...>;
  setThinkingLevel(level): void;
  executeBash(command): Promise<BashResult>;
  compact(customInstructions?): Promise<CompactionResult>;
  navigateTree(targetId, options?): Promise<...>;
  subscribe(handler): () => void;
  exportToHtml(outputPath?): Promise<string>;
}
```

---

## 10. Event System (`src/core/event-bus.ts`)

```typescript
export function createEventBus(): EventBusController {
  const emitter = new EventEmitter();
  return {
    emit: (channel, data) => { emitter.emit(channel, data); },
    on: (channel, handler) => {
      const safeHandler = async (data) => {
        try { await handler(data); }
        catch (err) { console.error(...); }
      };
      emitter.on(channel, safeHandler);
      return () => emitter.off(channel, safeHandler);  // Unsubscribe
    },
  };
}
```

---

## Python Port Guidance

1. **Component Abstraction**: `render(width: int) -> list[str]`, `handle_input(data: str) -> None`
2. **Differential Rendering**: Track previous state, compute line diff, only send changed ranges
3. **Event System**: Observer pattern with Node.js EventEmitter equivalent (Python: asyncio + callbacks)
4. **Mode Abstraction**: Three separate classes sharing AgentSession
5. **Subagent Spawning**: `subprocess.Popen()` with pipe stdout, parse JSONL line-by-line
6. **JSONL Protocol**: Framing on `\n` only (not `\r\n`), one JSON object per line
7. **Terminal Handling**: Raw mode, `signal.signal(SIGWINCH, ...)` for resize
8. **Keybindings**: Configurable mapping of key sequences to semantic actions
