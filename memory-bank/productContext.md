# Product Context

## Producto y alcance actual
El producto en este repositorio tiene las siguientes superficies:

1. Sitio público HealthCore
- Landing page corporativa EN/ES con servicios, ubicaciones y contacto.
- Formulario de consulta de pacientes (no reserva directa) con validaciones de negocio.

2. Talent Pipeline Tracker (Next.js)
- Listado de candidaturas con filtros por estado/etapa y búsqueda por nombre o email vía query params.
- Vista de detalle por candidato con actualización de estado y etapa.
- Gestión de notas (listar, agregar, eliminar).
- Formularios para crear y editar candidaturas.

3. Directorio de Proveedores (API + backoffice)
- Registro centralizado de proveedores clínicos, operacionales y tecnológicos, sustituyendo las hojas de cálculo separadas por departamento.
- Listado con filtros por país (USA/UK) y por categoría de producto.
- Alta de proveedores, actualización de tarifa mensual con sello temporal y cambio de estado.
- Baja de proveedores conservando el histórico y la fecha en que se dejó de trabajar con ellos.

4. Autenticación y gestión de usuarios (API + backoffice + tracker)
- Registro de usuarios con perfil (nombre, teléfono, dirección).
- Login con JWT y sesión persistente en localStorage.
- Recuperación de contraseña con flujo completo: forgot-password (email vía Resend), reset-password (token único), change-password.
- Perfil de usuario: consulta y actualización.
- Roles: USER y ADMIN, con permisos diferenciados.

5. Sistema de Incidencias (API independiente + backoffice)
- API FastAPI independiente (puerto 8010) para gestión de incidencias de pacientes.
- Registro, listado con filtros, detalle y actualización de incidencias.
- Análisis de CSV de incidencias del sistema legacy.
- Categorización por tipo (appointment, billing, clinical_equipment, compliance_breach, patient_experience).
- Clasificación por origen (customer, staff, system, supplier).

6. Módulo de Inventario de Suministros Médicos (API + backoffice)
- API de inventario con 6 endpoints bajo /inventory.
- Modelos SQLModel en Supabase PostgreSQL para suministros, entregas y consumos.
- Stock siempre calculado como neto de entradas menos salidas (no modificable directamente).
- 4 páginas en el backoffice: productos con código de color, registro de entradas, registro de salidas con stock reactivo, historial de órdenes.
- Datos semilla: 8 suministros con stock calculado, 12 clínicas.

7. **Cola de Tareas Asíncronas — Redis + Celery (Oct 2026)**
- **Sistema de cola de tareas asíncronas** que desacopla las operaciones pesadas de la API para que los endpoints respondan inmediatamente (HTTP 202 Accepted) mientras un worker independiente procesa la tarea en segundo plano.
- **Arquitectura Productor-Consumidor**: FastAPI (productor) encola tareas ligeras en Redis (broker), Celery Worker (consumidor) las procesa en un proceso separado, y TinyDB actúa como Dead Letter Queue (DLQ) para tareas que fallan tras 3 reintentos.
- **Endpoint asíncrono**: `POST /api/incidents/analyze` ahora responde 202 Accepted con `task_id` y header `Location: /tasks/{task_id}`. El análisis de CSV de incidencias se procesa en segundo plano.
- **Endpoint de consulta**: `GET /tasks/{task_id}` devuelve el estado (`pending | started | success | failure`), el resultado, y si falló, la entrada DLQ con detalles del error.
- **Reintentos automáticos**: backoff exponencial (10s, 20s, 40s) con jitter, `acks_late=True` para no perder tareas si el worker se cae, máximo 3 intentos antes de DLQ.
- **Dead Letter Queue**: persistencia en TinyDB (`data/process/task_dlq.json`) con todos los detalles del fallo. Consultable automáticamente por el endpoint de estado.
- **Monitoreo**: Flower disponible en `http://localhost:5555` para inspeccionar colas, workers y tareas en tiempo real.
- **Infraestructura Docker**: 3 nuevos servicios (`redis`, `flower`, `celery-worker`) añadidos a `docker-compose.yml` bajo la red `healthcore-net`.
- **Auditoría**: 10/10 criterios cumplidos — Redis responde, encolado en ~30ms, estados correctos, reintentos funcionales, DLQ operativa, worker conectado, payloads ligeros, Flower accesible.

