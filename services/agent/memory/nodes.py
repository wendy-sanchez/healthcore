"""Nodos de memoria para el grafo LangGraph del agente de soporte.

Estos nodos se añaden al grafo existente para extenderlo con
capacidad de memoria persistente.

Nuevos nodos:
  - ``load_memory_context`` — Carga hechos de memoria al inicio de la corrida
  - ``auto_evaluate_proposal`` — Evalúa si la interacción merece propuesta
  - ``check_pending_proposal`` — Verifica si hay propuesta pendiente al inicio
  - ``process_user_proposal_response`` — Procesa la respuesta del usuario a propuesta
  - ``generate_answer_with_memory`` — Genera respuesta considerando memoria

Nuevas aristas:
  - ``load_memory_context`` después de la clasificación (antes de RAG/tools)
  - ``check_pending_proposal`` en el entry point
  - Procesamiento de propuesta pendiente antes de responder pregunta nueva

Estado ampliado:
  - ``memory_facts``: hechos recuperados de memoria
  - ``memory_proposal``: propuesta pendiente (si existe)
  - ``memory_proposal_handled``: si la propuesta se manejó en este turno
  - ``memory_pending_since``: cuándo se creó la propuesta pendiente
"""

from __future__ import annotations

import logging
from typing import Any

from services.agent.memory.store import (
    _now,
    get_all_facts,
    get_pending_proposal,
    has_pending_proposal,
    set_pending_proposal,
)
from services.agent.memory.pipeline import (
    auto_evaluate_proposal as _auto_evaluate,
    build_memory_context,
    format_memory_confirmation,
    process_user_response,
    memory_status_summary,
)

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════
#  Nodos de memoria
# ═══════════════════════════════════════════════════════════════════════


def node_load_memory_context(state: dict[str, Any]) -> dict[str, Any]:
    """Carga los hechos de memoria al inicio de la corrida.

    Este nodo se ejecuta después de ``receive_question`` y antes de
    ``classify_query``.  Los hechos de memoria se cargan y se añaden
    al estado para que el agente pueda usarlos al responder.

    Si hay una propuesta pendiente (de una conversación anterior),
    este nodo la detecta y marca el estado para que se procese
    antes de responder.
    """
    facts = get_all_facts()
    memory_context = build_memory_context(facts)

    result: dict[str, Any] = {
        "memory_facts": facts,
        "memory_context": memory_context,
    }

    # Verificar si hay propuesta pendiente
    if has_pending_proposal():
        proposal = get_pending_proposal()
        result["memory_proposal"] = proposal
        result["memory_proposal_handled"] = False
        result["routing_log"] = [
            "check_pending_proposal → process_proposal (propuesta pendiente detectada)"
        ]
        logger.info("Propuesta pendiente detectada (run_id: %s)", proposal.get("source_run_id"))
    else:
        result["memory_proposal"] = None
        result["memory_proposal_handled"] = True
        result["routing_log"] = [
            "check_pending_proposal → continue (sin propuesta pendiente)"
        ]

    # Registrar trace event
    from services.agent.graph import _make_trace_event
    result["trace_events"] = [
        _make_trace_event(
            "load_memory_context", "ok",
            {"num_memory_facts": len(facts), "has_pending_proposal": has_pending_proposal()},
        )
    ]

    return result


def node_process_proposal(state: dict[str, Any]) -> dict[str, Any]:
    """Procesa el mensaje actual del usuario como respuesta a una propuesta pendiente.

    Este nodo se ejecuta cuando hay una propuesta pendiente de una
    conversación anterior y el usuario envía un mensaje.  Clasifica
    la intención y ejecuta la acción correspondiente.

    Después de procesar la propuesta, la conversación continúa con
    normalidad — el mensaje del usuario puede contener tanto la
    respuesta a la propuesta como una nueva pregunta.
    """
    question = state.get("question", "")
    proposal = state.get("memory_proposal")

    if not proposal:
        return {
            "memory_proposal_handled": True,
            "routing_log": ["process_proposal → skip (sin propuesta pendiente)"],
        }

    # Procesar la respuesta del usuario
    result = process_user_response(question)

    # Construir confirmación
    confirmation = format_memory_confirmation(
        result["resolution"], proposal
    )

    # Si la respuesta contenía también una nueva pregunta (no solo la respuesta a la propuesta),
    # la preservamos.  Esto permite que el usuario responda "sí, ¿y qué hay del inventario?"
    memory_proposal_handled = confirmation or ""

    from services.agent.graph import _make_trace_event
    return {
        "memory_proposal_handled": True,
        "memory_proposal_handled_text": memory_proposal_handled,
        "memory_proposal_result": result,
        "memory_proposal": None,  # Ya no hay pendiente
        "routing_log": [
            f"process_proposal → {result['resolution']} "
            f"({(result.get('final_content') or '')[:60]})"
        ],
        "trace_events": [
            _make_trace_event(
                "process_proposal", "ok",
                {
                    "resolution": result["resolution"],
                    "proposal": proposal.get("proposal", "")[:80],
                },
            )
        ],
    }


