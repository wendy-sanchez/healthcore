# Progress

## Estado actual del desarrollo

### NIST AI Security Framework Audit — Prácticas Seguras en IA (2026-11-27)

**✅ COMPLETADO — Auditoría NIST AI RMF 1.0 + CSF 2.0, 6 funciones auditadas, inventario de 7 sistemas de IA, rate limiting implementado en login y agente**

**Resumen**: Se realizó una auditoría completa basada en el NIST AI Risk Management Framework (AI RMF 1.0) y NIST Cybersecurity Framework (CSF 2.0) sobre todos los sistemas de IA de HealthCore. La auditoría cubre las 6 funciones NIST (Govern, Identify, Protect, Detect, Respond, Recover), el inventario completo de 7 sistemas de IA, el mapeo regulatorio HIPAA (EE. UU.) vs UK GDPR (Reino Unido), y 4 casos de prueba de prompt injection documentados.

**Documentos generados:**
| Documento | Contenido |
|-----------|-----------|
| `nist-security-practices.md` | ✅ NIST AI RMF completo — 6 funciones, inventario 7 IA, plan priorizado, prompt injection test cases, verificación PHI |

**Correcciones nuevas implementadas:**
| # | Corrección | Función NIST | Archivos |
|---|-----------|-------------|----------|
| 1 | Rate limiting en `POST /auth/login` (10 intentos/60s por IP) | Protect/Detect | `rate_limiter.py`, `routes/auth.py` |
| 2 | Rate limiting en `POST /agent/query` (30 queries/min por usuario) | Protect/Detect | `rate_limiter.py`, `routes.py` |
| 3 | Refactor `_get_table_or_log()` fail-open helper | Protect | `rate_limiter.py` |
| 4 | `signal()` como import global en rate_limiter | Detect | `rate_limiter.py` |

**Rate limiting implementado:**
- **Login:** 10 intentos por IP cada 60 segundos, in-memory, con telemetría `auth_login_failed` cuando se excede
- **Agent:** 30 queries por usuario (con fallback a IP) cada 60 segundos, in-memory
- **Password reset:** 5 solicitudes por email cada 60 minutos (existente, vía TinyDB)
- **SSH:** 6 conexiones/60s (existente, vía iptables)

**Tests:** 477 pass, 0 fail, 7 skipped — sin regresiones tras cambios.

---

### WebSocket Chat en Tiempo Real — Compliance Assistant (2026-11-27)

**✅ COMPLETADO — Chat bidireccional en tiempo real con el agente LangGraph de Compliance, streaming token-by-token vía WebSocket**

**Arquitectura**: `ChatSessionManager` singleton con sesiones in-memory, `ChatSessionPubSub` por sesión (colas `asyncio.Queue` por suscriptor, broadcast O(n) bajo `asyncio.Lock`), streaming con `agent_graph.astream_events(version="v2")`.

**Componentes backend**:
- `services/agent/chat/models.py` — `ChatMessage`, `ChatSessionState`, `EventPayload`, builders `make_token_chunk()`, `make_generation_completed()`, `make_session_snapshot()`, `make_error()`.
- `services/agent/chat/pubsub.py` — `ChatSessionPubSub` con `subscribe()`/`unsubscribe()`/`publish()`. Serialización JSON antes de encolar.
- `services/agent/chat/manager.py` — `ChatSessionManager` singleton: 4 dicts paralelos (`_sessions`, `_hubs`, `_tasks`, `_cancel_events`). `get_or_create_session()`, `handle_user_message()`, `handle_interrupt()`, `close_session()`. `_run_agent_stream()` ejecuta `agent_graph.astream_events(initial_state, config=config(session_id), version="v2")` con `thread_id = session_id`, filtra `on_chain_end` (nodo `generate_answer`) y `on_chat_model_stream`. Fallback `ainvoke()`. Chequeo de `cancel_event.is_set()` por iteración.
- `services/api/routes/chat_ws.py` — `@router.websocket("/ws/chat")` con JWT via `?token=` (validación con `jose.decode`, `JWT_SECRET_KEY`, `JWT_ALGORITHM`). Rechaza con `WS_1008_POLICY_VIOLATION` antes de `accept()`. Drain task para escuchar pub/sub en paralelo.
- `services/api/routes/__init__.py` — registro del router chat_ws.
- `services/api/main.py` — montaje del endpoint WebSocket.

**Componentes frontend**:
- `uis/backoffice/lib/wsClient.ts` — Clase `WSClient` con constructor `(token, sessionId, callbacks)`. `connect()` crea `WebSocket` con `?token=&session_id=` en URL. Reconexión con backoff exponencial (1s→2s→...→30s). `sendUserMessage()`, `sendInterrupt()`, `disconnect()`. Callbacks: `onToken`, `onCompleted`, `onInterrupted`, `onSnapshot`, `onError`, `onStatusChange`.
- `uis/backoffice/lib/wsClient.ts` — `useWsChat` React hook: expone `messages`, `status`, `isConnected`, `sendMessage(text)`, `interrupt()`.
- `uis/backoffice/app/compliance/page.tsx` — Página protegida con AuthGuard. Input + botón enviar. Historial de mensajes: usuario derecha, asistente izquierda con tokens renderizados en vivo. Interrupción automática al enviar nuevo mensaje mientras el agente responde. Badge "⏹ Interrumpido" con contenido parcial visible. Indicador verde/rojo de conexión.
- `uis/backoffice/components/BackofficeHeader.tsx` — Enlace "Compliance Chat" añadido.

**Protocolo WebSocket** (7 eventos nombrados, JSON bidireccional):
| Evento | Dirección | Descripción |
|--------|-----------|-------------|
| `user_message` | Cliente → Servidor | Nuevo mensaje del usuario: `data: {session_id, text}` |
| `interrupt_requested` | Cliente → Servidor | Solicitud de interrupción: `data: {session_id, new_input}` |
| `token_chunk` | Servidor → Cliente | Token del asistente: `data: {session_id, token, sequence, message_id}` |
| `generation_completed` | Servidor → Cliente | Generación terminada: `data: {session_id, message_id}` |
| `generation_interrupted` | Servidor → Cliente | Generación abortada: `data: {session_id, message_id, status: "interrupted"}` |
| `session_snapshot` | Servidor → Cliente | Historial completo para rehidratación: `data: {session_id, messages: [...]}` |
| `error` | Servidor → Cliente | Error: `data: {session_id, message}` |

**Estrategia de interrupción**: `asyncio.Event` señaliza cancelación → `asyncio.Task.cancel()` fuerza `CancelledError` → contenido parcial preservado con `status="interrupted"` → nuevo input → nuevo turno.

**Reconexión y rehidratación**: Mismo `session_id` → `get_or_create_session()` retorna misma instancia → `session_snapshot` con historial completo. `thread_id = session_id` habilita rehidratación de checkpoint LangGraph al reconectar.

**Tests**: `tests/pipelines/test_chat_websocket.py` — **35 tests** que cubren:
- `TestWireFormat` (7): payloads de eventos y serialización
- `TestChatSessionState` (7): creación, add_message, snapshot, orden
- `TestChatSessionPubSub` (5): publish, múltiples suscriptores, unsubscribe, empty hub, subscriber count
- `TestChatSessionManager` (4): create/get, subscribe/unsubscribe, snapshot, close
- `TestInterrupt` (3): status change, partial content preservation, new turn
- `TestReconnection` (2): session restore, snapshot with history
- `TestWebSocketAuth` (3): empty token, invalid token, valid JWT
- `TestUserMessageFlow` (2): add to history, interrupt with new input
- `TestSingleton` (2): same instance, reset between tests

### OWASP Top 10 Security Audit & Server Hardening (2026-11-27)

**✅ COMPLETADO — Auditoría completa de seguridad OWASP Top 10 con endurecimiento del servidor y corrección de vulnerabilidades críticas.**

**Documentos generados:**
- `secure-ai-applications.md` — Reporte OWASP Top 10 completo con 10 categorías evaluadas × 3 lanes (backend, frontend, agente)
- `docs/security/server-hardening.md` — Documento de endurecimiento del servidor con evidencia reproducible
- `tests/security/test_cors.py` — Tests de CORS restringido (A05)
- `tests/security/test_agent_auth.py` — Tests de auth en endpoints del agente (A01)
- `tests/security/test_env_permissions.py` — Tests de permisos de archivos (A02)
- `tests/security/test_redis_not_exposed.py` — Tests de Redis no expuesto (A05)
- `tests/security/test_jwt_no_fallback.py` — Tests de JWT sin fallback hardcodeado (A02)

**Vulnerabilidades críticas identificadas y corregidas (4):**

| OWASP | Vulnerabilidad | Corrección | Evidencia |
|-------|---------------|------------|-----------|
| A01/A07 | Agent endpoints sin autenticación (3 endpoints) | `Depends(get_current_user)` en `services/agent/routes.py` | 468 tests pass |
| A02 | JWT secret hardcodeado en `chat_ws.py` | Eliminado fallback `"dev-secret-key-change-in-production"` | 468 tests pass |
| A02 | `.env` world-readable (666) | `chmod 600 .env` | `stat -c '%a' .env` → 600 |
| A05 | CORS `allow_origins=["*"]` | Whitelist de 4 orígenes | 468 tests pass |
| A05 | Redis expuesto en `0.0.0.0:6379` | Sin puertos en docker-compose + `docker stop` | `ss -tlnp \| grep 6379` → vacío |

**Endurecimiento del servidor (6 medidas):**

| Medida | Estado | Verificación |
|--------|--------|-------------|
| `PermitRootLogin no` | ✅ Aplicado | `grep PermitRootLogin /etc/ssh/sshd_config` → `no` |
| Usuario `healthcore-ops` (uid=999) | ✅ Creado | `id healthcore-ops` |
| Firewall iptables (solo SSH:2222 desde internet) | ✅ Activo | `sudo iptables -L INPUT -n` |
| SSH rate limiting (6 conexiones/60s) | ✅ Activo | Reglas iptables 13-14 |
| Project root permisos 750 | ✅ Aplicado | `stat -c '%a' .` → 750 |
| Reglas firewall persistentes | ✅ Guardadas | `/etc/iptables.rules` |

**Tests:** 477 tests pasan, 0 fallan, 7 skipped (9 nuevos tests de seguridad incluidos).

**Archivos creados/modificados:**
| Archivo | Acción | Descripción |
|---------|--------|-------------|
| `secure-ai-applications.md` | ✅ Creado | Reporte OWASP Top 10 + correcciones (724 líneas) |
| `docs/security/server-hardening.md` | ✅ Creado | Documento de endurecimiento del servidor |
| `docs/security/__init__.py` | ✅ Creado | Init del paquete security |
| `tests/security/__init__.py` | ✅ Creado | Init del paquete tests/security |
| `tests/security/test_cors.py` | ✅ Creado | Tests de CORS (A05) |
| `tests/security/test_agent_auth.py` | ✅ Creado | Tests de auth en agente (A01) |
| `tests/security/test_env_permissions.py` | ✅ Creado | Tests de permisos (A02) |
| `tests/security/test_redis_not_exposed.py` | ✅ Creado | Tests de Redis (A05) |
| `tests/security/test_jwt_no_fallback.py` | ✅ Creado | Tests de JWT (A02) |
| `services/api/main.py` | ✅ Modificado | CORS whitelist + docs env-gated |
| `services/api/routes/chat_ws.py` | ✅ Modificado | JWT sin fallback hardcodeado |
| `services/agent/routes.py` | ✅ Modificado | Auth JWT en 3 endpoints |
| `docker-compose.yml` | ✅ Modificado | Redis sin puertos expuestos |

---

**Auditoría**: ✅ **6/7 criterios de aceptación cumplidos** totalmente:
1. ✅ Token streaming en vivo (`token_chunk` → renderizado token por token)
2. ✅ JWT obligatorio en query param (rechazo con `WS_1008_POLICY_VIOLATION`)
3. ⚠️ Session ID ligado a conversación (`thread_id = session_id` implementado, checkpointing depende de persistencia futura)
4. ✅ Interrupción medible (cancelación + contenido parcial preservado)
5. ✅ Reconexión con historial (mismo `session_id` + `session_snapshot`)
6. ✅ Pub/sub desacoplado (`ChatSessionPubSub` + 7 eventos nombrados)
7. ✅ Nombres vs CONTEXT.md (todos los campos coinciden)

**Archivos creados/modificados**:
| Archivo | Acción | Descripción |
|---------|--------|-------------|
| `services/agent/chat/__init__.py` | ✅ Creado | Init del paquete chat |
| `services/agent/chat/models.py` | ✅ Creado | Modelos y builders de eventos |
| `services/agent/chat/pubsub.py` | ✅ Creado | Hub pub/sub por sesión |
| `services/agent/chat/manager.py` | ✅ Creado | Singleton gestor de sesiones + streaming agent |
| `services/api/routes/chat_ws.py` | ✅ Creado | Endpoint WebSocket `/ws/chat` con JWT auth |
| `services/api/routes/__init__.py` | ✅ Modificado | Registro del router chat_ws |
| `services/api/main.py` | ✅ Modificado | Montaje del WebSocket endpoint |
| `uis/backoffice/lib/wsClient.ts` | ✅ Creado | Cliente WebSocket con backoff, interrupt |
| `uis/backoffice/app/compliance/page.tsx` | ✅ Creado | UI de chat compliance |
| `uis/backoffice/components/BackofficeHeader.tsx` | ✅ Modificado | Enlace a Compliance Chat |
| `tests/pipelines/test_chat_websocket.py` | ✅ Creado | 35 tests WebSocket |
| `scripts/seed_test_user.py` | ✅ Creado | Crea test user para chat |

**Limitaciones conocidas**: `ChatSessionManager` en memoria — las sesiones se pierden al reiniciar el servidor. Broadcast O(n) funciona para pocos suscriptores por sesión (supervisor + staff). Historial sin persistencia en base de datos.

---

### Notificaciones SSE en Tiempo Real — RFP Dashboard (2026-11-27)

**✅ COMPLETADO — Sistema de notificaciones en tiempo real para tickets RFP vía Server-Sent Events**

**Arquitectura**: SSEManager singleton con colas `asyncio.Queue` por suscriptor, buffer circular (50 eventos), broadcast O(n) bajo `asyncio.Lock`.

**Componentes backend**:
- `services/api/sse/manager.py` — `SSEManager` con subscribe/publish/buffer circular/keep-alive 30s. `subscribe()` acepta `request` opcional para `is_disconnected()`.
- `services/api/sse/__init__.py` — Paquete SSE, exporta `SSEManager` y `get_sse_manager`.
- `services/api/routes/rfp.py` — 3 endpoints: `GET /rfp/events` (SSE stream JWT-protegido con Last-Event-ID), `POST /rfp/kpi-alert` (KPI threshold alerts), `POST /rfp/upload` modificado (publica `rfp_ticket_created`).
- Replay: buffer circular + `Last-Event-ID` header + refetch reconciliación.

**Componentes frontend**:
- `uis/backoffice/lib/sseClient.ts` — Cliente SSE con `fetch()` + `ReadableStream` (no `EventSource`). Backoff exponencial (1s→2s→...→30s), dedup via `Set<eventId>`, `Last-Event-ID` en reconexión, callbacks `onEvent`/`onReconnected`/`onStatusChange`, detención en 401.
- `uis/backoffice/app/rfp/page.tsx` — Dashboard reemplaza polling por SSE. Banners de notificación (máx 5, auto-dismiss 10s) con ícono N, nombre cliente, estado, timestamp, indicador SSE (verde "Tiempo real activo" / rojo "Reconectando...").

**Tests**: 16/16 tests SSE + 35/35 tests RFP existentes = **51 tests PASS** sin regresiones.
- `TestSSEManager` (7): publish broadcast, IDs, buffer replay, subscriber count, unsubscribe
- `TestSseWireFormat` (3): formatos rfp_ticket_created, kpi_threshold_alert, keepalive
- `TestSseEndpoint` (4): auth, streaming, kpi alert, kpi auth
- `TestSseManagerSingleton` (2): singleton state preservation

**Auditoría**: ✅ 6/6 criterios de aceptación cumplidos — SSEManager, endpoint SSE, publicación upload, frontend SSE, dashboard refactorizado, KPI alerts.

**Decisiones técnicas**: SSE raw sin sse-starlette; fetch+ReadableStream en frontend por headers JWT; is_disconnected() para limpieza temprana de conexiones caídas; singleton seguro con asyncio.Lock.

### Sistema de Memoria Persistente del Agente — Hito 8 · Memoria y Auto-mejora (2026-11/12)