8. **Telemetría — Pipeline de análisis y dashboard (servidor standalone + backoffice)**
- **Servidor standalone**: `services/telemetry/server_standalone.py` — FastAPI en puerto 8000 que evita SQLModel (incompatible Python 3.14). Sirve `GET /telemetry/report` (5 métricas consolidadas) con caché de 60s, `POST /auth/login`, `GET /auth/me`, `GET /health`.
- **Pipeline de análisis**: `services/telemetry/analysis.py` — 5 funciones pandas que leen Supabase PostgreSQL y producen métricas: `events_per_day()`, `error_rate_by_type()`, `latency_percentiles_by_route()`, `event_type_distribution()`, `auth_failure_rate_by_day()`. Orquestadas por `run_all()`.
- **Autenticación standalone**: `services/telemetry/auth_standalone.py` — bcrypt + JWT (jose) + consultas TinyDB. Sin dependencia de `services/api/models.py`.
- **Seed de usuarios**: `admin@healthcore.com` / `adminpass456` (admin) y `test@healthcore.com` / `securepassword123` (user) en TinyDB.
- **Dashboard**: `uis/backoffice/app/telemetry/page.tsx` — 5 paneles visuales protegidos por AuthGuard. Selector de fechas. Proxy via rewrite en `next.config.ts`.
- **Eventos de telemetría**: 41 eventos (5 obligatorios, 36 oportunidades) definidos en `docs/telemetry/event-schemas.json`.

9. **Reporting Mensual — Supply Performance Pipeline + Dashboard (Oct 2026)**
- **Auth Standalone (`services/api/auth_standalone.py`)**: Servidor FastAPI ligero (puerto 8000) para login JWT del backoffice. Evita SQLModel (crash Python 3.14). Usa bcrypt + TinyDB.
- **Reporting API (`services/reporting/`)**: API FastAPI independiente (puerto 8001) con 3 endpoints JWT-protegidos para KPIs mensuales de suministros por clínica.
- **Pipeline de datos (`data/pipelines/`)**: Orquestación Prefect 3.8.8 (extract → transform → load) para proceso ETL mensual de telemetry_events a tabla reporting.
- **Dashboard Reporting**: Página protegida `/reporting` en backoffice con tabla de KPIs, nombres reales de clínica (mapeo `clinic-001` → "Central — Austin Main Clinic"), consumo, stockouts y riesgo caducidad.
- **Demo Data**: 10 clínicas (5 US + 5 UK) con datos de Octubre 2026 insertados en Supabase PostgreSQL.
- **Cadena E2E verificada**: Login → JWT → Reporting API → Dashboard con datos y nombres de clínica.

