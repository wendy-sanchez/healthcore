"""Orquestador del pipeline RFP.

Descompone el documento RFP en subtareas por departamento.
Asigna a cada worker los extractos relevantes del documento.
"""

from __future__ import annotations

import logging
import re

from data.pipelines.rfp_intake.models import (
    DepartmentId,
    DepartmentSection,
    RfpMetadata,
)

logger = logging.getLogger(__name__)

# Departamentos válidos en HealthCore para propuestas
VALID_DEPARTMENTS = [d.value for d in DepartmentId]


def orchestrate(markdown_content: str, metadata: RfpMetadata) -> list[DepartmentSection]:
    """Descompone el documento en workstreams por departamento.

    Para cada departamento, extrae las secciones relevantes del documento
    y prepara el input para el worker correspondiente.

    Args:
        markdown_content: Contenido del documento en Markdown.
        metadata: Metadatos extraídos del documento.

    Returns:
        Lista de DepartmentSection con extractos asignados a cada departamento.
    """
    departments: list[DepartmentSection] = []

    # Extraer secciones relevantes para cada departamento
    revenue_extract = _extract_revenue_sections(markdown_content)
    clinical_extract = _extract_clinical_sections(markdown_content)
    compliance_extract = _extract_compliance_sections(markdown_content)

    # Revenue siempre está presente
    departments.append(DepartmentSection(
        department_id=DepartmentId.REVENUE.value,
        key_aspects=[],
        open_questions=[],
        raw_extract=revenue_extract,
    ))

    # Clinical siempre está presente
    departments.append(DepartmentSection(
        department_id=DepartmentId.CLINICAL.value,
        key_aspects=[],
        open_questions=[],
        raw_extract=clinical_extract,
    ))

    # Compliance SIEMPRE activo (es obligatorio en todas las RFPs)
    departments.append(DepartmentSection(
        department_id=DepartmentId.COMPLIANCE.value,
        key_aspects=[],
        open_questions=[],
        raw_extract=compliance_extract,
    ))

    return departments


def _extract_revenue_sections(content: str) -> str:
    """Extrae secciones del documento relevantes para Revenue Cycle.

    Busca: términos financieros, moneda, presupuesto, estructura de pago,
    pricing, costos, volúmenes, población cubierta.
    """
    sections = _find_relevant_sections(content, [
        r"(?i)(?:budget|budget|pricing|cost|price|fee|rate|payment|currency|financial)",
        r"(?i)(?:dollar|usd|gbp|euro|€|\$|£)",
        r"(?i)(?:covered population|number of employees|headcount|volume)",
        r"(?i)(?:contract term|duration|12-month|annual|monthly)",
        r"(?i)(?:payment terms|invoice|billing|reimbursement)",
        r"(?i)(?:revenue|income|financial proposal|cost estimate)",
    ])
    return _format_extract(sections, "Revenue / Financial Information")


def _extract_clinical_sections(content: str) -> str:
    """Extrae secciones relevantes para Clinical Operations.

    Busca: tipo de programa, servicios clínicos, capacidad, personal,
    ubicaciones, volumes de pacientes.
    """
    sections = _find_relevant_sections(content, [
        r"(?i)(?:clinical|medical service|healthcare|health care)",
        r"(?i)(?:occupational health|wellness|primary care|specialist|referral)",
        r"(?i)(?:clinic|location|site|facility|on-site|on site)",
        r"(?i)(?:staff|physician|nurse|clinician|provider|personnel)",
        r"(?i)(?:program|service|care delivery|patient volume|capacity)",
        r"(?i)(?:employee health|wellness program|screening|vaccination)",
    ])
    return _format_extract(sections, "Clinical Operations Information")


def _extract_compliance_sections(content: str) -> str:
    """Extrae secciones relevantes para Compliance.

    Busca: requisitos regulatorios, HIPAA, GDPR, BAA, DPA, privacidad,
    país del cliente, tipo de datos.
    """
    sections = _find_relevant_sections(content, [
        r"(?i)(?:hipaa|gdpr|data protection|privacy|compliance|regulatory)",
        r"(?i)(?:baa|dpa|business associate|data processing|agreement)",
        r"(?i)(?:confidential|security|encrypt|audit|safeguard)",
        r"(?i)(?:patient data|phi|protected health|personal data)",
        r"(?i)(?:legal|law|regulation|comply|compliance requirement)",
        r"(?i)(?:country|jurisdiction|us|united states|uk|united kingdom)",
    ])
    return _format_extract(sections, "Compliance / Regulatory Information")


def _find_relevant_sections(content: str, patterns: list[str], context_lines: int = 5) -> list[str]:
    """Encuentra secciones del documento que coinciden con patrones dados.

    Args:
        content: Contenido del documento.
        patterns: Lista de patrones regex a buscar.
        context_lines: Líneas de contexto alrededor de cada coincidencia.

    Returns:
        Lista de fragmentos de texto relevantes.
    """
    lines = content.split("\n")
    matched_indices: set[int] = set()

    for pattern in patterns:
        for i, line in enumerate(lines):
            if re.search(pattern, line):
                start = max(0, i - context_lines)
                end = min(len(lines), i + context_lines + 1)
                for j in range(start, end):
                    matched_indices.add(j)

    if not matched_indices:
        # Si no hay coincidencias, devolver las primeras líneas como contexto general
        return lines[:20]

    # Extraer fragmentos ordenados
    sorted_indices = sorted(matched_indices)
    fragments = []
    current_fragment: list[str] = []

    for idx in sorted_indices:
        if current_fragment and idx != sorted_indices[sorted_indices.index(idx) - 1] + 1:
            fragments.append("\n".join(current_fragment))
            current_fragment = []
        current_fragment.append(lines[idx])

    if current_fragment:
        fragments.append("\n".join(current_fragment))

    return fragments


def _format_extract(sections: list[str], title: str) -> str:
    """Formatea los extractos como un texto estructurado."""
    if not sections:
        return f"# {title}\n\nNo se encontraron secciones específicas para esta área en el documento."

    result = [f"# {title}", ""]
    for i, section in enumerate(sections, 1):
        result.append(f"## Fragmento {i}")
        result.append("")
        result.append(section)
        result.append("")

    return "\n".join(result)