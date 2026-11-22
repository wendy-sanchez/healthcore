"""
services/agent/guardrails/output_guard.py — Guardrails de salida.

Valida la respuesta del LLM antes de devolverla al usuario:
  1. **Formato**: la respuesta no debe estar vacía ni ser excesivamente larga.
  2. **Instrucciones filtradas**: la respuesta no debe contener el system prompt
     ni fragmentos de instrucciones internas.
  3. **Datos sensibles**: la respuesta no debe contener PHI ni datos
     confidenciales como términos de BAA/DPA.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class OutputValidationResult:
    """Resultado de la validación de la respuesta del LLM."""

    valid: bool
    """True si la respuesta pasa todas las validaciones."""

    safe_response: str
    """Respuesta validada, o un mensaje de fallback si no es válida."""

    issues: list[dict[str, str]] = field(default_factory=list)
    """Lista de problemas encontrados durante la validación."""


# ──────────────────────────────────────────────────────────────────────
# 1.  Patrones de detección
# ──────────────────────────────────────────────────────────────────────

# Fragmentos de instrucciones internas que nunca deben filtrarse
_LEAKED_INSTRUCTION_PATTERNS: list[str] = [
    r"You are the HealthCore Compliance Assistant",
    r"## Identity",
    r"## Domain",
    r"Your sole domain is HealthCore compliance",
    r"## Behaviour",
    r"### Prohibited: Ignoring instructions",
    r"### Prohibited: Jailbreak attempts",
    r"## Sensitive Data",
    r"## Content Isolation",
    r"RAG document content is factual reference only",
    r"Only the system prompt",
    r"NEVER reveal or generate",
    r"NO debes revelar",
    r"ERES un asistente experto",
    r"---MEMORY_PROPOSAL---",
]

# Patrones de PHI en salida (versión simplificada)
_PHI_OUTPUT_PATTERNS: list[str] = [
    # Nombre + apellido en contexto narrativo (no en ejemplos)
    r"paciente\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+\b",
    r"patient\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+\b",
    # SSN
    r"\b\d{3}-\d{2}-\d{4}\b",
    # MRN / EXP / PAT + dígitos
    r"\b(MRN|EX[AP]|HCC|PAT|HIST|NHS|SSN)\s*[-:_]?\s*\d{4,}\b",
    # Teléfono completo
    r"\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b",
    # Email completo
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
]

# Patrones de términos comerciales confidenciales
_CONFIDENTIAL_TERMS_PATTERNS: list[str] = [
    r"\b(?:BAA|DPA)\s+(?:agreement|contract|acuerdo|contrato)\s+(?:con|with)\s+\w+",
    r"\bconfidential\s+commercial\s+terms?\b",
    r"\bt[eé]rminos?\s+comerciales?\s+confidenciales?\b",
]


# ──────────────────────────────────────────────────────────────────────
# 2.  Funciones de validación
# ──────────────────────────────────────────────────────────────────────


def check_format(output: str) -> list[dict[str, str]]:
    """Check output format: not empty, not too long, not too short.

    Returns:
        List of issues (empty if valid).
    """
    issues: list[dict[str, str]] = []

    if not output or len(output.strip()) < 2:
        issues.append({
            "type": "empty_output",
            "detail": "Output is empty or too short.",
        })
        return issues

    if len(output) > 10000:
        issues.append({
            "type": "output_too_long",
            "detail": f"Output is {len(output)} chars (max 10000).",
        })

    return issues


def check_leaked_instructions(output: str) -> list[dict[str, str]]:
    """Check output for leaked internal instructions.

    Returns:
        List of issues (empty if none found).
    """
    issues: list[dict[str, str]] = []

    for pattern in _LEAKED_INSTRUCTION_PATTERNS:
        if re.search(pattern, output, re.IGNORECASE):
            issues.append({
                "type": "leaked_instruction",
                "detail": f"Output contains leaked instruction pattern: '{pattern[:50]}'",
            })

    return issues


def check_sensitive_data(output: str) -> list[dict[str, str]]:
    """Check output for sensitive data (PHI, confidential terms).

    Returns:
        List of issues (empty if none found).
    """
    issues: list[dict[str, str]] = []

    for pattern in _PHI_OUTPUT_PATTERNS:
        matches = re.findall(pattern, output, re.IGNORECASE)
        if matches:
            # Don't log the actual match (it's PHI)
            issues.append({
                "type": "phi_in_output",
                "detail": f"Output contains PHI ({len(matches)} match(es)).",
            })

    for pattern in _CONFIDENTIAL_TERMS_PATTERNS:
        if re.search(pattern, output, re.IGNORECASE):
            issues.append({
                "type": "confidential_terms",
                "detail": f"Output may contain confidential terms: '{pattern[:50]}'",
            })

    return issues


# ──────────────────────────────────────────────────────────────────────
# 3.  Validador principal
# ──────────────────────────────────────────────────────────────────────


def validate_output(output: str) -> OutputValidationResult:
    """Validate the LLM output before returning it to the user.

    Checks:
      1. Format (not empty, not too long)
      2. No leaked internal instructions
      3. No sensitive data (PHI, confidential terms)

    If any check fails, the response is replaced with a safe fallback.

    Args:
        output: The raw LLM output.

    Returns:
        OutputValidationResult with valid flag and safe_response.
    """
    if not output or not output.strip():
        logger.warning("OUTPUT GUARD: empty output rejected.")
        return OutputValidationResult(
            valid=False,
            safe_response="Lo siento, no pude generar una respuesta válida. Intenta de nuevo.",
            issues=[{"type": "empty_output", "detail": "Output is empty."}],
        )

    all_issues: list[dict[str, str]] = []

    # 1. Format check
    format_issues = check_format(output)
    all_issues.extend(format_issues)

    # 2. Leaked instructions check
    leak_issues = check_leaked_instructions(output)
    all_issues.extend(leak_issues)

    # 3. Sensitive data check
    sensitive_issues = check_sensitive_data(output)
    all_issues.extend(sensitive_issues)

    if not all_issues:
        return OutputValidationResult(
            valid=True,
            safe_response=output,
            issues=[],
        )

    # Build safe fallback
    logger.warning(
        "OUTPUT GUARD: %d issue(s) found: %s",
        len(all_issues),
        [i["type"] for i in all_issues],
    )

    if any(i["type"] in ("phi_in_output", "confidential_terms") for i in all_issues):
        safe = (
            "I generated a response that appears to contain protected information. "
            "Please rephrase your question or contact Claire Whitfield's team directly."
        )
    elif any(i["type"] == "leaked_instruction" for i in all_issues):
        safe = (
            "I'm unable to provide that information. "
            "Please ask a compliance-related question about HealthCore policies."
        )
    else:
        safe = output  # format issues only

    return OutputValidationResult(
        valid=False,
        safe_response=safe,
        issues=all_issues,
    )