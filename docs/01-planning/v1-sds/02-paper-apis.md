# SDS-02: Paper Search & Full Text APIs

All sources used in Delta's paper retrieval waterfall. Server-side only — the LLM never calls these directly, code does.

---

## Retrieval Waterfall

For a given paper (or search query), try sources in this order:

```
1. Semantic Scholar   — metadata + abstract + OA PDF URL + embeddings
2. OpenAlex           — metadata + abstract + OA URL (better journal filtering)
3. Unpaywall          — per-DOI OA PDF check (fallback when S2 openAccessPdf is null)
4. arXiv API          — preprint search + direct PDF (physics cond-mat + ML)
5. CORE API           — full text body for OA repository papers
6. Crossref           — DOI metadata enrichment + abstract (~40% coverage)
7. User upload        — she downloads on IISc WiFi, uploads PDF to Delta
```

**Deduplication:** by DOI across all sources. One paper found in S2 + OpenAlex = one `PaperResult` record.

---

## 1. Semantic Scholar

**Endpoint base:** `https://api.semanticscholar.org/graph/v1`

**Key endpoints:**
```
GET /paper/search?query=...&fields=paperId,title,abstract,year,authors,externalIds,openAccessPdf,isOpenAccess,tldr,embedding,citationCount
GET /paper/{paperId}?fields=...
GET /paper/{paperId}/citations?fields=...
GET /paper/{paperId}/references?fields=...
GET /recommendations/v1/papers/forpaper/{paperId}
```

**Auth:** `x-api-key: {key}` header. Without key: shared 1 req/sec pool. With key: 1 req/sec guaranteed; request higher via their team.

**What we get:**
- `abstract` — yes
- `openAccessPdf.url` — direct OA PDF URL when known
- `isOpenAccess` — boolean
- `tldr.text` — AI-generated one-sentence summary
- `embedding.vector` — SPECTER2 embedding (768 dims) — usable for similarity search
- `externalIds.DOI` — for deduplication

**Coverage:** 214M papers. Covers Physical Review, Nature Materials, Advanced Materials, JAP, arXiv.

**Rate limits:** 1 req/sec with API key. Use asyncio + semaphore to stay within limit.

**Cost:** Free.

**API key:** Apply at semanticscholar.org/product/api (email form, usually approved quickly).

---

## 2. OpenAlex

**Endpoint base:** `https://api.openalex.org`

**Key endpoints:**
```
GET /works?search=...&filter=has_abstract:true&per-page=25&mailto=your@email.com
GET /works/{id}
GET /works?filter=doi:{doi}
GET /works?filter=primary_location.source.id:{source_id},open_access.is_oa:true
```

**Auth:** None required. Add `mailto=your@email.com` as query param to get polite-pool priority and avoid rate limiting.

**What we get:**
- `abstract_inverted_index` — reconstruct abstract client-side:
  ```python
  def reconstruct_abstract(inverted_index: dict) -> str:
      positions = {pos: word for word, positions in inverted_index.items() for pos in positions}
      return " ".join(positions[i] for i in sorted(positions))
  ```
- `open_access.oa_url` — direct OA PDF URL
- `best_oa_location.pdf_url` — most reliable PDF link
- `primary_location.source.display_name` — journal name
- `cited_by_count` — citation count

**Rate limits:** 10,000 list requests/day, 1,000 search queries/day, 100 content downloads/day.

**Coverage:** 250M works. Better than Semantic Scholar for filtering by specific journal (use `filter=primary_location.source.issn:{issn}`).

**ISSNs for primary user's journals:**
- Physical Review B: 2469-9969
- Physical Review Letters: 1079-7114
- Nature Materials: 1476-4660
- Advanced Materials: 1521-4095
- Journal of Applied Physics: 1089-7550
- ACS Nano: 1936-086X

**Cost:** Free.

---

## 3. Unpaywall

**Endpoint:** `https://api.unpaywall.org/v2/{doi}?email=your@email.com`

**Auth:** `email` query param (mandatory, no API key). Use a real email.

**What we get:**
- `is_oa` — boolean
- `oa_status` — `gold` / `green` / `hybrid` / `bronze` / `closed`
- `best_oa_location.url_for_pdf` — direct PDF URL (null if not found)
- `best_oa_location.version` — `publishedVersion` / `acceptedVersion`
- `oa_locations` — all known copies

**When to call:** After Semantic Scholar returns `openAccessPdf: null`. Pass the DOI.

**Rate limits:** ~100K calls/day for free use. Unrestricted for reasonable use.

**Cost:** Free.

**Registration:** None. Just use a real email address.

---

## 4. arXiv API

**Endpoint:** `http://export.arxiv.org/api/query`

**Key params:**
```
search_query=ti:thin+film+AND+cat:cond-mat.mtrl-sci
start=0
max_results=25
sortBy=relevance
sortOrder=descending
```

