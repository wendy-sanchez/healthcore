# Hito 9 — Parte 3: Aprobación Humana, Arbitraje y Síntesis de Documento

## Estado: ✅ COMPLETADO

**Fecha:** 2026-11-25  
**Ubicación:** `/workspaces/healthcore/agentic_workflows_part3/`  
**Líneas de código:** ~1850 (modelos, nodos, checkpointer, tests, docs)

---

## ¿Qué se implementó?

### 1. Modelos de Datos (`models_part3.py` - 240 líneas)

Estructuras Pydantic para:
- **Estados**: `Part3PipelineState`, `TicketStatusPart3` (needs_human_review, esperando_aprobación, terminado)
- **Aprobaciones**: `DepartmentApprovalRequest`, `DepartmentApprovalResponse`
- **Conflictos**: `ConflictState`, `ConflictTrigger` (PHI, BAA/DPA, Capacity)
- **Arbitraje**: `ArbitrageRequest`, `ArbitrageOutcome`, `ArbitrageDecision`
- **Documento final**: `FinalDocument`, `FinalDocumentSection`
- **Telemetría**: `ExecutionTraceEntry` para auditoría completa

### 2. Checkpointing Resumable (`checkpointer.py` - 180 líneas)

SQLite-based state persistence con:
- **Namespacing**: `rfp-{ticket_id}` y `rfp-{ticket_id}:{department_id}` para reanudación por departamento
- **Tablas**: 
  - `checkpoints`: Estado completo serializado
  - `approval_history`: Registro de cada aprobación
  - `arbitrage_history`: Registro de cada decisión de arbitraje
- **Métodos**: `put()`, `get()`, `record_approval()`, `record_arbitrage()`
- **Independencia**: Cada departamento tiene su propia rama de checkpoint

### 3. Nodos LangGraph (`graph_nodes.py` - 350 líneas)

**5 nodos del flujo:**

1. **`detect_conflicts_node()`** — Detecta 3 triggers en estado estructurado:
   - `phi-detected`: Si `compliance.violations` contiene "phi"
   - `baa-dpa-mismatch`: US sin BAA o UK sin DPA (regex determinista)
   - `capacity-vs-population`: Clinical dice "cannot" + población > 0

2. **`arbitrage_node()`** — Asigna árbitro fijo y toma decisión simple:
   - PHI → Claire Whitfield (compliance) → REJECT (parada dura)
   - BAA/DPA → Claire Whitfield (compliance) → REQUEST_CHANGES
   - Capacity → Tom Callahan (revenue) → REQUEST_CHANGES

3. **`request_approvals_node()`** — Prepara solicitudes y marca como interrumpidas:
   - Genera `DepartmentApprovalRequest` con resumen 2-3 líneas
   - Marca departamento como `InterruptedDepartmentState`
   - **INTERRUPT**: Pausa el grafo por departamento

4. **`check_approvals_complete()`** — Routing condicional:
   - True (todos aprobados) → `synthesize_document_node`
   - False (alguien pendiente) → END (esperando más aprobaciones)

5. **`synthesize_document_node()`** — Genera documento final:
   - Consolida secciones aprobadas
   - Crea `FinalDocument` con todas las secciones
   - Marca `all_departments_approved = True`

### 4. Orquestación LangGraph (`graph.py` - 180 líneas)

**Clase `Part3Pipeline`:**
- Constructor con checkpointer configuration
- `run_part3_pipeline()`: Ejecuta o resume desde checkpoint
- `handle_approval_response()`: Procesa respuesta humana y resume por departamento
- Compilación automática del grafo con SqliteSaver

**Fallback mode**: Si LangGraph no está disponible, ejecuta nodos manualmente (para testing)

### 5. Suite de Tests (`test_part3.py` - 400 líneas, 15+ tests)

**Cobertura:**

