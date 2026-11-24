# Active Context

## Sistema de Memoria Persistente del Agente — Hito 8 · Memoria y Auto-mejora de Agentes (2026-11/12)

- **Mejoras post-auditoría (2026-11-22)**:
  - **Bug 16 corregido — Alcance de negación en el clasificador**:
    - `_RE_REJECT_PHRASE` ampliado: acepta pronombres átonos entre "no" y el verbo (`no lo guardes`, `no la grabes`, `no los apuntes`, `no me recuerdes`)
    - `_hay_aprobacion` ignora comandos de memoria negados: los verbos como "guardes" cuando están precedidos por "no" ya no suman puntaje de approve
    - Nueva función `_comando_memoria_negado()` detecta "no + pronombre opcional + verbo de memoria"
    - Nuevo paso 5b en el pipeline: detección de contradicciones — si el mensaje contiene tanto "sí" como "no", se descarta como ambiguo (discard)
    - 7 nuevos tests en `TestMemoryClassifier` para los casos: "no lo guardes", "sí, pero no lo guardes", "sí, pero no", "sí, no", "no la grabes", "no los apuntes", "no me recuerdes"
  - **Bug 18 corregido — Comando de memoria contradictorio con negación** (2026-11-22):
    - **Problema**: Mensajes como "no, pero guárdalo" se clasificaban como `approve`, creando un fact (`facts_delta=1`). El clasificador detectaba contradicciones solo cuando aparecían "sí" y "no" juntos (Paso 5b), pero no cuando "no" iba seguido de un comando de memoria con separadores (coma, puntos suspensivos, conjunciones).
    - **Causa raíz**: `_comando_memoria_negado()` solo detectaba negación cuando "no" precede DIRECTAMENTE al verbo (con pronombre opcional). Cuando hay separadores como "pero", "," o "..." entre "no" y el comando, no se detectaba y el comando sumaba 0.8 de approve.
    - Tampoco se detectaba la contradicción semántica entre el "no" de negación y el comando de memoria que pide guardar.
    - **Solución**: Nuevo Paso 5c en el pipeline que detecta cuando "no" aparece ANTES de un comando de memoria en el texto (considerando cualquier separador). Si `no.start() < comando.start()`, la intención es contradictoria → discard. Una contradicción o negación debe ser reject/discard y nunca escribir.
    - **Nuevos tests**: 5 tests en `TestMemoryClassifier`:
      - `"no, pero guárdalo"` → discard
      - `"no, guárdalo"` → discard
      - `"bueno no, guárdalo mejor"` → discard
      - `"no... guárdalo"` → discard
      - `"guárdalo, no"` → approve (orden invertido, comando antes que no)
    - **Pipeline ampliado**: de 10 a 11 pasos (nuevo paso 8: contradicción no+comando).
    - **classifier.py**: ~580 líneas (antes ~567).
  - **Bug 19 corregido — Contradicción comando+no en cualquier orden** (2026-12):
    - **Problema**: Frases como `"guárdalo, no"`, `"apúntalo, pero no"`, `"recuérdalo, pero no"`, `"guárdalo, no gracias"` retornaban `approve` porque el Paso 5c original solo detectaba contradicción cuando `"no"` precedía al comando (`no.start() < command.start()`). El orden inverso (comando primero) no se detectaba.
    - **Causa raíz**: Verificación posicional rígida — `if no_match.start() < store_match.start()` solo capturaba una dirección.
    - **Solución**: Paso 5c reescrito de verificación posicional a **co-ocurrencia semántica** (cualquier orden). Si el texto contiene TANTO un comando de memoria COMO `"no"`, es semánticamente contradictorio → `discard`. Excepciones: (a) tag questions `"¿no?"` con `?` explícito → coletilla de confirmación, no negación; (b) `"no olvides"` / `"no te olvides"` → doble negación afirmativa = `"recuerda"`.
    - **Ajustes adicionales**: `_hay_rechazo()` retorna 0.0 para `"no olvides"`; `_hay_aprobacion()` suma +0.7 para `"no olvides"`; `_es_pregunta_o_condicional()` excluye tag questions `"¿no?"`.
    - **Nuevos tests**: 10 tests nuevos + 1 renombrado. 19 tests de contradicción comando+no en total.
  - **Bug 20 corregido — Expansión de detección de negación** (2026-11-22):
    - **Problema**: El clasificador solo detectaba "no" como negación. Los testers estaban probando con "nunca", "jamás" y "tampoco", que al no ser detectados, permitían que el sistema clasificara como `approve` mensajes contradictorios como "guárdalo, nunca".
    - **Causa raíz**: `_RE_NEGACION` solo incluía `_RE_NO_WORD`. Paso 5c usaba `_RE_NO_WORD` exclusivamente.
    - **Solución**: Nuevo regex `_RE_NUNCA = re.compile(r"\b(?:nunca|jam[aá]s|tampoco)\b")` que se integra en `_RE_NEGACION` (combinación de `_RE_NO_WORD` + `_RE_NUNCA`). Paso 5c ahora usa `_RE_NEGACION` en lugar de `_RE_NO_WORD`. `_hay_rechazo()` suma +0.5 también para "nunca"/"jamás"/"tampoco".
    - **Nuevos tests**: 9 tests nuevos en `TestMemoryClassifier`:
      - `"guárdalo, nunca"` → discard
      - `"guárdalo, jamás"` → discard
      - `"guárdalo, tampoco"` → discard
      - `"apúntalo, nunca"` → discard
      - `"recuérdalo, jamás"` → discard
      - `"nunca, guárdalo"` → discard
      - `"jamás, apúntalo"` → discard
      - `"tampoco lo guardes"` → discard|reject
      - `"nunca jamás"` → discard
    - **Tests totales de contradicción**: 28 (19 "no" + 9 "nunca/jamás/tampoco").
  - **Mejora 17 — Auto-evaluación LLM en lugar de heurística post-hoc** (2026-12):
    - ... (contenido existente)
  - **Bug 17 corregido — Mensajes "unrelated" ahora limpian la propuesta pendiente** (2026-11-22):
    - **Problema**: Cuando un usuario enviaba un mensaje no relacionado con la propuesta pendiente (ej: "El reporte semanal para Diane Foster debe incluir vacantes por rol, no solo por clínica"), el clasificador devolvía `discard` pero no limpiaba la propuesta, dejando atrapado el mensaje sin llegar a `auto_evaluate_memory`.
    - **Causa raíz**: `process_user_response()` en la rama `else` (discard) no distinguía entre "mensaje irrelevante" y "rechazo explícito", y no redirigía el mensaje al flujo normal de evaluación de memoria.
    - **Solución**: `process_user_response()` rama `else` ahora devuelve `resolution="unrelated"` + llama `clear_pending_proposal()` + redirige el mensaje al flujo normal. Nuevo evento de auditoría `"unrelated"`.
    - **Cambios**:
      - `pipeline.py`: `process_user_response()` — rama `else` devuelve `resolution="unrelated"` en lugar de `resolution="discard"`, limpia propuesta pendiente, registra auditoría.
      - `classifier.py`: Sin cambios — la clasificación `discard` es correcta; el cambio está en cómo `process_user_response()` interpreta el discard.
      - `agent-memory-harness-guard.md`: Nueva sección §9-B Ciclo 5 — Unrelated con 5 pasos documentados y registro de auditoría.
      - Nuevo evento `"unrelated"` en el audit_log (junto a proposed, approved, rejected, discarded, edited, phi_rejected, consolidated).
    - **Tests**: 89/89 tests de agente+memoria siguen pasando sin cambios.
    - **Problema**: El pipeline post-hoc (`_should_evaluate`) usaba heurísticas (triggers, regex) que fallaban en casos como `"porque"` matcheado como `"por qué"`, o conversaciones sin triggers explícitos que sí contenían información operativa valiosa.
    - **Solución**: El LLM devuelve la propuesta de memoria como **campo estructurado en la misma llamada** que genera la respuesta (`generate_answer_with_memory()` en `rag.py`). Elimina la necesidad de trigguers heurísticos y asegura precisión semántica.
    - **Cambios**:
      - `rag.py`: Nueva función `generate_answer_with_memory()` devuelve `(answer, proposal_or_None)`. Tiene `_MEMORY_SYSTEM_PROMPT_SUFFIX` que instruye al LLM evaluar memoria al final de la respuesta con formato JSON estructurado.
      - `graph.py`: `node_generate_answer()` usa `generate_answer_with_memory()` como primaria. Nuevo campo `llm_memory_proposal: Optional[dict]` en `AgentState`.
      - `pipeline.py`: `auto_evaluate_proposal()` acepta `llm_proposal: dict | None`. Nueva `_process_llm_proposal()` que prioriza el juicio del LLM, valida PHI + deduplicación, y construye texto de propuesta para el usuario.
      - `nodes.py`: `node_auto_evaluate_memory()` pasa `llm_memory_proposal` del estado a `_auto_evaluate()`.
    - **Tests**: 89/89 tests pasan (incluyendo fixes en tests de grounding y síntesis mixta que ahora parchan `generate_answer_with_memory`).

- **Entregable**: Sistema de memoria persistente para el agente LangGraph de HealthCore, que permite al agente recordar hechos operativos entre conversaciones con control explícito del usuario, auditoría completa y protección PHI.
- **Stack**: `Python 3.14`, `TinyDB >=4.9.0`, `LangGraph >=1.2.22`, `re` (clasificador estructural, sin LLM).
- **Arquitectura**: 6 módulos en `services/agent/memory/` + `rag.py`:
  - `store.py` (~716 líneas) — Backend TinyDB con 3 tablas: `memory_facts`, `pending_proposal`, `audit_log` (8 eventos: `proposed`, `approved`, `rejected`, `discarded`, `edited`, `phi_rejected`, `consolidated`, `unrelated`). Validación PHI dual (antes de mostrar propuesta y antes de escribir). **Lista blanca PHI** (`_PHI_WHITELIST` + `_PHI_WHITELIST_PREFIXED`). Consolidación automática (50 facts, TTL 90 días).
  - `classifier.py` (~590+ líneas) — **Clasificador estructural** con 6 ejes analíticos y pipeline de 11 pasos. Sin listas planas de patrones. Funciones: `_es_pregunta_o_condicional()`, `_hay_rechazo()`, `_hay_aprobacion()`, `_hay_edicion()`, `_es_solo_cortesia()`, `_es_falso_positivo_aprobacion()`, `_comando_memoria_negado()`. Umbrales: `approve ≥0.70`, `reject ≥0.75`, `edit ≥0.85`, `discard` por defecto. `Nunca se asume aprobación por silencio`. Guardas adicionales: negación con pronombres, contradicciones sí+no, contradicción no+comando de memoria (cualquier orden), comandos negados no suman approve, `"no olvides"` como approve, tag questions `"¿no?"` como confirmación.
  - `pipeline.py` (~802 líneas) — Orquestador con **28 trigger keywords** incluyendo 10 comandos explícitos. 3 extractores específicos + 2 de respaldo. Clasifica en 3 categorías (`operational`, `incident_pattern`, `staff_preference`). Rechazo PHI explícito con mensaje HIPAA/UK GDPR. **Nuevo**: `_process_llm_proposal()` que prioriza juicio del LLM sobre heurísticas.
  - `nodes.py` (~267 líneas) — 3 nodos LangGraph + 3 routers condicionales. `node_auto_evaluate_memory` maneja `phi_rejected` sin persistir. **Nuevo**: pasa `llm_memory_proposal` del estado a `_auto_evaluate()`.
  - `rag.py` — **Nueva función `generate_answer_with_memory()`**: devuelve `(answer, proposal_or_None)` en una sola llamada LLM. El LLM evalúa si la interacción contiene información memorable al final de la respuesta con formato JSON estructurado.
  - `__init__.py` — 6 principios: PHI-free, audit trail, no silent writes, no assumption by default, single pending proposal, dual PHI validation.
