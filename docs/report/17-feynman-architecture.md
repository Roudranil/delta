# Report 17: Feynman — Research Agent Architecture, Skills & Pi Runtime

Source: `reference-repos/feynman`

---

## Executive Summary

Feynman is a TypeScript/Node.js CLI research agent built on the Pi runtime (a fork of OpenCode). It adds a research-domain skills library on top of Pi's agent loop, integrating AlphaXiv for paper search, Hugging Face Hub for dataset/model metadata, Docker/Modal/RunPod for compute execution, and Exa/Perplexity/Gemini for web search. The architecture is skills-as-prompts: research workflows are implemented as markdown prompt files, not Python code. Delta should not replicate Feynman's runtime but can copy its skills taxonomy and research workflow designs.

---

## 1. Architecture Overview

```
Feynman CLI (Node.js TypeScript)
  ├── Pi runtime (agent loop, tool execution, session management)
  │     ├── package-ops.ts     — install/manage Pi packages
  │     ├── runtime.ts         — start/configure Pi process
  │     ├── runtime-patches.ts — patch Pi defaults (model, search, config)
  │     └── web-access.ts      — web search provider configuration
  ├── src/
  │     ├── cli.ts             — command parser, slash command routing
  │     ├── search/commands.ts — /lit, /deepresearch, /review, etc.
  │     ├── model/             — model registry, service tiers, catalog
  │     └── setup/             — first-run setup wizard, API key config
  └── skills/                  — prompt files for each research workflow
        ├── deepresearch.md
        ├── lit-review.md
        ├── peer-review.md
        ├── audit.md
        └── ...
```

**Key insight:** Feynman is a thin wrapper. The intelligence is in the skills (prompt files) and the AlphaXiv integration. The Pi runtime handles all the agent loop mechanics. This is the inverse of Delta's approach — Delta owns the agent loop (LangGraph), Feynman delegates it to Pi.

---

## 2. Research Workflows (Slash Commands)

| Command | What it does |
|---------|-------------|
| `/deepresearch <topic>` | Multi-agent parallel investigation, synthesis, verification |
| `/lit <topic>` | Literature review: consensus, disagreements, open questions |
| `/review <artifact>` | Simulated peer review with severity-graded feedback |
| `/audit <paper_id>` | Compare paper claims against public codebase |
| `/replicate <paper>` | Run experiments on local/cloud GPUs |
| `/recipe <task>` | Find ranked ML training recipes from papers/datasets/code |
| `/compare <topic>` | Source comparison matrix |
| `/draft <topic>` | Paper-style draft from research notes |
| `/autoresearch <idea>` | Autonomous experiment loop |
| `/watch <topic>` | Recurring research watch |
| `/outputs` | Browse all research artifacts |

**For Delta's UX design:** These are exactly the modes Delta needs, translated to non-technical UX:
- "Find papers" -> `/lit` equivalent
- "Understand a paper" -> `/review` equivalent  
- "What's new on this topic" -> `/watch` equivalent
- "Generate literature review" -> `/deepresearch` + `/draft`

The key difference: Feynman uses slash commands in a CLI. Delta must expose these as buttons.

---

## 3. Four Bundled Research Agents

```
Researcher  — gather evidence across papers, web, repos, docs
Reviewer    — simulated peer review with severity-graded feedback
Writer      — structured drafts from research notes
Verifier    — inline citations, source URL verification, dead link cleanup
```

This four-agent design is clean. For Delta's v1, collapse all four into one graph. For v2, consider splitting into:
- `searcher` — finds and retrieves papers
- `synthesizer` — reads and extracts key findings
- `writer` — produces the output report
- `verifier` — checks that all cited papers were actually retrieved

The Verifier agent is especially important: its job is checking that every inline citation corresponds to a paper that was actually retrieved in the session — the inline citation trust problem.

---

## 4. AlphaXiv Integration

AlphaXiv is a paper search and annotation layer on top of arXiv. Feynman uses it via the `alpha` CLI:

```bash
alpha search "mechanistic interpretability"  # search papers
alpha qa "2401.12345" "what is the key method?"  # ask questions about a paper
alpha code "2401.12345"  # read the associated codebase
alpha annotations "2401.12345"  # read community annotations
```

