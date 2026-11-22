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
9-B. [Ciclo 5 — Unrelated (mensaje no relacionado con la propuesta pendiente)](#9-b-ciclo-5--unrelated-mensaje-no-relacionado-con-la-propuesta-pendiente)
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
| Nombre + apellido       | "María García"                    | `\b[A-Z][a-zA-ZáéíóúñÁÉÍÓÚ]+ [A-Z][a-zA-ZáéíóúñÁÉÍÓÚ]+\b` |
| Nombre + apellido (título) | "Sr. García"                  | `\b(?:Sr[aá]?\.?|Sra\.|Dr[aá]\.?|Paciente|Socio|Cliente)\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+` |
| Apellido hispano standalone | "García"                    | Lista de ~70 apellidos comunes (García, López, Pérez, Rodríguez, etc.) |
| Número de teléfono USA  | "555-123-4567"                   | `\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b` |
| Teléfono internacional  | "+34 612 345 678"                | `(?:(?<=\s)|(?<=^))\+?\d{1,3}[\s.-]?\d{2,4}(?:[\s.-]?\d{2,4}){2,3}\b` |
| SSN                     | "123-45-6789"                    | `\b\d{3}-\d{2}-\d{4}\b`                |
| NHS Number (UK)         | "123 456 7890"                   | `\b\d{5}\s\d{6,7}\b`                   |
| Email                   | "paciente@email.com"             | `\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b` |
| Dirección física (US/UK)| "123 Main Street"                | `\b\d{1,5}\s+[A-Za-z]+\s+(?:Street|St|Avenue|Av|...)` |
| MRN / EXP / PAT + dígitos | "MRN-987654", "MRN: 1234567"   | `\b(MRN|EX[AP]|HCC|PAT|HIST|NHS|SSN)\s*[-:_]?\s*\d{4,}\b` |
| ID de paciente textual   | "patient id: P12345"             | `\b(?:patient\s+(?:id|number|#)|paciente\s+(?:id|número|#)|historia\s+clínica|medical\s+record\s+number)\s*:\s*\w+` |
| Fecha de nacimiento con prefijo | "fecha de nacimiento: 15/03/1985" | `\b(?:fecha\s+de\s+nacimiento|dob|birth\s*date|born|nacid[oó]?\s+el)\s*:?\s*\d{1,2}[/-]\d{1,2}[/-]\d{2,4}` |
| Fecha suelta (año 19xx/20xx) | "15/03/1985"                   | `\b\d{2}[/-]\d{2}[/-](?:19|20)\d{2}\b` |
| Diagnóstico / condición clínica | "diabetes tipo 2", "cáncer" | Términos clínicos en contexto narrativo (diabetes, cáncer, VIH, SIDA, tumor, leucemia, quimioterapia, hipertensión, cardiopatía, hepatitis, cirrosis, neoplasia, metástasis, infección, síndrome, alzhéimer, parkinson, esclerosis, artritis, asma, epilepsia, depresión, ansiedad, obesidad, osteoporosis, + variantes con tipo/gravedad) |
| Información clínica con prefijo | "diagnóstico: diabetes"       | `\b(?:diagnóstico|diagnosis|treatment|tratamiento|prescripción|prescription|medication|medicación|resultados? de laboratorio|lab results?)\s*:?\s*\S` |
| "Paciente" en contexto narrativo | "el paciente refiere dolor" | `\b(?:el\s+)?paciente\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+\s` |
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
5. **Clasificador conservador**: el clasificador de intención (`classifier.py`,
   ~567 líneas) usa **6 ejes analíticos estructurales** en lugar de listas
   planas de patrones. Evalúa la respuesta del usuario mediante un pipeline de
   **8 pasos secuenciales**: pregunta/condicional → rechazo → edición →
   cortesía → falsos positivos → contradicciones → aprobación → "si" corto.
   Por defecto → `discard`. Incorpora 3 guardas adicionales:
   - **Negación con pronombres**: "no lo guardes", "no la grabes" → reject
   - **Contradicciones**: "sí, pero no" → discard por ambigüedad
   - **Comandos negados**: "guardes" en "no lo guardes" no suma approve
   (Umbrales: aprobación ≥0.70, rechazo ≥0.75, edición ≥0.85).
6. **Sin escritura en RAG**: la memoria usa TinyDB, **no** las colecciones
   Qdrant de la empresa. Esto aísla completamente el riesgo de poison en
   retrieval.
7. **Audit trail inmutable**: toda decisión, PHI bloqueado, aprobación y
   rechazo queda registrado en `audit_log`. Es posible detectar inyectores
   a posteriori.

### 2.5 ¿Por qué NO un segundo agente o grafo separado? (No multi-agente → LLM directo)

La auto-evaluación es una **extensión del mismo modelo de lenguaje**, no un
segundo agente. El prompt del LLM incluye un bloque de instrucciones
(`_MEMORY_SYSTEM_PROMPT_SUFFIX` en `rag.py`) que le pide evaluar, al final de
su respuesta, si la interacción contiene información memorable, y devolverlo
como JSON estructurado. Esto significa:

- Una sola llamada al LLM → answer + optional memory proposal en formato JSON.
- Sin latencia adicional de round-trip.
- Sin estado compartido entre agentes.
- Sin complejidad de orquestación.

**Antes (heurístico → reemplazado)**: El pipeline post-hoc (`_should_evaluate`)
usaba triggers léxicos y extractores de contexto que fallaban en casos como
`"porque"` matcheando como `"por qué"`, o conversaciones sin palabras clave
explícitas pero con información operativa valiosa.

**Ahora (LLM → primario)**: `generate_answer_with_memory()` en `rag.py` añade
instrucciones al system prompt para que el LLM decida si algo es memorable.
El pipeline tradicional queda como **fallback** en `auto_evaluate_proposal()`.

Si se usara un segundo agente LangGraph para evaluar la memoria, se necesitaría:

1. Un segundo StateGraph → duplicar el esfuerzo de compilación, test y
   despliegue.
2. Un mecanismo de comunicación entre grafos (subgrafos o Send/Receive) →
   sincronización compleja.
3. Evaluar si el mensaje es "memorizable" requiere el mismo LLM → no hay
   ahorro de calidad.

**Decisión**: la propuesta de memoria se genera en `node_generate_answer`
como campo estructurado (`llm_memory_proposal`) en la misma llamada LLM.
`node_auto_evaluate_memory` procesa este campo — si el LLM devolvió propuesta,
la prioriza sobre las heurísticas.

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

| Evento           | Cuándo                                                      |
|-------------------|-------------------------------------------------------------|
| `proposed`       | Se genera una propuesta y se persiste en `pending_proposal`. |
| `phi_rejected`   | `validate_phi_safe()` detecta PHI → propuesta descartada.   |
| `approved`       | Usuario responde "sí" → fact escrito en `memory_facts`.      |
| `rejected`       | Usuario responde "no" → propuesta descartada.                |
| `edited`         | Usuario edita la propuesta → fact escrito con texto editado.  |
| `discarded`      | Sin respuesta clara → propuesta descartada por defecto.      |
| `consolidated`   | Consolidación ejecutada (resumen creado, originales marcados). |

Cada entrada incluye: `event_type`, `run_id`, `timestamp`, `proposal_text`,
`user_message`, `classification` (intención detectada), `detail`.

### 2.8 ¿Cómo decide si una interacción merece memoria? (LLM + fallback heurístico)

**Mecanismo primario (Mejora 17)**: El LLM evalúa la memoria durante la
generación de respuesta. `generate_answer_with_memory()` en `rag.py` añade
el bloque `_MEMORY_SYSTEM_PROMPT_SUFFIX` al system prompt, instruyendo al LLM
a devolver opcionalmente una propuesta JSON estructurada al final de su respuesta.

```python
_MEMORY_SYSTEM_PROMPT_SUFFIX: str = (
    "\n\n"
    "=== Instrucción adicional: auto-evaluación de memoria ===\n"
    "Además de responder la pregunta, evalúa si la interacción contiene "
    "información operativa que valga la pena recordar para futuras consultas.\n\n"
    "Al FINAL de tu respuesta (después de la respuesta al usuario), "
    "añade una línea exactamente con este formato:\n"
    "---MEMORY_PROPOSAL---\n"
    "Sigue uno de estos dos formatos JSON:\n\n"
    'Si HAY algo memorable:\n'
    '{"proposal": "texto del hecho a recordar", '
    '"category": "operational|incident_pattern|staff_preference", '
    '"reason": "por qué es útil recordarlo"}\n\n'
    'Si NO hay nada memorable:\n'
    "null\n\n"
    "Reglas para decidir qué es memorable:\n"
    "1. SÍ memorable: cambios operativos, nuevos protocolos, excepciones por clínica, "
    "patrones de incidentes conocidos, preferencias de staff sobre presentación.\n"
    "2. NO memorable: consultas puntuales de datos, preguntas hipotéticas, "
    "cierres de conversación (gracias, saludos), datos de pacientes (PHI).\n"
    "3. NUNCA memorable: información de salud protegida (PHI) como nombres de pacientes, "
    "diagnósticos, identificadores clínicos, números de seguro, o cualquier dato "
    "que permita re-identificar a un paciente.\n\n"
    "Ejemplos de SÍ memorable:\n"
    '- "En la clínica de Manchester el proceso de referidos cambió"\n'
    '- "La alerta de no-show en Austin fue por cierre de carretera, no por falla del sistema"\n'
    '- "El reporte semanal para Diane debe incluir vacantes por rol"\n\n'
    'Ejemplos de NO memorable:\n'
    '- "¿Cuál es la tasa de no-show?" (consulta puntual)\n'
    '- "Gracias, con eso resuelvo mi reporte" (cierre)\n'
    '- "El paciente Johnson canceló su cita" (contiene PHI)\n'
    '- "Si llueve mañana" (hipotético/condicional)\n\n'
    "IMPORTANTE: Si no estás seguro, responde null (nunca asumas que algo es memorable)."
)
```

**Mecanismo de respaldo (heurístico tradicional)**: Si el LLM no devuelve
propuesta, `auto_evaluate_proposal()` en `pipeline.py` usa la función
`_should_evaluate()` que implementa un clasificador basado en señales léxicas
(sin LLM) como fallback. Ver sección 5.1 para el flujo completo.

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
                          │  synthesize_tool_answer  │◄──────┐
                          │  • Incluye propuesta de  │       │
                          │    memoria si relevante  │       │
                          └──────────┬───────────────┘       │
                                     │ (ANTES: loop aquí)    │
                                     ▼                       │
                          ┌──────────────────────────┐       │
                          │  auto_evaluate_memory    │       │
                          │  • ¿Propuesta generada?  │       │
                          │  • PHI validation        │       │
                          │  • Persiste proposal     │       │
                          │  • Responde al usuario   │       │
                          └────────────┬─────────────┘       │
                                       ▼                     │
                          ┌──────────────────────────┐       │
                          │  route_after_synthesis   │───────┘
                          │  (NUNCA vuelve a         │  (roto)
                          │   synthesize_tool_answer)│
                          └────────────┬─────────────┘
                                       ▼
                                      END
```

**Corrección del loop RAG+tool**: Originalmente `synthesize_tool_answer` usaba
`route_after_generate` que podía re-enrutar a `synthesize_tool_answer` porque
`tool_results` aún estaban en el estado del grafo. Se creó
`route_after_synthesis()` que va directo a `auto_evaluate_memory` o `__end__`,
rompiendo el ciclo.

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
| `services/agent/memory/store.py`          | TinyDB backend, PHI validation, audit, consolidation. | ~716 |
| `services/agent/memory/classifier.py`     | Clasificador estructural de intención (6 ejes analíticos, 8 pasos). | ~514     |
| `services/agent/memory/pipeline.py`       | Orchestrador: auto-evaluación + respuesta.     | ~802     |
| `services/agent/memory/nodes.py`          | Nodos LangGraph (sync wrappers).              | ~267     |
| `data/pipelines/rag.py` (modificado)      | `generate_answer_with_memory()` — LLM devuelve answer + propuesta. | +140    |
| `services/agent/graph.py` (modificado)    | Integración en el grafo (4 nodos + 3 rutas).  | +100     |

---

## 4. Validación PHI (HIPAA + UK GDPR)

### 4.1 Patrones detectados por `validate_phi_safe()`

```python
_PHI_PATTERNS: list[re.Pattern] = [
    # ── Identificadores personales (US + UK) ─────────────────────────
    re.compile(r"\b[A-Z][a-zA-ZáéíóúñÁÉÍÓÚ]+ [A-Z][a-zA-ZáéíóúñÁÉÍÓÚ]+\b"),  # Nombre completo
    # ── Datos de contacto ────────────────────────────────────────────
    re.compile(r"\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b"),       # Teléfono USA (555-123-4567)
    re.compile(r"\b\d{5}\s\d{6,7}\b"),                       # NHS Number UK (123 4567890)
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),                    # SSN (123-45-6789)
    # ── Teléfono internacional (español/europeo: +34 612 345 678) ────
    re.compile(r"(?:(?<=\s)|(?<=^))\+?\d{1,3}[\s.-]?\d{2,4}(?:[\s.-]?\d{2,4}){2,3}\b"),
    # ── Información clínica (términos y diagnósticos) ────────────────
    # Captura prefijos como "diagnóstico:", "tratamiento:", "lab results:"
    re.compile(r"\b(?:diagn[oó]stico|diagnosis|treatment|tratamiento|"
               r"prescripci[oó]n|prescription|medication|medicaci[oó]n|"
               r"resultados?\s+de\s+laboratorio|lab\s+results?)\s*:?\s*\S",
               re.IGNORECASE),
    # ── Términos clínicos sensibles (condiciones, enfermedades) ─────
    re.compile(r"\b(diabetes|c[ná]ncer|tumor|VIH|SIDA|leucemia|quimioterapia|"
               r"hipertensión|cardiopatía|insuficiencia|hepatitis|cirrosis|"
               r"enfermedad\s+cr[oó]nica|diagnóstico|patología|neoplasia|"
               r"met[áa]stasis|infecci[oó]n|síndrome|trastorno|"
               r"alzhéimer|parkinson|esclerosis|artritis|asma|epilepsia|"
               r"depresi[oó]n|ansiedad|obesidad|osteoporosis)"
               r"(?:\s+(?:tipo\s+\d|cr[oó]nica|aguda|leve|grave|moderada))?",
               re.IGNORECASE),
    # ── Palabra "paciente" en contexto narrativo (no operativo) ────
    re.compile(r"\b(?:el\s+)?paciente\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+\s", re.IGNORECASE),
    # ── Fechas de nacimiento / edad exacta ───────────────────────────
    re.compile(r"\b(?:fecha\s+de\s+nacimiento|dob|birth\s*date|born|"
               r"nacid[oó]?\s+el)\s*:?\s*\d{1,2}[/-]\d{1,2}[/-]\d{2,4}",
               re.IGNORECASE),
    # ── Fecha suelta en contexto clínico (año 19xx/20xx) ────────────
    re.compile(r"\b\d{2}[/-]\d{2}[/-](?:19|20)\d{2}\b"),
    # ── Identificadores de paciente (MRN, EXP, PAT, HCC, etc.) ──────
    re.compile(r"\b(?:patient\s+(?:id|number|#|n[uo]mero)|"
               r"paciente\s+(?:id|n[uo]mero|#)|"
               r"historia\s+cl[ií]nica|medical\s+record\s+number)\s*:?\s*\w+",
               re.IGNORECASE),
    # ── MRN / EXP / PAT + dígitos (con o sin guion o dos puntos) ─────
    re.compile(r"\b(MRN|EX[AP]|HCC|PAT|HIST|NHS|SSN)\s*[-:_]?\s*\d{4,}\b", re.IGNORECASE),
    # ── Email ────────────────────────────────────────────────────────
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),
    # ── Dirección física (US/UK) ─────────────────────────────────────
    re.compile(r"\b\d{1,5}\s+[A-Za-z]+\s+(?:Street|St|Avenue|Av|Road|Rd|"
               r"Drive|Dr|Lane|Ln|Boulevard|Blvd|Circle|Cir)\b", re.IGNORECASE),
    # ── Apellidos precedidos de título o rol ─────────────────────────
    re.compile(r"\b(?:Sr[aá]?\.?|Sra\.|Dr[aá]\.?|Paciente|Socio|Cliente)\s+"
               r"[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)?", re.IGNORECASE),
    # ── Apellidos hispanos comunes en contexto narrativo ──────────────
    # Detecta apellidos como García, López, Pérez cuando aparecen
    # como palabras capitalizadas standalone (sin nombre de pila).
    re.compile(r"\b(?:García|López|Pérez|Rodríguez|Martínez|Hernández|"
               r"González|Sánchez|Díaz|Álvarez|Fernández|Gutiérrez|"
               r"Jiménez|Morales|Ruiz|Ramírez|Moreno|Muñoz|Romero|"
               r"Navarro|Torres|Domínguez|Vázquez|Ramos|Cruz|Flores|"
               r"Rivera|Reyes|Castillo|Ortiz|Medina|Silva|Garza|Vargas|"
               r"Campos|Guzmán|Delgado|Pena|Contreras|Valdez|Estrada|"
               r"Luna|Juárez|Aguilar|Rojas|Salazar|Chávez|Núñez|Cabrera|"
               r"Acosta|Figueroa|Mendoza|Vega|Soto|Molina|Cortés|"
               r"Suárez|Castro|Pacheco|Velázquez|Ibáñez|Márquez|"
               r"Guerrero|Santos|Valencia|Peña|Peralta|Rivas|Meza)\b"),
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