- **Integración en grafo**: `AgentState` con 8 campos (nuevo `llm_memory_proposal: Optional[dict]`). Flujo: `receive_question → load_memory_context → process_proposal` (si pendiente) / `classify_query` → ... → `generate_answer` (LLM produce answer + memory proposal) → `auto_evaluate_memory` (procesa propuesta LLM, valida PHI, presenta al usuario) → END.
- **PHI Compliance**: 8 patrones PHI. Validación TWICE + re-validación consolidación. **Lista blanca** para staff HealthCore. Marco HIPAA (US) + UK GDPR.
- **Tests**: **224 tests en toda la suite** (94 en `test_agent_graph.py` + 130 en otros módulos). 5 clases de memoria: `TestMemoryPHI` (8 tests), `TestMemoryStore` (5 tests), `TestMemoryClassifier` (**29** tests, cubriendo 6 ejes estructurales + pipeline 11 pasos + negación con pronombres + contradicciones sí+no + contradicción no+comando con separadores), `TestMemoryPipeline` (**11** tests, incluyendo **3 E2E**), `TestMemoryGraphIntegration` (**9** tests, incluyendo **5 E2E** de ciclo completo: approve, reject, discard, **PHI-rejected (2 rutas)**, **consolidación**). **Todos pasan**.
- **Bugs corregidos durante desarrollo (18 totales)**:
  1. `route_after_receive` → KeyError en aristas del grafo.
  2. PHI: falsos negativos acentos → expandido a `áéíóúñ`.
  3. `add_fact()` sin validación PHI → validación dual.
  4. Clasificador: ambigüedad como aprobado → `_should_evaluate` reescrito.
  5. Consolidación: facts nunca expiraban → `_DEFAULT_TTL_DAYS=90`.
  6. Loop RAG+tool → `route_after_synthesis()`.
  7. CONTEXT examples bloqueados → triggers movidos/expandidos.
  8. Word boundary `_QUERY_PHRASES` → `re.search(r"\b...\b")`.
  9. Faltaban comandos explícitos → 10 añadidos.
  10. `_extract_memory_candidate` sin fallback → extractores de respaldo.
  11. PHI rejection silenciosa → mensaje HIPAA/UK GDPR explícito.
  12. **"si llueve" → discard**: `\bs[ií]\b` emparejaba "si" condicional como "sí" approve.
  13. **"bien gracias" → discard**: `\bbien\b` daba approve falso en cortesías.
  14. **"cambia el horario" → discard**: "cambia" en lista de memorize daba approve en vez de edit.
  15. **Clasificador basado en listas planas**: Rediseño total a 6 ejes estructurales (resuelve bugs 12-14 de raíz).
  16. **Negación con pronombres no se detecta**: "no lo guardes", "sí, pero no" → approve. `_RE_REJECT_PHRASE` no aceptaba pronombres; comandos de memoria negados sumaban approve; contradicciones sí+no se resolvían como approve. Corregido con ampliación de regex, `_comando_memoria_negado()`, y paso 5b de contradicción. 7 nuevos tests.
  17. **Mensajes "unrelated" no limpiaban propuesta pendiente**: Mensaje de Diane Foster devolvía discard pero la propuesta quedaba atrapada. `process_user_response()` rama `else` ahora devuelve `resolution="unrelated"`, limpia propuesta y redirige a flujo normal.
  18. **Comando de memoria contradictorio con negación con separadores**: "no, pero guárdalo" → approve (creaba fact). `_comando_memoria_negado()` no detectaba negación cuando "no" y el verbo estaban separados por coma, "pero", puntos suspensivos. Nuevo Paso 5c detecta contradicción por posición: si `no.start() < comando.start()` → discard. 5 nuevos tests.
  19. **Contradicción comando+no en cualquier orden**: "guárdalo, no", "apúntalo, pero no" → approve (creaban facts). Paso 5c original solo detectaba cuando "no" precedía al comando; el orden inverso pasaba. Corregido a co-ocurrencia semántica (cualquier orden). Excepciones: tag questions `"¿no?"` y `"no olvides"`. 10 tests nuevos.
- **Auditoría post-auditoría (2026-11-22)**:
  - **PHI Bug 21 — Patrón de título Dr[aá].? sin ? en [aá]**: El patrón `Dr[aá]\.?` requería "a" entre "Dr" y el punto, por lo que "Dr. Smith" NO se detectaba como PHI — pasaba como safe. Corregido a `Dr[aá]?\.?` (con `?` después de `[aá]`). Esto era un bug crítico de compliance.
  - **PHI Bug 22 — Falsos positivos de título sin nombre**: "Dr. recomienda", "El Dr", "El paciente está estable" se detectaban como PHI porque:
    1. Patrón 0 (`\b[A-Z][a-z]+ [A-Z][a-z]+\b` con `re.IGNORECASE`) matchea "El Dr", "El paciente está" como nombres completos
    2. Patrón 7 (`paciente\s+[A-Z][a-z]+\s`) con IGNORECASE matchea "paciente está" 
    3. Patrón 11 (título + nombre) matchea "Dr. recomienda" porque IGNORECASE hace que "r" minúscula pase como mayúscula
  - **Solución**: Filtro en `validate_phi_safe()` que detecta cuando un título conocido (Dr, Sr, Sra, Paciente) va seguido de una palabra en minúscula → no es nombre propio → no es PHI. También se agregó filtro de prefijos ("El ", "La ", "En ") para falsos positivos del patrón 0 con IGNORECASE.
  - **Whitelist mejorada**: Staff names con 2+ palabras ahora se buscan como substrings dentro del match PHI. Ej: "Dr. Marcus Reid" → se busca "Marcus Reid" en whitelist → safe.
  - **Falso positivo "paciente id: P12345" corregido**: El filtro de título+minúscula descartaba incorrectamente IDs de paciente con "id:", "número:" — se agregó excepción para identificadores.
  - **PHI Bug 23 — Runtime `NameError` en filtro de artículos**: La variable `m_stripped` se referenció sin estar definida en el bloque de filtro de prefijos ("El ", "La ", "En "). Hubiera causado `NameError` con matches como "El Doctor García". Detectado por revisión manual post-tests. Corregido: `m_stripped` → `m`.
  - **Verificación**: 19/19 casos de prueba de PHI pasan (10 safe + 9 PHI), suite completa 305 tests.
- **Auditoría**: Criterios cumplidos. Ver `agent-memory-harness-guard.md` (~1370+ líneas, 13 secciones, tabla de 19 bugs corregidos, clasificador estructural documentado en §5.2, 5 ciclos completos documentados (incluyendo Ciclo 5 — Unrelated), 29+ tests de clasificador).
- **Verificación CONTEXT.md**: 6/6 ejemplos verificados al nivel de flujo completo (pipeline completo, no solo `_should_evaluate`):
  - ✅ Caso 1 (Johnson PHI) — rechazo explícito con mensaje HIPAA/UK GDPR
  - ✅ Caso 2 (Manchester cambio proceso) — propuesta creada correctamente
  - ✅ Caso 3 (Austin alerta no-show) — propuesta creada (antes bloqueado por falso positivo "En Austin")
  - ✅ Caso 4 (Diane Foster reporte) — propuesta creada (antes bloqueado por falso positivo PHI)
  - ✅ Caso 5 (consulta puntual) — correctamente sin propuesta
  - ✅ Caso 6 (agradecimiento) — correctamente sin propuesta
- **Documentación**: `agent-memory-harness-guard.md` — 7 preguntas de diseño respondidas, tabla comparativa de 5 alternativas de almacenamiento, **5 ciclos completos con trazas (incluyendo Ciclo 5 — Unrelated)**, política de consolidación, tabla de **17 bugs corregidos**, whitelist PHI documentada, rechazo explícito PHI documentado, evento de auditoría `"unrelated"`.
- **Branch**: `feature/agent-memory` (o rama activa del desarrollo).

### Hito 8 · Parte 2 — Harness y Guardrails (2026-12) — Refuerzo de integración (2026-11-23)

- **Entregable**: Blindaje del agente LangGraph contra inyección de prompt, jailbreaks, solicitudes fuera de dominio, y fugas de PHI mediante guardrails deterministas basados en regex (sin LLM en hot path).
- **Stack**: `Python 3.14`, `re` (guardrails regex puros, sin dependencia LLM), `LangGraph >=1.2.22` (nodos de guardrail en el grafo).
- **3 módulos** en `services/agent/guardrails/`:
  - `harness.py` — **System prompt hardening**: 5 reglas de restricción (Prohibited behavior, Constrained capabilities, Query-response binding, Completeness, Tone and empathy). Funciones: `build_system_prompt()` retorna string completo; `wrap_rag_context(context)` aísla contenido RAG con marcadores `BEGIN/END REFERENCE DOCUMENTS` + advertencia.
  - `content_guard.py` — **Clasificación de entrada**: **7 categorías** con prioridad estricta: `JAILBREAK (1) > PHI (2) > PERSONAL_TASK (3) > GRADUAL_EXTRACTION (4) > SMALL_TALK (5) > DOMAIN (6) > OUT_OF_DOMAIN (7)`. ~100 patrones regex. Función: `classify_input_scope(text)` retorna `GuardResult`. **OUT_OF_DOMAIN** es nueva: texto sin relación con HealthCore que no coincide con jailbreak/personal/small_talk se bloquea con respuesta predefinida, en lugar de pasarlo al LLM como DOMAIN.
  - `output_guard.py` — **Validación de respuesta**: 3 checkers independientes — `check_format` (vacío/largo), `check_leaked_instructions` (14 patrones EN+ES), `check_sensitive_data` (6 patrones PHI + 3 confidenciales).
- **Aislamiento de contenido externo**: `wrap_rag_context()` + sección `RAG_TOOL_ISOLATION` en el system prompt. Contenido RAG/tools nunca se trata como instrucción.

- **Nueva integración del harness en el grafo** (Bug fix 2026-11-23):
  - **Problema**: `build_system_prompt()` y `wrap_rag_context()` estaban importados en `graph.py` pero **nunca se usaban**. `_generate()` en `rag.py` solo enviaba un mensaje `user` con el antiguo SYSTEM_PROMPT embebido, sin usar mensaje `system` separado. `node_generate_answer` pasaba chunks RAG crudos sin aislar.
  - **Solución**:
    1. `_generate()`, `generate_answer()` y `generate_answer_with_memory()` en `rag.py` aceptan `system_role_message: str | None`. Cuando se proporciona, se envía como `{"role": "system"}` separado, con el contenido del usuario estructurado en secciones.
    2. `node_generate_answer()` en `graph.py` llama a `build_system_prompt()` para construir las instrucciones del harness y las pasa como `system_role_message`.
    3. `wrap_rag_context()` se usa para aislar el contenido RAG con marcadores `BEGIN/END REFERENCE DOCUMENTS` y advertencia de solo-referencia-factual.
    4. Nuevas helpers: `_build_context_from_chunks()` y `_build_memory_context_text()`.
    5. El trace event incluye `"harness_active": True` para observabilidad.
  - **OUT_OF_DOMAIN**: Nueva clasificación en `ScopeClassification` (blocked=True). El fallback por defecto en `classify_input_scope()` cambió de `DOMAIN` (blocked=False, confianza 0.60) a `OUT_OF_DOMAIN` (blocked=True, con respuesta de redirección). Keywords de dominio ampliadas con `auditor`, `audit`, `datos de salud`, `health data`, `salud`.
  - **Tests**: 6 nuevos tests en `TestGraphHarnessIntegration` (test_agent_graph.py) que verifican: uso de system prompt, wrapping de contexto RAG, chunk malicioso no contamina system prompt, flujo completo chunk→wrap→system_role_message limpio, flag `harness_active` en traza, ruteo OUT_OF_DOMAIN → __end__. Test renombrado en test_guardrails.py: `test_long_random_text_classifies_as_out_of_domain` (antes domain).

- **Bugs corregidos post-auditoría (2026-11-23)**:
  1. `guardrail_summary()` en `graph.py` — Usaba `event.get("event", "")` pero los eventos usan clave `"node"`. Metadata de output_guardrail esperaba shape incorrecto. Fix: `event.get("node", "")`, usar `status` para fallback, iterar strings directamente.
  2. `r"\bphi\b"` duplicado en `_DOMAIN_KEYWORDS` de `content_guard.py:83`. Eliminada duplicación.
  3. Cobertura de tests a nivel nodo: 6 nuevos tests (jailbreak block, domain pass, clean output, PHI replacement, 2 routing de auto_evaluate_v2).

- **Tests**: **62 tests de guardrail** en `test_guardrails.py` + **125 tests** en `test_agent_graph.py` (113 agente+memoria + 12 harness) = **187 tests núcleo agente+guardrails**, **317 tests totales workspace**. **0 fallos**.
- **Documentación**: `agent-memory-harness-guard.md` — §13-19 actualizado con OUT_OF_DOMAIN, §14 ampliado con TestGraphHarnessIntegration (12 tests), bugfixes post-auditoría documentados, §18 auditoría a 35/35 puntos.
- **Branch**: `feature/agent-memory`.

## MCP Server — Bugfix: ContextVar de auth desincronizado (2026-11-18)
- **Problema**: `tools/list` funcionaba con token válido (devuelve 200 + lista de 10 tools), pero `tools/call` siempre fallaba con error interno `PermissionError("Autenticación requerida")` aunque el token fuera válido.
- **Causa raíz**: `mcpauth.MCPAuth` crea su propio `ContextVar` interno (`mcp_auth_context`) donde guarda `AuthInfo`. Por otro lado, `_require_tool_scope()` y `_current_client()` en `server.py` leen desde `_auth_info_context` en `mcps/auth.py`. Como son **dos ContextVars distintos**, `get_auth_info()` siempre devolvía `None`, causando que cada `tools/call` lanzara `PermissionError`.
- **Corrección**: Al construir `MCPAuth`, pasar nuestro `_auth_info_context` como `context_var`:
  ```python
  from mcps.auth import _auth_info_context as _mcp_auth_ctx
  mcp_auth = MCPAuth(server=auth_server_config, context_var=_mcp_auth_ctx)
  ```
  Así mcpauth escribe en el mismo ContextVar que leen nuestras funciones.
- **Limpieza**: Se eliminó el `send_wrapper` en `SelectiveBearerASGI` que intentaba (sin éxito) leer `scope["state"]["auth_info"]` para propagarlo al ContextVar — mcpauth nunca escribe en `scope["state"]`, solo en su ContextVar.
- **Tests**: Se reemplazó `test_client_subject_in_error_response` (que solo verificaba HTTP 200) por `test_tool_call_with_valid_token_returns_result` que:
  - Verifica HTTP 200 real con `result` en el body SSE (no error de auth)
  - Verifica que el log contenga `client=dev-agent` via `caplog`
  - Se agregó `test_tool_call_fails_without_token` para verificar 401 real
  - **10/10 tests pasan**
