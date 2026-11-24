"""Modelos de datos del pipeline RFP Intake.

Define el estado del grafo LangGraph, los schemas internos y
los tipos compartidos entre nodos del pipeline.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ── Enums ─────────────────────────────────────────────────────────────


class TicketStatus(str, Enum):
    ANALIZANDO = "analizando"
    DESCARTADO = "descartado"
    ANALISIS_COMPLETO = "analisis_completo"
    ERROR = "error"


class DepartmentId(str, Enum):
    REVENUE = "revenue"
    CLINICAL = "clinical"
    COMPLIANCE = "compliance"


class ProgramType(str, Enum):
    OCCUPATIONAL_HEALTH = "occupational_health"
    CORPORATE_WELLNESS = "corporate_wellness"
    REFERRAL_NETWORK = "referral_network"


# ── Readability metrics ──────────────────────────────────────────────


class ReadabilityMetrics(BaseModel):
    """Métricas de legibilidad del Markdown convertido."""
    flesch_reading_ease: float | None = None
    flesch_kincaid_grade: float | None = None
    gunning_fog: float | None = None
    coleman_liau_index: float | None = None
    automated_readability_index: float | None = None
    syllable_count: int | None = None
    character_count: int | None = None
    word_count: int | None = None
    sentence_count: int | None = None


# ── Metadata de RFP ──────────────────────────────────────────────────


class RfpMetadata(BaseModel):
    """Metadatos extraídos del documento RFP."""
    client_name: str | None = None
    client_country: str | None = None  # "US" or "UK"
    program_type: str | None = None
    covered_population: int | None = None
    deadline: str | None = None  # ISO date string
    budget_range: str | None = None
    currency: str | None = None  # "USD" or "GBP"
    departments_mentioned: list[str] = Field(default_factory=list)
    extra_departments_mentioned: list[str] = Field(default_factory=list)
    estimated_complexity: str | None = None  # "low", "medium", "high"
    has_phi: bool = False  # True si el clasificador detectó PHI en el documento


# ── Key aspects por departamento ─────────────────────────────────────


class KeyAspect(BaseModel):
    """Un aspecto clave identificado para un departamento."""
    aspect: str
    detail: str
    source_reference: str | None = None  # sección/párrafo del Markdown
    confidence: str = "high"  # high, medium, low


class DepartmentSection(BaseModel):
    """Análisis de un worker para un departamento."""
    department_id: str
    key_aspects: list[KeyAspect] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    contains_phi: bool = False
    raw_extract: str | None = None  # Extracto relevante del documento


# ── Estado del pipeline ──────────────────────────────────────────────


class RfpPipelineState(BaseModel):
    """Estado completo que viaja entre nodos del grafo LangGraph."""
    ticket_id: str
    status: TicketStatus = TicketStatus.ANALIZANDO

    # Archivos
    pdf_path: str | None = None
    markdown_path: str | None = None
    markdown_content: str | None = None

    # Metadatos
    metadata: RfpMetadata = Field(default_factory=RfpMetadata)
    readability: ReadabilityMetrics = Field(default_factory=ReadabilityMetrics)

    # Clasificación
    is_rfp: bool | None = None
    classification_reason: str | None = None

    # Descomposición
    departments: list[DepartmentSection] = Field(default_factory=list)

    # Síntesis
    synthesis: str | None = None

    # Errores
    error: str | None = None


# ── Handoff para Parte 2 ─────────────────────────────────────────────


class HandoffPayload(BaseModel):
    """Payload de handoff hacia la Parte 2 (generación de borradores).

    La Parte 2 recibe ticket_id + este payload y arranca sin re-parsear el PDF.
    """
    ticket_id: str
    rfp_metadata: RfpMetadata
    departments: list[DepartmentSection]
    synthesis: str | None = None