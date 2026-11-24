# `data/pipelines` folder

This folder groups **all data pipelines in the monorepo** related to the company: ingestion, ETL/ELT, cleaning, transformation, and loading into analytical or production systems.

Each subfolder or file under `data/pipelines/` should represent **one pipeline or job set** (for example `sales-etl`, `telemetry-stream`, `customer-segmentation`) and include the required configuration (scripts, orchestration, connectors, schemas, etc.).

- **Main purpose**: consolidate in one place the data movement and transformation logic that powers the company’s applications and analytics.
- **Recommendation**: document pipelines as you add them—their goal, data sources and sinks, dependencies, and how to run them in development, testing, and production.

> _Spanish version: [README.es.md](./README.es.md)._

## Current pipelines

- `rag.py` — **RAG pipeline**: chunk documents → embed (pplx-embed-v1, 1024-dim) → index in Qdrant → retrieve → generate answer (DeepSeek-v4). Used by LangGraph agent for policy Q&A.
- `rfp_intake/` — **RFP Intake Pipeline** (Hito 9, Part 1): PDF upload → MarkItDown conversion → readability metrics → two-stage classifier (regex + DeepSeek-v4) → orchestration → 3 parallel workers (Revenue, Clinical, Compliance) → synthesizer → handoff payload. Async via Celery. 4 REST endpoints. 16 tests. 3 PDFs verified end-to-end.
- `pipeline.py` + `extract.py` + `transform.py` + `load.py` — **ETL pipeline** for monthly reporting (Prefect 3). Extracts from Supabase PostgreSQL, transforms KPI metrics, loads into reporting database.
- `runner.py` + `flows.py` — **Prefect orchestration**: flow definitions and runner for scheduling ETL jobs.
- `models.py` + `database.py` — Shared database models and connection utilities used across pipelines.
- `subflows/` — Reusable Prefect subflow components.
