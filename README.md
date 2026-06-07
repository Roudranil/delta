# delta

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

Required API keys (and other environment variables) are in [.env.sample](.env.sample)

#### Web Search

Web search is a fundamental resource for a research agent. This project relies on API keys from the below listed services. These are freemium services - with a generous free tier and the cheapest paid tier is reasonable.

| Tool | Free Usage / Month | LangChain Integration |
|------|--------------------|----------------------|
| [Exa](https://exa.ai/) | 1000 requests | `langchain-exa` |
| [Tavily](https://www.tavily.com) | 1000 requests | `langchain-tavily` |
| [SerpAPI](https://serpapi.com/) | 250 searches | `langchain-community` |
| [Jina](https://jina.ai/reader/) | 10M tokens | None |
| [Linkup](https://www.linkup.so) | $20 topup | `langchain-linkup` |
| [Firecrawl](https://www.firecrawl.dev) | 1000 credits | `FireCrawlLoader` (document loader) |


#### Paper Search

TODO

### Environment Setup

#### Backend

Delta's backend is built with python. To set up your development environment, use `uv sync` to create a virtual environment from the `pyproject.toml` files and install all packages.

#### Frontend

TBD

## Advanced setup

### Local hosting

TODO