10. **Agente de Soporte LangGraph — HealthCore (Nov 2026)**
- **Grafo de decisión**: Migración del flujo RAG lineal a un grafo LangGraph con estado explícito (`AgentState`), 10 nodos de responsabilidad única y 5 aristas condicionales. El grafo se compila en tiempo de importación (falla estructural → servidor no arranca).
- **Checkpointing**: `MemorySaver` en cada transición de estado, lo que permite inspeccionar o retomar cualquier corrida.
- **Trazabilidad**: Cada corrida produce un trace persistido en JSON (`services/agent/traces/{run_id}.json`) con eventos de nodos, decisiones de enrutamiento y metadatos. Consultable via `GET /agent/trace/{run_id}` y `GET /agent/traces`.
- **Endpoint**: `POST /agent/query` — capa delgada FastAPI que invoca el grafo compilado sin duplicar lógica RAG. Maneja errores sin exponer detalles internos (422, 503).
- **Parte 2 — Tools externas (Nov 2026)**: Se añadieron 2 herramientas que consultan sistemas operativos reales:
  - `query_incident`: consulta el gestor de incidentes real (GET /api/incidents, puerto 8010, TinyDB). Sin autenticación.
  - `query_inventory`: consulta el gestor de inventario real (GET /inventory/products, puerto 8000, Supabase PostgreSQL). Requiere JWT Bearer (variable de entorno `AGENT_JWT_TOKEN`).
  - Ambas tools son solo lectura (GET), con timeout explícito de 5s, y rutas de fallback verificables (timeout, 404, 401, error de conexión).
  - El agente **decide automáticamente** si necesita RAG, tool(s) o ambos mediante clasificación léxica por patrones regex (sin LLM para el routing) — produce 5 decisiones: `"rag"`, `"incident"`, `"inventory"`, `"rag+incident"`, `"rag+inventory"`.
  - Nuevos nodos en el grafo: `classify_query`, `query_incident`, `query_inventory`, `synthesize_tool_answer` (10 nodos totales).
  - Nuevos routers: `route_after_classify`, `route_after_tool`, `route_after_generate`, `route_after_retrieve_v2`.
  - En modo combinado (rag+tool): primero tool (datos operativos), luego RAG (conocimiento), luego sintetiza ambos.
  - Si todas las tools fallan, el nodo `synthesize_tool_answer` responde honestamente sin inventar datos.
- **Evals**: 30 tests en 8 clases verifican compilación, enrutamiento (Parte 1 + Parte 2), trazabilidad, comportamiento RAG, endpoint, enrutamiento de tools externas (5 tests) y fallback de tools (5 tests). Todos pasan.
- **Relación con RAG**: Reutiliza `retrieve()` y `generate_answer()` de `data/pipelines/rag.py` sin modificar. No reescribe nada. Los nodos llaman retrieve y generate por separado — ningún nodo envuelve el `query()` monolítico.

11. **Modelo de pronóstico de ventas — Regresión con XGBoost (Oct–Nov 2026)**
- **Qué entrega**: pronóstico de ingresos mensuales de HealthCore para los 24 meses del bienio 2024-2025, comparado contra la realidad, con banda de variabilidad. Herramienta de planificación para Finanzas, no un dashboard en vivo (no hay endpoint; se ejecuta como script).
- **Dataset**: `data/raw/healthcore_sales.csv` provisto en el escenario (no simulado), serie `consolidated`, 120 meses desde 2016-01 hasta 2025-12. El script valida columnas, rango de fechas, ausencia de huecos y unicidad de la serie consolidada antes de entrenar.
- **Split 8/2**: entrenamiento 2016-01..2023-12 (96 meses, de los que 12 se descartan como warm-up de lags → 84 efectivos); prueba 2024-01..2025-12 (24 meses que el modelo nunca ve). No hay mezcla aleatoria: el corte es estrictamente cronológico.
- **Features (12)**: lags (`rev_lag_1`, `rev_lag_2`, `rev_lag_12`), ventanas rodantes con `shift(1)` anti-fuga (`rev_ma_3`, `rev_min_12`, `rev_max_12`, `rev_std_12`), calendario (`month_num`, `season`, `trend_index`) y drivers operativos (`visits_count`, `avg_revenue_per_visit_usd`). Todas miran solo hacia atrás.
- **Modelo**: XGBoost regularizado (`max_depth=4`, `learning_rate=0.03`, `gamma=0.5`, `reg_alpha=0.5`, `reg_lambda=2.0`, `random_state=42`). Se eligió sobre Random Forest por el tamaño de muestra (84 filas) y la estacionalidad marcada del negocio; la comparación razonada está en la documentación.
- **Métricas (sobre prueba, post-corrección anti-fuga)**: RMSE $154.910 (5,52 % del ingreso mensual medio), MAPE 3,47 % (peor mes 8,85 %), Gini predictivo 0,8188, K2/R² 0,8104, PSI de nivel 6,03 (alto por crecimiento estructural, no por deriva de distribución) y PSI de crecimiento ajustado 0,2718.
- **Banda de variabilidad**: ±$265.312 (±1,96σ) con σ = $135.363 derivada de la validación walk-forward (RMSE 2024 = $134.459, 2025 = $136.268). Cobertura empírica: 21 de 24 meses dentro de la banda (87,5 %).
- **Descomposición aditiva**: tendencia 55,0 %, estacional 39,5 %, residual 2,9 %. Gráfico `decomposition.png`.
- **Features justificación**: `visits_count` (48,9 % importancia) y `avg_revenue_per_visit_usd` (0,9 %) son componentes del target, no conocibles en t−1 para forecast puro.
- **Corrección anti-fuga (2026-11-02)**: `rev_ma_3`, `rev_min_12`, `rev_max_12`, `rev_std_12` usaban `rolling()` sin `shift(1)`, incluyendo `revenue_usd[t]`. Corregido. RMSE mejoró −1,54 %. Evidencia visual en `forecast_comparison_before_after.png`.
- **Artefactos**: `reports/sales_forecast/metrics.json` (métricas completas), `forecast_vs_real.png` (predicción vs. real con banda), `decomposition.png` (4 paneles), `forecast_comparison_before_after.png` (comparativa fuga vs corregido).
- **Documentación**: `predic-ventas-model-regresion.md` — justificación del algoritmo, definición de cada métrica en lenguaje explicable a Finanzas, tabla de resultados, lectura mes a mes, limitaciones, trazabilidad contra CONTEXT.md, más §11 completa con auditoría, descomposición, justificación de features y evidencia visual.

