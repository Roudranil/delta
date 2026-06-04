# SDS-14: Frontend Stack & Concepts

## How the frontend talks to the backend

FastAPI returns JSON or SSE. The frontend calls it over HTTP. No special glue — the bridge is just `fetch()`.

**Regular requests** (JSON in, JSON out):
```js
const res = await fetch("/api/v1/sessions", {
  method: "POST",
  headers: { "Authorization": `Bearer ${token}`, "Content-Type": "application/json" },
  body: JSON.stringify({ mode: "explore" })
})
const data = await res.json()
```

**SSE streaming** (EXPLORE chat — token by token):
```js
const source = new EventSource("/api/v1/sessions/123/chat")
source.onmessage = (event) => {
  const { type, content } = JSON.parse(event.data)
  if (type === "token") appendToChat(content)
  if (type === "done") source.close()
}
```

The backend doesn't know or care what framework the frontend uses. FastAPI just returns JSON or a stream.

---

## Ecosystem glossary

**JavaScript vs TypeScript**
JavaScript (JS) is what browsers run natively. TypeScript (TS) is JS with types — like Python with type hints, except enforced at build time. TypeScript compiles to JavaScript before the browser sees it. Use TypeScript — editor autocomplete is dramatically better and it catches bugs before runtime.

**Node.js and npm**
Node.js is a runtime that lets you run JS on your machine (like CPython for Python). You need it locally to build and develop the frontend. `npm` is its package manager, like `uv`/`pip`.

**Vite**
Build tool. Watches files, reloads the browser on save during dev, bundles everything into static files for production. You don't interact with it directly — it runs in the background. Fast, which is the only reason it was chosen over older alternatives.

**React**
Component library from Meta. The mental model: your entire UI is a tree of components. Each component is a function that takes data (called "props") and returns HTML. When data changes, React re-renders only what changed.

```jsx
function ChatMessage({ role, content }) {
  return <div className={role === "user" ? "user-msg" : "ai-msg"}>{content}</div>
}
```

The `<div>` inside a JS function is JSX — it looks like HTML but compiles to JS.

**State**
Data that can change and that the UI should react to. `useState` is the hook for this:

```jsx
const [messages, setMessages] = useState([])  // like a variable, but reactive
setMessages([...messages, newMessage])         // triggers a re-render automatically
```

**Hooks**
React's way of adding behaviour to components. All start with `use`. The ones you'll use regularly: `useState` (reactive variables), `useEffect` (side effects like fetching data on load), `useRef` (direct DOM access). You'll pick these up by reading examples.

---

## Why not something lighter than React

Astro is great for content sites (blog, portfolio) where most pages are static with a little interactivity sprinkled in. Delta is the opposite — nearly everything is dynamic: streaming responses, live run status polling, session history. Astro would fight you constantly.

Vue, Svelte, SolidJS are all fine alternatives but:
- Smaller ecosystems — fewer component libraries, fewer tutorials when stuck
- The core mental model (components + state) is identical — you're not avoiding learning, just changing dialect
- shadcn/ui is React-only

React learning curve to productive: about a week.

---

## shadcn/ui

Use it. shadcn is not a typical component library — instead of installing a package and importing `<Button>`, it copies the component source into your project (`components/ui/button.tsx`). You own the code, can read it, modify it.

Built on:
- **Radix UI** — unstyled, accessible primitives. Handles keyboard navigation, ARIA attributes, focus trapping — the hard accessibility work you don't want to write.
- **Tailwind CSS** — utility-first CSS. Instead of writing `.button { padding: 8px; }`, you write `className="px-2"`. Verbose but fast once you know the class names.

Components you'll use from shadcn: Button, Input, Dialog, Card, Badge, ScrollArea, Tabs, Separator.

Components you write yourself: the chat message stream, SSE token rendering, the DEEP run progress view, MDX artifact display.

---

## Library stack

| Library | What it does | Use in Delta |
|---------|-------------|-------------|
| **React + TypeScript** | Component model, reactivity | Everything |
| **Vite** | Build tool, dev server | Everything |
| **React Router** | Client-side routing — different URLs render different components | `/sessions`, `/sessions/:id`, `/library` |
| **TanStack Query** | Data fetching, caching, polling. Handles loading/error/stale states. | DEEP run polling every 5s — this is exactly what it was made for |
| **Zustand** | Minimal global state — like a Python dict that components subscribe to | Active session, message list |
| **shadcn/ui** | UI components (owned, modifiable) | Buttons, inputs, dialogs, cards |
| **Tailwind CSS** | Styling — ships with shadcn | All styling |
| **MDX** | Markdown + JSX components — renders `<PaperCard>` inside a markdown string | DEEP artifact display |
| **@clerk/react** | Clerk's React SDK — `<SignIn>`, `useAuth()` hook that gives you the JWT | Auth |

---

## Folder structure (`web/src/`)

```
pages/        # One file per route: Chat.tsx, Library.tsx, Login.tsx
              # Owns layout and data orchestration for that route
              # No direct API calls — uses hooks/

components/   # Reusable UI components — stateless where possible
              # ChatMessage, PaperCard, RunProgress, ArtifactViewer

hooks/        # All data fetching and stateful logic
              # useSession, useStream (SSE), useRunStatus (TanStack Query polling)
              # Pages call hooks, never api/ directly

api/          # Typed fetch wrappers — one function per endpoint
              # Handles auth headers (Clerk JWT), base URL, error parsing
              # Called only by hooks/

store/        # Zustand — active session, message list, UI state
              # Kept minimal — server is source of truth

types/        # TypeScript types that mirror backend Pydantic models exactly
              # Updated whenever backend models change
```

---

## Bootstrapping

```bash
npm create vite@latest web -- --template react-ts
cd web
npx shadcn@latest init
npm install react-router-dom @tanstack/react-query zustand @clerk/react
```

That gives you React + TypeScript + Vite + Tailwind + shadcn configured and ready. Then add the rest incrementally.

---

## Local dev

```bash
cd web && npm run dev    # starts at http://localhost:5173
```

Vite proxies API calls to `http://localhost:8000` during dev — configure this in `vite.config.ts` so you don't have to hardcode the API URL or deal with CORS in dev:

```ts
// vite.config.ts
export default defineConfig({
  server: {
    proxy: {
      '/api': 'http://localhost:8000'
    }
  }
})
```

In production, `VITE_API_URL` points at the Render URL instead.
