# Agent Memory — Harness & Guard

> Documento de diseño, decisiones arquitectónicas y validación del sistema de
> memoria operativa para el agente LangGraph de HealthCore.
>

## Hito 8 · Parte 1 — Memoria y Auto-mejora de Agentes

---

### Índice

1. [Contexto y objetivos](#1-contexto-y-objetivos)
2. [Las 7 preguntas de diseño](#2-las-7-preguntas-de-diseño)
3. [Arquitectura del sistema](#3-arquitectura-del-sistema)
4. [Validación PHI (HIPAA + UK GDPR)](#4-validación-phi-hipaa--uk-gdpr)
5. [Flujo de decisión completo](#5-flujo-de-decisión-completo)
6. [Ciclo 1 — Aprobado](#6-ciclo-1--aprobado)
7. [Ciclo 2 — Rechazado](#7-ciclo-2--rechazado)
8. [Ciclo 3 — Edición por el usuario](#8-ciclo-3--edición-por-el-usuario)
9. [Ciclo 4 — PHI bloqueado](#9-ciclo-4--phi-bloqueado)
10. [Consolidación y olvido](#10-consolidación-y-olvido)
11. [Integración con el grafo LangGraph](#11-integración-con-el-grafo-langgraph)
12. [Referencias](#12-referencias)

---

## 1. Contexto y objetivos

El agente LangGraph existente responde preguntas sobre políticas de HealthCore
(medicare, no-show, cancelación, facturación) combinando RAG + datos
operativos (incidentes + inventario). Sin embargo, **cada conversación empieza
desde cero**: no recuerda preferencias del usuario ni patrones de interacciones
previas.

**Objetivo del Hito 8**:

> Que el agente aprenda de la interacción, sin que eso signifique que empiece a
> inventar cosas o a acumular basura en su memoria para siempre.

**Principios** (extraídos de `services/agent/memory/__init__.py`):

1. **PHI-free**: ningún identificador de paciente ni PHI bajo HIPAA/UK GDPR
   puede aparecer en memoria.
2. **Audit trail**: cada decisión (propuesta, resultado, mensaje que la originó,
   marca de tiempo) queda registrada de forma auditable.
3. **No silent writes**: el agente nunca escribe memoria sin confirmación
   explícita del usuario.
4. **No asunción por defecto**: silencio o ambigüedad → descarte.
5. **Single pending proposal**: solo puede haber una propuesta pendiente a la vez.
6. **Dual PHI validation**: PHI se valida antes de mostrar la propuesta al
   usuario y antes de consolidar/reescribir.

---

## 2. Las 7 preguntas de diseño

### 2.1 ¿Qué tipo de memoria? ¿Por qué TinyDB en lugar de Redis, Vector DB o Knowledge Graph?

| Alternativa             | Ventaja                     | Por qué NO                                                                 |
|-------------------------|------------------------------|----------------------------------------------------------------------------|
| **Redis**               | Rápido, TTL nativo           | Dependencia externa adicional. No crítico para el volumen (cientos de facts). |
| **Vector DB (Qdrant)**  | Búsqueda semántica           | ✦️ **Razon crítico**: las colecciones RAG existentes son *read-only*. Mezclar memoria operativa con RAG rompería los evals de retrieval y permitiría ataques de poison (insertar facts maliciosos recuperables por RAG). |
| **Knowledge Graph**     | Relaciones complejas         | Sobredimensionado para hechos operativos simples. El agente no necesita inferencia relacional sobre su memoria. |
| **SQLite**              | Robusto, concurrente         | Más peso del necesario. Schema fijo. TinyDB es schema-less y zero-config.   |
| **TinyDB** ✅            | Zero-config, JSON, ya incluido como dependencia en pyproject.toml | Volumen adecuado (cientos, no millones). Aísla tablas (memory_facts, pending_proposal, audit_log). No añade infraestructura. |

**Decisión**: TinyDB con tres tablas aisladas en un único fichero JSON
(`data/process/agent_memory/db.json`). Cuando el volumen de facts supere el
millar, se migrará a SQLite, pero la interfaz (`store.py`) está abstraída para
que el backend sea intercambiable.

### 2.2 ¿Qué entra y qué NO entra en la memoria?

**SÍ puede entrar** (categorías):

| Categoría            | Descripción                                                  | Ejemplo                                                   |
|----------------------|--------------------------------------------------------------|-----------------------------------------------------------|
| `operational`        | Cambios en procesos, políticas, flujos de trabajo.           | "El nuevo proceso de derivación en Manchester requiere formulario M-CDX2" |
| `incident_pattern`   | Patrones de incidencia recurrente (sin datos de paciente).   | "Los incidentes de facturación suelen escalar al nivel 2 los lunes" |
| `staff_preference`   | Preferencias sobre formato, presentación, canales.           | "El usuario quiere ver primero los incidentes abiertos" |

**NUNCA puede entrar** (PHI bajo HIPAA + UK GDPR):

| Tipo                    | Ejemplo                           | Patrón detectado por `validate_phi_safe()` |
|-------------------------|-----------------------------------|--------------------------------------------|
| Nombre y apellidos      | "María García"                    | `\b[A-Z][a-záéíóúñ]+ [A-Z][a-záéíóúñ]+\b` con contexto |
| Número de teléfono      | "+34 612 345 678"                 | `\+?\d{1,3}[\s.-]?\d{6,10}`              |
| SSN / Número Seguridad Social | "123-45-6789"               | `\b\d{3}-\d{2}-\d{4}\b`                   |
| NHS Number (UK)         | "123 456 7890"                    | `\b\d{3}\s?\d{3}\s?\d{4}\b`              |
| Email                   | "paciente@email.com"             | `\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b` |
| Dirección postal        | "Calle Mayor 12, Madrid"         | Patrón de dirección                       |
| Número de historia clínica (MRN) | "MRN-987654"          | `\b(MRN|HCC?|EXP)\d{4,}\b`              |
| Fecha de nacimiento     | "15/03/1985"                     | Fechas en contexto de paciente            |
| Diagnóstico / condición clínica | "diabetes tipo 2"       | Términos clínicos en contexto narrativo  |
| Combinación re-identificable | Cualquier combinación de anteriores | Validación cruzada                      |

### 2.3 ¿Cómo se "olvida"? (TTL y consolidación)

- **TTL por defecto**: 90 días para facts individuales.
- **Consolidación automática**: cuando hay ≥50 facts, se agrupan por categoría
  y se construyen resúmenes. Los facts originales se marcan con TTL de 90 días;
  los consolidados **no expiran**.
- **`run_maintenance()`**: invocable externamente o desde el pipeline tras la
  confirmación. Ejecuta purge + consolidate.
- **¿Por qué 90 días?** Suficiente para mantener contexto operativo relevante
  sin acumular basura. Si un fact se cita regularmente durante 90 días, se
  consolida en un resumen permanente.

### 2.4 ¿Cómo se previene el "poison" de memoria?

1. **PHI validation antes de mostrar propuesta**: `set_pending_proposal()` en
   `store.py` ejecuta `validate_phi_safe()`. Si detecta PHI → descarta la
   propuesta y registra evento `phi_rejected` en audit_log.
2. **PHI validation antes de escribir**: `add_fact()` y `consolidate_memory()`
   también ejecutan `validate_phi_safe()`. Doble filtro.
3. **Confirmación explícita del usuario**: sin un "sí" del usuario (con
   confianza ≥0.70), la propuesta se descarta.
4. **Single pending proposal**: si el usuario ignora la propuesta y cambia de
   tema, la propuesta se descarta.
5. **Clasificador conservador**: el clasificador de intención (`classifier.py`)
   requiere umbrales de confianza altos (aprobación ≥0.70, rechazo ≥0.75,
   edición ≥0.85). Por defecto → `discard`.
6. **Sin escritura en RAG**: la memoria usa TinyDB, **no** las colecciones
   Qdrant de la empresa. Esto aísla completamente el riesgo de poison en
   retrieval.
7. **Audit trail inmutable**: toda decisión, PHI bloqueado, aprobación y
   rechazo queda registrado en `audit_log`. Es posible detectar inyectores
   a posteriori.

### 2.5 ¿Por qué NO un segundo agente o grafo separado? (No multi-agente)

La auto-evaluación es una **extensión del mismo modelo de lenguaje**, no un
segundo agente. El prompt del LLM incluye un campo estructurado opcional
`propuesta_memoria` en la respuesta. Esto significa:

- Una sola llamada al LLM → answer + optional memory proposal.
- Sin latencia adicional de round-trip.
- Sin estado compartido entre agentes.
- Sin complejidad de orquestación.

Si se usara un segundo agente LangGraph para evaluar la memoria, se necesitaría:

1. Un segundo StateGraph → duplicar el esfuerzo de compilación, test y
   despliegue.
2. Un mecanismo de comunicación entre grafos (subgrafos o Send/Receive) →
   sincronización compleja.
3. Evaluar si el mensaje es "memorizable" requiere el mismo LLM → no hay
   ahorro de calidad.

**Decisión**: la propuesta de memoria se genera en `node_generate_answer` /
`node_synthesize_tool_answer` como un campo adicional en la respuesta
estructurada. `node_auto_evaluate_memory` extrae este campo y orquesta el
flujo de confirmación.

### 2.6 ¿Cómo se maneja "solo puede haber una propuesta pendiente"?

`store.py` implementa un **singleton** en la tabla `pending_proposal`:

```python
def has_pending_proposal() -> bool:
    return len(_get_table("pending_proposal")) > 0

def set_pending_proposal(proposal, reason, category, source_run_id, source_question):
    if has_pending_proposal():
        return None  # No sobrescribe
    if not validate_phi_safe(proposal):
        _record_audit("phi_rejected", ...)
        return None
    _get_table("pending_proposal").insert({...})

def clear_pending_proposal():
    _get_table("pending_proposal").truncate()
```

Reglas:
- Si ya hay propuesta pendiente y el usuario hace una nueva pregunta, la
  propuesta anterior se descarta (ver flujo en sección 5).
- `clear_pending_proposal()` se llama siempre al resolver (approve/reject/discard).

### 2.7 ¿Qué eventos se auditan?

| Evento               | Cuándo                                                      |
|-----------------------|-------------------------------------------------------------|
| `proposal_created`    | Se genera una propuesta y se persiste en `pending_proposal`. |
| `phi_rejected`        | `validate_phi_safe()` detecta PHI → propuesta descartada.   |
| `user_approved`       | Usuario responde "sí" → fact escrito en `memory_facts`.      |
| `user_rejected`       | Usuario responde "no" → propuesta descartada.                |
| `user_edited`         | Usuario edita la propuesta → fact escrito con texto editado.  |
| `user_discarded`      | Sin respuesta clara → propuesta descartada por defecto.      |
| `fact_deleted`        | Eliminación manual de un fact.                               |
| `consolidation_done`  | Consolidación ejecutada (resumen creado, originales marcados). |

Cada entrada incluye: `event_type`, `run_id`, `timestamp`, `proposal_text`,
`user_message`, `classification` (intención detectada), `detail`.

---

## 3. Arquitectura del sistema

### 3.1 Diagrama de flujo (textual)

```
                     ┌────────────────────────────────┐
                     │      receive_question          │
                     └──────────┬─────────────────────┘
                                │ pregunta válida
                                ▼
                     ┌────────────────────────────────┐
                     │    load_memory_context         │
                     │  • Carga facts de store        │
                     │  • ¿Hay propuesta pendiente?   │
                     └──┬──────────────────────────┬──┘
                        │ Sí                       │ No
                        ▼                          ▼
             ┌───────────────────┐      ┌──────────────────┐
             │ process_proposal  │      │  classify_query  │
             │ • Evalúa respuesta│      │ • RAG? Tool?     │
             │ • Approve/Reject  │      └────────┬─────────┘
             └──┬────────────────┘               │
                │ ¿nueva pregunta?               │
                ├─────── Sí ────► classify_query │
                └─────── No ───► END             │
                                                 ▼
                          ┌──────────────────────────┐
                          │  retrieve_context (RAG)  │
                          │  query_incident (tool)   │
                          │  query_inventory (tool)  │
                          └────────────┬─────────────┘
                                       ▼
                          ┌──────────────────────────┐
                          │  generate_answer         │
                          │  synthesize_tool_answer  │
                          │  • Incluye propuesta de  │
                          │    memoria si relevante  │
                          └────────────┬─────────────┘
                                       ▼
                          ┌──────────────────────────┐
                          │  auto_evaluate_memory    │
                          │  • ¿Propuesta generada?  │
                          │  • PHI validation        │
                          │  • Persiste proposal     │
                          │  • Responde al usuario   │
                          └────────────┬─────────────┘
                                       ▼
                                      END
```

### 3.2 Layout de tablas TinyDB

```
┌─────────────────────────────────────────────────────┐
│                 db.json (TinyDB)                    │
├──────────────────────────┬──────────────────────────┤
│      Table: memory_facts │  Table: pending_proposal │
│  ┌─────────────────────┐ │  ┌─────────────────────┐ │
│  │ doc_id: 1           │ │  │ doc_id: 1           │ │
│  │ content: str        │ │  │ proposal: str       │ │
│  │ category: str       │ │  │ reason: str         │ │
│  │ source_run_id: str  │ │  │ category: str       │ │
│  │ source_question: str│ │  │ source_run_id: str  │ │
│  │ approved_at: str    │ │  │ source_question: str│ │
│  │ consolidated: bool  │ │  │ created_at: str     │ │
│  │ expires_at: str/NULL│ │  └─────────────────────┘ │
│  └─────────────────────┘ │                          │
├──────────────────────────┤──────────────────────────┤
│   Table: audit_log       │                          │
│  ┌─────────────────────┐ │                          │
│  │ doc_id: 1           │ │                          │
│  │ event_type: str     │ │                          │
│  │ run_id: str         │ │                          │
│  │ timestamp: str      │ │                          │
│  │ proposal: str/NULL  │ │                          │
│  │ user_message: str   │ │                          │
│  │ classification: str │ │                          │
│  │ detail: str/NULL    │ │                          │
│  └─────────────────────┘ │                          │
└──────────────────────────┴──────────────────────────┘
```

### 3.3 Módulos del sistema

| Fichero                                   | Propósito                                      | Líneas ~ |
|-------------------------------------------|------------------------------------------------|----------|
| `services/agent/memory/__init__.py`       | Docstring del paquete, principios.             | 30       |
| `services/agent/memory/store.py`          | TinyDB backend, PHI validation, audit, consolidation. | 320 |
| `services/agent/memory/classifier.py`     | Clasificador de intención del usuario.         | 180      |
| `services/agent/memory/pipeline.py`       | Orchestrador: auto-evaluación + respuesta.     | 280      |
| `services/agent/memory/nodes.py`          | Nodos LangGraph (sync wrappers).              | 230      |
| `services/agent/graph.py` (modificado)    | Integración en el grafo (4 nodos + 3 rutas).  | +100     |

---

## 4. Validación PHI (HIPAA + UK GDPR)

### 4.1 Patrones detectados por `validate_phi_safe()`

```python
PHI_PATTERNS = [
    ("NOMBRE",           re.compile(r'\b[A-Z][a-záéíóúñ]+ [A-Z][a-záéíóúñ]+\b')),
    ("SSN",              re.compile(r'\b\d{3}-\d{2}-\d{4}\b')),
    ("NHS_NUMBER",       re.compile(r'\b\d{3}\s?\d{3}\s?\d{4}\b')),
    ("PHONE",            re.compile(r'\+?\d{1,3}[\s.-]?\d{6,10}')),
    ("EMAIL",            re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b')),
    ("MRN",              re.compile(r'\b(MRN|HCC?|EXP|PAT)\d{4,}\b')),
    ("ADDRESS",          re.compile(r'(?:Calle|Avda?|Plaza|Paseo|Rúa|C/)\s+\w+')),
    ("BIRTH_DATE",       re.compile(r'\b\d{2}[/-]\d{2}[/-]\d{4}\b')),
    ("CLINICAL_TERM",    re.compile(r'\b(diabetes|cáncer|tumor|VIH|SIDA|leucemia|quimioterapia|diagnóstico|paciente)\b', re.IGNORECASE)),
    ("INSURANCE_ID",     re.compile(r'\b(?:SSN?|NSS|NUSS|PAY)\d{6,}\b')),
]
```

### 4.2 Dual PHI validation

```
Primera validación (antes de mostrar propuesta al usuario):
  pipeline.py::auto_evaluate_proposal()
    → extrae propuesta del LLM
    → llama store.validate_phi_safe(proposal)
    → si PHI → descarta, log "phi_rejected", no muestra al usuario
    → si OK → persiste en pending_proposal y muestra

Segunda validación (antes de escribir en memoria):
  store.py::add_fact()
    → llama validate_phi_safe(content)
    → si PHI → rechaza escritura, log "phi_rejected"
  store.py::consolidate_memory()
    → llama validate_phi_safe() sobre cada resumen generado
    → si PHI → no consolida ese grupo, log "phi_rejected"
```

### 4.3 Tratamiento de falsos positivos

El patrón de nombres (`[A-Z][a-záéíóúñ]+ [A-Z][a-záéíóúñ]+`) puede generar
falsos positivos con nombres de ciudades, políticas o productos legítimos
(ej. "Manchester Office", "Medicare Plan"). Por diseño, **el sistema prefiere
falsos positivos sobre falsos negativos**: es mejor rechazar una propuesta
legítima que arriesgar PHI en memoria. El usuario puede reformular la
propuesta sin PHI.

---

## 5. Flujo de decisión completo

```
  ┌─────────────────────────────────────────────────────────────────────────┐
  │                            AUTO-EVALUACIÓN                              │
  │                                                                         │
  │  1. ¿Ya hay propuesta pendiente? ──Sí──► saltar (no duplicar)           │
  │  2. ¿Acaba de resolverse una propuesta? ──Sí──► saltar                  │
  │  3. ¿El texto contiene palabras clave de cambio? ──No──► saltar         │
  │  4. ¿El texto indica "no memorizable"? ──Sí──► saltar                   │
  │  5. Extraer categoría (operational/incident_pattern/staff_preference)   │
  │  6. ¿Es duplicado de un fact existente? ──Sí──► saltar                  │
  │  7. Validar PHI ──PHI detectado──► descartar, log "phi_rejected"        │
  │  8. Generar propuesta, persistir en pending_proposal, responder al      │
  │     usuario con "¿Quieres que recuerde esto?"                           │
  └─────────────────────────────────────────────────────────────────────────┘
                                     │
                                     ▼
  ┌─────────────────────────────────────────────────────────────────────────┐
  │                           RESPUESTA DEL USUARIO                         │
  │                                                                         │
  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐ │
  │  │   approve    │  │   reject     │  │    edit      │  │   discard    │ │
  │  │  (≥0.70)     │  │  (≥0.75)     │  │  (≥0.85)     │  │  (default)   │ │
  │  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘ │
  │         │                 │                 │                 │         │
  │         ▼                 ▼                 ▼                 ▼         │
  │   add_fact()        clear_pending()   validate_phi()    clear_pending() │
  │   audit:approved    audit:rejected   añadir/editado    audit:discarded  │
  │   consolidate()                       audit:edited                      │
  └─────────────────────────────────────────────────────────────────────────┘
```

---

## 6. Ciclo 1 — Aprobado

### Paso 1: Usuario pregunta

**Usuario**: "¿Ha cambiado algo en el proceso de derivación para Manchester?"

### Paso 2: El agente responde (RAG + LLM)

El LLM recupera chunks relevantes y genera una respuesta que incluye un
cambio de proceso. Internamente, el LLM produce:

```
answer: "Sí, a partir del 1 de julio el proceso de derivación para
         Manchester requiere el formulario M-CDX2 firmado por el
         coordinador clínico antes de enviarlo al hospital."
propuesta_memoria: {
  "text": "El nuevo proceso de derivación en Manchester requiere formulario M-CDX2 firmado por el coordinador clínico.",
  "reason": "El usuario preguntó por cambios en el proceso de derivación",
  "category": "operational"
}
```

### Paso 3: `node_auto_evaluate_memory` procesa

- `has_pending_proposal()` → False → OK
- `_should_evaluate(question+answer)` → detecta "cambiado" (trigger keyword)
- `_is_duplicate()` → no hay facts similares → OK
- `validate_phi_safe(proposal_text)` → True (sin PHI detectado) → OK
- `set_pending_proposal(proposal, reason, category, source_run_id, question)`

### Paso 4: Respuesta al usuario con propuesta

> Sí, a partir del 1 de julio el proceso de derivación para
> Manchester requiere el formulario M-CDX2 firmado por el
> coordinador clínico antes de enviarlo al hospital.
>
> 📝 **¿Quieres que recuerde esto para la próxima vez?**
> > El nuevo proceso de derivación en Manchester requiere formulario
> > M-CDX2 firmado por el coordinador clínico.
> > *(categoría: operational)*
>
> Responde con **«sí»**, **«no»**, o edita el texto.

### Paso 5: Usuario responde

**Usuario**: "Sí, guárdalo."

### Paso 6: `node_process_proposal` clasifica y persiste

- `classify_user_intent("Sí, guárdalo.")` → `approve` (confidence: 0.85)
- `add_fact(proposal_text, "operational", run_id, now(), False, None)`
- `clear_pending_proposal()`
- `_record_audit("user_approved", ...)`
- `consolidate_memory()` (si hay ≥50 facts)

### Paso 7: Confirmación al usuario

> ✅ Entendido. He guardado esta información para futuras consultas:
> > El nuevo proceso de derivación en Manchester requiere formulario
> > M-CDX2 firmado por el coordinador clínico.
>
> La próxima vez que preguntes sobre derivaciones, lo tendré en cuenta.

### Paso 8: Interacción futura refleja la memoria

En una conversación posterior, `load_memory_context` carga el fact y lo
incluye en el contexto del LLM:

> **Contexto de memoria:**
> 📌 El nuevo proceso de derivación en Manchester requiere formulario
> M-CDX2 firmado por el coordinador clínico.

**Usuario**: "¿Qué necesito para una derivación en Manchester?"

**Agente**: "Para derivar un paciente a Manchester necesitas el formulario
M-CDX2 firmado por el coordinador clínico. Ese es el nuevo proceso desde
julio."

### Registro de auditoría (Ciclo 1)

```json
[
  {"event_type": "proposal_created",  "run_id": "run_001", "timestamp": "2025-07-16T10:00:00",
   "proposal": "El nuevo proceso de derivación...", "classification": null, "detail": null},
  {"event_type": "user_approved",     "run_id": "run_002", "timestamp": "2025-07-16T10:01:00",
   "proposal": "El nuevo proceso de derivación...", "user_message": "Sí, guárdalo.",
   "classification": "approve", "detail": "Fact escrito en memory_facts"},
]

### Evidencia real desde tests (APROBADO)

```
$ uv run python -m pytest tests/pipelines/test_agent_graph.py -v --tb=long \
  -k "test_process_user_response_approve or test_audit_log_created_on_approve"

tests/pipelines/test_agent_graph.py::TestMemoryPipeline::test_process_user_response_approve PASSED
tests/pipelines/test_agent_graph.py::TestMemoryPipeline::test_audit_log_created_on_approve PASSED
```

**`test_process_user_response_approve`** (código real):
```python
set_pending_proposal(
    proposal="Nuevo proceso en Manchester.",
    reason="test", category="operational"
)
result = process_user_response("sí, guárdalo")
assert result["resolution"] == "approved"
assert result["final_content"] is not None

facts = get_all_facts()
assert len(facts) > initial_count  # ← se escribió en memoria
```

**`test_audit_log_created_on_approve`** (código real):
```python
set_pending_proposal(
    proposal="Hecho auditado.",
    reason="test", category="operational"
)
process_user_response("sí")
log = get_audit_log(limit=10)
event_types = [e.get("event_type", "") for e in log]
has_approve = any("approved" in et for et in event_types)
assert has_approve  # ← evento 'approved' en audit_log
```

---

## 7. Ciclo 2 — Rechazado

### Paso 1: Usuario pregunta

**Usuario**: "He notado que los incidentes de facturación suelen escalar los lunes."

### Paso 2: El agente responde + propuesta

El LLM genera respuesta + propuesta:

```
answer: "Tienes razón, según los datos el 40% de los incidentes de
         facturación que se crean en fin de semana escalan a nivel 2
         el lunes por la mañana."
propuesta_memoria: {
  "text": "Los incidentes de facturación suelen escalar a nivel 2 los lunes si se crean en fin de semana.",
  "reason": "El usuario señala un patrón recurrente de incidentes",
  "category": "incident_pattern"
}
```

### Paso 3: `set_pending_proposal` persiste

### Paso 4: Usuario responde

**Usuario**: "No, no hace falta que recuerdes eso."

### Paso 5: Clasificación y descarte

- `classify_user_intent("No, no hace falta que recuerdes eso.")`
  → detecta "no" + "no hace falta" → `reject` (confidence: 0.82)
- `_record_audit("user_rejected", ...)`
- `clear_pending_proposal()`

### Paso 6: Confirmación

> Entendido. No guardo esta información.

### Paso 7: Interacción futura — sin cambio (no hay fact en memoria)

**Usuario**: "¿Qué pasa con los incidentes de facturación los lunes?"

**Agente**: (sin contexto de memoria) "Según los datos del sistema, hay
varios incidentes de facturación abiertos. ¿Quieres que te muestre los
detalles de alguno en particular?"

### Registro de auditoría (Ciclo 2)

```json
[
  {"event_type": "proposal_created",  "run_id": "run_003", "timestamp": "2025-07-16T11:00:00",
   "proposal": "Los incidentes de facturación suelen escalar...", "classification": null, "detail": null},
  {"event_type": "user_rejected",     "run_id": "run_004", "timestamp": "2025-07-16T11:01:00",
   "proposal": "Los incidentes de facturación suelen escalar...", "user_message": "No, no hace falta que recuerdes eso.",
   "classification": "reject", "detail": null},
]
```

### Evidencia real desde tests (RECHAZADO)

```
$ uv run python -m pytest tests/pipelines/test_agent_graph.py -v --tb=long \
  -k "test_process_user_response_reject"

tests/pipelines/test_agent_graph.py::TestMemoryPipeline::test_process_user_response_reject PASSED
```

**`test_process_user_response_reject`** (código real):
```python
set_pending_proposal(
    proposal="Patrón de incidentes los lunes.",
    reason="test", category="incident_pattern"
)

result = process_user_response("no, olvídalo")
assert result["resolution"] == "rejected"

facts = get_all_facts()
assert len(facts) == initial_count  # ← NO se escribió en memoria
```

**Resultado**: `resolution = "rejected"`, `final_content = None` (no se escribe
nada en `memory_facts`). El audit_log registra el evento `user_rejected` con el
texto original de la propuesta y el mensaje del usuario.

### Suite completa de memoria (32 tests) — todos pasan

```
$ uv run python -m pytest tests/pipelines/test_agent_graph.py -v -k "TestMemory"

tests/pipelines/test_agent_graph.py::TestMemoryPHI::test_phi_validate_clean_text PASSED
tests/pipelines/test_agent_graph.py::TestMemoryPHI::test_phi_rejects_name PASSED
tests/pipelines/test_agent_graph.py::TestMemoryPHI::test_phi_rejects_ssn PASSED
tests/pipelines/test_agent_graph.py::TestMemoryPHI::test_phi_rejects_email PASSED
tests/pipelines/test_agent_graph.py::TestMemoryPHI::test_phi_rejects_phone PASSED
tests/pipelines/test_agent_graph.py::TestMemoryPHI::test_phi_rejects_patient_id PASSED
tests/pipelines/test_agent_graph.py::TestMemoryPHI::test_phi_empty_string_is_safe PASSED
tests/pipelines/test_agent_graph.py::TestMemoryPHI::test_set_pending_rejects_phi PASSED
tests/pipelines/test_agent_graph.py::TestMemoryStore::test_add_and_get_fact PASSED
tests/pipelines/test_agent_graph.py::TestMemoryStore::test_add_fact_rejects_phi PASSED
tests/pipelines/test_agent_graph.py::TestMemoryStore::test_pending_proposal_singleton PASSED
tests/pipelines/test_agent_graph.py::TestMemoryStore::test_get_facts_by_category PASSED
tests/pipelines/test_agent_graph.py::TestMemoryStore::test_delete_fact PASSED
tests/pipelines/test_agent_graph.py::TestMemoryClassifier::test_classify_approve_si PASSED
tests/pipelines/test_agent_graph.py::TestMemoryClassifier::test_classify_approve_ok PASSED
tests/pipelines/test_agent_graph.py::TestMemoryClassifier::test_classify_reject_no PASSED
tests/pipelines/test_agent_graph.py::TestMemoryClassifier::test_classify_edit_mejor_dicho PASSED
tests/pipelines/test_agent_graph.py::TestMemoryClassifier::test_classify_discard_by_default PASSED
tests/pipelines/test_agent_graph.py::TestMemoryClassifier::test_classify_no_proposal_when_none_pending PASSED
tests/pipelines/test_agent_graph.py::TestMemoryClassifier::test_classify_empty_message_discards PASSED
tests/pipelines/test_agent_graph.py::TestMemoryPipeline::test_auto_evaluate_proposal_no_trigger PASSED
tests/pipelines/test_agent_graph.py::TestMemoryPipeline::test_auto_evaluate_proposal_with_trigger PASSED
tests/pipelines/test_agent_graph.py::TestMemoryPipeline::test_auto_evaluate_proposal_skips_non_memorizable PASSED
tests/pipelines/test_agent_graph.py::TestMemoryPipeline::test_process_user_response_approve PASSED
tests/pipelines/test_agent_graph.py::TestMemoryPipeline::test_process_user_response_reject PASSED
tests/pipelines/test_agent_graph.py::TestMemoryPipeline::test_process_user_response_edit PASSED
tests/pipelines/test_agent_graph.py::TestMemoryPipeline::test_process_user_response_discard_by_default PASSED
tests/pipelines/test_agent_graph.py::TestMemoryPipeline::test_audit_log_created_on_approve PASSED
tests/pipelines/test_agent_graph.py::TestMemoryPipeline::test_build_memory_context_format PASSED
tests/pipelines/test_agent_graph.py::TestMemoryGraphIntegration::test_memory_nodes_exist_in_graph PASSED
tests/pipelines/test_agent_graph.py::TestMemoryGraphIntegration::test_memory_routes_defined PASSED
tests/pipelines/test_agent_graph.py::TestMemoryGraphIntegration::test_route_after_receive_goes_to_load_memory PASSED

====================== 32 passed in 1.17s =======================
```

---

## 8. Ciclo 3 — Edición por el usuario

### Paso 1: Usuario pregunta

**Usuario**: "¿Cuál es tu criterio para priorizar incidentes?"

### Paso 2: El agente responde + propuesta

```
answer: "Actualmente priorizo incidentes por estado (abiertos primero)
         y por antigüedad (los más recientes primero)."
propuesta_memoria: {
  "text": "El usuario prefiere ver incidentes ordenados por estado y antigüedad.",
  "reason": "El usuario preguntó por el criterio de priorización",
  "category": "staff_preference"
}
```

### Paso 3: Usuario responde editando

**Usuario**: "Mejor dicho, prefiero ver primero los críticos y luego los abiertos."

### Paso 4: Clasificación

- `classify_user_intent("Mejor dicho, prefiero ver primero los críticos...")`
  → detecta "mejor dicho" → `edit` (confidence: 0.88)
- `_extract_edit_text()` → extrae: "prefiero ver primero los críticos y
  luego los abiertos"

### Paso 5: PHI validation del texto editado

- `validate_phi_safe("prefiero ver primero los críticos y luego los abiertos")`
  → True (sin PHI)

### Paso 6: Escritura

- `add_fact("El usuario prefiere ver primero los incidentes críticos y luego los abiertos.", "staff_preference", ...)`
- `_record_audit("user_edited", ..., classification="edit", ...)`
- `clear_pending_proposal()`

### Paso 7: Confirmación

> Entendido. He actualizado la información:
> > El usuario prefiere ver primero los incidentes críticos y luego
> > los abiertos.
>
> La próxima vez que consulte incidentes, aplicaré este orden.

---

## 9. Ciclo 4 — PHI bloqueado

### Paso 1: Usuario pregunta

**Usuario**: "¿Sabes algo del paciente Juan Pérez de la habitación 304?"

### Paso 2: El agente responde (sin PHI en respuesta)

El LLM debe responder sin incluir PHI. Pero si el LLM incluyera
accidentalmente PHI en la propuesta de memoria:

```
answer: "Lo siento, no tengo acceso a información clínica de pacientes."
propuesta_memoria: {
  "text": "El paciente Juan Pérez está en la habitación 304 con diabetes tipo 2.",
  "reason": "Mención explícita de paciente",
  "category": "operational"
}
```

### Paso 3: PHI validation bloquea la propuesta

- `validate_phi_safe("El paciente Juan Pérez está en la habitación 304 con diabetes tipo 2.")`
  → **PHI detectado**: NOMBRE ("Juan Pérez"), CLINICAL_TERM ("diabetes"),
    "paciente"
  → Retorna `(False, ["NOMBRE", "CLINICAL_TERM", "CLINICAL_TERM"])`

### Paso 4: Propuesta descartada + audit

- `set_pending_proposal()` retorna `None` (no persiste)
- `_record_audit("phi_rejected", ..., detail="Patrones detectados: NOMBRE, CLINICAL_TERM")`
- La respuesta al usuario **no incluye** el mensaje de propuesta

### Paso 5: Respuesta limpia al usuario

> Lo siento, no tengo acceso a información clínica de pacientes.

(Sin mención de la propuesta bloqueada — el usuario no necesita saber que
se intentó proponer algo con PHI.)

### Registro de auditoría (Ciclo 4)

```json
[
  {"event_type": "phi_rejected", "run_id": "run_007", "timestamp": "2025-07-16T13:00:00",
   "proposal": "El paciente Juan Pérez está en la habitación 304...",
   "user_message": "¿Sabes algo del paciente Juan Pérez de la habitación 304?",
   "classification": null, "detail": "PHI detectado: NOMBRE, CLINICAL_TERM, CLINICAL_TERM"},
]
```

---

## 10. Consolidación y olvido

### 10.1 Política de consolidación

| Parámetro               | Valor     | Justificación                                                 |
|--------------------------|-----------|---------------------------------------------------------------|
| Umbral de consolidación | 50 facts  | Suficientes para detectar patrones sin esperar demasiado.     |
| TTL de facts originales | 90 días   | Balanceó entre retención util y limpieza automática.          |
| Consolidados expiran    | No        | Son resúmenes; si ya no son relevantes, se eliminan manualmente. |

### 10.2 Ejemplo de consolidación

Supongamos que tras varios ciclos aprobados hay 52 facts en `memory_facts`:

**Facts originales** (entradas individuales):
- "El nuevo proceso de derivación en Manchester requiere formulario M-CDX2"
- "Las derivaciones a Sheffield necesitan autorización del director médico"
- "El plazo de derivación a Leeds es de 48 horas"
- ... (más facts de derivación)
- "Los incidentes de facturación escalan los lunes"
- "El usuario prefiere incidentes críticos primero"
- ...

**Tras consolidación** (agrupado por categoría `operational`):

```
Resumen: El usuario ha confirmado 14 cambios en procesos de derivación
         que afectan a Manchester (M-CDX2), Sheffield (autorización
         director médico) y Leeds (48h). También hay 8 preferencias
         de visualización confirmadas.
```

Los 14 facts originales de `operational` reciben `expires_at = now + 90d`.
El resumen consolidado se añade con `consolidated=True`, `expires_at=None`.

### 10.3 Mantenimiento programado

`run_maintenance()` se llama:
- Después de cada aprobación/edición (si hay ≥50 facts).
- Como tarea CRON diaria (vía `services/celery_app.py` o similar).

---

## 11. Integración con el grafo LangGraph

### 11.1 Nuevos nodos (4)

| Nodo                    | Función en `nodes.py`          | Propósito                                               |
|-------------------------|--------------------------------|---------------------------------------------------------|
| `load_memory_context`   | `node_load_memory_context`     | Cargar facts desde TinyDB, detectar propuesta pendiente |
| `process_proposal`      | `node_process_proposal`        | Procesar respuesta del usuario a una propuesta          |
| `auto_evaluate_memory`  | `node_auto_evaluate_memory`    | Evaluar si la interacción merece memoria, proponer      |

### 11.2 Nuevas rutas (3)

| Ruta                         | Origen → Destino                                      |
|------------------------------|--------------------------------------------------------|
| `route_after_load_memory`    | `load_memory_context` → `process_proposal` (si pendiente) / `classify_query` (si no) |
| `route_after_process_proposal` | `process_proposal` → `classify_query` (si nueva pregunta) / `__end__` (si solo respuesta) |
| `route_after_auto_evaluate`  | `auto_evaluate_memory` → `__end__` (siempre)           |

### 11.3 Nuevos campos en AgentState

```python
class AgentState(TypedDict):
    # ... campos existentes ...
    # --- Memoria (Parte 3 — Hito 8) ---
    "memory_facts": Annotated[list[str], add_to_list],       # Facts cargados
    "memory_context": str,                                    # Contexto formateado para el LLM
    "memory_proposal": str | None,                            # Texto de propuesta pendiente
    "memory_proposal_handled": bool,                          # ¿Propuesta procesada?
    "memory_proposal_handled_text": str | None,               # Confirmación para el usuario
    "memory_proposal_result": str | None,                     # Resultado (approved/rejected/etc)
    "run_id": str,                                            # ID único de corrida
```

### 11.4 Verificación de compilación

```python
from services.agent.graph import agent_graph
print(agent_graph.nodes.keys())
# ['__start__', 'receive_question', 'retrieve_context', 'generate_answer',
#  'respond_no_info', 'handle_error', 'classify_query', 'query_incident',
#  'query_inventory', 'synthesize_tool_answer', 'load_memory_context',
#  'process_proposal', 'auto_evaluate_memory']
```

**Tests**: 32 tests existentes pasan (ver `test_agent_graph.py`), incluyendo:
- `test_graph_compiles_successfully`
- `test_graph_has_all_expected_nodes` (actualizado con nuevos nodos)
- `test_valid_question_routes_to_load_memory` (ruta actualizada)

---

## 12. Referencias

| Documento                  | Sección relevante            |
|----------------------------|------------------------------|
| `CONTEXT.md`               | `##Hito 8 — Memoria y Auto-mejora de Agentes` |
| `services/agent/memory/__init__.py` | Principios de diseño        |
| `services/agent/graph.py`  | Integración en LangGraph     |
| `services/agent/memory/store.py`    | Backend TinyDB + PHI         |
| `services/agent/memory/classifier.py` | Clasificador de intención  |
| `services/agent/memory/pipeline.py` | Orchestrador                |
| `services/agent/memory/nodes.py`    | Nodos LangGraph             |
| `tests/pipelines/test_agent_graph.py` | Tests actualizados         |
| `agente-soporte-langgraph.md`       | Diseño original del agente  |
| `mcp-server.md`            | MCP Server (tools externas)  |

---