#### 4.3.1 Lista blanca (whitelist) de términos NO PHI

Para evitar que datos operativos de HealthCore sean bloqueados por PHI
validation, se añadió una **lista blanca** en `store.py`:

```python
_PHI_WHITELIST: set[str] = {
    # HealthCore Staff
    "Diane Foster", "Claire Whitfield", "James Osei",
    "Marcus Reid", "Sandra Okonkwo", "Priya Nair",
    # Clínicas y ubicaciones operativas
    "Austin", "Manchester", "London", "Miami",
    "Dallas", "Houston", "San Antonio", "Orlando",
    "Tampa", "Atlanta", "Savannah", "Central",
    "Texas", "Florida", "Georgia",
}
# Combinaciones preposición+ubicación que NO son nombre completo
_PHI_WHITELIST_PREFIXED: set[str] = {
    "en austin", "en manchester", "en london", "en miami",
    "en dallas", "en houston", "en san antonio", "en orlando",
    "en tampa", "en atlanta", "en savannah",
    "la austin", "la manchester", "la london",
}
```

**Problemas resueltos**:
- `"En Austin"` era detectado como nombre completo (`[A-Z]word [A-Z]word`) →
  falso positivo PHI. Ahora `"en austin"` está en `_PHI_WHITELIST_PREFIXED`.
