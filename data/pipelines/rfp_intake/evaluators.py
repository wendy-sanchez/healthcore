"""Evaluadores multi-agente para la Parte 2.

Tres evaluadores en paralelo sobre cada sección generada:
1. Legibilidad — usando py-readability-metrics
2. Pertinencia — que responda lo que pide la RFP
3. Cumplimiento — contra lineamientos del CONTEXT.md §5

Cada evaluador devuelve un EvaluationResult estructurado.
Si una sección falla, el feedback_for_generator debe ser concreto y accionable.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any

from data.pipelines.rfp_intake.models_part2 import (
    EvaluationResult,
    ComplianceEval,
    ReadabilityEval,
    RelevanceEval,
)

logger = logging.getLogger(__name__)

# ── Reglas del CONTEXT.md §5 (lineamientos) ──────────────────────────

COMPLIANCE_RULES: dict[str, dict[str, str]] = {
    "phi-detected": {
        "description": "Ninguna sección generada puede contener nombres, diagnósticos o identificadores de paciente, reales o de ejemplo.",
        "severity": "hard_stop",
    },
    "baa-us": {
        "description": "Toda propuesta para un cliente en EE.UU. debe incluir una cláusula de Business Associate Agreement (BAA).",
        "severity": "required",
    },
    "dpa-uk": {
        "description": "Toda propuesta para un cliente en Reino Unido debe incluir una cláusula de Data Processing Agreement (DPA) referenciando UK GDPR.",
        "severity": "required",
    },
    "currency-country": {
        "description": "El precio se cotiza en USD para US y en GBP para UK.",
        "severity": "required",
    },
    "compliance-mandatory": {
        "description": "Compliance es obligatorio en todas las propuestas sin excepción.",
        "severity": "required",
    },
}

PHI_PATTERNS = re.compile(
    r"\b(?:"
    r"patient\s+(?:name|id|identifier|ssn|dob|date\s+of\s+birth)"
    r"|diagnosis\s+of\s+\w+"
    r"|ssn[\s:-]*\d{3}"
    r"|medical\s+record\s+(?:number|#)"
    r"|health\s+insurance\s+(?:id|number)"
    r"|DOB[\s:-]*\d{2}[/-]\d{2}"
    r")\b",
    re.IGNORECASE,
)

PHI_NAME_PATTERNS = re.compile(
    r"\b(?:"
    r"Mr\.\s+[A-Z][a-z]+"
    r"|Mrs\.\s+[A-Z][a-z]+"
    r"|Ms\.\s+[A-Z][a-z]+"
    r"|Dr\.\s+[A-Z][a-z]+\s+[A-Z][a-z]+"
    r")\b(?!\s*(?:Callahan|Reid|Whitfield|Marcus|Claire|Tom))",
)

BAAPatterns = re.compile(r"\bBAA\b|\bBusiness\s*Associate\s*Agreement\b", re.IGNORECASE)
DPAPatterns = re.compile(r"\bDPA\b|\bData\s*Processing\s*Agreement\b|\bUK\s*GDPR\b", re.IGNORECASE)
USDPattern = re.compile(r"\bUSD\b|\$\s*\d|dollar", re.IGNORECASE)
GBPPattern = re.compile(r"\bGBP\b|£\s*\d|pound", re.IGNORECASE)
COMPLIANCE_KEYWORDS = re.compile(r"\b(?:compliance|regulatorio|regulatory|BAA|DPA|HIPAA|GDPR)\b", re.IGNORECASE)


async def evaluate_readability(draft_content: str, section_id: str, department_id: str) -> ReadabilityEval:
    """Evalúa legibilidad del borrador usando py-readability-metrics.

    Criterios:
    - Flesch Reading Ease >= 30 (accessible)
    - Flesch-Kincaid Grade <= 16 (no universitario avanzado)
    - Longitud mínima: al menos 100 palabras
    """
    word_count = len(draft_content.split())
    sentence_count = max(1, draft_content.count(". ") + draft_content.count(".\n") + draft_content.count("!") + draft_content.count("?"))

    try:
        from py_readability_metrics import ReadabilityMetrics as PyReadability
        metrics = PyReadability(text=draft_content)
        fre = metrics.flesch_reading_ease
        fk = metrics.flesch_kincaid_grade
        details = f"Flesch Reading Ease={fre:.1f}, Flesch-Kincaid={fk:.1f}, Palabras={word_count}, Oraciones={sentence_count}"
    except Exception:
        fre = None
        fk = None
        details = f"Palabras={word_count}, Oraciones={sentence_count} (py-readability-metrics no disponible)"

    # Criterios
    passes = True
    issues = []

    if word_count < 100:
        passes = False
        issues.append(f"Demasiado corto: {word_count} palabras (mínimo 100)")

    if fre is not None and fre < 30:
        passes = False
        issues.append(f"Legibilidad baja: FRE={fre:.1f} (mínimo 30)")

    if fk is not None and fk > 16:
        passes = False
        issues.append(f"Grado Flesch-Kincaid alto: {fk:.1f} (máximo 16)")

    if issues:
        details += " | ❌ " + "; ".join(issues)
    else:
        details += " | ✅ Legibilidad aceptable"

    return ReadabilityEval(
        pass_=passes,
        score=fre if fre is not None else 0.0,
        details=details,
    )


async def evaluate_relevance(draft_content: str, department_id: str, rfp_metadata: Any, key_aspects: list[dict]) -> RelevanceEval:
    """Evalúa pertinencia del borrador respecto a la RFP y key_aspects.

    Verifica que el borrador:
    - Mencione al cliente por nombre
    - Responda los key_aspects relevantes
    - No divague sobre temas fuera del alcance
    - Use la moneda correcta según país
    """
    missing_aspects = []
    issues = []

    content_lower = draft_content.lower()
    client_name = getattr(rfp_metadata, "client_name", "")
    country = getattr(rfp_metadata, "client_country", "US")

    # Verificar mención del cliente
    if client_name and client_name.lower() not in content_lower:
        # Intentar el primer token del nombre
        first_word = client_name.split()[0].lower() if client_name.split() else ""
        if first_word and first_word not in content_lower:
            missing_aspects.append(f"mención del cliente '{client_name}'")
            issues.append(f"No se menciona al cliente '{client_name}'")

    # Verificar aspectos clave cubiertos
    for ka in key_aspects:
        aspect = ka.get("aspect", "") if isinstance(ka, dict) else getattr(ka, "aspect", "")
        if aspect and aspect.lower() not in content_lower:
            missing_aspects.append(aspect)

    # Verificar moneda
    if country == "US" and not USDPattern.search(draft_content) and not BAAPatterns.search(draft_content):
        missing_aspects.append("moneda USD")
    elif country == "UK" and not GBPPattern.search(draft_content) and not DPAPatterns.search(draft_content):
        missing_aspects.append("moneda GBP")

    details = f"Departamento: {department_id}, Aspectos faltantes: {len(missing_aspects)}"
    if missing_aspects:
        details += f" | ❌ Faltan: {', '.join(missing_aspects[:5])}"
    else:
        details += " | ✅ Todos los aspectos relevantes cubiertos"

    return RelevanceEval(
        pass_=len(missing_aspects) < 3,
        missing_aspects=missing_aspects,
        details=details,
    )


async def evaluate_compliance(draft_content: str, department_id: str, rfp_metadata: Any) -> ComplianceEval:
    """Evalúa cumplimiento contra lineamientos del CONTEXT.md §5.

    Reglas evaluadas:
    - phi-detected: sin PHI en el contenido
    - baa-us: BAA presente si cliente US
    - dpa-uk: DPA presente si cliente UK
    - currency-country: moneda correcta
    - compliance-mandatory: compliance siempre presente
    """
    country = getattr(rfp_metadata, "client_country", "US")
    violations = []
    triggered_rules = []

    # Regla 1: PHI detection
    phi_matches = PHI_PATTERNS.findall(draft_content)
    phi_names = PHI_NAME_PATTERNS.findall(draft_content)
    if phi_matches or phi_names:
        triggered_rules.append("phi-detected")
        violations.append(f"Contenido PHI detectado: {phi_matches + phi_names}")

    # Regla 2: BAA para US
    if country == "US":
        if not BAAPatterns.search(draft_content):
            triggered_rules.append("baa-us")
            violations.append("Cliente US sin cláusula BAA (Business Associate Agreement)")
    else:
        triggered_rules.append("baa-us")
        # No es violación, solo no aplica

    # Regla 3: DPA para UK
    if country == "UK":
        if not DPAPatterns.search(draft_content):
            triggered_rules.append("dpa-uk")
            violations.append("Cliente UK sin cláusula DPA referenciando UK GDPR")
    else:
        triggered_rules.append("dpa-uk")

    # Regla 4: Moneda correcta
    if country == "US" and not USDPattern.search(draft_content):
        triggered_rules.append("currency-country")
        violations.append("Cliente US: la cotización debe estar en USD")
    elif country == "UK" and not GBPPattern.search(draft_content):
        triggered_rules.append("currency-country")
        violations.append("Cliente UK: la cotización debe estar en GBP")

    # Regla 5: Compliance obligatorio
    if department_id != "compliance" and not COMPLIANCE_KEYWORDS.search(draft_content):
        triggered_rules.append("compliance-mandatory")
        violations.append("No se menciona compliance/regulatorio — requisito obligatorio en toda propuesta")

    details = f"Reglas evaluadas: {', '.join(COMPLIANCE_RULES.keys())}"
    if violations:
        details += f" | ❌ {len(violations)} violaciones: {'; '.join(violations)}"
    else:
        details += " | ✅ Todas las reglas cumplidas"

    return ComplianceEval(
        pass_=len(violations) == 0,
        rule_ids=triggered_rules,
        violations=violations,
        details=details,
    )


async def evaluate_section(
    draft_content: str,
    department_id: str,
    rfp_metadata: Any,
    key_aspects: list[dict],
    section_id: str = "",
) -> EvaluationResult:
    """Evalúa una sección con los 3 evaluadores en paralelo (asíncrono con asyncio.gather).

    Args:
        draft_content: Borrador de la sección.
        department_id: revenue, clinical, compliance.
        rfp_metadata: Metadatos de la RFP.
        key_aspects: Aspectos clave del handoff.
        section_id: ID opcional de la sección.

    Returns:
        EvaluationResult completo.
    """
    readability, relevance, compliance = await asyncio.gather(
        evaluate_readability(draft_content, section_id, department_id),
        evaluate_relevance(draft_content, department_id, rfp_metadata, key_aspects),
        evaluate_compliance(draft_content, department_id, rfp_metadata),
    )

    overall_pass = readability.pass_ and relevance.pass_ and compliance.pass_

    # Construir feedback accionable
    feedback_parts = []
    if not readability.pass_:
        feedback_parts.append(f"[LEGIBILIDAD] {readability.details}")
    if not relevance.pass_:
        feedback_parts.append(f"[PERTINENCIA] {relevance.details}")
        if relevance.missing_aspects:
            feedback_parts.append(f"  → Aspectos no cubiertos: {', '.join(relevance.missing_aspects[:5])}")
    if not compliance.pass_:
        feedback_parts.append(f"[CUMPLIMIENTO] {compliance.details}")
        if compliance.violations:
            feedback_parts.append(f"  → Violaciones: {'; '.join(compliance.violations)}")

    feedback = " | ".join(feedback_parts) if feedback_parts else "✅ Todas las evaluaciones pasaron."
    if not overall_pass and feedback_parts:
        feedback += "\n\nPor favor, corrige específicamente los puntos anteriores y regenera la sección."

    return EvaluationResult(
        section_id=section_id,
        department_id=department_id,
        readability=readability,
        relevance=relevance,
        compliance=compliance,
        overall_pass=overall_pass,
        feedback_for_generator=feedback,
    )