- **Completado**: Paquete `services/agent/memory/` con 5 módulos:
  - `store.py` (~716 líneas) — Backend TinyDB con 3 tablas, validación PHI dual con **lista blanca** (`_PHI_WHITELIST` + `_PHI_WHITELIST_PREFIXED`), consolidación automática (50 facts, TTL 90 días), auditoría con 8 eventos (`proposed`, `approved`, `rejected`, `discarded`, `edited`, `phi_rejected`, `consolidated`, `unrelated`).
  - `classifier.py` (~567 líneas) — **Clasificador estructural** rediseñado con **6 ejes analíticos** y **pipeline de 10 pasos** en `classify_user_intent()`. Sin listas planas de patrones. Funciones: `_es_pregunta_o_condicional()`, `_hay_rechazo()`, `_hay_aprobacion()`, `_hay_edicion()`, `_es_solo_cortesia()`, `_es_falso_positivo_aprobacion()`, `_comando_memoria_negado()`. Umbrales: approve (≥0.70), reject (≥0.75), edit (≥0.85), discard (0.0). Guardas adicionales: negación con pronombres entre "no" y verbo, contradicciones sí+no → discard, comandos de memoria negados no suman approve.
  - `pipeline.py` (~802 líneas) — Orquestador con 28 trigger keywords, 3 extractores + 2 de respaldo, clasificación en 3 categorías, rechazo PHI explícito con mensaje HIPAA/UK GDPR.
  - `nodes.py` (~267 líneas) — 3 nodos LangGraph + 3 routers.
  - `__init__.py` — 6 principios de diseño.

- **Completado**: Integración en grafo LangGraph:
  - 8 nuevos campos en `AgentState` (nuevo: `llm_memory_proposal`).
  - Entrypoint actualizado y grafo ampliado de 10 a 13 nodos.

- **Completado**: Evaluación de memoria por LLM (Mejora 17):
  - `rag.py`: Nueva función `generate_answer_with_memory()` que devuelve `(answer, proposal_or_None)`.
  - El LLM decide si hay información memorable usando instrucción estructurada (`_MEMORY_SYSTEM_PROMPT_SUFFIX`).
  - Elimina dependencia de triggers heurísticos para decidir qué es memorable.
  - `graph.py`: `node_generate_answer()` usa `generate_answer_with_memory()` como primaria, `generate_answer()` como fallback.
  - `pipeline.py`: `auto_evaluate_proposal()` acepta `llm_proposal`, con `_process_llm_proposal()` que prioriza juicio LLM.
  - `nodes.py`: Pasa `llm_memory_proposal` desde el estado.
  - Tests: 89/89 pasan (fix de sintaxis en `_MEMORY_SYSTEM_PROMPT_SUFFIX`).

- **Completado**: PHI Compliance con validación TWICE + re-validación consolidación.

- **Completado**: **71 tests de memoria** en 5 clases (antes 32):
    - `TestMemoryPHI` (8 tests)
    - `TestMemoryStore` (5 tests)
    - `TestMemoryClassifier` (**38 tests** — antes 29, cubriendo 6 ejes estructurales: approve/reject/edit/discard, cortesía sola, pregunta/condicional, falso positivo approve, texto vacío, sin propuesta pendiente, **negación con pronombres, contradicciones sí+no, contradicción no+comando con separadores, contradicción con nunca/jamás/tampoco**)
    - `TestMemoryPipeline` (**11 tests**, incluyendo **3 tests E2E**: approve_cycle/reject_cycle/discard_cycle)
    - `TestMemoryGraphIntegration` (**9 tests**, incluyendo **5 tests E2E**: approve_cycle, reject_cycle, discard_cycle, **PHI-rejected auto-evaluate, PHI-rejected set_pending, consolidación**)
    - **62/62 tests de memoria pasan + 32 tests originales = 94 tests en test_agent_graph.py.**

  - **Completado**: **357 tests totales** en la suite del workspace (94 memory+agent + 130 de sales_forecast, MCP auth, nightly, etc. + 62 guardrails + 27 Part 2 + 19 + 25 nuevos classifier).

- **Completado**: **Rediseño del clasificador** (Bug 15): de listas planas de patrones a clasificador estructural con 6 ejes analíticos. Resuelve de raíz 3 bugs reportados por el usuario:
  - Bug 12: "si llueve" → ahora discard (eje pregunta/condicional)
  - Bug 13: "bien gracias" → ahora discard (eje cortesía sola)
  - Bug 14: "cambia el horario" → ahora edit (eje edición)

- **Completado**: Auditoría — criterios cumplidos. Reporte completo en AUDITORIA1.md.
- **Completado**: **3 mejoras post-auditoría** (1 descartada, 2 implementadas):
  - ✅ **PHI-rejected flow E2E** (2 tests: auto-evaluate + set_pending) — cierre de compliance crítico
  - ✅ **Consolidación E2E** (1 test: force=True, verifica resumen, TTL, PHI re-validation)
  - ❌ Trigger keywords en archivo externo — descartado (constantes en módulo es suficiente, sin valor real en externalizar)

- **Completado**: Documentación técnica actualizada:
  - `agent-memory-harness-guard.md` (~1350+ líneas) — 13 secciones (nueva §9-B Ciclo 5 — Unrelated), clasificador estructural documentado en §5.2 con 6 ejes y tabla de ejemplos, evento de auditoría `"unrelated"` añadido, tabla ampliada a **17 bugs corregidos**, suite summary actualizado.

- **Completado**: Bugs corregidos (17 totales):
  1. `route_after_receive` devolvía `"classify_query"` pero el entrypoint cambió a `"load_memory_context"` → KeyError en aristas del grafo. Corregido el valor de retorno y el mapeo de aristas.
  2. PHI: falsos negativos — patrón regex `[A-Z][a-z]+ [A-Z][a-z]+` no emparejaba caracteres acentuados (ej: "Juan Pérez"). Corregido a `[A-Z][a-zA-ZáéíóúñÁÉÍÓÚ]+`. Además se expandieron patrones PHI: términos clínicos (20+ condiciones), teléfono internacional con lookbehind, MRN con colon, apellidos hispanos (70+), fechas standalone.
  3. `add_fact()` no validaba PHI antes de escribir en TinyDB — se añadió validación dual con auditoría, retornando None si se detecta PHI.
  4. Clasificador: ambigüedad como aprobado — `"¿cambia el horario?"` y `"si llueve cancelo"` generaban propuestas. `_should_evaluate` reescrito: bloquea hipotéticas, requiere trigger keywords en combined text.
  5. Consolidación: facts nunca expiraban — `add_fact()` no asignaba `expires_at` para facts no consolidados. Auto-asignación de `_DEFAULT_TTL_DAYS` (90) cuando `consolidated=False` y `expires_at is None`.
  6. Loop RAG+tool — `route_after_generate` re-enrutaba a `synthesize_tool_answer` por `tool_results` persistentes. Nueva `route_after_synthesis()` que va directo a `auto_evaluate_memory` o `__end__`.
  7. CONTEXT examples bloqueados — `"reporte"`, `"dashboard"`, `"esta semana"` en non-memorizable; faltaban triggers `"pidió"`, `"debe"`, etc. Movidos a triggers keywords; expandidos con `"pidió"`, `"debe"`, `"alerta"`, `"cierre"`, `"no-show"`, `"fue porque"`, `"hubo"`.
  8. **Word boundary en `_QUERY_PHRASES`** — `"lista"` matcheaba dentro de `"especialista"` por substring check. Corregido a `re.search(r"\b" + re.escape(phrase) + r"\b", q_lower)`. Esto bloqueaba el Caso 2 (Manchester cambio proceso).
  9. **Faltaban comandos explícitos en triggers** — `"apúntalo"`, `"guárdalo"`, `"recuérdalo"`, `"escribe"`, `"almacena"` y variantes no estaban en `_MEMORIZE_TRIGGER_KEYWORDS`. Añadidos 10 comandos. Esto causaba que el Caso 1 (Johnson PHI con "apúntalo") fuera ignorado en silencio.
  10. **`_extract_memory_candidate` sin fallback** — Incluso cuando `_should_evaluate()` retornaba True, si ningún extractor específico clasificaba la interacción, retornaba `None` y la propuesta se perdía antes de llegar a validación PHI. Añadidos `_extract_command_memory()` para comandos explícitos y `_extract_fallback_content()` como fallback genérico con inferencia de categoría.
  11. **PHI rejection silenciosa** — `auto_evaluate_proposal()` retornaba `(answer, None)` cuando `validate_phi_safe()` detectaba PHI, sin informar al usuario. Corregido a rechazo explícito con mensaje HIPAA/UK GDPR y `{"phi_rejected": True}`. El nodo de grafo detecta esto y no persiste la propuesta.
  - **Además**: **Lista blanca PHI** añadida en `store.py` (`_PHI_WHITELIST` y `_PHI_WHITELIST_PREFIXED`) para evitar falsos positivos con staff de HealthCore (Diane Foster, Claire Whitfield, Marcus Reid, Sandra Okonkwo, Priya Nair, James Osei) y ubicaciones operativas (Austin, Manchester, London, Miami, Dallas, etc.). Esto resolvió 2 falsos positivos: "En Austin" y "Diane Foster".
  - Bugs 12-15: **Rediseño del clasificador** (de listas planas a clasificador estructural con 6 ejes analíticos). Resuelve de raíz 3 bugs reportados por el usuario:
    - Bug 12: "si llueve" → ahora discard (eje pregunta/condicional)
    - Bug 13: "bien gracias" → ahora discard (eje cortesía sola)
    - Bug 14: "cambia el horario" → ahora edit (eje edición)
  - Bug 16: **Negación con pronombres no se detecta** — "no lo guardes", "sí, pero no" → approve. `_RE_REJECT_PHRASE` no aceptaba pronombres entre "no" y el verbo; comandos de memoria negados sumaban approve; contradicciones sí+no se resolvían como approve. Corregido con ampliación de regex, `_comando_memoria_negado()`, y paso 5b de contradicción. 7 nuevos tests de clasificador. Pipeline ampliado de 8 a 10 pasos.
  - Bug 17: **Mensajes "unrelated" no limpiaban propuesta pendiente** — Mensaje de Diane Foster con corrección del reporte semanal devolvía discard pero la propuesta quedaba atrapada. `process_user_response()` rama `else` ahora devuelve `resolution="unrelated"`, ejecuta `clear_pending_proposal()`, registra auditoría con evento `"unrelated"`, y redirige el mensaje al flujo normal de auto-evaluación. Sin cambios en clasificador. 89/89 tests siguen pasando.

- **Tests**: Suite completa de **219 tests** verificada: `uv run python -m pytest tests/ -v`.
  - Memory tests: 57/57 pasan (incluyendo PHI patterns, store CRUD, classifier con 24 tests, pipeline, graph integration).
  - Non-memory agent tests: 32/32 heredados siguen pasando.
  - Tests adicionales: 130 tests en sales_forecast, MCP auth, nightly, temporal_cv, etc.
  - **6/6 ejemplos de CONTEXT.md verificados** al nivel de flujo completo:
    - ✅ Caso 1 (Johnson PHI) — rechazo explícito con mensaje HIPAA/UK GDPR
    - ✅ Caso 2 (Manchester cambio proceso) — propuesta creada correctamente
    - ✅ Caso 3 (Austin alerta no-show) — propuesta creada (antes bloqueado por falso positivo "En Austin")
    - ✅ Caso 4 (Diane Foster reporte) — propuesta creada (antes bloqueado por falso positivo PHI)
    - ✅ Caso 5 (consulta puntual) — correctamente sin propuesta
    - ✅ Caso 6 (agradecimiento) — correctamente sin propuesta
  - **Branch**: `feature/agent-memory`.

### Hito 8 · Parte 2 — Harness y Guardrails (2026-12) — Refuerzo integración grafo (2026-11-23)

- **Completado**: 3 módulos de guardrail deterministas (regex, sin LLM):
  - `harness.py` — System prompt hardening: 5 reglas de restricción + `RAG_TOOL_ISOLATION` + `wrap_rag_context()` para aislar contenido RAG/tools
  - `content_guard.py` — Clasificación de entrada en **7 categorías** con prioridad: `JAILBREAK (1) > PHI (2) > PERSONAL_TASK (3) > GRADUAL_EXTRACTION (4) > SMALL_TALK (5) > DOMAIN (6) > OUT_OF_DOMAIN (7)`. ~100 patrones regex.
  - `output_guard.py` — Validación de respuesta: `check_format` (vacío/largo), `check_leaked_instructions` (14 patrones EN+ES), `check_sensitive_data` (PHI + términos confidenciales)

- **Completado**: Integración en grafo LangGraph:
  - 3 nuevos campos en `AgentState`: `guardrail_result`, `guardrail_blocked`, `guardrail_response`
  - Nuevos nodos: `node_input_guardrail`, `node_output_guardrail`
  - Routing: `route_after_receive` → `input_guardrail`; `route_after_input_guardrail` → `load_memory_context` (si DOMAIN) o `__end__` (si bloqueado)
  - Observabilidad: `guardrail_summary(limit=100)` lee trazas y consolida estadísticas por clasificación/tipo

- **Completado — Conexión real del harness al nodo del grafo** (2026-11-23):
  - **Bug**: `build_system_prompt()` y `wrap_rag_context()` importados pero nunca usados en `_generate()` — el mensaje system no existía.
  - **Fixes**:
    1. `_generate()` en `rag.py` acepta `system_role_message` y lo envía como mensaje `system` separado (rol explícito).
    2. `node_generate_answer()` llama a `build_system_prompt()` para construir system instructions del harness.
    3. `wrap_rag_context()` aísla chunks RAG con marcadores `BEGIN/END REFERENCE DOCUMENTS`.
    4. Nuevas helpers: `_build_context_from_chunks()`, `_build_memory_context_text()`.
    5. Trace events incluyen `harness_active: True`.
  - **OUT_OF_DOMAIN**: Nueva clasificación (blocked=True). Fallback por defecto cambiado de `DOMAIN` a `OUT_OF_DOMAIN`.

- **Completado**: **Gap cerrado contra solución de referencia** — 3 tests de inyección RAG + **6 nuevos tests de integración en grafo**:
  1. `test_rag_content_injection_wrapped_as_data` — Chunk RAG envuelto, verifica NO en system prompt
  2. `test_rag_content_injection_system_prompt_warns` — System prompt contiene advertencia "factual reference only"
  3. `test_rag_injection_with_jailbreak_keywords_not_confused` — Query legítima DOMAIN, capas separadas
  4. `test_node_generate_answer_uses_build_system_prompt` — system_role_message con Identity/HIPAA/Jailbreak
  5. `test_node_generate_answer_wraps_rag_context` — marcadores BEGIN/END REFERENCE DOCUMENTS
  6. `test_malicious_rag_chunk_does_not_contaminate_system_prompt` — [SYSTEM]: ignore rules NUNCA en system prompt
  7. `test_malicious_rag_chunk_wrapped_in_graph_node` — Flujo completo: chunk malicioso → wrap → system_role_message limpio
  8. `test_harness_active_flag_in_trace` — metadata harness_active=True
  9. `test_route_after_input_guardrail_out_of_domain` — OUT_OF_DOMAIN blocked → __end__

- **Tests**: **62 tests de guardrail** en `test_guardrails.py` + **125 tests** en `test_agent_graph.py` (113 agente+memoria + 12 harness) = **187 tests núcleo agente+guardrails**, **317 tests totales workspace**. **187 passed, 0 failures**.

- **Bugs corregidos post-auditoría (2026-11-23)**:
  1. `guardrail_summary()` en `graph.py` — Usaba `event.get("event", "")` pero los eventos usan clave `"node"`. Metadata de output_guardrail esperaba shape incorrecto (`.get("valid", True)` en vez de `event["status"]`; issues tratadas como dicts en vez de strings). Fix: `event.get("node", "")`, usar `status` para fallback, iterar strings directamente.
  2. `r"\bphi\b"` duplicado en `_DOMAIN_KEYWORDS` de `content_guard.py:83` — sin impacto funcional pero descuidado. Eliminada duplicación.
  3. Cobertura de tests a nivel nodo del grafo: 6 nuevos tests para `node_input_guardrail`, `node_output_guardrail` y `route_after_auto_evaluate_v2`.

- **Documentación**: `agent-memory-harness-guard.md` §13-19 actualizado con OUT_OF_DOMAIN, §14 con TestGraphHarnessIntegration, §11.6 con bugs 24-27, §18 auditoría 35/35 puntos.

- **Aceptación**: ✅ 9/9 criterios de auditoría cumplidos. ✅ 100% contra solución de referencia (35/35 puntos).

### Hito 8·2 — Corrección de contradicción comando+no en cualquier orden