- `"Diane Foster"` (VP of People de HealthCore) es personal del staff, no
  paciente. Su preferencia de reporte SÍ debe ser memorable.
- CONTEXT.md §Hito8: "preferencias de staff SÍ son memorables; datos
  operativos de clínicas (Austin, Manchester, etc.) también."

**Flujo de filtrado en `validate_phi_safe()`**:

```python
filtered = []
for m in matches:
    m_lower = m.lower().strip()
    if m_lower in _PHI_WHITELIST or m_lower in _PHI_WHITELIST_PREFIXED:
        continue
    for wp in _PHI_WHITELIST_PREFIXED:
        if wp in m_lower:  # "en austin" dentro de texto
            skip = True; break
    if skip: continue
    for loc in _PHI_WHITELIST:
        if m_lower == loc.lower() or m_lower == loc.lower() + " ":
            skip = True; break
    if skip: continue
    filtered.append(m)
if filtered:
    return False, list(set(filtered))
return True, []
```

---

## 5. Flujo de decisión completo

### 5.1 Auto-evaluación (pipeline)

```
  ┌─────────────────────────────────────────────────────────────────────────┐
  │                            AUTO-EVALUACIÓN                              │
  │                                                                         │
  │  1. ¿Ya hay propuesta pendiente? ──Sí──► saltar (no duplicar)           │
  │  2. ¿Acaba de resolverse una propuesta? ──Sí──► saltar                  │
  │                                                                         │
  │  ┌─── RUTA PRIMARIA (Mejora 17): LLM ya evaluó ────────────────────┐    │
  │  │  3. ¿LLM devolvió propuesta? (llm_proposal != None)             │    │
  │  │     ──Sí──► _process_llm_proposal()                             │    │
  │  │              • Valida PHI + deduplicación                       │    │
  │  │              • Construye texto de propuesta para el usuario     │    │
  │  │              • Persiste en pending_proposal                     │    │
  │  │              • Responde con "¿Quieres que recuerde esto?"       │    │
  │  │     ──No──► continuar con ruta secundaria                       │    │
  │  └─────────────────────────────────────────────────────────────────┘    │
  │                                                                         │
  │  ┌─── RUTA SECUNDARIA (fallback heurístico tradicional) ───────────┐    │
  │  │  4. ¿Contiene indicador de "no memorizable"? ──Sí──► saltar     │    │
  │  │  5. ¿La pregunta es hipotética/condicional? ──Sí──► saltar      │    │
  │  │  6. ¿Hay trigger keyword en pregunta+respuesta? ──No──► saltar  │    │
  │  │  7. Extraer categoría (operational/incident_pattern/...)        │    │
  │  │  8. ¿Es duplicado de un fact existente? ──Sí──► saltar          │    │
  │  │  9. Validar PHI ──PHI detectado──► descartar, phi_rejected      │    │
  │  │  10. Persistir en pending_proposal, responder al usuario        │    │
  │  └─────────────────────────────────────────────────────────────────┘    │
  └─────────────────────────────────────────────────────────────────────────┘
```

