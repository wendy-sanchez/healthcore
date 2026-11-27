"""Synthesizer del pipeline RFP.

Consolida los resultados de los workers por departamento en un resumen
único que el equipo de Ventas (Revenue Cycle) puede leer sin tener que
revisar el documento original.

El synthesizer detecta:
- Discrepancias entre workers (cifras contradictorias)
- Capacidad vs. población (conflictos)
- PHI detectada por cualquier worker
- Preguntas abiertas que necesitan respuesta antes de la Parte 2
"""

from __future__ import annotations

import logging
from typing import Any

from data.pipelines.rfp_intake.models import (
    DepartmentSection,
    HandoffPayload,
    RfpMetadata,
)

logger = logging.getLogger(__name__)


def synthesize(
    departments: list[DepartmentSection],
    metadata: RfpMetadata,
    ticket_id: str,
) -> tuple[str, HandoffPayload]:
    """Consolida los análisis de todos los departamentos.

    Args:
        departments: Lista de DepartmentSection de cada worker.
        metadata: Metadatos de la RFP.
        ticket_id: ID del ticket.

    Returns:
        Tupla (synthesis_text, handoff_payload) donde:
        - synthesis_text: Resumen legible para Ventas
        - handoff_payload: Payload estructurado para la Parte 2
    """
    synthesis_parts: list[str] = []
    issues: list[str] = []
    all_open_questions: list[str] = []
    any_phi = False

    # ── Encabezado ────────────────────────────────────────────────
    synthesis_parts.append(f"# Análisis Completo de RFP: {metadata.client_name or 'Cliente no identificado'}")
    synthesis_parts.append("")

    # ── Resumen ejecutivo ─────────────────────────────────────────
    synthesis_parts.append("## Resumen Ejecutivo")
    synthesis_parts.append("")

    if metadata.client_name:
        synthesis_parts.append(f"- **Cliente:** {metadata.client_name}")
    if metadata.client_country:
        synthesis_parts.append(f"- **País:** {metadata.client_country}")
    if metadata.program_type:
        synthesis_parts.append(f"- **Programa:** {metadata.program_type}")
    if metadata.covered_population:
        synthesis_parts.append(f"- **Población cubierta:** {metadata.covered_population:,}")
    if metadata.deadline:
        synthesis_parts.append(f"- **Fecha límite:** {metadata.deadline}")
    if metadata.budget_range:
        synthesis_parts.append(f"- **Presupuesto:** {metadata.budget_range}")
    if metadata.currency:
        synthesis_parts.append(f"- **Moneda:** {metadata.currency}")

    synthesis_parts.append("")

    # ── Análisis por departamento ─────────────────────────────────
    synthesis_parts.append("## Análisis por Departamento")
    synthesis_parts.append("")

    dept_names = {
        "revenue": "💰 Revenue Cycle (Tom Callahan)",
        "clinical": "🏥 Clinical Operations (Dr. Marcus Reid)",
        "compliance": "🔒 Compliance (Claire Whitfield)",
    }

    for dept in departments:
        dept_name = dept_names.get(dept.department_id, dept.department_id)
        synthesis_parts.append(f"### {dept_name}")
        synthesis_parts.append("")

        if dept.contains_phi:
            any_phi = True
            issues.append(f"⚠️ **PHI detectada en {dept.department_id}** — requiere revisión de Compliance antes de continuar.")

        if dept.key_aspects:
            synthesis_parts.append("**Aspectos clave:**")
            for ka in dept.key_aspects:
                confidence_icon = {"high": "✅", "medium": "📌", "low": "❓"}
                icon = confidence_icon.get(ka.confidence, "📌")
                synthesis_parts.append(f"- {icon} **{ka.aspect}:** {ka.detail}")
            synthesis_parts.append("")

        if dept.open_questions:
            synthesis_parts.append("**Preguntas abiertas (requieren respuesta):**")
            for q in dept.open_questions:
                synthesis_parts.append(f"- ❓ {q}")
                all_open_questions.append(q)
            synthesis_parts.append("")

        if not dept.key_aspects and not dept.open_questions:
            synthesis_parts.append("*Sin información específica disponible.*")
            synthesis_parts.append("")

    # ── Detección de discrepancias ─────────────────────────────────
    conflicts = _detect_conflicts(departments, metadata)
    if conflicts:
        synthesis_parts.append("## ⚠️ Discrepancias Detectadas")
        synthesis_parts.append("")
        for conflict in conflicts:
            synthesis_parts.append(f"- {conflict}")
            issues.append(conflict)
        synthesis_parts.append("")

    # ── Issues / Alertas ───────────────────────────────────────────
    if issues:
        synthesis_parts.append("## 🚨 Alertas")
        synthesis_parts.append("")
        for issue in issues:
            synthesis_parts.append(f"- {issue}")
        synthesis_parts.append("")

    # ── Handoff a Ventas ───────────────────────────────────────────
    synthesis_parts.append("## 📋 Handoff para Ventas (Parte 2)")
    synthesis_parts.append("")
    synthesis_parts.append("**¿Qué necesita cada departamento y a quién pedírselo?**")
    synthesis_parts.append("")

    for dept in departments:
        dept_name = dept_names.get(dept.department_id, dept.department_id)
        synthesis_parts.append(f"### {dept_name}")

        if dept.key_aspects:
            synthesis_parts.append(f"Ya tiene información sobre: {', '.join(ka.aspect for ka in dept.key_aspects[:3])}")
        if dept.open_questions:
            synthesis_parts.append(f"Necesita respuesta sobre: {', '.join(dept.open_questions[:3])}")

        synthesis_parts.append("")

    synthesis_parts.append("---")
    synthesis_parts.append("")
    synthesis_parts.append("*Este análisis fue generado automáticamente por el pipeline RFP Intake.*")

    synthesis_text = "\n".join(synthesis_parts)

    # ── Construir handoff payload ───────────────────────────────────
    # Limpiar raw_extract de departments para evitar inflar la DB
    import copy
    clean_departments = []
    for d in departments:
        clean = copy.copy(d)
        clean.raw_extract = None
        clean_departments.append(clean)

    handoff_payload = HandoffPayload(
        ticket_id=ticket_id,
        rfp_metadata=metadata,
        departments=clean_departments,
        synthesis=synthesis_text,
    )

    logger.info(
        "Synthesizer completado: %d departamentos, %d preguntas abiertas, PHI=%s",
        len(departments),
        len(all_open_questions),
        any_phi,
    )

    return synthesis_text, handoff_payload


