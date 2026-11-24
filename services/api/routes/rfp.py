"""Rutas HTTP para el pipeline RFP Intake.

Endpoints:
  POST /api/rfp/upload    — Sube un PDF, crea ticket, encola pipeline async
  GET  /api/rfp/tickets   — Lista todos los tickets
  GET  /api/rfp/tickets/{ticket_id} — Detalle de un ticket
  POST /api/rfp/tickets/{ticket_id}/reprocess — Reintenta un ticket fallido/descartado
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from services.api.database import get_db
from services.api.models_rfp import DepartmentSectionDB, RfpMetadataDB, TicketDB
from sqlmodel import Session, select

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/rfp", tags=["rfp"])

RAW_BASE = Path(__file__).resolve().parents[3] / "data" / "raw"  # data/raw/


# ── Schemas ─────────────────────────────────────────────────────────


class TicketListItem(BaseModel):
    """Item de la lista de tickets."""
    ticket_id: str
    status: str
    client_name: str | None = None
    created_at: str
    updated_at: str
    classification_reason: str | None = None
    error_detail: str | None = None


class TicketsListResponse(BaseModel):
    tickets: list[TicketListItem]


class UploadResponse(BaseModel):
    ticket_id: str
    status: str = "analizando"
    message: str = "PDF recibido. El análisis comenzará en breve."


class DepartmentSectionResponse(BaseModel):
    department_id: str
    key_aspects: list[dict]
    open_questions: list[str]
    contains_phi: bool


class TicketDetailResponse(BaseModel):
    ticket_id: str
    status: str
    raw_pdf_path: str | None = None
    classification_reason: str | None = None
    synthesis: str | None = None
    error_detail: str | None = None
    created_at: str
    updated_at: str
    metadata: dict | None = None
    departments: list[DepartmentSectionResponse] = []


class ReprocessResponse(BaseModel):
    ticket_id: str
    status: str
    message: str = "Reprocesamiento iniciado."


# ── Endpoints ────────────────────────────────────────────────────────


@router.post("/upload", response_model=UploadResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_rfp(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> UploadResponse:
    """Sube un PDF de RFP, crea un ticket y encola el pipeline async.

    El pipeline corre en background vía Celery. La UI debe hacer poll
    del GET /rfp/tickets/{ticket_id} para conocer el estado final.
    """
    # Validar que es un PDF
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Solo se aceptan archivos PDF.",
        )

    # Leer contenido
    content = await file.read()
    if len(content) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El archivo PDF está vacío.",
        )
    if len(content) > 50 * 1024 * 1024:  # 50 MB
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El archivo PDF excede el límite de 50 MB.",
        )

    # Crear ticket
    ticket_id = str(uuid.uuid4())
    ticket_dir = RAW_BASE / ticket_id
    ticket_dir.mkdir(parents=True, exist_ok=True)

    # Guardar PDF en data/raw/{ticket_id}/original.pdf
    pdf_path = ticket_dir / "original.pdf"
    pdf_path.write_bytes(content)

    # Persistir ticket en PostgreSQL
    ticket = TicketDB(
        ticket_id=ticket_id,
        status="analizando",
        raw_pdf_path=str(pdf_path.resolve()),
    )
    db.add(ticket)
    db.commit()

    logger.info("Ticket RFP creado: %s (PDF: %s, %d bytes)", ticket_id, pdf_path, len(content))

    # Encolar pipeline async (Celery)
    try:
        from data.pipelines.rfp_intake.tasks import process_rfp_pipeline
        process_rfp_pipeline.delay(ticket_id=ticket_id, pdf_path=str(pdf_path.resolve()))
        logger.info("Tarea Celery encolada para ticket %s", ticket_id)
    except Exception as e:
        logger.warning("No se pudo encolar tarea Celery: %s. El ticket queda pendiente.", e)
        # No bloqueamos — el ticket existe y se puede reprocesar manualmente

    return UploadResponse(ticket_id=ticket_id)


@router.get("/tickets", response_model=TicketsListResponse)
async def list_tickets(db: Session = Depends(get_db)) -> TicketsListResponse:
    """Lista todos los tickets RFP."""
    tickets = db.exec(select(TicketDB).order_by(TicketDB.created_at.desc())).all()

    items = []
    for t in tickets:
        client_name = None
        if t.handoff_payload:
            client_name = t.handoff_payload.get("rfp_metadata", {}).get("client_name")
        if not client_name:
            # Intentar desde metadata
            meta = db.exec(
                select(RfpMetadataDB).where(RfpMetadataDB.ticket_id == t.ticket_id)
            ).first()
            if meta:
                client_name = meta.client_name

        items.append(TicketListItem(
            ticket_id=t.ticket_id,
            status=t.status,
            client_name=client_name,
            created_at=t.created_at.isoformat() if t.created_at else "",
            updated_at=t.updated_at.isoformat() if t.updated_at else "",
            classification_reason=t.classification_reason,
            error_detail=t.error_detail,
        ))

    return TicketsListResponse(tickets=items)


@router.get("/tickets/{ticket_id}", response_model=TicketDetailResponse)
async def get_ticket(ticket_id: str, db: Session = Depends(get_db)) -> TicketDetailResponse:
    """Obtiene el detalle completo de un ticket RFP."""
    ticket = db.exec(select(TicketDB).where(TicketDB.ticket_id == ticket_id)).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket no encontrado")

    # Obtener metadata
    meta = db.exec(
        select(RfpMetadataDB).where(RfpMetadataDB.ticket_id == ticket_id)
    ).first()

    # Obtener department sections
    dept_sections = db.exec(
        select(DepartmentSectionDB).where(DepartmentSectionDB.ticket_id == ticket_id)
    ).all()

    departments = [
        DepartmentSectionResponse(
            department_id=ds.department_id,
            key_aspects=ds.key_aspects or [],
            open_questions=ds.open_questions or [],
            contains_phi=ds.contains_phi,
        )
        for ds in dept_sections
    ]

    return TicketDetailResponse(
        ticket_id=ticket.ticket_id,
        status=ticket.status,
        raw_pdf_path=ticket.raw_pdf_path,
        classification_reason=ticket.classification_reason,
        synthesis=ticket.synthesis,
        error_detail=ticket.error_detail,
        created_at=ticket.created_at.isoformat() if ticket.created_at else "",
        updated_at=ticket.updated_at.isoformat() if ticket.updated_at else "",
        metadata=meta.model_dump() if meta else None,
        departments=departments,
    )


@router.post("/tickets/{ticket_id}/reprocess", response_model=ReprocessResponse)
async def reprocess_ticket(ticket_id: str, db: Session = Depends(get_db)) -> ReprocessResponse:
    """Reintenta un ticket fallido o reabre un ticket descartado.

    Resetea el estado a 'analizando' y encola una nueva tarea Celery.
    """
    ticket = db.exec(select(TicketDB).where(TicketDB.ticket_id == ticket_id)).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket no encontrado")

    if ticket.status not in ("error", "descartado"):
        raise HTTPException(
            status_code=400,
            detail=f"El ticket está en estado '{ticket.status}'. Solo se pueden reprocesar tickets en 'error' o 'descartado'.",
        )

    # Resetear estado
    ticket.status = "analizando"
    ticket.error_detail = None
    ticket.classification_reason = None
    db.add(ticket)
    db.commit()

    logger.info("Ticket %s reabierto para reprocesamiento", ticket_id)

    # Encolar pipeline
    try:
        from data.pipelines.rfp_intake.tasks import process_rfp_pipeline
        process_rfp_pipeline.delay(ticket_id=ticket_id, pdf_path=ticket.raw_pdf_path)
        logger.info("Tarea Celery encolada para reprocesamiento de ticket %s", ticket_id)
    except Exception as e:
        logger.warning("No se pudo encolar reprocesamiento: %s", e)

    return ReprocessResponse(
        ticket_id=ticket_id,
        status="analizando",
        message="Reprocesamiento iniciado. Consulta GET /rfp/tickets/{ticket_id} para ver el progreso.",
    )