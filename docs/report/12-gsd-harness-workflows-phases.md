# Report 12: GSD Redux — Harness, Workflows & Phase Orchestration

Source: `reference-repos/get-shit-done-redux`

---

## Executive Summary

**get-shit-done-redux (GSD)** is a prompt-engineering meta-framework layered on top of Claude Code and compatible runtimes. It is not a runtime agent itself — it is a **workflow orchestration system** built from markdown command files, specialized agent definitions, injectable context documents, and Claude Code hooks. The fundamental insight: treat an AI coding session as a state machine with explicit phases, decision checkpoints, and persistent artifacts that survive session boundaries.

---

## 1. The Six-Command Main Loop

```
/gsd-new-project → /gsd-discuss-phase → /gsd-plan-phase → /gsd-execute-phase → /gsd-verify-work → /gsd-ship → repeat
```

Each command is a discrete workflow markdown file (50–500 lines) that orchestrates subagents or executes inline. Three nested orchestration layers:

1. **Top-level orchestrator** (user command) — stays at 15–20% context, dispatches subagents
2. **Subagent layer** (gsd-executor, gsd-planner, gsd-researcher) — gets 100% fresh context per spawn
3. **Inline execution** (small phases/interactive mode) — orchestrator runs plan directly

---

## 2. Three Core Phases

### DISCUSS-PHASE (499 lines)

**Purpose**: Capture implementation decisions *before* planning, so user preferences flow downstream.

**Phase steps**:
1. Parse phase number, check blocking anti-patterns, load PROJECT.md/REQUIREMENTS.md/prior CONTEXT files
2. If CONTEXT.md already exists, offer to update/view/skip
3. Load prior 3 phases' CONTEXT.md files to avoid re-asking decided questions
4. Scout codebase for existing patterns and reusable assets (10% context max)
5. Identify gray areas (domain boundary, prior decisions, unresolved questions)
6. Present gray areas; user selects which to discuss
7. Deep-dive per area with mode overlays:
   - `--default`: 4 single-question turns per area
   - `--batch`: group 2–5 questions per turn
   - `--text`: plain-text numbered lists (CLI/remote sessions)
   - `--analyze`: trade-off table before each question
   - `--auto`: Claude picks recommended option
   - `--power`: skip to expert fast-track
   - `--advisor`: research-backed comparison + table-first selection
8. Write `{padded_phase}-CONTEXT.md` with sections:
   - `<domain>`: phase boundary/deliverable
   - `<decisions>`: locked implementation choices
   - `<canonical_refs>`: full file paths to specs/ADRs — **MANDATORY**, downstream agents must read
   - `<specifics>`: particular references/examples
   - `<code_context>`: reusable patterns from scouting
   - `<deferred>`: scope-creep ideas for future phases
9. Git commit CONTEXT.md + `{padded_phase}-DISCUSSION-LOG.md`
10. Update STATE.md; optionally chain to plan-phase with `--chain`

**Unique mechanics**:
- **Incremental checkpoint**: After each area, write `{padded_phase}-DISCUSS-CHECKPOINT.json` → session can resume mid-discussion
- **Scope guardrail**: If user mentions out-of-phase feature → redirect to deferred, never expand scope
- **Prior-decision carrying**: If Phase 2 decided "infinite scroll", Phase 3 doesn't re-ask pagination

---

### PLAN-PHASE (500+ lines)

**Purpose**: Transform CONTEXT.md + optional research into executable PLAN.md files (one per plan/slice).

**Default flow**: Research (optional) → Plan → Verify (loop) → Done

**Subagent chain**:
1. **gsd-phase-researcher** (if research enabled) → produces RESEARCH.md with domain exploration, patterns, risks, library recommendations
2. **gsd-planner** → reads CONTEXT.md + RESEARCH.md → produces PLAN.md with tasks + estimates
3. **gsd-plan-checker** → verifies plan quality against gates (mandatory):
   - Does each task have a clear objective?
   - Are estimates realistic?
   - Hidden dependencies?
   - Will this actually achieve the phase goal?

**Revision loop**: Fails verification → plan-checker generates fix suggestions → planner re-plans → re-verify (max 3 iterations)

**Express paths** (bypass discuss-phase):
- `--prd <file>` → parse PRD into CONTEXT.md automatically
- `--ingest <adr-paths>` → parse ADRs into CONTEXT.md
- `--research-phase <N>` → research-only mode (no planner spawn)

