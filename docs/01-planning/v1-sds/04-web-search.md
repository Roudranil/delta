# SDS-04: Web Search

Web search is a supplement to paper search, not a replacement. The agent uses it when paper APIs alone can't answer the question — for context, background, or discovering papers via web URLs.

## Selected Stack

**Primary:** Exa
**Fallback:** Tavily
**Not used:** Brave (no date filtering, no academic index, low AI rate limit)

## When Web Search Is Used

The agent does NOT use web search by default on every query. It's triggered in specific situations:

| Situation                                            | Why web search                                                             |
| ---------------------------------------------------- | -------------------------------------------------------------------------- |
| Query needs background context, not just papers      | "what is ALD?" — better answered by a clear explainer than a primary paper |
| Paper APIs return < 3 results                         | Topic may be too new or niche for Semantic Scholar's index                 |
| User explicitly asks for recent news or developments | Paper APIs lag by months; web catches preprints, lab announcements         |
| A web URL is found that might be a paper             | Extract DOI → hand off to paper waterfall                                  |
| Query is about equipment, software, or techniques    | Not in paper form — vendor docs, forum answers, tutorials                  |

## Exa (Primary)

**Base URL:** `https://api.exa.ai`

**Auth:** `x-api-key: {key}` header

**Why Exa over Tavily for research:**

- Neural/semantic search — finds conceptually related content, not just keyword matches
- `category: "research paper"` — dedicated index of 100M+ academic papers
- `publishedDate` returned in results — Tavily does not return this
- `startPublishedDate` / `endPublishedDate` — ISO 8601 date range filtering
- `highlights` field — token-efficient relevant snippets instead of full page text; keeps LLM costs down
- `includeDomains` supports up to 1,200 domains — can whitelist the entire academic web

### Key Endpoint: POST /search

```python
POST https://api.exa.ai/search
Headers: x-api-key: {EXA_API_KEY}

{
  "query": "MoS2 thin film deposition atomic layer deposition",
  "type": "auto",              # "instant" | "fast" | "auto" | "deep-lite" | "deep"
  "numResults": 10,
  "category": "research paper", # dedicated academic index
  "startPublishedDate": "2020-01-01T00:00:00Z",  # ISO 8601
  "endPublishedDate": "2025-01-01T00:00:00Z",
  "includeDomains": [          # optional whitelist
    "arxiv.org",
    "nature.com",
    "aps.org",
    "wiley.com",
    "sciencedirect.com"
  ],
  "contents": {
    "highlights": {            # token-efficient snippets — prefer over full text
      "numSentences": 3,
      "highlightsPerUrl": 2
    }
  }
}
```

### Response Fields (per result)

```python
{
  "id": "...",
  "url": "https://...",
  "title": "...",
  "publishedDate": "2023-04-15",   # returned — unlike Tavily
  "author": "...",
  "score": 0.87,                   # relevance score
  "highlights": ["...snippet..."], # token-efficient relevant text
  "text": "..."                    # full page text (only if requested)
}
```

### Key Endpoint: POST /contents

Fetch full text or highlights from a list of URLs after search:

```python
POST https://api.exa.ai/contents
{
  "ids": ["exa_id_1", "exa_id_2"],   # from search results
  "text": {"maxCharacters": 3000},    # or
  "highlights": {"numSentences": 5}   # prefer highlights for token budget
}
```

### Pricing

| Usage                   | Cost                                  |
| ----------------------- | ------------------------------------- |
| Free tier               | 1,000 requests/month                  |
| Search (≤10 results)    | $7 per 1,000 requests                 |
| Contents                | $1 per 1,000 pages                    |
| Deep Search             | $12–15 per 1,000 requests             |
| Startup/education grant | $1,000 free credits (apply at exa.ai) |

**At 2 users:** 1,000 free requests/month is enough for MVP. Apply for the education grant — $1K covers months of usage.

## Tavily (Fallback)

Used when Exa fails, is rate-limited, or for general web queries where semantic search isn't needed.

**Base URL:** `https://api.tavily.com`

**Auth:** `Authorization: Bearer tvly-{key}`

### Key Endpoint: POST /search

```python
POST https://api.tavily.com/search
{
  "query": "MoS2 thin film deposition",
  "search_depth": "basic",      # "basic" (1 credit) | "advanced" (2 credits)
  "max_results": 5,
  "time_range": "year",         # "day" | "week" | "month" | "year"
  "include_domains": ["arxiv.org", "nature.com"],
  "include_raw_content": False  # True = full page text, but often blocked
}
```

### Response Fields (per result)

