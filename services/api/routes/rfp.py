"""Rutas HTTP para el pipeline RFP Intake.

Endpoints existentes:
  POST /api/rfp/upload              — Sube un PDF, crea ticket, encola pipeline async
  GET  /api/rfp/tickets             — Lista todos los tickets
  GET  /api/rfp/tickets/{ticket_id} — Detalle de un ticket
  POST /api/rfp/tickets/{ticket_id}/reprocess — Reintenta un ticket fallido/descartado
  POST /api/rfp/tickets/{ticket_id}/approve   — Aprueba/rechaza un departamento (Parte 3)
  GET  /api/rfp/tickets/{ticket_id}/approvals — Historial de aprobaciones

Endpoints SSE (tiempo real):
  GET  /api/rfp/events              — Stream SSE: notificaciones en vivo (JWT required)
  POST /api/rfp/kpi-alert           — Publica alerta de umbral KPI via SSE (JWT required)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
import uuid
from pathlib import Path
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from services.api.database import get_db
from services.api.models_rfp import DepartmentSectionDB, RfpMetadataDB, TicketDB
from services.api.security import get_current_user
from services.api.sse.manager import get_sse_manager
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
    draft_content: str | None = None
    evaluation_results: dict | None = None
    approval_status: str | None = None


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

    # Publicar evento SSE para notificar en tiempo real al dashboard
    try:
        sse_manager = get_sse_manager()
        await sse_manager.publish(
            event="rfp_ticket_created",
            data={
                "ticket_id": ticket_id,
                "rfp_id": f"rfp_{ticket_id.split('-')[0][:6]}",
                "client_name": None,
                "client_country": None,
                "program_type": None,
                "status": "analizando",
                "created_at": datetime.utcnow().isoformat(),
            },
        )
    except Exception as e:
        logger.warning("No se pudo publicar evento SSE para ticket %s: %s", ticket_id, e)

    return UploadResponse(ticket_id=ticket_id)


# ── SSE Endpoint ─────────────────────────────────────────────────────


async def _sse_event_stream(request: Request) -> AsyncGenerator[str, None]:
    """
    Generador asíncrono para StreamingResponse SSE.

    Suscribe al SSEManager, maneja Last-Event-ID para replay,
    detecta desconexión del cliente vía request.is_disconnected(),
    y mantiene la conexión abierta con keep-alive.
    """
    sse_manager = get_sse_manager()

    # Replay: si el cliente envía Last-Event-ID, reenviar eventos perdidos
    last_event_id = request.headers.get("last-event-id")
    if last_event_id:
        events = sse_manager.get_history(since_event_id=last_event_id)
        for ev in events:
            if await request.is_disconnected():
                logger.debug("Cliente desconectado durante replay SSE — abortando")
                return
            payload = json.dumps(ev.data, ensure_ascii=False, default=str)
            yield f"id: {ev.event_id}\nevent: {ev.event_name}\ndata: {payload}\n\n"

    # Stream en vivo (pasa request para detección temprana de desconexión)
    async for message in sse_manager.subscribe(request=request):
        yield message


@router.get("/events")
async def rfp_events(
    request: Request,
    _user=Depends(get_current_user),
) -> StreamingResponse:
    """
    SSE (Server-Sent Events) endpoint para notificaciones en tiempo real.

    Emite eventos cuando:
      - Un nuevo ticket RFP es creado (event: rfp_ticket_created)
      - Un KPI cruza un umbral crítico (event: kpi_threshold_alert)

    Requiere JWT (Authorization: Bearer <token>).
    No usa EventSource del lado cliente; usa fetch + ReadableStream
    para poder enviar el header Authorization.

    Ver: real-time-communication.md para detalles de diseño.
    """
    return StreamingResponse(
        _sse_event_stream(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── KPI Alert Endpoint ────────────────────────────────────────────────


class KpiAlertPayload(BaseModel):
    """Payload para publicar una alerta de umbral de KPI."""
    location_id: str
    location_name: str
    kpi_type: str = Field(..., description="Tipo de KPI (no_show_rate, denial_rate, etc.)")
    kpi_value: float
    threshold: float
    unit: str = "%"
    detected_at: str | None = None


@router.post("/kpi-alert", status_code=status.HTTP_202_ACCEPTED)
async def rfp_kpi_alert(
    body: KpiAlertPayload,
    _user=Depends(get_current_user),
) -> dict:
    """
    Publica una alerta de umbral de KPI via SSE.

    Este endpoint es llamado por el pipeline de reporting cuando
    detecta condiciones críticas (no-show rate > 25%, denial rate > 10%).
    Reutiliza el mismo canal SSE que los eventos de tickets RFP.
    """
    sse_manager = get_sse_manager()
    await sse_manager.publish(
        event="kpi_threshold_alert",
        data=body.model_dump(exclude_none=True),
    )
    logger.info(
        "Alerta KPI publicada: %s=%.1f%% (umbral %.1f%%) en %s",
        body.kpi_type, body.kpi_value, body.threshold, body.location_id,
    )
    return {"status": "published", "event": "kpi_threshold_alert"}


@router.get("/tickets", response_model=TicketsListResponse)
async def list_tickets(db: Session = Depends(get_db)) -> TicketsListResponse:
    """Lista todos los tickets RFP."""
    tickets = db.exec(select(TicketDB).order_by(TicketDB.created_at.desc())).all()

    # Eager-load metadatos para evitar N+1
    ticket_ids = [t.ticket_id for t in tickets]
    metadata_rows = db.exec(
        select(RfpMetadataDB).where(RfpMetadataDB.ticket_id.in_(ticket_ids))
    ).all()
    metadata_map: dict[str, RfpMetadataDB] = {m.ticket_id: m for m in metadata_rows}

    items = []
    for t in tickets:
        client_name = None
        if t.handoff_payload:
            client_name = t.handoff_payload.get("rfp_metadata", {}).get("client_name")
        if not client_name:
            meta = metadata_map.get(t.ticket_id)
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
            draft_content=ds.draft_content,
            evaluation_results=ds.evaluation_results,
            approval_status=ds.approval_status,
        )
        for ds in dept_sections
    ]

    # Transformar metadata plana a formato anidado que espera la UI
    metadata_response = None
    if meta:
        metadata_response = {
            "client_name": meta.client_name,
            "client_country": meta.client_country,
            "program_type": meta.program_type,
            "covered_population": meta.covered_population,
            "deadline": meta.deadline,
            "budget_range": meta.budget_range,
            "currency": meta.currency,
            "departments_needed": meta.departments_needed or [],
            "extra_departments_mentioned": meta.extra_departments_mentioned or [],
            "readability": {
                "flesch_reading_ease": meta.readability_fre_score,
                "flesch_kincaid_grade": meta.readability_fk_score,
                "gunning_fog": meta.readability_gf_score,
                "coleman_liau_index": meta.readability_cl_score,
                "automated_readability_index": meta.readability_ari_score,
                "word_count": meta.word_count or 0,
            } if any([meta.readability_fk_score is not None,
                       meta.readability_gf_score is not None,
                       meta.readability_fre_score is not None,
                       meta.word_count is not None]) else None,
            "word_count": meta.word_count,
            "estimated_complexity": meta.estimated_complexity,
            "has_phi": meta.has_phi,
        }

    return TicketDetailResponse(
        ticket_id=ticket.ticket_id,
        status=ticket.status,
        raw_pdf_path=ticket.raw_pdf_path,
        classification_reason=ticket.classification_reason,
        synthesis=ticket.synthesis,
        error_detail=ticket.error_detail,
        created_at=ticket.created_at.isoformat() if ticket.created_at else "",
        updated_at=ticket.updated_at.isoformat() if ticket.updated_at else "",
        metadata=metadata_response,
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

    # Resetear estado y limpiar datos de síntesis previa
    ticket.status = "analizando"
    ticket.error_detail = None
    ticket.classification_reason = None
    ticket.handoff_payload = None
    db.add(ticket)
    db.commit()

    # Eliminar metadatos previos para que se regeneren limpios
    old_meta = db.exec(
        select(RfpMetadataDB).where(RfpMetadataDB.ticket_id == ticket_id)
    ).all()
    for m in old_meta:
        db.delete(m)

    # Eliminar department sections previas (incluye draft, evaluation, approval)
    old_sections = db.exec(
        select(DepartmentSectionDB).where(DepartmentSectionDB.ticket_id == ticket_id)
    ).all()
    for s in old_sections:
        db.delete(s)

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


# ── Part 3 Endpoints: Approvals ─────────────────────────────────────


class ApproveRequest(BaseModel):
    approval_status: str = Field(..., description="approved | rejected | requested_changes")
    approver_name: str
    approver_email: str
    rejection_reason: str | None = None
    requested_changes: list[str] | None = None

    @field_validator("requested_changes")
    @classmethod
    def validate_requested_changes(cls, v, info):
        if info.data.get("approval_status") == "requested_changes" and not v:
            raise ValueError("requested_changes is required when approval_status is requested_changes")
        return v


class ApprovalRecord(BaseModel):
    ticket_id: str
    department_id: str
    approval_status: str
    approver_name: str
    approved_at: str


class ApprovalsListResponse(BaseModel):
    ticket_id: str
    approvals: list[ApprovalRecord] = []


@router.post("/tickets/{ticket_id}/approve", status_code=status.HTTP_200_OK)
async def approve_ticket_department(
    ticket_id: str,
    department_id: str,
    body: ApproveRequest,
    db: Session = Depends(get_db),
) -> dict:
    """Registra una aprobación/rechazo de un departamento del pipeline Parte 3.

    Valida que el ticket exista, que esté en estado 'esperando_aprobacion',
    persiste la respuesta en PostgreSQL, y si todos los departamentos han
    aprobado, desencadena la síntesis del documento final.
    """
    # 1. Validar ticket
    ticket = db.exec(select(TicketDB).where(TicketDB.ticket_id == ticket_id)).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket no encontrado")

    if ticket.status not in ("esperando_aprobacion", "needs_human_review"):
        raise HTTPException(
            status_code=400,
            detail=f"El ticket está en estado '{ticket.status}'. Solo se aceptan aprobaciones en estado 'esperando_aprobacion'.",
        )

    # 2. Validar department_id exists
    department = db.exec(
        select(DepartmentSectionDB).where(
            DepartmentSectionDB.ticket_id == ticket_id,
            DepartmentSectionDB.department_id == department_id,
        )
    ).first()
    if not department:
        raise HTTPException(status_code=404, detail=f"Departamento '{department_id}' no encontrado para este ticket")

    # 3. Validar approval_status
    valid_statuses = {"approved", "rejected", "requested_changes"}
    if body.approval_status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"approval_status debe ser uno de: {', '.join(sorted(valid_statuses))}")

    # 4. Persistir approbación en DepartmentSectionDB
    department.approval_status = body.approval_status
    department.approver = body.approver_name
    department.approved_at = datetime.utcnow()
    db.add(department)
    db.commit()
    db.refresh(department)

    logger.info(
        "Aprobación registrada: ticket=%s dept=%s status=%s approver=%s",
        ticket_id, department_id, body.approval_status, body.approver_name,
    )

    # 5. Re-evaluar estado del ticket: si todos los deptos aprobaron -> terminar
    all_sections = db.exec(
        select(DepartmentSectionDB).where(DepartmentSectionDB.ticket_id == ticket_id)
    ).all()

    all_approved = all(
        s.approval_status == "approved"
        for s in all_sections
        if s.approval_status is not None  # Solo considerar los que han sido evaluados
    )

    # Si hay secciones y todas las evaluadas están aprobadas, marcar completado
    evaluated_sections = [s for s in all_sections if s.approval_status is not None]
    pending_sections = [s for s in all_sections if s.approval_status is None]

    if evaluated_sections and not pending_sections and all_approved:
        # Todos los departamentos han aprobado -> intentar síntesis
        ticket.status = "terminado"
        ticket.part3_completed_at = datetime.utcnow()

        # Intentar síntesis de documento final si está disponible Part 3
        try:
            from agentic_workflows_part3.graph import create_part3_pipeline
            pipeline = create_part3_pipeline()
            checkpoint = pipeline.checkpointer.get(ticket_id)
            if checkpoint:
                part3_state = pipeline.handle_approval_response(
                    ticket_id=ticket_id,
                    department_id=department_id,
                    approval_response=body.model_dump(),
                )
                if part3_state and part3_state.final_document:
                    ticket.final_document_path = part3_state.final_document.document_path
                    logger.info("Documento final generado para ticket %s en %s", ticket_id, ticket.final_document_path)
        except ImportError:
            logger.warning("Part3 pipeline no disponible para síntesis posterior a aprobación")
        except Exception as e:
            logger.warning("Error en síntesis posterior a aprobación: %s", e)

        db.add(ticket)
        db.commit()

        return {
            "message": "Todos los departamentos aprobaron. Ticket completado.",
            "ticket_id": ticket_id,
            "status": "terminado",
            "department": department_id,
            "approval_status": body.approval_status,
        }

    # Si solo algunos aprobaron, actualizar estado con granularidad Part 3
    if all_sections and all_approved:
        ticket.status = "terminado"
    elif body.approval_status == "rejected":
        ticket.status = "needs_human_review"
    elif body.approval_status == "requested_changes":
        ticket.status = "needs_human_review"
    elif evaluated_sections and pending_sections and all_approved is False:
        ticket.status = "parcialmente_aprobado"
    else:
        ticket.status = "esperando_aprobacion"

    db.add(ticket)
    db.commit()

    return {
        "message": f"Aprobación registrada para departamento '{department_id}'.",
        "ticket_id": ticket_id,
        "department": department_id,
        "approval_status": body.approval_status,
        "ticket_status": ticket.status,
    }


@router.get("/tickets/{ticket_id}/approvals", response_model=ApprovalsListResponse)
async def get_ticket_approvals(
    ticket_id: str,
    db: Session = Depends(get_db),
) -> ApprovalsListResponse:
    """Obtiene el historial de aprobaciones de todos los departamentos de un ticket."""
    ticket = db.exec(select(TicketDB).where(TicketDB.ticket_id == ticket_id)).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket no encontrado")

    sections = db.exec(
        select(DepartmentSectionDB).where(DepartmentSectionDB.ticket_id == ticket_id)
    ).all()

    approvals = [
        ApprovalRecord(
            ticket_id=s.ticket_id,
            department_id=s.department_id,
            approval_status=s.approval_status or "pending",
            approver_name=s.approver or "",
            approved_at=s.approved_at.isoformat() if s.approved_at else "",
        )
        for s in sections
    ]

    return ApprovalsListResponse(ticket_id=ticket_id, approvals=approvals)