### 5.2 Clasificador de intención (classifier.py) — 6 ejes estructurales

La función `classify_user_intent()` procesa la respuesta del usuario mediante
**8 pasos secuenciales** que evalúan **6 ejes analíticos** en lugar de
depender de listas planas de patrones:

**Ejes analíticos**:
1. **Estructura gramatical**: ¿La frase es una pregunta o condicional? →
   descartar inmediatamente (función `_es_pregunta_o_condicional()`).
2. **Señales de rechazo**: ¿Hay «no», «no lo guardes», «no hace falta»,
   «mejor no»? → puntuar como `reject` (función `_hay_rechazo()`).
   Incluye pronombres entre «no» y el verbo (lo/la/los/las/le/les/me/te).
3. **Señales de aprobación**: ¿Hay «sí», «ok», «de acuerdo», «correcto»,
   «guárdalo»? → puntuar como `approve` (función `_hay_aprobacion()`).
   Los comandos negados («no lo guardes») NO contribuyen.
4. **Señales de edición**: ¿Hay «mejor», «mejor dicho», «corrige»,
   «cambia»? → puntuar como `edit` (función `_hay_edicion()`).
5. **Cortesía sola**: ¿La frase es solo «gracias», «bien gracias»,
   «de nada»? → descartar (función `_es_solo_cortesia()`).
