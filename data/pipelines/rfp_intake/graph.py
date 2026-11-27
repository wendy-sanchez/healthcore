"""Grafo LangGraph del pipeline RFP Intake.

Define los nodos del pipeline y la función run_rfp_pipeline que ejecuta
el flujo completo:
  1. Convert PDF → Markdown
  2. Extract metadata & readability
  3. Classify document
  4. Orchestrate by department
  5. Workers (parallel)
  6. Synthesize

El grafo NO se mezcla con el grafo CX del agente de soporte.
"""

from __future__ import annotations

import json
import re
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
import asyncio

from data.pipelines.rfp_intake.classifier import classify_document
from data.pipelines.rfp_intake.converter import convert_pdf_to_markdown, read_converted_markdown
from data.pipelines.rfp_intake.models import (
    DepartmentSection,
    HandoffPayload,
    ReadabilityMetrics,
    RfpMetadata,
    TicketStatus,
)
from data.pipelines.rfp_intake.orchestrator import orchestrate
from data.pipelines.rfp_intake.readability import compute_readability
from data.pipelines.rfp_intake.synthesizer import synthesize
from data.pipelines.rfp_intake.workers import run_worker, run_worker_rule_based
from data.pipelines.rfp_intake.cycle import run_part2_pipeline
from services.api.models_rfp import DepartmentSectionDB

logger = logging.getLogger(__name__)

# ── Ruta de la base raw ──────────────────────────────────────────────
RAW_BASE = Path(__file__).resolve().parents[1] / "raw"  # data/raw/


