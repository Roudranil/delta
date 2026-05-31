# Pi Agent: UI/UX Architecture & Critical Mechanics

## 1. TUI Architecture

**NOT React/Ink** — custom differential rendering engine built from scratch.

### The Rendering Loop

```
requestRender() → scheduleRender() → doRender()
```

- **16ms debounce** (60fps cap)
- On each render: calls `render(width)` on all components, composites overlays, finds first/last changed line, only writes changed range to terminal
- **Synchronized output** (CSI 2026) to reduce flicker

### Cursor Positioning

**`CURSOR_MARKER = "\x1b_pi:c\x07"`** (APC sequence, zero-width):
- Components emit this marker at their cursor position when focused
- TUI finds the marker, moves hardware cursor there
- Enables proper IME candidate window placement

### Component Interface

```typescript
export interface Component {
  render(width: number): string[];    // Return terminal lines
  handleInput?(data: string): void;  // Handle keyboard input
  wantsKeyRelease?: boolean;
  invalidate(): void;                 // Clear cached state
}
```

### Terminal Abstraction

```typescript
export interface Terminal {
  columns: number;
  rows: number;
  start(onInput: (data) => void, onResize: () => void): void;
  stop(): void;
  write(data: string): void;
}

export class ProcessTerminal implements Terminal {
  // stdin/stdout raw mode
  // Kitty protocol detection
  // SIGWINCH for resize events
}
```

---

## 2. Interactive Mode Real-Time Streaming

Messages render in real time:
1. User sends prompt
2. Empty assistant message shown immediately
3. Content chunks arrive, message updates in-place
4. Tool calls appear inline with results as they stream
5. Footer shows cumulative tokens (↑input ↓output Rcache Wcache) and cost ($0.001)
6. Turn ends, input prompt returns

---

## 3. Permission / Approval UX

Dangerous operations controlled via **extension event hooks** + UI dialogs:

```typescript
pi.on("tool_call", async (event, ctx) => {
  if (isDangerous) {
    const choice = await ctx.ui.select("Allow?", ["Yes", "No"]);
    if (choice !== "Yes") {
      return { block: true, reason: "Blocked by user" };
    }
  }
});
```

Also supports `session_before_switch`, `session_before_fork` events that return `{ cancel: true }`.

---

## 4. Interrupt / Cancellation UX

User presses Escape:
1. `CancellableLoader.onAbort()` called
2. `abortController.abort()`
3. `AbortSignal` propagates to LLM stream AND tool executions
4. Message marked `stopReason: "aborted"`
5. UI shows "Request aborted"
6. Input prompt returns

---

## 5. Token & Cost Tracking

- Accumulated across entire session: `for each message: totalCost += message.usage.cost.total`
- Footer displays with colorization: > 90% context used (red), > 70% (yellow)
- `scripts/cost.ts` and `scripts/stats.ts` analyze session files in `~/.pi/agent/sessions/<encoded-cwd>/`

---

## 6. Keyboard Handling

**Kitty keyboard protocol** (modern) + legacy CSI sequences.

**Emacs keybindings built-in**:
- `Ctrl+A`/`E` — Start/end of line
- `Ctrl+K`/`U` — Kill to end/start
- `Ctrl+Y` — Yank (paste from kill ring)
- `Alt+Y` — Yank-pop (cycle kill ring)
- `Ctrl+-` — Undo

**Kill ring** implementation for Emacs-style text operations.

**Grapheme-aware** using `Intl.Segmenter` for Unicode support.

---

## 7. Extension System as UI Plugins

Extensions can:
- Subscribe to agent lifecycle events (`agent_start`, `agent_end`, `tool_call`, etc.)
- Show UI dialogs: `ctx.ui.select()`, `ctx.ui.confirm()`, `ctx.ui.input()`
- Set custom widgets: `ctx.ui.setWidget()`, `ctx.ui.setFooter()`, `ctx.ui.setHeader()`
- Intercept terminal input: `ctx.ui.onTerminalInput(handler)`
- Provide custom editors/autocomplete providers

