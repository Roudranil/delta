# SDS-03: Paper APIs & Full Text Pipeline

The agent sees exactly one tool: `fetch_paper(...)`. Everything below is hidden inside it.

## The Single Tool Contract

```python
async def fetch_paper(
    query: str | None = None,        # keyword search
    doi: str | None = None,          # direct DOI lookup
    arxiv_id: str | None = None,     # arXiv ID lookup
    s2_id: str | None = None,        # Semantic Scholar paper ID
    mode: Literal["full", "abstract_only"] = "full",
) -> PaperResult | None
```

**Input:** one of query / doi / arxiv_id / s2_id.
**Output:** a single `PaperResult` with guaranteed fields, or `None` if nothing found.
**The agent never calls individual APIs.** It calls `fetch_paper`, the waterfall runs internally, and a `PaperResult` comes back.

## PaperResult — Guaranteed Output Schema

```python
@dataclass
class PaperResult:
    # Identity — always present
    paper_id: str                      # internal Delta ID (SHA256 of DOI or arXiv ID)
    doi: str | None
    arxiv_id: str | None
    s2_id: str | None

    # Metadata — always present
    title: str
    authors: list[str]                 # ["Smith, J.", "Lee, K."]
    year: int | None
    venue: str | None                  # journal or conference name
    citation_count: int | None

    # Abstract — always present if paper found (may be reconstructed from inverted index)
    abstract: str | None

    # Open access
    is_open_access: bool
    pdf_url: str | None                # direct PDF URL if found, else None

    # Full text (populated only when mode="full" and PDF was fetchable)
    full_text: str | None              # raw extracted text
    chunks: list[TextChunk] | None     # chunked + scored for RAG

    # Embeddings (populated after chunk embedding)
    content_embedding: list[float] | None   # 1536-dim, mean of chunk embeddings
    specter_embedding: list[float] | None   # 768-dim, from Semantic Scholar

    # Status flags
    full_text_available: bool          # False = abstract only
    full_text_source: str | None       # "core" | "arxiv_pdf" | "unpaywall" | "user_upload"
    abstract_only: bool                # True = LLM will reason over abstract only

    # Provenance
    source_apis: list[str]             # which APIs contributed data
    fetched_at: datetime


@dataclass
class TextChunk:
    chunk_id: str                      # SHA256 of content
    paper_id: str
    text: str
    chunk_index: int
    token_count: int
    relevance_score: float | None      # populated after query-based ranking
```

## Waterfall Priority Order

For a given paper identifier, sources are tried in this order. Each step either succeeds (returns data and stops) or falls through to the next.

