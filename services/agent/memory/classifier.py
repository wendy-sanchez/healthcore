"""Clasificador de intención del usuario frente a una propuesta de memoria.

Cuando el agente ha hecho una propuesta de memoria y el usuario responde,
este módulo clasifica la intención del usuario de forma explícita y
estructurada. Usa patrones léxicos (reglas) para determinar si el usuario:

  - ``approve``  — Aprueba la propuesta (sí, ok, adelante, guárdalo, etc.)
  - ``reject``   — Rechaza la propuesta (no, no guardes, olvídalo, etc.)
  - ``edit``     — Propone una edición (mejor dicho así, cambia algo, etc.)
  - ``discard``  — Cambia de tema sin responder a la propuesta (default)

Reglas de decisión:
  - Si no hay propuesta pendiente → devuelve ``no_proposal``.
  - Si el mensaje contiene señales claras de aprobación/rechazo/edición,
    se clasifica como tal.
  - Si no hay señales claras → ``discard`` (descarte por defecto).
  - NUNCA se asume aprobación por silencio o ambigüedad.
  - La clasificación ``edit`` requiere un texto de edición asociado.

Formato: devuelve un ``IntentClassification`` con:
  - intent: Literal["approve", "reject", "edit", "discard", "no_proposal"]
  - confidence: float (0.0 - 1.0)
  - edit_text: str | None (solo si intent == "edit")
  - explanation: str
"""

from __future__ import annotations

import logging
import re
from typing import Literal

from .store import get_pending_proposal

logger = logging.getLogger(__name__)


class IntentClassification:
    """Resultado de la clasificación de intención del usuario."""

    __slots__ = ("intent", "confidence", "edit_text", "explanation")

    def __init__(
        self,
        intent: Literal["approve", "reject", "edit", "discard", "no_proposal"],
        confidence: float,
        edit_text: str | None = None,
        explanation: str = "",
    ):
        self.intent = intent
        self.confidence = confidence
        self.edit_text = edit_text
        self.explanation = explanation

    def __repr__(self) -> str:
        return (
            f"IntentClassification(intent={self.intent!r}, "
            f"confidence={self.confidence:.2f}, "
            f"edit_text={self.edit_text!r})"
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "intent": self.intent,
            "confidence": self.confidence,
            "edit_text": self.edit_text,
            "explanation": self.explanation,
        }


# ── Patrones de aprobación explícita ────────────────────────────────
_APPROVE_PATTERNS: list[re.Pattern] = [
    re.compile(r"\b(?:s[ií]|ok|okay|vale|de[ae]cuerdo|adelante|"
               r"gu[aá]rdalo|recu[eé]rdalo|aprobado|confirmo|good|yes|yeah|"
               r"correcto|exacto|perfecto|guardar|recuerda)\b", re.IGNORECASE),
    re.compile(r"\b(?:hazlo|h[aá]galo|procede|adelante|guardar|guardalo|"
               r"s[ií]guiente|bien|est[aá] bien|me parece bien)\b", re.IGNORECASE),
]

# ── Patrones de rechazo explícito ───────────────────────────────────
_REJECT_PATTERNS: list[re.Pattern] = [
    re.compile(r"\b(?:no\s+(?:guardes|recuerdes|gra[ae]bes|guardar|"
               r"recuerdes eso|quiero|hagas)|"
               r"olv[ií]dalo|descarta|no|nunca|quit a|elimina|"
               r"no\s+gracias|no\s+m[aá]s|d[eé]jalo)\b", re.IGNORECASE),
]

# ── Patrones de edición ─────────────────────────────────────────────
_EDIT_PATTERNS: list[re.Pattern] = [
    re.compile(r"\b(?:mejor\s+d[ií]cho|en\s+realidad|cambia|"
               r"edita|modifica|corrige|rectifica|en\s+lugar\s+de|"
               r"mejor\s+as[ií]|no\s+es\s+exacto|matiza|precisemos|"
               r"con\s+m[aá]s\s+precisi[oó]n)\b", re.IGNORECASE),
]

# ── Umbrales de confianza ───────────────────────────────────────────
_CONFIDENCE_HIGH = 0.85
_CONFIDENCE_APPROVE_MEDIUM = 0.70
_CONFIDENCE_REJECT_MEDIUM = 0.75
_CONFIDENCE_DISCARD = 0.0  # no hay señal → descarte


