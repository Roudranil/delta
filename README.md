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

### Environment Setup

#### Backend

Delta's backend is built with python. To set up your development environment, use `uv sync` to create a virtual environment from the `pyproject.toml` files and install all packages.

#### Frontend

TBD

## Advanced setup

### Local hosting

TODO