**For Delta:** AlphaXiv only covers arXiv. The primary user (experimental physics at IISc) needs Physical Review, Nature Materials, Advanced Materials — none of which are on arXiv. AlphaXiv is irrelevant for her domain.

Use Semantic Scholar API instead. It covers her journals.

---

## 5. Skills Architecture (Prompt Files)

Feynman's skills are markdown files that Pi injects into the agent's context:

```markdown
# Literature Review Skill

You are conducting a systematic literature review on: {{topic}}

## Phase 1: Search
Search AlphaXiv and web for papers using these queries:
  1. {{topic}} survey
  2. {{topic}} review
  3. {{topic}} recent advances

## Phase 2: Extract
For each paper found:
  - Identify the core claim
  - Note the methodology
  - Record the key results

## Phase 3: Synthesize
Organize findings by:
  - Consensus views
  - Areas of disagreement
  - Open questions
  - Future directions
```

**For Delta:** This is essentially what Delta's prompts should look like — structured workflow instructions injected into the agent. The content is excellent; the mechanism (Pi runtime injection) is different (LangGraph nodes in Delta).

Copy the **content** of Feynman's skills, not the runtime mechanism.

---

## 6. Model Registry & Service Tiers

```typescript
// model/service-tier.ts
type ServiceTier = "free" | "pro" | "enterprise"

// model/catalog.ts — defines available models per tier
// model/registry.ts — runtime model selection based on tier + task
```

Feynman supports multiple search providers:
- Exa (primary for research)
- Perplexity
- Gemini API

For Delta: Exa is expensive (~$20/month). Semantic Scholar is free and covers her domain better than Exa for physics papers. Skip Exa for v1.

---

## 7. Bootstrap & Sync

```typescript
// bootstrap/sync.ts — syncs skills/prompts from remote on startup
// Allows skills to be updated without reinstalling the app
```

For Delta: Not needed. Skills are baked into the codebase. Don't add remote skill sync for 2 users.

---

## 8. Web Search Configuration

```typescript
// src/pi/web-access.ts
// Configures which search provider Pi uses:
//   - exa: research-grade semantic search
//   - perplexity: general web
//   - gemini: Google web access
```

Feynman defaults to Exa for academic research. This costs money per query. For Delta, Semantic Scholar's free API is sufficient for paper discovery. Add Exa only if the user specifically wants web-based research beyond papers.

---

## 9. Source Grounding Requirement

From the README:
> Every output is source-grounded — claims link to papers, docs, or repos with direct URLs.

Feynman enforces this at the Verifier agent level. All claims must link to an actual retrieved source. This is the right requirement for Delta too.

**For Delta:** Never let the LLM fabricate citations. If a claim can't be linked to a paper in the retrieved set, it should be marked as "not verified" or omitted. The `Verifier` agent pattern handles this.

---

## 10. What Delta Should Copy from Feynman

| Element | Application in Delta |
|---------|---------------------|
| Four-agent taxonomy (Researcher/Reviewer/Writer/Verifier) | Design Delta's graph around these roles |
| Research workflow templates (lit, review, deep) | Copy prompt content from skills/*.md |
| "Source grounded" requirement | Every claim links to paper_id or DOI |
| `/watch` recurring research concept | Future: alert when new papers appear |
| `/audit` paper vs. codebase check | Interesting for ML researcher (secondary user) |
| Slash command UX taxonomy | Map to Delta's button UX |

---

## 11. What Delta Should Ignore from Feynman

- Pi runtime (use LangGraph instead)
- TypeScript / Node.js (Delta is Python)
- AlphaXiv (arXiv only — wrong domain for primary user)
- Exa web search (expensive, not needed for paper discovery)
- Docker/Modal/RunPod compute execution (wrong use case)
- Remote skill sync (overkill for 2 users)
- CLI interface (Delta is a web app)

---

## 12. The Core Design Lesson

Feynman's skill quality is high because each skill is a carefully written prompt template with explicit phases (search -> extract -> synthesize -> verify). The intelligence is in the prompts, not the runtime.

Delta should invest heavily in prompt engineering for each research mode. The LangGraph scaffolding is cheap; the prompt quality determines output quality. Study Feynman's skill files carefully when writing Delta's prompts.