```python
{
  "url": "...",
  "title": "...",
  "content": "...",   # snippet/summary — no publishedDate
  "score": 0.91
}
```

**Gotcha:** Tavily does not return `publishedDate`. For research queries where recency matters, this is a significant gap — you can't tell if a result is from 2019 or 2024.

### Pricing

| Plan          | Credits/month |
| ------------- | ------------- |
| Free          | 1,000 credits |
| Pay-as-you-go | $0.008/credit |

## The DOI Extraction Bridge

When web search returns a URL that looks like a paper, extract the DOI or arXiv ID and hand off to the paper waterfall. This is how web search and paper search are linked.

```python
import re

DOI_PATTERNS = [
    r"doi\.org/(10\.\d{4,}/[^\s\"'>]+)",        # https://doi.org/10.xxxx/...
    r"dx\.doi\.org/(10\.\d{4,}/[^\s\"'>]+)",
]

ARXIV_PATTERNS = [
    r"arxiv\.org/abs/(\d{4}\.\d{4,})",          # arxiv.org/abs/2301.12345
    r"arxiv\.org/pdf/(\d{4}\.\d{4,})",
]

PUBLISHER_DOI_PATTERNS = {
    # nature.com/articles/s41586-023-XXXXX → can construct DOI
    r"nature\.com/articles/(s\d+-\d+-\d+-\d+)": lambda m: f"10.1038/{m.group(1)}",
    # aps.org/doi/10.xxxx → extract directly
    r"aps\.org/doi/(10\.\d{4,}/[^\s\"'>]+)": lambda m: m.group(1),
}

def extract_paper_id_from_url(url: str) -> dict | None:
    for pattern in DOI_PATTERNS:
        m = re.search(pattern, url)
        if m:
            return {"type": "doi", "value": m.group(1)}

    for pattern in ARXIV_PATTERNS:
        m = re.search(pattern, url)
        if m:
            return {"type": "arxiv", "value": m.group(1)}

    return None
```

**Flow:**

```
web_search(query) → list[WebResult]
  for each result:
    paper_ref = extract_paper_id_from_url(result.url)
    if paper_ref:
      paper = fetch_paper(doi=paper_ref["value"])  # full paper waterfall
      yield paper  # as a PaperResult, not a WebResult
    else:
      yield result  # as a WebResult, used for context only
```

## Output Types

Web search returns two different things depending on whether a paper was found:

```python
@dataclass
class WebResult:
    url: str
    title: str
    snippet: str
    published_date: str | None   # None if Tavily; present if Exa
    source: str                  # "exa" | "tavily"

# If DOI extracted → handed to paper waterfall → returns PaperResult (see SDS-03)
# If no DOI → stays as WebResult, used for context in synthesis prompt
```

The LLM receives both `PaperResult` objects (from paper waterfall) and `WebResult` objects (from web search) in its context. Both are cited in output, but differently:
- Papers → numbered citations with full metadata: `[1] Smith et al., 2023 — Nature Materials`
- Web sources → separate numbered series: `[W1] Page Title — https://... (2023)`

The citation invariant applies to web results too: the LLM references web results by `web_id` only. Code resolves `web_id` → full URL + title. The LLM cannot fabricate a web citation any more than it can fabricate a paper citation.

## What the Agent Tool Looks Like

```python
# One tool, two modes
async def web_search(
    query: str,
    mode: Literal["research", "general"] = "research",
    date_from: str | None = None,   # YYYY-MM-DD
    date_to: str | None = None,
    domains: list[str] | None = None,
) -> list[WebResult | PaperResult]:
    ...
```

- `mode="research"` → uses Exa with `category="research paper"`, semantic search
- `mode="general"` → uses Tavily, good for background context, documentation, tutorials
- Any result with an extractable DOI/arXiv ID → automatically handed to paper waterfall

## Rate Limiting

```python
EXA_SEMAPHORE = asyncio.Semaphore(5)    # undocumented limit; conservative
TAVILY_SEMAPHORE = asyncio.Semaphore(5)
```

## Environment Variables

```bash
# Exa
EXA_API_KEY=...

# Tavily
TAVILY_API_KEY=tvly-...
```

## Packages

```
exa-py          # official Exa Python client
tavily-python   # official Tavily Python client
```

Both have async clients. Use `AsyncExa` and `AsyncTavilyClient`.

## What Web Search Is NOT Used For

- Replacing paper search — Semantic Scholar and OpenAlex are always tried first
- Fetching full paper text — that's the paper waterfall's job
- Being the primary citation source — papers always preferred; web citations are supplementary