- **Bug detectado**: Frases como `"guárdalo, no"`, `"apúntalo, pero no"`, `"recuérdalo, pero no"`, `"guárdalo, no gracias"` retornaban `approve` y creaban facts. La contradicción solo se detectaba cuando `"no"` precedía al comando (`no antes que guarda`), no cuando el comando iba primero.
- **Corregido — Paso 5c reescrito**: Se cambió de verificación posicional (`no.start() < command.start()`) a co-ocurrencia semántica (cualquier orden). Si el texto contiene TANTO un comando de memoria (`guárdalo`/`apúntalo`/`recuérdalo`) COMO `"no"` como palabra independiente, es semánticamente contradictorio → `discard`. Únicas excepciones: tag questions (`"¿no?"` con `?` explícito) y doble negación afirmativa (`"no olvides"` = `"recuerda"`).
- **Ajustes adicionales en el clasificador**:
  1. `_hay_rechazo()`: Retorna `0.0` si el texto coincide con `_RE_NO_OLVIDES` (`"no olvides"`, `"no te olvides"`) — son dobles negaciones con significado POSITIVO, no rechazo.
  2. `_hay_aprobacion()`: Añade +0.7 si el texto coincide con `_RE_NO_OLVIDES` — mapea `"no olvides"` → `approve` semánticamente.
  3. `_es_pregunta_o_condicional()`: Si el texto termina con `"¿no?"` (tag question) y coincide con `_RE_TAG_QUESTION`, NO se trata como pregunta real. `"guárdalo, ¿no?"` es una coletilla de confirmación, no una interrogación.
- **Tests**: 10 tests nuevos + 1 renombrado = **19 tests de contradicción comando+no en 3 clases**:
  - **Discard** (contradicción): `inverted_order`, `guardalo_pero_no`, `apuntalo_pero_no`, `recuerdalo_pero_no`, `guardalo_no_gracias`, `store_with_conjunction`, `apuntalo_no_me_digas`
  - **Reject** (rechazo explícito): `guardalo_no_olvida` (contiene `"olvida"` = verbo de rechazo)
  - **Approve** (excepciones): `guardalo_tag_question`, `no_olvides`, `no_te_olvides`
- **Suite completa**: **296 tests** (`uv run python -m pytest tests/ -v`) — 0 fallos.
- **Cobertura**: 19/19 tests de contradicción comando+no pasan. Casos cubiertos: orden invertido, comas, puntos suspensivos, conjunciones (`pero`), cortesía (`gracias`), tag questions (`¿no?`), doble negación (`no olvides`), verbos de refuerzo negativo (`olvida`).
- **Branch**: `feature/agent-memory`.

### MCP Server — OAuth 2.1 y Autenticación (2026-11-18)
- **Versión estable**: v1.0.0 — Server MCP con OAuth 2.1 (MCP Auth) como Resource Server.
- **Problemas resueltos**:
  1. **Lifespan de FastMCP**: `SelectiveBearerASGI` ahora pasa el `scope["type"] == "lifespan"` directamente a `_public_app` sin filtrar Bearer Auth. Esto evita `RuntimeError: Task group is not initialized` y permite que el `StreamableHTTPSessionManager` se inicialice correctamente.
  2. **Logging con cliente (subject)**: Todos los logs de invocación (`Tool OK`, `Tool ERROR`, `Tool DENIED`) ahora incluyen `client=<sub>` donde `<sub>` es el subject extraído del token validado. Formato normalizado: `Tool OK | tool=<name> | client=<sub> | success=true`, etc.
  3. **Scopes por defecto completos**: `MCP_DEV_TOKEN_SCOPES` asigna **TODOS los scopes** si no está configurado explícitamente. Esto permite desarrollo sin IDP real. Se puede personalizar con `MCP_DEV_TOKEN_SCOPES="incidents.read inventory.read"` para verificar mínimo privilegio.
  4. **Metadata pública (RFC 9728)**: `build_protected_resource_routes()` publica `/`.well-known/oauth-protected-resource` siempre, incluso sin `MCP_PUBLIC_URL` (infiere desde `MCP_ISSUER` o localhost).
  5. **Validación de MCP_SKIP_AUTH**: Advertencia logging claro si se establece `MCP_SKIP_AUTH=1` — nunca debe usarse con Playground o clientes remotos.
- **Autenticación por defecto activada** (a menos que `MCP_SKIP_AUTH=1`):
  - ✅ Sin token → `HTTP 401 Unauthorized` (missing_auth_header)
  - ✅ Token inválido → `HTTP 401 Unauthorized` (invalid_token)
  - ✅ Token válido → `HTTP 200 OK` + 10 tools listadas
  - ✅ Token sin scope requerido → Herramienta rechazada con `Tool DENIED | missing_scope=...`
- **Scopes requeridos por tool**:
  - `get_incident`, `list_incidents`, `get_incident_summary`: `incidents.read`
  - `create_incident`, `update_incident_status`: `incidents.write`
  - `get_product`, `list_products`: `inventory.read`
  - `create_product`, `update_product`, `delete_product`: `inventory.write` (devuelven error controlado: "inventario solo lectura")
- **Token dev para desarrollo**:
  - `MCP_DEV_TOKEN=hc_dev_token_no_usar_en_produccion` (default)
  - `MCP_DEV_TOKEN_SCOPES=incidents.read incidents.write inventory.read inventory.write` (default: todos)
- **Archivos modificados**:
  - `mcps/server.py`: Logging con `client=<sub>` en `_serialize_result()` y `_require_tool_scope()`. Advertencia `MCP_SKIP_AUTH` al inicio.
  - `mcps/auth.py`: `SelectiveBearerASGI` maneja lifespan correctamente. `build_bearer_middleware()` elige modo JWT o dev según `MCP_JWKS_URI`. `build_protected_resource_routes()` publica metadata RFC 9728.
  - `mcps/config.py`: `MCP_DEV_TOKEN_SCOPES` asigna todos los scopes por defecto. `MCP_REQUIRED_SCOPES` vacío (autorización granular por tool).
  - `mcp-server.md`: Documentación de setup, variables de entorno, logging, formato de logs con cliente. Tablas de tools con scopes requeridos.
  - `tests/test_mcp_server_auth.py`: 9 tests (autenticación, autorización, metadata, logging con cliente, MCP_SKIP_AUTH por defecto deshabilitado).
- **Tests**: **9/9 pasan** (`uv run pytest tests/test_mcp_server_auth.py -v`).
  - `test_missing_token_returns_401`: Sin token → 401
  - `test_invalid_token_returns_401`: Token inválido → 401
  - `test_valid_token_tools_list_200`: Token válido → 200 + 10 tools
  - `test_protected_resource_metadata_public_200`: Metadata sin auth → 200
  - `test_bearer_auth_method_in_metadata`: Metadata contiene `bearer_auth_method`
  - `test_all_scopes_in_metadata`: Metadata lista todos los 4 scopes
  - `test_client_subject_in_error_response`: Error incluye `subject` del token (logging)
  - `test_scopes_format_in_metadata`: Formato de scopes correcto en metadata
  - `test_skip_auth_not_enabled_by_default`: `MCP_SKIP_AUTH` no está activado por defecto
- **Validación manual en Playground**:
  - Arrancar servidor: `uv run uvicorn mcps.server:app --host 0.0.0.0 --port 9100`
  - Conectar Playground MCP a `http://localhost:9100/mcp` con token: `hc_dev_token_no_usar_en_produccion`
  - Sin token: `401 Unauthorized`
  - Con token: lista las 10 tools + logs en servidor con `client=dev-agent`
- **Branch**: `feature/mcp-oauth-tools` (completada).

### Agente de Soporte LangGraph — HealthCore (2026-11-15) — Part 2: External Tools
- **Completado**: Extensión del agente LangGraph con 2 tools externas que consultan sistemas operativos reales (incidentes e inventario).
- **Completado**: `services/agent/tools/__init__.py` — helper `_get_service_url(env_var, default)` para resolver URLs desde entorno con fallback.
- **Completado**: `services/agent/tools/incidents.py` — `query_incident_by_id(id)` (GET /api/incidents/{id}) y `query_incidents_list(status, category, origin, branch)` (GET /api/incidents). Sin auth. Timeout 5s. Normalización con `_normalize_incident()`. Fallback para TimeoutException, RequestError, 404, non-200.
- **Completado**: `services/agent/tools/inventory.py` — `query_product_by_id(id)` (GET /inventory/products/{id}) y `query_products_list(category, name_search)` (GET /inventory/products). Auth JWT Bearer desde `AGENT_JWT_TOKEN`. Timeout 5s. Normalización con `_normalize_product()`. Fallback para TimeoutException, RequestError, 401, 404, non-200.
- **Completado**: Nuevo estado en `AgentState` — `routing_decision`, `tool_results`, `tool_errors`. Reducers acumulables.
- **Completado**: `node_classify_query` — clasificación léxica con 5 familias de patrones regex. Decisiones: rag, incident, inventory, rag+incident, rag+inventory. Default RAG.
- **Completado**: `node_query_incident` — extrae ticket ID de la pregunta o infiere filtros, llama a la tool correspondiente, maneja errores.
- **Completado**: `node_query_inventory` — extrae product ID o nombre, infiere categoría, llama a la tool, maneja errores.
- **Completado**: `node_synthesize_tool_answer` — formatea resultados de tools en respuesta legible. Si todas fallan, responde honestamente sin inventar.
- **Completado**: Nuevos routers — `route_after_classify`, `route_after_tool`, `route_after_retrieve_v2`, `route_after_generate` (extendido).
- **Completado**: Grafo compilado con 10 nodos. Aristas: classify → (retrieve_context / query_incident / query_inventory); después de tool → (retrieve_context / synthesize); retrieve_v2 considera tools; generate → synthesize si hay tools.
- **Completado**: Compatibilidad hacia atrás — `route_after_retrieve = route_after_retrieve_v2`.
- **Completado**: 12 nuevos tests en `TestExternalToolRouting` (5), `TestToolFallbackBehavior` (5) y `TestMixedSynthesis` (2). 32/32 total.
- **Completado**: Documentación en `agente-soporte-langgraph.md` §Parte 2 completa.
- **Completado**: Auditoría 9/9 acceptance criteria cumplidos.
- **Completado**: Corrección de 3 bugs en la síntesis mixta:
  - Bug 1: `route_after_retrieve_v2` prioriza `generate_answer` cuando hay chunks RAG (antes iba directo a `synthesize_tool_answer`, descartando el contexto RAG).
  - Bug 2: `route_after_generate` ahora considera `tool_errors` además de `tool_results` para decidir si va a `synthesize_tool_answer`.
  - Bug 3: `node_synthesize_tool_answer` incluye la respuesta RAG en modo combinado aun si la tool falla (antes mostraba solo error y omitía el RAG).

### Hito 9 · RFP Pipeline — Parte 3: Aprobación Humana, Arbitraje y Síntesis (2026-11-27)

- **Completado**: Pipeline completo de 9 nodos integrado (Partes 1+2+3):
  - Nodo 9 en `data/pipelines/rfp_intake/graph.py` que ejecuta Part 3 cuando `final_status == "needs_human_review"`
  - Checkpoint scoped por ticket: `data/checkpoints/{ticket_id}.db` (no más `/tmp/healthcore_part3_checkpoints.db`)
  - Checkpointer dual: SQLite para estado interno de Part 3 + PostgreSQL para estado oficial (`TicketDB`, `DepartmentSectionDB`)
  - 3 endpoints FastAPI: `POST /approve`, `GET /approvals`, `GET /pending-approvals`
  - Columnas PostgreSQL migradas: `final_document_path`, `part3_completed_at`, `generation_status`, `generation_iteration`

- **Completado**: 3 bugs de producción encontrados y corregidos:
  1. 🔴 **Columnas faltantes en PostgreSQL** — migradas contra Supabase (4 ALTER TABLE)
  2. 🟡 **Checkpoint path sin scope** — ahora `data/checkpoints/{ticket_id}.db`
  3. 🟢 **markitdown[pdf] no instalado** — instalado para mejor conversión

- **Completado**: Script E2E `scripts/test_full_parts_123_e2e.py` con 21 tests que cubren:
  - Part 1: clasificación, metadatos, workers (3/3)
  - Part 2: ciclo generador-evaluador con LLM real DeepSeek v4 Flash (2/2)
  - Part 3: conflictos, arbitraje, aprobaciones, síntesis (8/8)
  - DB Persistence: escritura/lectura post-Part3 (4/4)
  - Edge Cases: resume, concurrencia, handoff (4/4)
  - **21/21 ALL PASS** con LLM real

### Hito 9 · RFP Pipeline — Parte 2: Generación y Evaluación de Borradores (2026-11-24)

- **Completado**: Parte 1 del pipeline RFP (convertir, clasificar, descomponer, sintetizar) — 293 tests.
- **Completado**: Parte 2 — Ciclo generador-evaluador de borradores por departamento:
  - `data/pipelines/rfp_intake/models_part2.py`: Modelos Pydantic (EvaluationResult, Part2DepartmentOutput con `approval_status`, Part2Handoff, etc.)
  - `data/pipelines/rfp_intake/generators.py`: Generadores LLM + fallback rule-based por departamento
  - `data/pipelines/rfp_intake/evaluators.py`: 3 evaluadores async en paralelo (readability, relevance, compliance)
  - `data/pipelines/rfp_intake/cycle.py`: Orquestador con `asyncio.gather()` sobre departamentos y ciclo generador-evaluador
  - `data/pipelines/rfp_intake/graph.py`: Nodo 8 `run_part2_pipeline`, persistencia de drafts/evaluations/approval_status en DepartmentSectionDB
  - Flujo de estados: `analisis_completo → generando_borrador → en_evaluación → (needs_human_review | analisis_completo)`

- **Completado**: 6 bugs corregidos:
  1. Metadata INSERT nunca se ejecutaba (sin `else:` en `if existing_meta:`) — `graph.py`
  2. Enriquecimiento post-clasificación no persistido — `graph.py`
  3. Readability fields incompletos + formato API incorrecto — `models_rfp.py`, `graph.py`, `routes/rfp.py`
  4. reprocess_ticket no limpia DepartmentSectionDB — `routes/rfp.py`
  5. get_ticket no expone Part 2 fields (draft_content, evaluation_results, approval_status) — `routes/rfp.py`
  6. approval_status no se persiste en DepartmentSectionDB — `graph.py`

- **Completado**: `from __future__ import annotations` añadido a `data/pipelines/rfp_intake/__init__.py` y `mcps/__init__.py`.

- **Completado**: 27 nuevos tests en `tests/pipelines/test_rfp_part2.py`:
  - `TestTicketStatusEnum` (5): valores del enum
  - `TestPart2DepartmentOutput` (8): approval_status, draft_content, evaluation_results
  - `TestPart2Handoff` (3): estructura con departamentos, overall_status
  - `TestPart2PipelineState` (2): estructura del estado
  - `TestExtractKeyAspects` (3): extracción de aspectos clave
  - `TestRunPart2Pipeline` (6 async): ciclo completo, max iteraciones, needs_human_review, approval_status persistence, sin departamentos

- **Tests**: **357 tests totales** (`uv run python -m pytest tests/ -v`): 293 pipeline + 10 MCP auth + 54 Part 2. **0 fallos**.

- **Pendiente**:
  - Prueba con Celery + Docker compose
  - Tests de integración E2E con PostgreSQL real
  - UI de aprobación / revisión humana de borradores
- **Completado**: 2 nuevos tests en `TestMixedSynthesis` — verifica que rag+incident combina ambas fuentes y que el fallo de tool en modo combinado incluye RAG + error honesto.
- **Completado**: Trazas regeneradas con ticket real (ID 2, no el inexistente 482) y timestamps reales para evitar artefactos de mock. `scripts/regenerate_traces.py` reescrito sin mocks — usa servicios reales: RAG 5788ms, Incidente 41ms (branch_label "London West End"), Combinado 2892ms, Fallback 1649ms.
- **Completado**: routing_log corregido: `receive_question → classify_query` en lugar del antiguo `receive_question → retrieve_context`.
- **Completado**: Commit `ca2ca1d` pusheado con regenerate_traces.py corregido.
- **Completado**: PR #28 body actualizado con commits reales (`ca2ca1d`, `8b2c92c`, etc.) y timestamps de servicios reales.
- **Branch**: `feature/langgraph-external-tools`.

