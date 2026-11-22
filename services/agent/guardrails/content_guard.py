"""
services/agent/guardrails/content_guard.py — Guardrails de contenido y alcance.

Tres categorías de detección:
  1. **Domain scoping** — ¿La consulta está dentro del dominio de compliance?
  2. **Personal use** — ¿El usuario pide algo personal no relacionado?
  3. **Jailbreak** — ¿El usuario intenta eludir las restricciones?

Además detecta PHI en input y extracción gradual de información sensible.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class ScopeClassification(Enum):
    """Clasificación del alcance de la pregunta del usuario."""

    DOMAIN = "domain"
    """Consulta legítima sobre compliance, políticas o procedimientos."""

    SMALL_TALK = "small_talk"
    """Saludo o comentario casual, sin intención de consulta."""

    PERSONAL_TASK = "personal_task"
    """Solicitud de asistencia personal no relacionada con HealthCore."""

    JAILBREAK = "jailbreak"
    """Intento de eludir las instrucciones del sistema o restricciones."""

    PHI_IN_QUERY = "phi_in_query"
    """La consulta contiene PHI que el usuario no debería estar compartiendo."""

    GRADUAL_EXTRACTION = "gradual_extraction"
    """Intento de extraer información sensible pregunta por pregunta."""


@dataclass
class GuardResult:
    """Resultado de la evaluación de un guardrail de contenido."""

    classification: ScopeClassification
    """Clasificación resultante."""

    blocked: bool
    """True si la consulta debe ser bloqueada (no debe llegar al LLM)."""

    confidence: float
    """Confianza de la clasificación (0.0 a 1.0)."""

    reason: str
    """Explicación legible de por qué se llegó a esta clasificación."""

    response: Optional[str] = None
    """Respuesta predefinida para usar en lugar de llamar al LLM (si blocked=True)."""


# ──────────────────────────────────────────────────────────────────────
# 1.  Patrones de dominio — regex estructurales
# ──────────────────────────────────────────────────────────────────────

# Palabras clave del dominio de compliance HealthCore
_DOMAIN_KEYWORDS: list[str] = [
    # General compliance
    r"\bhipaa\b", r"\bgdpr\b", r"\bcomplianc", r"\bcumplimient", r"\bregulator",
    r"\bregulatori", r"\breglamento", r"\bnormati", r"\bpol[ií]tic", r"\bpolicy",
    r"\bprocedimiento", r"\bprocedure", r"\bprotocolo", r"\bprotocol",
    # Breach notification
    r"\bbrecha", r"\bbreach", r"\bnotificaci[oó]n", r"\bnotification",
    r"\bico\b", r"\binfring", r"\bviolaci[oó]n",
    # Patient/data rights
    r"\bphi\b", r"\bphi\b", r"\bprivacidad", r"\bprivacy",
    r"\bconsentimient", r"\bconsent", r"\bderechos?\s+del?\s+paciente",
    r"\bpatient\s+rights?", r"\bao\b", r"\baccess\s+request",
    r"\bsolicitud\s+de\s+acceso", r"\bsars?\b",
    # Business agreements
    r"\bbaa\b", r"\bdpa\b", r"\bbusiness\s+associate", r"\bdata\s+processing",
    r"\bacuerdo\s+de\s+sociedad?", r"\bdata\s+protection",
    # HealthCore internal
    r"\bhealthcore\b", r"\bcl[ií]nic", r"\bclinic", r"\bsede\b",
    r"\bpaciente\b(?!\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+\b)",  # "paciente" as role, not name
    r"\bshow\b", r"\bcancelaci[oó]n", r"\bcancellation",
    r"\bagendamient", r"\bschedul", r"\bcita", r"\bappointment",
    r"\bmedicare\b", r"\bmedicaid\b",
    r"\bfacturaci[oó]n", r"\bbilling", r"\breclamaci[oó]n", r"\bclaim",
    r"\brechaz[oó]", r"\bdenial",
    # Operations
    r"\bincidente", r"\bincident", r"\bticket\b", r"\binventario", r"\binventory",
    r"\bstock\b", r"\bproducto", r"\bproduct",
    # Staff
    r"\bstaff\b", r"\bpersonal\b", r"\bemplead", r"\bemployee",
    r"\bcontrataci[oó]n", r"\bhiring", r"\brrhh\b", r"\bhr\b",
]

# Patrones de saludo / small talk
_SMALL_TALK_PATTERNS: list[str] = [
    r"^(?:hola|buenas?|hey|hello|hi|good\s*(?:morning|afternoon|evening)|gracias|thanks)\s*[\.!]?\s*$",
    r"^buenos?\s+d[ií]as\s*[\.!]?\s*$",
    r"^(?:qué\s+tal|c[oó]mo\s+est[aá]s?|how\s+are\s+you)\s*[\.!?\s]*$",
    r"^(?:bien\s+gracias|fine\s+thanks|muy\s+bien)\s*[\.!]?\s*$",
    # Saludos con comas (ej: "Hola, ¿cómo estás?")
    r"^hola[,\s]+¿?(?:qué\s+tal|c[oó]mo\s+est[aá]s)?\s*[¿?!.]*\s*$",
    r"^buenos\s+d[ií]as[,\s]+¿?(?:gracias\s+por\s+tu\s+ayuda)?\s*[¿?!.]*\s*$",
    r"^gracias[,\s]+¿?(?:me\s+has\s+sido\s+de\s+gran\s+ayuda)?\s*[¿?!.]*\s*$",
    r"^(?:muchas\s+)?gracias[,\s]+¿?(?:por\s+tu\s+ayuda|por\s+la\s+información)?\s*[¿?!.]*\s*$",
]

# Patrones de tareas personales
_PERSONAL_TASK_PATTERNS: list[str] = [
    # Escritura de contenido
    r"\bescr[ií]beme\s+(?:un\s+)?(?:correo|email|mensaje|texto|carta)",
    r"\bwrite\s+(?:an?\s+)?(?:email|letter|essay|resume|cv|code|program)",
    r"\bredact(?:a|ar|ame)\s+(?:un\s+)?(?:correo|email|art[ií]culo|ensayo|carta)",
    r"\bay[uú]dame\s+a\s+redactar\b",
    r"\bhelp\s+me\s+(?:write|draft|compose)\b",
    # Tareas académicas / favores
    r"\bay[uú]dame\s+(?:con\s+)?(?:mi\s+)?(?:tarea|deberes?|homework|assignment)",
    r"\b(?:hazme|h[aá]zme|hágame|make\s+me)\s+(?:el\s+)?(?:favor\s+de\s+)?",
    # Planificación personal
    r"\bplan(?:if)?(?:ea)?\s+(?:mi\s+)?(?:viaje|vacaciones|lunch|dieta|ejercicio)",
    r"\bplan\s+(?:my\s+)?(?:trip|vacation|meal|workout)",
    # Recetas médicas
    r"\brec[eé]t(?:ame|arme|e|a)\s+(?:(?:algo)\s+)?(?:para\s+)?(?:el\s+|la\s+|mi\s+)?(?:cabeza|gripe|dolor|tos|fiebre|catarro)",
    r"\bprescribe\s+(?:me\s+)?(?:for\s+)?(?:headache|cold|cough|fever|pain)",
    # Salario / personal
    r"\bcu[áa]nto\s+(?:ganas?|cobras?|te\s+pagan)",
    r"\bhow\s+much\s+(?:do\s+you\s+)?(?:make|earn|get\s+paid)",
    # Contenido creativo
    r"\bpuedes\s+(?:hacer|decir|escribir|generar|crear)\s+(?:un|una)\s+(?:poema?|canci[oó]n|historia|cue[nt]o)",
    r"\bcan\s+you\s+(?:make|write|create|generate)\s+(?:a\s+)?(?:poem|song|story)",
    r"\bescribe?\s+(?:un\s+)?(?:poema?|canci[oó]n|historia|cue[nt]o)",
    # Aumento de sueldo / promoción
    r"\baumento\s+de\s+sueldo\b",
    r"\bpuedes\s+(?:ayudarme|ayudar\s+me)\s+(?:con\s+)?(?:un|mi)\s+(?:correo|email|carta|mensaje)\b",
    # Variantes más flexibles de redacción
    r"\bredacta\s+(?:una\s+)?(?:carta|presentación|solicitud|propuesta)",
    r"\bplanifica\s+(?:mis\s+)?(?:viaje|vacaciones|día|semana|rutina)",
    r"\bplanifica\s+(?:mi\s+)?(?:trip|vacation|day|week)",
    r"\brecétame\s+(?:algo\s+)?(?:para\s+)?(?:la\s+)?(?:cabeza|gripe|dolor|tos|fiebre|catarro)",
    r"\bescribe\s+(?:un\s+)?(?:poema?|canci[oó]n|historia|carta)",
]

# Patrones de jailbreak (3+ variantes)
_JAILBREAK_PATTERNS: list[str] = [
    # Ignorar instrucciones
    r"\bignora\s+(?:tus\s+)?instrucciones?\b",
    r"\bignore\s+(?:your\s+)?instructions?\b",
    r"\bignora\s+(?:todas\s+)?(?:las\s+)?(?:reglas?|normas?|restricciones?)\b",
    r"\bignore\s+(?:all\s+)?(?:rules?|restrictions?)\b",
    # Sin reglas / sin restricciones
    r"\b(?:act[uú]a|respond[eo])\s+(?:como\s+si\s+)?(?:sin\s+)?(?:reglas?|restricciones?|l[ií]mites?)\b",
    r"\b(?:act|respond)\s+(?:as\s+if\s+)?(?:with\s+)?(?:no\s+)?(?:rules?|restrictions?|limits?)\b",
    # DAN y variantes
    r"\bdan\b", r"\bdo\s+anything\s+now\b",
    r"\bhaz\s+lo\s+que\s+quieras\b", r"\bdo\s+what(?:ever)?\s+you\s+want\b",
    # Cambio de personalidad
    r"\beres\s+ahora\b",
    r"\byou\s+are\s+now\b",
    r"\bact[uú]a\s+como\b(?!\s+un\s+(?:experto|asistente|especialista)\s+(?:en\s+)?(?:compliance|hipaa|gdpr))",
    r"\bact\s+as\b(?!\s+(?:an?\s+)?(?:expert|assistant|specialist)\s+(?:in\s+)?(?:compliance|hipaa|gdpr))",
    # Prompt injection / override
    r"\boverride\b",
    r"\bnueva\s+instrucci[oó]n\b",
    r"\bnew\s+instruction\b",
    r"\ba\s+partir\s+de\s+ahora\b",
    r"\bfrom\s+now\s+on\b",
    r"\bnormas\s+de\s+compliance\b",
    # Extract system prompt
    r"\b(?:dime|mu[eé]strame|revela|repite|imprime|print|show|reveal|repeat|output)\s+(?:tu\s+|tus\s+|el\s+|la\s+|las\s+|los\s+)?(?:prompt|system\s+prompt|system\s+message|instrucciones?|reglas?|instructions?|rules?)\b",
    r"\btell\s+me\s+(?:your\s+)?(?:system\s+)?prompt\b",
    r"\b(?:need|want|show)\s+to\s+see\s+(?:your\s+)?(?:instructions?|system\s+prompt)\b",
    r"\bc[oó]mo\s+(?:funcionas?|est[aá]s?\s+(?:programad[oó]|configurad[oó])|te\s+(?:programaron|configuraron))\b",
    r"\bhow\s+(?:do\s+you\s+)?(?:work|operate|function|are\s+you\s+(?:programmed|configured))\b",
    # Cambio de idioma como ataque
    r"\b(?:responde|answer|respond)\s+(?:en|in)\s+(?:alem[aá]n|france[és]|chino|japon[eé]s|rruso)\s+(?:a\s+)?(?:esto|this)\b",
    # Instrucciones previas
    r"\b(?:ignor[ae]|forget|disregard|skip)\s+(?:all\s+|your\s+|the\s+)?(?:previous|prior|old|anteriores)\s+(?:instructions?|rules?|commands?|normas?)\b",
    r"\bdale\s+un\s+(?:diagnóstico?|diagnosis|receta|prescription)\b",
    r"\bcomo\s+si\s+fueras\s+(?:un\s+)?(?:médico|doctor|enfermer[oa])\b",
]

# Patrones de PHI en input (versión simplificada para el guardrail de input)
_PHI_INPUT_PATTERNS: list[str] = [
    # Nombre + apellido (paciente/patient como prefijo, con o sin coma/artículo)
    r"\b(?:el\s+)?paciente[,\s]+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+\b",
    r"\bpatient[,\s]+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+\b",
    # Nombre propio suelto en contexto de paciente (ej: "Tengo un paciente, John")
    r"\b(?:el\s+)?paciente[,\s]+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]{2,}\b",
    r"\bpatient[,\s]+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]{2,}\b",
    # Título + apellido
    r"\b(?:Sr[aá]\.?|Sra\.|Dr[aá]\.?|Mr\.|Mrs\.|Ms\.)\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+\b",
    # Fecha de nacimiento
    r"\b(?:fecha\s+de\s+nacimiento|dob|birth\s*date|born|nacid[oó])\s*:?\s*\d{1,2}[/-]\d{1,2}[/-](?:19|20)\d{2}\b",
    # SSN / Número de seguro
    r"\b\d{3}-\d{2}-\d{4}\b",
    # NHS Number (UK)
    r"\b\d{3}\s+\d{3}\s+\d{4}\b",
    # MRN / EXP / PAT + dígitos
    r"\b(MRN|EX[AP]|HCC|PAT|HIST|NHS|SSN)\s*[-:_]?\s*\d{4,}\b",
    # Email
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
    # Teléfono
    r"\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b",
    # Edad + diagnóstico (ej: "45 años, con diagnóstico de X")
    r"\b\d{1,3}\s*años?[,\s]+(?:con\s+)?(?:diagnóstico|diagnosis|diagnóstico\s+de|diagnosis\s+of)\b",
]

# Patrones de extracción gradual
_GRADUAL_EXTRACTION_KEYWORDS: list[str] = [
    r"\bbrecha\s+activa\b",
    r"\binvestigaci[oó]n\s+(?:no\s+)?cerrada?\b",
    r"\bactive\s+breach\b",
    r"\bunclosed?\s+investigation\b",
    r"\bcu[áa]ntos?\s+registros?\b",
    r"\bhow\s+many\s+records?\b",
    r"\bcu[áa]ndo\s+se\s+descubri[oó]\b",
    r"\bwhen\s+was\s+it\s+discovered?\b",
    r"\bqu[eé]\s+cl[ií]nica?\b",
    r"\bwhich\s+clinic\b",
    r"\bdueño\s+del\s+proceso\b",
    r"\bowner\b",
    r"\bdetalles?\s+del?\s+(?:acuerdo|contrato)\b",
    r"\bdetails?\s+of\s+(?:the\s+)?(?:agreement|contract)\b",
    r"\bt[eé]rminos?\s+comerciales?\b",
    r"\bcommercial\s+terms?\b",
    r"\bconfidencial\b",
    r"\bconfidential\b",
    # Patrones adicionales para extracción gradual sobre brechas
    r"\bbrecha\s+de\s+seguridad\b",
    r"\binvestigaci[oó]n\s+de\s+la\s+brecha\b",
    r"\bdetalles?\s+de\s+la\s+brecha\b",
    r"\bla\s+brecha\s+del\s+mes\b",
]


# ──────────────────────────────────────────────────────────────────────
# 2.  Funciones de detección
# ──────────────────────────────────────────────────────────────────────


def _matches_any(text: str, patterns: list[str]) -> bool:
    """Check if text matches any regex pattern in the list."""
    for pattern in patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


def _is_domain_query(text: str) -> bool:
    """Check if the query is within the HealthCore compliance domain."""
    return _matches_any(text, _DOMAIN_KEYWORDS)


def _is_small_talk(text: str) -> bool:
    """Check if the query is small talk or greeting only."""
    text_stripped = text.strip().lower()
    for pattern in _SMALL_TALK_PATTERNS:
        if re.match(pattern, text_stripped):
            return True
    return False


def _is_personal_task(text: str) -> bool:
    """Check if the query is a personal task request."""
    return _matches_any(text, _PERSONAL_TASK_PATTERNS)


def _is_jailbreak(text: str) -> bool:
    """Check if the query is a jailbreak attempt."""
    return _matches_any(text, _JAILBREAK_PATTERNS)


def _has_phi(text: str) -> bool:
    """Check if the query contains apparent PHI."""
    return _matches_any(text, _PHI_INPUT_PATTERNS)


def _is_gradual_extraction(text: str) -> bool:
    """Check if the query seems to be gradually extracting sensitive info."""
    return _matches_any(text, _GRADUAL_EXTRACTION_KEYWORDS)


# ──────────────────────────────────────────────────────────────────────
# 3.  Clasificador principal
# ──────────────────────────────────────────────────────────────────────

# Respuestas predefinidas para consultas bloqueadas
_RESPONSES = {
    ScopeClassification.SMALL_TALK: (
        "¡Hola! Soy el asistente de compliance de HealthCore. "
        "¿En qué puedo ayudarte con HIPAA, UK GDPR "
        "o las políticas de HealthCore?"
    ),
    ScopeClassification.PERSONAL_TASK: (
        "Estoy diseñado para ayudar con preguntas de compliance "
        "y políticas de HealthCore exclusivamente. "
        "No puedo ayudar con tareas personales. "
        "Por favor, haz una pregunta relacionada con compliance "
        "(HIPAA, UK GDPR, o procedimientos de HealthCore)."
    ),
    ScopeClassification.JAILBREAK: (
        "No puedo cumplir con esta solicitud. "
        "Soy el asistente de compliance de HealthCore y debo seguir "
        "mis instrucciones de operación. "
        "Por favor, haz una pregunta relacionada con compliance."
    ),
    ScopeClassification.PHI_IN_QUERY: (
        "No puedo procesar información de salud protegida (PHI). "
        "Por favor, reformula tu pregunta sin identificadores de paciente "
        "(nombres, fechas de nacimiento, números de historia clínica, "
        "o diagnósticos vinculados a una persona)."
    ),
    ScopeClassification.GRADUAL_EXTRACTION: (
        "No puedo compartir detalles de brechas activas, acuerdos "
        "confidenciales, o investigaciones en curso. "
        "Por favor, envía una solicitud formal a través del equipo "
        "de Claire Whitfield si necesitas esta información."
    ),
}


def classify_input_scope(text: str) -> GuardResult:
    """Classify the user's input to determine scope and whether to block.

    This guardrail runs BEFORE the user message reaches the LLM. It checks
    for jailbreak, personal tasks, PHI, small talk, and gradually extraction.

    Args:
        text: The user's input text.

    Returns:
        A GuardResult with classification, blocked flag, and optional response.
    """
    if not text or len(text.strip()) < 2:
        return GuardResult(
            classification=ScopeClassification.SMALL_TALK,
            blocked=False,
            confidence=0.5,
            reason="Empty or very short input.",
        )

    # ── 1. Check jailbreak first (highest priority) ──────────────────
    if _is_jailbreak(text):
        logger.info("GUARDRAIL: jailbreak detected in input: '%s'", text[:80])
        return GuardResult(
            classification=ScopeClassification.JAILBREAK,
            blocked=True,
            confidence=0.95,
            reason="Input matches jailbreak patterns (ignore instructions, role-play, DAN, etc.).",
            response=_RESPONSES[ScopeClassification.JAILBREAK],
        )

    # ── 2. Check PHI in input ────────────────────────────────────────
    if _has_phi(text):
        logger.info("GUARDRAIL: PHI detected in input: '%s'", text[:80])
        return GuardResult(
            classification=ScopeClassification.PHI_IN_QUERY,
            blocked=True,
            confidence=0.90,
            reason="Input contains apparent protected health information (PHI).",
            response=_RESPONSES[ScopeClassification.PHI_IN_QUERY],
        )

    # ── 3. Check personal task ────────────────────────────────────────
    if _is_personal_task(text):
        logger.info("GUARDRAIL: personal task detected: '%s'", text[:80])
        return GuardResult(
            classification=ScopeClassification.PERSONAL_TASK,
            blocked=True,
            confidence=0.85,
            reason="Input requests a personal/unauthorised task.",
            response=_RESPONSES[ScopeClassification.PERSONAL_TASK],
        )

    # ── 4. Check gradual extraction ──────────────────────────────────
    if _is_gradual_extraction(text):
        logger.info("GUARDRAIL: gradual extraction detected: '%s'", text[:80])
        return GuardResult(
            classification=ScopeClassification.GRADUAL_EXTRACTION,
            blocked=True,
            confidence=0.80,
            reason="Input appears to be gradually extracting sensitive information.",
            response=_RESPONSES[ScopeClassification.GRADUAL_EXTRACTION],
        )

    # ── 5. Check small talk (not blocked, but classified) ────────────
    if _is_small_talk(text):
        logger.info("GUARDRAIL: small talk detected: '%s'", text[:80])
        return GuardResult(
            classification=ScopeClassification.SMALL_TALK,
            blocked=False,
            confidence=0.85,
            reason="Input is a greeting or casual comment.",
        )

    # ── 6. Check domain ──────────────────────────────────────────────
    if _is_domain_query(text):
        return GuardResult(
            classification=ScopeClassification.DOMAIN,
            blocked=False,
            confidence=0.90,
            reason="Input is within the HealthCore compliance domain.",
        )

    # ── 7. Default: ambiguous, let LLM handle it ────────────────────
    return GuardResult(
        classification=ScopeClassification.DOMAIN,
        blocked=False,
        confidence=0.60,
        reason="Input does not match any guardrail pattern; passing to LLM.",
    )


# ──────────────────────────────────────────────────────────────────────
# 4.  Funciones individuales (exportadas para tests)
# ──────────────────────────────────────────────────────────────────────

check_jailbreak = _is_jailbreak
check_personal_use = _is_personal_task
check_domain_query = _is_domain_query
check_phi_in_input = _has_phi
check_gradual_extraction = _is_gradual_extraction