# Report 16: LLM Wiki — Karpathy Pattern, Incremental Knowledge Base & What's Worth Stealing

Source: `reference-repos/llm_wiki`

---

## Executive Summary

LLM Wiki is a desktop app (Tauri v2 + React + TypeScript) implementing Karpathy's "LLM Wiki" pattern: instead of RAG (retrieve-and-answer fresh every time), the LLM **incrementally builds and maintains a persistent wiki** from your documents. Knowledge is compiled once and kept current. This is architecturally opposite to how Delta works, but the knowledge graph, two-step ingest chain-of-thought, and source traceability patterns are directly applicable to Delta's synthesis and note-taking features.

---

## 1. The Karpathy Pattern (Core Architecture)

```
Three layers:
  raw/sources/     — immutable documents you add
  wiki/            — LLM-generated knowledge pages (entities, concepts, sources)
  schema.md        — structural rules (what page types exist, how to link)
  purpose.md       — why this wiki exists (LLM reads this for context)

Three operations:
  Ingest   — read source -> analyze -> write wiki pages
  Query    — semantic search + graph expansion -> LLM answer with citations
  Lint     — detect dead wikilinks, orphaned pages, stale content
```

**Key insight:** The wiki is the LLM's persistent memory. Every query starts by reading relevant wiki pages, not the raw sources. This is RAG over a curated synthesis layer, not raw documents.

**For Delta:** Delta doesn't maintain a persistent wiki. It runs research fresh each session. The pattern is too heavy for v1. But specific elements — especially the two-step ingest and source traceability — are worth copying at the output layer.

---

## 2. Wiki Page Structure

Every generated page has YAML frontmatter:
```yaml
---
title: "Thin Film Deposition"
type: concept           # entity | concept | source | query | synthesis
sources:
  - raw/sources/paper_2023.pdf
  - raw/sources/review_2024.pdf
created: 2024-01-15
updated: 2024-01-20
---

# Thin Film Deposition

...content with [[wikilinks]] to other pages...
```

**`sources[]` field is the traceability mechanism.** Every wiki page links back to the raw documents that contributed to it. This prevents the inline citation trust problem: you only cite sources that actually exist in your collection.

For Delta: every claim in the research output should link back to the paper it came from (DOI, Semantic Scholar paper ID, or URL). Build this as a `sources` list on every output section.

---

## 3. Two-Step Chain-of-Thought Ingest

Open Notebook and similar tools do single-step ingest (read -> write). LLM Wiki splits it:

**Step 1 — Analysis LLM call:**
```
Input:  source document + existing wiki index + purpose.md
Output: structured analysis
  - key_entities: [list]
  - key_concepts: [list]
  - connections_to_existing: [list of existing pages this connects to]
  - contradictions: [conflicts with existing wiki content]
  - recommended_structure: [what pages to create/update]
  - review_items: [things needing human judgment]
  - search_queries: [web searches to fill gaps]
```

**Step 2 — Generation LLM call:**
```
Input:  analysis from Step 1 + existing relevant wiki pages
Output: wiki files to write
  - source summary page
  - entity pages (people, orgs, materials, instruments)
  - concept pages (theories, methods, phenomena)
  - updated index.md, log.md, overview.md
```

**Why two steps is better:** Step 1 forces the LLM to think before writing. The analysis surfaces connections and contradictions that a single-step approach misses. Output quality is significantly higher.

**For Delta:** When synthesizing a literature review, use this two-step pattern:
1. Analysis pass: what are the themes, what agrees, what contradicts, what's missing?
2. Writing pass: structured report from the analysis

---

## 4. Knowledge Graph with 4-Signal Relevance Model

```
Relevance signals (combined score):
  direct_link      ×3.0   — pages linked via [[wikilinks]]
  source_overlap   ×4.0   — pages sharing the same raw source (frontmatter sources[])
  adamic_adar      ×1.5   — pages sharing common neighbors (weighted by degree)
  type_affinity    ×1.0   — bonus for same page type (entity↔entity)
```

Used for both query retrieval and graph visualization (sigma.js + ForceAtlas2).

**For Delta's future:** This relevance model is exactly what would power the "what connects to what" feature the primary user wants (tracing how her research topics relate). Not for v1, but the algorithm is clear and implementable.

**Louvain community detection** for automatic cluster discovery of related papers — also not for v1, but note it for v2.

---

## 5. Multi-Phase Query Retrieval Pipeline

```
Phase 1: Tokenized Search
  - word splitting + stop word removal
  - title match bonus (+10 score)
  - searches both wiki/ and raw/sources/

Phase 1.5: Vector Semantic Search (optional)
  - LanceDB (Rust, embedded) for ANN retrieval
  - cosine similarity over embeddings
  - results merged into Phase 1: boosts existing + adds new

Phase 2: Graph Expansion
  - top search results as seed nodes
  - 4-signal relevance model finds related pages
  - 2-hop traversal with decay

Phase 3: Budget Control
  - configurable context window: 4K -> 1M tokens
  - 60% wiki pages / 20% chat history / 5% index / 15% system
  - pages prioritized by combined relevance score
```