## Usuarios clave
- Pacientes potenciales de HealthCore (especialmente mercado bilingue en EE. UU.).
- Equipo de recepción y experiencia del paciente.
- Equipo de People/HR para seguimiento de candidaturas.
- Diane Foster (VP of People): gestión del gasto y contratos con proveedores.
- Claire Whitfield (Chief Compliance Officer): verificación de acuerdos BAA/DPA y auditoría de cambios de coste, más auditoría de incidencias de datos de pacientes.
- James Osei (CTO): prioriza la API de inventario como base del panel de operaciones clínicas.
- Dra. Okonkwo (Dirección Ejecutiva): necesita visibilidad del stock en toda la red clínica.
- Personal clínico y administrativo de las 12 clínicas: registran consumos y recepciones de suministros.
- Stakeholders operativos y ejecutivos que necesitan datos consistentes.

## Necesidades que cubre
- Credibilidad digital y acceso bilingüe para pacientes.
- Estandarización de datos de entrada para contacto posterior.
- Flujo de reclutamiento con visibilidad de estado y etapa.
- Reducción de trabajo manual mediante validaciones y formularios estructurados.
- Autenticación segura con JWT y gestión de sesión en backoffice y tracker.
- Flujo completo de recuperación de contraseña (olvido, restablecimiento, cambio) con rate limiting y auditoría.
- Registro y gestión de incidencias de pacientes con categorización y filtros.
- Análisis de CSV de incidencias del sistema legacy.
- API de inventario como base para el panel de operaciones clínicas.
- Visibilidad centralizada del stock de suministros médicos en las 12 clínicas.
- Los niveles de stock no se pueden modificar directamente — solo mediante órdenes de entrada (entregas) y salida (consumos).
- Distinción visual del nivel de stock (crítico, bajo, saludable) en la interfaz.