- **Archivos modificados**: `mcps/server.py` (pasar context_var a MCPAuth), `mcps/auth.py` (eliminar send_wrapper, actualizar docstring), `tests/test_mcp_server_auth.py` (test robusto con caplog).
- **Branch**: `feature/mcp-oauth-tools`.

## Agente de Soporte LangGraph — HealthCore — Part 2: External Tools (2026-11-15)
- **Entregable**: Extensión del agente de soporte LangGraph con **tools externas** que consultan datos operativos en tiempo real (gestor de incidentes + gestor de inventario). El agente decide automáticamente si necesita RAG, tool(s) o ambos, sin que el usuario lo especifique.
- **Stack**: `langgraph v1.2.22`, `langgraph-checkpoint v4.3.2`, Python 3.14, `uv 0.13.5`, `httpx 0.28.1`. La lógica RAG existente se reutiliza sin modificar.
- **Tools** (`services/agent/tools/`):
  - `incidents.py` — `query_incident_by_id(ticket_id: int) → dict`, `query_incidents_list(status, category, origin, branch) → list[dict]`. Consulta `http://incidents-backend:8010/api/incidents`. Sin auth. Timeout 5s.
  - `inventory.py` — `query_product_by_id(product_id: int) → dict`, `query_products_list(category, name_search) → list[dict]`. Consulta `http://backend:8000/inventory/products`. Auth: JWT Bearer desde `AGENT_JWT_TOKEN`. Timeout 5s.
  - `__init__.py` — helper compartido `_get_service_url(env_var, default)`.
  - Ambas tools son **solo GET** (lectura). Toda escritura está explícitamente prohibida.
  - Fallback verificable: TimeoutException, RequestError, 404, 401, non-200 → siempre devuelven `{"error": "..."}`.
- **Grafo ampliado** (`services/agent/graph.py`): Ahora **10 nodos**:
  - **Nuevos**: `classify_query` (routing por patrones regex), `query_incident`, `query_inventory`, `synthesize_tool_answer`.
  - **Nuevas aristas**: `route_after_classify`, `route_after_tool`, `route_after_retrieve_v2` (considera tools), `route_after_generate` (extendida para tools).
  - **Compatibilidad**: `route_after_retrieve = route_after_retrieve_v2` para no romper imports de tests de la Parte 1.
- **Clasificación** (`node_classify_query`): 5 familias de patrones regex:
  - Incidentes: ticket, incidencia, estado, #ID, seguimiento, etc.
  - Inventario: stock, inventario, producto, suministro, SKU, etc.
  - RAG: política, procedimiento, cobertura, seguro, cita, HealthCore, etc.
  - Produce 5 decisiones: `"rag"`, `"incident"`, `"inventory"`, `"rag+incident"`, `"rag+inventory"`.
  - Sin señales → default RAG.
- **Flujo del grafo**: receive → classify → (RAG / incident / inventory) → si combinado: tool → RAG → generate → synthesize; si tool-only: tool → synthesize.
- **Tests** (`tests/pipelines/test_agent_graph.py`): **32 tests** (20 originales + 12 nuevos).
  - **TestExternalToolRouting** (5): routing a incident, policy→rag, stock→inventory, combinado, default→rag.
  - **TestToolFallbackBehavior** (5): timeout, 404, synthesize con todas fallidas, extracción de ticket ID, trace_events con decisión.
  - **TestMixedSynthesis** (2): síntesis rag+incident combina fuentes; modo combinado con falla de tool incluye RAG + error honesto.
  - **32/32 pasan**. Tests RAG (26) siguen intactos. Suite total: 58 tests agente + RAG.
- **Bug fixes aplicados (2026-11-15)**:
  - `route_after_retrieve_v2` corregido: prioriza `generate_answer` cuando hay chunks RAG, en vez de saltar a `synthesize_tool_answer`.
  - `route_after_generate` ampliado: detecta `tool_errors` además de `tool_results` para enrutar a `synthesize_tool_answer`.
  - `node_synthesize_tool_answer` mejorado: en modo combinado (rag+incident, rag+inventory) incluye RAG aun si la tool falla.
  - routing_log corregido: `receive_question → classify_query` en vez de `→ retrieve_context`.
  - `regenerate_traces.py` reescrito sin mocks: usa servicios reales (Qdrant, LLM, Incidents API) con timestamps reales y branch_label correcto "London West End".
  - Trazas regeneradas con ticket real (ID 2) y timestamps reales: RAG 5788ms, Incidente 41ms, Combinado 2892ms, Fallback 1649ms.
- **Documentación**: `agente-soporte-langgraph.md` actualizada con §Parte 2 completa (~200 líneas): resumen, estructura, contrato, diagrama, estado, clasificación, timeout, fallback, trazabilidad, evaluación, auth, checklist.
- **Auditoría (2026-11-15)**: **9/9 acceptance criteria cumplidos** — verificado contra código fuente + tests. Incluye: contratos tipados + gestor real + auth, solo GET, timeout 5s, fallback sin inventar, routing automático, única responsabilidad por nodo, trace con fuentes/orden, 5 evals de routing, tool de inventario sigue mismas reglas.
- **Branch**: `feature/langgraph-external-tools`. Pusheado y completado.
- **Commits**: `ca2ca1d` (HEAD, regenerate_traces.py usa servicios reales), `8b2c92c` (bugs 1,2,3), `9d5e33c` (traces anteriores con mocks), `15c93c7` (documentación), `7dfa797` (herramientas externas base).

## Modelo de pronóstico de ventas — Regresión con XGBoost (Oct–Nov 2026)
- **Entregable**: pronóstico honesto de ingresos mensuales de HealthCore, con split temporal 8 años/2 años, 4 métricas exigidas (MSE, PSI, Gini, K2) + MAPE, y visualización con banda de variabilidad. Documentado íntegro en `predic-ventas-model-regresion.md`.
- **Entorno**: `uv 0.13.5` (`uv init --bare --name healthcore-sales-forecast`), Python 3.14, pandas 3.1, scikit-learn 1.9.1, xgboost 3.4.1, matplotlib 3.11.2, pytest 9.2.0. Nunca `pip install` ni `pipenv`.
- **Datos**: `data/raw/healthcore_sales.csv` (provisto, no simulado), serie `consolidated`, 120 meses 2016-01..2025-12.
- **Split sin fuga**: entreno 2016-01..2023-12 (96 meses − 12 de warm-up = 84), prueba 2024-01..2025-12 (24). `StandardScaler` ajustado SOLO con train. Features causales (lags/rodantes mirando solo atrás).
- **Modelo**: XGBoost regularizado (max_depth=4, lr=0.03, gamma=0.5, L1=0.5, L2=2.0). Justificación vs Random Forest: pocos datos, boosting capta estacionalidad; explicabilidad equivalente; RF sería candidato con más volumen y menos estacionalidad.
- **Resultados (test 2024-2025, post-corrección anti-fuga)**: RMSE $154.910 (5,52 % del ingreso medio), MAPE 3,47 % (máx 8,85 %), Gini 0,8188, K2/R² 0,8104, PSI nivel 6,03 (alto por crecimiento estructural), PSI crecimiento ajustado 0,2718. Walk-forward RMSE 2024=$134.459, 2025=$136.268 → σ=$135.363, banda ±$265.312, cobertura 21/24 (87,5 %).
- **Paquete**: `scripts/sales_forecast/{data,metrics,model,visualize,compare_before_after}.py` + entrypoint `scripts/train_sales_forecast.py`. Artefactos: `reports/sales_forecast/metrics.json`, `forecast_vs_real.png`, `decomposition.png`, `forecast_comparison_before_after.png`.
- **Tests**: `tests/pipelines/test_sales_forecast_split.py` — **19/19 en verde** (split 8/2, anti-fuga de lags/escalado, blindaje de nombres de features, +5 tests de causalidad anti-fuga rodantes).
- **Auditoría (2026-10-31)**: 7/7 acceptance criteria verificados en ejecución con recálculo independiente. Hallazgos corregidos: (a) `test_escalado_...` era tautológico → reemplazado por reconstrucción exacta `(x−μ_train)/σ_train` y **validado con mutation test** (inyectar `fit(train+test)` hace fallar el test); (b) `rev_ma_12` (máximo rodante con nombre de "moving average") → `rev_max_12`, `rev_mi_12` → `rev_min_12`; (c) docstring decía "~107 filas" → corregido a 84; (d) `year_num` código muerto eliminado. Métricas idénticas bit a bit tras el refactor.
- **Auditoría extendida (2026-11-02, fuga de datos en rolling features)**: detectado que `rev_ma_3`, `rev_min_12`, `rev_max_12` y `rev_std_12` usaban `rolling()` sin `shift(1)`, incluyendo `revenue_usd[t]` (el target). Corregido en `_add_causal_features()`. Añadidos 5 tests de causalidad en `TestCausalidadAntiFugaRodantes`. RMSE mejoró −1,54 % (de $157.336 a $154.910) pese a que el modelo no dependía de la fuga. Añadida descomposición aditiva del target (tendencia 55,0 %, estacional 39,5 %, residual 2,9 %). Añadido análisis de justificación de features (visits_count 48,9 %, avg_revenue_per_visit 0,9 %). Generada gráfica comparativa `forecast_comparison_before_after.png`. Nueva documentación §11 en `predic-ventas-model-regresion.md`.
- **Branch**: `feature/sales-forecast-model`. CONTEXT.md NO modificado (ruta protegida).

## Cola de Tareas Asíncronas — Redis + Celery (2026-10-24)
- **Entregable**: Sistema de cola de tareas asíncronas para desacoplar operaciones pesadas de la API, utilizando **Redis como broker** y **Celery como framework de tareas**.
- **Arquitectura**: FastAPI (Productor) → Redis (Broker) → Celery Worker (Consumidor) → TinyDB (DLQ). Flower como monitor en tiempo real.
- **Endpoint convertido**: `POST /api/incidents/analyze` ahora responde **HTTP 202 Accepted** inmediatamente con `task_id` y `status: PENDING`. El worker procesa el CSV en segundo plano. Header `Location: /tasks/{task_id}`.
- **Endpoint de consulta**: `GET /tasks/{task_id}` devuelve el estado (`pending | started | success | failure`), resultado, error, y si falló, la entrada de la **Dead Letter Queue (DLQ)**.
- **Módulo Celery**: `services/celery_app.py` — instancia `Celery("healthcore", broker=REDIS_URL, backend=REDIS_URL)`. Tarea `analyze_incidents_csv` con `HealthCoreTask(Task)` base para DLQ automática.
- **Tarea base**: `HealthCoreTask(Task)` — `abstract=True`, `max_retries=3`, `default_retry_delay=10`. Método `on_failure` que persiste en TinyDB (`data/process/task_dlq.json`) cuando se agotan los reintentos.
- **Configuración de reintentos**: `autoretry_for=(Exception,)`, `max_retries=3`, `retry_backoff=True` (10s, 20s, 40s), `retry_backoff_max=300`, `retry_jitter=True`.
- **Configuración de colas**: `healthcore.default` (por defecto) y `healthcore.dlq` (cola DLQ explícita en Celery para inspección manual).
- **Señales Celery**: `task_success`, `task_failure` (con traceback 500 chars), `task_retry` (con intento actual), `task_revoked`, `on_after_configure` — logging completo del ciclo de vida.
- **DLQ (Dead Letter Queue)**: Persistencia en TinyDB (`data/process/task_dlq.json`) con `task_id`, `task_name`, `attempt`, `error`, `exc_type`, `args`, `kwargs`, `timestamp`. Consultable automáticamente por `GET /tasks/{task_id}` en estado `failure`.
- **Infraestructura Docker**: 3 nuevos servicios en `docker-compose.yml`:
  - `redis`: `redis:7-alpine` con `--appendonly yes --maxmemory-policy noeviction`, puerto 6379.
  - `flower`: `mher/flower:2.0` con `CELERY_BROKER_URL` y `CELERY_RESULT_BACKEND`, puerto 5555.
  - `celery-worker`: mismo build que backend, `concurrency=4`, `max-tasks-per-child=100`, flag `-E` para eventos. Red `healthcore-net`.
- **Auditoría**: **10/10 criterios ✅** (AC-1 a AC-8). Verificado: Redis 7.4.11 standalone responde PING (AC-1), 29.61ms media en encolado (AC-2), estados PENDING→RETRY→SUCCESS/FAILURE (AC-3), `max_retries=3`, `retry_backoff=True`, `acks_late=True` (AC-4), DLQ tras 3 fallos con `task_id`, `attempt=3`, `error`, `timestamp` (AC-5), worker `celery@codespaces-04e6df` conectado a Redis (AC-6), payloads ligeros <1.2KB (AC-7), Flower HTTP 200 en localhost:5555 (AC-8). Documentado en `memories/repo/audit-async-queue.md`.
- **Nota**: La API principal (`services/api/`) no arranca por incompatibilidad SQLModel 0.0.48 + Python 3.14 (annotationlib ForwardRef), pero la infraestructura Redis/Celery/DLQ funciona perfectamente independientemente.
- **Archivos clave**: `services/celery_app.py` (módulo Celery con tarea, DLQ, señales), `services/api/routes/tasks.py` (GET /tasks/{task_id}), `services/api/main.py` (POST /api/incidents/analyze → 202), `docker-compose.yml` (redis, flower, celery-worker).