```
STEP 1 — Cache check (Neon papers table)
  Input:  doi | arxiv_id | s2_id
  Action: SELECT from papers WHERE doi=$1 OR arxiv_id=$2
  Hit:    return cached PaperResult immediately, skip all API calls
  Miss:   continue to step 2

STEP 2 — Semantic Scholar
  Input:  query | doi ("DOI:10.xxx") | arxiv_id ("ARXIV:2301.xxx") | s2_id
  Action: GET /paper/search or GET /paper/{id}
  Gets:   metadata, abstract, openAccessPdf.url, specter embedding, citation count
  On hit: populate PaperResult metadata fields; if openAccessPdf.url present → jump to STEP 6
  On miss or no PDF: continue to STEP 3

  STEP 2b — Citation snowballing (runs after initial search, not per-paper)
  Trigger: after N seed papers have been collected from steps 2–5 across all subtopics
           only runs during Deep Research mode, not Quick Answer
  Input:   top 3–5 seed papers ranked by cosine_similarity(specter_embedding, query_embedding)
  
  For each seed paper:
    backward = GET /paper/{s2_id}/references?fields=paperId,title,citationCount,embedding&limit=50
               → finds foundational older work this paper builds on
    forward  = GET /paper/{s2_id}/citations?fields=paperId,title,citationCount,embedding&limit=50
               → finds newer papers that built on this one (especially useful for recency)

  Filtering before fetching (cheap — uses embeddings, no LLM):
    - skip if paper_id in visited_paper_ids (dedup)
    - skip if citationCount < 5 (too obscure)
    - skip if embedding missing (can't score)
    - score = cosine_similarity(candidate.specter_embedding, query_embedding)
    - skip if score < 0.75 (not relevant enough)

  Budget: max 20 new papers per research run from snowballing
  Depth:  1 only — never recurse into citations-of-citations (explosion risk)

  Top candidates by score → each runs through fetch_paper() waterfall (steps 3–8)
  All discovered papers added to visited_paper_ids immediately

STEP 3 — OpenAlex
  Input:  doi (/works/https://doi.org/{doi}) | query (/works?search=)
  Action: GET /works/{doi} or GET /works?search=QUERY&filter=has_abstract:true
  Gets:   metadata, abstract (reconstructed from inverted index), best_oa_location.pdf_url
  On hit: fill any missing metadata fields; if pdf_url present → jump to STEP 6
  On miss or no PDF: continue to STEP 4

STEP 4 — Unpaywall (DOI required)
  Input:  doi only — skip this step if no DOI available
  Action: GET /v2/{doi}?email={UNPAYWALL_EMAIL}
  Gets:   is_oa, oa_status, best_oa_location.url_for_pdf
  On hit with pdf_url: jump to STEP 6
  On hit without pdf_url or miss: continue to STEP 5

STEP 5 — arXiv (preprints)
  Input:  arxiv_id | query with category filter
  Action: GET /api/query?id_list={arxiv_id} or search_query=...
  Gets:   abstract, direct PDF URL (always available for arXiv papers)
  On hit: PDF URL always present → jump to STEP 6
  On miss: continue to STEP 7 (CORE)

STEP 6 — Structured/clean text extraction (prefer parseable formats over PDF)
  Tried in order for each paper — stop at first success:

  6a. arXiv HTML (arXiv papers only)
      URL: https://arxiv.org/html/{arxiv_id}
      Action: httpx GET → BeautifulSoup strip tags → clean text
      Why: arXiv renders most papers to semantic HTML (ar5iv integration).
           Column-aware, equation-aware, no PDF parsing needed.
           Returns clean prose. Fast (~200ms). Always try this before PDF.
      On success: continue to STEP 8
      On 404 or empty: continue to 6b

  6b. CORE fullText field
      Action: GET /search/works?q=doi:{doi}
      Gets:   fullText (plain text body, pre-extracted by CORE)
      Why:    Already clean text — no parsing. No column merging issues.
      On hit with non-empty fullText: continue to STEP 8
      On miss or empty: continue to 6c

  6c. PDF via pymupdf (last resort)
      Input:  pdf_url from steps 2–5
      Action: httpx GET pdf_url → bytes → fitz.open() → extract text per page
      Quality check: if words_per_page < 100 → text is garbage (column merge failure)
                     set abstract_only=True, stop — do NOT attempt further parsing
      On good quality: continue to STEP 8
      On bad quality or fetch failure (403, timeout): abstract_only=True, stop waterfall

STEP 8 — Chunking + Embedding (triggered when full text available)
  Input:  raw full text string
  Action: chunk → embed → store
  See "PDF Processing Pipeline" section below

STEP 9 — Crossref (metadata enrichment only, runs in parallel with steps 2-5)
  Input:  doi
  Action: GET /works/{doi}?mailto={email}
  Gets:   abstract (if publisher provides it), citation count, reference list
  Used:   to fill gaps in metadata only; not a source of full text or PDF URLs
```

## Deduplication

Before any API call, check by DOI:

```python
async def fetch_paper(...) -> PaperResult | None:
    # 1. Normalise identifier
    doi = normalise_doi(doi)  # strip https://doi.org/ prefix, lowercase

    # 2. Check Neon cache
    cached = await db.fetchrow(
        "SELECT * FROM papers WHERE doi = $1 OR arxiv_id = $2",
        doi, arxiv_id
    )
    if cached:
        return PaperResult.from_db(cached)

    # 3. Check Redis dedup set for current research run
    run_key = f"run:{run_id}:seen_dois"
    if doi and await redis.sismember(run_key, doi):
        return None  # already fetched this run, skip

    # 4. Run waterfall...
    result = await _run_waterfall(...)

    # 5. Mark seen
    if doi:
        await redis.sadd(run_key, doi)
        await redis.expire(run_key, 3600)

    # 6. Persist to Neon papers cache
    await db.execute("INSERT INTO papers (...) VALUES (...) ON CONFLICT (doi) DO NOTHING", ...)

    return result
```

## Full Text Processing Pipeline (Step 8 detail)