For Delta: this retrieval pipeline structure (search -> expand -> budget-limit -> assemble) is a clean blueprint for deciding what to include in the LLM's context window during research synthesis.

---

## 6. SHA256 Incremental Caching

```python
# Before ingesting a source, hash its content
import hashlib
content_hash = hashlib.sha256(source_content.encode()).hexdigest()

# Check if this hash has been processed before
if content_hash in processed_hashes:
    skip()  # unchanged, don't spend tokens
else:
    ingest(source)
    processed_hashes[content_hash] = True
    save_cache()
```

Also used in LLM Wiki for:
- Skipping unchanged wiki pages during lint
- Skipping already-embedded pages during vector indexing

**For Delta:** If a paper was already fetched and summarized in a previous session, cache it. Don't re-fetch and re-summarize. Key: `(paper_id, model_version)` -> cached summary.

---

## 7. Review System (Async Human-in-the-Loop)

During ingest, the LLM flags items for human review:
```yaml
review_items:
  - type: create_page      # Create a new page about topic X
    reason: "Mentioned 5 times but no page exists"
    search_queries:
      - "X mechanism review 2023"
      - "X applications materials science"
  - type: deep_research    # Trigger web search on topic Y
    reason: "Gap in current wiki coverage"
  - type: skip             # LLM suggests to ignore
```

Constrained to 3 action types to prevent LLM hallucination of arbitrary actions.

**For Delta:** When the research agent identifies gaps ("I couldn't find papers on X"), let it flag them explicitly for the user rather than silently omitting them. Same pattern: constrained action types, not free-form LLM output.

---

## 8. Deep Research (Web Search Integration)

When knowledge gaps are found:
1. LLM generates domain-aware search queries (reads `purpose.md` + `overview.md` for context)
2. User sees confirmation dialog with editable queries before search starts
3. Web search via Tavily / SerpApi / SearXNG
4. LLM synthesizes findings -> wiki research page with cross-references
5. Auto-ingest into knowledge base

**For Delta:** The "confirm before searching" pattern is good UX. Before launching a full research run, show the user the search plan and let her edit it. Especially important for a non-technical user who may have a very specific context the LLM doesn't know about.

---

## 9. Tech Stack: What Delta Should Care About

| Component | LLM Wiki choice | Delta consideration |
|-----------|-----------------|---------------------|
| Desktop | Tauri v2 (Rust) | Not applicable — Delta is web |
| Frontend | React + Vite | React or Vue for Delta frontend |
| Knowledge graph | sigma.js + graphology | Future: visualize paper connections |
| Vector search | LanceDB (embedded Rust) | SQLite-vec or skip for v1 |
| State | Zustand | Good choice for Delta frontend |
| LLM streaming | Custom fetch (OpenAI/Anthropic APIs) | Use LiteLLM instead |
| Web search | Tavily / SerpApi / SearXNG | Semantic Scholar API for Delta |

---

## 10. What Delta Should Copy from LLM Wiki

| Feature | Where to apply in Delta |
|---------|------------------------|
| Two-step ingest (analyze then generate) | Research synthesis: analyze papers -> write report |
| `sources[]` traceability in every output | Every claim links to its paper ID |
| SHA256 incremental cache | Cache paper summaries across sessions |
| Budget-controlled context assembly | Limit what goes into final synthesis prompt |
| "Confirm before searching" UX | Show research plan before executing |
| Constrained review item types | When flagging gaps, use typed actions not free text |
| `purpose.md` concept | User's stated research scope shapes all searches |

---

## 11. What Delta Should Ignore

- Tauri desktop app (Delta is a web app)
- Persistent wiki maintenance (Delta runs fresh research, no maintained wiki in v1)
- Knowledge graph visualization (v2 feature)
- Louvain community detection (v2)
- Local LanceDB vector store (Semantic Scholar handles relevance)
- Chrome extension web clipper (irrelevant for paper research)
- i18n, multi-platform builds (Delta has 2 users)

---

## 12. The Fundamental Tradeoff: Wiki vs. Fresh Research

LLM Wiki compiles knowledge incrementally -> cheap queries, stale if sources not updated.
Delta (and GPT Researcher/Open Deep Research) runs fresh research -> expensive, always current.

For experimental physics paper discovery, **fresh research is correct**. Her field moves fast. A 6-month-old wiki entry about thin film deposition techniques would miss recent Physical Review Letters. Delta's approach is right.

The wiki pattern becomes valuable if Delta ever adds a "remember what I've found before" feature — essentially, let the user build a persistent wiki from their research sessions. That's a v2 feature.