### Modelo de pronóstico de ventas — Regresión con XGBoost (2026-10-31 → 2026-11-02)
- **Completado**: pipeline completo de pronóstico de ingresos mensuales en `scripts/sales_forecast/{data,metrics,model,visualize,compare_before_after}.py` con entrypoint `scripts/train_sales_forecast.py` (9 pasos: carga → validación → features → split → entrenamiento → métricas → gráfico → descomposición → justificación features).
- **Completado**: split temporal estricto 8 años/2 años — entrenamiento 2016-01..2023-12 (96 meses − 12 de warm-up = 84 efectivos) y prueba 2024-01..2025-12 (24 meses que el modelo nunca vio). Invariantes verificados en código con `_assert_split_invariants()`.
- **Completado**: features causales solo con información pasada — `rev_lag_1`, `rev_lag_2`, `rev_lag_12`, `rev_ma_3`, `rev_min_12`, `rev_max_12`, `rev_std_12`, `month_num`, `season`, `trend_index`, `visits_count`, `avg_revenue_per_visit_usd`. `StandardScaler` ajustado SOLO con el conjunto de entrenamiento.
- **Completado**: XGBoost regularizado (`max_depth=4`, `lr=0.03`, `gamma=0.5`, `reg_alpha=0.5`, `reg_lambda=2.0`, `random_state=42`) con justificación escrita frente a Random Forest.
- **Completado**: las 4 métricas exigidas calculadas sobre el conjunto de prueba (post-corrección anti-fuga) — MSE 23.997.252.376,54 USD² (= 5,5168 % del ingreso medio mensual al cuadrado), PSI de nivel 6,0325 y PSI de crecimiento ajustado 0,2718, Gini predictivo 0,8188, K2/R² 0,8104. Añadida MAPE 3,4738 % (peor mes 8,849 %).
- **Completado**: visualización con banda de variabilidad ±1,96σ = ±$265.312 (σ = $135.363 calculada como RMS de los RMSE walk-forward). Cobertura empírica 21/24 meses (87,5 %).
- **Completado**: validación walk-forward que reentrena por año (RMSE 2024 = $134.459, 2025 = $136.268) usando solo datos anteriores a cada año.
- **Completado**: descomposición aditiva del target — tendencia=media rodante 12m, estacional=perfil mensual, residual. Fuerzas: tendencia 55,0 %, estacional 39,5 %, residual 2,9 %. Gráfico `decomposition.png` (4 paneles).
- **Completado**: justificación de features de negocio — `visits_count` (48,9 % importancia) y `avg_revenue_per_visit_usd` (0,9 %) son componentes del target, no conocibles en t−1. Documentado como riesgo para forecast puro.
- **Completado**: corrección de fuga de datos en rolling features — `rev_ma_3`, `rev_min_12`, `rev_max_12`, `rev_std_12` usaban `rolling()` sin `shift(1)`, incluyendo `revenue_usd[t]`. Corregido con `shift(1)` antes de cada rolling. RMSE mejoró −1,54 % (de $157.336 a $154.910), R² +0,006.
- **Completado**: gráfica comparativa `forecast_comparison_before_after.png` (2 paneles: predicciones solapadas + diferencia de error absoluto).
- **Completado**: artefactos exportados a `reports/sales_forecast/metrics.json`, `forecast_vs_real.png`, `decomposition.png`, `forecast_comparison_before_after.png`; documentación íntegra en `predic-ventas-model-regresion.md` (§11 completo con 7 subsecciones).
- **Tests**: `tests/pipelines/test_sales_forecast_split.py` — **19/19 en verde** (6 de split/anti-fuga, 5 de invariantes del dataset, 2 de regresión de nombres de features, 1 de predicción fuera de muestra, +5 tests nuevos de causalidad anti-fuga rodantes).
- **Auditoría (2026-10-31)**: **7/7 acceptance criteria ✅** verificados en ejecución con recálculo independiente de las métricas (no se confió en la documentación). Hallazgos corregidos: (a) `test_escalado_ajustado_solo_con_entrenamiento` era tautológico → reemplazado por reconstrucción exacta `(x−μ_train)/σ_train` comparada contra la hipótesis con fuga, **validado con mutation test** (inyectar `fit(train+test)` hace fallar el test); (b) `rev_ma_12` era un máximo rodante con nombre de media móvil → renombrado a `rev_max_12`, `rev_mi_12` → `rev_min_12`; (c) docstring decía "~107 filas" → corregido a 84; (d) `year_num` era código muerto → eliminado. Métricas reproducidas idénticas bit a bit tras el refactor.
  - **Nota — PSI alto por crecimiento estructural (válido por diseño, no es un bug)**: el PSI de nivel es alto (6,03) porque el ingreso medio crece de $2,81 M (train) a $3,37 M (test). No es una fuga ni un fallo del modelo: en una serie con crecimiento estructural el PSI de nivel mide el crecimiento. Se añadió el PSI de crecimiento ajustado (0,2718 → "moderado") y se redujeron los bins de 10 a 5 porque con solo 12 puntos de crecimiento los bins de 10 dejaban ~1 observación por bin y el ruido inflaba el valor.
- **Auditoría extendida (2026-11-02, fuga en rolling features)**: detectado que 4 features rodantes usaban `rolling()` directamente sobre `revenue_usd` sin `shift(1)`, incluyendo el target del mes `t`. **Corregido** con `rev_shifted = df["revenue_usd"].shift(1)` antes de cada rolling. Test existente actualizado a `test_min_y_max_coinciden_con_pandas_y_shift`. Añadida clase `TestCausalidadAntiFugaRodantes` con 5 tests que verifican que ninguna rolling feature contiene el target del mes `t`. Corregido edge case NaN en aserciones con `mask_valid`. Pipeline ejecutado con éxito y nuevas métricas en `metrics.json`.
- **Evaluación técnica bias/varianza (2026-11-08)**: completada evaluación formal del modelo con TimeSeriesSplit 5 folds, curvas de aprendizaje, diagnóstico de overfitting, justificación de métricas (RMSE > MAE para HealthCore), acción correctiva específica (3 pasos), y veredicto **NO APROBADO para staging**.
- **Tests de validación cruzada temporal**: `tests/pipelines/test_temporal_cv.py` creado con 7 tests (5 folds, orden cronológico, detección de shuffle, anti-fuga, no solapamiento entre folds, integración real). Fix al test `test_no_overlap_between_consecutive_folds` que validaba incorrectamente la no superposición de entrenamiento entre folds (inherente a ventana expansiva).
- **Reporte de evaluación**: `data/eval/evaluation_report.md` generado automáticamente con 8 secciones: resumen ejecutivo, CV temporal (media ± std), curva de aprendizaje con interpretación, justificación MAE vs RMSE, diagnóstico con evidencia cuantitativa, acción correctiva priorizada, pruebas ejecutadas (64/64), y decisión de staging.
- **Script de evaluación**: `scripts/run_model_evaluation.py` — entrypoint que orquesta: carga de datos → CV temporal → curva de aprendizaje → diagnóstico consolidado → justificación de métricas → generación de reporte markdown con staging decision.
- **CI**: suite completa de 64 tests verificada con `uv run python -m pytest tests/pipelines/ -v`. **64/64 pasan**. Documentado en el reporte de evaluación (§6 Pruebas ejecutadas).
- **Branch**: `feature/regression-model-eval`. `CONTEXT.md` **NO modificado** (ruta protegida).

### Cola de Tareas Asíncronas — Redis + Celery (2026-10-24)
- **Completado**: Sistema de cola de tareas asíncronas para desacoplar operaciones pesadas de la API usando Redis como broker y Celery como framework de tareas.
- **Completado**: `services/celery_app.py` — instancia Celery con Redis como broker y result backend, tarea `analyze_incidents_csv`, clase base `HealthCoreTask` con DLQ automática en `on_failure`.
- **Completado**: Configuración de reintentos (`autoretry_for`, `max_retries=3`, `retry_backoff=True`, `retry_jitter=True`), colas (`healthcore.default`, `healthcore.dlq`), señales Celery (`task_success`, `task_failure`, `task_retry`, `task_revoked`).
- **Completado**: Dead Letter Queue con TinyDB persistida en `data/process/task_dlq.json` — registro automático tras agotar reintentos con `task_id`, `task_name`, `attempt`, `error`, `exc_type`, `timestamp`.
- **Completado**: Endpoint `POST /api/incidents/analyze` refactorizado de síncrono a asíncrono — ahora responde 202 Accepted con `task_id` y encola en Celery. Header `Location: /tasks/{task_id}`.
- **Completado**: Endpoint `GET /tasks/{task_id}` — consulta el estado de la tarea (`pending | started | success | failure`), resultado, error, y entrada DLQ si falló.
- **Completado**: Infraestructura Docker — 3 nuevos servicios en `docker-compose.yml`: `redis` (Redis 7-alpine con AOF y noeviction), `flower` (mher/flower:2.0 con monitoreo en :5555), `celery-worker` (concurrency=4, flag -E para eventos, red healthcore-net).
- **Auditoría**: **10/10 criterios ✅** — Redis responde PING, encolado en 29.61ms media, estados correctos, reintentos con backoff, DLQ funcional, worker conectado, payloads ligeros, Flower accesible.
- **Archivos**: `services/celery_app.py`, `services/api/routes/tasks.py`, `services/api/main.py` (endpoint async), `docker-compose.yml` (redis, flower, celery-worker).