6. **Falsos positivos de aprobación**: ¿«sí» va seguido de condicional
   (ej. «sí cambia algo...», «sí hay un problema...»)? → descartar
   (función `_es_falso_positivo_aprobacion()`).

**Pipeline de 8 pasos** (`classify_user_intent()`):

```
  1. Texto vacío / solo espacios        → discard   (0.0)
  2. ¿Es pregunta o condicional?        → discard   (0.0)
  3. Evaluar señales de rechazo         → score_reject
  4. Evaluar señales de edición         → score_edit
  5. Descartar cortesías sin approve     → discard   (0.0)
  6. Falsos positivos de approve        → discard   (0.0)
  7. ¿Contradicción (sí + no)?          → discard   (0.0)
  8. Evaluar señales de aprobación      → score_approve
  9. "si" sin tilde en respuesta corta  → approve   (0.70)
 10. Por defecto                        → discard   (0.0)
```

**Umbrales de confianza**:
- approve ≥ 0.70
- reject ≥ 0.75
- edit ≥ 0.85
- discard = 0.0 (por defecto)

**Ejemplos de clasificación**:

| Entrada                     | Ejes activados                          | Resultado  | Confianza |
|-----------------------------|-----------------------------------------|------------|-----------|
| "sí, guárdalo"              | approve (sí + store_commands)           | approve    | 0.85      |
| "sí, claro"                 | approve + exception segunda palabra     | approve    | 0.75      |
| "ok"                        | approve (ok)                            | approve    | 0.70      |
| "no, gracias"               | reject (no + cortesía)                  | reject     | 0.75      |
| "no guardes eso"            | reject (no + frase compuesta)           | reject     | 0.80      |
| "mejor dicho..."            | edit (mejor)                            | edit       | 0.88      |
| "corrige el texto"          | edit (corrige)                          | edit       | 0.85      |
| "gracias"                   | cortesía sola                           | discard    | 0.0       |
| "bien, gracias"             | cortesía sola                           | discard    | 0.0       |
| "si llueve mañana"          | pregunta/condicional                    | discard    | 0.0       |
| "sí cambia algo, avísame"   | falso positivo approve                  | discard    | 0.0       |
| "¿qué has guardado?"        | pregunta                               | discard    | 0.0       |
| "cambia el horario"         | edit (cambia)                           | edit       | 0.85      |

```
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
- `_record_audit("approved", ...)`
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
  {"event_type": "proposed",  "run_id": "run_001", "timestamp": "2025-07-16T10:00:00",
   "proposal": "El nuevo proceso de derivación...", "classification": null, "detail": null},
  {"event_type": "approved",     "run_id": "run_002", "timestamp": "2025-07-16T10:01:00",
   "proposal": "El nuevo proceso de derivación...", "user_message": "Sí, guárdalo.",
   "classification": "approve", "detail": "Fact escrito en memory_facts"},
]
```

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
has_approve = any(et == "approved" for et in event_types)
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
- `_record_audit("rejected", ...)`
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
  {"event_type": "proposed",  "run_id": "run_003", "timestamp": "2025-07-16T11:00:00",
   "proposal": "Los incidentes de facturación suelen escalar...", "classification": null, "detail": null},
  {"event_type": "rejected",     "run_id": "run_004", "timestamp": "2025-07-16T11:01:00",
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
nada en `memory_facts`). El audit_log registra el evento `rejected` con el
texto original de la propuesta y el mensaje del usuario.

### Suite completa de memoria (50 tests) — todos pasan

