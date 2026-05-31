# Report 13: GSD Redux — Agent Catalog, Context Management & Skills

Source: `reference-repos/get-shit-done-redux`

---

## Executive Summary

GSD ships **33 specialized agents** organized by function. Each agent is a markdown definition file (`agents/*.md`) with a narrowly scoped role, specific tool access, and a structured output contract. The system treats context as a first-class resource: rather than letting one agent handle everything, it decomposes work into focused roles so each agent operates on a minimal, well-defined slice of the problem.

---

## 1. Complete Agent Roster (33 Agents)

### Researchers (4 agents)

**gsd-project-researcher**
- Researches domain ecosystem before roadmap creation
- Spawned 4× in parallel: stack, features, architecture, pitfalls
- Output: domain-specific RESEARCH fragments merged by gsd-research-synthesizer

**gsd-phase-researcher**
- Researches implementation patterns for a specific phase
- Reads: CONTEXT.md, REQUIREMENTS.md, prior RESEARCH.md
- Output: `{padded_phase}-RESEARCH.md` (domain exploration, patterns, risks, library recs)

**gsd-ui-researcher**
- Produces UI design contracts for frontend phases
- Detects whether a design system exists (shadcn, Tailwind) → offers initialization
- Output: UI design contract doc with component specs, layout decisions, accessibility notes

**gsd-advisor-researcher**
- Researches a single gray-area decision during advisor mode
- Returns 5-column comparison table: option, approach, tradeoffs, complexity, recommendation
- Used by discuss-phase `--advisor` mode for research-backed question answering

---

### Analyzers & Synthesizers (3 agents)

**gsd-assumptions-analyzer**
- Deeply analyzes codebase for phase assumptions with evidence + confidence levels
- Tool access: Read, Grep, Glob (read-only)
- Output: structured list of assumptions with evidence strength (high/medium/low)

**gsd-research-synthesizer**
- Combines outputs from 4 parallel project-researcher instances into unified summary
- Input: 4 RESEARCH fragments
- Output: consolidated RESEARCH.md with de-duplicated findings

**gsd-domain-researcher**
- Surfaces business domain and evaluation context for AI systems
- Fills Section 1b of AI-SPEC.md (domain, user personas, success criteria)
- Used only in `/gsd:ai-integration-phase` workflow

---

### Planners (3 agents)

**gsd-planner**
- Transforms CONTEXT.md + RESEARCH.md into executable PLAN.md files
- Input: phase number, CONTEXT.md, RESEARCH.md (optional), config
- Output: one or more `{padded_phase}-{plan_id}-PLAN.md` files with:
  ```yaml
  # Frontmatter
  wave: 1
  gap_closure: false
  autonomous: true
  files_modified: ["src/api/routes.py", "tests/test_api.py"]
  cross_ai: false
  ```
  - `<objective>`: what this plan builds
  - `<tasks>`: numbered tasks with `tdd: true` flag if applicable
  - `<estimates>`: tokens, duration

**gsd-plan-checker**
- Verifies plan quality before execution
- Mandatory gates:
  - Does each task have a clear objective?
  - Are estimates realistic?
  - Hidden dependencies?
  - Will this achieve the phase goal?
- If fails → auto-generates fix suggestions → planner re-plans (max 3 iterations)

**gsd-roadmap-planner** (inferred)
- Generates phased ROADMAP.md from requirements/user answers
- Used in `/gsd:new-project` flow

---

### Executors (2 agents)

**gsd-executor** ⭐ (most critical agent)
- Performs the actual implementation work
- Fresh 200K context window per invocation
- Optionally isolated in git worktree
- Receives:
  - Full PLAN.md
  - Phase CONTEXT.md
  - `@gates.md` (mandatory gates file)
  - `@execute-plan.md` (execution workflow)
- Contract:
  - Atomic git commits (one per task)
  - Verify before committing (tests pass, no lint errors)
  - Write SUMMARY.md on completion
  - Update STATE.md marking plan complete