## Reglas de negocio relevantes
- Campos de formulario de paciente definidos explícitamente en CONTEXT.md.
- Validaciones especificas: edad, teléfono con código de país, ventana de fecha preferida, reglas condicionales de seguro, regla pediátrica y consentimiento obligatorio.
- En tracker de talento, operaciones CRUD y PATCH para estado/etapa con feedback visual.
- Proveedores: moneda determinada por el país (USA->USD, UK->GBP), tarifa mensual estrictamente positiva y estado limitado a active/suspended.
- Proveedores: cada cambio de tarifa registra su timestamp; los proveedores no se borran, se suspenden o se archivan con fecha de baja.
- **Autenticación**: login con email+password, JWT con expiración de 30 minutos, las rutas protegidas requieren Bearer token.
- **Incidencias**: no deben almacenar datos identificativos de pacientes (solo ID interno opaco). El campo description debe mostrar advertencia sobre no incluir PHI.
- **Inventario**: `current_stock` siempre se calcula (nunca almacenado). No se puede registrar un consumo que resulte en stock negativo (HTTP 400). `consumption_type` solo acepta `"clinical_use"` o `"expiry_waste"`. Los datos de inventario son operativos, no PHI.
- **Arquitectura dual de base de datos**: usuarios/autenticación en TinyDB, datos de inventario en Supabase PostgreSQL con SQLModel. No hay tabla de usuarios en SQLModel — `user_uuid` referencia TinyDB.
- **Pronóstico de ventas**: el conjunto de prueba (2024-2025) es intocable durante el entrenamiento y el ajuste de hiperparámetros. Cualquier métrica reportada como rendimiento del modelo debe calcularse sobre ese conjunto, nunca sobre el de entrenamiento.
- **Pronóstico de ventas**: el escalado (`StandardScaler`) se ajusta únicamente con datos de entrenamiento; ajustarlo con todo el histórico introduciría información del futuro en el modelo.
- **Pronóstico de ventas**: un PSI de nivel alto no invalida el modelo por sí solo — en una serie con crecimiento estructural mide el crecimiento, no la rotura del supuesto. Se reporta siempre junto al PSI de crecimiento ajustado.
- **Evaluación técnica del modelo**: el diagnóstico de overfitting/underfitting debe basarse en la brecha train/val de la CV y el patrón de convergencia de la curva de aprendizaje, no en una opinión. La acción correctiva debe ser específica y coherente con el diagnóstico.

## Criterios de experiencia de usuario
- Sin recargas completas para filtrar/buscar en tracker.
- Estados claros de carga, error y éxito en acciones asíncronas.
- Formularios con mensajes de validación accionables.
- Navegación simple entre listado y detalle.
- **Rendimiento de carga inicial**: la página principal debe cargar rápido incluso con bundles grandes. Los componentes pesados (AnalysisResultsPanel, IncidentFormPanel) se cargan bajo demanda (lazy loading) para no penalizar la primera impresión.
- **Consistencia de datos**: los valores derivados (denial rates, no-show costs) se calculan una sola vez por render (useMemo) y no cambian durante la vida de la página, incluso si hay re-renders parciales.
- **Frescura de datos en dashboards**: el resumen de incidencias y la lista de productos pueden tener hasta 30-60 segundos de desfase (caché). Esto es aceptable para dashboards operativos donde la inmediatez no es crítica.

13. **RFP Pipeline — Partes 1, 2 y 3: Clasificación, Generación de Borradores y Aprobación Humana (Nov 2026)**
- **Pipeline completo de 9 nodos LangGraph** que procesa solicitudes RFP (Request for Proposal) desde PDF hasta documento final aprobado:
  - **Parte 1**: Conversión PDF→Markdown (con markitdown[pdf]), extracción de metadatos, clasificación, descomposición por departamento, workers por departamento y síntesis.
  - **Parte 2**: Ciclo generador-evaluador de borradores por departamento con LLM real (DeepSeek v4 Flash via 4Geeks AI). Evaluadores async en paralelo (readability, relevance, compliance).
  - **Parte 3**: Aprobación humana con checkpoints scoped por ticket, arbitraje (PHI, BAA/DPA, Capacity), síntesis final y generación de documento completo.