def _detect_conflicts(departments: list[DepartmentSection], metadata: RfpMetadata) -> list[str]:
    """Detecta contradicciones entre workers.

    Args:
        departments: Lista de análisis por departamento.
        metadata: Metadatos de la RFP.

    Returns:
        Lista de conflictos detectados (strings descriptivos).
    """
    conflicts: list[str] = []

    # Buscar por departamento en la lista
    compliance_dept = next((d for d in departments if d.department_id == "compliance"), None)

    # Conflicto 1: Moneda vs. país
    if metadata.client_country and metadata.client_country == "US" and metadata.currency and metadata.currency != "USD":
        conflicts.append(
            f"Conflicto de moneda: cliente en EE.UU. pero moneda sugerida es {metadata.currency}. "
            "Debe ser USD. (Regla de negocio: client_country → currency)"
        )
    if metadata.client_country and metadata.client_country == "UK" and metadata.currency and metadata.currency != "GBP":
        conflicts.append(
            f"Conflicto de moneda: cliente en Reino Unido pero moneda sugerida es {metadata.currency}. "
            "Debe ser GBP. (Regla de negocio: client_country → currency)"
        )

    # Conflicto 2: Compliance debe estar presente siempre
    if not compliance_dept:
        conflicts.append("Compliance no está presente en el análisis — es obligatorio en todas las RFPs.")

    # Conflicto 3: PHI detectada
    for dept in departments:
        if dept.contains_phi:
            conflicts.append(
                f"PHI detectada en {dept.department_id}: este contenido debe ser redactado "
                "y revisado por Compliance antes de continuar a la Parte 2."
            )

    # Conflicto 4: Capacidad vs. Población (si tenemos datos de ambos)
    if metadata.covered_population and metadata.covered_population > 5000:
        conflicts.append(
            f"⚠️ Población cubierta grande ({metadata.covered_population:,} personas). "
            "Verificar con Clinical Operations si la capacidad actual de HealthCore puede cubrir este volumen."
        )

    return conflicts