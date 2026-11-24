# Carpeta `services`

Esta carpeta contiene **todos los servicios backend** (APIs y workers en segundo plano) relacionados con la compañía para el proyecto transversal de AI Engineering.

Cada subcarpeta dentro de `services/` debe corresponder a **un servicio concreto** (por ejemplo `admin-api`, `data-processor-worker`) e incluir su propia documentación técnica y funcional.

- **Propósito principal**: centralizar toda la lógica backend, APIs y consumidores de colas que dan soporte a los casos de uso de la compañía.
- **Recomendación**: documenta en este archivo (o en sub-READMEs) los servicios que vayas añadiendo, su objetivo, tecnología usada y cómo ejecutarlos.

## Servicios actuales

- `api/` — **FastAPI principal (puerto 8000)**. Rutas: auth (JWT login/register/recuperación), proveedores (CRUD + tarifas), inventario (6 endpoints REST + cálculo de stock), RFP intake (subir/listar/detalle/reprocesar), endpoint del agente LangGraph (`POST /agent/query`). SQLModel + Supabase PostgreSQL para inventario; TinyDB para proveedores y auth. Integración Celery vía `celery_app.py` para procesamiento RFP asíncrono.
- `incidents-api/` — **FastAPI de incidencias (puerto 8010)**. API CRUD standalone para incidencias de pacientes. Ingesta de datos CSV legacy. Persistencia TinyDB. Usada por la tool del agente LangGraph.
- `reporting/` — **FastAPI de reporting (puerto 8001)**. Endpoints de KPIs mensuales. Supabase PostgreSQL. Alimentado por pipeline ETL de Prefect.
- `telemetry/` — **Análisis de telemetría + servidor auth standalone**. 5 métricas pandas. Servidor auth standalone evita incompatibilidad SQLModel/Python 3.14. Dashboard consumido por backoffice.
- `agent/` — **Agente LangGraph**. Definición del grafo (13 nodos, 8 routers), tools (incidencias HTTP + MCP), memoria persistente (TinyDB), guardrails (3 capas deterministas regex). Traces en `traces/`.
- `nightly/` — **Job nocturno de exportación**. Proceso en segundo plano para exportaciones programadas.
- `celery_app.py` — **App Celery** con broker Redis. Definiciones de tareas: `process_rfp_pipeline`, `nightly_export`. Backoff exponencial, Dead Letter Queue.
- `job_runner.py` — Máquina de estados para orquestar ejecución de jobs entre servicios.