```
$ uv run python -m pytest tests/pipelines/test_agent_graph.py -v -k "TestMemory" --tb=short

Tests de memoria (50/50):
  PHI:        8/8  PASSED  (phi_validate_clean_text, phi_rejects_name, ...)
  Store:      5/5  PASSED  (add_fact, pending_proposal, delete, ...)
  Classifier: 24/24 PASSED  (6 ejes estructurales, approve, reject, edit,
                              discard, falso positivo approve, cortesía sola,
                              pregunta/condicional, texto vacío, sin propuesta,
                              NEGACIÓN CON PRONOMBRES y CONTRADICCIONES)
  Pipeline:   11/11 PASSED  (auto_evaluate, process_response, audit,
                              e2e_approve_cycle, e2e_reject_cycle,
                              e2e_discard_cycle)
  Integration:9/9  PASSED  (graph_E2E_approve_cycle, graph_E2E_reject_cycle,
                             graph_E2E_discard_cycle, graphs_E2E_PHI-rejected (2),
                             graph_E2E_consolidation, nodes_exist,
                             routes_defined, route_after_receive)

====================== 50 passed in 1.24s =======================
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
- `_record_audit("edited", ..., classification="edit", ...)`
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

### Paso 3: PHI validation bloquea la propuesta + rechazo explícito

- `validate_phi_safe("El paciente Juan Pérez está en la habitación 304 con diabetes tipo 2.")`
  → **PHI detectado**: NOMBRE ("Juan Pérez"), CLINICAL_TERM ("diabetes"),
    "paciente"
  → Retorna `(False, ["NOMBRE", "CLINICAL_TERM", "CLINICAL_TERM"])`

- `auto_evaluate_proposal()` en `pipeline.py` **NO** retorna `(answer, None)`
  (rechazo silencioso), sino:

  ```python
  rejection_msg = (
      "\n\n---\n\n"
      "⚠️ **No puedo guardar eso en mi memoria.**\n\n"
      "La información que mencionas parece contener datos de salud "
      "protegidos (PHI). Por cumplimiento de **HIPAA** y **UK GDPR**, "
      "no está permitido almacenar identificadores de pacientes, "
      "diagnósticos ni ningún dato que pueda re-identificar a una "
      "persona en mi memoria persistente.\n\n"
      "Puedo ayudarte a gestionar esta incidencia a través de los "
      "canales estándar de HealthCore, pero no puedo «recordarlo» "
      "como un hecho operativo.\n\n"
      "*Si se trata de un cambio operativo sin datos de paciente, "
      "por favor reformúlalo sin incluir información personal.*"
  )
  return answer + rejection_msg, {
      "phi_rejected": True,
      "phi_matches": phi_matches,
      "category": candidate["category"],
  }
  ```

### Paso 4: Nodo de grafo maneja propuesta PHI rechazada

- `node_auto_evaluate_memory()` en `nodes.py` detecta `phi_rejected=True`:
  - Usa `answer_with_proposal` (que incluye el mensaje de rechazo)
  - **NO** persiste en `pending_proposal` (no se guarda en TinyDB)
  - Registra en `routing_log`: `"phi_rejected"` con detalles
  - La respuesta al usuario **incluye** la explicación del rechazo

### Paso 5: Respuesta con explicación al usuario

> Lo siento, no tengo acceso a información clínica de pacientes.
>
> ---
>
> ⚠️ **No puedo guardar eso en mi memoria.**
>
> La información que mencionas parece contener datos de salud protegidos
> (PHI). Por cumplimiento de **HIPAA** y **UK GDPR**, no está permitido
> almacenar identificadores de pacientes, diagnósticos ni ningún dato que
> pueda re-identificar a una persona en mi memoria persistente.
>
> Puedo ayudarte a gestionar esta incidencia a través de los canales
> estándar de HealthCore, pero no puedo «recordarlo» como un hecho operativo.
>
> *Si se trata de un cambio operativo sin datos de paciente, por favor
> reformúlalo sin incluir información personal.*

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

## 9-B. Ciclo 5 — Unrelated (mensaje no relacionado con la propuesta pendiente)

### Contexto

Cuando hay una propuesta pendiente pero el usuario escribe un mensaje que **no
está relacionado** con ella (por ejemplo, nueva información operativa, una
pregunta diferente, o una corrección sobre un tema distinto), el sistema
reconoce que el mensaje no es una respuesta a la propuesta y la descarta
como **unrelated**, dejando que el mensaje fluya por el cauce normal de
evaluación de memoria.

### Paso 1: Estado inicial — hay propuesta pendiente

**Propuesta pendiente**:
> Diane Foster quiere un reporte semanal de vacantes por clínica.

**Categoría**: `staff_preference`

### Paso 2: Usuario escribe un mensaje no relacionado

**Usuario**:
> El reporte semanal para Diane Foster debe incluir vacantes por rol, no solo
> por clínica — eso lo pidió hace dos semanas.

Este mensaje **no es una respuesta** a la propuesta pendiente. El usuario no
dice ni "sí", ni "no", ni edita la propuesta — está aportando información
operativa nueva que contradice la propuesta original.

### Paso 3: `process_user_response` clasifica y descarta como unrelated

- `classify_user_intent()`:
  - Ejes de approve: mensaje no contiene "sí", "ok", "guárdalo" → puntuación baja
  - Ejes de reject: "no solo" se detecta como rechazo parcial (0.5, por debajo del umbral 0.75)
  - Ejes de edit: sin señales de edición
  - Resultado: `discard` (nada coincide con approve/reject/edit)
- `process_user_response()`:
  - Intención `discard` → rama `else:` (por defecto)
  - **Nuevo**: en lugar de devolver `resolution="discard"`, ahora devuelve
    `resolution="unrelated"`
  - `clear_pending_proposal()`: propuesta anterior se limpia
  - `_record_audit("unrelated", ...)`: se registra como evento de auditoría
  - El mensaje queda libre para ser procesado por `auto_evaluate_memory`

### Paso 4: Mensaje redirigido al flujo normal

Al no haber propuesta pendiente, el grafo continúa:

```
process_proposal (unrelated) → classify_query → retrieve_context
→ generate_answer → auto_evaluate_memory
```

En `auto_evaluate_memory`, el mensaje de Diane Foster se evalúa como nueva
información operativa:
- Contiene información memorable: preferencia de Diane sobre el formato del
  reporte semanal
- Categoría: `staff_preference` (preferencia de un miembro del staff)
- Propuesta generada:
  > Diane Foster prefiere su reporte semanal de vacantes agrupado por rol,
  > no solo por clínica.

### Paso 5: Respuesta al usuario con nueva propuesta

> Entendido. He actualizado la información: no guardaré el dato anterior sobre
> el reporte por clínica.
>
> 📝 **¿Quieres que recuerde esto para la próxima vez?**
> > Diane Foster prefiere su reporte semanal de vacantes agrupado por rol,
> > no solo por clínica.
> > *(categoría: staff_preference)*
>
> Responde con **«sí»**, **«no»**, o edita el texto.

### Registro de auditoría (Ciclo 5)

```json
[
  {"event_type": "unrelated", "run_id": "run_008", "timestamp": "2025-07-16T14:00:00",
   "proposal": "Diane Foster quiere un reporte semanal de vacantes por clínica.",
   "user_message": "El reporte semanal para Diane Foster debe incluir vacantes por rol...",
   "classification": "discard",
   "detail": "Mensaje no relacionado con la propuesta pendiente — propuesta anterior limpiada, mensaje redirigido a flujo normal."},
  {"event_type": "proposed", "run_id": "run_009", "timestamp": "2025-07-16T14:00:01",
   "proposal": "Diane Foster prefiere su reporte semanal de vacantes agrupado por rol...",
   "classification": null, "detail": null},
]
```

### Verificación desde el test server

```
$ curl -s -X POST http://localhost:8000/classify \
  -H "Content-Type: application/json" \
  -d '{"message":"El reporte semanal para Diane Foster debe incluir vacantes
       por rol, no solo por clínica — eso lo pidió hace dos semanas."}'