- **Estados del ticket**: `nuevo → analizandose → analisis_completo → generando_borrador → en_evaluacion → needs_human_review → arbitrando → esperando_aprobacion / parcialmente_aprobado → produciendo → terminado`
- **Persistencia dual**: SQLite para checkpoints de Part 3 (en `data/checkpoints/{ticket_id}.db`) + PostgreSQL (Supabase) para estado oficial (TicketDB, DepartmentSectionDB).
- **3 endpoints Part 3**: `POST /approve`, `GET /approvals`, `GET /pending-approvals`
- **Tests**: 21 tests E2E que cubren Part 1, 2, 3, persistencia y edge cases. **21/21 ALL PASS** con LLM real.
- **Bugs corregidos en producción**: migración de columnas PG (`final_document_path`, `part3_completed_at`, `generation_status`, `generation_iteration`), checkpoint scoped por ticket, markitdown[pdf] instalado.

## Usuarios clave
- **Sistema de preguntas y respuestas** basado en Retrieval-Augmented Generation (RAG) que permite al equipo de HealthCore consultar en lenguaje natural las políticas documentadas de la clínica.
- **Documentos fuente**: 4 archivos Markdown en `docs/company-knowledge-base/` (cobertura de seguros, política de citas, proceso de referencias internas, checklist de pacientes nuevos), todos en español.
- **Arquitectura**: chunking semántico por párrafos → embedding con `pplx-embed-v1-0.6b` → indexado en Qdrant (vector DB) → recuperación por similitud coseno → generación de respuesta con `deepseek-v4-flash`.
- **Pipeline de chunking**: `CHUNK_MAX_CHARS=500` con solapamiento 0. Produce 9 chunks (de 4 originales) de 213-474 caracteres, 2-3 chunks por documento. Esto mejora la granularidad de recuperación.
- **Pipeline de recuperación**: `k=5`, `min_score=0.3`. Scores top-1 para preguntas relevantes ≈0.42-0.63. El umbral protege contra preguntas fuera de tema.
- **Afinación empírica**: se redujo `min_score` de 0.5 a 0.3 tras observar que con 4 chunks grandes los scores relevantes a veces caían a 0.49. Con 9 chunks más pequeños, los scores subieron y 0.3 captura todo lo relevante.
- **Servidor standalone**: Evita SQLModel (incompatible Python 3.14). Sirve UI HTML de chat en `GET /`, endpoints REST en `POST /knowledge/query`, `POST /rag/query`, y health check en `GET /rag/health`.
- **Interfaz de usuario**: Página de chat Q&A en el website público (`uis/website/app/rag/page.tsx`) para coordinadores de HealthCore. También UI HTML embebida en el propio servidor standalone.
- **Evaluación**: 8 queries de prueba en `data/eval/test-queries.json` cubriendo los 4 documentos. KPI: Recall@3 ≥ 80%, Faithfulness 100%, Threshold coverage 100%.
- **Tests**: 26 tests unitarios en `tests/pipelines/test_rag.py` cubriendo chunking, embedding, retrieval, generación y validación de modelos.

