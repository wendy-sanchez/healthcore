# HealthCore — Proyecto de Ingeniería IA

![AI Engineering](https://img.shields.io/badge/track-AI%20Engineering-green)
[![LangGraph](https://img.shields.io/badge/LangGraph-1.2.22-purple)](https://langchain-ai.github.io/langgraph/)
[![Python](https://img.shields.io/badge/Python-3.14-yellow)](https://python.org)

> _English instructions in [README.md](./README.md)._

**HealthCore** es una red de atención ambulatoria que opera 12 clínicas en Estados Unidos y Reino Unido. Este repositorio implementa los sistemas digitales, herramientas y agentes de IA construidos por su unidad interna **HealthCore Digital** para modernizar las operaciones, mejorar la experiencia del paciente y habilitar decisiones basadas en datos.

📄 La descripción completa de la empresa está en [`CONTEXT.md`](./CONTEXT.md).

---

## 📦 Entregables

| # | Entregable | Stack | Estado |
|---|------------|-------|--------|
| 1 | **Sitio Web Corporativo** — Landing page bilingüe (EN/ES) con marcado Schema.org y formulario de consulta | HTML, Tailwind CSS, vanilla JS | ✅ Completado |
| 2 | **Talent Pipeline Tracker** — Gestión de candidatos con filtros, vista detalle, actualización de estado y notas | Next.js 19, React 19, TypeScript | ✅ Completado |
| 3 | **Directorio de Proveedores** — Registro centralizado con tarifas, cambios de estado y baja con auditoría | FastAPI, Pydantic v2, TinyDB | ✅ Completado |
| 4 | **Autenticación y Usuarios** — Login JWT, registro, recuperación de contraseña completa, roles USER/ADMIN | FastAPI, python-jose, bcrypt, Resend | ✅ Completado |
| 5 | **Sistema de Incidencias** — API independiente para incidencias de pacientes con análisis CSV legacy y frontend | FastAPI (puerto 8010), TinyDB, Next.js | ✅ Completado |
| 6 | **Inventario de Suministros Médicos** — 6 endpoints REST, stock calculado como neto de entradas menos salidas, 4 páginas frontend | FastAPI, SQLModel, Supabase PostgreSQL | ✅ Completado |
| 7 | **Cola de Tareas Asíncronas** — Redis + Celery con backoff exponencial, Dead Letter Queue en TinyDB, monitorización Flower | Redis 7, Celery, Docker Compose | ✅ Completado |
| 8 | **Pipeline de Telemetría y Dashboard** — 5 métricas pandas, servidor auth standalone, dashboard de 5 paneles | FastAPI, pandas, Supabase, Next.js | ✅ Completado |
| 9 | **Reporting Mensual** — Pipeline ETL (Prefect 3), API de KPIs (puerto 8001), dashboard de rendimiento por clínica | Prefect, FastAPI, Supabase PostgreSQL | ✅ Completado |
| 10 | **Modelo de Pronóstico de Ventas** — Regresión XGBoost con split temporal estricto 8/2, 12 features causales, auditoría anti-fuga, evaluación sesgo/varianza | XGBoost, scikit-learn, pandas, matplotlib | ✅ Completado |
| 11 | **Base de Conocimiento RAG** — Generación Aumentada por Recuperación sobre documentos de políticas de HealthCore | Qdrant, pplx-embed-v1, DeepSeek-v4, FastAPI | ✅ Completado |
| 12 | **Agente de Soporte LangGraph** — Grafo de decisión que envuelve el pipeline RAG con estado explícito, checkpointing, trazabilidad y endpoint HTTP | LangGraph 1.2.22, MemorySaver, httpx | ✅ Completado |
| 13 | **Agente LangGraph — Tools Externas** — Añadidas herramientas de consulta en tiempo real a incidencias e inventario. Enrutamiento automático entre RAG, tools o ambos mediante clasificación regex (sin LLM). Timeout 5s, fallback honesto. 32 tests. | LangGraph, httpx, regex | ✅ Completado |
| 14 | **Servidor MCP — Tools Protegidas con OAuth** — Servidor Model Context Protocol que expone Incidencias (5 tools) e Inventario (5 tools) como herramientas MCP, protegidas con OAuth 2.1 Resource Server vía mcpauth. Operaciones de escritura en inventario explícitamente rechazadas (RETO 2). Agente LangGraph integrado vía langchain-mcp-adapters. 9/9 criterios de aceptación. | FastMCP, mcpauth, langchain-mcp-adapters, Streamable HTTP | ✅ Completado |
| 15 | **Agente LangGraph — Memoria Persistente + Guardrails** — El agente aprende de la interacción sin acumular basura. Memoria TinyDB (separada de RAG), validación PHI dual (8 patrones HIPAA+UK GDPR), aprobación explícita del usuario requerida (nunca asumida), clasificador léxico (sin LLM), máximo una propuesta pendiente, trazabilidad completa (8 eventos), consolidación automática (threshold 50 facts, TTL 90 días). **Además**: 3 capas de guardrails deterministas (regex, sin LLM): hardening de system prompt (5 reglas), clasificación de entrada (6 categorías, ~100 patrones), validación de salida (PHI/filtración instrucciones/formato). Aislamiento de contenido RAG con marcadores. Integración en grafo con 2 nodos + observabilidad. 62 tests de guardrail + 94 tests agente/grafo = 286 total. 11/11 criterios de aceptación. | TinyDB, LangGraph, regex, Python 3.14 | ✅ Completado |
| 16 | **Pipeline de Recepción RFP** — Ingesta automatizada de RFPs desde carga de PDF. Conversión con MarkItDown, métricas de legibilidad (py-readability-metrics), clasificador de dos etapas (pre-filtro regex + DeepSeek-v4 LLM), orquestación multi-agente (orquestador→3 workers paralelos→sintetizador), análisis por departamento (Revenue, Clinical, Compliance), payload de handoff para Parte 2. Upload asíncrono con Celery, respuesta 202 con polling. 4 endpoints REST, 16 tests. 3 PDFs reales verificados end-to-end. | FastAPI, Celery, SQLModel/PostgreSQL, DeepSeek-v4 | ✅ Completado |

---

## 🏗️ Arquitectura General

```text
uis/                        # Interfaces de usuario
├── website/                #   Sitio público (Next.js) + página Q&A RAG
├── backoffice/             #   Panel admin (Next.js): proveedores, inventario, incidencias, telemetría, reporting
└── talent-pipeline-tracker/#   Seguimiento de candidatos (Next.js)

services/                   # Servicios backend
├── api/                    #   FastAPI principal (puerto 8000) — auth, proveedores, inventario, endpoint agente
├── incidents-api/          #   FastAPI incidencias (puerto 8010) — standalone
├── reporting/              #   FastAPI reporting (puerto 8001) — KPIs mensuales
├── telemetry/              #   Análisis de telemetría + servidor auth standalone
├── agent/                  #   Agente LangGraph — grafo, tools, memoria, guardrails
├── celery_app.py           #   App Celery — definiciones de tareas asíncronas
├── nightly/                #   Job nocturno de exportación — proceso en segundo plano
└── job_runner.py           #   Máquina de estados para orquestación de trabajos

mcps/                       # Servidor MCP (puerto 9100) — FastMCP + OAuth 2.1
                            #   10 tools (incidencias + inventario) protegidas vía mcpauth

data/                       # Capa de datos
├── raw/                    #   Datos crudos (CSV)
├── process/                #   Datos procesados, bases TinyDB
├── pipelines/              #   Pipeline RAG, pipeline ETL
├── eval/                   #   Consultas de prueba, informes de evaluación
└── migrations/             #   Migraciones SQL

agents/                     # Plantillas de agentes y documentación de herramientas
docs/                       # Arquitectura, diseño RAG, plan de telemetría
scripts/                    # Herramientas CLI: pronóstico ventas, evaluación modelo, exportación nocturna, seed
tests/                      # Suites de tests: pipelines, nightly, agente, RAG, evaluación
```

**Arquitectura backend:** La mayoría de las APIs usan TinyDB para auth y datos operativos. Inventario y reporting usan Supabase PostgreSQL. Un servidor auth standalone evita la incompatibilidad SQLModel/Python 3.14. El servidor MCP (`mcps/`) expone herramientas de incidencias e inventario mediante Model Context Protocol con protección OAuth 2.1.

**Docker:** Los servicios se comunican mediante Docker Compose bajo la red `healthcore-net`. Ver [`docker-compose.yml`](./docker-compose.yml).

---

## 🔧 Inicio Rápido

```bash
# Requisitos: Python 3.14, Node 22, uv 0.13+, Docker (opcional)
uv sync                      # Instalar dependencias Python
cd uis/backoffice && npm install  # Instalar dependencias frontend
cd uis/website && npm install
```

### Ejecutar APIs

```bash
# API principal (puerto 8000) — auth, proveedores, inventario
uv run uvicorn services.api.main:app --reload --port 8000

# API de incidencias (puerto 8010)
uv run uvicorn services.incidents-api.main:app --reload --port 8010

# API de reporting (puerto 8001)
uv run uvicorn services.reporting.main:app --reload --port 8001

# Servidor telemetría standalone (puerto 8000)
uv run python services/telemetry/server_standalone.py

# Servidor RAG standalone (puerto 8000 — evita SQLModel)
uv run python services/api/routes/rag_standalone.py

# Servidor MCP (puerto 9100) — tools protegidas con OAuth y token dev
uv run uvicorn mcps.server:app --host 0.0.0.0 --port 9100
# El servidor MCP arranca con autenticación ACTIVADA por defecto.
# Usa el token dev: hc_dev_token_no_usar_en_produccion
# Para conectar vía MCP Playground o CLI: añade el header Authorization con este token.
```

### Ejecutar Frontends

```bash
cd uis/backoffice && npm run dev      # Backoffice (puerto 3001)
cd uis/website && npm run dev          # Sitio web (puerto 3000)
cd uis/talent-pipeline-tracker && npm run dev  # Tracker (puerto 3000 por defecto)
```

### Ejecutar Tests

```bash
# Todos los tests de pipelines (agente, RAG, pronóstico ventas, CV temporal)
uv run python -m pytest tests/pipelines/ -v

# Tests del agente (32 tests)
uv run python -m pytest tests/pipelines/test_agent_graph.py -v

# Tests de RAG (26 tests)
uv run python -m pytest tests/pipelines/test_rag.py -v

# Tests de pronóstico de ventas (19 tests)
uv run python -m pytest tests/pipelines/test_sales_forecast_split.py -v

# Tests de autenticación del servidor MCP (10 tests)
uv run pytest tests/test_mcp_server_auth.py -v

# Tests del job nocturno (30 tests)
.venv-nightly/bin/python -m pytest tests/nightly -q
```

### Ejecutar Stack Docker

```bash
docker compose up --build    # Construye e inicia todos los servicios
```

---

## 🧠 Agente LangGraph — Detalles Clave

El agente de soporte está implementado como un `StateGraph` de LangGraph con **13 nodos** y **8 enrutadores condicionales**. Reutiliza las funciones RAG existentes (`retrieve`, `generate_answer`) sin reescribirlas.

**Flujo principal:** `receive_question` → `load_memory_context` → (process_proposal / `classify_query`) → (tool y/o RAG) → `synthesize_tool_answer` / `generate_answer` → `auto_evaluate_memory` → END

- **Clasificación:** Basada en regex (sin LLM). Decide entre RAG, tool de incidencias, tool de inventario, o una combinación.
- **Tools:** Solo lectura (GET). Consultan los servicios reales de incidencias e inventario. Timeout 5s. Todos los errores producen respuestas de fallback honestas.
- **Memoria Persistente (Parte 1):** El agente puede recordar hechos operativos entre conversaciones (preferencias de personal, patrones de incidencias, configuraciones operativas) con control explícito del usuario. Usa TinyDB (separado de RAG), validación PHI dual, clasificador de intención léxico (sin LLM), y consolida/expira hechos viejos automáticamente. 113 tests de agente+memoria en test_agent_graph.py.
- **Harness y Guardrails (Parte 2):** Sistema de seguridad determinista de 3 capas (regex, sin LLM) documentado en [`agent-memory-harness-guard.md`](./agent-memory-harness-guard.md):
  - **Harness** — Generación segura del system prompt (`build_system_prompt`) con identidad, dominio, 5 reglas de comportamiento estrictas, y aislamiento del contexto RAG (`wrap_rag_context` mediante marcadores `--- BEGIN/END REFERENCE DOCUMENTS ---`).
  - **Content Guardrail** — Clasificación del alcance de entrada (`classify_input_scope`) en 6 categorías (domain, small_talk, personal_task, jailbreak, phi_in_query, gradual_extraction) usando ~100 patrones regex. Sin LLM.
  - **Output Guardrail** — Validación de respuesta (`validate_output`) verificando filtraciones PHI, filtración de instrucciones, términos prohibidos y formato. 62 tests deterministas (con parametrización).
- **Trazabilidad:** Cada ejecución produce un trace JSON persistido en `services/agent/traces/{run_id}.json`.
- **Endpoint:** `POST /agent/query` (capa FastAPI delgada — sin lógica de negocio).
- **Documentación:** [`agente-soporte-langgraph.md`](./agente-soporte-langgraph.md), [`agent-memory-harness-guard.md`](./agent-memory-harness-guard.md).

---

## 🔧 Servidor MCP — Tools Protegidas con OAuth — Detalles Clave

Servidor Model Context Protocol que expone herramientas internas como recursos MCP, protegidas con **OAuth 2.1 Resource Server** vía **mcpauth**. Diseñado para que el agente LangGraph las consuma mediante `langchain-mcp-adapters`.

- **10 tools:** Incidencias (5 — CRUD + resumen) e Inventario (5 — 2 lectura + 3 rechazo explícito de escritura).
- **OAuth enforcement:** Wrapper `SelectiveBearerASGI` — rutas públicas (`/.well-known/`, `/health`) sin auth; `/mcp` requiere Bearer JWT.
- **Autenticación activada por defecto:** Modo dev usa token `hc_dev_token_no_usar_en_produccion` con todos los scopes. Producción usa JWKS (`MCP_JWKS_URI`).
- **Scopes:** Mínimo privilegio — `incidents.read`, `incidents.write`, `inventory.read`, `inventory.write`. Cada tool verifica su scope requerido.
- **Logging:** Todas las invocaciones de tools registradas con subject del cliente (`client=<sub>`), nombre de la tool, estado (éxito/error) y detalles.
- **Rechazo de escritura (RETO 2):** `create_product`, `update_product`, `delete_product` existen y son descubribles pero devuelven `{"error": "read_only", "success": false, "code": "INVENTORY_READ_ONLY"}`.
- **Integración LangGraph:** Agente migrado de HTTP directo a MCP Client vía `langchain-mcp-adapters` (`load_mcp_tools()`). Nodos son `async def` con `await tool.ainvoke()`.
- **Transporte:** Streamable HTTP (`stateless_http=True`). Protección anti-DNS rebinding deshabilitada para compatibilidad con túneles.
- **Documentación:** [`mcp-server.md`](./mcp-server.md), [`mcps/README.es.md`](./mcps/README.es.md).

---

## 📊 Modelo de Pronóstico de Ventas — Detalles Clave

Regresión XGBoost entrenada con 84 meses (2016–2023), probada en 24 meses no vistos (2024–2025). Sin fuga de datos. Validación walk-forward.

- **Métricas (test):** RMSE $154.9K (5.5% del ingreso medio), MAPE 3.5%, Gini 0.82, K²/R² 0.81
- **Banda de variabilidad:** ±$265K (cobertura empírica 87.5%)
- **Diagnóstico sesgo/varianza:** Overfitting confirmado — brecha CV 28.7×. Acciones correctivas documentadas.
- **Documentación:** [`predic-ventas-model-regresion.md`](./predic-ventas-model-regresion.md)

---

## 📚 Base de Conocimiento RAG — Detalles Clave

Generación Aumentada por Recuperación sobre 4 documentos de políticas de HealthCore. Embeddings con `pplx-embed-v1` (1024-dim) en Qdrant. Genera respuestas con `deepseek-v4-flash`.

- **12 fragmentos** (3 por documento), 450 chars máx, 140 chars mín
- **Recall@3:** 100% (8/8 consultas de prueba)
- **Frontend:** Página de chat Q&A en el sitio web público (`uis/website/app/rag/`)
- **Documentación:** [`docs/rag/rag-design.es.md`](./docs/rag/rag-design.es.md) (ES) / [`rag-design.md`](./docs/rag/rag-design.md) (EN)

---

## 🗃️ Pipeline de Recepción RFP — Detalles Clave

Pipeline automatizado para la ingesta de RFPs (Solicitudes de Propuesta) desde carga de PDF. Construido para el **Hito 9, Parte 1** (Flujos de Trabajo Agénticos — recepción/ingesta).

**Flujo:** `Carga PDF → Conversión MarkItDown → Métricas de legibilidad → Clasificador de dos etapas (pre-filtro regex + DeepSeek-v4 LLM) → Orquestador → 3 workers paralelos (Revenue, Clinical, Compliance) → Sintetizador → Payload de handoff → Persistencia PostgreSQL`

**Características principales:**
- **Arquitectura asíncrona:** `POST /rfp/upload` responde `202 Accepted` con UUID de ticket. Tarea Celery en segundo plano ejecuta el pipeline completo. El frontend consulta `GET /rfp/tickets/{ticket_id}` para conocer el estado.
- **Clasificador:** Diseño de dos etapas. Primero, patrones regex detectan documentos no-RFP (pitch, CV, newsletter — descarte temprano). Segundo, DeepSeek-v4 LLM con salida JSON estructurada (is_rfp, confidence, reason, client_name, program_type, deadline). Hard gate: no-RFP → `descartado` con motivo.
- **Orquestación multi-agente:** El orquestador descompone el documento en extractos por departamento. 3 workers se ejecutan en paralelo via `asyncio.gather()` con `asyncio.to_thread()` — Revenue (moneda por país), Clinical (detección PHI), Compliance (BAA/DPA por país). LLM como primario, fallback basado en reglas. Los workers **nunca inventan cifras** — los datos faltantes se registran como `open_questions`.
- **Handoff para Parte 2:** El sintetizador genera un `handoff_payload` JSON con ticket_id, rfp_metadata, análisis por departamento y síntesis. Listo para la Parte 2 (generación de borradores).
- **Estados:** `analizando` → (`analisis_completo` | `descartado` | `error`). Bilingüe español (alineado con el BRIEFING de CONTEXT.md).
- **3 PDFs verificados:** RFPs académicos y empresariales reales de 4GeeksAcademy verificados end-to-end.
- **16 tests:** 7 tests del clasificador (RFP, pitch, CV, newsletter, vacío, corto, PHI) + 9 tests de workers (Compliance BAA/DPA, Revenue USD/GBP, modelo DepartmentSection).
- **Documentación:** [`agentic-workflows.md`](./agentic-workflows.md), [`data/pipelines/rfp_intake/`](./data/pipelines/rfp_intake/)

---

## 📁 Documentación Clave

| Archivo | Contenido |
|---------|-----------|
| [`CONTEXT.md`](./CONTEXT.md) | Descripción completa de la empresa (HealthCore, departamentos, problemas) |
| [`agente-soporte-langgraph.md`](./agente-soporte-langgraph.md) | Arquitectura del agente LangGraph (Parte 1 + Parte 2) |
| [`predic-ventas-model-regresion.md`](./predic-ventas-model-regresion.md) | Diseño y auditoría del modelo de pronóstico de ventas |
| [`docs/rag/rag-design.es.md`](./docs/rag/rag-design.es.md) | Diseño de la base de conocimiento RAG |
| [`background-processes.md`](./background-processes.md) | Arquitectura del proceso en segundo plano |
| [`message-queues-asyns-task.md`](./message-queues-asyns-task.md) | Cola de Tareas Asíncronas — Redis + Celery |
| [`docs/ARCHITECTURE_PROPOSAL.md`](./docs/ARCHITECTURE_PROPOSAL.md) | Propuesta de arquitectura general |
| [`mcp-server.md`](./mcp-server.md) | Servidor MCP — tools protegidas con OAuth, arquitectura, configuración |
| [`agent-memory-harness-guard.md`](./agent-memory-harness-guard.md) | Memoria Persistente del Agente LangGraph + Harness y Guardrails — arquitectura, hardening del system prompt, seguridad determinista de 3 capas, diagramas de flujo |
| [`agentic-workflows.md`](./agentic-workflows.md) | Pipeline de Recepción RFP (Hito 9, Parte 1) — diseño, arquitectura, bugs, verificación, limitaciones |
| [`memory-bank/`](./memory-bank/) | Progreso del desarrollo, contexto activo, patrones del sistema, decisiones técnicas |

---

## 🧪 Filosofía de Testing

- **Tests de pipelines** (`tests/pipelines/`) cubren agente, RAG y pronóstico de ventas — todos ejecutables con `uv run python -m pytest`
- Los tests del agente incluyen: compilación del grafo, enrutamiento condicional, trazabilidad, comportamiento RAG fundamentado, enrutamiento de tools (5 tests), comportamiento de fallback de tools (5 tests), síntesis mixta (2 tests), 113 tests de agente+memoria (validación PHI, CRUD de store, clasificación de intención, orquestación del pipeline, integración en el grafo), y 12 tests de integración de harness
- **Tests de guardrails** (`tests/pipelines/test_guardrails.py`): 62 tests deterministas (con parametrización) que cubren el **Harness** (estructura del system prompt, aislamiento RAG, defensas anti-jailbreak), el **Content Guardrail** (las 6 categorías de alcance) y el **Output Guardrail** (PHI, filtración de instrucciones, formato). Todos son Python puro — sin LLM, sin red, sin base de datos.
- **Suite total del agente**: 125 tests de grafo + 62 tests de guardrails = **187 tests** cubriendo tanto la capa de memoria como la de seguridad.
  - Todos los tests del agente + guardrails pasan como puerta de entrada antes de fusionar ramas.
  - Sistema de memoria: 11/11 criterios de aceptación verificados mediante auditoría.
  - Harness y guardrails: 3 módulos deterministas (system prompt, content guard, output guard), 62 tests (con parametrización), 6 categorías de alcance de entrada, ~100 patrones regex — todo sin LLM.
  - Desarrollo impulsado por auditoría: cada entregable tiene criterios de aceptación verificables.


---

## Notas específicas del proyecto

- Este repositorio incluye actualmente un sitio web estático bilingüe (`index.html`, `index.es.html`, `application.html`, `application.es.html`) y lógica de validación de formularios en `validation.js`.
- Existe una aplicación Next.js para «Talent Pipeline Tracker» en `uis/talent-pipeline-tracker`.
- Comandos de validación de TypeScript disponibles actualmente:
  - `npm run typecheck`
  - `cd uis/talent-pipeline-tracker && npm run typecheck`

---

**Developer Wendy Sanchez**