async def run_rfp_pipeline(ticket_id: str, pdf_path: str | None = None) -> dict[str, Any]:
    """Ejecuta el pipeline completo de recepción RFP.

    Esta función es llamada por la tarea Celery. Cada paso persiste
    resultados en PostgreSQL vía los modelos DB.

    Args:
        ticket_id: UUID del ticket.
        pdf_path: Ruta al archivo PDF (si es reprocesamiento).

    Returns:
        Diccionario con el estado final del pipeline.
    """
    from services.api.database import get_sql_engine
    from sqlmodel import Session, select

    logger.info("Iniciando pipeline RFP para ticket %s", ticket_id)

    engine = get_sql_engine()
    state: dict[str, Any] = {
        "ticket_id": ticket_id,
        "status": TicketStatus.ANALIZANDO.value,
        "error": None,
    }

    try:
        # ── Nodo 1: Obtener PDF path ──────────────────────────────
        with Session(engine) as session:
            from services.api.models_rfp import TicketDB
            stmt = select(TicketDB).where(TicketDB.ticket_id == ticket_id)
            ticket = session.exec(stmt).first()
            if not ticket:
                raise ValueError(f"Ticket no encontrado: {ticket_id}")

            pdf_path = pdf_path or ticket.raw_pdf_path
            if not pdf_path or not Path(pdf_path).exists():
                raise FileNotFoundError(f"PDF no encontrado para ticket {ticket_id}: {pdf_path}")

            # Actualizar estado a analizando
            ticket.status = TicketStatus.ANALIZANDO.value
            session.add(ticket)
            session.commit()

        # ── Nodo 2: Convertir PDF → Markdown ──────────────────────
        logger.info("[Nodo 1/6] Convirtiendo PDF a Markdown...")
        markdown_path = convert_pdf_to_markdown(pdf_path, ticket_id)
        markdown_content = read_converted_markdown(markdown_path)
        state["markdown_path"] = markdown_path
        state["markdown_content"] = markdown_content[:50000]  # Limitar para estado
        logger.info("Markdown generado: %s (%d chars)", markdown_path, len(markdown_content))

        # ── Nodo 3: Extraer metadatos y legibilidad ───────────────
        logger.info("[Nodo 2/6] Extrayendo metadatos y legibilidad...")
        metadata, readability = _extract_initial_metadata(markdown_content)

        # Estimar complejidad basada en conteo de palabras
        if readability.word_count:
            if readability.word_count < 1000:
                metadata.estimated_complexity = "low"
            elif readability.word_count < 5000:
                metadata.estimated_complexity = "medium"
            else:
                metadata.estimated_complexity = "high"
        else:
            metadata.estimated_complexity = "medium"

        # Persistir metadatos (upsert)
        with Session(engine) as session:
            from services.api.models_rfp import RfpMetadataDB
            stmt = select(RfpMetadataDB).where(RfpMetadataDB.ticket_id == ticket_id)
            existing_meta = session.exec(stmt).first()
            if existing_meta:
                existing_meta.client_name = metadata.client_name
                existing_meta.client_country = metadata.client_country
                existing_meta.program_type = metadata.program_type
                existing_meta.covered_population = metadata.covered_population
                existing_meta.deadline = metadata.deadline
                existing_meta.budget_range = metadata.budget_range
                existing_meta.readability_fk_score = readability.flesch_kincaid_grade
                existing_meta.readability_gf_score = readability.gunning_fog
                existing_meta.readability_fre_score = readability.flesch_reading_ease
                existing_meta.readability_cl_score = readability.coleman_liau_index
                existing_meta.readability_ari_score = readability.automated_readability_index
                existing_meta.word_count = readability.word_count
                existing_meta.sentence_count = readability.sentence_count
                session.add(existing_meta)
            else:
                # Crear fila de metadatos si no existe
                new_meta = RfpMetadataDB(
                    ticket_id=ticket_id,
                    client_name=metadata.client_name,
                    client_country=metadata.client_country,
                    program_type=metadata.program_type,
                    covered_population=metadata.covered_population,
                    deadline=metadata.deadline,
                    budget_range=metadata.budget_range,
                    currency=metadata.currency,
                    departments_needed=metadata.departments_mentioned or None,
                    extra_departments_mentioned=metadata.extra_departments_mentioned or None,
                    readability_fk_score=readability.flesch_kincaid_grade,
                    readability_gf_score=readability.gunning_fog,
                    readability_fre_score=readability.flesch_reading_ease,
                    readability_cl_score=readability.coleman_liau_index,
                    readability_ari_score=readability.automated_readability_index,
                    word_count=readability.word_count,
                    sentence_count=readability.sentence_count,
                    estimated_complexity=metadata.estimated_complexity,
                )
                session.add(new_meta)
            session.commit()

        state["metadata"] = metadata.model_dump()
        state["readability"] = readability.model_dump()

        # ── Nodo 4: Clasificar documento ──────────────────────────
        logger.info("[Nodo 3/6] Clasificando documento...")
        classification = classify_document(markdown_content)
        state["classification"] = classification

        if not classification.get("is_rfp", False):
            logger.info("Documento NO es RFP. Ticket %s → descartado.", ticket_id)
            with Session(engine) as session:
                stmt = select(TicketDB).where(TicketDB.ticket_id == ticket_id)
                ticket = session.exec(stmt).first()
                if ticket:
                    ticket.status = TicketStatus.DESCARTADO.value
                    ticket.classification_reason = classification.get("reason", "No es una RFP válida")
                    session.add(ticket)
                session.commit()
            state["status"] = TicketStatus.DESCARTADO.value
            return state

        # Actualizar metadatos con clasificación
        if classification.get("client_name"):
            metadata.client_name = classification["client_name"]
        if classification.get("client_country"):
            metadata.client_country = classification["client_country"]
        if classification.get("program_type"):
            metadata.program_type = classification["program_type"]
        if classification.get("covered_population"):
            try:
                metadata.covered_population = int(classification["covered_population"])
            except (ValueError, TypeError):
                pass
        if classification.get("deadline"):
            metadata.deadline = classification["deadline"]
        if classification.get("budget_range"):
            metadata.budget_range = classification["budget_range"]
        if classification.get("currency"):
            metadata.currency = classification["currency"]
        if classification.get("departments_needed"):
            metadata.departments_mentioned = classification["departments_needed"]
        if classification.get("extra_departments_mentioned"):
            metadata.extra_departments_mentioned = classification["extra_departments_mentioned"]
        metadata.has_phi = classification.get("has_phi", False)
        # Persistir metadatos enriquecidos tras clasificación
        with Session(engine) as session:
            from services.api.models_rfp import RfpMetadataDB
            stmt = select(RfpMetadataDB).where(RfpMetadataDB.ticket_id == ticket_id)
            meta_row = session.exec(stmt).first()
            if meta_row:
                meta_row.client_name = metadata.client_name
                meta_row.client_country = metadata.client_country
                meta_row.program_type = metadata.program_type
                meta_row.covered_population = metadata.covered_population
                meta_row.deadline = metadata.deadline
                meta_row.budget_range = metadata.budget_range
                meta_row.currency = metadata.currency
                meta_row.departments_needed = metadata.departments_mentioned or None
                meta_row.extra_departments_mentioned = metadata.extra_departments_mentioned or None
                meta_row.has_phi = metadata.has_phi
                meta_row.estimated_complexity = metadata.estimated_complexity
                session.add(meta_row)
                session.commit()

        # Actualizar state con metadatos enriquecidos tras clasificación
        state["metadata"] = metadata.model_dump()

        logger.info("Documento clasificado como RFP. Metadatos: cliente=%s, país=%s, programa=%s",
                     metadata.client_name, metadata.client_country, metadata.program_type)

        # ── Nodo 5: Orchestrate ────────────────────────────────────
        logger.info("[Nodo 4/6] Orquestando descomposición por departamento...")
        departments = orchestrate(markdown_content, metadata)
        state["departments"] = [d.model_dump() for d in departments]
        logger.info("Orquestación completada: %d departamentos", len(departments))

        # ── Nodo 6: Workers en paralelo ────────────────────────────
        logger.info("[Nodo 5/6] Ejecutando workers por departamento (paralelo)...")

        async def _run_single_worker(
            dept: DepartmentSection,
            metadata: RfpMetadata,
            ticket_id: str,
            engine: Any,
        ) -> DepartmentSection:
            dept_id = dept.department_id
            logger.info("  Worker: %s", dept_id)

            # Intentar con LLM primero, fallback a reglas
            try:
                result = await asyncio.to_thread(
                    run_worker, dept_id, metadata, dept.raw_extract
                )
            except Exception as e:
                logger.warning(
                    "Worker LLM %s falló: %s. Usando fallback por reglas.",
                    dept_id, e,
                )
                result = await asyncio.to_thread(
                    run_worker_rule_based, dept_id, metadata, dept.raw_extract
                )

            # Persistir DepartmentSection en DB
            with Session(engine) as session:
                from services.api.models_rfp import DepartmentSectionDB
                dept_db = DepartmentSectionDB(
                    ticket_id=ticket_id,
                    department_id=result.department_id,
                    key_aspects=[ka.model_dump() for ka in result.key_aspects],
                    open_questions=result.open_questions,
                    contains_phi=result.contains_phi,
                )
                stmt = select(DepartmentSectionDB).where(
                    DepartmentSectionDB.ticket_id == ticket_id,
                    DepartmentSectionDB.department_id == result.department_id,
                )
                existing = session.exec(stmt).first()
                if existing:
                    existing.key_aspects = dept_db.key_aspects
                    existing.open_questions = dept_db.open_questions
                    existing.contains_phi = dept_db.contains_phi
                else:
                    session.add(dept_db)
                session.commit()

            return result

        tasks = [
            _run_single_worker(dept, metadata, ticket_id, engine)
            for dept in departments
        ]
        completed_departments: list[DepartmentSection] = await asyncio.gather(*tasks)

        # ── Nodo 7: Synthesize ─────────────────────────────────────
        logger.info("[Nodo 6/6] Sintetizando resultados...")
        synthesis_text, handoff = synthesize(completed_departments, metadata, ticket_id)

        # Actualizar ticket a analisis_completo
        with Session(engine) as session:
            stmt = select(TicketDB).where(TicketDB.ticket_id == ticket_id)
            ticket = session.exec(stmt).first()
            if ticket:
                ticket.status = TicketStatus.ANALISIS_COMPLETO.value
                ticket.synthesis = synthesis_text
                ticket.handoff_payload = handoff.model_dump(mode="json")
                session.add(ticket)
            session.commit()

        state["status"] = TicketStatus.ANALISIS_COMPLETO.value
        state["synthesis"] = synthesis_text
        state["handoff"] = handoff.model_dump(mode="json")

        # ── Nodo 8 (Parte 2): Generación y Evaluación de Borradores ─
        logger.info("[Nodo 7/7] Ejecutando Parte 2 — ciclo generador-evaluador...")
        state["status"] = TicketStatus.GENERANDO_BORRADOR.value
        with Session(engine) as session:
            stmt = select(TicketDB).where(TicketDB.ticket_id == ticket_id)
            ticket = session.exec(stmt).first()
            if ticket:
                ticket.status = TicketStatus.GENERANDO_BORRADOR.value
                session.add(ticket)
            session.commit()

        part2_handoff = await run_part2_pipeline(
            handoff, max_iterations=3, engine=engine,
        )

        # Actualizar ticket con resultados de Parte 2
        # (La persistencia intermedia en DepartmentSectionDB y TicketDB
        #  se realiza en tiempo real dentro de cycle.py)
        final_status = part2_handoff.overall_status  # "needs_human_review" or "analisis_completo"
        with Session(engine) as session:
            stmt = select(TicketDB).where(TicketDB.ticket_id == ticket_id)
            ticket = session.exec(stmt).first()
            if ticket:
                ticket.status = final_status
                ticket.handoff_payload = part2_handoff.model_dump(mode="json")
                session.add(ticket)
            session.commit()

        state["status"] = final_status
        state["part2_handoff"] = part2_handoff.model_dump(mode="json")

        # ── Nodo 9 (Parte 3): Conflict Detection, Arbitrage, Approval & Synthesis ─
        logger.info("[Nodo 9/9] Ejecutando Parte 3 — detección de conflictos, arbitraje y aprobaciones...")
        if final_status == "needs_human_review":
            try:
                from agentic_workflows_part3.graph import create_part3_pipeline
                from agentic_workflows_part3.models_part3 import TicketStatusPart3
                
                # Crear pipeline Parte 3 con checkpoint scoped al ticket
                checkpoint_dir = Path('/workspaces/healthcore/data/checkpoints')
                checkpoint_dir.mkdir(parents=True, exist_ok=True)
                part3_pipeline = create_part3_pipeline(
                    checkpoint_db_path=str(checkpoint_dir / f'{ticket_id}.db')
                )
                part3_handoff = {
                    "departments": part2_handoff.model_dump(mode="json").get("departments", [])
                    if hasattr(part2_handoff, "model_dump")
                    else part2_handoff.get("departments", []),
                }
                meta = state.get("metadata", {})
                rfp_metadata_for_part3 = {
                    "client_name": meta.get("client_name", "Unknown"),
                    "client_country": meta.get("client_country", "US"),
                    "program_type": meta.get("program_type", "unknown"),
                    "covered_population": meta.get("covered_population", 0),
                }

                # Set arbitrando state before execution
                with Session(engine) as session:
                    stmt = select(TicketDB).where(TicketDB.ticket_id == ticket_id)
                    t = session.exec(stmt).first()
                    if t:
                        t.status = 'arbitrando'
                        session.add(t)
                    session.commit()

                part3_state = part3_pipeline.run_part3_pipeline(
                    ticket_id=ticket_id,
                    part2_handoff=part3_handoff,
                    rfp_metadata=rfp_metadata_for_part3,
                )
                
                # Transition to produciendo while writing final document
                if part3_state.all_departments_approved or part3_state.final_document:
                    with Session(engine) as session:
                        stmt = select(TicketDB).where(TicketDB.ticket_id == ticket_id)
                        t = session.exec(stmt).first()
                        if t:
                            t.status = 'produciendo'
                            session.add(t)
                        session.commit()

                # Persistir resultados de Parte 3 en PostgreSQL
                with Session(engine) as session:
                    stmt = select(TicketDB).where(TicketDB.ticket_id == ticket_id)
                    ticket = session.exec(stmt).first()
                    if ticket:
                        # Si hay conflictos no resueltos o arbitraje que requiere cambios
                        if part3_state.arbitrage_outcomes:
                            has_reject = any(
                                o.decision == "reject"
                                for o in part3_state.arbitrage_outcomes
                            )
                            if has_reject:
                                ticket.status = "needs_human_review"
                        elif part3_state.interrupted_departments:
                            ticket.status = "esperando_aprobacion"
                        
                        # Si todas las aprobaciones se completaron y hay documento final
                        if part3_state.all_departments_approved and part3_state.final_document:
                            ticket.status = "terminado"
                            ticket.final_document_path = part3_state.final_document.document_path
                            ticket.part3_completed_at = datetime.now(timezone.utc)
                        
                        session.add(ticket)
                    session.commit()

                logger.info(
                    "Parte 3 completada para ticket %s: %d conflictos, %d arbitrajes, aprobaciones=%s, documento=%s",
                    ticket_id,
                    len(part3_state.detected_conflicts),
                    len(part3_state.arbitrage_outcomes),
                    part3_state.all_departments_approved,
                    part3_state.final_document is not None,
                )
                state["part3_result"] = {
                    "conflicts": len(part3_state.detected_conflicts),
                    "arbitrage": len(part3_state.arbitrage_outcomes),
                    "all_approved": part3_state.all_departments_approved,
                    "document_generated": part3_state.final_document is not None,
                }
                # Actualizar status final
                if part3_state.all_departments_approved and part3_state.final_document:
                    state["status"] = "terminado"
                elif part3_state.interrupted_departments:
                    state["status"] = "esperando_aprobacion"
                    
            except Exception as e3:
                logger.warning("Parte 3 no disponible o falló para ticket %s: %s", ticket_id, e3)
                # Parte 3 es opcional — el pipeline no falla si no está disponible
                state["part3_result"] = {"error": str(e3)}
        else:
            logger.info("Parte 3 no requiere intervención (status=%s)", final_status)

        logger.info("Pipeline RFP completado para ticket %s → %s", ticket_id, state["status"])
        return state

    except Exception as e:
        logger.error("Error en pipeline RFP para ticket %s: %s", ticket_id, e, exc_info=True)
        state["status"] = TicketStatus.ERROR.value
        state["error"] = str(e)

        # Actualizar ticket en DB con error
        try:
            with Session(engine) as session:
                from services.api.models_rfp import TicketDB
                stmt = select(TicketDB).where(TicketDB.ticket_id == ticket_id)
                ticket = session.exec(stmt).first()
                if ticket:
                    ticket.status = TicketStatus.ERROR.value
                    ticket.error_detail = str(e)[:500]
                    session.add(ticket)
                session.commit()
        except Exception:
            logger.exception("Error adicional actualizando ticket en DB")

        return state


def _extract_initial_metadata(markdown_content: str) -> tuple[RfpMetadata, ReadabilityMetrics]:
    """Extracción inicial de metadatos y legibilidad.

    Args:
        markdown_content: Contenido Markdown del documento.

    Returns:
        Tupla (RfpMetadata, ReadabilityMetrics).
    """
    readability = compute_readability(markdown_content)
    metadata = RfpMetadata()

    # Extraer indicios básicos del texto (pre-clasificación)
    content_lower = markdown_content.lower()

    # Detectar país
    if "united kingdom" in content_lower or " uk " in content_lower or "london" in content_lower:
        metadata.client_country = "UK"
    elif "united states" in content_lower or re.search(r'\bUS\b|\bUSA\b|\bU\.S\.', markdown_content) or "austin" in content_lower:
        metadata.client_country = "US"

    return metadata, readability