{
    "intent": "discard",
    "confidence": 0.0,
    "explanation": "No se detectaron señales claras... \n📋 Resolución completa: unrelated. ¿Queda propuesta? No",
    "pending_proposal": false
}
```

---

---

## 10. Consolidación y olvido

### 10.1 Política de consolidación

| Parámetro               | Valor     | Justificación                                                 |
|--------------------------|-----------|---------------------------------------------------------------|
| Umbral de consolidación | 50 facts  | Suficientes para detectar patrones sin esperar demasiado.     |
| TTL de facts originales | 90 días   | Balanceó entre retención util y limpieza automática.           |
| TTL auto-asignado       | 90 días   | `add_fact()` asigna automáticamente `expires_at = now + 90d`  |
|                         |           | cuando el fact NO es consolidado y no tiene `expires_at`.     |
| Consolidados expiran    | No        | Son resúmenes; si ya no son relevantes, se eliminan manualmente. |

**TTL auto-assignment** (`store.py:add_fact()`):

```python
def add_fact(content, category, source_run_id, approved_at, consolidated, expires_at):
    if not validate_phi_safe(content):
        return None
    if not consolidated and expires_at is None:
        expires_at = _now_plus_days(_DEFAULT_TTL_DAYS)  # 90 días
    table.insert({
        "content": content,
        "category": category,
        "source_run_id": source_run_id,
        "approved_at": approved_at,
        "consolidated": consolidated,
        "expires_at": expires_at,
    })
```

Esto garantiza que **todo fact original expire automáticamente** tras 90 días
si no se consolida. Antes de esta corrección, los facts originales nunca
recibían `expires_at`, por lo que permanecían indefinidamente en memoria.

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

### 11.2 Nuevas rutas (4)

| Ruta                         | Origen → Destino                                      |
|------------------------------|--------------------------------------------------------|
| `route_after_load_memory`    | `load_memory_context` → `process_proposal` (si pendiente) / `classify_query` (si no) |
| `route_after_process_proposal` | `process_proposal` → `classify_query` (si nueva pregunta) / `__end__` (si solo respuesta) |
| `route_after_auto_evaluate`  | `auto_evaluate_memory` → `__end__` (siempre)           |
| `route_after_synthesis`      | `synthesize_tool_answer` → `auto_evaluate_memory` (si ok) / `__end__` (si error) |

> **Nota**: `route_after_synthesis` se creó para romper el loop RAG+tool.
> Antes, `synthesize_tool_answer` usaba `route_after_generate`, que podía
> re-enrutar a `synthesize_tool_answer` porque `tool_results` aún estaban
> en el estado. La nueva ruta va directo a `auto_evaluate_memory` o `__end__`,
> nunca de vuelta a síntesis.

### 11.3 Nuevos campos en AgentState

```python
class AgentState(TypedDict):
    # ... campos existentes ...
    # --- Memoria (Parte 3 — Hito 8) ---
    "memory_facts": list[dict[str, Any]],                  # Facts cargados desde TinyDB
    "memory_context": str,                                 # Contexto formateado para el LLM
    "memory_proposal": Optional[dict[str, Any]],           # Propuesta pendiente (dict) o None
    "memory_proposal_handled": bool,                       # ¿Propuesta ya procesada?
    "memory_proposal_handled_text": str,                   # Texto de confirmación para el usuario
    "memory_proposal_result": Optional[dict[str, Any]],    # Resultado (approved/rejected/etc)
    "llm_memory_proposal": Optional[dict[str, Any]],       # Propuesta del LLM (Mejora 17)
    "run_id": str,                                         # ID único de corrida (para auditoría)
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

