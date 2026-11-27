"""Orquestador del ciclo generador-evaluador (Parte 2).

Coordina:
1. Generacion de borrador por departamento
2. Evaluacion paralela (3 evaluadores)
3. Iteracion si falla (max. max_iterations)
4. Si se agota el limite -> needs_human_review, conserva ultimo borrador
5. Handoff a Parte 3

Persistencia en tiempo real:
  Cada departamento persiste su progreso en PostgreSQL (DepartmentSectionDB)
  ANTES de continuar con la siguiente fase, para que el ticket refleje
  el avance en tiempo real.

Flujo de estados del ticket:
  analisis_completo -> generando_borrador -> en_evaluacion -> (needs_human_review | analisis_completo)

Flujo de generation_status por departamento:
  null -> generando -> draft_complete -> evaluando -> completado
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from data.pipelines.rfp_intake.generators import generate_draft
from data.pipelines.rfp_intake.evaluators import evaluate_section
from data.pipelines.rfp_intake.models_part2 import (
    Part2PipelineState,
    Part2DepartmentState,
    Part2DepartmentOutput,
    Part2Handoff,
    DraftContent,
)
from data.pipelines.rfp_intake.models import (
    TicketStatus,
    RfpMetadata,
    DepartmentSection,
    HandoffPayload,
)

logger = logging.getLogger(__name__)


# -- Helpers de persistencia en PostgreSQL ----------------------------


def _persist_generation_started(engine, ticket_id, department_id, iteration):
    """Persistir 'generando' en DepartmentSectionDB ANTES de generar."""
    from sqlmodel import Session, select
    from services.api.models_rfp import DepartmentSectionDB, TicketDB

    with Session(engine) as session:
        stmt = select(DepartmentSectionDB).where(
            DepartmentSectionDB.ticket_id == ticket_id,
            DepartmentSectionDB.department_id == department_id,
        )
        dept_db = session.exec(stmt).first()
        if dept_db:
            dept_db.generation_status = "generando"
            dept_db.generation_iteration = iteration
            session.add(dept_db)
        stmt_t = select(TicketDB).where(TicketDB.ticket_id == ticket_id)
        ticket = session.exec(stmt_t).first()
        if ticket:
            ticket.status = TicketStatus.GENERANDO_BORRADOR.value
            session.add(ticket)
        session.commit()


def _persist_draft_complete(engine, ticket_id, department_id, draft_content):
    """Persistir borrador INMEDIATAMENTE despues de generar, antes de evaluar."""
    from sqlmodel import Session, select
    from services.api.models_rfp import DepartmentSectionDB

    with Session(engine) as session:
        stmt = select(DepartmentSectionDB).where(
            DepartmentSectionDB.ticket_id == ticket_id,
            DepartmentSectionDB.department_id == department_id,
        )
        dept_db = session.exec(stmt).first()
        if dept_db:
            dept_db.draft_content = draft_content
            dept_db.generation_status = "draft_complete"
            session.add(dept_db)
        session.commit()


def _persist_evaluation_started(engine, ticket_id, department_id):
    """Persistir 'evaluando' en DepartmentSectionDB ANTES de evaluar, y ticket en en_evaluacion."""
    from sqlmodel import Session, select
    from services.api.models_rfp import DepartmentSectionDB, TicketDB

    with Session(engine) as session:
        stmt = select(DepartmentSectionDB).where(
            DepartmentSectionDB.ticket_id == ticket_id,
            DepartmentSectionDB.department_id == department_id,
        )
        dept_db = session.exec(stmt).first()
        if dept_db:
            dept_db.generation_status = "evaluando"
            session.add(dept_db)
        stmt_t = select(TicketDB).where(TicketDB.ticket_id == ticket_id)
        ticket = session.exec(stmt_t).first()
        if ticket:
            ticket.status = TicketStatus.EN_EVALUACION.value
            session.add(ticket)
        session.commit()


def _persist_evaluation_complete(engine, ticket_id, department_id, evaluation_dict, passed):
    """Persistir evaluacion y estado completado INMEDIATAMENTE despues de evaluar."""
    from sqlmodel import Session, select
    from services.api.models_rfp import DepartmentSectionDB

    with Session(engine) as session:
        stmt = select(DepartmentSectionDB).where(
            DepartmentSectionDB.ticket_id == ticket_id,
            DepartmentSectionDB.department_id == department_id,
        )
        dept_db = session.exec(stmt).first()
        if dept_db:
            dept_db.generation_status = "completado"
            dept_db.evaluation_results = evaluation_dict
            session.add(dept_db)
        session.commit()


# -- Pipeline principal ------------------------------------------------


async def run_part2_pipeline(
    handoff: HandoffPayload,
    max_iterations: int = 3,
    engine: Optional[Any] = None,
) -> Part2Handoff:
    """Ejecuta el ciclo generador-evaluador completo para todos los departamentos.

    Args:
        handoff: Payload de la Parte 1.
        max_iterations: Limite de iteraciones por departamento.
        engine: SQLModel engine de PostgreSQL. Si se provee, persiste estados
                intermedios en tiempo real en DepartmentSectionDB y TicketDB.

    Returns:
        Part2Handoff listo para la Parte 3.
    """
    logger.info("Parte 2 iniciada para ticket %s - %d departamento(s)",
                handoff.ticket_id, len(handoff.departments))

    metadata = RfpMetadata.model_validate(handoff.rfp_metadata) \
        if isinstance(handoff.rfp_metadata, dict) else handoff.rfp_metadata

    pipeline_state = Part2PipelineState(
        ticket_id=handoff.ticket_id,
        rfp_metadata=metadata,
        synthesis=handoff.synthesis,
        departments=[d.model_dump() if hasattr(d, 'model_dump') else d for d in handoff.departments],
    )

    # Crear estado por departamento
    for dept in handoff.departments:
        dept_id = dept.department_id if hasattr(dept, 'department_id') else dept.get("department_id", "")
        pipeline_state.department_states.append(
            Part2DepartmentState(
                department_id=dept_id,
                max_iterations=max_iterations,
            )
        )

    # Procesar departamentos en paralelo
    dept_tasks = [
        _process_department(dept_state, metadata, handoff.departments, engine, handoff.ticket_id)
        for dept_state in pipeline_state.department_states
    ]
    await asyncio.gather(*dept_tasks)

    for dept_state in pipeline_state.department_states:
        if not dept_state.passed:
            logger.warning(
                "Departamento %s no paso evaluacion tras %d iteraciones - marcado needs_human_review",
                dept_state.department_id, dept_state.iteration
            )

    # Construir output
    dept_outputs = []
    for dept_state in pipeline_state.department_states:
        draft = dept_state.draft or dept_state.best_draft
        output = Part2DepartmentOutput(
            department_id=dept_state.department_id,
            finalized_draft=draft.content if draft else "",
            evaluation=draft.evaluation if draft else None,
            needs_human_review=dept_state.needs_human_review,
            iteration_count=dept_state.iteration,
        )
        dept_outputs.append(output)

    any_human_review = any(o.needs_human_review for o in dept_outputs)
    overall_status = "needs_human_review" if any_human_review else "analisis_completo"

    pipeline_state.overall_status = overall_status
    pipeline_state.completed = True

    handoff_payload = Part2Handoff(
        ticket_id=handoff.ticket_id,
        rfp_metadata=metadata,
        departments=dept_outputs,
        synthesis=handoff.synthesis,
        overall_status=overall_status,
    )

    logger.info("Parte 2 completada para ticket %s - overall_status=%s",
                handoff.ticket_id, overall_status)
    return handoff_payload


async def _process_department(
    dept_state: Part2DepartmentState,
    metadata: RfpMetadata,
    departments: list[Any],
    engine: Optional[Any] = None,
    ticket_id: Optional[str] = None,
) -> None:
    """Procesa un departamento: genera, evalua, itera.

    Persiste cada fase en PostgreSQL (via engine) INMEDIATAMENTE:
      1. generation_status='generando' -> ANTES de generate_draft()
      2. draft_content + generation_status='draft_complete' -> DESPUES de generar
      3. generation_status='evaluando' -> ANTES de evaluate_section()
      4. evaluation_results + generation_status='completado' -> DESPUES de evaluar

    Asi, cualquier consulta concurrente a la BD ve el progreso en tiempo real.
    """
    dept_id = dept_state.department_id

    # Extraer key_aspects del departamento (desde el handoff)
    key_aspects = _extract_key_aspects(dept_id, departments)

    while dept_state.iteration < dept_state.max_iterations and not dept_state.passed:
        dept_state.iteration += 1
        logger.info("Departamento %s - iteracion %d/%d",
                    dept_id, dept_state.iteration, dept_state.max_iterations)

        # -- FASE 1: Generar borrador ----------------------------------
        # Persistir "generando" ANTES de llamar al LLM
        if engine and ticket_id:
            _persist_generation_started(engine, ticket_id, dept_id, dept_state.iteration)

        previous_feedback = None
        if dept_state.draft and dept_state.draft.evaluation:
            previous_feedback = dept_state.draft.evaluation.feedback_for_generator

        content = await generate_draft(
            department_id=dept_id,
            rfp_metadata=metadata,
            key_aspects=key_aspects,
            previous_feedback=previous_feedback,
        )

        # Persistir borrador INMEDIATAMENTE despues de generar (antes de evaluar)
        if engine and ticket_id:
            _persist_draft_complete(engine, ticket_id, dept_id, content)

        # -- FASE 2: Evaluar borrador ---------------------------------
        # Persistir "evaluando" ANTES de llamar al evaluador
        if engine and ticket_id:
            _persist_evaluation_started(engine, ticket_id, dept_id)

        evaluation = await evaluate_section(
            draft_content=content,
            department_id=dept_id,
            rfp_metadata=metadata,
            key_aspects=key_aspects,
            section_id=f"ticket-{ticket_id or ''}-{dept_id}",
        )

        # Persistir evaluacion INMEDIATAMENTE despues de evaluar
        if engine:
            eval_dict = evaluation.model_dump(mode="json", by_alias=True) if hasattr(evaluation, "model_dump") else {}
            _persist_evaluation_complete(engine, ticket_id or "", dept_id, eval_dict, evaluation.overall_pass)

        # Guardar borrador (incluso si falla)
        draft = DraftContent(
            department_id=dept_id,
            content=content,
            iteration=dept_state.iteration,
            evaluation=evaluation,
            needs_human_review=not evaluation.overall_pass,
        )

        dept_state.draft = draft
        dept_state.best_draft = draft

        if evaluation.overall_pass:
            dept_state.passed = True
            dept_state.completed = True
            logger.info("Departamento %s - PASO en iteracion %d", dept_id, dept_state.iteration)
        else:
            logger.info("Departamento %s - fallo evaluacion: %s",
                        dept_id, evaluation.feedback_for_generator[:100])

    # Si se agotaron iteraciones y no paso
    if not dept_state.passed:
        dept_state.needs_human_review = True
        dept_state.completed = True
        logger.warning("Departamento %s - limite agotado, marcado needs_human_review", dept_id)


def _extract_key_aspects(dept_id, departments):
    """Extrae key_aspects para un departamento desde el handoff de Parte 1."""
    for dept in departments:
        if isinstance(dept, dict):
            if dept.get("department_id") == dept_id:
                return dept.get("key_aspects", [])
        else:
            if getattr(dept, "department_id", None) == dept_id:
                aspects = getattr(dept, "key_aspects", [])
                return [a if isinstance(a, dict) else a.model_dump() if hasattr(a, "model_dump") else {"aspect": str(a)} for a in aspects]
    return []
