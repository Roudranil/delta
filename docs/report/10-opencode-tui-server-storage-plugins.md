# OpenCode: TUI, Server, Storage, Plugins, IDE Integration

## 1. TUI Architecture

**Framework**: `@opentui` — Solid.js-based terminal UI (NOT Ink, NOT custom differential renderer like pi)

**Rendering Engine**: `createCliRenderer` from `@opentui/core` with 60fps target

**Real-time Updates**: SSE (Server-Sent Events) with 16ms event batching for 60fps rendering

**Root Component** (`cli/cmd/tui/app.tsx`, ~1000 lines):
- 15+ nested context providers: RouteProvider, SDKProvider, SyncProvider, ThemeProvider, DialogProvider, etc.
- Component count: 150+ across 15 categories

**Themes**: 40+ built-in (dracula, github, tokyonight, catppuccin, gruvbox, solarized, etc.)

**Keyboard**: Registry-based keybinding system with 20+ application commands

**Mouse Support**: Enabled by default, right-click context menu, experimental copy-on-select

### TUI Layout Structure

```
App (app.tsx)
├── RouteProvider
│   ├── SyncProvider (event subscription)
│   ├── SDKProvider (API client)
│   ├── ThemeProvider
│   └── Routes
│       ├── /home → HomeScreen
│       └── /session/:id → SessionScreen
│           ├── Sidebar
│           ├── MessageList
│           └── Input + StatusBar
```

### Session View (`cli/cmd/tui/routes/session/`)

The main conversation UI:
- MessageList: scrollable list of messages with virtual rendering
- Each message: TextPart (markdown), ToolPart (call + result + state indicator), ReasoningPart
- StatusBar: model name, token usage, cost, session info
- InputArea: multiline editor, attachment support, command autocomplete

### Feature Plugins (`cli/cmd/tui/feature-plugins/`)

TUI extensions loaded at startup:
- **home**: Default home screen content
- **sidebar**: Right panel content
- **system**: System-level TUI hooks

---

## 2. HTTP Server Architecture

**Framework**: Effect.ts (`effect/unstable/http` and `effect/unstable/httpapi`)

**File**: `packages/opencode/src/server/server.ts`

### Server Model

Two modes:
1. **Single daemon**: Long-running server process, TUI/web/IDE all connect via HTTP
2. **Per-request instances**: Routed via `x-opencode-directory` header — each instance is a separate isolated context

### Port Resolution

Tries port 4096 first, then finds any free port. Publishes port via mDNS for local discovery.

### API Groups (20+)

All groups in `server/routes/instance/httpapi/groups/v2/`:

```
Session    - CRUD for sessions, fork, archive, share
Message    - Messages, parts, streaming
Event      - SSE stream of all events
Question   - User question/confirmation requests
File       - File operations
PTY        - Pseudo-terminal WebSocket
Provider   - LLM provider management
MCP        - MCP server management
Permission - Permission management
Account    - User account
Sync       - Event sync
Workspace  - Workspace management
Share      - Session sharing
...
```

### Real-time Communication

- **SSE** for event streams: `GET /event` — streams all events to connected clients
- **WebSocket** for PTY connections: bidirectional interactive terminal
- No polling — pure push model

### Middleware Pipeline

```
Authorization → CORS → Compression → Schema validation → Instance context routing → Handler
```

### WebSocket Management

`WebSocketTracker` service manages WebSocket lifecycle — tracks connections, handles cleanup on instance dispose.

---

## 3. Storage Layer

**Engine**: SQLite (Bun-native or Node.js via `better-sqlite3`)
**ORM**: Drizzle ORM (lightweight, type-safe)
**Location**: `~/.opencode/opencode.db` (channel-specific variants: `opencode-beta.db`, `opencode-dev.db`)

### SQLite Pragmas

```sql
PRAGMA journal_mode = WAL;          -- Write-Ahead Logging (concurrent reads)
PRAGMA synchronous = NORMAL;
PRAGMA cache_size = -64000;         -- 64MB cache
PRAGMA busy_timeout = 5000;         -- 5s busy timeout
```

### Schema Tables

| Table | Purpose |
|-------|---------|
| `SessionTable` | Session metadata (id, title, model, agent, cost, tokens) |
| `MessageTable` | Message headers (id, session, role, model, timestamp) |
| `PartTable` | Message parts with JSON data (text, tool calls, reasoning) |
| `TodoTable` | TODO items |
| `PermissionTable` | Stored permission approvals per project |
| `SessionShareTable` | Session share records (id, url, secret, expiration) |
| `ProjectTable` | Project metadata |
| `WorkspaceTable` | Workspace records |
| `AccountTable` | User account data |
| `EventTable` | Event sourcing stream |

### Database Migrations

**Location**: `packages/opencode/migration/` (15+ timestamped folders)

Each migration:
```
20260323234822_events/
├── migration.sql    # DDL/DML
└── snapshot.json    # Full schema state for drift detection
```

Pattern: `LocalContext`-based transaction management with deferred effects.

### Event Sourcing

`EventTable` stores all domain events with sequence tracking for replication:
- `EventSequenceTable`: Per-instance sequence counters
- Events replicated to cloud backend via Sync service

---

## 4. Plugin System

**File**: `packages/opencode/src/plugin/index.ts`

### Built-in Plugins (8)

- Codex (OpenAI Codex integration)
- GitHub Copilot
- GitLab
- Poe
- Cloudflare
- Azure
- DigitalOcean
- venice

### Plugin Types