- Model selection: can inherit from orchestrator or be configured explicitly (e.g., `claude-opus-4-7` for heavy tasks)

**gsd-code-fixer**
- Targeted bug fix agent
- Activated by `/gsd:debug` workflow
- Input: debug session report, error description, relevant files
- Output: fix commits + updated SUMMARY.md with root cause analysis

---

### Code Quality Agents (3 agents)

**gsd-code-reviewer**
- Reviews implementation against phase CONTEXT.md + REQUIREMENTS.md
- Generates structured review with: issues (critical/major/minor), suggestions, verdict
- Used in verify-work and optional inline during execute

**gsd-integration-checker**
- Validates that new implementation doesn't break existing integrations
- Runs in post-execute before verify-work
- Checks: API contracts, data models, shared interfaces

**gsd-nyquist-auditor**
- Named after Nyquist sampling theorem — checks coverage sufficiency
- Audits whether tests cover the important cases (not just happy paths)
- Output: coverage gap report with specific missing test scenarios

---

### Verification & Evaluation Agents (3 agents)

**gsd-eval-planner**
- Plans evaluation strategy for AI system phases (AI-SPEC.md phases)
- Determines: eval dataset design, metrics, baseline, acceptance criteria
- Used only in AI-integration workflow

**gsd-eval-auditor**
- Audits eval results against acceptance criteria
- Input: eval run outputs, AI-SPEC.md, acceptance criteria
- Output: pass/fail verdict with gap analysis

**gsd-doc-verifier**
- Verifies documentation is accurate and complete relative to implementation
- Reads: actual code + docs → finds stale/missing/incorrect documentation

---

### Documentation Agents (4 agents)

**gsd-doc-writer**
- Generates user-facing documentation from implementation + CONTEXT.md
- Follows project documentation style (auto-detected from existing docs)

**gsd-doc-classifier**
- Classifies documents by type (ADR, spec, runbook, guide, API reference)
- Used by `/gsd:map-codebase` to build knowledge index

**gsd-doc-synthesizer**
- Synthesizes scattered documentation into coherent reference
- Used for knowledge distillation during new-project phase

**gsd-doc-writer** (variant — code docs)
- Specialized for code-level docs: docstrings, README updates, inline comments

---

### Debugging Agents (2 agents)

**gsd-debugger**
- Analyzes failures during execution or verify-work
- Input: error output, relevant code, test failures
- Uses: structured fault isolation (hypothesis → evidence → conclusion)
- Output: root cause report + reproduction steps

**gsd-debug-session-manager**
- Manages a multi-turn debug session
- Tracks: hypotheses tested, evidence gathered, decisions made
- Persists debug session state so it can resume if interrupted
- Output: debug session log + recommended fix plan

---

### Mapping & Discovery Agents (3 agents)

**gsd-codebase-mapper**
- Builds comprehensive map of existing codebase
- Produces: directory tree with annotations, key files, patterns detected, tech stack
- Used in `/gsd:map-codebase` and early discuss-phase scouting

**gsd-pattern-mapper**
- Maps reusable code patterns across codebase
- Output: pattern catalog with examples (sorted by reuse frequency)

**gsd-framework-selector**
- Recommends framework/library choices for given tech requirements
- Used in new-project flow
- Considers: existing stack, team skills, community support, maintenance burden

---

### Intelligence & Research Agents (3 agents)

**gsd-ai-researcher**
- Researches AI/ML implementation approaches
- Specialized knowledge: RAG patterns, embedding strategies, agent architectures, eval frameworks
- Used in AI-integration phases

**gsd-phase-researcher** (also in researchers section)
- Phase-specific research

**gsd-intel-updater**
- Updates existing RESEARCH.md with fresh findings (re-research)
- Used when implementation reveals assumptions were wrong → mid-phase research correction

---

### Other Agents (7 agents)

**gsd-assumptions-analyzer** (also in analyzers)

**gsd-domain-researcher** (also in analyzers)

**gsd-advisor-researcher** (also in researchers)

