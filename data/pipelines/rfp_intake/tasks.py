"""Tareas Celery para el pipeline RFP Intake.

La tarea Celery ejecuta el pipeline completo en background.
El endpoint de subida encola esta tarea y responde rápido.
"""

from __future__ import annotations

import asyncio
import logging

from celery import Task

from services.celery_app import celery_app

logger = logging.getLogger(__name__)


class RfpPipelineTask(Task):
    """Tarea Celery para procesar una RFP.

    Atributos:
        autoretry_for: Reintentar hasta 2 veces si hay error transitorio.
        max_retries: Máximo de reintentos.
        default_retry_delay: Esperar 10 segundos entre reintentos.
    """
    autoretry_for = (Exception,)
    max_retries = 2
    default_retry_delay = 10
    acks_late = True


@celery_app.task(
    base=RfpPipelineTask,
    bind=True,
    name="rfp.process_pipeline",
    queue="healthcore.default",
)
def process_rfp_pipeline(self: RfpPipelineTask, ticket_id: str, pdf_path: str | None = None) -> dict:
    """Ejecuta el pipeline de recepción RFP en background.

    Args:
        ticket_id: UUID del ticket a procesar.
        pdf_path: Ruta opcional al PDF (se usa en reprocesamiento).

    Returns:
        Diccionario con el estado final del pipeline.
    """
    logger.info("Celery task: process_rfp_pipeline para ticket %s", ticket_id)

    from data.pipelines.rfp_intake.graph import run_rfp_pipeline

    try:
        result = asyncio.run(run_rfp_pipeline(ticket_id=ticket_id, pdf_path=pdf_path))
        logger.info("Pipeline completado para ticket %s: %s", ticket_id, result.get("status"))
        return result
    except Exception as e:
        logger.exception("Pipeline falló para ticket %s: %s", ticket_id, e)
        # Actualizar ticket con error
        try:
            from services.api.database import get_sql_engine
            from sqlmodel import Session, select
            from services.api.models_rfp import TicketDB

            engine = get_sql_engine()
            with Session(engine) as session:
                stmt = select(TicketDB).where(TicketDB.ticket_id == ticket_id)
                ticket = session.exec(stmt).first()
                if ticket:
                    ticket.status = "error"
                    ticket.error_detail = f"Pipeline error: {e!s}"[:500]
                    session.add(ticket)
                    session.commit()
        except Exception:
            logger.exception("Error adicional actualizando ticket fallido")
        raise