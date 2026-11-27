# `services` folder

This folder contains **all the backend services** (APIs and background workers) related to the company for the cross-functional AI Engineering project.

Each subfolder inside `services/` must correspond to **one specific service** (for example: `admin-api`, `data-processor-worker`) and include its own technical and functional documentation.

- **Main purpose**: to centralize all the backend logic, APIs, and queue consumers that support the company's use cases.
- **Recommendation**: document in this file (or in sub-READMEs) the services you add, their objective, the technology used, and how to run them.

> _Spanish version: [README.es.md](./README.es.md)._

## Current services in this repository

- `api/` — **Main FastAPI (port 8000)**. Routes: auth (JWT login/register/password recovery), suppliers (CRUD + rates), inventory (6 REST endpoints + stock calculation), RFP intake (upload/list/detail/reprocess), LangGraph agent endpoint (`POST /agent/query`). SQLModel + Supabase PostgreSQL for inventory; TinyDB for suppliers and auth. Celery integration via `celery_app.py` for async RFP processing.
- `incidents-api/` — **Incidents FastAPI (port 8010)**. Standalone CRUD API for patient incidents. CSV legacy data ingestion. TinyDB persistence. Used by LangGraph agent tool.
- `reporting/` — **Reporting FastAPI (port 8001)**. Monthly KPI endpoints. Supabase PostgreSQL. Prefect ETL pipeline feeds the data.
- `telemetry/` — **Telemetry analysis + standalone auth server**. 5 pandas metrics. Standalone auth server avoids SQLModel/Python 3.14 incompatibility. Dashboard consumed by backoffice.
- `agent/` — **LangGraph agent**. Graph definition (13 nodes, 8 routers), tools (incidents HTTP + MCP), persistent memory (TinyDB), guardrails (3-layer deterministic regex). Traces in `traces/`.
- `nightly/` — **Nightly export job**. Background process for scheduled data exports.
- `celery_app.py` — **Celery app** with Redis broker. Task definitions: `process_rfp_pipeline`, `nightly_export`. Exponential backoff, Dead Letter Queue.
- `job_runner.py` — State machine for orchestrating job execution across services.