```
raw_text (string from arXiv HTML | CORE fullText | pymupdf)
    │
    ▼
clean_text()
    - strip headers/footers (page numbers, journal names repeated on each page)
    - normalise whitespace
    - detect and remove reference section (everything after "References\n")
    │
    ▼
chunk_text()
    - RecursiveCharacterTextSplitter
    - chunk_size = 800 tokens
    - chunk_overlap = 100 tokens
    - separators = ["\n\n", "\n", ". ", " "]
    │
    ▼
embed_chunks()
    - model: text-embedding-3-small (OpenAI via LiteLLM, 1536 dims)
    - batch size: 100 chunks per API call
    - store each chunk embedding in Neon paper_chunks table
    │
    ▼
mean_pool_embedding()
    - average all chunk embeddings → single document embedding (1536 dims)
    - store as content_embedding on papers table
    │
    ▼
rank_chunks_by_query(query_embedding)
    - cosine similarity between query embedding and each chunk embedding
    - sort descending
    - take top-K chunks within token budget (default: 8,000 tokens)
    - these ranked chunks go into the synthesis prompt
```

## Full Text Extraction — Format Preference

```python
async def extract_full_text(
    arxiv_id: str | None,
    doi: str | None,
    pdf_url: str | None,
) -> tuple[str | None, str]:  # (text, source)
    """
    Try formats in preference order. Cleanest first, PDF last.
    Returns (text, source) where source is one of:
    "arxiv_html" | "core_fulltext" | "pdf_pymupdf" | None (abstract only)
    """

    # 1. arXiv HTML — cleanest, column-aware, no parsing issues
    if arxiv_id:
        html_url = f"https://arxiv.org/html/{arxiv_id}"
        try:
            resp = await httpx_client.get(html_url, timeout=10)
            if resp.status_code == 200 and len(resp.text) > 1000:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(resp.text, "html.parser")
                # Remove nav, references, footnotes
                for tag in soup.select("nav, .ltx_bibliography, .ltx_note"):
                    tag.decompose()
                text = soup.get_text(separator="\n", strip=True)
                if len(text.split()) > 500:
                    return text, "arxiv_html"
        except Exception:
            pass

    # 2. CORE fullText — pre-extracted plain text, no parsing needed
    if doi:
        core_result = await _query_core(doi)
        if core_result and core_result.get("fullText"):
            text = core_result["fullText"]
            if len(text.split()) > 200:
                return text, "core_fulltext"

    # 3. PDF via pymupdf — last resort
    if pdf_url:
        try:
            resp = await httpx_client.get(pdf_url, timeout=30, follow_redirects=True)
            resp.raise_for_status()
            import fitz
            doc = fitz.open(stream=resp.content, filetype="pdf")
            text = "\n\n".join(page.get_text() for page in doc)
            words_per_page = len(text.split()) / max(len(doc), 1)
            if words_per_page >= 100:
                return text, "pdf_pymupdf"
            # < 100 words/page = column merge failure — don't return garbage
        except Exception:
            pass

    # All formats failed → abstract only
    return None, None
```

**Why no marker-pdf:** It loads an ML model (~500MB) which OOMs Render's 512MB free tier. Two-column PDF quality issues are instead handled by preferring arXiv HTML and CORE plain text, which between them cover the majority of papers she'll encounter. If pymupdf produces garbage on a multi-column PDF, the paper is flagged `abstract_only` rather than returning bad text.

## Rate Limiting (process-global semaphores)

These live on the FastAPI app object — shared across all async tasks and parallel researchers.

```python
# In app/state.py — initialised once at startup
RATE_LIMITERS = {
    "semantic_scholar": asyncio.Semaphore(1),   # 1 req/sec with API key
    "openalex":         asyncio.Semaphore(5),   # generous; polite pool
    "unpaywall":        asyncio.Semaphore(5),   # no hard limit; conservative
    "core":             asyncio.Semaphore(1),   # 10 req/min = ~0.17/sec; conservative
    "arxiv":            asyncio.Semaphore(1),   # 1 req/3sec recommended
    "crossref":         asyncio.Semaphore(5),   # polite pool; generous
}

# Usage in each API wrapper:
async def _call_semantic_scholar(endpoint: str, params: dict):
    async with RATE_LIMITERS["semantic_scholar"]:
        await asyncio.sleep(1.1)  # enforce 1 req/sec
        return await httpx_client.get(endpoint, params=params, headers={"x-api-key": S2_KEY})
```