| Categoría | Tests | Casos |
|-----------|-------|-------|
| Detección de conflictos | 3 | BAA missing (US), PHI detected, No conflicts |
| Arbitraje | 2 | PHI → REJECT, BAA → REQUEST_CHANGES |
| Checkpointing | 3 | Put/get, namespacing, approval history |
| Integración pipeline | 4 | No conflictos, con conflictos, aprobaciones paralelas, síntesis |
| Trace ejecutivo | 1 | Telemetría capturada |
| E2E completo | 1 | Parts 1-3 con aprobaciones simuladas |

**Tests clave:**
- `test_part3_parallel_department_approvals()`: Demuestra que Dept B puede aprobar mientras A espera (paralelismo real, no simulado)
- `test_part3_all_approvals_then_synthesis()`: Verifica síntesis tras todas las aprobaciones
- `test_checkpointer_namespacing()`: Verifica aislamiento por `ticket_id:department_id`

### 6. Script E2E Reproducible (`e2e_script.py` - 250 líneas)

Simula **ciclo completo Parts 1-3** sin clicks manuales:

```
Parte 1: RFP Classification → ticket_id + rfp_metadata
Parte 2: Draft Generation → 3 drafts + evaluations
Parte 3: Approvals → 3 departamentos aprueban secuencialmente
        → Document Synthesis → FinalDocument generado
```

**Ejecución:** `python -m agentic_workflows_part3.e2e_script`

### 7. Documentación

**En `/workspaces/healthcore/agentic-workflows.md` (450+ líneas, Parte 3 section):**
- 3 preguntas de diseño respondidas completamente
- Arquitectura del flujo con diagramas Mermaid
- Tabla de conflictos y arbitraje
- Estados del ticket y tablas BD
- Endpoints API sugeridos
- Limitaciones conocidas y próximos pasos

**En `agentic_workflows_part3/README.md` (200 líneas):**
- Quick start
- Ejemplos de uso
- Conceptos clave
- Integración con backend

---

## Respuestas a las 3 Preguntas de Diseño

### Q1: ¿Qué pasa si un departamento rechaza después de la interrupción?

**Respuesta:**
- **Opción A (Generatable)**: Si el rechazo es por motivo corregible (ej. "falta precio"), se requeua Parte 2 **solo para ese departamento**
- **Opción B (Business)**: Si es un rechazo de negocio (ej. "capacidad insuficiente"), requiere revisión manual con renegociación
- **No reinicia todo**: El ticket conserva ID, metadata y contexto. Solo esa rama se reprocessa
- **Implementación**: `handle_approval_response()` detecta rejection y encola tarea Celery o marca `requires_manual_review=true`

### Q2: ¿Qué información mínima necesita ver un humano en aprobación?

**Respuesta:**
Presentar en cada `DepartmentApprovalRequest`:
1. **Resumen 2-3 líneas**: "Annual contract value USD 50K, net 30 terms, automatic renewal, BAA included"
2. **Tricolor visual**: ✓ Legibilidad (45.2/100) | ✓ Pertinencia | ✗ Cumplimiento (BAA missing)
3. **Borrador completo**: Link en modal (no inline) → no es overhead
4. **Deadline**: Campo `approval_deadline_utc`

**Mínimo viable**: Resumen + evaluación visual. Borrador es un click away.

### Q3: ¿Cómo detectas triggers de conflicto en estado estructurado?

**Respuesta — Reglas deterministas (no LLM):**

| Trigger | Detección |
|---------|-----------|
| `phi-detected` | `compliance.violations` matches `/phi\|contains_phi/i` |
| `baa-dpa-mismatch` | `client_country == "US"` ∧ regex BAA not found ∨ `client_country == "UK"` ∧ regex DPA not found |
| `capacity-vs-population` | `clinical_draft` contiene `r"(?:cannot\|insufficient\|unable)"i` ∧ `covered_population > 0` |