## Proceso de segundo plano — Job nocturno de telemetría (2026-10-22)
- **Entregable**: script nocturno independiente de la API que exporta telemetría del día anterior a CSV, lanza el pipeline (Hito 6) como subprocess, y deja constancia en BD (`job_runs`).
- **Nueva tabla**: `job_runs` (id, job_name, target_date, status, started_at, finished_at, error_message, created_at, exported_rows, csv_path) con UNIQUE(job_name,target_date) e índice idéntico. DDL en `data/migrations/0002_job_runs.sql`. NO se fusiona con `reporting.pipeline_runs` (capa de ETL Hito 6, lock propio por periodo).
- **Máquina de estados**: `pending → processing → completed | failed`, con reintento `failed → pending`. El lock ES el estado `processing` (UPDATE atómico `WHERE status='pending'`); no hay tabla/flag de bloqueo aparte. Nunca quedan registros en `processing` (sin zombis).
- **Trigger**: **crontab** `0 2 * * *` (02:00 UTC) o **contenedor scheduler dedicado** (recomendado en prod); documentado en `background-processes.md` (§2, 2.1–2.3). Prohibido dentro de FastAPI (no APScheduler/@repeat_every/lifespan).
- **"Ayer"** = `now(UTC).date() - timedelta(days=1)`; override con `TARGET_DATE=YYYY-MM-DD` o `--target-date` (var opcional para pruebas).
- **Pipeline real invocado**: `python -m data.pipelines.runner` (el `telemetry_kpi_daily` de la referencia NO existe). Ajustado en `nightly_service._DEFAULT_PIPELINE_CMD`.
- **Observabilidad (§6)**: logger estable `healthcore.nightly`; INFO (started/finished/skip_duplicate/aborted) y ERROR (failed con traceback). Cada línea: timestamp + `job=nightly_export` + `status=`. Helper central `log_result()` en `services/job_runner.py`.
- **Archivos nuevos**: `services/job_runner.py` (control de estado + log_result), `services/nightly/{__init__,nightly_service}.py`, `scripts/nightly_export.py`, `data/migrations/0002_job_runs.sql`, `data/migrations/README.md`, `tests/nightly/*`, `background-processes.md`.
- **Tests**: **30/30** pasan vía `.venv-nightly/bin/python -m pytest tests/nightly -q` (incl. caplog de logs INFO/ERROR). Entorno de tests: `.venv-nightly/` (NO commitear, añadido a .gitignore).
- **Auditoría del proceso**: **10/10 criterios ✅** con evidencia estática + demostración en vivo (`scripts/_audit_hito7_demo.py` lanza DOS instancias CLI reales en carrera contra una BD SQLite compartida). La demo es reproducible (limpia su BD y el CSV residual de `data/raw/`, `exit=0`). Hallazgos y soluciones del desarrollo registrados en `background-processes.md` §11 (reintento tras fallo con `reset_failed_to_pending`, módulo en `services/job_runner.py`, logger estable `healthcore.nightly` + `log_result`, pipeline real `python -m data.pipelines.runner`).
- **Humo completo Supabase (2026-10-22)**: corrida real con `--skip-pipeline --target-date 2026-10-10` → `completed`, 12 filas en `data/raw/telemetry_2026-10-10.csv`, fila en `job_runs` (`exported_rows=12`); 2ª corrida → `noop_export`. Se validó por primera vez la rama Postgres contra Postgres real. Hallazgo menor: `schema_migrations` count 0 en Postgres (ver `background-processes.md` §11.6).
- Branch `2do-plano` activo. **NO commitear** (usuario sigue iterando).

## Evaluación técnica del modelo de pronóstico — sesgo/varianza (2026-11-08)

- **Propósito**: Evaluación formal bias/varianza del modelo XGBoost de HealthCore según ticket técnico que exige: (1) diagnóstico underfitting/overfitting, (2) estabilidad a través de splits, (3) acción correctiva específica. **No se acepta "el modelo funciona bien" sin evidencia.**
- **Diagnóstico**: **OVERFITTING** confirmado por dos fuentes independientes (CV + curva de aprendizaje), con brecha train/val RMSE de $131,147 (ratio 28.7x).
- **Validación cruzada temporal**: `TimeSeriesSplit` de scikit-learn con 5 folds (12 meses de validación cada uno). Verificado sin shuffle y sin fuga de features entre folds mediante `assert_chronological_folds()` y `assert_features_causal()`.
- **Curva de aprendizaje**: ventana expansiva 24→72 meses, bloque fijo de validación (2023). Train RMSE consistentemente bajo (~$4,727), Val RMSE alto y estable (~$135,875). **Las curvas NO convergen** — overfitting confirmado.
- **Métricas**: RMSE (principal, justificado por contexto de negocio — penaliza errores grandes cuadráticamente, sobreestimar es más costoso que subestimar para HealthCore) y MAE (complemento interpretable).
- **Acción correctiva** (3 pasos priorizados): (1) eliminar `visits_count` y `avg_revenue_per_visit_usd` (componentes directos del target, ~50% importancia), (2) aumentar `reg_alpha` de 0.5 a 2.0, (3) reducir `max_depth` de 4 a 3. **No se recomienda aumentar datos** — la curva muestra que el error de validación no mejora de 24 a 72 meses.
- **Veredicto**: ❌ **NO APROBADO para staging** sin las correcciones de §5.3.
- **Archivos nuevos**: `scripts/run_model_evaluation.py` (entrypoint de evaluación), `data/eval/evaluation_report.md` (reporte técnico completo), `data/eval/learning_curve_{rmse,mae}.png` (curvas de aprendizaje), `tests/pipelines/test_temporal_cv.py` (7 tests de validación cruzada temporal).
- **Tests**: **64/64 en verde** — `test_temporal_cv.py` (7), `test_sales_forecast_split.py` (19), `test_pipeline.py` (38). Comando CI: `uv run python -m pytest tests/pipelines/ -v`.
- **Fix de test**: `test_no_overlap_between_consecutive_folds` corregido — validaba incorrectamente que entrenamiento de folds consecutivos no se solapara (imposible en ventana expansiva). Corregido para verificar: (a) train_max < val_min dentro de cada fold, (b) val_i_max < val_{i+1}_min entre folds.
- **Branch**: `feature/regression-model-eval`.

## RAG Knowledge Base — HealthCore Knowledge Q&A (2026-11-09/10)

- **Entregable**: Sistema completo de Retrieval-Augmented Generation (RAG) que permite al equipo de HealthCore hacer preguntas en lenguaje natural sobre las políticas de la clínica (cobertura de seguros, política de citas, proceso de referencias, checklist de pacientes nuevos). Responde con información extraída de los documentos oficiales, no con conocimiento alucinado del LLM.

- **Arquitectura**: 3 fases + documentación + tests:
  - **Fase 1 (data/process/rag.py)**: `setup()`, `embed()`, `chunk_document()`, `load_corpus()` — lectura de 4 documentos Markdown, chunking semántico por párrafos, embedding con `pplx-embed-v1-0.6b` (1024-dim), indexado en Qdrant.
  - **Fase 2 (data/pipelines/rag.py)**: `retrieve()`, `query()`, `generate_answer()` — pipeline de recuperación + generación con `deepseek-v4-flash`.
  - **Fase 3 (services/api/routes/)**: `knowledge.py` (endpoint público POST /knowledge/query), `rag.py` (router FastAPI POST /rag/query + GET /rag/health + POST /rag/setup), `rag_standalone.py` (servidor FastAPI standalone que evita SQLModel/Python 3.14).
  - **Fase 4 (uis/website/app/rag/page.tsx)**: Página de chat Q&A para coordinadores en el website público (HealthCore Q&A).
  - **Fases 5-6**: Tests (26 tests, todos pasando) + Documentación bilingüe EN/ES.

- **Stack técnico**: Python 3.14, `uv 0.13.5`, `qdrant-client 1.20.0` (usa `query_points()`, NO `search()`), `openai 3.49.0`, `fastapi 0.162.4`. Qdrant Docker (`qdrant/qdrant:v1.20.0`, puerto 6333, colección `healthcore_knowledge`, 9 puntos, Cosine, 1024-dim).

- **Modelos de IA**: Ambos vía 4Geeks AI API (`LLM_HOST` + `API_KEY` via OpenRouter):
  - **Embedding**: `madrid-spain/openrouter/perplexity/pplx-embed-v1-0.6b` (1024-dim).
  - **Generación**: `madrid-spain/openrouter/deepseek/deepseek-v4-flash` (modelo de razonamiento, puede devolver `content=None` — fallback extrae la última frase de `reasoning_content`).

- **Afinación de chunking (auditada 2026-11-11)**: `CHUNK_MAX_CHARS=450` (de 500), `CHUNK_MIN_CHARS=140` (de 200). Produce **12 chunks** (de 9) — los 4 documentos tienen exactamente 3 chunks cada uno, cumpliendo CONTEXT.md §5 ("al menos 3 chunks por documento"). Cambio necesario porque 3 documentos tenían solo 2 chunks (insurance-coverage, referral-process, new-patient-checklist): el último párrafo de cada uno era demasiado corto (< 200 chars) para ser chunk independiente. Solución: reducir CHUNK_MAX_CHARS a 450 fuerza división de párrafos largos (US/UK en insurance-coverage), y CHUNK_MIN_CHARS a 140 captura los párrafos de cierre.

- **Afinación de recuperación**: `DEFAULT_MIN_SCORE=0.3` (de 0.5), `DEFAULT_K=5`. Scores top-1 para preguntas relevantes: ≈0.42–0.63 (media ≈0.48). El umbral 0.3 captura todos los resultados relevantes mientras filtra ruido (< 0.25).

- **Recall@3 audit (2026-11-11)**: Evaluación post-auditoría con `scripts/eval_rag_recall.py` corriendo las 8 queries de `data/eval/test-queries.json` contra Qdrant en vivo. Resultado: **8/8 = 100% ≥ 80% ✅ APROBADO**. Documentado en `docs/rag/rag-design.md` §8.1 con tabla detallada de aciertos.

- **Fix de import en page.tsx**: La línea `import RagChat from "./RagChat"` no resolvía porque RagChat.tsx está en `uis/website/components/`, no en `uis/website/app/rag/`. Corregido a `import RagChat from "@/components/RagChat"` usando el path alias `@/*` definido en `tsconfig.json`.

- **Problema resuelto (SQLModel)**: La API principal (`services/api/`) no arranca por incompatibilidad SQLModel 0.0.48 + Python 3.14. Solución: `rag_standalone.py` — servidor FastAPI autónomo que no importa `models.py`. Corre con `QDRANT_URL=http://localhost:6333 uv run python services/api/routes/rag_standalone.py` desde la raíz.

- **Servidor standalone**: Corre en puerto 8000 con:
  - `GET /` — UI HTML completa con 4 preguntas de ejemplo, loading spinner, sticky input, errores visibles.
  - `POST /knowledge/query` — endpoint público.
  - `POST /rag/query`, `GET /rag/health`, `POST /rag/setup` — endpoints del router RAG.
  - CORS habilitado para desarrollo.

- **Pruebas en vivo**: Las 4 preguntas de ejemplo devuelven respuestas correctas:
  1. "¿Qué seguros médicos aceptan?" → lista de seguros.
  2. "¿Cómo puedo agendar una cita?" → pasos del proceso.
  3. "¿Cómo funciona el proceso de referencias internas?" → explicación del proceso.
  4. "¿Qué documentos necesito llevar a mi primera cita?" → checklist.

- **Tests**: 26/26 pasando (`uv run pytest tests/pipelines/test_rag.py -q`).

- **Documentación**: `docs/rag/rag-design.md` y `docs/rag/rag-design.es.md` — 13 secciones bilingües completas con: overview arquitectónico, modelos, colleción Qdrant, chunking strategy (9 chunks, 500 chars), retrieval params (k=5, min_score=0.3), prompt template, test queries, KPI targets (Recall@3 ≥ 80%), end-to-end flow, chunking detallado con tabla real, embedding practices (model separation, threshold tuning history v1→v2, preprocessing, API config), files layout, production upgrade path.

- **Archivos clave**:
  - `data/process/rag.py` — chunking, embedding, setup.
  - `data/pipelines/rag.py` — retrieve, query, generate_answer.
  - `services/api/routes/rag.py` — FastAPI router.
  - `services/api/routes/rag_standalone.py` — servidor standalone + HTML UI.
  - `services/api/routes/knowledge.py` — endpoint público.
  - `uis/website/app/rag/page.tsx` — UI de chat.
  - `tests/pipelines/test_rag.py` — 26 tests.
  - `data/eval/test-queries.json` — 8 queries de evaluación.
  - `docs/rag/rag-design.md` + `docs/rag/rag-design.es.md` — documentación bilingüe.

- **Branch**: `feature/rag-knowledge-base`. Commiteado y pusheado.

## Entregables activos en el repositorio

### Telemetría: Fase 5 — Autenticación funcional para backoffice (2026-10-11)
- **Problema**: No existían usuarios en TinyDB, API principal no arranca (SQLModel + Python 3.14 incompatible), servidor standalone no tenía auth.
- **Seed de usuarios**: `services/telemetry/seed_auth_users.py` — script autónomo que crea `admin@healthcore.com` / `adminpass456` (admin) y `test@healthcore.com` / `securepassword123` (user) en TinyDB sin depender de `models.py`.
- **Módulo auth standalone**: `services/telemetry/auth_standalone.py` — bcrypt, JWT (jose), consultas TinyDB. No depende de `services/api/`.
- **Endpoints añadidos a `server_standalone.py`**: `POST /auth/login` y `GET /auth/me`. La cadena completa navegador → Next.js proxy → standalone server funciona correctamente.
- **`JWT_SECRET_KEY`** añadida a `services/api/.env`.
- **Credenciales**: `admin@healthcore.com` / `adminpass456` (admin), `test@healthcore.com` / `securepassword123` (user).

