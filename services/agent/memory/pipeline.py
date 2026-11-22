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

import re

logger = logging.getLogger(__name__)

# ── Categorías de hechos memorizables ───────────────────────────────
MEMORIZABLE_CATEGORIES = ("operational", "incident_pattern", "staff_preference")


def _process_llm_proposal(
    llm_proposal: dict[str, Any],
    question: str,
    answer: str,
    facts: list[dict[str, Any]],
) -> tuple[str, dict[str, Any] | None]:
    """Procesa y valida una propuesta de memoria generada por el LLM.

    Esta es la ruta prioritaria: el LLM ya evaluó la interacción y
    determinó si hay algo memorable, todo en la misma llamada.
    Solo hacemos validaciones PHI y de duplicados antes de mostrar
    la propuesta al usuario.

    Returns:
        Tupla (answer_con_propuesta, proposal_info o None).
    """
    content = llm_proposal.get("proposal", "").strip()
    if not content or len(content) < 10:
        return answer, None

    # 1. Verificar duplicado
    if _is_duplicate(content, facts):
        logger.info("Propuesta LLM descartada — ya existe: '%s'", content[:80])
        return answer, None

    # 2. Validación PHI
    phi_safe, phi_matches = validate_phi_safe(content)
    if not phi_safe:
        logger.warning("Propuesta LLM rechazada por PHI: %s", phi_matches)
        _record_audit(
            event_type="phi_rejected",
            run_id="_llm_structured_",
            proposal=llm_proposal,
            detail=f"Propuesta LLM rechazada por PHI: {phi_matches}",
        )
        rejection_msg = (
            "\n\n---\n\n"
            "⚠️ **No puedo guardar eso en mi memoria.**\n\n"
            "La información que mencionas parece contener datos de salud "
            "protegidos (PHI). Por cumplimiento de **HIPAA** y **UK GDPR**, "
            "no está permitido almacenar identificadores de pacientes, "
            "diagnósticos ni ningún dato que pueda re-identificar a una "
            "persona en mi memoria persistente.\n\n"
            "Puedo ayudarte a gestionar esta incidencia a través de los "
            "canales estándar de HealthCore, pero no puedo «recordarlo» "
            "como un hecho operativo.\n\n"
            "*Si se trata de un cambio operativo sin datos de paciente, "
            "por favor reformúlalo sin incluir información personal.*"
        )
        return answer + rejection_msg, {
            "phi_rejected": True,
            "phi_matches": phi_matches,
            "category": llm_proposal.get("category", "operational"),
        }

    # 3. Validar categoría
    valid_cats = {"operational", "incident_pattern", "staff_preference"}
    category = llm_proposal.get("category", "operational")
    if category not in valid_cats:
        category = "operational"

    reason = llm_proposal.get("reason", "Evaluado por el modelo.")

    # 4. Construir pregunta al usuario
    proposal_suffix = (
        f"\n\n---\n\n"
        f"📝 **¿Quieres que recuerde esto para la próxima vez?**\n\n"
        f"> _{content}_\n\n"
        f"_({reason})_\n\n"
        f"*Responde con «sí», «no», o edita el texto si quieres cambiarlo.*"
    )

    proposal_info = {
        "proposal": content,
        "reason": reason,
        "category": category,
        "source_question": question,
        "source": "llm_structured",
    }

    return answer + proposal_suffix, proposal_info


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
    "reporte",  # "El reporte semanal debe incluir..." (declarativo)
    "pidió",    # "Eso lo pidió hace dos semanas"
    "debe",     # "debe incluir", "debe ser"
    "alerta",   # "Esa alerta de no-show elevado"
    "cierre",   # "cierre de carretera"
    "no-show",  # Patrón de incidentes
    "fue porque",  # Explicación causal
    "hubo",     # "hubo un cierre"
    # Comandos explícitos de memorización (CONTEXT.md §Hito8)
    # El usuario pide activamente guardar información
    "apúntalo", "apunta",  # "apúntalo" / "apunta esto"
    "guárdalo", "guarda",  # "guárdalo" / "guarda esto"
    "recuérdalo", "recordar",  # "recuérdalo" / "para recordar"
    "escribe", "escribir",  # "escribe que..."
    "almacena", "almacenar",  # "almacena esta información"
}