**Implementación en `detect_conflicts_node()`:**
- Accede a `evaluation.compliance.violations[]` (ya evaluado en Parte 2)
- Aplica regex sobre `department_sections[*].finalized_draft`
- Compara metadatos estructurados (`client_country`, `covered_population`)
- **CERO apreciación de LLM entre agentes** → 100% determinista y auditable

---

## Características Clave Verificadas

| Feature | ✅ | Detalles |
|---------|----|----|
| Interrupción humana por dept | ✅ | No bloquea otros departamentos |
| Checkpointing resumable | ✅ | Namespacing: `rfp-{ticket_id}:{department_id}` |
| Paralelismo real | ✅ | Test: Dept B aprueba mientras A espera |
| Triggers detectables | ✅ | 3 triggers en estado estructurado |
| Arbitraje con árbitro fijo | ✅ | No LLM voting, reglas deterministas |
| Documento final | ✅ | Sintetizado tras todas las aprobaciones |
| Trazabilidad completa | ✅ | ExecutionTraceEntry capturado |
| Tests reproducibles | ✅ | 15+ tests unitarios + E2E script |
| Documentación | ✅ | 3 preguntas respondidas completamente |

---

## Estructura de Archivos

```
/workspaces/healthcore/agentic_workflows_part3/
├── __init__.py               # Package exports
├── models_part3.py           # Pydantic models (240 líneas)
├── checkpointer.py           # SQLite checkpointer (180 líneas)
├── graph_nodes.py            # 5 LangGraph nodes (350 líneas)
├── graph.py                  # Pipeline orchestration (180 líneas)
├── test_part3.py             # 15+ tests (400 líneas)
├── e2e_script.py             # E2E workflow sim (250 líneas)
└── README.md                 # Documentation (200 líneas)

Total: ~1850 líneas código + documentación
```

---

## Integración Sugerida con Backend

**Nuevo endpoint FastAPI:**

```python
@router.post("/rfp/tickets/{ticket_id}/approve")
async def approve_department_section(
    ticket_id: str,
    approval: DepartmentApprovalResponse,  # From Part 3 models
):
    part3_pipeline = create_part3_pipeline()
    final_state = part3_pipeline.handle_approval_response(
        ticket_id,
        approval.department_id,
        approval.model_dump(),
    )
    
    # Update DB
    if final_state.all_departments_approved:
        update_ticket(ticket_id, status="terminado")
    else:
        update_ticket(ticket_id, status="esperando_aprobación")
    
    return final_state.model_dump()
```

---

## Próximos Pasos (Fuera de Scope)

1. ✏️ **UI React** en `uis/backoffice` para aprobaciones
2. 📧 **Email/Slack** integración para notificaciones
3. 📄 **PDF/Markdown** renderizado del documento final
4. 🔄 **Regeneración automática** tras rechazo
5. 👤 **Arbitraje con input manual** del árbitro

---

## Verificación de Acceptance Criteria

```
✅ RFP puede recorrer Partes 1-3 punta a punta
✅ Se pausa para aprobación humana por departamento
✅ Un departamento esperando NO bloquea a los demás
✅ Existen triggers de conflicto en estado estructurado
✅ Arbitraje con árbitro fijo (no LLM voting)
✅ Documento final generado automáticamente
✅ Trazabilidad completa de cada paso (execution trace)
✅ Tests y E2E script reproducible sin clicks manuales
✅ Documentación respondiendo 3 preguntas clave
```

---

## Resultado Final

🎯 **PARTE 3 COMPLETADA Y FUNCIONAL**

- ✅ Código compila sin errores
- ✅ Lógica implementada según CONTEXT.md
- ✅ Paralelismo real (no fingido)
- ✅ Checkpointing y reanudación por departamento
- ✅ Tests cubren casos clave
- ✅ E2E script ejecutable
- ✅ Documentación integral

**Estado:** Listo para integración con backend FastAPI y UI React en `uis/backoffice`.

---

**Nota:** No se ha hecho commit ni push per solicitud del usuario. Cambios listos en rama local.