Plus additional agents visible in directory listing:
- **gsd-milestone-completer** — Archive milestone, create release tag, update changelog
- **gsd-requirements-writer** — Transform user descriptions into structured REQUIREMENTS.md
- **gsd-project-setup** — Bootstrap project structure, install dependencies, configure tooling
- **gsd-verifier** — Run full verification suite (acceptance tests + manual QA checks)

---

## 2. Context Injection Architecture

### Phase-Specific File Manifest

Each agent receives only the files it needs (principle of least context):

```
Execute phase agent:    STATE.md*, config.json
Research agent:         STATE.md*, ROADMAP.md*, CONTEXT.md*, REQUIREMENTS.md
Plan agent:             STATE.md*, ROADMAP.md*, CONTEXT.md*, RESEARCH.md, REQUIREMENTS.md
Verify agent:           STATE.md*, ROADMAP.md*, REQUIREMENTS.md, PLAN.md, SUMMARY.md
Repair agent:           STATE.md*, config.json, PLAN.md
Discuss agent:          STATE.md*, ROADMAP.md, CONTEXT.md

(* = required, triggers warning if missing)
```

### Canonical Refs Flow

Every CONTEXT.md `<canonical_refs>` section is the primary downstream injection mechanism:

```markdown
<canonical_refs>
Downstream agents MUST read these before planning or implementing.

- `docs/adr/0001-dispatch-policy.md` — Query dispatch outcomes
- `docs/references/ui-brand.md` — Design system tokens
- `REQUIREMENTS.md ## User Flows` — Expected interactions
</canonical_refs>
```

The orchestrator injects this list verbatim into subagent prompts. Agents must read every file listed before proceeding.

### Context Reduction for Small Windows (#1614)

When context window is small:
- Large files truncated (preserves headings + first paragraph per section)
- ROADMAP narrowed to current milestone
- Examples provided via separate `executor-examples.md` reference file
- Keeps prompts cache-friendly for repeated agent invocations

---

## 3. Agent Composition & Handoff Patterns

### Sequential Handoff Chain (Plan-Phase)

```
gsd-phase-researcher → RESEARCH.md → gsd-planner → PLAN.md → gsd-plan-checker
                                                              ↓ (if fails)
                                                         gsd-planner (re-plan)
                                                              ↓ (max 3×)
                                                     PLAN.md approved
```

### Parallel Fan-Out (New Project Research)

```
                    → gsd-project-researcher (stack)    ↘
/gsd:new-project    → gsd-project-researcher (features) → gsd-research-synthesizer → RESEARCH.md
                    → gsd-project-researcher (arch)     ↗
                    → gsd-project-researcher (pitfalls) ↗
```

### Parallel Wave Execution (Execute-Phase)

```
Wave 1:
  plan-1 → gsd-executor (worktree-1) → SUMMARY-1.md + git commits
  plan-2 → gsd-executor (worktree-2) → SUMMARY-2.md + git commits
  plan-3 → gsd-executor (worktree-3) → SUMMARY-3.md + git commits
  (orchestrator merges worktrees)

Wave 2 (blocked on Wave 1):
  plan-4 → gsd-executor (worktree-4) → ...
```

### Debug Loop

```
Execution fails → gsd-debugger (analyze) → gsd-debug-session-manager (coordinate)
                                         → fix plan → gsd-executor (re-execute)
                                         → gsd-code-reviewer (verify fix)