## API Reference

### Semantic Scholar

**Base URL:** `https://api.semanticscholar.org/graph/v1`

**Auth:** `x-api-key: {key}` header (optional but required for stable rate limits)

**Key calls:**

```python
# Search
GET /paper/search?query={q}&fields=paperId,externalIds,title,abstract,year,authors,
    venue,citationCount,isOpenAccess,openAccessPdf,embedding,tldr,publicationDate

# By DOI
GET /paper/DOI:{doi}?fields=...

# By arXiv ID
GET /paper/ARXIV:{arxiv_id}?fields=...

# Forward citations (who cited this paper)
GET /paper/{s2_id}/citations?fields=paperId,title,year,authors,citationCount&limit=50

# Recommendations
GET /recommendations/v1/papers/forpaper/{s2_id}?fields=...&limit=10
```

**Abstract inverted index:** Not applicable — S2 returns plain text abstract directly.

**Registration:** semanticscholar.org/product/api — email form, 1–3 days.

### OpenAlex

**Base URL:** `https://api.openalex.org`

**Auth:** `?mailto=your@email.com` query param (polite pool, free, no account needed)
Or: `?api_key={key}` (free account at openalex.org/settings/api)

**Key calls:**

```python
# Search
GET /works?search={q}&filter=has_abstract:true&per-page=25&mailto={email}

# By DOI
GET /works/https://doi.org/{doi}

# By journal ISSN
GET /works?filter=primary_location.source.issn:{issn},has_abstract:true&sort=cited_by_count:desc

# Abstract reconstruction (required — OpenAlex does not return plain text)
def reconstruct_abstract(inverted_index: dict) -> str:
    positions = {}
    for word, pos_list in inverted_index.items():
        for pos in pos_list:
            positions[pos] = word
    return " ".join(positions[k] for k in sorted(positions))
```

**Journal ISSNs for primary user's domain:**

```python
PHYSICS_JOURNAL_ISSNS = {
    "Physical Review B":        "2469-9969",
    "Physical Review Letters":  "1079-7114",
    "Physical Review Applied":  "2331-7019",
    "Nature Materials":         "1476-4660",
    "Advanced Materials":       "1521-4095",
    "Journal of Applied Physics": "1089-7550",
    "ACS Nano":                 "1936-086X",
    "Nano Letters":             "1530-6992",
    "npj 2D Materials":         "2397-7132",
}
```

### Unpaywall

**Base URL:** `https://api.unpaywall.org/v2`

**Auth:** `?email=your@email.com` param on every request (no account, no key)

**Key call:**

```python
GET /v2/{doi}?email={UNPAYWALL_EMAIL}

# Response fields used:
{
  "is_oa": bool,
  "oa_status": "gold"|"hybrid"|"bronze"|"green"|"closed",
  "best_oa_location": {
    "url_for_pdf": str | None,      # direct PDF download URL
    "url_for_landing_page": str,
    "version": "publishedVersion"|"acceptedVersion"|"submittedVersion",
    "license": str | None,
    "host_type": "publisher"|"repository"
  }
}
```

**Gotcha:** `bronze` OA can disappear — publisher can revoke. Prefer `green` (repository copy).

### arXiv

**Base URL:** `http://export.arxiv.org/api/query`

**Auth:** None

**Key calls:**

```python
# By arXiv ID
GET ?id_list=2301.07041

# Search (physics categories for primary user)
GET ?search_query=ti:{terms}+AND+cat:cond-mat.mtrl-sci&sortBy=relevance&max_results=25

# Parse response (Atom XML — use feedparser)
import feedparser
feed = feedparser.parse(response_text)
for entry in feed.entries:
    arxiv_id = entry.id.split("/abs/")[-1]    # "2301.07041v2"
    pdf_url = next(l.href for l in entry.links if l.get("title") == "pdf")
    abstract = entry.summary

# PDF URL always: https://arxiv.org/pdf/{arxiv_id}
```

**Physics categories:**

```python
PHYSICS_CATEGORIES = [
    "cond-mat.mtrl-sci",   # condensed matter — materials science
    "cond-mat.supr-con",   # superconductivity
    "physics.app-ph",      # applied physics
    "cond-mat.mes-hall",   # mesoscale / nanoscale physics
]
ML_CATEGORIES = ["cs.LG", "cs.AI", "cs.CL", "stat.ML"]
```