1. **Server plugins** (TypeScript) — business logic, new providers/tools
2. **TUI plugins** (Solid.js) — UI components, routes, dialogs
3. **Workspace adapters** — cloud integration

### Plugin Inputs

```typescript
interface PluginInput {
  sdk: SDKClient,
  project: ProjectMetadata,
  worktree: WorktreeInfo,
  serverUrl: string,
  shell: BunShell,
}
```

### Plugin Hooks

```typescript
config()                              // Modify config before use
event(event)                          // React to any bus event
question(request)                     // Handle user question
message.before(message)               // Pre-process message
message.after(message)                // Post-process message
trigger(input)                        // Custom trigger conditions
```

### External Plugin Loading

External plugins loaded from npm, installed to `~/.opencode/plugins/`:

```bash
opencode plug install @my-org/my-plugin
opencode plug list
opencode plug remove @my-org/my-plugin
```

### TUI Plugin API

Rich interface for TUI plugins:

```typescript
interface TUIPluginAPI {
  router: SolidRouter,           // Register routes
  dialogs: DialogManager,        // Show dialogs
  state: StateManager,           // Access/mutate state
  commands: CommandRegistry,     // Register commands
  keybindings: KeybindManager,   // Register key combos
  events: EventBus,              // Subscribe to events
  components: ComponentRegistry, // Register components
}
```

### Error Handling

Failed plugins don't crash — errors are logged and reported via bus events. System continues with remaining plugins.

---

## 5. IDE Integration

**Supported IDEs**: Windsurf, VSCode, VSCode-Insiders, Cursor, VSCodium

**Detection**: Via `TERM_PROGRAM` env var and `GIT_ASKPASS` parsing

**Installation**:
```bash
code --install-extension sst-dev.opencode
```

**Communication**: Environment variables (`OPENCODE_CALLER`, `TERM_PROGRAM`, `GIT_ASKPASS`)

**Extension**: VSCode extension `sst-dev.opencode` handles bidirectional sync:
- Shows opencode TUI embedded in IDE
- Syncs selection/file context to opencode
- Receives file edits from opencode

---

## 6. Desktop App (Tauri-based)

**Tech stack**: Tauri (Rust main process) + Node.js sidecar (OpenCode server) + React renderer

**Process Model**:
```
Tauri (Rust)
├── Main Window (React renderer)
└── Sidecar (Node.js via utilityProcess.fork())
    └── OpenCode HTTP server
```

**Sidecar IPC**:
- From sidecar: `{ type: "ready" }`, progress updates, errors
- Main window talks to sidecar via HTTP (same as TUI/web)

**Features**:
- Native menus, titlebar theming
- File picker, clipboard access
- Built-in Tauri auto-updater
- Logging to `~/.opencode/logs/`

**Database**: `app.getPath("appData")/opencode/opencode.db`

---

## 7. Web UI (Solid.js)

**Framework**: Solid Router for client-side routing (same as TUI, not Next.js/React Router)
**State**: TanStack Query + context providers
**Routes**: `/home`, `/session/:sessionID`, admin routes
**Components**: 50+ Solid components (dialogs, input, layout, session view)
**Connection**: HTTP client to same server as TUI/desktop
**Error tracking**: Sentry integration for production

### Component Architecture

```
Web App
├── GlobalSDKProvider (HTTP client)
├── GlobalSyncProvider (SSE subscription)
├── ModelsProvider (available models)
└── Router
    ├── /home → Session picker
    └── /session/:id
        ├── SessionView
        │   ├── MessageList
        │   └── MessageInput
        └── Sidebar
```

---

## 8. Session Sharing

**Flow**:
1. HTTP POST to remote console server
2. Returns share URL: `https://console.opencode.ai/share/abc123`
3. Stored in `SessionShareTable` (id, url, secret, expiration)
4. Queue-based sync: changes accumulated and flushed with retry logic

**Data synced**: Session metadata, messages, parts, file diffs, models

**Secret**: Prevents unauthorized access to shared sessions

---

## 9. Sync System

**Event Sourcing**: All domain events stored in `EventTable`

**Replication**:
- Upload events via `POST /sync`
- Download via `GET /sync`

**Ownership**: Multi-device tracking with `ownerID` to prevent conflicts

**Sequence Validation**: No gaps, no duplicates, version-based schemas

**Projectors**: Database projectors convert events to domain tables

---

## 10. CLI Commands (20+)

```
serve      - Start headless server
session    - List/delete sessions
account    - Auth management
provider   - Connect/manage providers
models     - List/search models
mcp        - MCP server management
plug       - Plugin management
web        - Start web UI
generate   - One-shot code generation
run        - Execute command with context
export     - Export session
import     - Import session
upgrade    - Auto-update
db         - Database operations
agent      - Agent selection
pr         - GitHub PR integration
github     - GitHub auth
stats      - Usage statistics
```

---

## 11. Effect System (Effect.ts)

**Pervasive usage** across all services:

```typescript
// Service pattern (used everywhere):
export class Service extends Context.Service<Service, Interface>()("@opencode/ServiceName") {}

export const layer = Layer.effect(
  Service,
  Effect.gen(function* () {
    const dependency = yield* DependencyService

    const method = Effect.fn("ServiceName.method")(function* (...args) {
      // Named span for tracing
    })

    return Service.of({ method })
  }),
)

export const defaultLayer = layer.pipe(Layer.provide(Dependency.defaultLayer))
```

**Benefits**:
- Automatic distributed tracing (736+ named Effect.fn spans)
- Structured concurrency with fiber management
- Guaranteed resource cleanup via Scope
- Typed errors on error channel
- Dependency injection via Context/Layer