### Telemetría: Fase 4 — Dashboard de telemetría en backoffice (2026-10-11)
- **Nueva página**: `uis/backoffice/app/telemetry/page.tsx` — protegida por AuthGuard.
- **5 paneles visuales**: events_per_day (barras), error_rate_by_type (barras coloreadas), latency_percentiles_by_route (tabla), event_type_distribution (barras), auth_failure_rate (barras duales + línea).
- **Selector de fechas**: dos campos datetime-local que recargan el reporte.
- **Proxy**: rewrite en `next.config.ts` (`/api/telemetry/report` → `http://localhost:8000/telemetry/report`). Se eliminó el route handler bugado anterior.
- **Navegación**: enlace "Telemetry" añadido al `BackofficeHeader`.
- **Datos de prueba**: 100 eventos insertados en Supabase (distribuidos en 14 días, 5 tipos).

### Telemetría: Fase 3 — auth_failure_rate_by_day (2026-10-11)
- **Nueva función**: `auth_failure_rate_by_day()` en `services/telemetry/analysis.py`.
- **SQL**: filtra `event_type IN ('auth_login_failed', 'auth_login_succeeded')`.
- **Pandas**: groupby día → pivot → `failure_rate_pct = (failed / total * 100).where(total > 0, 0).round(2)`.
- **Integrada en `run_all()`**: se sirve automáticamente en `GET /telemetry/report`.
- **Auditoría 10/10**: todos los acceptance criteria cumplidos.

### Telemetría: correcciones de auditoría (2026-10-06)
- Reglas de privacidad compartidas cierran valores sensibles en campos allowlisted; timestamp ISO estricto y expiración restaurada con identidad efímera segura.
- Productor `services/api/telemetry_delivery.py` captura señales en origen y las envía por lotes sin depender del navegador. Caducidad se deduplica tras acuse del stub. Cola en memoria, sin almacenamiento analítico.
- Proxy frontend delega HTTP a `TelemetryService.server.ts`; componentes siguen usando solo `track()`.

## Hito 9 · Parte 1 — RFP Intake Pipeline (2026-12) — ✅ COMPLETADO

> **Estado**: 21 archivos creados, 2 modificados, 16/16 tests pasando, API verificada, 3 tablas PostgreSQL creadas.

### Arquitectura del Pipeline

Pipeline **async** de 6 nodos en `data/pipelines/rfp_intake/graph.py`. Los 3 workers del Nodo 5 se ejecutan **en paralelo** via `asyncio.gather()` con `asyncio.to_thread()` para las funciones síncronas. Cada nodo persiste su resultado en PostgreSQL.

```
PDF → [N1: Convert] → Markdown → [N2: Readability + Metadata] → [N3: Classify]
→ [N4: Orchestrate] → [N5: Workers] → [N6: Synthesize] → Complete
```

### Componentes Creados

**Pipeline (`data/pipelines/rfp_intake/`)**:
- `models.py` — Enums (TicketStatus, DepartmentId, ProgramType), modelos (ReadabilityMetrics, RfpMetadata, DepartmentSection, RfpPipelineState, HandoffPayload)
- `converter.py` — PDF → Markdown via MarkItDown (con fallback PyPDF2)
- `readability.py` — 5 métricas (Flesch, Kincaid, Gunning Fog, Coleman-Liau, ARI) + word_count via py-readability-metrics
- `classifier.py` — Two-stage: reglas pre-filter → OpenAI gpt-4o-mini con `response_format={"type": "json_object"}`. Patrones NO-RFP (pitch, CV, newsletter) y YES-RFP ("request for proposal" + programa)
- `orchestrator.py` — Descompone documento en 3 secciones por departamento usando patrones regex
- `workers.py` — Workers LLM con fallback rule-based: Revenue (moneda por país), Clinical (PHI detection), Compliance (BAA/DPA según país). **Nunca inventa cifras**
- `synthesizer.py` — Consolidación final con executive summary, conflict detection, alerts, handoff payload
- `graph.py` — **`run_rfp_pipeline(ticket_id, pdf_path)`**: async, 6 nodos. Nodo 5 workers en paralelo via `asyncio.gather()`. Cada nodo persiste a PostgreSQL
- `tasks.py` — Celery task `process_rfp_pipeline`, autoretry_for=(Exception,), max_retries=2

**Backend (`services/api/`)**:
- `models_rfp.py` — 3 tablas SQLModel: `TicketDB` (rfp_tickets), `RfpMetadataDB` (rfp_metadata), `DepartmentSectionDB` (rfp_department_sections)
- `routes/rfp.py` — 4 endpoints: upload (202), list (GET /rfp/tickets), detail (GET /rfp/tickets/{id}), reprocess (POST /rfp/tickets/{id}/reprocess)
- `main.py` — router `rfp_router` registrado

**Frontend (`uis/backoffice/app/`)**:
- `rfp/page.tsx` — Upload form (drag & drop) + ticket list with status badges + polling 5s
- `rfp/[ticket_id]/page.tsx` — Detail with metadata, readability, department aspects, PHI alerts, synthesis, reprocess button
- `api/rfp/upload/route.ts` — Next.js proxy POST
- `api/rfp/tickets/route.ts` — Next.js proxy GET
- `api/rfp/tickets/[ticket_id]/route.ts` — Next.js proxy GET
- `api/rfp/tickets/[ticket_id]/reprocess/route.ts` — Next.js proxy POST

**Scripts**:
- `scripts/rerun_rfp_ticket.py` — CLI reprocess (carga pipeline directamente sin Celery)
- `scripts/smoke_test_rfp.py` — Smoke test: genera RFP, inserta en DB, corre pipeline, verifica output

**Tests** (16/16 pasando):
- `tests/pipelines/test_rfp_classifier.py` — 8 tests: RFP detected, pitch/CV/newsletter rejected, empty/short docs, PHI detection (mocked)
- `tests/pipelines/test_rfp_workers.py` — 8 tests: Compliance BAA/DPA (US/UK), no country, Revenue USD/GBP, no numbers, DepartmentSection model

### Problemas Resueltos Durante la Implementación

1. Tests async/sync mismatch → removido `async`/`await`
2. Workers esperaban dict, no RfpMetadata → helper `_make_metadata()`
3. Mock de OpenAI patcheaba variable incorrecta → `openai.OpenAI` en lugar de `openai_client`
4. Dependencias faltantes en Docker → añadidas a requirements.txt (openai, markitdown, py-readability-metrics, pypdf2, nltk, langgraph, langchain-openai)
5. Starlette incompatible → instalado starlette 0.41.3
6. psycopg/psycopg-binary faltantes → instalados
7. database.py missing import → `from sqlalchemy import text` añadido
8. API routes sin prefijo `/api` → creados 4 proxy routes en Next.js

### Verificación

```bash
curl http://localhost:8000/rfp/tickets    # → {"tickets":[]}
uv run pytest tests/pipelines/test_rfp_classifier.py tests/pipelines/test_rfp_workers.py -v  # 16/16
```

- **Documentación**: `agentic-workflows.md` actualizado con estado de implementación, endpoints corregidos (`/rfp/*` en vez de `/api/rfp/*`), proxy routes documentados.
- **Branch pendiente**: sin branch específico creado aún.
- Verificado: 152 pruebas backend y 62 frontend (2 de 3 tests de control channel corregidos, 1 reintento pendiente); integración real de middleware → lote de cinco obligatorios + alta → stub 200. Canario de auditoría bloqueado.
- Pendientes: recorrido completo en Chromium, cuatro errores TypeScript preexistentes y persistencia/outbox futura. Reiniciar backend para activar el nuevo worker.

### Telemetría: control channel refactorizado (2026-10-06)
- `TelemetryService.control.ts` violaba la regla de "única función pública `track()`" con su propia cola, fetch y reintentos a `/api/telemetry/control`. Refactorizado a `recordTelemetryControl()` que valida propiedades y llama a `track()`. Todos los eventos frontend fluyen por el mismo pipeline `/api/telemetry/events`.
- Backend usa `@router.post` stacked en `telemetry.py` para registrar `/telemetry/events` y `/telemetry/control` en el mismo handler.
- 3 tests actualizados: expectativas cambiadas de `"/api/telemetry/control"` a `"/api/telemetry/events"`.
- Pendiente: 1 test de reintentos (retries three times) sigue fallando — espera 4 llamadas de fetch pero recibe 12.

### Telemetría: corrección de 422 en producción (2026-10-06)
- El backend de puerto 8000 (PID 42927) se levantó a las 18:01 con código desactualizado que no tenía las rutas correctas de telemetría. Se reinició con `uvicorn --reload` para usar el código completo con ambas rutas y validación correcta.
- El proxy de Next.js en backoffice (3001) se reinició para conectar con el backend fresco.
- Verificado: eventos reales del navegador responden 200 OK en lugar de 422. Tanto fetch como sendBeacon funcionan correctamente.

### Telemetría: conectividad de navegador (2026-10-06)
- Endpoints públicos loopback pasan por `/api/telemetry/events` del backoffice, evitando apuntar al localhost del operador en Codespaces. Colectores externos mantienen su URL.
- Proxy con `TELEMETRY_ENDPOINT` de servidor y soporte de backend interno mediante `SUPPLIERS_API_URL`; no reenvía cookies ni Authorization al colector.
- Verificado: 14 pruebas focalizadas y lint pasan; lote real de dos eventos responde 200 a través de Next.js. El recorrido completo de navegador de fases 3/4 sigue sin completarse.

### Telemetría: fase 1 (2026-10-06)
- Stub `POST /telemetry/events` en router propio: valida lotes y contratos del catálogo, registra solo cantidad/tipos y responde `{ "received": N }` sin persistencia.
- `TelemetryEvent` reutilizable en `services/api/telemetry.py`; configuración backend mediante `TELEMETRY_ENDPOINT`. Catálogo incluido en la imagen Docker.
- Servicio, variable pública e instrumentación frontend pendientes por indicación expresa del usuario. Verificación: 16 pruebas focalizadas pasan, sin Supabase.

### Telemetría: fase 2 (2026-10-06)
- Servicio único `uis/backoffice/lib/telemetry.ts`: cola en memoria, lotes cada 10 s/20 eventos, beacon al ocultar/cerrar y tres reintentos con backoff. Sin instrumentación de negocio.
- Sesión en memoria enlazada a auth; captura autenticada desactivada hasta recibir `telemetry_user_id` HMAC. El usuario rechazó ampliar el backend en esta fase. No enviar email, ID interno ni JWT al colector.
- Configuración de `NEXT_PUBLIC_TELEMETRY_ENDPOINT` pendiente en el archivo local bloqueado para edición. Avances documentados solo al final de `docs/telemetry/telemetry-plan.md`, después del plan original.
- Diez pruebas focalizadas pasan; tipos focalizados sin errores. Typecheck global: 11 errores ajenos; aviso lint de AuthContext confirmado en HEAD. Dependencias locales reportan 28 vulnerabilidades, sin cambios de lockfiles.

### Pipeline de Reporting Mensual — Supply Performance (2026-10-11/12)

**Problema**: No existía un dashboard operativo que mostrara los KPI mensuales de desempeño de insumos por clínica (coste, consumo, stockouts, riesgo de caducidad). La API principal no arranca (SQLModel + Python 3.14), y el pipeline de datos (Prefect 3.8.8) tenía múltiples bugs de versión y lógica.

**Arquitectura de solución — 3 servicios independientes**:

1. **`services/api/auth_standalone.py`** (CREADO):
   - Servidor FastAPI ligero en puerto 8000 que provee exclusivamente `POST /auth/login` y `GET /auth/me` para el backoffice.
   - Usa bcrypt + python-jose + TinyDB (`data/process/suppliers_db.json`).
   - **No depende de SQLModel ni de Supabase** — esto es crítico porque `models.py` (línea 315) crashea en Python 3.14 con `TypeError: issubclass() arg 1 must be a class`.
   - Credenciales: `admin@healthcore.com` / `adminpass456` (admin), `test@healthcore.com` / `securepassword123` (user).
   - Token JWT expira en 24h (TOKEN_EXPIRE_SECONDS=86400).
   - Bug corregido: `/auth/me` cambiado de query param `authorization: str | None = None` a `authorization: str | None = Header(None)` para leer HTTP header correctamente.

2. **`services/reporting/`** (CREADO — suite completa):
   - API FastAPI independiente en puerto 8001 con 3 endpoints JWT-protegidos:
     - `GET /reporting/monthly-clinic-supply-performance` — KPI mensuales (coste, consumo, stockouts, expiry)
     - `GET /reporting/pipeline-runs/latest` — última ejecución del pipeline
     - `POST /reporting/pipeline-runs` — disparo manual del pipeline
   - **`auth.py`**: Usa `OAuth2PasswordBearer` con la misma `JWT_SECRET_KEY` que `services/api/`. Valida tokens sin necesidad de acceder a la base de datos de usuarios.
   - **`queries.py`**: Función `get_monthly_clinic_supply_performance()` con consulta SQL sobre `reporting.monthly_clinic_supply_performance`. Bug DISTINCT ON corregido (ver más abajo).
   - **`routes/reporting.py`**: Endpoints con modelos Pydantic (`ClinicKpiOut`, `MonthlyKpisResponse`, `PipelineRunOut`, `PipelineRunTriggerOut`).
   - **Clinic name mapping**: Diccionario `CLINIC_NAMES` en rutas/reporting.py que asigna `clinic-001` → `"Central — Austin Main Clinic"` ... `clinic-010` → `"Atlanta Midtown"`. 5 clínicas US, 5 UK.