def node_auto_evaluate_memory(state: dict[str, Any]) -> dict[str, Any]:
    """Auto-evalúa si la interacción actual merece una propuesta de memoria.

    Este nodo se ejecuta DESPUÉS de que el agente ha generado su respuesta
    (en ``generate_answer`` o ``synthesize_tool_answer``).  Si no hay una
    propuesta pendiente, evalúa si la interacción contiene algo memorable.

    Si hay propuesta, la incluye al final de la respuesta como pregunta
    al usuario.
    """
    # No evaluar si ya hay propuesta pendiente o si ya se manejó una
    if has_pending_proposal() or state.get("memory_proposal_handled", False):
        return {}

    question = state.get("question", "")
    answer = state.get("answer", "")
    facts = state.get("memory_facts", get_all_facts())
    tool_results = state.get("tool_results", [])
    tool_errors = state.get("tool_errors", [])

    # Auto-evaluar
    answer_with_proposal, proposal_info = _auto_evaluate(
        question=question,
        answer=answer,
        facts=facts,
        tool_results=tool_results,
        tool_errors=tool_errors,
    )

    result: dict[str, Any] = {}

    if proposal_info:
        # Guardar propuesta en BD
        saved = set_pending_proposal(
            proposal=proposal_info["proposal"],
            reason=proposal_info["reason"],
            category=proposal_info["category"],
            source_run_id=state.get("run_id", "_unknown_"),
            source_question=proposal_info["source_question"],
        )
        if saved:
            result["answer"] = answer_with_proposal
            result["memory_proposal"] = saved
            result["memory_proposal_handled"] = False
            result["routing_log"] = [
                f"auto_evaluate_memory → propuesta creada "
                f"(categoría: {proposal_info['category']})"
            ]
        else:
            # No se pudo guardar (probablemente PHI)
            result["routing_log"] = [
                "auto_evaluate_memory → propuesta rechazada (PHI)"
            ]
    else:
        result["routing_log"] = [
            "auto_evaluate_memory → nada que recordar"
        ]

    from services.agent.graph import _make_trace_event
    result["trace_events"] = [
        _make_trace_event(
            "auto_evaluate_memory", "ok",
            {
                "had_proposal": proposal_info is not None,
                "category": proposal_info.get("category") if proposal_info else None,
            },
        )
    ]

    return result


# ═══════════════════════════════════════════════════════════════════════
#  Nuevas aristas condicionales
# ═══════════════════════════════════════════════════════════════════════


def route_after_load_memory(state: dict[str, Any]) -> str:
    """Decide el siguiente paso después de cargar memoria.

    Si hay propuesta pendiente → ``process_proposal``.
    Si no → ``classify_query`` (flujo normal).
    """
    if state.get("memory_proposal") is not None and not state.get("memory_proposal_handled", True):
        return "process_proposal"
    return "classify_query"


def route_after_process_proposal(state: dict[str, Any]) -> str:
    """Después de procesar propuesta, decide si continuar o terminar.

    Si el mensaje del usuario era solo respuesta a la propuesta (sin
    pregunta nueva), la conversación termina aquí.
    Si el mensaje contenía también una pregunta nueva, continúa.
    """
    question = state.get("question", "").strip()

    # Si no hay pregunta nueva, terminar
    if not question or len(question) < 3:
        return "__end__"

    # Si hay pregunta nueva, clasificar
    return "classify_query"


def route_after_auto_evaluate(state: dict[str, Any]) -> str:
    """Después de auto-evaluar, siempre va a END (es el nodo final)."""
    return "__end__"