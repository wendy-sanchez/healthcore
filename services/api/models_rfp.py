"""Modelos SQLModel para el pipeline RFP Intake.

Persistencia en PostgreSQL (Supabase) vía SQLModel:
- Ticket: ciclo de vida del ticket
- RfpMetadataDB: metadatos de la RFP
- DepartmentSectionDB: aspectos clave por departamento

Nunca guardes identificadores de paciente ni PHI en estas tablas.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TicketDB(SQLModel, table=True):
    """Ticket de recepción RFP — ciclo de vida completo (Partes 1-3).

    Un ticket se crea cuando se sube un PDF. Atraviesa los estados:
    analizando → descartado | analisis_completo → generando_borrador
    → en_evaluacion → needs_human_review → esperando_aprobacion |
      parcialmente_aprobado | arbitrando | produciendo | terminado
    """
    __tablename__: str = "rfp_tickets"

    id: Optional[int] = Field(default=None, primary_key=True)
    ticket_id: str = Field(max_length=36, unique=True, nullable=False, index=True)
    """UUID del ticket."""
    rfp_id: str | None = Field(default=None, max_length=64)
    """Identificador opcional de la RFP (ej. número de referencia)."""
    status: str = Field(default="analizando", max_length=32, nullable=False)
    """Estado actual: analizando, descartado, analisis_completo, generando_borrador, en_evaluacion, needs_human_review, esperando_aprobacion, parcialmente_aprobado, arbitrando, produciendo, terminado, error."""
    raw_pdf_path: str | None = Field(default=None, max_length=512)
    """Ruta absoluta al PDF original en data/raw/{ticket_id}/original.pdf."""
    classification_reason: str | None = Field(default=None, max_length=500)
    """Razón de clasificación (especialmente si es descartado)."""
    synthesis: str | None = Field(default=None)
    """Texto de síntesis generado por el synthesizer."""
    handoff_payload: dict | None = Field(
        default=None,
        sa_column=Column(JSONB, nullable=True),
    )
    """Payload de handoff hacia la Parte 2/3."""
    final_document_path: str | None = Field(default=None, max_length=512)
    """Ruta al documento final generado (Parte 3)."""
    part3_completed_at: datetime | None = Field(default=None)
    """Timestamp de cuando la Parte 3 completó la síntesis."""
    error_detail: str | None = Field(default=None, max_length=500)
    """Detalle del error si el pipeline falló."""
    created_at: datetime = Field(default_factory=_utc_now, nullable=False)
    updated_at: datetime = Field(default_factory=_utc_now, nullable=False)


class RfpMetadataDB(SQLModel, table=True):
    """Metadatos de la RFP extraídos durante el pipeline.

    Mapea 1:1 con RfpMetadata del pipeline.
    Nunca almacena PHI ni datos de paciente.
    """
    __tablename__: str = "rfp_metadata"

    id: Optional[int] = Field(default=None, primary_key=True)
    ticket_id: str = Field(max_length=36, unique=True, nullable=False, index=True)
    """FK al ticket."""
    client_name: str | None = Field(default=None, max_length=200)
    """Nombre del cliente institucional."""
    client_country: str | None = Field(default=None, max_length=2)
    """País del cliente: US o UK."""
    program_type: str | None = Field(default=None, max_length=64)
    """Tipo de programa: occupational_health, corporate_wellness, referral_network."""
    covered_population: int | None = Field(default=None)
    """Número de empleados/estudiantes cubiertos."""
    deadline: str | None = Field(default=None, max_length=16)
    """Fecha límite ISO (YYYY-MM-DD)."""
    budget_range: str | None = Field(default=None, max_length=100)
    """Rango de presupuesto."""
    currency: str | None = Field(default=None, max_length=3)
    """Moneda: USD o GBP."""
    departments_needed: list[str] | None = Field(
        default=None,
        sa_column=Column(JSONB, nullable=True),
    )
    """Departamentos requeridos para esta propuesta."""
    extra_departments_mentioned: list[str] | None = Field(
        default=None,
        sa_column=Column(JSONB, nullable=True),
    )
    """Departamentos mencionados que no existen en HealthCore."""

    # Métricas de legibilidad
    readability_fk_score: float | None = Field(default=None)
    """Flesch-Kincaid Grade Level."""
    readability_gf_score: float | None = Field(default=None)
    """Gunning Fog Index."""
    readability_fre_score: float | None = Field(default=None)
    """Flesch Reading Ease score."""
    readability_cl_score: float | None = Field(default=None)
    """Coleman-Liau Index."""
    readability_ari_score: float | None = Field(default=None)
    """Automated Readability Index."""
    word_count: int | None = Field(default=None)
    """Número de palabras del Markdown."""
    sentence_count: int | None = Field(default=None)
    """Número de oraciones del Markdown."""
    estimated_complexity: str | None = Field(default=None, max_length=16)
    """low / medium / high — estimación de complejidad."""
    has_phi: bool = Field(default=False)
    """True si el clasificador detectó PHI en el documento."""

    created_at: datetime = Field(default_factory=_utc_now, nullable=False)


class DepartmentSectionDB(SQLModel, table=True):
    """Aspectos clave por departamento para una RFP.

    Almacena el resultado del worker de cada departamento.
    """
    __tablename__: str = "rfp_department_sections"

    id: Optional[int] = Field(default=None, primary_key=True)
    ticket_id: str = Field(max_length=36, nullable=False, index=True)
    """FK al ticket."""
    department_id: str = Field(max_length=32, nullable=False)
    """revenue, clinical, compliance."""
    key_aspects: list[dict] | None = Field(
        default=None,
        sa_column=Column(JSONB, nullable=True),
    )
    """Lista de KeyAspect (aspect, detail, confidence, source_reference)."""
    open_questions: list[str] | None = Field(
        default=None,
        sa_column=Column(JSONB, nullable=True),
    )
    """Preguntas abiertas que necesita respuesta."""
    contains_phi: bool = Field(default=False)
    """True si el worker detectó PHI en su extracto."""
    generation_status: str | None = Field(default=None, max_length=32)
    """Estado de generación (Parte 2): generando, evaluando, completado."""
    generation_iteration: int | None = Field(default=None)
    """Iteración actual de generación (1, 2, 3...)."""
    draft_content: str | None = Field(default=None)
    """Borrador de sección (Parte 2)."""
    evaluation_results: dict | None = Field(
        default=None,
        sa_column=Column(JSONB, nullable=True),
    )
    """Resultados de evaluación (Parte 2)."""
    approval_status: str | None = Field(default=None, max_length=32)
    """Estado de aprobación (Parte 3)."""
    approver: str | None = Field(default=None, max_length=120)
    """Nombre del aprobador (Parte 3)."""
    approved_at: datetime | None = Field(default=None)

    created_at: datetime = Field(default_factory=_utc_now, nullable=False)