### CORE

**Base URL:** `https://api.core.ac.uk/v3`

**Auth:** `Authorization: Bearer {CORE_API_KEY}` header

**Key calls:**

```python
# Search (returns fullText when available)
GET /search/works?q={query}&limit=10

# By DOI
GET /works/doi:{doi}

# Response fields:
{
  "id": int,
  "doi": str,
  "title": str,
  "abstract": str,
  "fullText": str | None,       # actual paper body — only populated when available
  "downloadUrl": str | None,    # PDF from repository
  "year": int,
  "authors": [{"name": str}],
}
```

### Crossref (metadata enrichment only)

**Base URL:** `https://api.crossref.org`

**Auth:** `?mailto=your@email.com` param (polite pool)

**Key calls:**

```python
# By DOI (primary use — fill gaps in metadata)
GET /works/{doi}?mailto={email}&select=DOI,title,abstract,author,published,
    is-referenced-by-count,container-title,link

# Abstract is HTML-encoded (JATS XML tags) — strip before use:
import re
def strip_jats(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html).strip()
```

## Packages Required

```
httpx[asyncio]          # async HTTP client for all API calls
feedparser              # arXiv Atom XML parsing
pymupdf                 # PDF text extraction (primary)
marker-pdf              # PDF text extraction (fallback, ML-based)
langchain-text-splitters # RecursiveCharacterTextSplitter
openai                  # text-embedding-3-small via LiteLLM
tenacity                # retry with exponential backoff
```

## Environment Variables

```bash
# Semantic Scholar
SEMANTIC_SCHOLAR_API_KEY=...         # request at semanticscholar.org/product/api

# OpenAlex
OPENALEX_EMAIL=your@email.com        # polite pool — just an email, no account needed
OPENALEX_API_KEY=...                 # optional; get at openalex.org/settings/api

# Unpaywall
UNPAYWALL_EMAIL=your@email.com       # required param on every request

# CORE
CORE_API_KEY=...                     # register at core.ac.uk/services/api

# Crossref
CROSSREF_EMAIL=your@email.com        # polite pool param

# Embeddings (via LiteLLM)
OPENAI_API_KEY=...                   # for text-embedding-3-small
```

arXiv and Crossref require no API keys.

## What Gets Stored in Neon

```sql
-- Paper cache: avoid re-fetching same paper across sessions
CREATE TABLE papers (
    paper_id        TEXT PRIMARY KEY,          -- SHA256(doi or arxiv_id)
    doi             TEXT UNIQUE,
    arxiv_id        TEXT UNIQUE,
    s2_id           TEXT,
    title           TEXT NOT NULL,
    authors         TEXT[],
    year            INT,
    venue           TEXT,
    citation_count  INT,
    abstract        TEXT,
    is_open_access  BOOLEAN DEFAULT FALSE,
    pdf_url         TEXT,
    full_text_available BOOLEAN DEFAULT FALSE,
    full_text_source    TEXT,
    content_embedding   vector(1536),
    specter_embedding   vector(768),
    source_apis     TEXT[],
    fetched_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Chunks: stored for RAG retrieval
CREATE TABLE paper_chunks (
    chunk_id        TEXT PRIMARY KEY,           -- SHA256(content)
    paper_id        TEXT REFERENCES papers(paper_id),
    chunk_index     INT,
    text            TEXT NOT NULL,
    token_count     INT,
    embedding       vector(1536),
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX ON papers USING hnsw (content_embedding vector_cosine_ops);
CREATE INDEX ON paper_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ON papers (doi);
CREATE INDEX ON papers (arxiv_id);
```

## Error Handling

| Failure                       | Behaviour                                                   |
| ----------------------------- | ----------------------------------------------------------- |
| S2 returns 429                | tenacity retry, exponential backoff, max 6 attempts         |
| PDF URL returns 403           | skip PDF, mark `abstract_only=True`, continue               |
| PDF corrupt / <100 words/page | fallback to marker; if still bad, mark `abstract_only=True` |
| All steps return nothing      | return `None`; agent notes paper not found                  |
| CORE returns fullText=""      | treat as no full text; try PDF step                         |
| Neon unavailable              | bubble up 503; research run fails cleanly                   |
| Embedding API fails           | store paper without embedding; re-embed on next access      |