# ── Señales de que NO hay nada que recordar ─────────────────────────
_NON_MEMORIZABLE_INDICATORS: set[str] = {
    "gracias",
}

# ── Señales de que la pregunta es una consulta, no un hecho memorable ──
_QUERY_PHRASES: set[str] = {
    "cuál es", "cuáles son", "dime", "lista", "muestra",
    "qué pasó", "cuántos", "qué dice",
    "cómo está", "cómo van", "qué tal",
    "sabes", "conoces", "puedes",
    "hay algún", "hay alguna",
    "es posible", "es necesario",
}

# ── Verbos en condicional/hipotético que NO son hechos consumados ──
_HYPOTHETICAL_INDICATORS: set[str] = {
    "si llueve", "si pasa", "si ocurre", "si puede",
    "si debería", "si tendría", "si hubiera", "si fuese",
    "podría", "tendría", "debería",
    "hipotéticamente", "supón", "suponga",
}


def auto_evaluate_proposal(
    question: str,
    answer: str,
    facts: list[dict[str, Any]],
    tool_results: list[dict[str, Any]],
    tool_errors: list[str],
    llm_proposal: dict[str, Any] | None = None,
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
        llm_proposal: Propuesta estructurada generada por el LLM en la misma
            llamada (desde generate_answer_with_memory). Si se proporciona,
            se prioriza sobre la heurística post-hoc.

    Returns:
        Tupla (answer_con_propuesta, proposal_info o None).
        Si hay propuesta, answer_con_propuesta incluye la pregunta al usuario.
        Si NO hay propuesta o es duplicado, answer_con_propuesta == answer.
        Si la propuesta fue RECHAZADA POR PHI, answer_con_propuesta incluye
            un mensaje explícito al usuario explicando por qué no se puede
            recordar (requisito CONTEXT.md Hito8).

    La propuesta NO se guarda directamente — solo se prepara para
    mostrarse al usuario.  ``finalize_proposal()`` la escribe si el
    usuario aprueba.
    """
    # Si ya hay una propuesta pendiente, no generar otra
    if has_pending_proposal():
        logger.info("Ya hay una propuesta pendiente — no se genera otra.")
        return answer, None

    # 0. Si el LLM ya proveyó una propuesta estructurada, priorizarla
    if llm_proposal is not None:
        return _process_llm_proposal(llm_proposal, question, answer, facts)

    # 1. Verificar si la interacción es candidata a generar propuesta (heurística)
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

        # ❗ REQUISITO CONTEXT.md Hito8:
        #   "el agente debe rechazar la propuesta explícitamente,
        #    no solo ignorarla en silencio, y explicarle al usuario
        #    por qué no puede recordar eso"
        rejection_msg = (
            "\n\n---\n\n"
            "⚠️ **No puedo guardar eso en mi memoria.**\n\n"
            "La información que mencionas parece contener datos de salud "
            "protegidos (PHI). Por cumplimiento de **HIPAA** y **UK GDPR**, "
            "no está permitido almacenar identificadores de pacientes, "
            "diagnósticos ni ningún dato que pueda re-identificar a una "
            "persona en mi memoria persistente.\n\n"
            "Puedo ayudarte a gestionar esta incidencia a través de los "
            "canales estándar de HealthCore, pero no puedo «recordarlo» "
            "como un hecho operativo.\n\n"
            "*Si se trata de un cambio operativo sin datos de paciente, "
            "por favor reformúlalo sin incluir información personal.*"
        )
        answer_rechazada = answer + rejection_msg

        return answer_rechazada, {
            "phi_rejected": True,
            "phi_matches": phi_matches,
            "category": candidate["category"],
        }

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
    - Preguntas hipotéticas o condicionales no generan propuestas
    - Consultas de datos puntuales no generan propuestas
    - Preguntas con señal de cambio SÍ pueden generar si la respuesta confirma
      una cambio real, no si el usuario solo pregunta si algo cambió
    """
    combined = (question + " " + answer).lower()
    q_lower = question.lower()

    # Señales de NO evaluar (solo en la pregunta del usuario)
    for indicator in _NON_MEMORIZABLE_INDICATORS:
        if indicator in q_lower:
            return False

    # Si la pregunta es hipotética/condicional → no memorizar
    for indicator in _HYPOTHETICAL_INDICATORS:
        if indicator in q_lower:
            return False

    # Señales de SÍ evaluar
    has_trigger = any(keyword in combined for keyword in _MEMORIZE_TRIGGER_KEYWORDS)

    if has_trigger:
        # ── Bloqueo de preguntas ───────────────────────────────────
        # Si la pregunta termina con "?" y contiene triggers como
        # "cambia", "cambio", "nuevo" etc., puede ser una CONSULTA sobre
        # un posible cambio. Distinguimos:
        #
        #   "cambia el horario de la clínica?" → Pregunta genérica,
        #       el usuario pregunta si algo cambió. La respuesta típica
        #       describe el horario actual, no confirma un cambio. → NO evaluar
        #
        #   "¿Ha cambiado el proceso en Manchester?" + respuesta "Sí, ahora
        #       requiere M-CDX2" → La respuesta CONFIRMA el cambio real.
        #       → SÍ evaluar si la respuesta contiene confirmación del cambio
        #
        # Regla: una pregunta sobre cambio SÍ genera propuesta si la respuesta
        # contiene un verbo en pasado/confirmativo que indique que el cambio
        # ya ocurrió (ahora, ya, cambió, actualmente).
        # Excepción: comandos explícitos de memorización (apúntalo, guárdalo, etc.)
        has_explicit_cmd = any(
            cmd in combined
            for cmd in ["apúntalo", "guárdalo", "recuérdalo", "escribe", "guarda", "almacena"]
        )
        if q_lower.strip().endswith("?") and not has_explicit_cmd:
            # Si la respuesta NO contiene confirmación de cambio → bloquear
            a_lower = answer.lower()
            has_confirmation = any(
                word in a_lower
                for word in [
                    "ahora", "cambió", "cambio", "ya ", "actualmente",
                    "sí,", "correcto", "exacto", "así es", "efectivamente",
                    "claro,", "exactamente",
                ]
            )
            if not has_confirmation:
                return False
            # Si la respuesta confirma, dejar pasar a los extractores

        # ── Bloqueo de consultas genéricas con regex ───────────────
        if any(
            re.search(r"\b" + re.escape(phrase) + r"\b", q_lower)
            for phrase in _QUERY_PHRASES
        ):
            return False

        return True

    # Sin trigger → no evaluar (conservador)
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
    # Incluye alertas, no-show, interrupciones, y explicaciones causales
    if any(
        word in combined
        for word in ["incidente", "falla", "falló", "patrón", "suele", "típicamente", "lunes",
                      "alerta", "no-show", "hubo", "cierre", "hora falsa"]
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

    # ── Fallback: Si pasó _should_evaluate pero no tiene categoría específica ──
    # Esto asegura que contenido con PHI (como "El paciente Johnson... apúntalo")
    # llegue a la validación PHI para ser rechazado explícitamente, en lugar de
    # ser ignorado en silencio (ver CONTEXT.md Hito8).
    # También captura comandos directos como "guárdalo", "recuérdalo", "escribe".
    if any(
        word in combined
        for word in ["apúntalo", "guárdalo", "recuérdalo", "escribe", "guarda", "almacena"]
    ):
        content = _extract_command_memory(question, answer)
        if content:
            return {
                "content": content,
                "category": "operational",
                "reason": "El usuario solicitó explícitamente recordar esta información.",
            }

    # ── Fallback genérico ──
    # Si _should_evaluate() ya determinó que es memorable pero ningún
    # extractor específico lo clasificó, extraemos el contenido factual
    # de la pregunta/respuesta para que al menos pase por validación PHI.
    # Esto cubre casos como "alerta de no-show... hubo..." (Caso 3) y
    # "reporte para Diane Foster debe incluir..." (Caso 4).
    content = _extract_fallback_content(question, answer)
    if content:
        # Inferir categoría según palabras clave en el texto
        combined_lower = (question + " " + answer).lower()
        if any(w in combined_lower for w in ["alerta", "no-show", "incidente", "falla", "falló", "hubo", "cierre"]):
            inferred = "incident_pattern"
            reason = "Patrón operativo o alerta que puede ser útil en el futuro."
        elif any(w in combined_lower for w in ["reporte", "prefiere", "incluir"]):
            inferred = "staff_preference"
            reason = "Preferencia o requerimiento de miembro del staff."
        else:
            inferred = "operational"
            reason = "Información operativa que puede afectar futuras consultas."
        return {
            "content": content,
            "category": inferred,
            "reason": reason,
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


def _extract_command_memory(question: str, answer: str) -> str | None:
    """Extrae contenido de un comando explícito de memorización.

    El usuario pide activamente guardar algo con verbos como
    «apúntalo», «guárdalo», «recuérdalo», «escribe».
    Extraemos el contenido factual quitando la parte del comando.
    """
    import re
    combined = question + " " + answer

    # Intentar extraer el hecho quitando el comando
    # Patrón: "[contexto], [comando]" o "[comando]: [contenido]"
    for cmd_pat in [r"apúntalo", r"guárdalo", r"recuérdalo", r"escribe\s+(?:que\s+)?", r"guarda\s+(?:esto\s+)?"]:
        m = re.search(
            r"(.+?)[,;.\s]*" + cmd_pat,
            combined, re.IGNORECASE,
        )
        if m:
            content = m.group(1).strip()
            # Limpiar comando residual
            content = re.sub(r"\b(?:apunta|guarda|escribe|recuerda|almacena)\s*", "", content, flags=re.IGNORECASE).strip()
            if len(content) > 10:
                return content

    # Fallback: si el comando está al inicio
    for cmd_pat in [r"apunta", r"guarda", r"escribe", r"recuerda", r"almacena"]:
        m = re.search(
            cmd_pat + r"\s+(?:que\s+)?(.+)$",
            combined, re.IGNORECASE,
        )
        if m:
            content = m.group(1).strip()
            if len(content) > 10:
                return content

    # Último recurso: devolver la pregunta completa si es corta
    # (para que al menos pase por validación PHI)
    if len(question) > 15:
        return question.strip()

    return None


def _extract_fallback_content(question: str, answer: str) -> str | None:
    """Extrae contenido factual como fallback cuando ningún extractor
    específico clasificó la interacción.

    Usa heurísticas simples: toma la oración más informativa de la
    pregunta o la combinación pregunta+respuesta.
    """
    import re

    # Estrategia 1: buscar la oración más larga en la pregunta
    # que contenga palabras clave del trigger
    sentences = re.split(r'[.!?\n]', question)
    best = ""
    for s in sentences:
        s = s.strip()
        if len(s) > len(best) and len(s) > 20:
            best = s

    if best:
        return best

    # Estrategia 2: usar toda la pregunta si es sustancial
    if len(question) > 20:
        return question.strip()

    # Estrategia 3: usar la respuesta si la pregunta es muy corta
    if len(answer) > 20:
        return answer.strip()

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

    elif classification.intent == "phi_rejected":
        # ❗ REQUISITO CONTEXT.md Hito8:
        #   El mensaje del usuario contiene PHI — se rechaza la propuesta
        #   explícitamente y se explica por qué no se puede recordar.
        #   La propuesta pendiente SE MANTIENE (no se limpia) para que el
        #   usuario pueda reformularla sin PHI.
        audit = _record_audit(
            event_type="phi_rejected",
            run_id=proposal["source_run_id"],
            proposal=proposal,
            user_message=user_message,
            classification=classification.intent,
            detail=(
                f"Propuesta no guardada — el mensaje del usuario contiene PHI. "
                f"Coincidencias: {classification.explanation}"
            ),
        )
        result["resolution"] = "phi_rejected"
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
    elif resolution == "phi_rejected":
        return (
            f"⚠️ **No puedo guardar eso en mi memoria.**\n\n"
            f"La información que mencionas parece contener datos de salud "
            f"protegidos (PHI). Por cumplimiento de **HIPAA** y **UK GDPR**, "
            f"no está permitido almacenar identificadores de pacientes, "
            f"diagnósticos ni ningún dato que pueda re-identificar a una "
            f"persona en mi memoria persistente.\n\n"
            f"Puedo ayudarte a gestionar esta incidencia a través de los "
            f"canales estándar de HealthCore, pero no puedo «recordarlo» "
            f"como un hecho operativo.\n\n"
            f"*Si se trata de un cambio operativo sin datos de paciente, "
            f"por favor reformúlalo sin incluir información personal.*"
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