"""Modelos de la Parte 2 — Generación y Evaluación de Borradores.

Define los tipos para el ciclo generador-evaluador, incluyendo:
- DraftContent: borrador de sección de propuesta económica
- EvaluationResult: resultado estructurado de la evaluación multi-agente
- Part2State: estado del ciclo para un departamento
- Part2Handoff: payload de handoff hacia la Parte 3

Los evaluadores verifican:
1. Legibilidad (py-readability-metrics)
2. Pertinencia (que responda lo que pide la RFP)
3. Cumplimiento de lineamientos del CONTEXT.md

Nunca almacenes PHI en estos modelos.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class EvaluationType(str, Enum):
    READABILITY = "readability"
    RELEVANCE = "relevance"
    COMPLIANCE = "compliance"


class ReadabilityEval(BaseModel):
    """Resultado de la evaluación de legibilidad."""
    pass_: bool = Field(default=False, alias="pass")
    score: float | None = None
    details: str | None = None

    model_config = {"populate_by_name": True}


class RelevanceEval(BaseModel):
    """Resultado de la evaluación de pertinencia."""
    pass_: bool = Field(default=False, alias="pass")
    missing_aspects: list[str] = Field(default_factory=list)
    details: str | None = None

    model_config = {"populate_by_name": True}


class ComplianceEval(BaseModel):
    """Resultado de la evaluación de cumplimiento.

    Evalúa contra las reglas del CONTEXT.md §5:
    - PHI detection: ninguna sección debe contener PHI
    - BAA/DPA según país del cliente
    - Moneda correcta (USD/US, GBP/UK)
    - compliance siempre activo como requisito transversal

    El evaluador de cumplimiento NO debe dar opiniones subjetivas de estilo;
    debe contrastar el contenido generado contra cada regla concreta.
    """
    pass_: bool = Field(default=False, alias="pass")
    rule_ids: list[str] = Field(default_factory=list)
    violations: list[str] = Field(default_factory=list)
    details: str | None = None

    model_config = {"populate_by_name": True}


class EvaluationResult(BaseModel):
    """Resultado completo de la evaluación sobre una sección."""
    section_id: str = ""
    department_id: str
    readability: ReadabilityEval = Field(default_factory=ReadabilityEval)
    relevance: RelevanceEval = Field(default_factory=RelevanceEval)
    compliance: ComplianceEval = Field(default_factory=ComplianceEval)
    overall_pass: bool = False
    feedback_for_generator: str = ""
    """Feedback concreto y accionable para el generador. No genérico."""


class DraftContent(BaseModel):
    """Borrador de una sección de propuesta económica."""
    department_id: str
    content: str
    iteration: int = 0
    evaluation: EvaluationResult | None = None
    needs_human_review: bool = False


class Part2DepartmentState(BaseModel):
    """Estado interno del ciclo generador-evaluador para un departamento."""
    department_id: str
    draft: DraftContent | None = None
    best_draft: DraftContent | None = None
    """Último borrador generado, conservado incluso si falla evaluación."""
    iteration: int = 0
    max_iterations: int = 3
    passed: bool = False
    needs_human_review: bool = False
    completed: bool = False


class Part2PipelineState(BaseModel):
    """Estado completo de la Parte 2 para un ticket.

    Se construye a partir del handoff de la Parte 1 (HandoffPayload).
    """
    ticket_id: str
    rfp_metadata: Any  # RfpMetadata, tipado dinámico para evitar import circular
    synthesis: str | None = None
    departments: list[dict] = Field(default_factory=list)
    """Departamentos del handoff de Parte 1 (DepartmentSection como dict)."""
    department_states: list[Part2DepartmentState] = Field(default_factory=list)
    overall_status: str = "generando_borrador"
    completed: bool = False


# ── Handoff hacia Parte 3 ─────────────────────────────────────────────


class Part2DepartmentOutput(BaseModel):
    """Salida de un departamento hacia la Parte 3."""
    department_id: str
    finalized_draft: str
    """Contenido final del borrador (último borrador, incluso si falló evaluación)."""
    evaluation: EvaluationResult | None = None
    needs_human_review: bool = False
    iteration_count: int = 0
    approval_status: str = "pending"
    """Estado de aprobación: pending, approved, rejected."""


class Part2Handoff(BaseModel):
    """Payload de handoff desde la Parte 2 hacia la Parte 3 (aprobación).

    Incluye por cada departamento el borrador final y su evaluación.
    Las secciones que agotaron el límite van con needs_human_review=true.
    """
    ticket_id: str
    rfp_metadata: Any  # RfpMetadata
    departments: list[Part2DepartmentOutput]
    synthesis: str | None = None
    overall_status: str = "needs_human_review"
    """needs_human_review si alguna sección lo necesita, analisis_completo si no."""