# Carpeta `data/pipelines`

Esta carpeta agrupa **todos los pipelines de datos del monorepo** relacionados con la compañía: procesos de ingesta, ETL/ELT, limpieza, transformación y carga hacia sistemas analíticos o de producción.

Cada subcarpeta o archivo dentro de `data/pipelines/` debe representar **un pipeline o conjunto de jobs** (por ejemplo `sales-etl`, `telemetry-stream`, `customer-segmentation`) e incluir la configuración necesaria (scripts, orquestación, conectores, esquemas, etc.).

- **Propósito principal**: unificar en un único lugar la lógica de movimiento y transformación de datos que soporta las aplicaciones y analíticas de la compañía.
- **Recomendación**: documenta aquí los pipelines que vayas añadiendo, describiendo su objetivo, orígenes/destinos de datos, dependencias y cómo ejecutarlos en desarrollo, pruebas y producción.

## Pipelines actuales

- `rag.py` — **Pipeline RAG**: fragmentar documentos → embed (pplx-embed-v1, 1024-dim) → indexar en Qdrant → recuperar → generar respuesta (DeepSeek-v4). Usado por el agente LangGraph para Q&A sobre políticas.
- `rfp_intake/` — **Pipeline de Recepción RFP** (Hito 9, Parte 1): carga PDF → conversión MarkItDown → métricas de legibilidad → clasificador de dos etapas (regex + DeepSeek-v4) → orquestación → 3 workers paralelos (Revenue, Clinical, Compliance) → sintetizador → payload de handoff. Async vía Celery. 4 endpoints REST. 16 tests. 3 PDFs verificados end-to-end.
- `pipeline.py` + `extract.py` + `transform.py` + `load.py` — **Pipeline ETL** para reporting mensual (Prefect 3). Extrae de Supabase PostgreSQL, transforma métricas KPI, carga en base de reporting.
- `runner.py` + `flows.py` — **Orquestación Prefect**: definiciones de flujos y ejecutor para programar jobs ETL.
- `models.py` + `database.py` — Modelos de base de datos compartidos y utilidades de conexión usados entre pipelines.
- `subflows/` — Componentes reutilizables de subflujos Prefect.