---

## 8. Small but Critical Mechanics

### Mid-stream interruption
When user interrupts (Escape), the abort signal fires immediately. The LLM stream and any in-flight tool executions all receive the signal and terminate. The partial `AssistantMessage` is saved with `stopReason: "aborted"` — the session is consistent.

### Tool call result display
The TUI renders tool calls inline as they stream. Tool name + arguments appear as the tool call starts. Results appear below once the tool finishes. Both visible simultaneously in the conversation.

### "Thinking" indicator
A spinner/loader component is shown while the LLM is streaming but hasn't yet produced visible text output. Once text starts, the loader transitions to the message component. The `message_start` → `message_update` events drive this.

### Error display
LLM errors and tool errors both appear inline in the conversation (they're encoded in messages, not thrown). The user sees the error text and can continue the conversation.

### Session resume display
On load, the full session history is rendered. The TUI scrolls to the bottom. Previous messages are rendered in their final state (not re-streamed).

---

## 9. Overlay System

```typescript
export interface OverlayOptions {
  width?: SizeValue;           // Absolute or "50%"
  maxHeight?: SizeValue;
  anchor?: OverlayAnchor;      // "center", "top-left", "top-right", etc.
  offsetX?: number; offsetY?: number;
  visible?: (width, height) => boolean;
  nonCapturing?: boolean;      // Don't steal focus
}

const handle = TUI.showOverlay(component, options);
// Returns OverlayHandle with .remove() to dismiss
```

**Focus Stack**: Overlays stack; top visible one gets keyboard input.

---

## 10. Web UI (AgentInterface LitElement)

**LitElement (Web Components)** — not React/Svelte.

**Communication Patterns**:

Direct JS import (same process):
```typescript
import { Agent } from "@earendil-works/pi-agent-core";
const agent = new Agent({...});
const stream = agent.stream(messages, options);
stream.on("text_delta", delta => {...});
```

HTTP to RPC mode (separate process):
```
POST /api/prompt
→ 200 OK

EventSource /api/events
← data: {"type":"text_delta", "delta":"..."}
← data: {"type":"message_end", ...}
```

---

## 11. `.pi/extensions/` Example Extensions

**`redraws.ts`**: Tracks and displays render count per second in a footer widget. Shows how to subscribe to `agent_start`/`agent_end` events and use `ctx.ui.setWidget()`.

**`tps.ts`**: Displays tokens per second in real-time during LLM streaming. Hooks into `message_update` events to compute token velocity.

**`prompt-url-widget.ts`**: Shows a URL in the footer pointing to the current session. Demonstrates `ctx.ui.setFooter()`.

---

## 12. Scripts

**`scripts/cost.ts`**: Reads all JSONL session files and computes total cost + token usage. Used for billing analysis.

**`scripts/stats.ts`**: Aggregates session statistics (turn count, tool usage frequency, model usage, average context size).

**`scripts/session-context-stats.mjs`**: Shows context window utilization per session — useful for tuning compaction thresholds.

---

## Python Port Guidance

For building the TUI in Python:

1. **`Component` base class**: `render(width: int) -> list[str]`, `handle_input(data: str) -> None`, `invalidate() -> None`
2. **`TUI` class**: manages diff engine, overlay stack, render loop, 16ms debounce
3. **Terminal abstraction**: raw mode, `signal.signal(SIGWINCH, ...)` for resize, Kitty protocol opt-in
4. **Keybinding system**: configurable mapping of key sequences to semantic actions
5. **Component library**: `Input`, `Editor`, `Markdown`, `SelectList`, `Loader`, `CancellableLoader`
6. **Kill ring**: store cut text in ring buffer; yank from it
7. **Overlay stack**: list of `(component, options)` pairs; top captures input

For the event bus connecting UI to agent:
- Python asyncio `asyncio.Queue` for events
- Callback-based subscription (`subscribe(handler) → unsubscribe`)
- Handler errors logged but don't crash the subscriber