13. **MCP Server — OAuth-protected tools (Dec 2026 v1.0.0)**
- **Servidor MCP** que expone los módulos de Incidencias e Inventario como 10 herramientas MCP (Model Context Protocol), protegidas con OAuth 2.1 Resource Server vía MCP Auth (mcpauth). Autenticación activada por defecto con token dev fijo.
- **Herramientas de Incidencias (5)**: `get_incident`, `list_incidents`, `create_incident`, `update_incident_status` (usa endpoint de ciclo de vida `PATCH /api/incidents/{id}/status`), `get_incident_summary`.
- **Herramientas de Inventario (5)**: `get_product`, `list_products` (lectura), `create_product`, `update_product`, `delete_product` (las 3 de escritura devuelven error controlado `INVENTORY_READ_ONLY` — rechazo explícito, RETO 2).
- **OAuth v1.0.0**: SelectiveBearerASGI wrapper que mantiene `/.well-known/` y `/health` públicos, protege `/mcp` con Bearer JWT. Soporta JWKS (producción), token fijo de desarrollo (`hc_dev_token_no_usar_en_produccion`), y modo skip. 401 Unauthorized si falta token o es inválido.
- **Scopes**: `incidents.read`, `incidents.write`, `inventory.read`, `inventory.write` — mínimo privilegio por tool. Variable `MCP_DEV_TOKEN_SCOPES` asigna TODOS los scopes en desarrollo si no está configurado (permisivo para dev sin IDP real).
- **Logging con auditoría**: Cada invocación de tool genera log `"Tool OK/ERROR/DENIED | tool=<name> | client=<sub> | ..."` donde `client=<sub>` es el subject del token validado. Acceso al auth_info desde funciones de tool via ContextVar (`get_auth_info()`).
- **Integración LangGraph**: El agente de soporte migró de HTTP directo a MCP Client usando `langchain-mcp-adapters`. Los nodos del grafo se convirtieron a `async def` con `await tool.ainvoke()`.
- **Validación completa**: 9/9 tests de autenticación pasan. Auth_info accesible en contexto async. Logging incluye cliente. Scopes se validan por tool. MCP_SKIP_AUTH produce warning si está activado.
- **Documentación**: `mcp-server.md` — arquitectura completa, setup, tools, OAuth flow, logging con cliente, contextvars, códigos de error, migración del agente, testing. README.md / README.es.md actualizados con startup sin `MCP_SKIP_AUTH=1`.