3. **Pipeline de datos (`data/pipelines/`)**:
   - Diseñado en `PIPELINE_DESIGN.md` (archivo ignorado por Copilot pero existente en disco).
   - Usa **Prefect 3.8.8** como orquestador.
   - Componentes: `extract.py` → `transform.py` → `load.py`, con `flows.py` (Prefect flows), `runner.py` (trigger), `run_store.py` (persistencia de ejecuciones), `database.py` (conexiones SQLAlchemy), `models.py` (modelos de datos).
   - **Bug corregido en `transform.py`**: `clinic_id = (tags.get("clinic_id") or ...).strip()` crasheaba cuando `clinic_id` era `int` en vez de `str`. Convertido a str antes de .strip().
   - **Bug corregido en `flows.py`**: `subflow_clinic_kpi_snapshot.submit(return_state=True)` no funciona en Prefect 3.x porque `@flow` objects no tienen método `.submit()` (solo `@task` lo tiene). Reemplazado con try/except y llamada directa.
   - **Bug corregido en `queries.py`**: `SELECT DISTINCT ON (month_start)` en PostgreSQL devuelve exactamente 1 fila por mes, no todas las clínicas. Cambiado a `WHERE month_start = (SELECT MAX(month_start) FROM ...)`, devolviendo las 10 clínicas del último periodo.
   - **PgBouncer workaround**: Supabase usa PgBouncer (pooler) que causa problemas de caché de prepared statements. Solución: usar raw SQL strings con valores inline en lugar de queries parametrizadas con named parameters.

**Demo Data**:
- Insertado directamente en `reporting.monthly_clinic_supply_performance` en Supabase PostgreSQL (10 filas para Octubre 2026).
- Cada clínica con KPIs realistas: coste ($430 – $3,200), consumo (12–95 unidades), stockouts (0–5), riesgo caducidad (0–4).
- Coste total mensual combinado: **$13,251.25**.
- Divisas: USD para clínicas US (001-004, 009-010), GBP para UK (005-008).

**Dashboard Backoffice — Fix JWT**:
- `uis/backoffice/app/reporting/page.tsx` usaba `fetch(url)` sin cabecera Authorization, causando error "Credenciales inválidas o ausentes" del reporting API (OAuth2PasswordBearer).
- **Solución**: Reemplazar `fetch(url)` por `request<T>(url)` de `lib/httpClient.ts` que auto-inyecta `Authorization: Bearer <token>` desde localStorage.
- Afectó dos endpoints: `fetchKpis()` y `fetchLatestRun()`.
- También se añadió `clinic_name?: string` a interfaces locales y tabla/cards actualizadas para mostrar `{clinic.clinic_name || clinic.clinic_id}`.

**Dependencias de servicio (puertos)**:
| Servicio | Puerto | Estado |
|----------|--------|--------|
| Auth Standalone (auth_standalone.py) | 8000 | ✅ JWT login + /me |
| Reporting API (services/reporting/) | 8001 | ✅ KPI + pipeline endpoints |
| Backoffice Next.js | 3001 | ✅ Dashboard reporting funcional |
| Supabase PostgreSQL (pooler) | 6543 | ✅ Reporting table con 10 filas |

**Errores conocidos**:
- Python 3.14 + SQLModel 0.0.48 → `TypeError: issubclass()` — la API principal NO arranca. Solución: auth_standalone.py y reporting API como servicios independientes.
- Prefect 3.8.8 no es compatible con `.submit()` en `@flow` objects. Solución alternativa con llamadas directas.
- Supabase PgBouncer causa errores de prepared statement cache en queries repetidas con named parameters. Workaround: raw SQL.

### 1) Landing y formulario bilingüe
- index.html e index.es.html: secciones corporativas de HealthCore con JSON-LD requerido.
- application.html y application.es.html: formulario de consulta de pacientes.
- validation.js: validaciones de negocio, estados de error/correcto, mensajes y comportamiento dinámico.

### 2) Utilidades TypeScript de negocio (src/)
- types/models.ts: entidades Claim, Appointment, Clinician, Location y reportes CME.
- utils/collections.ts: filtros, ordenamientos y agrupaciones.
- utils/search.ts: busqueda lineal y binaria.
- utils/transformations.ts: agregaciones y métricas (denial rate, no-show cost, CME report).
- utils/validations.ts: validaciones de claim y clinician + thresholds.

### 3) Talent Pipeline Tracker (Next.js)
- app/page.tsx: listado, filtros por query params, búsqueda y alta de candidatura.
- app/candidates/[id]/page.tsx + components/CandidateDetailClient.tsx: detalle, PATCH de estado/etapa, notas y reemplazo de registro.
- components/CandidateForm.tsx: formulario reutilizable de alta/edición.
- services/api.ts: cliente HTTP centralizado con autenticación JWT (Bearer token desde localStorage).
- types/candidate.ts y lib/validation.ts: contrato de datos y validación.
- **Autenticación**: lib/auth.ts (tipos y utilidades de token), lib/AuthContext.tsx (React Context), components/AuthGuard.tsx (guard de rutas con PUBLIC_ROUTES), components/TrackerHeader.tsx (controles de sesión), app/AuthShell.tsx (composición provider+guard+header), app/login/page.tsx, app/register/page.tsx, app/account/profile/page.tsx — login/registro llama directamente a NEXT_PUBLIC_API_URL (FastAPI).

### 4) API HealthCore — Directorio de Proveedores + Autenticación (services/api)
- models.py: modelos Pydantic (Supplier, SupplierCreate, SupplierReplace, SupplierRateUpdate, SupplierStatusUpdate, User, UserCreate, UserOut, UserUpdate, LoginRequest, Token, MeResponse, ChangePasswordRequest, ForgotPasswordRequest, ResetPasswordRequest, Profile, ProfileUpdate, Role enum) con enums de país, moneda, categoría, estado y acuerdo de cumplimiento, más validador de coherencia moneda-país.
- database.py: inicialización de TinyDB (data/process/suppliers_db.json, override con HEALTHCARE_DB_PATH) + get_sql_engine() para Supabase PostgreSQL + init_supabase_schema() + get_db() session generator.
- security.py: JWT stateless con bcrypt (hash/verify), OAuth2PasswordBearer, create_access_token, get_current_user, create_reset_token/validate/invalidate.
- user_service.py: CRUD de usuarios y perfiles en TinyDB (create_user, get_user_by_email, get_user_by_id, list_users, update_user, create_profile, get_profile_by_user_id, update_profile_by_user_id).
- audit_logger.py: registro de eventos de restablecimiento de contraseña en tabla `password_reset_audit` de TinyDB.
- email_service.py: envío de emails transaccionales mediante Resend (simulado en consola si no hay RESEND_API_KEY).
- rate_limiter.py: control de tasa para forgot-password (5 solicitudes por email cada 60 minutos).
- incidents_analysis.py: analizador de CSV de incidencias de pacientes (parseo, validación de clínicas/categorías/orígenes, reportes con conteo por tipo).
- routes/suppliers.py: listado con filtros, búsqueda por país y por categoría, alta, reemplazo, PATCH de tarifa (registra updated_at), PATCH de estado y DELETE como baja lógica (suspende y sella archived_at, no borra).
- routes/auth.py: POST /auth/login (JWT), GET /auth/me (perfil actual), POST /auth/forgot-password, POST /auth/reset-password, POST /auth/change-password.
- routes/profiles.py: GET/PUT /profiles/me (lectura y actualización del perfil del usuario autenticado).
- routes/users.py: CRUD de usuarios (POST /users con hash de password + creación de perfil, GET /users listado, GET/PUT/DELETE por ID).
- routes/inventory.py: API de inventario con SQLModel + Supabase.
- **cache.py**: MemoryCache singleton con dict + time.monotonic() expiry e invalidación por prefijo.
- **main.py**: timing middleware que loggea método/ruta/status/tiempo en ms.
- seed.py: carga idempotente de 15 proveedores (`seed_suppliers()`) + inventario (`seed_inventory()`: 6 supplies, 4 deliveries, 3 consumptions). Ejecutable como `uv run seed`.
- tests/ (9 archivos): conftest.py con seed_user + seed_admin fixtures, tests para login, forgot/reset/change password, /me, user_service, security.
- pyproject.toml: dependencias y scripts.

### 6) Módulo de Inventario — Backend (FastAPI + SQLModel + Supabase PostgreSQL)
- **Archivo router:** `services/api/routes/inventory.py`
- **Modelos SQLModel:** `MedicalSupply`, `SupplyDelivery`, `SupplyConsumption` (añadidos en `services/api/models.py`)
- **Schemas Pydantic separados:** `services/api/schemas.py` (MedicalSupplyCreate/Response, SupplyDeliveryCreate/Response, SupplyConsumptionCreate/Response)
- **Base de datos:** Supabase PostgreSQL (`postgresql://postgres.vtuwongbxvyirfaxpqtt:iinCXLIg3QT9pwQH@aws-1-eu-west-1.pooler.supabase.com:6543/postgres`)
- **Inicialización:** `init_supabase_schema()` en `database.py`, llamado desde `lifespan` de `main.py`
- **6 endpoints** bajo prefijo `/inventory`:
  - `GET /inventory/products` — lista con stock calculado
  - `POST /inventory/products` — crear suministro
  - `GET /inventory/products/{id}` — detalle con stock
  - `POST /inventory/orders/inbound` — registrar entrada (SupplyDelivery)
  - `POST /inventory/orders/outbound` — registrar salida (SupplyConsumption), rechaza stock negativo con 400
  - `GET /inventory/orders` — historial completo con datos del suministro
- **Reglas de negocio:** stock calculado (nunca almacenado), stock insuficiente → HTTP 400, consumption_type validado, clinic_id 1-12, user_uuid referencia TinyDB
- **Seed data:** `seed.py` → `seed_inventory()` con 6 supplies, 4 deliveries, 3 consumptions (ya ejecutado, 8 productos en Supabase)

### 7) Módulo de Inventario — Frontend (uis/backoffice)
- **Capa API dedicada:** `uis/backoffice/lib/inventoryApi.ts` — centraliza TODAS las llamadas, sin fetch directo en componentes
- **Proxy Next.js:** `next.config.ts` rewrite `/api/inventory/*` → `http://127.0.0.1:8000/inventory/*`
- **4 páginas protegidas:**
  - `/inventory/products` — tabla con código de color (rojo=crítico, ámbar=bajo, verde=saludable), botones "Entrada"/"Salida" por fila
  - `/inventory/orders/inbound` — formulario: selector producto, cantidad, proveedor, clínica; feedback éxito/error
  - `/inventory/orders/outbound` — formulario con **stock reactivo** (fetchProduct por API al seleccionar), advertencia cliente si cantidad > stock, botón deshabilitado
  - `/inventory/orders` — historial solo lectura con tarjetas: borde verde (entrada) / ámbar (salida), nombre, SKU, cantidad, detalle, clínica, fecha, user_uuid
- **Umbrales de stock:** ≤0 = critical (rojo), <20 = low (ámbar), ≥20 = healthy (verde)
- **Helpers:** `getStockLevel`, `getStockLevelColor`, `getStockLevelLabel` en inventoryApi.ts
- **Error handling:** Clase `ApiError`, `extractErrorMessage()` extrae detail de la API, errores 400 visibles en UI
- **Autenticación:** misma que el resto del backoffice (AuthGuard + `if (!user) return null`)
- **Header actualizado:** BackofficeHeader.tsx con links: 📦 Stock, 📥 Registrar Entrada, 📤 Registrar Salida, 📋 Historial

### 8) API de Incidencias (services/incidents-api)
- **API independiente** en FastAPI (puerto 8010), separada de la API principal de proveedores.
- `models.py`: IncidentCreate, IncidentResponse, IncidentUpdate con categorías, orígenes, estados, clinic_id.
- `database.py`: inicialización de TinyDB específica para incidencias.
- `routes/incidents.py`: CRUD de incidencias (GET listado con filtros, POST crear, GET/PUT por ID, DELETE) con caché en GET /summary (TTL 60s) y GET / list (TTL 30s) e invalidación en los 4 write endpoints.
- **cache.py**: MemoryCache singleton para incidencias.
- **main.py**: timing middleware que loggea método/ruta/status/tiempo en ms.
- `tests/`: conftest.py, helpers.py, test_incidents.py (tests funcionales).
- Proxy en next.config.ts: `/api/incidents/*` → `http://127.0.0.1:8010/api/incidents/*`

### 9) Frontend de Incidencias (uis/backoffice)
- `/incidents` — página principal con `IncidentsAnalyzerClient` (subida de CSV y análisis).
- `/incidents/list` — listado de incidencias registradas con filtros.
- `/incidents/register` — formulario de registro de nueva incidencia.
- `/incidents/summary` — resumen/estadísticas de incidencias.
- `/incidents-manager` — página de gestión de incidencias con `IncidentsManagerClient`.
- Componentes: `IncidentListPanel`, `IncidentRegisterForm`, `IncidentSummaryPanel`, `IncidentsAnalyzerClient`, `IncidentsManagerClient`.
- **Optimización de carga**: `AnalysisResultsPanel` (~200 líneas) y `IncidentFormPanel` (~150 líneas) extraídos a archivos independientes e importados con `next/dynamic` + `ssr: false` para lazy loading.
- **Página principal optimizada**: `app/page.tsx` con 7 wrappers `useMemo` para valores derivados (denialRate, payerRates, locationNoShowRates, weeklyNoShowCost, sortedClaims, binaryIndex, linearClaim, cmeReport) que evitan recálculos en cada render.