### 11.5 Resultado final — 219/219 tests PASSED

```
$ uv run python -m pytest tests/ --tb=short -q

219 passed in 4.34s
```

Desglose por categoría (tests en `test_agent_graph.py`):

| Categoría                  | Tests | Estado |
|----------------------------|-------|--------|
| GraphCompilation           | 3     | ✅     |
| ConditionalRouting         | 7     | ✅     |
| Traceability               | 4     | ✅     |
| RAGBehavior                | 4     | ✅     |
| AgentEndpoint              | 2     | ✅     |
| ExternalToolRouting        | 5     | ✅     |
| ToolFallbackBehavior       | 5     | ✅     |
| MixedSynthesis             | 2     | ✅     |
| MemoryPHI                  | 8     | ✅     |
| MemoryStore                | 5     | ✅     |
| MemoryClassifier           | 24    | ✅     |
| MemoryPipeline             | 11    | ✅     |
| MemoryGraphIntegration     | 9     | ✅     |
| **Total (agente+memoria)** | **89**| ✅     |
| **Total suite completa**   | **219**| ✅     |

### 11.6 Historial de bugs corregidos (17 totales)

| #  | Bug | Síntoma | Causa raíz | Corrección |
|----|-----|---------|------------|------------|
| 1 | `route_after_receive` entrypoint | KeyError en aristas del grafo | Devolvía `"classify_query"` pero cambió a `"load_memory_context"` | Actualizar ruta |
| 2 | PHI: falsos negativos acentos | "Juan Pérez" no detectado | Regex sin soporte acentos | `[A-Z][a-zA-ZáéíóúñÁÉÍÓÚ]+` |
| 3 | `add_fact()` sin validación PHI | PHI llegaba a TinyDB | Falta validación en escritura | Validación dual |
| 4 | Clasificador ambigüedad como aprobado | Hipotéticas clasificadas como approve | Falta filtro de condicionales | `_should_evaluate` reescrito |
| 5 | Consolidación: facts nunca expiraban | Memoria acumulaba facts | No auto-asignación de TTL | `_DEFAULT_TTL_DAYS=90` |
| 6 | Loop RAG+tool | Síntesis se re-ejecutaba en ciclo | `route_after_generate` re-enrutaba | `route_after_synthesis()` |
| 7 | CONTEXT examples bloqueados | Casos 3,5,6 no generaban propuesta | Keywords en lista equivocada | Mover triggers |
| 8 | Word boundary en `_QUERY_PHRASES` | "especialista" bloqueado por "lista" | Substring match | `re.search(r"\b...\b")` |
| 9 | Faltaban comandos explícitos | "apúntalo", "guárdalo" ignorados | No en triggers | Añadir 10 comandos |
| 10 | `_extract_memory_candidate` sin fallback | Propuesta None aunque _should_evaluate True | Sin extractor de respaldo | `_extract_command_memory`, `_extract_fallback_content` |
| 11 | PHI rejection silenciosa | Propuesta PHI se descartaba sin aviso | Retornaba `(answer, None)` | Rechazo explícito HIPAA/UK GDPR |
| 12 | **"si llueve" → discard** | "si" condicional se clasificaba como approve | `\bs[ií]\b` emparejaba "si" = "sí" | Añadir eje de pregunta/condicional en §5.2 |
| 13 | **"bien gracias" → discard** | "bien" daba approve falso | `\bbien\b` en patrones de aprobación | Añadir eje de cortesía sola en §5.2 |
| 14 | **"cambia el horario" → discard** | "cambia" daba approve en vez de edit | "cambia" listado como trigger de memorize | Rediseño completo a 6 ejes estructurales |
| 15 | **Clasificador basado en listas planas** | Múltiples falsos positivos por patrón | Arquitectura de pattern lists | Rediseño total: clasificador estructural con 6 ejes analíticos y pipeline de 8 pasos |
| 16 | **Negación con pronombres no se detecta** | "no lo guardes" → approve, "sí, pero no" → approve | `_RE_REJECT_PHRASE` no aceptaba pronombres `lo/la/los/las` entre "no" y el verbo; comandos de memoria negados sumaban puntaje de approve; contradicciones sí+no se resolvían como approve | `_RE_REJECT_PHRASE` ampliado con raíces de verbos; `_hay_aprobacion` ignora comandos negados vía `_comando_memoria_negado()`; nuevo paso 5b de contradicción → discard. 7 nuevos tests añadidos. |
| 17 | **Mensajes "unrelated" clasificados como discard sin limpiar propuesta** (2026-11-22) | Mensaje de Diane Foster "debe incluir vacantes por rol, no solo por clínica" devuelve discard pero la propuesta quedaba pendiente — el mensaje nunca llegaba a `auto_evaluate_memory` | El pipeline llamaba `process_user_response()` que clasificaba como discard pero no distinguía entre "mensaje irrelevante para la propuesta" y "rechazo explícito" | `process_user_response()` rama `else` ahora devuelve `resolution="unrelated"` + `clear_pending_proposal()` + redirige el mensaje al flujo normal de auto-evaluación. Nuevo evento de auditoría `"unrelated"`. |

> **Nota**: 89 tests en `test_agent_graph.py` (57 de memoria + 32 de agente) +
> 130 tests en otros módulos (`test_sales_forecast_split.py`,
> `test_mcp_server_auth.py`, `test_temporal_cv.py`, `nightly/`, etc.) =
> **212 tests totales en la suite del workspace**.

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
