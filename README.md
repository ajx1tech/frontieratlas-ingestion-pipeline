# FrontierAtlas — Asynchronous AI & Venture Intelligence Ingestion Pipeline

Production-grade, fault-tolerant ingestion pipeline built to acquire, normalize, deduplicate, and ground AI ecosystem intelligence across **Startups**, **Products**, **Research Papers (with live GitHub stars)**, **24-Hour Fresh Jobs**, **24-Hour Fresh News**, and **Canonical Entity Resolution Logs**.

## Architecture Overview

1. **Async Acquisition Layer (`src/scrapers/`)**:
   - **Bulk Verticals (3,300+ verified records)**: Concurrent `httpx.AsyncClient` ingestion from Y Combinator OSS (`1,100+` startups), Hugging Face Spaces (`1,100+` AI products), and Hugging Face Daily Papers + arXiv Atom API (`1,100+` AI research papers).
   - **Dynamic GitHub Metrics**: Batched GraphQL `stargazerCount` queries (50 repositories per aliased POST request) to attach live star counts without exhausting REST rate limits.
   - **Strict 24-Hour Freshness Crawlers**: Concurrent ingestion across 5 AI news feeds (TechCrunch AI, The Verge AI, Ars Technica AI, MIT Technology Review AI, MarkTechPost) with full-text extraction via `trafilatura`, and 5 job board APIs (Greenhouse, Lever, Himalayas, RemoteOK, Arbeitnow).
2. **Multi-Tier LLM Extraction Engine (`src/llm/`)**:
   - **413 Payload Protection (`chunker.py`)**: Strips HTML boilerplate (`<script>`, `<style>`, `<nav>`, `<footer>`) and applies semantic head+tail truncation (`4,000` chars) to prevent context window overflows.
   - **Resilient Fallback & 429 Handling (`orchestrator.py`)**: Tier 1 (`gemini-2.5-flash`) → Tier 2 (`Groq llama-3.3-70b-versatile`) → Tier 3 (`OpenRouter deepseek-r1:free` / deterministic fallback) with exponential backoff + random jitter and `asyncio.Semaphore` concurrency caps.
3. **Deterministic Entity Resolution (`src/resolution/`)**:
   - 3-stage waterfall (legal suffix normalization → exact lookup against 55+ canonical AI entities → `RapidFuzz` `token_sort_ratio >= 88`) emitting an auditable `entity_mapping_log.csv`.
4. **Zero-Hallucination Grounding Gate (`src/schemas.py`)**:
   - Every record is validated through Pydantic v2 schemas enforcing real `http/https` source URLs, rejecting placeholder domains, and verifying live HTTP reachability.

## Repository Structure

```text
frontieratlas-ingestion-pipeline/
├── README.md
├── architecture.pdf          # Phase VI System Design (3 pages)
├── requirements.txt
├── .env.example
├── data/
│   └── exports/              # Generated CSV outputs for all 6 Google Sheet tabs
├── src/
│   ├── config.py
│   ├── schemas.py
│   ├── pipeline.py
│   ├── llm/
│   │   ├── chunker.py
│   │   └── orchestrator.py
│   ├── resolution/
│   │   ├── seed_entities.py
│   │   └── resolver.py
│   ├── freshness/
│   │   └── date_engine.py
│   └── scrapers/
│       ├── bulk_startups.py
│       ├── bulk_products.py
│       ├── bulk_papers.py
│       ├── fresh_news.py
│       ├── fresh_jobs.py
│       └── antibot_demo.py
└── tests/
    └── test_core.py