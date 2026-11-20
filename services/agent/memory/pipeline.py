"""Pipeline de memoria: auto-evaluación, propuesta, confirmación y escritura.

Este módulo unifica todas las operaciones de memoria en funciones que el
agente (grafo LangGraph) puede llamar desde sus nodos.

Flujo completo:
  1. El agente genera su respuesta normalmente.
  2. ``auto_evaluate_proposal()`` decide si hay algo memorable.
  3. Si hay propuesta, se valida PHI y se guarda como pendiente.
  4. El próximo mensaje del usuario se clasifica con ``classifier.classify_user_intent()``.
  5. Según la clasificación, se escribe en memoria, se rechaza, o se edita.
  6. Todo queda registrado en auditoría.

Funciones públicas:
  - auto_evaluate_proposal(question, answer, facts, tool_results, tool_errors)
      → (answer_with_proposal, proposal_info o None)
  - process_user_response(user_message)
      → resultado del procesamiento de la propuesta pendiente
  - build_memory_context(facts, question)
      → contexto de memoria formateado para el prompt
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Literal

from .classifier import IntentClassification, classify_user_intent
from .store import (
    _now,
    add_fact,
    clear_pending_proposal,
    consolidate_memory,
    get_all_facts,
    get_audit_log,
    get_pending_proposal,
    has_pending_proposal,
    set_pending_proposal,
    _record_audit,
    validate_phi_safe,
)

logger = logging.getLogger(__name__)

# ── Categorías de hechos memorizables ───────────────────────────────
MEMORIZABLE_CATEGORIES = ("operational", "incident_pattern", "staff_preference")

# ── Palabras clave que sugieren "vale la pena recordar" ─────────────
# Señales de que la interacción contiene información operativa duradera.
_MEMORIZE_TRIGGER_KEYWORDS: set[str] = {
    "cambió", "cambio", "cambia",
    "nuevo", "nueva", "actualización", "actualizó",
    "modificó", "modificación",
    "desde", "ahora", "ya no",
    "mejor", "preferencia", "prefiere",
    "recuerda", "importante", "atención",
    "procord", "protocolo",
    "excepción", "excepcional",
}

# ── Señales de que NO hay nada que recordar ─────────────────────────
_NON_MEMORIZABLE_INDICATORS: set[str] = {
    "gracias", "reporte", "dashboard", "esta semana",
    "este mes", "hoy", "ayer", "mañana",
}


def auto_evaluate_proposal(
    question: str,
    answer: str,
    facts: list[dict[str, Any]],
    tool_results: list[dict[str, Any]],
    tool_errors: list[str],
) -> tuple[str, dict[str, Any] | None]:
    """Evalúa si la interacción actual contiene algo memorable.

    Se llama DESPUÉS de que el agente ha generado su respuesta.
    No modifica el estado del agente — SOLO produce una propuesta si
    corresponde.

    Args:
        question: Pregunta original del usuario.
        answer: Respuesta generada por el agente.
        facts: Hechos actuales en memoria (para evitar duplicados).
        tool_results: Resultados de tools externas.
        tool_errors: Errores de tools externas.

    Returns:
        Tupla (answer_con_propuesta, proposal_info o None).
        Si hay propuesta, answer_con_propuesta incluye la pregunta al usuario.
        Si no hay propuesta, answer_con_propuesta == answer.

    La propuesta NO se guarda directamente — solo se prepara para
    mostrarse al usuario.  ``finalize_proposal()`` la escribe si el
    usuario aprueba.
    """
    # Si ya hay una propuesta pendiente, no generar otra
    if has_pending_proposal():
        logger.info("Ya hay una propuesta pendiente — no se genera otra.")
        return answer, None

    # 1. Verificar si la interacción es candidata a generar propuesta
    if not _should_evaluate(question, answer):
        return answer, None

    # 2. Extraer candidato a hecho memorable
    candidate = _extract_memory_candidate(question, answer, tool_results, tool_errors)
    if candidate is None:
        return answer, None

    # 3. Verificar que no sea duplicado de un hecho existente
    if _is_duplicate(candidate["content"], facts):
        logger.info(
            "Propuesta descartada — ya existe en memoria: '%s'",
            candidate["content"][:80],
        )
        return answer, None

    # 4. Validación PHI (doble chequeo)
    phi_safe, phi_matches = validate_phi_safe(candidate["content"])
    if not phi_safe:
        logger.warning(
            "Propuesta auto-evaluada rechazada por PHI: %s",
            phi_matches,
        )
        # Registrar en auditoría el rechazo PHI
        _record_audit(
            event_type="phi_rejected",
            run_id="_auto_eval_",
            proposal=candidate,
            detail=f"Auto-evaluación rechazó propuesta por PHI: {phi_matches}",
        )
        return answer, None

    # 5. Construir la pregunta al usuario
    proposal_text = candidate["content"]
    category = candidate["category"]
    reason = candidate["reason"]

    proposal_suffix = (
        f"\n\n---\n\n"
        f"📝 **¿Quieres que recuerde esto para la próxima vez?**\n\n"
        f"> _{proposal_text}_\n\n"
        f"_({reason})_\n\n"
        f"*Responde con «sí», «no», o edita el texto si quieres cambiarlo.*"
    )

    answer_with_proposal = answer + proposal_suffix

    proposal_info = {
        "proposal": proposal_text,
        "reason": reason,
        "category": category,
        "source_question": question,
    }

    return answer_with_proposal, proposal_info


def _should_evaluate(question: str, answer: str) -> bool:
    """Determina si esta interacción merece auto-evaluación.

    No todas las interacciones deben generar propuestas de memoria.
    """
    combined = (question + " " + answer).lower()

    # Señales de NO evaluar
    for indicator in _NON_MEMORIZABLE_INDICATORS:
        if indicator in combined:
            return False

    # Señales de SÍ evaluar
    for keyword in _MEMORIZE_TRIGGER_KEYWORDS:
        if keyword in combined:
            return True

    # Si la respuesta es muy corta o genérica → no evaluar
    if len(answer) < 50:
        return False

    # Si la pregunta es puntual (consulta de datos) → no evaluar
    if any(
        phrase in question.lower()
        for phrase in [
            "cuál es", "cuáles son", "dime", "lista", "muestra",
            "qué pasó", "cuántos", "qué dice",
        ]
    ):
        return False

    # Por defecto, no generar propuesta (conservador)
    return False


def _extract_memory_candidate(
    question: str,
    answer: str,
    tool_results: list[dict[str, Any]],
    tool_errors: list[str],
) -> dict[str, str] | None:
    """Extrae un candidato a hecho memorable de la interacción actual.

    Analiza la pregunta y respuesta para determinar si contienen
    información que valga la pena recordar, y en qué categoría.
    """
    q_lower = question.lower()
    a_lower = answer.lower()
    combined = q_lower + " " + a_lower

    # ── Categoría: operational ──
    # Cambios en procesos, horarios, protocolos
    if any(word in combined for word in ["cambió", "cambio", "cambia", "ahora", "ya no"]):
        # Extraer la frase relevante (heurística simple)
        content = _extract_operational_fact(question, answer)
        if content:
            return {
                "content": content,
                "category": "operational",
                "reason": "Cambio en proceso operativo que puede afectar futuras consultas.",
            }

    # ── Categoría: incident_pattern ──
    # Patrones de incidentes conocidos (sin datos de paciente)
    if any(
        word in combined
        for word in ["incidente", "falla", "falló", "patrón", "suele", "típicamente", "lunes"]
    ):
        content = _extract_incident_pattern(question, answer, tool_results)
        if content:
            return {
                "content": content,
                "category": "incident_pattern",
                "reason": "Patrón de incidentes conocido que puede ayudar en diagnósticos futuros.",
            }

    # ── Categoría: staff_preference ──
    # Preferencias de staff sobre cómo presentar información
    if any(
        word in combined
        for word in ["prefiere", "preferencia", "reporte", "incluir", "formato"]
    ):
        content = _extract_staff_preference(question, answer)
        if content:
            return {
                "content": content,
                "category": "staff_preference",
                "reason": "Preferencia de miembro del staff sobre presentación de información.",
            }

    return None


def _extract_operational_fact(question: str, answer: str) -> str | None:
    """Extrae un hecho operativo de la interacción."""
    # Buscar patrones como "en X ahora Y" o "X cambió a Y"
    import re
    combined = question + " " + answer

    # Patrón: "en [clínica/lugar] ahora [cambio]"
    m = re.search(
        r"(?:en\s+)?(?:la\s+)?(?:cl[ií]nica\s+de\s+)?(\w+(?:\s+\w+)?:?\s*)"
        r"(?:,?\s*)?(?:el\s+)?(?:ahora|ya\s+no|cambi[oó])"
        r"(?:\s+(?:el\s+)?)?(.+?)(?:\.|$)",
        combined, re.IGNORECASE,
    )
    if m:
        location = m.group(1).strip()
        change = m.group(2).strip()
        return f"{location}: {change}"

    # Fallback: primera frase con "cambio" o "ahora"
    for sentence in re.split(r'[.!?\n]', combined):
        if any(word in sentence.lower() for word in ["cambió", "cambio", "cambia", "ahora"]):
            sentence = sentence.strip()
            if len(sentence) > 15:
                return sentence

    return None


def _extract_incident_pattern(
    question: str,
    answer: str,
    tool_results: list[dict[str, Any]],
) -> str | None:
    """Extrae un patrón de incidentes de la interacción."""
    # Si hay resultados de tools, extraer información generalizable
    if tool_results:
        # No extraer datos de tickets individuales, solo patrones
        return None  # Los patrones se detectan mejor con múltiples consultas

    combined = question + " " + answer
    import re

    # Buscar frases como "el sistema X falla los Y"
    m = re.search(
        r"(?:el\s+)?(?:sistema|servicio|proceso|m[oó]dulo)\s+"
        r"(.+?)\s+(?:falla|fall[oó]|tiene\s+problemas|no\s+responde)"
        r"(.+?)(?:\.|$)",
        combined, re.IGNORECASE,
    )
    if m:
        return f"Patrón: {m.group(1).strip()} → {m.group(2).strip()}".strip()

    return None


def _extract_staff_preference(question: str, answer: str) -> str | None:
    """Extrae una preferencia de staff de la interacción."""
    combined = question + " " + answer
    import re

    m = re.search(
        r"(?:para|a)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)"
        r".+?(?:incluir|mostrar|formato|ordenar|agrupar)",
        combined, re.IGNORECASE,
    )
    if m:
        staff_name = m.group(1)
        return f"Preferencia de {staff_name}: presentar información por rol, no solo por clínica."

    return None


def _is_duplicate(content: str, existing_facts: list[dict[str, Any]]) -> bool:
    """Verifica si un contenido ya está en memoria (duplicado aproximado)."""
    content_lower = content.lower().strip()
    for fact in existing_facts:
        existing = fact.get("content", "").lower().strip()
        # Coincidencia exacta o contenido muy similar
        if content_lower == existing:
            return True
        # Si el nuevo contenido está contenido en uno existente o viceversa
        if len(content_lower) > 30 and (
            content_lower in existing or existing in content_lower
        ):
            return True
    return False


# ═══════════════════════════════════════════════════════════════════════
#  Procesamiento de respuesta del usuario
# ═══════════════════════════════════════════════════════════════════════


def process_user_response(
    user_message: str,
) -> dict[str, Any]:
    """Procesa la respuesta del usuario a una propuesta de memoria pendiente.

    Esta función se llama cuando:
      - El usuario responde después de que el agente hizo una propuesta.
      - Se clasifica la intención del usuario.
      - Se ejecuta la acción correspondiente.

    Args:
        user_message: El mensaje completo del usuario.

    Returns:
        Dict con:
          - resolution: 'approved' | 'rejected' | 'edited' | 'discarded' | 'no_proposal'
          - proposal: la propuesta original o None
          - final_content: el contenido final (aprobado/editado) o None
          - audit_entry: la entrada de auditoría registrada
          - classification: el IntentClassification
    """
    # 1. Obtener propuesta pendiente
    proposal = get_pending_proposal()
    if proposal is None:
        return {
            "resolution": "no_proposal",
            "proposal": None,
            "final_content": None,
            "audit_entry": None,
            "classification": None,
        }

    # 2. Clasificar intención
    classification: IntentClassification = classify_user_intent(user_message)

    result: dict[str, Any] = {
        "proposal": proposal,
        "classification": classification.to_dict(),
    }

    # 3. Ejecutar según intención
    if classification.intent == "approve":
        # Escribir en memoria
        fact = add_fact(
            content=proposal["proposal"],
            category=proposal["category"],
            source_run_id=proposal["source_run_id"],
        )
        audit = _record_audit(
            event_type="approved",
            run_id=proposal["source_run_id"],
            proposal=proposal,
            user_message=user_message,
            classification=classification.intent,
            detail=f"Propuesta aprobada por el usuario. Hecho creado (id={fact.get('id')}).",
        )
        clear_pending_proposal()
        result["resolution"] = "approved"
        result["final_content"] = proposal["proposal"]
        result["audit_entry"] = audit

        # Intentar consolidación si hay suficientes hechos
        consolidate_memory(force=False)

    elif classification.intent == "reject":
        audit = _record_audit(
            event_type="rejected",
            run_id=proposal["source_run_id"],
            proposal=proposal,
            user_message=user_message,
            classification=classification.intent,
            detail="Propuesta rechazada explícitamente por el usuario.",
        )
        clear_pending_proposal()
        result["resolution"] = "rejected"
        result["final_content"] = None
        result["audit_entry"] = audit

    elif classification.intent == "edit":
        # Usar la edición propuesta
        edit_text = classification.edit_text or proposal["proposal"]
        # Validar PHI de la edición
        phi_safe, phi_matches = validate_phi_safe(edit_text)
        if not phi_safe:
            audit = _record_audit(
                event_type="phi_rejected",
                run_id=proposal["source_run_id"],
                proposal=proposal,
                user_message=user_message,
                classification=classification.intent,
                detail=f"Edición del usuario rechazada por PHI: {phi_matches}",
            )
            clear_pending_proposal()
            result["resolution"] = "discarded"
            result["final_content"] = None
            result["audit_entry"] = audit
        else:
            fact = add_fact(
                content=edit_text,
                category=proposal["category"],
                source_run_id=proposal["source_run_id"],
            )
            audit = _record_audit(
                event_type="approved",
                run_id=proposal["source_run_id"],
                proposal=proposal,
                user_message=user_message,
                classification=classification.intent,
                detail=f"Propuesta editada por el usuario. Hecho creado (id={fact.get('id')}). Edición: {edit_text[:80]}",
            )
            clear_pending_proposal()
            result["resolution"] = "edited"
            result["final_content"] = edit_text
            result["audit_entry"] = audit

            consolidate_memory(force=False)

    else:  # discard por defecto
        audit = _record_audit(
            event_type="discarded",
            run_id=proposal["source_run_id"],
            proposal=proposal,
            user_message=user_message,
            classification=classification.intent,
            detail=(
                "Propuesta descartada por defecto: el usuario no confirmó, "
                "rechazó o editó explícitamente la propuesta."
            ),
        )
        clear_pending_proposal()
        result["resolution"] = "discarded"
        result["final_content"] = None
        result["audit_entry"] = audit

    return result


# ═══════════════════════════════════════════════════════════════════════
#  Contexto de memoria para el agente
# ═══════════════════════════════════════════════════════════════════════


def build_memory_context(facts: list[dict[str, Any]] | None = None) -> str:
    """Construye un contexto de memoria formateado para incluir en el prompt.

    Args:
        facts: Lista de hechos (si es None, se obtienen de la BD).

    Returns:
        String formateado con los hechos memorizados, o cadena vacía.
    """
    if facts is None:
        facts = get_all_facts()

    if not facts:
        return ""

    lines = ["📚 **Memoria del agente (hechos operativos recordados):**"]
    for fact in facts:
        category_icon = {
            "operational": "🏥",
            "incident_pattern": "🔍",
            "staff_preference": "👤",
        }.get(fact.get("category", ""), "📌")
        lines.append(
            f"- {category_icon} {fact.get('content', '')} "
            f"_(desde {fact.get('approved_at', '?')[:10]})_"
        )

    return "\n".join(lines) + "\n\n"


def format_memory_confirmation(resolution: str, proposal: dict[str, Any] | None) -> str:
    """Genera un mensaje de confirmación para el usuario tras procesar su decisión.

    Args:
        resolution: 'approved', 'rejected', 'edited', 'discarded', 'no_proposal'
        proposal: La propuesta original.

    Returns:
        Mensaje de confirmación para mostrar al usuario.
    """
    if resolution == "no_proposal":
        return ""

    if proposal is None:
        return ""

    if resolution == "approved":
        return (
            f"✅ Entendido. Recordaré esto para la próxima vez:\n"
            f"> _{proposal['proposal']}_"
        )
    elif resolution == "rejected":
        return (
            f"👌 De acuerdo, no lo guardo. La propuesta queda descartada."
        )
    elif resolution == "edited":
        return (
            f"✅ Gracias por la corrección. He guardado la versión actualizada."
        )
    elif resolution == "discarded":
        return (
            f"👌 La propuesta se descarta. Si necesitas que recuerde algo "
            f"en el futuro, solo dímelo."
        )
    return ""


def memory_status_summary() -> dict[str, Any]:
    """Devuelve un resumen del estado actual de la memoria.

    Returns:
        Dict con estadísticas de la memoria.
    """
    from .store import count_facts, get_pending_proposal, get_audit_log

    facts = get_all_facts()
    proposal = get_pending_proposal()
    audit = get_audit_log(limit=5)

    by_category: dict[str, int] = {}
    for f in facts:
        cat = f.get("category", "other")
        by_category[cat] = by_category.get(cat, 0) + 1

    return {
        "total_facts": len(facts),
        "by_category": by_category,
        "has_pending_proposal": proposal is not None,
        "pending_proposal": proposal.get("proposal", "") if proposal else None,
        "recent_audit_events": [
            {
                "event_type": e.get("event_type"),
                "timestamp": e.get("timestamp"),
                "detail": (e.get("detail") or "")[:80],
            }
            for e in audit
        ],
    }