def classify_user_intent(
    user_message: str,
) -> IntentClassification:
    """Clasifica la intención del usuario respecto a la propuesta pendiente.

    Args:
        user_message: El mensaje completo del usuario.

    Returns:
        IntentClassification con la decisión estructurada.
    """
    if not user_message:
        return IntentClassification(
            intent="discard",
            confidence=_CONFIDENCE_DISCARD,
            explanation="Mensaje vacío → se descarta por defecto.",
        )

    # Verificar si hay propuesta pendiente
    proposal = get_pending_proposal()
    if proposal is None:
        return IntentClassification(
            intent="no_proposal",
            confidence=1.0,
            explanation="No hay propuesta pendiente para clasificar.",
        )

    msg_lower = user_message.lower().strip()

    # ── Detectar intención de edición (primero, porque contiene "no" pero es constructiva) ──
    for pattern in _EDIT_PATTERNS:
        if pattern.search(msg_lower):
            # Intentar extraer el texto editado
            edit_text = _extract_edit_text(user_message, proposal.get("proposal", ""))
            return IntentClassification(
                intent="edit",
                confidence=_CONFIDENCE_HIGH,
                edit_text=edit_text,
                explanation=f"El usuario sugiere una edición a la propuesta: '{edit_text[:80] if edit_text else 'N/A'}'.",
            )

    # ── Detectar rechazo explícito ──
    for pattern in _REJECT_PATTERNS:
        if pattern.search(msg_lower):
            return IntentClassification(
                intent="reject",
                confidence=_CONFIDENCE_REJECT_MEDIUM,
                explanation=f"Patrón de rechazo detectado en mensaje del usuario.",
            )

    # ── Detectar aprobación explícita ──
    for pattern in _APPROVE_PATTERNS:
        if pattern.search(msg_lower):
            return IntentClassification(
                intent="approve",
                confidence=_CONFIDENCE_APPROVE_MEDIUM,
                explanation=f"Patrón de aprobación detectado en mensaje del usuario.",
            )

    # ── Si el mensaje es muy corto y parece respuesta directa ──
    if len(msg_lower) <= 5 and msg_lower in ("sí", "si", "ok", "okay", "vale", "no", "yes"):
        if msg_lower in ("sí", "si", "ok", "okay", "vale", "yes"):
            return IntentClassification(
                intent="approve",
                confidence=_CONFIDENCE_HIGH,
                explanation="Respuesta afirmativa explícita.",
            )
        else:
            return IntentClassification(
                intent="reject",
                confidence=_CONFIDENCE_HIGH,
                explanation="Respuesta negativa explícita.",
            )

    # ── Si no hay señales claras → descarte por defecto ──
    return IntentClassification(
        intent="discard",
        confidence=_CONFIDENCE_DISCARD,
        explanation=(
            "No se detectaron señales claras de aprobación, rechazo o edición. "
            "La propuesta se descarta por defecto — nunca se asume aprobación "
            "por silencio o ambigüedad."
        ),
    )


def _extract_edit_text(user_message: str, original_proposal: str) -> str | None:
    """Intenta extraer el texto editado de la propuesta.

    Estrategia simple: busca frases como 'mejor dicho: ...' o
    'en realidad ...' o 'cambia X por Y'.
    """
    msg = user_message

    # Patrón "mejor dicho: X" o "mejor así: X"
    m = re.search(
        r"(?:mejor\s+d[ií]cho|mejor\s+as[ií]|en\s+realidad)\s*:?\s*(.+?)(?:\.\s*$|$)",
        msg, re.IGNORECASE | re.DOTALL,
    )
    if m:
        return m.group(1).strip()

    # Patrón "cambia X por Y"
    m = re.search(
        r"cambia\s+(.+?)\s+por\s+(.+?)(?:\.\s*$|$)",
        msg, re.IGNORECASE | re.DOTALL,
    )
    if m:
        return f"{m.group(1).strip()} → {m.group(2).strip()}"

    # Si el mensaje es una corrección directa de la propuesta
    # (p. ej. el usuario escribe la versión corregida)
    if len(msg) > 20 and msg != original_proposal:
        return msg.strip()

    return None


def user_changed_topic(user_message: str, original_question: str | None) -> bool:
    """Determina si el usuario cambió de tema respecto a la propuesta.

    Args:
        user_message: Mensaje actual del usuario.
        original_question: Pregunta original que generó la propuesta.

    Returns:
        True si el usuario parece haber cambiado de tema.
    """
    if not original_question:
        # Sin referencia, consideramos cambio de tema si hay
        # una pregunta nueva sobre la que clasificar
        return "?" in user_message

    # Si hay una pregunta que no sea sobre la propuesta
    # y no contiene referencias a la propuesta, es cambio de tema
    # (la heurística simple: el clasificador ya nos dice si descartar)
    return False