**Key gates**:
- **Closed-phase gate** (#3569): If phase is `Complete` (has VERIFICATION.md with `status: passed`), refuse to replan unless `--force`
- **MVP+TDD gate**: When both modes active, tasks must have failing-test commit before implementation
- **AI-SPEC detection**: If phase involves AI/LLM, offer `/gsd:ai-integration-phase` before planning

---

### EXECUTE-PHASE (500+ lines)

**Purpose**: Run all plans in a phase with wave-based parallelization.

**Wave execution model**:
- Plans grouped into **waves** (dependencies resolved at plan-time)
- Within a wave: parallel execution (if `parallelization: true`) or sequential
- Between waves: sequential (wave N+1 waits for wave N)
- **Wave safety check**: Detect `files_modified` overlap across plans → force sequential for that wave (safety net for planning defects)

**Subagent contract**:
```
For each plan in wave:
  spawn gsd-executor with:
    - full PLAN.md + phase CONTEXT.md
    - fresh 200K context window per executor
    - isolation: "worktree" (default)
  executor returns:
    - SUMMARY.md (what was built, blockers)
    - git commits (atomic per task)
    - STATE.md update (plan marked complete)
```

**Isolation strategy**:
- **Git worktrees** (default): each executor works in `worktree-agent-{phase}-{plan}` → parallel safe
- **Submodule exception**: If plan touches `.gitmodules` path → drop isolation, run sequential
- **Codex exception**: Codex has no Agent isolation → sequential inline fallback

**Runtime compatibility**:
- **Claude Code**: `Agent(subagent_type="gsd-executor", isolation="worktree")` → parallel, blocks until complete
- **Copilot**: Subagent completion signals unreliable → fallback to sequential inline via `execute-plan.md`
- **Others**: If `Agent` tool unavailable → sequential inline fallback

**Checkpoint heartbeats** (#2410) — prevent "Stream idle timeout" on large phases:
```
[checkpoint] phase {N} wave {W}/{total_W} starting, {plans_count} plan(s), {completed}/{total} plans done
[checkpoint] phase {N} wave {W}/{total_W} plan {id} complete, {P}/{Q} plans done
```

**Optional modes**:
- `--wave N` → execute only Wave N (quota management)
- `--gaps-only` → execute only gap-closure plans (from `/gsd:verify-work` fix generation)
- `--interactive` → inline execution with user checkpoints between tasks (pair-programming style)

---

## 3. Cross-Phase Orchestration

### PROGRESS Workflow

Answers "Where are we?" and routes to next action.

```
1. Load STATE.md (current_phase, completed_plans, blocked_plans)
2. Analyze ROADMAP.md (phase order, dependencies, completion status)
3. ROUTE:
   a. No project? → /gsd:new-project
   b. Phase incomplete:
      - No CONTEXT? → /gsd:discuss-phase
      - No PLAN? → /gsd:plan-phase
      - Plans incomplete? → /gsd:execute-phase
      - Execution blocked? → /gsd:debug
      - Ready for QA? → /gsd:verify-work
   c. Phase complete but not shipped? → /gsd:ship
   d. Milestone complete? → /gsd:complete-milestone
   e. Next milestone queued? → /gsd:new-milestone
```

### MANAGER Workflow — Interactive Command Center

Dashboard with ASCII progress bar + phase status table:
```
 ████████████░░░░░░░░ 60%  (3/5 phases)
 ◆ Background: Planning Phase 4
 | # | Phase                | Deps | D | P | E | Status              |
 | 1 | Foundation           | —    | ✓ | ✓ | ✓ | ✓ Complete          |
 | 2 | API Layer            | 1    | ✓ | ✓ | ◆ | ◆ Executing (active)|
 | 3 | Auth System          | 1    | ✓ | ✓ | ○ | ○ Ready to execute  |
```

Actions via AskUserQuestion:
- `Continue` → dispatch ALL recommended actions (background + inline, parallel)
- `Refresh dashboard` → poll background agent updates
- `Exit manager`

---

## 4. Phase Lifecycle State Machine

Disk status field in CONTEXT.md frontmatter:

```
no_directory  → discuss-phase runs → discussed
discussed     → plan-phase runs  → planned
planned       → execute-phase    → partial (some done) or complete (all done)
complete      → verify-work      → needs_review (gaps found) or verified
verified      → ship             → shipped
```

---

## 5. Context Management Strategy

### Seven Canonical Documents

| Document | Scope | Lifetime |
|----------|-------|----------|
| `PROJECT.md` | Vision, tech stack | Entire project |
| `REQUIREMENTS.md` | Feature set, acceptance criteria | Current milestone |
| `ROADMAP.md` | Phased delivery, dependencies | Current milestone |
| `STATE.md` | Current position, decisions, blockers | Session-to-session |
| `{phase}-CONTEXT.md` | Implementation decisions locked by user | Per-phase |
| `{phase}-RESEARCH.md` | Technical findings, patterns, risks | Per-phase |
| `{plan}-PLAN.md` | Executable tasks + estimates | Per-plan |

### Context Injection Pattern

Orchestrator → subagent handoff:
1. Orchestrator loads minimal init JSON (paths only, ~2% context)
2. Subagent receives full `<files_to_read>` block in its prompt
3. Subagent has 100% fresh context for its task

### Adaptive Context Windows (#3707)

```bash
CONTEXT_WINDOW=$($GSD_SDK query config-get context_window 2>/dev/null || echo "200000")

if [ "$CONTEXT_WINDOW" >= 500000 ]; then
  # Include 3 prior phase CONTEXT/SUMMARY pairs + explicit deps
else
  # Thin for <200K models: extract examples on-demand via executor-examples.md
fi
```

### Prior Context Carryover

- **Discuss-phase**: Load most recent 3 prior CONTEXT.md files → extract `<decisions>` → annotate gray areas ("You already chose X in Phase 5")
- **Plan-phase**: Load this phase's CONTEXT.md + all `Depends on:` phases from ROADMAP
- **Execute-phase**: Executor reads this phase's CONTEXT + RESEARCH + all prior SUMMARY.md files (if CONTEXT_WINDOW >= 500K)

---

## 6. Fault Tolerance & Safe Resume

### Discuss-Phase Checkpoint
```json
// {padded_phase}-DISCUSS-CHECKPOINT.json written after each gray area
{
  "timestamp": "2026-05-24T...",
  "areas_completed": ["Layout", "Behavior"],
  "areas_remaining": ["Error handling"],
  "decisions": { ... }
}
```
On resume: load checkpoint, skip completed areas, continue from last.

### Execute-Phase Safe Resume Gate (#3097)
```bash
# If executor crashed mid-plan: partial commits exist but SUMMARY.md missing
if [ commits exist ] && [ SUMMARY.md missing ]; then
  # Options: (1) close out manually, (2) re-execute from scratch, (3) mark-and-skip
fi
```

### Worktree Orphan Reaping (#3707)
```bash
# Clean up locked worktrees from crashed sessions before spawning new executors
$GSD_SDK query worktree.reap-orphans 2>/dev/null || true
```

### STATE.md Staleness Detection
Every workflow checks `last_activity`. If stale, offers:
- "Resume from last checkpoint"
- "Start fresh" (abandon partial work, reset phase state)

### Cross-AI Fallback (#3095)
With `--cross-ai` flag:
1. Construct task prompt from PLAN.md
2. Run external command with timeout (default 300s)
3. Success → validate summary, write SUMMARY.md, mark plan complete
4. Failure → offer retry / skip (fallback to executor) / abort

---

## 7. Subagent Brief Structure

Every spawned subagent receives:

```markdown
<system_context>
You are executing a plan for a software project. Your job: do the work, test it, commit it.
[role description + constraints]
</system_context>

<files_to_read>
@~/.claude/get-shit-done/references/gates.md
@~/.claude/get-shit-done/workflows/execute-plan.md
.planning/PROJECT.md
.planning/STATE.md
.planning/phases/{phase}/{phase}-CONTEXT.md
.planning/phases/{phase}/{plan}-PLAN.md
</files_to_read>

<required_reading>
DO NOT proceed until you have read every file above.
</required_reading>

<execution_context>
Phase: ${PHASE_NUMBER}
Plan: ${PLAN_ID}
Isolation: worktree

BLOCKING ANTI-PATTERNS:
- Do not skip tests on green
- Do not commit incomplete features
</execution_context>
```

---

## 8. Canonical Refs in CONTEXT.md (MANDATORY Pattern)

```markdown
<canonical_refs>
## Canonical References

Downstream agents MUST read these before planning or implementing.

### Architecture
- `docs/adr/0001-dispatch-policy.md` — Query dispatch outcomes (centralized seam)

### UI/UX
- `docs/references/ui-brand.md` — Design system, color palette, typography

### Tech Stack
- `docs/tech-stack.md` — Framework versions, DB choice, deployment target
</canonical_refs>
```

Every ref has: full relative path + one-line why downstream needs it.

---

## 9. Profile-Based Skill Installation

**Problem**: Not every project needs all 80 commands.

**Solution**: Named profiles at bootstrap time:
```bash
npx @opengsd/get-shit-done-redux --profile=core     # 6 core commands
npx @opengsd/get-shit-done-redux --profile=standard  # core + phase mgmt
npx @opengsd/get-shit-done-redux                     # full suite (default)
```

Profiles: `core` (new-project, discuss-phase, plan-phase, execute-phase, verify-work, ship), `standard` (core + milestone), `full` (all 80 commands).

---

## 10. Key Innovations vs. Vanilla Agent

| Dimension | Vanilla | GSD |
|-----------|---------|-----|
| Context rot | Fills up, quality degrades | 7 canonical docs; subagents start fresh each time |
| Memory | Lost between sessions | CONTEXT.md/STATE.md survive session boundaries |
| Phase transition | Manual ("what next?") | Automatic routing via progress/manager state machine |
| Parallelization | Hard to coordinate | Wave-based DAG with dependency tracking |
| Fault tolerance | Session dies = restart | Incremental checkpoints + git commits verify work |
| Verification | Hope it works | Mandatory verify step, gaps → fix plans → re-execute |
| Decision capture | Lost in chat history | Locked in CONTEXT.md per phase, consulted by downstream |
| Cost optimization | Wasteful | Adaptive context windows, express paths, orchestrator stays lean |
