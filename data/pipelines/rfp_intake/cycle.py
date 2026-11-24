"""Orquestador del ciclo generador-evaluador (Parte 2).

Coordina:
1. Generación de borrador por departamento
2. Evaluación paralela (3 evaluadores)
3. Iteración si falla (máx. max_iterations)
4. Si se agota el límite → needs_human_review, conserva último borrador
5. Handoff a Parte 3

Flujo de estados del ticket:
  analisis_completo → generando_borrador → en_evaluación → (needs_human_review | analisis_completo)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

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


async def run_part2_pipeline(handoff: HandoffPayload, max_iterations: int = 3) -> Part2Handoff:
    """Ejecuta el ciclo generador-evaluador completo para todos los departamentos.

    Args:
        handoff: Payload de la Parte 1.
        max_iterations: Límite de iteraciones por departamento.

    Returns:
        Part2Handoff listo para la Parte 3.
    """
    logger.info("Parte 2 iniciada para ticket %s — %d departamento(s)",
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

    # Procesar departamentos en paralelo (cada uno tiene su propio ciclo)
    dept_tasks = [
        _process_department(dept_state, metadata, handoff.departments)
        for dept_state in pipeline_state.department_states
    ]
    await asyncio.gather(*dept_tasks)
    for dept_state in pipeline_state.department_states:
        if not dept_state.passed:
            logger.warning("Departamento %s no pasó evaluación tras %d iteraciones — marcado needs_human_review",
                           dept_state.department_id, dept_state.iteration)

    # Construir output
    dept_outputs: list[Part2DepartmentOutput] = []
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

    logger.info("Parte 2 completada para ticket %s — overall_status=%s",
                handoff.ticket_id, overall_status)
    return handoff_payload


async def _process_department(
    dept_state: Part2DepartmentState,
    metadata: RfpMetadata,
    departments: list[Any],
) -> None:
    """Procesa un departamento: genera, evalúa, itera."""
    dept_id = dept_state.department_id

    # Extraer key_aspects del departamento (desde el handoff)
    key_aspects = _extract_key_aspects(dept_id, departments)

    while dept_state.iteration < dept_state.max_iterations and not dept_state.passed:
        dept_state.iteration += 1
        logger.info("Departamento %s — iteración %d/%d",
                    dept_id, dept_state.iteration, dept_state.max_iterations)

        # Generar borrador
        previous_feedback = None
        if dept_state.draft and dept_state.draft.evaluation:
            previous_feedback = dept_state.draft.evaluation.feedback_for_generator

        content = await generate_draft(
            department_id=dept_id,
            rfp_metadata=metadata,
            key_aspects=key_aspects,
            previous_feedback=previous_feedback,
        )

        # Evaluar
        evaluation = await evaluate_section(
            draft_content=content,
            department_id=dept_id,
            rfp_metadata=metadata,
            key_aspects=key_aspects,
            section_id=f"ticket-{getattr(metadata, 'ticket_id', '')}-{dept_id}",
        )

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
            logger.info("Departamento %s — PASÓ en iteración %d", dept_id, dept_state.iteration)
        else:
            logger.info("Departamento %s — falló evaluación: %s",
                        dept_id, evaluation.feedback_for_generator[:100])

    # Si se agotaron iteraciones y no pasó
    if not dept_state.passed:
        dept_state.needs_human_review = True
        dept_state.completed = True
        logger.warning("Departamento %s — límite agotado, marcado needs_human_review", dept_id)


def _extract_key_aspects(dept_id: str, departments: list[Any]) -> list[dict]:
    """Extrae key_aspects para un departamento desde el handoff de Parte 1."""
    for dept in departments:
        if isinstance(dept, dict):
            if dept.get("department_id") == dept_id:
                return dept.get("key_aspects", [])
        else:
            if getattr(dept, "department_id", None) == dept_id:
                aspects = getattr(dept, "key_aspects", [])
                return [a if isinstance(a, dict) else {"aspect": str(a)} for a in aspects]
    return []