14. **Sistema de Memoria Persistente del Agente — Memory & Self-Improvement (Nov 2026)**
- **Sistema de memoria persistente** para el agente LangGraph de HealthCore, que permite al agente recordar hechos operativos entre conversaciones (preferencias de personal, patrones de incidencias, configuraciones operativas) con control explícito del usuario y auditoría completa.
- **Backend TinyDB**: 3 tablas en `data/process/agent_memory/agent_memory.json` — `memory_facts` (hechos aprobados y consolidados), `pending_proposal` (singleton, máximo 1 propuesta pendiente), `audit_log` (8 tipos de eventos trazados).
- **Flujo de memoria**: El agente evalúa automáticamente sus respuestas para detectar contenido memorizable (18 trigger keywords). Si detecta un candidato, clasifica en 3 categorías (`operational`, `incident_pattern`, `staff_preference`), previene duplicados, y construye una propuesta que se anexa a la respuesta con el sufijo `📝 **¿Quieres que recuerde esto para la próxima vez?**`. El usuario responde en lenguaje natural, y el clasificador interpreta la intención (approve/reject/edit/discard) mediante reglas léxicas con umbrales de confianza.
- **Clasificación sin LLM**: El clasificador (`classifier.py`) usa patrones regex para determinar la intención del usuario, con umbrales calibrados (approve ≥0.70, reject ≥0.75, edit ≥0.85). Silencio o ambigüedad = descarte (confianza 0.0). Incluye detección de cambio de tema para no interpretar mensajes no relacionados como respuestas a la propuesta.
- **PHI Compliance**: 8 patrones PHI cubriendo HIPAA (US) + UK GDPR: nombres con caracteres acentuados, SSN (###-##-####), NHS numbers (###-###-####), emails, MRNs, términos clínicos (diagnosis, biopsy, etc.), fechas de nacimiento, teléfonos, y direcciones físicas. Validación dual (antes de mostrar propuesta y antes de escribir en BD) con auditoría en ambos casos.
- **Integración en grafo**: 3 nuevos nodos LangGraph (`load_memory_context`, `process_proposal`, `auto_evaluate_memory`) y 3 nuevos routers. El grafo ahora tiene 13 nodos totales. Las respuestas del agente siempre pasan por evaluación de memoria antes de llegar al usuario.
- **Consolidación automática**: Cuando el número de hechos alcanza 50 (threshold configurable) o un hecho supera 90 días de antigüedad, la consolidación agrupa por categoría, resume, y purga hechos expirados. La consolidación re-valida PHI.
- **Auditoría completa**: 8 tipos de eventos auditables (`created`, `rejected`, `edited`, `phi_blocked`, `discarded`, `consolidated`, `expired`, `proposal_skipped`). Cada decisión del usuario queda registrada con timestamp, propuesta original, contenido final (si aplica) y resultado de validación PHI.
- **Tests**: 32 nuevos tests específicos de memoria + 32 tests originales = 64 tests total. PHI tests cubren los 8 patrones individualmente. Tests de integración verifican el flujo completo en el grafo.
- **Ciclos de evidencia documentados**: 4 ciclos completos en `agent-memory-harness-guard.md`: (1) propuesta aprobada, (2) propuesta rechazada, (3) propuesta editada por el usuario, (4) propuesta bloqueada por PHI.

15. **RFP Intake Pipeline — Automatización de recepción y clasificación de RFP (Dec 2026)**
- **Pipeline automático** que recibe documentos PDF de solicitudes de propuesta (RFP), los clasifica, extrae metadatos, descompone por departamento, y genera un payload estructurado para el equipo de Ventas de HealthCore.
- **¿Qué problema resuelve?**: El equipo de Ventas recibe documentos RFP de potenciales clientes (planes de salud, gobiernos, empleadores) y actualmente debe leerlos manualmente, determinar si realmente es una RFP o un pitch comercial/CV genérico, extraer fechas, presupuestos y población cubierta, y distribuir secciones a los departamentos de Revenue, Clinical y Compliance para análisis. Este pipeline automatiza todo ese proceso de principio a fin.
- **Pipeline de 6 nodos secuenciales** (LangGraph):
  1. **Convertir PDF a Markdown** — MarkItDown con fallback PyPDF2.
  2. **Extraer metadata y legibilidad** — 5 métricas (Flesch, Kincaid, Fog, Coleman-Liau, ARI).
  3. **Clasificar documento** — Two-stage: reglas pre-filter (descarta pitches/CV/newsletters/EHR vendor pitches) → LLM deepseek-v4-flash vía 4Geeks AI. Early return cuando las reglas ya determinan YES-RFP con alta confianza.
  4. **Orquestar por departamento** — Descompone el documento en 3 secciones (Revenue, Clinical, Compliance) usando patrones regex.
  5. **Workers departamentales** — Cada worker enriquece su sección con aspectos clave y preguntas abiertas. Reglas de negocio: moneda según país del cliente; cláusulas BAA/DPA según país. NUNCA inventa cifras.
  6. **Sintetizar** — Consolidación: executive summary, análisis por departamento, detección de conflictos entre secciones, alertas (PHI, fechas próximas), y payload de handoff para la Parte 2.
- **4 estados de ticket**: `analizando` → `descartado` (si no es RFP legítima), `analisis_completo` (éxito), `error` (fallo técnico).
- **Handoff para Parte 2 (Hito 9)**: El payload `handoff_payload` (JSONB en `rfp_tickets`) contiene `executive_summary`, `departments_analysis`, `conflicts_detected`, `alerts`, y `handoff_instructions`. Esta estructura será consumida por el pipeline de Parte 2 para la evaluación técnica por departamento.
- **Interfaz de Backoffice**: Dos páginas en `uis/backoffice/app/rfp/` — listado con upload (polling 5s) y detalle con metadata, legibilidad, secciones departamentales, alertas PHI, y botón de reproceso.
- **API asíncrona**: `POST /rfp/upload` responde 202 Accepted con `ticket_id`, el procesamiento continúa en background via Celery. El frontend polling detecta la transición a `analisis_completo`.
- **Arquitectura de proxy**: Next.js sirve rutas `/api/rfp/*` que redirigen al backend en `/rfp/*`, solucionando la diferencia de paths entre navegador y servidor FastAPI.