**Subject categories relevant to primary user:**
- `cond-mat.mtrl-sci` — condensed matter, materials science
- `cond-mat.supr-con` — superconductivity
- `physics.app-ph` — applied physics
- `cond-mat.mes-hall` — mesoscale and nanoscale physics

**Subject categories for secondary user (ML):**
- `cs.LG`, `cs.AI`, `cs.CL`, `stat.ML`

**What we get:** Atom XML with title, summary (abstract), authors, PDF link, DOI, journal_ref.

**PDF access:** `https://arxiv.org/pdf/{arxiv_id}` — always free, no auth.

**Rate limits:** No hard limit; 3-second delay between sequential requests recommended. Use `asyncio.sleep(3)` between calls.

**Auth:** None needed.

**Cost:** Free.

---

## 5. CORE API

**Endpoint base:** `https://api.core.ac.uk/v3`

**Key endpoints:**
```
GET /search/works?q=...&limit=25
POST /search/works (with JSON body for complex queries)
GET /works/{id}
```

**Auth:** `Authorization: Bearer {api_key}` header. Free key from core.ac.uk.

**What we get:**
- `fullText` — actual paper body as a string (when available)
- `downloadUrl` — PDF from source repository
- `abstract` — yes

**Coverage:** 200M+ OA papers. Excellent for institutional repository deposits and arXiv mirrors. Weak for paywalled content (by design — CORE only indexes OA papers).

**Rate limits:** ~10 req/min on free tier. Use asyncio + rate limiter.

**Cost:** Free.

**When to use:** When we have an OA paper and want full text body for RAG. Semantic Scholar gives us the PDF URL; CORE gives us the extracted text directly (saves PDF extraction step).

---

## 6. Crossref

**Endpoint base:** `https://api.crossref.org`

**Key endpoints:**
```
GET /works?query=...&rows=25&mailto=your@email.com
GET /works/{doi}
```

**Auth:** None. Add `mailto=` to polite pool.

**What we get:**
- `abstract` — for ~40% of papers (APS, Elsevier, Springer participate; Nature often doesn't)
- `is-referenced-by-count` — citation count
- `link` — full-text URLs from publisher
- `license` — usage rights

**When to use:** DOI metadata enrichment. If Semantic Scholar and OpenAlex both miss the abstract, try Crossref. Also useful for verifying DOI validity.

**Rate limits:** Polite pool = effectively unlimited.

**Cost:** Free.

---

## 7. User Upload (Fallback)

When a paper is paywalled and not in any OA repository:
- She downloads the PDF on IISc LAN/WiFi
- Uploads to Delta via `POST /papers/upload`
- Delta stores the PDF in R2, extracts text, runs chunking + embedding
- Paper becomes available for synthesis in that session

**Implementation:** `pdfminer.six` or `pypdf` for extraction. Chunk at 1,000 tokens with 200 token overlap. Embed with `text-embedding-3-small` (OpenAI) or a local model.

---

## PDF Extraction Stack

When we have a PDF URL (from Unpaywall, arXiv, CORE, or user upload):

```python
# Extraction
pdfminer.six  # better text extraction than pypdf for multi-column layouts
# or
pymupdf (fitz)  # faster, handles more PDF variants

# Chunking
langchain_text_splitters.RecursiveCharacterTextSplitter(
    chunk_size=1000,
    chunk_overlap=200,
    separators=["\n\n", "\n", ". ", " "]
)

# Embedding
text-embedding-3-small (1536 dims) via LiteLLM
# or: nomic-embed-text (768 dims) if cost is a concern
```

**Token budget enforcement:** Never pass more than 40K tokens of paper content to the synthesis model. Rank chunks by cosine similarity to the research query, take top-K within budget.

---

## Web Search Fallback

For queries that need current web information (not just papers):

**Tavily** — primary web search fallback
- 1,000 free API credits/month, no credit card
- AI-native: designed for agent use, returns structured results
- Endpoint: `https://api.tavily.com/search`
- Free tier sufficient for 2 users

**Exa** — alternative (1,000 free requests/month)
- Neural search, better for "find papers similar to X" queries
- Worth applying for the $1K education/startup grant

**What NOT to use:** SerpAPI (250 searches too low), DuckDuckGo (no official API).

---

## API Keys Needed

| Service | How to get | Time |
|---------|-----------|------|
| Semantic Scholar | semanticscholar.org/product/api — email form | 1–3 days |
| OpenAlex | openalex.org — free account | Instant |
| CORE | core.ac.uk — register | Instant |
| Tavily | app.tavily.com | Instant |
| Unpaywall | No key — just use a real email | No wait |
| arXiv | No key needed | No wait |
| Crossref | No key — just add mailto= param | No wait |
