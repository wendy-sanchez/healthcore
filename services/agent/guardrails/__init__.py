"""
services/agent/guardrails/ — Harness y Guardrails de seguridad (Hito 8 · Parte 2).

Este módulo implementa el sistema de aseguramiento del agente de compliance
de HealthCore, dividido en tres capas:

1. **Harness (system prompt)** — System prompt seguro que separa instrucciones
   del sistema del input del usuario, declara dominio de HealthCore, y define
   reglas de comportamiento.

2. **Content Guardrails** — Guardrails de contenido que detectan:
   - Consultas fuera de dominio (personal use, trivia, jailbreak)
   - Intento de inyectar PHI en la conversación
   - Ataques de jailbreak (3+ variantes)

3. **Output Guardrails** — Guardrails de salida que validan:
   - Formato de la respuesta
   - Ausencia de instrucciones internas filtradas
   - Ausencia de datos sensibles (PHI)
"""

from __future__ import annotations

from .harness import (
    build_system_prompt,
    wrap_rag_context,
    DOMAIN_DESCRIPTION,
    ALLOWED_BEHAVIOR,
    PROHIBITED_BEHAVIOR,
    JAILBREAK_DEFENSES,
    SYSTEM_INSTRUCTIONS_HEADER,
)
from .content_guard import (
    classify_input_scope,
    GuardResult,
    ScopeClassification,
    check_jailbreak,
    check_personal_use,
    check_domain_query,
    check_phi_in_input,
    check_gradual_extraction,
)
from .output_guard import (
    validate_output,
    OutputValidationResult,
    check_leaked_instructions,
    check_sensitive_data,
    check_format,
)

__all__ = [
    # Harness
    "build_system_prompt",
    "wrap_rag_context",
    "DOMAIN_DESCRIPTION",
    "ALLOWED_BEHAVIOR",
    "PROHIBITED_BEHAVIOR",
    "JAILBREAK_DEFENSES",
    "SYSTEM_INSTRUCTIONS_HEADER",
    # Content guard
    "classify_input_scope",
    "GuardResult",
    "ScopeClassification",
    "check_jailbreak",
    "check_personal_use",
    "check_domain_query",
    "check_phi_in_input",
    "check_gradual_extraction",
    # Output guard
    "validate_output",
    "OutputValidationResult",
    "check_leaked_instructions",
    "check_sensitive_data",
    "check_format",
]