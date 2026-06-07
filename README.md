# delta

- [delta](#delta)
  - [Getting started](#getting-started)
    - [Prerequisites](#prerequisites)
    - [API Keys](#api-keys)
  - [Backend](#backend)
    - [Hosting Infrastructure](#hosting-infrastructure)
      - [Cloud](#cloud)
      - [Local](#local)
    - [Web Search](#web-search)
    - [Paper Search](#paper-search)
      - [PMC Resources](#pmc-resources)
  - [Environment Setup](#environment-setup)
    - [Backend](#backend-1)
    - [Frontend](#frontend)


Delta is an opinionated academic research agent (and harness) built to assist a researcher.

## Getting started

### Prerequisites

| Tool                             | Version | Required for                                     |
| -------------------------------- | ------- | ------------------------------------------------ |
| Python                           | >= 3.13 | Backend (FastAPI, LangGraph)                     |
| [uv](https://docs.astral.sh/uv/) | latest  | Python package management (recommended over pip) |
| Docker + Docker Compose          | latest  | Local Postgres (pgvector), Redis, MinIO          |
| TBD                              | TBD     | Frontend                                         |

### API Keys

You will need API keys for the following requirements:

- LLM inference endpoints
- Embedding model inference endpoints (can be the same as the ones for LLM inference, depending on your provider)
- Exa, Tavily, SerpAPI for web search
- Semantic Scholar, OpenAlex for paper search
- Remote hosting resources

Required API keys (and other environment variables) are in [.env.sample](.env.sample).

For more information on the exact services required and the auth mechanisms please see below.

## Backend

### Hosting Infrastructure

#### Cloud

Delta is remotely hosted on serverless cloud services. Below is the list of services used. All of them are freemium services. All of them require sign up with a email id and need an API key for authentication. Cloudflare requires a credit card with international transactions enabled to activate R2. 

| Service | Free Tier | Purpose |
|----------|----------|----------|
| [Neon](https://console.neon.tech) · [[Docs](https://neon.com/docs/introduction)] | 100 CU-hours/month, 0.5 GB storage, 5 GB egress | PostgreSQL database, pgvector embeddings, LangGraph checkpoints |
| [Upstash Redis](https://console.upstash.com/redis) · [[Docs](https://upstash.com/docs/redis/introduction)] | 500K commands/month, 256 MB storage | Session locks, caching, paper deduplication, ephemeral state |
| [Cloudflare R2](https://dash.cloudflare.com/) · [[Docs](https://developers.cloudflare.com/r2/)] | 10 GB storage, 1M Class A ops/month, 10M Class B ops/month, free egress | PDFs, extracted text, uploads, artifacts, session archives |
| Render | TODO | web server hosting |
| Cloudflare Pages | TODO | website hosting |

#### Local

Delta can also be remotely hosted using docker containers.

TODO

### Web Search

Web search is a fundamental resource for a research agent. This project relies on API keys from the below listed services. These are freemium services - with a generous free tier and the cheapest paid tier is reasonable.

| Tool | Free Usage / Month | LangChain Integration |
|------|--------------------|----------------------|
| [Exa](https://exa.ai/) | 1000 requests | `langchain-exa` |
| [Tavily](https://www.tavily.com) | 1000 requests | `langchain-tavily` |
| [SerpAPI](https://serpapi.com/) | 250 searches | `langchain-community` |
| [Jina](https://jina.ai/reader/) | 10M tokens | None |
| [Linkup](https://www.linkup.so) | $20 topup | `langchain-linkup` |
| [Firecrawl](https://www.firecrawl.dev) | 1000 credits | `FireCrawlLoader` (document loader) |


### Paper Search

Delta resolves research papers from the below sources. Semantic Scholar is a notable missing entry as acquiring an API key is difficult for a solo dev unaffiliated with any enterprise org or academic institution. If in the future we acquire a semantic scholar API key, it will become a part of the below table.

| Service | Access / Setup | Python Package | Primary Usage |
|----------|----------|----------|----------|
| [CORE](https://api.core.ac.uk/docs/v3) | API Key | None (REST API via `httpx`) | Full-text retrieval, repository PDFs, extracted text |
| [arXiv](https://info.arxiv.org/help/api/basics.html#python) | No authentication required | [`arxiv`](https://github.com/lukasschwab/arxiv.py) | Full-text HTML, PDFs, preprints, paper search |
| [Unpaywall](https://unpaywall.org/products/api) | Email parameter only | None (REST API via `httpx`) | DOI → legal open-access copy discovery |
| [Crossref](https://www.crossref.org/documentation/retrieve-metadata/rest-api/) | No API key (include email/User-Agent) | [`habanero`](https://github.com/sckott/habanero) | DOI normalization, metadata enrichment |
| [OpenCitations](https://api.opencitations.net/index/v2) | Optional API key | None (REST API via `httpx`) | Citation graph enrichment |
| [OpenAlex](https://docs.openalex.org/api-reference/introduction) | Optional API key (recommended) | [`pyalex`](https://github.com/J535D165/pyalex) | Primary paper search, metadata, references, citations |
| [PMC](https://pmc.ncbi.nlm.nih.gov/tools/developers/) | Optional NCBI API key | [`pyeuropepmc`](https://github.com/JonasHeinickeBio/pyeuropepmc) | Open-access full text, PDFs, structured XML/JSON, citation data |

#### PMC Resources

PMC is slightly more complicated than the other sources in the sense that it is 3 API services and not 1. Below there is a link to the services.

| Resource | Documentation | Purpose |
|----------|----------|----------|
| [PMC ID Converter API](https://pmc.ncbi.nlm.nih.gov/tools/id-converter-api/) | DOI ↔ PMCID ↔ PMID conversion | Resolve identifiers and determine whether a paper exists in PMC |
| [PMC BioC API](https://www.ncbi.nlm.nih.gov/research/bionlp/APIs/BioC-PMC/) | Structured XML/JSON full text | Retrieve machine-readable article content instead of parsing PDFs |
| [PMC Open Access API](https://pmc.ncbi.nlm.nih.gov/tools/oa-service/) | OA metadata and file locations | Discover licenses, availability, and downloadable article packages |

## Environment Setup

### Backend

Delta's backend is built with python. To set up your development environment, use `uv sync` to create a virtual environment from the `pyproject.toml` files and install all packages.

### Frontend

TODO