### 10) Website público (uis/website)
- Aplicación Next.js independiente para el sitio público de HealthCore.
- `app/`: páginas públicas (sin autenticación).
- `components/`: componentes de UI del sitio público.
- `data/`: datos estáticos del sitio.
- `types/`: tipos TypeScript del sitio.
- Configurado con ESLint, Next.js config, PostCSS, Tailwind CSS.

### 11) Scripts de análisis (scripts/)
- `analyze.py`: script de análisis de datos.
- `seed_incidents.py`: seed de incidencias desde CSV.

### 12) Dockerización completa del monorepo para desarrollo
- **Objetivo**: Cualquier miembro del equipo ejecuta `docker compose up` desde la raíz y toda la plataforma está operativa.
- **3 servicios** en `docker-compose.yml`:
  - `uis`: website (Next.js, puerto 3000) + backoffice (Next.js, puerto 3001)
  - `backend`: FastAPI principal (proveedores, auth, inventario, puerto 8000)
  - `incidents-backend`: FastAPI de incidencias (puerto 8010)
- **Red Docker explícita**: `healthcore-net` (bridge)
- **Comunicación entre servicios**: por nombre Docker (`http://backend:8000`, `http://incidents-backend:8010`), no por localhost.
- **Bind mounts** para hot-reload en desarrollo (cambios locales → reflejados en el contenedor).
- **Volúmenes anónimos** para `node_modules` (evita sobreescribir con bind mount del host).

### 13) Archivos Docker creados
- **`docker-compose.yml`** (raíz): orquestación de 3 servicios con `env_file: .env`, red `healthcore-net`, bind mounts, healthcheck opcional.
- **`uis/Dockerfile`**: imagen multi-etapa Node 22 Alpine (base → deps-website → deps-backoffice → runner). EXPOSE 3000 3001. CMD `start.sh`.
- **`uis/start.sh`**: arranca ambos Next.js en paralelo (website:3000, backoffice:3001) con trap SIGTERM/SIGINT + wait.
- **`uis/.dockerignore`**: excluye node_modules, .next, .env, .log del contexto Docker de uis.
- **`services/Dockerfile`**: imagen Python 3.12-slim + uv. Copia ambos requirements.txt, los instala, copia `services/api` y `services/incidents-api`. EXPOSE 8000 8010.
- **`services/entrypoint.sh`**: entrypoint dinámico según `SERVICE_NAME` (api→8000, incidents-api→8010) con `uvicorn --reload`.
- **`services/.dockerignore`**: excluye `__pycache__`, `*.pyc`, `.env*`, `tests/`, `*.log`.
- **`.dockerignore`** (raíz): reduce drásticamente el contexto de build (node_modules, .env, `__pycache__`, `.next`, `.venv`, etc.).

### 14) Actualización de URLs para entorno Docker
- **Route handlers** (`authProxy.ts`, `suppliersProxy.ts`, `suppliersServer.ts`, `inventoryApi.ts`): fallbacks cambiados de `127.0.0.1:8000` a `http://backend:8000`.
- **`next.config.ts`**: rewrites actualizados de `127.0.0.1` a nombres Docker (`backend:8000`, `incidents-backend:8010`).
- **`IncidentsManagerClient.tsx`**: API_BASE cambiado de URL fija a relativa (`""`) para que pase por proxy Next.js.
- **`.env`**: `SUPPLIERS_API_URL=http://backend:8000`, `INCIDENTS_API_URL=http://backend:8000`, `NEXT_PUBLIC_INVENTORY_API_URL=http://backend:8000`, `NEXT_PUBLIC_INCIDENTS_API_URL=/api/incidents`.

### 15) Correcciones aplicadas
- **Bug corregido**: `INCIDENTS_API_URL` estaba en `backend:8010` pero el servicio backend solo escucha en `:8000`. Se usaba como fallback para auth/suppliers. Corregido a `backend:8000`.
- **`.dockerignore` raíz**: no existía inicialmente. El contexto de build pasó de ~987 kB a ~10.5 kB.
- **`requirements.txt`**: se añadieron `sqlmodel>=0.0.42` y `psycopg2-binary>=2.9.13` que estaban en `pyproject.toml` pero no en `requirements.txt`.
- **`.gitignore`**: se añadieron `.env.local` y `.env.*.local`.

### 16) Pruebas y verificación
- **`docker compose build`**: las 3 imágenes se construyen exitosamente.
- **Tests unitarios del backoffice**: 11/11 tests pasan con `npx jest` (0.723s).
- **TypeScript**: todos los errores resueltos (45 problemas iniciales en `suppliersProxy.test.ts`).
- **Configuración de tests**: `tsconfig.test.json` creado con `types: ["jest", "node"]`, `jest.config.ts` actualizado para usarlo, `__tests__` excluido del `tsconfig.json` principal.

### 17) Correcciones runtime (docker compose up)
- **Problema**: Backoffice (3001) daba error 500 — `Module not found: Can't resolve '../../../src/utils/transformations'`
  - **Causa raíz**: Turbopack no resuelve imports fuera del directorio del proyecto, aunque `experimental: { externalDir: true }` esté configurado. El volumen de `src/` estaba montado en `/workspace/src/` pero el import `../../../src/` queda fuera del árbol de resolución de Turbopack.
  - **Solución**: se montó `./src:/workspace/uis/backoffice/src` en docker-compose.yml para que la carpeta `src/` esté dentro del proyecto backoffice. Los imports en `app/page.tsx` se cambiaron de `../../../src/...` a `../src/...`.
  - **Alternativa descartada**: `NEXT_DISABLE_TURBOPACK=1` + `@healthcore/*` path alias (Webpack tampoco resolvía bien externalDir).
- **Problema**: Backend (8000) crasheaba al arrancar — `RuntimeError: DATABASE_URL no está configurada`
  - **Causa raíz**: `init_supabase_schema()` llamaba a `get_sql_engine()` que lanza error si `DATABASE_URL` está vacía.
  - **Solución**: `init_supabase_schema()` ahora verifica si `DATABASE_URL` existe antes de llamar a `get_sql_engine()`. Añadido `logger.info` skip graceful. Añadido `import logging` en database.py.
- **Problema**: Website (3000) mostraba imágenes rotas — error `getaddrinfo EAI_AGAIN images.unsplash.com`
  - **Causa raíz**: El contenedor Docker no resuelve DNS externo. Next.js intenta optimizar imágenes (descargarlas, redimensionarlas, convertirlas) en el servidor y eso falla.
  - **Solución**: `images: { unoptimized: true }` en `uis/website/next.config.ts`. Ahora el navegador carga las imágenes directamente de Unsplash.

### 18) Estado actual de los servicios (docker compose up)
| Puerto | Servicio | Estado | URL |
|--------|----------|--------|-----|
| 3000 | Website (Next.js) | ✅ 200 | http://localhost:3000 |
| 3001 | Backoffice (Next.js) | ✅ 200 | http://localhost:3001 |
| 8000 | HealthCore API (FastAPI) | ✅ Arrancó sin Supabase | http://localhost:8000/docs |
| 8010 | Incidents API (FastAPI) | ✅ 200 | http://localhost:8010 |

### 19) Backend — Auditoría de serialización (completada)

Se realizó una auditoría exhaustiva de serialización sobre **38 endpoints** (31 de HealthCore API + 7 de Incidents API), documentada en `docs/serialization-audit.md`.

**Hallazgos iniciales:**
- 21 endpoints ✅ serializados correctamente (55%)
- 14 endpoints ⚠️ parcialmente serializados (37%) — esquemas compartidos entre listado/detalle, exposición de FK interna (`user_id`), schemas sin nombre (`dict[str,str]`)
- 3 endpoints ❌ sin serializar (8%) — raíces y análisis de incidencias sin `response_model`

**Cambios implementados (8 schemas Pydantic nuevos, 14 endpoints actualizados):**
| Schema | Propósito |
|---|---|
| `ProfilePublic` | Perfil sin `user_id` (FK interna) |
| `MessageResponse` | Mensaje genérico para auth flows |
| `SupplierListItem` | Listado ligero de proveedores (sin `notes`) |
| `AnalysisResponse` / `AnalysisSummary` / `AnalysisPercentages` | Respuesta tipada de análisis de incidentes |
| `RootResponse` (x2) | Root de descubrimiento (Main API + Incidents API) |

**Endpoints actualizados:**
- Auth: forgot/reset/change → `MessageResponse` (antes `dict[str,str]`)
- Auth/me: ahora convierte Profile → ProfilePublic (oculta `user_id`)
- Profiles: GET/PUT → `ProfilePublic`
- Suppliers: 3 listados → `SupplierListItem` (sin campo `notes`)
- Main API: Root → `RootResponse`, analyze/sample → `AnalysisResponse`
- Incidents API: Root → `RootResponse`, summary → `IncidentSummary`

**Verificación:** 180 tests pasan (113 API + 67 Incidents API). Todos los endpoints verificados via OpenAPI `/docs`.

---

### 20) Frontend — Auditoría Lighthouse y refactorización (completada)

Se ejecutó auditoría con Lighthouse sobre website (EN/ES) y backoffice, documentada en `audit/AUDIT.md`. Se aplicaron correcciones documentadas en `audit/REPORT.md`.

---

### 21) Optimización integral de caché — Frontend + Backend (completada)

Se realizó una optimización integral de caching en toda la aplicación (frontend y backend) para reducir latencia, carga en base de datos y tamaño de bundle. Todo documentado en `CACHING_REPORT.md`.

**Frontend — useMemo (página principal del backoffice):**
- 7 wrappers con `useMemo` en `uis/backoffice/app/page.tsx` para valores derivados que se computan desde listas estáticas: `denialRate`, `payerRates`, `locationNoShowRates`, `weeklyNoShowCost`, `sortedClaims`, `binaryIndex`, `linearClaim`, `cmeReport`.
- Cada `useMemo` depende de las variables que realmente necesita (ej: `[sortedClaims]` para los que usan la lista ordenada), evitando recálculos innecesarios en cada render.
- Impacto: se eliminan ~7 operaciones O(n) + 1 O(n log n) por render.

**Frontend — Lazy Loading (2 componentes):**
- `IncidentsAnalyzerClient.tsx`: `AnalysisResultsPanel` (~200 líneas, 15-20 KB) ahora se importa con `next/dynamic` y `{ ssr: false }`. Solo se descarga cuando el usuario sube un CSV y recibe resultados.
- `IncidentsManagerClient.tsx`: `IncidentFormPanel` (~150 líneas, 10-15 KB) ahora se importa con `next/dynamic` y `{ ssr: false }`. Solo se descarga cuando el usuario hace clic en "New" o "Edit".
- Ambos componentes extraídos a archivos independientes para poder ser importados dinámicamente.

**Backend — MemoryCache (ambas APIs):**
- Creado `services/api/cache.py` y `services/incidents-api/cache.py`: clase `MemoryCache` singleton con almacenamiento en `dict`, expiración por `time.monotonic()`, invalidación por prefijo de clave, y métodos `get(key)`, `set(key, value, ttl)`, `invalidate(prefix)`.
- Patrón cache-aside: check cache → hit → devolver; miss → computar → guardar en caché → devolver.
- Es una solución ligera sin dependencias externas (no Redis), con la limitación de que no persiste entre reinicios ni comparte estado entre workers.

**Backend — Caché en HealthCore API (inventario):**
- `GET /products` cacheado con clave `"inventory:products:list"`, TTL 30 segundos.
- Invalidación del prefijo `"inventory:products"` en:
  - `POST /products` (crear producto)
  - `POST /orders/inbound` (entrada de stock)
  - `POST /orders/outbound` (salida de stock)
- Justificación TTL 30s: el inventario cambia con frecuencia (consumos clínicos continuos), pero 30 segundos de desfase es aceptable para la visibilidad operativa.

**Backend — Caché en Incidents API:**
- `GET /summary` cacheado con clave `"incidents:summary"`, TTL 60 segundos. El resumen (conteos por estado/categoría/origen) no necesita ser instantáneo para un dashboard.
- `GET / list` cacheado con clave dinámica `"incidents:list:s:{status}|c:{category}|o:{origin}|b:{branch}"`, TTL 30 segundos. Cada combinación de filtros tiene su propia entrada en caché.
- Invalidación de ambos prefijos (`"incidents:summary"` e `"incidents:list"`) en:
  - `POST /` (crear incidencia)
  - `PUT /{id}` (actualizar)
  - `PATCH /{id}/status` (cambiar estado)
  - `DELETE /{id}` (eliminar)

**Bugs encontrados y corregidos durante la auditoría:**
1. **Dead code en `get_summary()`**: la línea `cache.set(...)` estaba después de `return`, por lo que NUNCA se guardaba el resumen en caché. Ningún test lo detectó porque TinyDB es rápido en datasets pequeños.
2. **Invalidación incompleta**: `_invalidate_summary_cache()` solo limpiaba `"incidents:summary"`, pero no `"incidents:list"`. Al crear una incidencia, el listado con filtros seguía mostrando datos obsoletos.