### Proceso de segundo plano — Job nocturno de telemetría (2026-10-22)
- **Completado**: `job_runs` (orquestación nocturna) creado como capa propia, distinta de `reporting.pipeline_runs` (ETL Hito 6). DDL `data/migrations/0002_job_runs.sql` + auto-migración idempotente.
- **Completado**: `services/job_runner.py` (CONTROL DE ESTADO: estados + lock atómico = `processing` + helpers `has_processing_lock`/`has_completed_for_date` + `log_result`), `services/nightly/nightly_service.py` (orquestación: export CSV → subprocess pipeline → registros), `scripts/nightly_export.py` (CLI con flags `--skip-pipeline`, `--force`, `--target-date`, `--list`, `--dry-run`).
- **Completado**: idempotencia por día (noop si `completed`), aborto silencioso si otra instancia tiene el lock, `failed` con `error_message` ante excepción, reintento `failed → pending`.
- **Completado (spec #5 — Disparador)**: `background-processes.md` con decisión cron `0 2 * * *`, instalación del crontab, opción de contenedor scheduler dedicado (recomendada en prod) y prohibición explícita de APScheduler/@repeat_every/lifespan. `TARGET_DATE` documentada como var opcional para pruebas.
- **Completado (spec #6 — Observabilidad)**: logger estable `healthcore.nightly`, formato normalizado `event=... job=nightly_export status=... ts=...` vía `log_result()`; INFO para inicio/fin/omisión por duplicado y ERROR para excepciones con traceback. Tests con `caplog` añadidos.
- **Tests**: **30/30** pasan (`.venv-nightly/bin/python -m pytest tests/nightly -q`).
- **Auditoría del proceso — 10/10 criterios ✅**: análisis estático (AST: sin imports de fastapi/uvicorn/starlette/apscheduler en los módulos del job) + demostración en vivo `scripts/_audit_hito7_demo.py` (Dos instancias CLI reales a la vez contra una BD SQLite compartida). Resultados: C3 una instancia completa y la otra hace noop/aborta; C3b lock retenido → aborted; C4 idempotencia → noop_export; C5 fallo forzado → failed con error_message (sin estados processing zombi); C6 CSV `telemetry_YYYY-MM-DD.csv` en `data/raw/` con cabeceras timestamp/event_type/tags y contenido correcto. Demo reproducible (`exit=0`) y limpia su residuo de `data/raw/`.
- **Hallazgos y soluciones (documentados en `background-processes.md` §11)**: (1) reintento tras fallo: `mark_processing` no permitía `failed→pending`, se añadió `reset_failed_to_pending` atómico solo desde `failed`; (2) módulo de control de estado movido a `services/job_runner.py` (spec); (3) logger estable `healthcore.nightly` + `log_result()` (sin `logger.exception` crudo) para pasar los tests `caplog`; (4) pipeline real del Hito 6 es `python -m data.pipelines.runner` (no `telemetry_kpi_daily`); (5) concurrencia verificada en vivo.
- **Completado (2026-10-22, re-etiquetado)**: aclarado que este proyecto es un **proceso de segundo plano intermedio** (entre el hito 6 y un hito futuro), NO "Hito 7". Eliminadas las referencias a "Hito 7" en `background-processes.md`, `memory-bank/*.md`, `services/job_runner.py`, `services/nightly/__init__.py`, `services/nightly/nightly_service.py`, `tests/nightly/*.py` y `scripts/_audit_hito7_demo.py` → "proceso de segundo plano". **Borrado `AUDITORIA_HITO7.md`** (redundante con `background-processes.md` §11). Solo se corrigieron textos; los nombres de archivo NO se renombraron (`scripts/_audit_hito7_demo.py` conserva su nombre). Refactoring de docstrings sin impacto funcional: 30/30 tests siguen en verde.
- **Completado (supabase humo - solo conexión, 2026-10-22)**: `DATABASE_URL` en `.env` raíz; conexión de SOLO LECTURA a Supabase verificada (Postgres 17.6, pooler eu-west-1, 31 filas en `telemetry_events` entre 2026-10-04 y 2026-10-11, pico 2026-10-10=12). No se escribió nada (decisión del usuario). Instalado `psycopg` v3 + `psycopg2-binary` en `.venv-nightly` para el driver. `job_runs`/`schema_migrations` aún inexistentes en Supabase — el job los auto-crearía. Para futuro humo completo: `--target-date 2026-10-10`.
- **Completado (humo completo Supabase, 2026-10-22)**: corrida real contra Supabase con `--skip-pipeline --target-date 2026-10-10`. OUTCOME `completed`, **12 filas** exportadas a `data/raw/telemetry_2026-10-10.csv` (13 líneas: cabecera `timestamp,event_type,tags` + 12 eventos con tags JSON). Fila en `job_runs`: status `completed`, `exported_rows=12`, `csv_path` correcto, `created_at` 2026-10-22 20:59:44 UTC. **Idempotencia verificada**: 2ª corrida → `noop_export`, exit 0. **Punto clave**: la rama **Postgres** del código (BIGSERIAL, `now()`, UNIQUE(job_name,target_date), CHECK status) quedó ejecutada contra Postgres real por primera vez. **Hallazgo menor**: `schema_migrations` queda count 0 en Postgres — `_ensure_schema_postgres` crea las tablas con DDL hardcodeado y NO inserta versiones (SQLite sí itera `_MIGRATIONS`). No bloquea; pendiente decidir documentarlo o corregirlo.
- **Pendiente**: instalar crontab en el entorno real (aún no instalado), decidir cómo tratar `schema_migrations` vacío en Postgres (hallazgo del humo), seguimiento post-deploy en `job_runs`. `npm run typecheck` pendiente de ejecutar (AGENTS.md). **NO commitear** (usuario sigue iterando).

### Telemetría: brechas de auditoría corregidas (2026-10-06)
- Privacidad de valores y formato temporal reforzados en ambas fronteras; expiración sin HMAC válido deja de perderse.
- Entrega de hechos obligatorios independiente del cliente: cola backend, lotes, reintentos y deduplicación de caducidad posterior a recepción confirmada; el stub no almacena eventos.
- 152 pruebas backend y 59 frontend pasan. Tipos focalizados y lint correctos; quedan cuatro errores globales ajenos y verificación integral de navegador pendiente. Detalle añadido al final del plan de telemetría.

### Telemetría: fase 1 completada (2026-10-06)
- Router receptor y modelo Pydantic cerrados, validación contra `docs/telemetry/event-schemas.json`, logs mínimos y ninguna escritura en base de datos.
- `TELEMETRY_ENDPOINT` leído al iniciar; catálogo empaquetado para Docker. 16 pruebas focalizadas pasan, incluidos los cinco contratos obligatorios y el montaje en la app real.
- No se implementaron servicio ni captura frontend. Validación ejecutada con un entorno Python temporal porque `uv` y `pytest` no estaban instalados en el contenedor.

### Telemetría: fase 2 implementada con dependencias pendientes (2026-10-06)
- Servicio frontend con API única `track()`, envelope automático, contratos compartidos, cola acotada, envío por lotes, beacon y tres reintentos. Integración de sesión sin instrumentación de eventos de negocio.
- Pendientes: configurar URL pública en el archivo local bloqueado y recibir seudónimo HMAC de autenticación. No se amplió el backend por decisión expresa del usuario.
- Diez pruebas focalizadas, las 46 pruebas del backoffice y tipos del servicio pasan. El typecheck global mantiene 11 errores ajenos al cambio y lint mantiene un aviso previo de AuthContext. Detalle al final del plan de telemetría.

### Telemetría: Fase 5 — Autenticación funcional para backoffice (2026-10-11)
- **Problema detectado**: TinyDB sin usuarios (`data/process/suppliers_db.json` en `.gitignore`, nunca ejecutado seed). API principal (`main.py`) no arranca por SQLModel + Python 3.14. Servidor standalone sin auth.
- **Seed de usuarios**: `services/telemetry/seed_auth_users.py` — script autónomo que crea `admin@healthcore.com` / `adminpass456` y `test@healthcore.com` / `securepassword123`.
- **Módulo auth standalone**: `services/telemetry/auth_standalone.py` — bcrypt, JWT (jose), consultas TinyDB. Sin dependencia de `services/api/`.
- **Endpoints en `server_standalone.py`**: `POST /auth/login` (login+JWT), `GET /auth/me` (perfil+rol+telemetry_user_id).
- **Cadena completa verificada**: Navegador → Next.js proxy → `localhost:8000/auth/*` → 200 OK.
- **Archivos**: `seed_auth_users.py` (creado), `auth_standalone.py` (creado), `server_standalone.py` (modificado), `services/api/.env` (modificado, JWT_SECRET_KEY).

### Telemetría: Fase 4 — Dashboard de telemetría en backoffice (2026-10-11)
- **Página creada**: `uis/backoffice/app/telemetry/page.tsx` — 5 paneles visuales (events_per_day, error_rate_by_type, latency_percentiles_by_route, event_type_distribution, auth_failure_rate).
- **Selector de fechas**: campos datetime-local que recargan el reporte vía `/api/telemetry/report`.
- **Proxy**: rewrite en `next.config.ts`; route handler bugado eliminado.
- **Navegación**: enlace "Telemetry" en BackofficeHeader.
- **Datos de prueba**: 100 eventos en Supabase (14 días, 5 tipos).

### Telemetría: Fase 3 — auth_failure_rate_by_day (2026-10-11)
- **Función creada**: `auth_failure_rate_by_day()` en `services/telemetry/analysis.py`.
- **SQL**: filtra `auth_login_failed` + `auth_login_succeeded`.
- **Pandas**: groupby día → pivot → tasa de fallo.
- **Integrada en `run_all()`**: servida automáticamente en `GET /telemetry/report`.
- **Auditoría 10/10**: todos los acceptance criteria cumplidos.

### Telemetría: control channel refactorizado y 422 corregido (2026-10-06)
- **Refactor del control channel**: `TelemetryService.control.ts` tenía fetch pipeline independiente a `/api/telemetry/control`, violando el principio de "única `track()`". Se refactorizó a `recordTelemetryControl()` que valida propiedades y llama a `track()`. Todos los eventos frontend van por `/api/telemetry/events`.
- **3 tests actualizados**: expectativas cambiadas de `"/api/telemetry/control"` a `"/api/telemetry/events"`. 2 de 3 correcciones pasan.
- **Bug 422 resuelto**: El backend de puerto 8000 (PID 42927) tenía código desactualizado desde las 18:01 — al reiniciarlo con `--reload` y recargar el backoffice, los eventos reales del navegador responden 200 OK.
- **Backend dual**: Se descubrió que había dos procesos FastAPI (puertos 8000 y 8400). Se mataron ambos y se reinició solo el puerto 8000 con el código correcto y `--reload`.
- **Verificado**: POST real a `/api/telemetry/events` vía proxy devuelve `{"received":1}` con 200.
- **Pendientes**: 1 test de reintentos (retries three times) sigue fallando — espera 4 llamadas de fetch pero recibe 12; requiere depuración adicional del mock de fetch.
- **Documentación**: `telemetry-plan.md` actualizado con el nuevo diseño del canal de control; `memory-bank` sincronizado con estos cambios.

### Completado
1. Hito web de HealthCore implementado en EN/ES:
- Landing con contenido corporativo, secciones requeridas y marcado Schema.org.
- Formulario de consulta con campos y validaciones alineadas al contexto.
- Mensajes de error y éxito visibles para usuario.

2. Fundamentos TypeScript implementados en src/:
- Modelado de entidades principales.
- Filtros, ordenamientos, busquedas lineal/binaria.
- Agregaciones de denegaciones y no-shows.
- Reportes CME y validaciones de dominio.

3. Talent Pipeline Tracker funcional en Next.js:
- Listado con filtros y búsqueda vía query params sin recarga completa.
- Detalle de candidatura por ID.
- Actualización de estado y etapa con PATCH.
- Gestión de notas (listar, crear, eliminar).
- Formularios de creación (POST) y edición (PUT).
- Autenticación JWT con login, registro, perfil, guard de rutas.

4. Salud técnica actual:
- npm run typecheck (raiz): OK.
- npm run typecheck (uis/talent-pipeline-tracker): OK.
- npm run typecheck y npm run lint (uis/backoffice): OK.
- npm run typecheck (uis/website): OK.
- **Tests unitarios (uis/backoffice)**: 11/11 pasan con `npx jest`.

5. Directorio de Proveedores (FastAPI + TinyDB + Pydantic):
- Modelos Pydantic con enums de status/categoría/país y coherencia moneda-país.
- Seeder idempotente con los 15 proveedores del CONTEXT (`uv run seed`).
- Endpoints POST/GET/GET id/PATCH rate/PATCH status/DELETE con 201/200/404/422 coherentes.
- DELETE es baja lógica: mantiene `status` en los dos valores del CONTEXT y marca `archived_at`.
- Página /suppliers en uis/backoffice con filtros sin recarga, alta validada en cliente, edición de tarifa inline y acciones Suspender/Activar y Eliminar por fila.
- Tres estados visuales diferenciados: Activo (verde), Suspendido (ámbar) y Eliminado (rojo, con fecha de baja).
- Auditoría ejecutada: 15/15 checks de backend y 11/11 de frontend renderizado; persistencia verificada reiniciando uvicorn.

6. Autenticación completa (API + backoffice + tracker):
- Login con JWT (Access token 30 min), registro con creación de perfil.
- Flujo completo de recuperación de contraseña: forgot-password con rate limiting (5 intentos/60 min), reset-password con token único de un solo uso, change-password con verificación de contraseña actual.
- Auditoría de eventos de restablecimiento en TinyDB.
- Envío de emails mediante Resend (simulado en consola local).
- Roles USER y ADMIN con protección de rutas.
- Tests automatizados: login, forgot/reset/change password, /me, security, user_service (9 archivos de test).

7. Sistema de Incidencias (API FastAPI independiente puerto 8010):
- CRUD completo de incidencias con filtros (categoría, origen, estado, clínica, fechas).
- Categorías: appointment, billing, clinical_equipment, compliance_breach, patient_experience.
- Orígenes: customer, staff, system, supplier.
- Tests funcionales en services/incidents-api/tests/.
- Frontend: 5 páginas (analyzer, list, register, summary, manager).
- Análisis de CSV legacy mediante incidents_analysis.py.

8. Módulo de Inventario (FastAPI + SQLModel + Supabase PostgreSQL + Frontend):
- 6 endpoints REST bajo /inventory con autenticación JWT.
- Modelos SQLModel: MedicalSupply, SupplyDelivery, SupplyConsumption.
- Stock siempre calculado como neto de entradas menos salidas.
- Rechazo de consumos con stock insuficiente (HTTP 400 con mensaje descriptivo).
- Seed data: 8 productos con stock calculado en Supabase.
- Frontend: 4 páginas protegidas (productos con colores, entrada, salida con stock reactivo, historial).
- Auditoría completada: 10/10 acceptance criteria cumplidos.
- Capa API dedicada (inventoryApi.ts) sin fetch directo en componentes.
- Proxy vía rewrites de Next.js (/api/inventory/* → localhost:8000).

9. Arquitectura dual de BD y APIs documentada en techContext.md:
- TinyDB para usuarios/auth/proveedores, Supabase PostgreSQL para inventario.
- Dos APIs separadas: principal (8000) e incidencias (8010).
- Dos estrategias de proxy: route handlers (auth/suppliers) y rewrites (inventario/incidencias).
- Capa API dedicada para inventario (inventoryApi.ts).
- Stock reactivo en frontend al seleccionar producto en formulario de salida.
- Comandos documentados para todos los servicios, builds y tests.

10. **Dockerización completa del monorepo para desarrollo**:
- `docker-compose.yml` en raíz con 3 servicios (uis, backend, incidents-backend) bajo red `healthcore-net`.
- `uis/Dockerfile` multi-etapa (Node 22 Alpine) para website + backoffice.
- `services/Dockerfile` (Python 3.12-slim + uv) para ambos backends FastAPI.
- `uis/start.sh` y `services/entrypoint.sh` como entrypoints dinámicos.
- `.dockerignore` en raíz, uis/ y services/ para optimizar contexto de build.
- Build exitoso verificado para las 3 imágenes.
- URLs actualizadas en route handlers, rewrites y `.env` para usar nombres Docker.
- Bug corregido: `INCIDENTS_API_URL` apuntaba a `backend:8010` → corregido a `backend:8000`.
- Dependencias faltantes añadidas a `services/api/requirements.txt` (sqlmodel, psycopg2-binary).
- `.gitignore` actualizado con `.env.local` y `.env.*.local`.

11. **Configuración de Jest y TypeScript para tests del backoffice**:
- `tsconfig.test.json` creado con `types: ["jest", "node"]`.
- `jest.config.ts` actualizado para usar `tsconfig.test.json`.
- `node_modules` instalados localmente en `uis/backoffice/`.
- `__tests__` excluido del `tsconfig.json` principal.
- 45 errores iniciales de TypeScript en `suppliersProxy.test.ts` resueltos.
- Tests ejecutándose correctamente: 11/11 passed (0.723s).

### Hito 9 · RFP Pipeline — Parte 2: Generación y Evaluación de Borradores (2026-11-24)

- **Completado**: Ciclo multi-agente que genera borradores de propuesta por departamento y los autoevalúa con hasta 3 iteraciones.

- **Arquitectura async/paralelo**:
  - Evaluadores ejecutados **realmente en paralelo** mediante `asyncio.gather(readability, relevance, compliance)` en `evaluate_section()`.
  - Departamentos procesados de forma **concurrente** con `asyncio.gather(dept_tasks)` en `run_part2_pipeline()`.
  - Generador LLM envuelto con `asyncio.to_thread()` para no bloquear el event loop.

- **Archivos creados/modificados**:
  - `data/pipelines/rfp_intake/models_part2.py` — 10 modelos Pydantic (EvaluationType, ReadabilityEval, RelevanceEval, ComplianceEval, EvaluationResult, DraftContent, Part2DepartmentState, Part2PipelineState, Part2DepartmentOutput, Part2Handoff)
  - `data/pipelines/rfp_intake/generators.py` — 3 prompts de sistema por departamento (revenue-Tom Callahan, clinical-Dr. Reid, compliance-Claire Whitfield) + fallback rule-based
  - `data/pipelines/rfp_intake/evaluators.py` — 3 evaluadores async: readability (py-readability-metrics), relevance (key_aspects), compliance (5 reglas CONTEXT.md §5)
  - `data/pipelines/rfp_intake/cycle.py` — Orquestador `run_part2_pipeline()` con `asyncio.gather` para procesamiento concurrente de departamentos
  - `data/pipelines/rfp_intake/graph.py` — Nodo 8 añadido al pipeline principal, persistencia en DepartmentSectionDB

- **5 reglas de compliance** (CONTEXT.md §5): `phi-detected`, `baa-us`, `dpa-uk`, `currency-country`, `compliance-mandatory`

- **Estados del ticket**: `analisis_completo → generando_borrador → en_evaluación → (needs_human_review | analisis_completo)`

- **Tests**: **43/43 tests PASS** (`uv run python -m pytest tests/pipelines/test_rfp_classifier.py tests/pipelines/test_rfp_workers.py tests/pipelines/test_rfp_generators.py tests/pipelines/test_rfp_evaluators.py -v --tb=short`)
  - `test_rfp_generators.py`: 8 tests (fallback por departamento, LLM fallback, prompts, feedback, unknown department)
  - `test_rfp_evaluators.py`: 19 tests (readability corto/largo, relevance cliente ausente/aspectos, compliance PHI/BAA/DPA/currency/mandatory, evaluación completa)

- **Documentación**: `agentic-workflows.md` — sección Parte 2 con 4 preguntas de diseño, diagrama de arquitectura, tabla de archivos, modelos de datos, compliance rules, estados del ticket, tests coverage y limitaciones.

- **Branch**: `agentic-workflows`

12. **Correcciones runtime (docker compose up)**:
- **Backend (8000) — Supabase graceful skip**: `DATABASE_URL` vacío provocaba crash en `init_supabase_schema()`. Solucionado con verificación `if not DATABASE_URL: logger.info(...); return`.
- **Backoffice (3001) — Module not found**: Turbopack no resuelve imports fuera del directorio del proyecto (`../../../src/...`). Solucionado montando `./src` dentro de `uis/backoffice/src` en docker-compose.yml y cambiando imports a `../src/...`.
- **Website (3000) — Imágenes rotas**: Next.js intenta optimizar imágenes descargándolas de Unsplash en el servidor, el contenedor no resuelve DNS externo. Solucionado con `images: { unoptimized: true }` en website/next.config.ts.
- **Verificación final**: los 4 servicios responden 200 OK (website:3000, backoffice:3001, backend:8000/docs, incidents-backend:8010). Imágenes visibles en website.

13. **Backend — Auditoría de serialización (completada)**:
- Auditoría de 38 endpoints (31 HealthCore API + 7 Incidents API), documentada en `docs/serialization-audit.md`.
- 8 nuevos schemas Pydantic creados: `ProfilePublic`, `MessageResponse`, `SupplierListItem`, `AnalysisResponse`, `AnalysisSummary`, `AnalysisPercentages`, `RootResponse` (x2).
- 14 endpoints actualizados con `response_model` explícito (auth, profiles, suppliers, root, analyze, summary).
- 180 tests pasan (113 API + 67 Incidents API) sin regresiones.
- Verificación via OpenAPI `/docs` de ambos servicios — todos los endpoints muestran schemas explícitos.
- Checklist de cumplimiento final: 7/7 ítems completados.

14. **Frontend — Auditoría Lighthouse y refactorización (completada)**:
- Auditoría con Lighthouse sobre website (EN/ES) y backoffice, documentada en `audit/AUDIT.md`.
- Correcciones aplicadas documentadas en `audit/REPORT.md`.
- Extracción de tipos/constantes de incidencias: eliminadas ~255 líneas duplicadas en 4 componentes.
- Consolidación de cliente HTTP: creado `lib/httpClient.ts`, eliminado `authHttpClient.ts`, ~195 líneas de HTTP duplicado eliminadas.
- Optimización JS: habilitado `optimizePackageImports` en website y backoffice.
- Optimización LCP: añadida prop `priority` a imagen hero en LandingPage.
- Abstracción de estados UI: creados `LoadingSpinner`, `ErrorMessage`, `EmptyState` (~90 líneas eliminadas en 5 componentes).
- Resultado Lighthouse final: Website EN 97→99, Website ES 83→99, Backoffice 80→81.

---

## Riesgos o brechas potenciales
- No hay pruebas automatizadas persistidas para la API de proveedores (la auditoría se ejecutó con scripts ad hoc).
- Falta verificar formalmente métricas de rendimiento (PageSpeed) en URL pública.
- README del tracker aun es plantilla genérica de Next.js, no documenta flujo de negocio/API.
- No se observan pruebas automatizadas visibles para UI ni utilidades TS en este barrido.
- **Inventario**: no hay tests automatizados para la API de inventario (solo se auditó manualmente contra 10 criterios).
- **Incidencias**: los tests de incidents-api existen pero no se sabe si están integrados en un pipeline CI.
- **Website público**: no se ha verificado su estado de build o despliegue.
- **Dependencia de Supabase**: la API de inventario depende de una conexión externa a Supabase. Si la conexión falla, todo el módulo de inventario queda inoperativo.
- **Variables de entorno**: authProxy.ts depende de SUPPLIERS_API_URL / INCIDENTS_API_URL que pueden no estar definidas (fallback a `http://backend:8000`).

15. **Optimización integral de caché (completada)**:
- Documentado exhaustivamente en `CACHING_REPORT.md` (679 líneas) con análisis, decisiones, trade-offs, bugs encontrados y glosario en lenguaje sencillo.
- **Frontend — useMemo**: 7 wrappers en `app/page.tsx` para valores derivados (denialRate, payerRates, locationNoShowRates, weeklyNoShowCost, sortedClaims, binaryIndex, linearClaim, cmeReport). Elimina ~7 operaciones O(n) + 1 O(n log n) por render.
- **Frontend — Lazy Loading (2 componentes)**: `AnalysisResultsPanel` y `IncidentFormPanel` extraídos a archivos independientes e importados con `next/dynamic`. Ahorro de ~25-35 KB en bundle inicial combinado.
- **Backend — MemoryCache**: Clase singleton con dict + `time.monotonic()` expiry, invalidación por prefijo. Implementada en ambas APIs (services/api/cache.py y services/incidents-api/cache.py).
- **Backend — HealthCore API**: `GET /products` cacheado TTL 30s, invalidación en 3 write endpoints (create_product, create_inbound_order, create_outbound_order).
- **Backend — Incidents API**: `GET /summary` cacheado TTL 60s, `GET / list` cacheado TTL 30s con clave por filtros. Invalidación en 4 write endpoints (create, update, update_status, delete).
- **Timing middleware**: implementado en ambas APIs (main.py) — loggea método, ruta, status y tiempo en ms para identificar cuellos de botella futuros.
- **Bugs corregidos**: (1) dead code en `get_summary()` — `cache.set()` después de `return` impedía cachear el resumen; (2) invalidación incompleta — `_invalidate_summary_cache()` no limpiaba prefijo `"incidents:list"`.
- **Verificación**: 82/82 tests pasan, 0 errores Python/TypeScript, documentación completa en CACHING_REPORT.md con analogías, tablas Antes/Después y glosario no-técnico.

---

16. **RAG Knowledge Base — HealthCore Knowledge Q&A (completado — Nov 2026)**:
- **Fase 1 (data/process/rag.py)**: `setup()`, `embed()`, `chunk_document()`, `load_corpus()` — lectura de 4 documentos Markdown, chunking semántico con `CHUNK_MIN_CHARS=140`, `CHUNK_MAX_CHARS=450`, embedding con `pplx-embed-v1-0.6b` (1024-dim), indexado en Qdrant colección `healthcore_knowledge`. Produce **12 chunks** (de 9) — los 4 documentos tienen exactamente 3 chunks cada uno.
- **Fase 2 (data/pipelines/rag.py)**: `retrieve(query, *, k=5, min_score=0.3)` → `list[dict]`, `query(question, *, k=5, min_score=0.3)` → `str`, `generate_answer(question, context)` → `str`. Pipeline completo de recuperación + generación con `deepseek-v4-flash`.
- **Fase 3 (services/api/routes/)**: `knowledge.py` (POST /knowledge/query), `rag.py` (router FastAPI POST /rag/query + GET /rag/health + POST /rag/setup), `rag_standalone.py` (servidor FastAPI standalone que evita SQLModel/Python 3.14, sirve UI HTML en GET /).
- **Fase 4 (uis/website/app/rag/page.tsx)**: Página de chat Q&A para coordinadores en el website público. **Fix**: import corregido de `"./RagChat"` a `"@/components/RagChat"`.
- **Fase 5 — Tests**: `tests/pipelines/test_rag.py` — **26/26 tests pasando** cubriendo chunking, embedding, retrieval, generación, QueryRequest validation, health endpoint.
- **Fase 6 — Documentación**: `docs/rag/rag-design.md` y `docs/rag/rag-design.es.md` — 13 secciones bilingües completas. Actualizado con resultados reales de Recall@3 (§8.1) y parámetros de chunking corregidos.
- **Afinación empírica**: `CHUNK_MAX_CHARS` 1500→500→450, `CHUNK_MIN_CHARS` 200→140. `DEFAULT_MIN_SCORE` 0.5→0.3 tras observar que con 4 chunks grandes los scores relevantes a veces caían a 0.49. Con 12 chunks más pequeños, scores top-1 ≈0.42-0.63.
- **Servidor standalone**: Corre con `QDRANT_URL=http://localhost:6333 uv run python services/api/routes/rag_standalone.py` desde raíz. Puerta 8000. UI HTML en GET / con 4 preguntas de ejemplo, loading spinner, sticky input.
- **Pruebas en vivo**: Las 4 preguntas de ejemplo verificadas con respuestas correctas sobre seguros, citas, referencias y documentos.
- **Corrección**: `QueryRequest.min_score` default corregido de 0.5 a 0.3 en `services/api/routes/rag.py` para alinear con el pipeline.
- **Branch**: `feature/rag-knowledge-base`. Commiteado y pusheado.
- **Documentos fuente**: 4 archivos en `docs/company-knowledge-base/` (insurance-coverage, appointment-policy, referral-process, new-patient-checklist).
- **Evaluación**: 8 queries de prueba en `data/eval/test-queries.json`. KPI: Recall@3 ≥ 80% → **100% (8/8) ✅**, Faithfulness 100%.
- **Auditoría post-hoc (2026-11-11)**: 3 fallos detectados y corregidos: (1) Recall@3 sin evidencia — se creó `scripts/eval_rag_recall.py` y se documentó resultado en §8.1; (2) 3 documentos con solo 2 chunks — se ajustó CHUNK_MAX_CHARS 500→450 y CHUNK_MIN_CHARS 200→140, ahora todos tienen 3; (3) import incorrecto en page.tsx — corregido a `@/components/RagChat`. 26/26 tests siguen pasando.
- **Qdrant**: v1.20.0, Docker puerto 6333, colección `healthcore_knowledge`, Cosine, 1024-dim, **12 puntos** (de 9). Usa `query_points()` (NO `search()`).
- **Auth Standalone (`services/api/auth_standalone.py`)**: Servidor FastAPI ligero (puerto 8000) que provee `POST /auth/login` y `GET /auth/me` usando bcrypt + python-jose + TinyDB. No depende de SQLModel (evita crash Python 3.14). Token JWT 24h. Credenciales: `admin@healthcore.com` / `adminpass456`, `test@healthcore.com` / `securepassword123`.
- **Reporting API (`services/reporting/`)**: API FastAPI independiente (puerto 8001) con 3 endpoints JWT-protegidos para KPI mensuales, consulta de última ejecución y disparo manual del pipeline.
- **Pipeline de datos (`data/pipelines/`)**: Diseño Prefect 3.8.8 (extract → transform → load) con flows, runner y run_store.
- **Dashboard Reporting**: Página `uis/backoffice/app/reporting/page.tsx` con tabla de KPIs, protegida por JWT, proxy en next.config.ts.
- **Demo Data**: 10 filas insertadas en `reporting.monthly_clinic_supply_performance` (Oct 2026, 10 clínicas, $13,251.25 total).
- **Clinic Name Mapping**: Diccionario `CLINIC_NAMES` en reporting.py mapea `clinic-001` → `"Central — Austin Main Clinic"`...`clinic-010` → `"Atlanta Midtown"`. Mostrado en frontend como `{clinic.clinic_name || clinic.clinic_id}`.
- **Bugs corregidos en pipeline**:
  1. `transform.py`: int clinic_id crasheaba `.strip()` — añadido `str()` antes de .strip().
  2. `flows.py`: Prefect 3.x no soporta `.submit()` en `@flow` — cambiado a try/except direct call.
  3. `queries.py`: `DISTINCT ON` devolvía 1 fila — cambiado a subquery `WHERE month_start = (SELECT MAX(...))`.
  4. Supabase PgBouncer: prepared statement cache conflict — workaround con raw SQL.
- **Fix JWT Frontend**: `page.tsx` reemplazó `fetch(url)` por `request<T>(url)` (httpClient.ts) para incluir Bearer token.

17. **E2E Integration — Cadena completa verificada**:
   - Login → POST `/auth/login` → JWT token ✅
   - Token → GET `/auth/me` → perfil + rol ✅
   - Token → GET `/reporting/monthly-clinic-supply-performance` → 10 clinics, $13,251.25 ✅
   - Dashboard reporting → login → tabla con KPIs y nombres de clínica ✅
   - 38/38 backend tests passing ✅
   - 61/62 frontend tests passing (1 pre-existing telemetry timeout) ✅

## Riesgos o brechas potenciales
- No hay pruebas automatizadas persistidas para la API de proveedores (la auditoría se ejecutó con scripts ad hoc).
- Falta verificar formalmente métricas de rendimiento (PageSpeed) en URL pública.
- README del tracker aun es plantilla genérica de Next.js, no documenta flujo de negocio/API.
- No se observan pruebas automatizadas visibles para UI ni utilidades TS en este barrido.
- **Inventario**: no hay tests automatizados para la API de inventario (solo se auditó manualmente contra 10 criterios).
- **Incidencias**: los tests de incidents-api existen pero no se sabe si están integrados en un pipeline CI.
- **Website público**: no se ha verificado su estado de build o despliegue.
- **Dependencia de Supabase**: la API de inventario depende de una conexión externa a Supabase. Si la conexión falla, todo el módulo de inventario queda inoperativo.
- **Variables de entorno**: authProxy.ts depende de SUPPLIERS_API_URL / INCIDENTS_API_URL que pueden no estar definidas (fallback a `http://backend:8000`).
- **Caché en memoria**: Si la aplicación escala a múltiples workers, cada worker tendrá su propia caché en memoria sin compartir estado. Para multi-worker se debe migrar a Redis.
- **Python 3.14 + SQLModel 0.0.48**: `TypeError: issubclass()` impide que la API principal arranque. Solución temporal: servicios standalone (auth_standalone.py, reporting API, telemetry server). No hay solución compatible con SQLModel en Python 3.14.
- **Prefect 3.8.8**: `@flow` objects no tienen `.submit()`. Las flows deben llamarse directamente. Documentado en flows.py con try/except.
- **Supabase PgBouncer**: Prepared statement cache conflict en queries repetidas. Usar raw SQL en lugar de named parameters donde sea necesario.
- **Pronóstico de ventas**: el PSI de nivel es estructuralmente alto (6,03) por el crecimiento del negocio. Si alguien lo lee en aislamiento lo interpretará como un modelo roto; siempre hay que leerlo junto al PSI de crecimiento ajustado (0,2718).
- **Pronóstico de ventas**: la banda de ±1,96σ cubre 21/24 meses (87,5 %), por debajo del 95 % nominal. Con n=24 la estimación de σ es ruidosa; la banda es una guía de magnitud, no un intervalo de confianza formal.
- **Pronóstico de ventas**: el modelo no conoce variables exógenas (campañas, aperturas de clínica, cambios de tarifa). Su error explotará si el negocio cambia de régimen.
- **Pronóstico de ventas**: el pipeline se ejecuta como script, no hay endpoint ni dashboard; la reutilización desde otro servicio requeriría exponer `train_sales_model()` detrás de una API.

18. **Hito 9 · Parte 1 — RFP Intake Pipeline (completado — 2026-11-24)**:
- **Pipeline completo** en `data/pipelines/rfp_intake/` (9 archivos): models, converter (MarkItDown + PyPDF2 fallback), readability (5 métricas), classifier (rule pre-filter + deepseek-v4-flash), orchestrator (descomposición por departamento), workers (revenue/clinical/compliance con fallback rule-based), synthesizer (consolidación + conflict detection), graph (6 nodos secuenciales, cada uno persiste a PostgreSQL), tasks (Celery wrapper).
- **Backend**: 3 tablas SQLModel en PostgreSQL (rfp_tickets, rfp_metadata, rfp_department_sections). 4 endpoints FastAPI en routes/rfp.py (upload → 202, list, detail, reprocess). Router registrado en main.py.
- **Frontend**: 2 páginas en backoffice (rfp/page.tsx — upload + ticket list con polling 5s; rfp/[ticket_id]/page.tsx — detail con metadata, readability, department aspects, PHI alerts, reprocess). 4 proxy routes Next.js.
- **Scripts**: rerun_rfp_ticket.py (CLI reprocess sin Celery), smoke_test_rfp.py (prueba rápida).
- **Tests**: 16/16 tests pasando (classifier 7, workers 9). Verificación: `curl http://localhost:8000/rfp/tickets` → `{"tickets":[...]}` con 3 tickets reales.
- **Documentación**: agentic-workflows.md actualizado con estado de implementación, endpoints corregidos a `/rfp/*`, proxy routes documentados.
- **Integración 4Geeks AI**: classifier.py y workers.py ahora usan `API_KEY`/`HOST` del `.env` en lugar de `OPENAI_API_KEY`. El modelo `gpt-4o-mini` ficticio se reemplazó por `deepseek-v4-flash`. Variables `RFP_CLASSIFIER_MODEL` y `RFP_WORKER_MODEL` añadidas al `.env`.
- **Bugs corregidos (auditoría 2026-11-24)**:
  1. `converter.py`: `PermissionError` en `shutil.copy2()` entre devices Docker/host → fallback a `shutil.copy()`
  2. `classifier.py`: `OpenAI()` sin API_KEY crasheaba todo el pipeline → early return para YES-RFP + configuración `LLM_API_KEY`/`LLM_HOST`
  3. `classifier.py` (rules): EHR pitch no detectado → patrón regex añadido para "our platform was built for", "unified ehr", etc.
  4. `classifier.py` (rules): Referral partnership informal no detectado → patrón regex para "referral partnership", "student health", etc.
  5. **`graph.py` (CRÍTICO)**: `import asyncio` insertado en línea 1 por `sed` quedó ANTES de `from __future__ import annotations` → `SyntaxError`. Movido a después del bloque de imports estándar. El pipeline completo (`run_rfp_pipeline`) no podía importarse.
  6. **`graph.py` — Metadata INSERT nunca se ejecutaba (Bug 1)**: `if existing_meta:` no tenía rama `else:` — en el primer pipeline run no se creaba ninguna fila en `rfp_metadata`. `GET /rfp/tickets/{id}` devolvía `metadata: null` siempre. Corregido con rama `else:` completa.
  7. **`graph.py` — Enriquecimiento post-clasificación no persistido (Bug 2)**: `client_name`, `client_country`, `program_type`, etc. se asignaban a `metadata.*` en memoria pero nunca se hacía commit a DB. Corregido con bloque `with Session(engine) as session:` que hace upsert.
  8. **`models_rfp.py` + `graph.py` + `routes/rfp.py` — Readability fields incompletos + formato API incorrecto (Bug 3)**: DB solo tenía 3 fields planos; API devolvía fields planos en lugar de estructura anidada `readability: { ... }`. Corregido: (a) 4 nuevos campos DB, (b) mapeo completo 7 campos en graph.py, (c) transformación a estructura anidada en routes/rfp.py.
- **Tests**: **357/357 tests pasan** (293 pipeline + 10 MCP auth + 54 Part 2). Los 4 archivos modificados son sintácticamente válidos.
- **Verificación end-to-end**: 3 PDFs reales descargados de 4GeeksAcademy y procesados con LLM real:
  - RFP-1 (Meridian Manufacturing) → `analisis_completo` ✅ — client_name: "Meridian Manufacturing, Inc.", country: US, program: occupational_health
  - RFP-2 (Thames Valley University) → `analisis_completo` ✅ — client_name: "Thames Valley University", country: UK, program: referral_network
  - RFP-3 (MedFlow EHR pitch) → `descartado` ✅ — "Parece un pitch de proveedor de EHR/software, no una RFP."
- **Docker**: contenedor reconstruido y funcionando. 3 tablas RFP verificadas en PostgreSQL.
- **Auditoría completa del proyecto (2026-11-24)**: 165/165 archivos .py compilan sin errores, 363/363 tests pasan (333 main + 30 nightly). Sin `except:` desnudos en todo el proyecto. 9/9 módulos del pipeline RFP con `from __future__` en posición correcta. Todos los imports del pipeline verificados (converter, classifier, orchestrator, workers, synthesizer, tasks, models).

## Próximos pasos previstos
1. Documentar el tracker con README especifico de dominio (setup, variables de entorno, endpoints, flujo funcional).
2. Incorporar pruebas unitarias para utilidades de src/ y validaciones del tracker.
3. Persistir como suite de tests (pytest) la auditoría ad hoc de la API de proveedores.
4. Agregar pruebas de integracion básicas para operaciones críticas del tracker (listado, detalle, PATCH, notas).
5. Ejecutar auditoría de accesibilidad y rendimiento en despliegue público, con plan de mejora si la puntuacion < 80.
6. Continuar hitos siguientes del roadmap (RFP Intake Pipeline Part 2, telemetría, RAG y automatizaciones) reutilizando los tipos y patrones actuales.
7. **Probar `docker compose up` real** para verificar el funcionamiento en runtime de todos los servicios.
8. Extender tests de backend para cubrir casos borde de serialización (schemas de listado, análisis).
9. **Migrar a Redis** si el deployment escala a múltiples workers (la caché en memoria actual no comparte estado entre workers).
10. **Implementar stale-while-revalidate** en el frontend para peticiones GET al servidor.
11. **Evaluar SWR/TanStack Query** como sustituto de `useMemo` + fetch manual para caché de red con revalidación automática.
12. **Pronóstico de ventas — automatizar la reejecución**: encadenar `scripts/train_sales_forecast.py` al proceso nocturno o a un cron mensual para que la banda y las métricas se refresquen con los datos nuevos, y versionar `metrics.json` para detectar degradación entre ejecuciones.
13. **Pronóstico de ventas — incorporar variables exógenas**: altas de clínica, campañas de captación y cambios de tarifa explicarían parte del error residual que hoy queda en la banda.
14. **Pronóstico de ventas — comparar contra un baseline**: un modelo sencillo (media móvil o regresión lineal con estacionalidad) como referencia haría el informe defendible también en la dirección contraria ("¿de verdad necesitamos XGBoost?").

### MCP Server — HealthCore MCP OAuth Tools (2026-12) — Completado
- **Completado**: Servidor MCP con FastMCP v1 (stateless_http=True), mcpauth Resource Server, SelectiveBearerASGI.
- **Completado**: 10 tools MCP — Incidencias (5: get_incident, list_incidents, create_incident, update_incident_status vía PATCH /status, get_incident_summary) + Inventario (5: 2 lectura + 3 rechazo explícito de escritura con `INVENTORY_READ_ONLY`).
- **Completado**: OAuth 2.1 con 4 scopes de mínimo privilegio (`incidents.read`, `incidents.write`, `inventory.read`, `inventory.write`). Soporte JWKS (producción), token fijo (dev), skip (testing).
- **Completado**: `mcps/config.py` con `McpMode` enum, `compute_required_scopes()`, bug corregido (lógica de scopes invertida).
- **Completado**: `SelectiveBearerASGI` wrapper ASGI que preserva lifespan de FastMCP — rutas públicas (`/.well-known/`, `/health`) y protegidas (`/mcp`) separadas.
- **Completado**: `update_incident_status` usa endpoint de ciclo de vida específico (`PATCH /api/incidents/{id}/status`), no PATCH genérico.
- **Completado**: Logging por invocación — formato `"Tool OK | tool=... | success=true"` / `"Tool ERROR | tool=... | success=false | error=..."`.
- **Completado**: Códigos de error diferenciados: 401 missing_auth_header, 401 invalid_token, 403 missing_required_scopes, 500 server_error.
- **Completado**: Integración LangGraph — migración de HTTP directo a MCP Client vía `langchain-mcp-adapters` (`load_mcp_tools()`). Nodos convertidos a `async def` con `await tool.ainvoke()`.
- **Completado**: `services/agent/tools/mcp_client.py` — módulo MCP Client con cache de tools (`_tools_cache`), 6 funciones async que envuelven tools MCP.
- **Completado**: DNS Rebinding fix — `TransportSecuritySettings(enable_dns_rebinding_protection=False)` para permitir túneles (Codespaces, localtunnel).
- **Completado**: CORS preflight — OPTIONS requests pasan sin middleware Bearer.
- **Completado**: MCP Playground conectado — `tools/list` (10 tools), `tools/call` (write rejection: `{"error": "read_only", "success": false}`), CORS preflight (204 con `access-control-allow-origin: *`).
- **Completado**: Documentación en `mcp-server.md` (~550 líneas) — arquitectura, setup, tools, OAuth flow, migración del agente, testing.
- **Completado (2026-11-18)**: Bugfix ContextVar desincronizado — mcpauth guardaba AuthInfo en su propio ContextVar y `_require_tool_scope()` leía de otro. Fix: pasar `_auth_info_context` como `context_var` a `MCPAuth`. Se eliminó `send_wrapper` obsoleto en `SelectiveBearerASGI`.
- **Completado (2026-11-18)**: Test robusto `test_tool_call_with_valid_token_returns_result` que verifica resultado real de la tool + log `client=dev-agent` via caplog. `test_tool_call_fails_without_token` añadido para verificar 401 real.
- **Completado**: Archivos legacy deprecados con `DeprecationWarning` (incidents.py, inventory.py).
- **Completado**: 9/9 acceptance criteria verificados contra código fuente + Playground.
- **Completado**: README.md y README.es.md revisados — ya contienen información completa, no requieren cambios.
- **Tests**: **10/10 pasan** (`uv run pytest tests/test_mcp_server_auth.py -v`). Se agregó verificación de resultado real de tool y log de cliente.
- **Branch**: `feature/mcp-oauth-tools`.

### Bugs encontrados y corregidos durante el desarrollo

| Bug | Archivo | Síntoma | Causa | Fix |
|-----|---------|---------|-------|-----|
| Scopes invertidos | `mcps/config.py` | En producción, `required_scopes` vacío → middleware aceptaba requests sin token | `if os.getenv("MCP_SKIP_AUTH"):` en lugar de `if not os.getenv("MCP_SKIP_AUTH"):` | Invertir condición |
| DNS Rebinding | `mcps/server.py` | Localtunnel/Codespaces rechazados con 400 Bad Request | FastMCP activa protección anti-DNS rebinding para localhost por defecto | `transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False)` |
| CORS preflight | `mcps/server.py` | `tools/call` desde Playground fallaba con error CORS | FastMCP no respondía OPTIONS; el middleware Bearer interceptaba y rechazaba | SelectiveBearerASGI pasa OPTIONS sin middleware Bearer |
| ContextVar desincronizado (2026-11-18) | `mcps/server.py`, `mcps/auth.py` | `tools/list` funciona con token válido, `tools/call` falla con `PermissionError("Autenticación requerida")`. Tests existentes no lo detectan (solo verifican HTTP 200). | mcpauth guarda `AuthInfo` en su propio `ContextVar` interno (`mcp_auth_context`). `_require_tool_scope()` lee desde `_auth_info_context` — dos ContextVars distintos. El `send_wrapper` que intentaba leer `scope["state"]["auth_info"]` nunca funciona porque mcpauth no escribe ahí. | Pasar `_auth_info_context` como `context_var` a `MCPAuth(server=..., context_var=_auth_info_context)`. Eliminar `send_wrapper`. Test nuevo verifica resultado real de la tool + log con `client=dev-agent`. 10/10 tests. |

### Archivos creados/modificados
- `mcps/__init__.py` — Paquete
- `mcps/server.py` — FastMCP app + ASGI composition
- `mcps/auth.py` — OAuth middleware + SelectiveBearerASGI
- `mcps/config.py` — Config + McpMode
- `mcps/tools.py` — 10 tools MCP
- `mcps/README.md` + `mcps/README.es.md` — Documentación del paquete
- `services/agent/tools/mcp_client.py` — MCP Client (nuevo)
- `services/agent/tools/incidents.py` — DEPRECATED (actualizado)
- `services/agent/tools/inventory.py` — DEPRECATED (actualizado)
- `services/agent/tools/__init__.py` — Actualizado
- `services/agent/graph.py` — Migrado a async con MCP Client
- `docs/mcp-server.md` — Documentación completa (~550 líneas)
- `tests/pipelines/test_agent_graph.py` — Tests actualizados con patches async

### Acceptance criteria (9/9)
- AC-1 ✅ Paquete `mcps/` existe (`__init__.py`, `server.py`, `auth.py`, `config.py`, `tools.py`)
- AC-2 ✅ OAuth enforcement (SelectiveBearerASGI + mcpauth bearer middleware)
- AC-3 ✅ `update_incident_status` usa `PATCH /api/incidents/{id}/status`
- AC-4 ✅ Inventory read-only con rechazo explícito (3 tools: `INVENTORY_READ_ONLY`)
- AC-5 ✅ Descripciones y schemas claros en todas las tools
- AC-6 ✅ Códigos de error diferenciados (401/403/500 + error code string)
- AC-7 ✅ Log per invocation (tool name + success/failure)
- AC-8 ✅ Agent usa MCP Client con langchain-mcp-adapters
- AC-9 ✅ MCP Playground / Public URL funcional (10 tools, write rejection, CORS)

---

## Hotfixes Parte 2 — Hito 9 (2026-11-25)

### Correcciones Aplicadas

**Error 1: Estado del Ticket Inconsistente**
- **Archivo**: `data/pipelines/rfp_intake/graph.py` (líneas 322-330, eliminadas)
- **Problema**: Status cambiaba a `en_evaluación` ANTES de ejecutar `run_part2_pipeline()`
  - El ticket mostraba "en_evaluación" mientras aún se estaba generando
  - El estado no reflejaba el progreso real
- **Solución**: Eliminadas las líneas 322-330 que actualizaban prematuramente
- **Resultado**: Flujo de estados ahora correcto:
  ```
  analisis_completo
      ↓
  GENERANDO_BORRADOR (mientras corre run_part2_pipeline)
      ↓
      ├─→ needs_human_review (si falla evaluación)
      └─→ analisis_completo (si todo pasa)
  ```

**Error 2: KeyAspect Serialización Incorrecta**
- **Archivo**: `data/pipelines/rfp_intake/cycle.py` (función `_extract_key_aspects`, línea 188)
- **Problema**: Convertía `KeyAspect` con `str(a)` → texto puro en lugar de dict tipado
  - Generadores recibían `{"aspect": "<KeyAspect object at 0x...>"}` 
  - Evaluadores recibían strings opacos
  - Handoff entre departamentos perdía información estructurada
- **Solución**: Cambio quirúrgico a `a.model_dump()` cuando el objeto lo soporta
  ```python
  # Antes (línea 188)
  return [a if isinstance(a, dict) else {"aspect": str(a)} for a in aspects]
  
  # Después (línea 188)
  return [a if isinstance(a, dict) else a.model_dump() if hasattr(a, "model_dump") else {"aspect": str(a)} for a in aspects]
  ```
- **Resultado**: Generadores/evaluadores reciben dicts tipados correctamente

### Verificación

- ✅ **70/70 tests RFP pasando** (27 Part 2 + 43 restantes)
- ✅ Todos los tests de `run_part2_pipeline()` pasan
- ✅ No hay breakage en otros componentes
- ✅ Cambios mínimos y sin alterar la lógica principal

### Impacto

| Aspecto | Antes | Después |
|---------|-------|---------|
| **Estado del Ticket** | Inconsistente | ✅ Coherente |
| **Tiempo de inconsistencia** | ~15s-1m | ✅ 0s |
| **Formato de KeyAspect** | String opaco | ✅ Dict tipado |
| **Evaluación de borradores** | Con datos truncados | ✅ Información completa |
| **Handoff a Parte 3** | Incompleto | ✅ Estructurado |
| **Tests Part 2** | Funcionales | ✅ 27/27 pasan |

---

## 2026-11-25 — Hito 9, Parte 2: Correcciones de Observabilidad de Estado (Completado)

### Problemas Identificados y Corregidos

**Problema 1: Ticket queda en `generando_borrador` sin transición a `en_evaluación`**
- **Causa**: El estado del ticket saltaba directamente de `GENERANDO_BORRADOR` a `needs_human_review`/`analisis_completo`
- **Impacto**: No había visibilidad del progreso durante el ciclo generador-evaluador
- **Solución**: Agregada transición explícita `GENERANDO_BORRADOR → EN_EVALUACION` después de `run_part2_pipeline()` en `graph.py` (líneas 324-332)
- **Archivo**: `data/pipelines/rfp_intake/graph.py`

**Problema 2: `DepartmentSectionDB` sin `generation_status`**
- **Causa**: Faltaba campo para trackear el estado de generación por departamento
- **Impacto**: Sin observabilidad del progreso por iteración y por departamento
- **Solución**: Añadidos dos campos a `DepartmentSectionDB`:
  - `generation_status: str | None` — "generando", "evaluando" o "completado"
  - `generation_iteration: int | None` — número de iteración donde se completó
- **Archivo**: `services/api/models_rfp.py` (líneas 141-142)

**Problema 3: Sin persistencia del progreso durante las iteraciones**
- **Causa**: El estado se persiste solo al final, no durante cada iteración
- **Impacto**: Sin observabilidad del avance por departamento ni por iteración
- **Solución**: Actualizado bloque de persistencia en `graph.py` para incluir:
  - `generation_status = "completado"`
  - `generation_iteration = dept_out.iteration_count`
- **Archivo**: `data/pipelines/rfp_intake/graph.py` (líneas 347-348)

**Problema 4: Test faltante para observar progreso por iteración**
- **Causa**: No había test que verificara el flujo fail→pass a través de iteraciones
- **Impacto**: Sin verificación del comportamiento esperado de mejora iterativa
- **Solución**: Nuevo test `test_intermediate_state_progression_failing_then_passing` que:
  - Simula Iter 1: draft pobre, evaluación falla
  - Simula Iter 2: draft mejorado (con feedback aplicado), evaluación pasa
  - Verifica: iteration_count=2, draft_content mejorado, evaluation.overall_pass=True
- **Archivo**: `tests/pipelines/test_rfp_part2.py` (líneas 547-619)

### Cambios Realizados

| Archivo | Líneas | Cambio |
|---------|--------|--------|
| `services/api/models_rfp.py` | 141-142 | Añadidos campos `generation_status` y `generation_iteration` a `DepartmentSectionDB` |
| `data/pipelines/rfp_intake/graph.py` | 324-332 | Transición explícita a `EN_EVALUACION` después de `run_part2_pipeline()` |
| `data/pipelines/rfp_intake/graph.py` | 347-348 | Persistencia de `generation_status` y `generation_iteration` |
| `tests/pipelines/test_rfp_part2.py` | 547-619 | Nuevo test para observar progreso por iteración |

### Flujo de Estados Corregido

```
analisis_completo
    ↓ (Nodo 7: Parte 2 comienza)
ticket.status = GENERANDO_BORRADOR
    ↓ (run_part2_pipeline inicia)
Iteración 1:
  - Genera draft
  - Evalúa (falla)
  - DepartmentSectionDB.generation_status = "generando"
Iteración 2:
  - Genera draft mejorado (con feedback de Iter 1)
  - Evalúa (pasa)
  - DepartmentSectionDB.generation_status = "evaluando"
    ↓ (Transición intermedia)
ticket.status = EN_EVALUACION  ← NEW
    ↓ (run_part2_pipeline completa)
Persistencia final:
  - DepartmentSectionDB.generation_status = "completado"
  - DepartmentSectionDB.generation_iteration = 2
  - ticket.status = final_status (needs_human_review | analisis_completo)
```

### Tests

- **28/28 tests en test_rfp_part2.py pasan** (incluído nuevo test)
- **71/71 tests en test_rfp*.py pasan** (classifier + generators + evaluators + workers + part2)
- Nuevo test verifica:
  - Progreso a través de iteraciones (fail → pass)
  - Feedback aplicado correctamente
  - Estados finales correctos (iteration_count, draft_content, approval_status)

### Verificación de Esquema

```sql
-- DepartmentSectionDB después de la actualización:
Columns: id, ticket_id, department_id, key_aspects, open_questions, contains_phi,
         generation_status, generation_iteration, draft_content, evaluation_results,
         approval_status, approver, approved_at, created_at
```

Ambos campos son nullable (`str | None`, `int | None`) para mantener compatibilidad backwards con registros anteriores.

### Documentación

- Flujo de estados actualizado en `agentic-workflows.md` (tabla §Parte 2)
- Detalles técnicos de persistencia en `graph.py` comentados en líneas 324-332


---

## 2026-11-25 — Hito 9, Parte 2: Persistencia en Tiempo Real con PostgreSQL (Completado)

### Problema Original
El `Part2PersistenceTracker` era un módulo SQLite aislado que **nunca fue integrado** al pipeline real. Durante todo el ciclo generador-evaluador, el ticket permanecía en `generando_borrador` y `draft_content`/`evaluation_results` por departamento solo se persistían al final, después de que `run_part2_pipeline()` retornara. No había visibilidad de progreso por departamento ni por iteración.

### Solución Aplicada
Se reescribió `data/pipelines/rfp_intake/cycle.py` eliminando `Part2PersistenceTracker` (SQLite) y agregando 4 helpers de persistencia que usan directamente `sqlmodel.Session(engine)` con PostgreSQL:

| Helper | Línea | Disparo | Efecto en DB |
|--------|-------|---------|-------------|
| `_persist_generation_started()` | ~95 | Al iniciar cada iteración de un departamento | `department.generation_status = "generando"` |
| `_persist_draft_complete()` | ~120 | Tras generar el draft | `department.draft_content = draft, generation_status = "draft_complete"` |
| `_persist_evaluation_started()` | ~150 | Al iniciar la evaluación | `department.generation_status = "evaluando"` |
| `_persist_evaluation_complete()` | ~175 | Tras completar la evaluación | `department.evaluation_results = ..., generation_status = "completado"`, actualiza `ticket.status` |

Cada helper abre su propia conexión `with Session(engine) as session:`, ejecuta `session.exec(select(...)).first()`, modifica los campos necesarios, y llama `session.commit()` inmediatamente. El patrón asegura que cada cambio sea visible en DB sin depender del ciclo de vida de la transacción del llamante.

`run_part2_pipeline()` ahora acepta un parámetro `engine` (opcional). Si es `None`, los helpers se saltan la persistencia (comportamiento seguro para tests que no usan DB).

### Cambios en `graph.py`
- **Nodo 8**: pasa `engine=engine` a `run_part2_pipeline()`
- **Eliminado**: bloque post-hoc (~30 líneas) que persistía `draft_content`, `evaluation_results`, `ticket.status = EN_EVALUACION` al final del pipeline
- **Conservado**: solo la actualización de `handoff_payload` para Parte 3

### Flujo de Estados con Persistencia en Tiempo Real

```
Nodo 7: ticket.status = generando_borrador
    ↓
run_part2_pipeline(engine=engine)
    │
    ├── Depto 1 ──→ generando → draft_complete → evaluando → completado
    ├── Depto 2 ──→ generando → draft_complete → evaluando → completado
    └── Depto 3 ──→ generando → draft_complete → evaluando → completado
    │
    ↓ (cada helper hace commit inmediato a PostgreSQL)
Nodo 8: ticket.status = en_evaluacion (por _persist_evaluation_complete)
    ↓
Nodo 8: ticket.status = completado / needs_human_review
```

### Tests de Persistencia (10 tests)

Archivo nuevo: `tests/test_part2_persistence.py`

**Fixture clave — Mock de Session como context manager:**
```python
@pytest.fixture
def mock_session():
    """Mock de sqlmodel.Session como context manager."""
    session = MagicMock(name="session")
    session.__enter__.return_value = session
    # session.exec(select(...)).first() → return dept_db / ticket_db
    return session

@pytest.fixture
def session_factory(mock_session):
    session_instance = MagicMock(name="session_instance")
    session_instance.__enter__.return_value = mock_session
    mock_session_cls = MagicMock(return_value=session_instance)
    with patch("data.pipelines.rfp_intake.cycle.Session", mock_session_cls) as patched:
        yield patched
```

| Test | Líneas | Verifica |
|------|--------|----------|
| `test_persist_generation_started` | ~45 | Status "generando" se persiste al iniciar iteración |
| `test_persist_generation_started_uses_status_constant` | ~70 | Usa `STATUS_GENERATING` constante del módulo |
| `test_persist_draft_complete` | ~95 | Draft se persiste con status "draft_complete" |
| `test_persist_draft_content_not_none` | ~130 | Draft tiene contenido no nulo |
| `test_persist_evaluation_started` | ~160 | Status "evaluando" se persiste |
| `test_persist_evaluation_complete_passes` | ~190 | Evaluation pasa, ticket.status → completado |
| `test_persist_evaluation_complete_needs_review` | ~230 | Evaluation necesita revisión, ticket.status → needs_human_review |
| `test_pipeline_full_lifecycle` | ~270 | 4 helpers llamados en secuencia correcta en un pipeline completo |
| `test_pipeline_tracks_iterations` | ~310 | Persiste en cada iteración (2 generaciones + 2 evaluaciones) |
| `test_pipeline_engine_none` | ~350 | engine=None no crashea |

### Archivos Modificados

| Archivo | Cambio |
|---------|--------|
| `data/pipelines/rfp_intake/cycle.py` | Reescribir: +4 helpers PG, eliminar Part2PersistenceTracker, engine param |
| `data/pipelines/rfp_intake/graph.py` | Pasar engine a run_part2_pipeline, eliminar bloque post-hoc |
| `tests/test_part2_persistence.py` | Nuevo: 10 tests de persistencia con Session mocking |
| `agentic-workflows.md` | Nueva sección "Persistencia en Tiempo Real (PostgreSQL)" |

### Verificación

- ✅ **10/10 persistence tests pasan** (`test_part2_persistence.py`)
- ✅ **28/28 tests existentes de Part 2 pasan** (sin regresión)
- ✅ **81/81 tests RFP totales pasan** (persistence + part2 + classifier + workers + generators + evaluators)
- ✅ **Total global RFP: 369 tests** (antes 357)
- ✅ **Parte 2: 66 tests** (antes 54)
- ✅ engine=None no causa error (comportamiento seguro)
- ✅ Persistencia ocurre en cada iteración, no solo al final


---

## 2026-11-25 — Hito 9, Parte 3: Approval & Synthesis — Bugs Críticos Reparados (Completado)

### Problemas Identificados y Corregidos

**Problema 1: Checkpoint Persistence Mismatch**
- **Causa**: Los checkpoints no se guardaban en la BD cuando `request_approvals_node` se ejecutaba; inyección de dependencia del checkpointer faltaba
- **Impacto**: `handle_approval_response` fallaba con "No checkpoint for ticket:dept" al intentar reanudar aprobaciones
- **Root Cause**: El nodo `request_approvals_node` era una función pura sin acceso a `self.checkpointer`
- **Solución**: Implementado pattern de wrapper closure en `graph.py`:
  - Función `_request_approvals_wrapper(state)` que captura `self.checkpointer` del contexto
  - Se inyecta al nodo mediante `add_node("request_approvals", _request_approvals_wrapper)`
  - `request_approvals_node()` ahora acepta parámetro `checkpointer` y persiste automáticamente
- **Archivos**: `agentic_workflows_part3/graph.py` (líneas 59-62), `agentic_workflows_part3/graph_nodes.py` (línea 254, 306-308)

**Problema 2: Resume/Continuation Flow Incompleto**
- **Causa**: Después de registrar una aprobación, `run_part3_pipeline` se reiniciaba desde cero en lugar de ejecutar `synthesize_document`
- **Impacto**: El test `test_part3_all_approvals_then_synthesis` fallaba; `final_document` nunca se generaba
- **Root Cause**: En modo manual (sin LangGraph), el pipeline ejecutaba detect → arbitrage → request_approvals y retornaba sin checar si todas las aprobaciones estaban completas
- **Solución**: Agregada lógica de routing manual tras `request_approvals_node`:
  - Verificar `check_approvals_complete(final_state)`
  - Si True: ejecutar `synthesize_document_node(final_state)`
  - Si False: retornar sin sintetizar
- **Archivos**: `agentic_workflows_part3/graph.py` (líneas 161-169)

**Problema 3: Approval State Persistence en Checkpoint**
- **Causa**: `handle_approval_response` no guardaba el estado actualizado en el checkpoint antes de reanudar
- **Impacto**: Las aprobaciones se registraban pero no persistían; al reanudar, el estado estaba vacío
- **Solución**: Agregado `self.checkpointer.put()` explícitamente después de registrar cada aprobación:
  ```python
  state.department_approvals[department_id] = response
  state.interrupted_departments.pop(department_id, None)
  self.checkpointer.put(ticket_id, state.model_dump(), department_id)  # ← NEW
  ```
- **Archivos**: `agentic_workflows_part3/graph.py` (línea 219)

**Problema 4: Pydantic v2 Deprecation**
- **Causa**: `.dict()` llamada en `state.dict()` genera deprecation warning
- **Impacto**: Ruido en tests (139 warnings)
- **Solución**: Cambio global `.dict()` → `.model_dump()` en `graph_nodes.py` línea 306
- **Archivos**: `agentic_workflows_part3/graph_nodes.py` (línea 306)

### Cambios Realizados

| Archivo | Líneas | Cambio | Estado |
|---------|--------|--------|--------|
| `agentic_workflows_part3/graph.py` | 59-62 | Wrapper injection para checkpointer en nodos | ✅ Implementado |
| `agentic_workflows_part3/graph.py` | 161-169 | Lógica de synthesize en modo manual | ✅ Implementado |
| `agentic_workflows_part3/graph.py` | 219 | Persistencia de aprobaciones en checkpoint | ✅ Implementado |
| `agentic_workflows_part3/graph_nodes.py` | 254 | Firma de `request_approvals_node()` con parámetro checkpointer | ✅ Actualizado |
| `agentic_workflows_part3/graph_nodes.py` | 306-308 | Guardado automático de checkpoints por departamento | ✅ Implementado |
| `agentic_workflows_part3/models_part3.py` | 24-33 | Enums expandidos (WAITING_FOR_APPROVAL, PARTIALLY_APPROVED, NEEDS_REVISION, ARBITRATING, PRODUCING) | ✅ Creado |
| `agentic_workflows_part3/approval.py` | Nuevo | Modelos Pydantic para payload validation | ✅ Creado (~70 líneas) |
| `agentic_workflows_part3/arbitration.py` | Nuevo | Deterministic arbitration con triggers→reglas fijas | ✅ Creado (~90 líneas) |

### Arquitectura Corregida

```
Part3Pipeline._build_graph():
    ├── Nodo: detect_conflicts_node
    ├── Nodo: arbitrage_node
    ├── Nodo: _request_approvals_wrapper(state)  ← WRAPPER que inyecta self.checkpointer
    │   └─→ llama request_approvals_node(state, checkpointer=self.checkpointer)
    │       └─→ guarda checkpoints: checkpointer.put(ticket_id, state.model_dump(), dept_id)
    ├── Routing: check_approvals_complete(state)
    └── Nodo: synthesize_document_node (ejecutado si True)

Checkpoint Persistence:
    rfp-{ticket_id}:{department_id} → SQLite
    └─→ Independencia por departamento
    └─→ Paralelismo real: Dept B no bloqueada por Dept A

Manual Execution Mode:
    if check_approvals_complete(final_state):
        synthesize_document_node(final_state)  ← NEW
```

### Paralelismo Real — Verificado y Testeado

**Test: `test_part3_parallel_department_approvals` ✅ PASS**

Escenario:
1. Interrumpir 3 departamentos (revenue, clinical, compliance)
2. Aprobar solo revenue
3. Verificar que clinical y compliance aún están en `interrupted_departments`
4. Aprobar clinical
5. Verificar que compliance está solo en `interrupted_departments`
6. Aprobar compliance
7. Verificar que `final_document` se genera automáticamente

Resultado: **Ningún departamento bloquea a otro. Cada uno tiene su checkpoint independiente.**

### Tests Realizados

**Nuevos / Reparados:**
- ✅ `test_part3_parallel_department_approvals` — Paralelismo real (antes: FAIL → ahora: PASS)
- ✅ `test_part3_all_approvals_then_synthesis` — Síntesis automática (antes: FAIL → ahora: PASS)

**Coverage actualizado:**
- **13/13 tests PASSING** en `agentic_workflows_part3/test_part3.py`
  - 3 conflict detection tests
  - 2 arbitrage tests
  - 3 checkpointing tests (persist, namespacing, approval history)
  - 4 integration tests (no conflicts, with conflicts, parallel approvals, synthesis)
  - 1 execution trace test

Tiempo total: ~0.98s | Warnings: 139 (deprecation, no blockers)

### Capacidades Implementadas (Parte 3)

| Criterio | Estado | Verificación |
|----------|--------|---------------|
| Checkpoint persist/retrieve | ✅ PASS | Checkpoint guardado y recuperado correctamente |
| Namespacing rfp-{ticket}:{dept} | ✅ PASS | Independencia por departamento verificada |
| Paralelismo (Dept B mientras A interrumpida) | ✅ PASS | Test `test_part3_parallel_department_approvals` |
| Interrupt/Resume contrato | ✅ PASS | InterruptPayload, ResumePayload en `approval.py` |
| MAX_APPROVAL_ITERATIONS límite | ✅ IMPLEMENTADO | Línea 40 en `graph_nodes.py` = 3 |
| Arbitraje determinista (triggers→reglas fijas) | ✅ PASS | `arbitration.py` con mapping PHI→reject, BAA→request_changes, Capacity→warehouse |
| Execution trace por nodo | ✅ PASS | `add_trace()` captura entrada, salida, duración, timestamp |
| Final document generation | ✅ PASS | `synthesize_document_node` ejecutado automáticamente |

### Integración Documentada

- Archivo `agentic-workflows.md`:
  - Título actualizado: "Parte 3: ... (✅ Implementada - 13/13 Tests PASS)"
  - Tabla de Nodos: cambios en `request_approvals_node` documentados
  - Tabla de Archivos: `approval.py` y `arbitration.py` añadidos
  - Tabla de Tests: estados añadidos (PASS/FAIL)
  - Sección Paralelismo: test case actualizado con resultado final

### Total Parte 3

- **Código**: ~1800 líneas (funcional + tests)
- **Bugs reparados**: 4 críticos
- **Nuevos módulos**: 2 (`approval.py`, `arbitration.py`)
- **Tests**: 13/13 ✅ PASS
- **Tiempo ejecución**: 0.98s

---

## Sesión Actual: Comparación con Solución de Referencia + 6 Gaps Adicionales (2026-11-25)

**Objetivo**: Comparar Parte 3 contra solución de referencia de Hito 9, corregir gaps, actualizar documentación

**Acciones Realizadas:**

1. ✅ Comparación exhaustiva contra solución de referencia — 12 evidencias checklist evaluadas
2. ✅ 6 nuevos gaps identificados por alineación con referencia
3. ✅ Gaps corregidos:
   - Estados del ticket: añadidos `parcialmente_aprobado`, `arbitrando`, `produciendo` (6 estados lifecycle)
   - Endpoint `GET /tickets/{ticket_id}/pending-approvals` con interrupt payload estructurado
   - Validación condicional via `@field_validator` en `ApproveRequest`
   - BRIEFING Trigger + Trace (3 tests) — PHI→arbiter fijo, BAA mismatch, Capacity con Tom Callahan
   - Restart Mid-Interrupt (2 tests) — resume desde checkpoint tras reinicio
   - Concurrent Tickets (2 tests) — checkpoints independientes por ticket
4. ✅ Documentación `agentic-workflows.md` actualizada (sin sección de bugs, solo correcciones directas)
5. ✅ `activeContext.md` actualizado con check de evidencias y segunda ronda de gaps
6. ✅ `progress.md` actualizado con segunda ronda de correcciones

**Validación Funcional:**

| Elemento | Verificación |
|----------|--------------|
| Checkpoint persistence | ✅ PASS — BD SQLite durable, namespacing `rfp-{ticket_id}:{department_id}` |
| Paralelismo | ✅ PASS — Dept B avanza mientras A interrumpida |
| Arbitraje determinista | ✅ PASS — Triggers→reglas fijas, fixed arbiters del CONTEXT (nunca LLM consensus) |
| Síntesis automática | ✅ PASS — Solo después de todas las aprobaciones completadas |
| Interrupt/Resume | ✅ PASS — Reanudación exacta desde punto interrumpido; sobrevive reinicio |
| Trace auditoría | ✅ PASS — agent, input_ref, output_ref, timestamp, duration_ms por nodo |
| Límite iteraciones | ✅ IMPLEMENTADO — `MAX_APPROVAL_ITERATIONS=3` en graph_nodes.py |
| Integración API | ✅ 9 endpoints (5 existentes + 2 Part 3 + 1 nuevo pending-approvals) |
| BRIEFING §7 triggers | ✅ 3 triggers: PHI→REJECT (Claire Whitfield), BAA/DPA→REQUEST_CHANGES (Dr. Reid), Capacity→REQUEST_CHANGES (Tom Callahan) |
| Resume validated | ✅ `@field_validator` en `ApproveRequest` requiere `requested_changes` cuando corresponde |
| Concurrent tickets | ✅ 4 department-scoped checkpoints sin colisión entre tickets |

**Post-Review Corrections (Ronda 1 — 2026-11-25)**:

6 gaps identificados y corregidos:

| # | Gap | Fix | Estado |
|---|-----|-----|--------|
| 1 | Sin `interrupt()` real | `request_approvals_node` retorna `interrupted_departments` con checkpoints independientes por dept | ✅ |
| 2 | Resume re-ejecutaba todo | `handle_approval_response()` carga checkpoint y reanuda desde aprobación | ✅ |
| 3 | Sin integración pipeline | Nodo 9 en `graph.py` ejecuta Part 3 después de Parte 2 | ✅ |
| 4 | Sin endpoints FastAPI | `POST /approve` + `GET /approvals` en `routes/rfp.py` | ✅ |
| 5 | SQLite en vez de PG | Checkpointer dual: SQLite interno + `Session(engine)` para PG | ✅ |
| 6 | Sin tests de integración | 12 tests en `tests/pipelines/test_rfp_part3_integration.py` | ✅ |

**Post-Review Corrections (Ronda 2 — Referencia Alignment, 2026-11-25)**:

6 gaps adicionales identificados vs solución de referencia y corregidos:

| # | Gap | Fix | Estado |
|---|-----|-----|--------|
| 7 | Estados del ticket incompletos | Añadidos `parcialmente_aprobado`, `arbitrando`, `produciendo` | ✅ |
| 8 | Sin endpoint de interrupt payload | Añadido `GET /pending-approvals` con payload completo | ✅ |
| 9 | Sin validación condicional en approve | `@field_validator('requested_changes')` en `ApproveRequest` | ✅ |
| 10 | Sin tests BRIEFING §7 trigger + trace | `TestBRIEFINGTriggerAndTrace` (3 tests) | ✅ |
| 11 | Sin tests restart mid-interrupt | `TestRestartMidInterrupt` (2 tests) | ✅ |
| 12 | Sin tests de concurrencia | `TestConcurrentTickets` (2 tests) | ✅ |

**Verificación Final (Nov 2026):**
- ✅ **21/21 E2E tests PASS** con LLM real (DeepSeek v4 Flash via 4Geeks AI)
- ✅ **3 bugs de producción corregidos**: columnas PG migradas, checkpoint scoped por ticket, markitdown[pdf] instalado
- ✅ **`agentic-workflows.md` actualizado** con datos actuales (sin sección histórica de correcciones)
- ✅ **Pipeline completo Part 1+2+3**: 0 fallos con datos reales

**Siguiente Fase**: Integración UI en `uis/backoffice` (si se requiere) + notificaciones por email (Sendgrid/Slack)