```

---

## 4. Skill vs. Agent Distinction

In GSD terminology:

| Concept | Definition | Storage | Invocation |
|---------|-----------|---------|------------|
| **Skill** | A Claude Code slash command (`/gsd-discuss-phase`) | `~/.claude/skills/gsd-*/SKILL.md` | User types `/gsd-...` |
| **Agent** | A specialized subagent definition spawned programmatically | `~/.claude/agents/gsd-*.md` | Orchestrator calls `Agent(subagent_type=...)` |
| **Workflow** | The markdown file implementing a skill | `~/.claude/get-shit-done/workflows/*.md` | Loaded when skill is invoked |
| **Context** | Injectable reference document | `~/.claude/get-shit-done/contexts/*.md` | Explicitly referenced in `<files_to_read>` |

**Skills are user-facing; agents are internal.**

A skill (e.g., `/gsd:discuss-phase`) invokes a workflow file that in turn spawns one or more agents. Users only interact with skills. Agents never appear in the user interface.

---

## 5. Context Document Inventory

`get-shit-done/contexts/` contains injectable reference files:

- **agent-contracts.md** — Formal contracts for each agent (inputs, outputs, mandatory behaviors)
- **gates.md** — Mandatory gates all executors must pass before marking tasks complete
- **executor-examples.md** — Few-shot examples for executor behavior (used in small context mode)
- **anti-patterns.md** — Known failure modes to avoid (per-phase and cross-phase)
- **discussion-modes.md** — Reference for all discuss-phase modes and their behaviors
- **phase-structure.md** — Template for all phase-level document types

---

## 6. Few-Shot Examples

`get-shit-done/references/few-shot-examples/` demonstrates:

### Good CONTEXT.md Decision Entry
```markdown
<decisions>
**Layout**: Cards (not list/table) — user chose cards for visual scanning; reversible in Phase 4
**State management**: Zustand (not Redux) — simpler for this scope, no cross-domain state
**Pagination**: Infinite scroll (not pages) — aligns with REQUIREMENTS.md §3.2 "continuous browsing"
</decisions>
```

Key pattern: Every decision includes (a) the choice made, (b) the rejected alternative, (c) the *reason*, (d) whether it's reversible.

### Good PLAN.md Task Entry
```markdown
**Task 3: Add pagination to /api/items endpoint**
- tdd: true
- files: ["src/api/items.py", "tests/test_items.py"]
- read_first: ["docs/adr/0003-pagination.md", "src/api/base.py"]
- action: Add `?cursor=` query param with SQLite cursor-based pagination
- verify: `pytest tests/test_items.py -k "pagination"` returns green
- acceptance_criteria: Response includes `next_cursor` field; empty list returns `null` cursor
```

Key pattern: Every task specifies exact files, what to read first, what action to take, how to verify, and the acceptance criterion.

---

## 7. Superpowers Docs (`docs/superpowers/`)

GSD calls major capabilities "superpowers":

- **Parallel Research Superpower**: 4-agent parallel research fan-out for project initialization
- **Wave Parallelization Superpower**: Dependency-aware plan grouping with isolated worktrees
- **Context Continuity Superpower**: CONTEXT.md as inter-session and inter-agent memory
- **Automatic Routing Superpower**: Progress/Manager state machine routing without user intervention
- **Mandatory Verification Superpower**: Accept/reject gate with gap plan generation
- **Safe Resume Superpower**: Incremental checkpoints + STATE.md oracle

Each superpower spec defines: problem solved, mechanism, constraints, failure modes.

---

## 8. Design Philosophy

### Principle of Least Context
Each agent receives only what it needs. No agent sees the full project history. This:
- Prevents context pollution (earlier irrelevant content influencing behavior)
- Allows fresh perspective on each phase
- Reduces token cost per invocation

### Locked Decisions Flow Downstream
Once a user makes a decision in discuss-phase, it's locked in CONTEXT.md and injected into every downstream agent. No agent can contradict a locked decision. This prevents plan/implementation drift from user intent.

### Orchestrators Stay Lean
The top-level orchestrator (the skill command) never does implementation work. It:
- Reads minimal state (STATE.md paths only)
- Spawns agents with full context
- Collects results
- Stays at 15–20% context fill

This ensures the orchestrator never hits context limits and can manage the full milestone lifecycle.

### Gate-First Design
Every phase boundary has explicit quality gates. Work doesn't advance until it passes the gate. Gates are defined in `contexts/gates.md` and enforced by gsd-plan-checker, gsd-verifier, gsd-nyquist-auditor.