**Timing middleware (ambas APIs):**
- Agregado middleware en `main.py` de ambas APIs que registra: método, ruta, código de estado y tiempo de respuesta en milisegundos.
- Las métricas se loggean a consola con el formato: `"METHOD /path → 200 (12.34ms)"`.
- Sirve para identificar los próximos cuellos de botella con datos reales.

**Pruebas y verificación:**
- 82/82 tests pasan (sin regresiones).
- 0 errores de TypeScript en backoffice.
- 0 errores de Python en ambas APIs.
- Documentación completa en `CACHING_REPORT.md` (679 líneas) con:
  - Analogías para lectores no técnicos (post-it, restaurante, maleta, supermercado)
  - Tablas "Antes/Después" por cada decisión
  - Desglose de costes por operación ("Traducción a números")
  - Análisis de riesgos por trade-off ("¿Qué tan grave es?")
  - Justificación detallada de cada TTL
  - Sección "¿Qué no se cacheó y por qué?" con análisis por endpoint
  - Glosario de 12 términos técnicos en lenguaje sencillo

**Resultados Lighthouse iniciales:**
| Aplicación | Performance | Accessibility | Best Practices | SEO |
|---|---|---|---|---|
| Website (EN) | 97 | 100 | 77 | 63 |
| Website (ES) | 83 | 100 | 77 | 63 |
| Backoffice | 80 | 96 | 100 | 60 |

**Mejoras implementadas:**

1. **Extracción de tipos y constantes de incidencias (Caso 1):**
   - Creados `types/incident.ts` y `lib/incidents.ts`
   - Eliminadas ~255 líneas de tipos/constantes duplicadas en 4 componentes
   - Unificados `BRANCHES` / `VALID_BRANCHES` como `BRANCH_OPTIONS`
   - Lighthouse: Website EN 97→98, Website ES 83→98

2. **Consolidación de cliente HTTP (Caso 2):**
   - Creado `lib/httpClient.ts` como único cliente HTTP del backoffice
   - Eliminados `authHttpClient.ts` (~75 líneas) y ~120 líneas de HTTP duplicado en `suppliersApi` e `inventoryApi`
   - Unificado manejo de 401, extracción de errores, `ApiError`
   - Lighthouse: sin cambios (refactorización interna)

3. **Reducción de JavaScript no utilizado (Mejora 1):**
   - Habilitado `experimental.optimizePackageImports` en website y backoffice
   - Instalado `@next/bundle-analyzer` + script `analyze` en ambos proyectos
   - Lighthouse: sin cambios inmediatos (medidas preventivas)

4. **Optimización de LCP (Mejora 2):**
   - Añadida prop `priority` a imagen hero en `LandingPage.tsx`
   - Beneficia las 3 rutas del website (`, `/en`, `/es`)
   - Lighthouse: Website EN 98→99

5. **Abstracción de estados de UI (Mejora 6):**
   - Creados `LoadingSpinner`, `ErrorMessage`, `EmptyState` en `components/ui/`
   - Refactorizados 5 componentes del backoffice (~90 líneas eliminadas)
   - Lighthouse: Website ES 98→99, Backoffice 80→81

**Mejoras descartadas:**
- Mejora 5 (SEO/noindex): Correcto y deseable en proyecto personal
- Mejora 7 (minificación): Next.js ya minifica en producción

**Validación:** TypeScript sin errores nuevos en todos los cambios. Build de website exitoso. Backoffice tiene error preexistente de module resolution con `../src/` (no relacionado con los cambios).
- Typecheck de uis/website sin errores (no afectado por auth).
- API de proveedores auditada extremo a extremo: validaciones 422, 404, filtros, seeder idempotente y persistencia tras reiniciar uvicorn.
- Autenticación implementada en backoffice y tracker: login, registro, perfil, guard de rutas, headers Authorization, manejo 401, flujo completo de restablecimiento de contraseña.
- API de inventario funcional en Supabase PostgreSQL: 8 productos con stock calculado.
- Frontend de inventario con 4 páginas operativas, auditado contra 10 criterios — todos cumplidos.
- API de incidencias operativa en puerto 8010 con tests funcionales.
- Frontend de incidencias con 5 páginas/componentes.
- Website público (uis/website) como aplicación Next.js independiente.
- Scripts de análisis de datos (scripts/analyze.py, seed_incidents.py).
- Estructuras preparadas para escalado: agents/, skills/, workflows/, docs/, infra/.

---

## RFP Intake Pipeline — Hito 9 · Parte 1 (2026-11-24)

### Estado actual
- **Completado**: Pipeline de 6 nodos (LangGraph secuencial) en `data/pipelines/rfp_intake/`: converter, readability, classifier, orchestrator, workers, synthesizer.
- **Completado**: 3 tablas SQLModel en PostgreSQL (rfp_tickets, rfp_metadata, rfp_department_sections). 4 endpoints FastAPI en `/rfp/*`.
- **Completado**: Frontend de backoffice con upload + listado con polling 5s + detalle con metadata, readability, department aspects, PHI alerts, reprocess.
- **Completado**: 4 proxy routes Next.js (`/api/rfp/*` → `/rfp/*`).
- **Completado**: Integración con LLM real de 4Geeks AI — `classifier.py` y `workers.py` usan `API_KEY`/`HOST` del `.env` en lugar de `OPENAI_API_KEY`.
- **Completado**: Clasificación rule-based con early return para YES-RFP (evita LLM cuando las reglas ya determinaron el resultado).
- **Bugs corregidos**:
  1. `converter.py`: `PermissionError` en `shutil.copy2()` entre devices Docker/host → fallback a `shutil.copy()`
  2. `classifier.py`: `OpenAI()` sin API_KEY crasheaba antes de ejecutar reglas → early return para YES-RFP + configuración de `LLM_API_KEY`/`LLM_HOST`
  3. `classifier.py` (rules): EHR pitch no detectado como NO-RFP → patrón añadido
  4. `classifier.py` (rules): Referral partnership informal no detectado como YES-RFP → patrón añadido
- **Verificación end-to-end**: 3 PDFs reales descargados de 4GeeksAcademy/ai-engineering-syllabus y procesados:
  - RFP-1 (Meridian Manufacturing) → `analisis_completo` ✅
  - RFP-2 (Thames Valley University) → `analisis_completo` ✅
  - RFP-3 (MedFlow EHR pitch) → `descartado` ✅
- **Tests: 16/16 pasando**.
- **Variables de entorno**: `RFP_CLASSIFIER_MODEL`, `RFP_WORKER_MODEL` añadidas al `.env` apuntando a `deepseek-v4-flash`.

### Archivos clave
- `data/pipelines/rfp_intake/` (9 archivos: models, converter, readability, classifier, orchestrator, workers, synthesizer, graph, tasks)
- `services/api/models_rfp.py` — 3 SQLModel tables
- `services/api/routes/rfp.py` — 4 endpoints
- `uis/backoffice/app/rfp/` — 2 páginas + 4 proxy routes
- `scripts/rerun_rfp_ticket.py`, `scripts/smoke_test_rfp.py`
- `tests/pipelines/test_rfp_classifier.py`, `tests/pipelines/test_rfp_workers.py`

### Limitaciones
- MarkItDown sin dependencia `[pdf]` (usa PyPDF2 fallback)
- Celery no corre localmente (verificación via `asyncio.run()`)
- NLTK data warning no bloqueante

---

## MCP Server — HealthCore MCP OAuth Tools (2026-12)

### Estado actual
- **Completado**: Servidor MCP con FastMCP v1 + mcpauth Resource Server + SelectiveBearerASGI.
- **Completado**: 10 tools MCP (5 incidents + 5 inventory) con scopes, descripciones claras y schemas.
- **Completado**: Rechazo explícito de escritura en inventario (RETO 2) — `create_product`, `update_product`, `delete_product` devuelven `INVENTORY_READ_ONLY`.
- **Completado**: OAuth 2.1 funcional — JWKS (producción), token fijo (dev), skip (testing).
- **Completado**: Bug corregido en `config.py` — lógica de scopes invertida (`if os.getenv("MCP_SKIP_AUTH")` → `if not`).
- **Completado**: `update_incident_status` usa endpoint de ciclo de vida específico (`PATCH /api/incidents/{id}/status`).
- **Completado**: Logging por invocación con `_serialize_result()` — formato `"Tool OK | tool=... | client=<sub> | success=true"`.
- **Completado**: Códigos de error diferenciados (HTTP 401 missing_auth_header, invalid_token; HTTP 403 missing_required_scopes; HTTP 500 server_error).
- **Completado**: Integración LangGraph con langchain-mcp-adapters — `services/agent/tools/mcp_client.py` con cache, 6 funciones async.
- **Completado**: Migración del grafo a async — nodos `async def`, `load_mcp_tools(session=session)`, `await tool.ainvoke()`.
- **Completado**: DNS Rebinding fix — `TransportSecuritySettings(enable_dns_rebinding_protection=False)`.
- **Completado**: MCP Playground conectado exitosamente — `tools/list` (10 tools), `tools/call` (rechazo escritura), CORS preflight (204).
- **Completado**: Documentación en `mcp-server.md` — arquitectura, setup, tools, OAuth flow, migración, testing.
- **Completado**: Archivos legacy deprecados con `DeprecationWarning` (`services/agent/tools/incidents.py`, `inventory.py`).
- **Completado (v1.0.0)**: ContextVar-based auth_info propagation — `get_auth_info()` / `set_auth_info()` para acceso al cliente (`sub`) desde funciones de tool de FastMCP.
- **Completado (v1.0.0)**: Logging con identificación de cliente — format `"Tool OK/ERROR/DENIED | tool=... | client=<sub> | ..."` para auditoría completa.
- **Completado (v1.0.0)**: Scopes por defecto — `MCP_DEV_TOKEN_SCOPES` asigna TODOS los scopes en desarrollo si no está configurado (permisivo para dev sin IDP real).
- **Completado (v1.0.0)**: Validación de inicio — warnings claros si `MCP_SKIP_AUTH=1` está activado en producción con clientes remotos.
- **Completado (v1.0.0)**: Verificación completa — 9/9 tests de autenticación pasan; auth_info accesible en contexto de tool; logging incluye cliente.

### Bugs encontrados y corregidos
1. **Scopes invertidos** (`mcps/config.py`): `if os.getenv("MCP_SKIP_AUTH")` en lugar de `if not os.getenv("MCP_SKIP_AUTH")`. En producción (sin `MCP_SKIP_AUTH`), `required_scopes` quedaba vacío y el middleware aceptaba cualquier request sin token. Corregido.
2. **DNS Rebinding** (`mcps/server.py`): FastMCP activa protección anti-DNS rebinding para localhost por defecto, lo que rechaza Host headers de túneles (localtunnel). Corregido con `TransportSecuritySettings(enable_dns_rebinding_protection=False)`.
3. **CORS preflight** (`mcps/server.py`): FastMCP no respondía OPTIONS correctamente. Corregido asegurando que SelectiveBearerASGI pase requests OPTIONS sin intervención del middleware Bearer.
4. **Auth_info context propagation** (`mcps/auth.py`, `mcps/server.py`): FastMCP ejecuta funciones de tool en contexto async profundo donde el request no es accesible. Corregido con Python `contextvars.ContextVar` que captura auth_info en SelectiveBearerASGI y lo propaga a través del async stack. Funciones `_current_client()` y `_require_tool_scope()` usan `get_auth_info()`.
5. **Logging sin cliente** (`mcps/server.py`): El formato de log original `"Tool OK | tool=... | success=..."` no incluía el cliente (sub del token). Actualizado a `"Tool OK/ERROR/DENIED | tool=... | client=<sub> | ..."` para auditoría completa.

### Acceptance criteria (9/9)
- AC-1: Paquete `mcps/` existe con `__init__.py`, `server.py`, `auth.py`, `config.py`, `tools.py` ✅
- AC-2: OAuth enforcement funcional (SelectiveBearerASGI + mcpauth bearer middleware) ✅
- AC-3: `update_incident_status` usa endpoint de ciclo de vida (`PATCH /api/incidents/{id}/status`) ✅
- AC-4: Inventory read-only con rechazo explícito (3 tools con error `INVENTORY_READ_ONLY`) ✅
- AC-5: Clear descriptions y argument schemas en todas las tools ✅
- AC-6: Códigos de error diferenciados por escenario (401/403/500 + error code string) ✅
- AC-7: Log per invocation con tool name y success/failure ✅
- AC-8: Agent usa MCP Client con langchain-mcp-adapters (no HTTP directo) ✅
- AC-9: MCP Playground / Public URL funcional (10 tools listables, write rejection, CORS) ✅

### Archivos clave
- `mcps/server.py` — FastMCP app composition
- `mcps/auth.py` — OAuth middleware, SelectiveBearerASGI
- `mcps/config.py` — Config, McpMode, compute_required_scopes
- `mcps/tools.py` — 10 tools MCP
- `services/agent/tools/mcp_client.py` — MCP Client con langchain-mcp-adapters
- `docs/mcp-server.md` — Documentación completa
- `services/agent/graph.py` — Grafo migrado a async con MCP Client

### Documentación
- `mcp-server.md` — Documentación técnica completa del MCP Server
- `mcps/README.md` / `mcps/README.es.md` — Documentación del paquete

### URL pública (Playground)
- `https://congenial-pancake-gxq77rxwr56q3v7x4-9100.app.github.dev/mcp`
- Verificada con `tools/list` → 10 tools, `tools/call` → write rejection, CORS preflight → 204
