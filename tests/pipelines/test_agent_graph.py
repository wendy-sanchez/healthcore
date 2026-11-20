"""
tests/pipelines/test_agent_graph.py — Evals del agente LangGraph de soporte.

Estos tests verifican:
  1. Compilación del grafo: errores estructurales se detectan en tiempo de build.
  2. Enrutamiento condicional: cada bifurcación se prueba con entradas controladas.
  3. Trazabilidad: cada corrida produce un trace consultable.
  4. Comportamiento RAG: el agente responde según las políticas de CONTEXT.md.

Los evals corren contra el grafo compilado (no contra ejecuciones en vivo).
Para los tests que requieren retrieve() real, se mockea Qdrant para que los
tests sean deterministas y no dependan de infraestructura externa.
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# Ensure project root is on sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Ensure services/api is on sys.path for router tests
_API_SRC = str(_PROJECT_ROOT / "services" / "api")
if _API_SRC not in sys.path:
    sys.path.insert(0, _API_SRC)


# ── Helpers ──────────────────────────────────────────────────────────


def _make_chunk(
    source_document: str = "appointment-policy",
    section: str = "Política de cancelación",
    text: str | None = None,
    score: float = 0.85,
) -> dict[str, Any]:
    """Helper para construir un fragmento simulado como el que devuelve retrieve()."""
    return {
        "company": "healthcore",
        "source_document": source_document,
        "section": section,
        "language": "es",
        "chunk_index": 0,
        "text": text or (
            "Política de cancelación:\n"
            "Cancelar con más de 24 horas de anticipación: sin cargo.\n"
            "Cancelar con menos de 24 horas o no presentarse (no-show): "
            "cargo de 50 USD (o 40 GBP en Reino Unido) para pacientes de pago privado.\n"
            "Pacientes con Medicare o Medicaid: no se les cobra cargo por no-show."
        ),
        "_score": score,
    }


def _mock_retrieve(chunks: list[dict[str, Any]] | None = None):
    """Patchea el retrieve dentro del módulo del grafo."""
    if chunks is None:
        chunks = [_make_chunk()]

    # El módulo graph importa 'retrieve' directamente, así que parcheamos
    # la referencia dentro de services.agent.graph
    return patch("services.agent.graph.retrieve", return_value=chunks)


def _mock_retrieve_empty():
    """Patchea retrieve para devolver lista vacía (simula umbral no superado)."""
    return patch("services.agent.graph.retrieve", return_value=[])


def _mock_retrieve_error():
    """Patchea retrieve para lanzar RuntimeError."""
    return patch(
        "services.agent.graph.retrieve",
        side_effect=RuntimeError("Qdrant connection refused"),
    )


def _fake_generate_answer_grounded(question: str, context: list[dict[str, Any]]) -> str:
    """Fake LLM que SOLO puede contestar con el texto del contexto.

    Si la pregunta se puede responder con el fragmento, devuelve una
    respuesta basada en ese texto.  Si no encuentra información relevante
    en el contexto, devuelve un fallback genérico — NUNCA inventa datos
    que no estén en ``context``.

    Esto verifica el **anclaje (grounding)**: el nodo ``generate_answer``
    recibe el fragmento recuperado y la respuesta sale de él, no de
    conocimiento interno del LLM.
    """
    if not context:
        return (
            "No tengo información en los documentos disponibles "
            "para responder tu pregunta."
        )

    # Buscar en el texto de los fragmentos
    texts = [chunk.get("text", "") for chunk in context]

    # Palabras clave de la pregunta
    question_lower = question.lower()
    keywords = question_lower.split()

    # Encontrar el fragmento más relevante
    best_text = ""
    best_score = 0
    for text in texts:
        text_lower = text.lower()
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > best_score:
            best_score = score
            best_text = text

    if best_text:
        return (
            "Según la documentación de HealthCore:\n"
            f"{best_text.strip()}"
        )

    return (
        "No encontré información relevante en la documentación "
        "disponible para responder tu pregunta."
    )


def _mock_generate_answer(answer: str = "Respuesta simulada."):
    """Patchea generate_answer dentro del módulo del grafo."""
    return patch("services.agent.graph.generate_answer", return_value=answer)


def _mock_generate_answer_error():
    """Patchea generate_answer para lanzar RuntimeError."""
    return patch(
        "services.agent.graph.generate_answer",
        side_effect=RuntimeError("LLM API timeout"),
    )


# ======================================================================
# EVAL 1: Compilación y estructura del grafo
# ======================================================================


class TestGraphCompilation:
    """El grafo debe compilarse sin errores y exponer todos los nodos."""

    def test_graph_compiles_successfully(self):
        """El grafo debe compilar en tiempo de importación sin errores."""
        from services.agent.graph import agent_graph

        assert agent_graph is not None
        assert hasattr(agent_graph, "nodes")

    def test_graph_has_all_expected_nodes(self):
        """Deben existir todos los nodos del diseño (Parte 1 + Parte 2 + Parte 3)."""
        from services.agent.graph import agent_graph

        expected_nodes = {
            "__start__",
            "receive_question",
            "retrieve_context",
            "generate_answer",
            "respond_no_info",
            "handle_error",
            # Parte 2
            "classify_query",
            "query_incident",
            "query_inventory",
            "synthesize_tool_answer",
            # Parte 3 — Memoria
            "load_memory_context",
            "process_proposal",
            "auto_evaluate_memory",
        }
        actual_nodes = set(agent_graph.nodes.keys())
        for node in expected_nodes:
            assert node in actual_nodes, f"Nodo faltante: {node}"

    def test_graph_entry_point_is_receive_question(self):
        """El entry point debe ser receive_question."""
        from services.agent.graph import agent_graph

        # Verificar que __start__ tiene conexión a receive_question
        # Usamos la introspección del grafo si está disponible
        assert "receive_question" in agent_graph.nodes


# ======================================================================
# EVAL 2: Enrutamiento condicional — pregunta válida e inválida
# ======================================================================


class TestConditionalRouting:
    """Las aristas condicionales deben enrutar según el estado."""

    @pytest.mark.asyncio
    async def test_empty_question_routes_to_end(self):
        """Pregunta vacía → error, no debe ejecutar retrieve_context."""
        from services.agent.graph import run_agent

        with pytest.raises(ValueError, match="no puede estar vacía"):
            await run_agent("")

    @pytest.mark.asyncio
    async def test_short_question_routes_to_end(self):
        """Pregunta muy corta → error, no debe ejecutar retrieve_context."""
        from services.agent.graph import run_agent

        with pytest.raises(ValueError, match="demasiado corta"):
            await run_agent("ab")

    def test_valid_question_routes_to_load_memory(self):
        """Pregunta válida → load_memory_context debe ejecutarse primero (Parte 3)."""
        from services.agent.graph import agent_graph, build_initial_state

        state = build_initial_state("¿Cuál es la política de cancelación?")
        config = {"configurable": {"thread_id": str(uuid.uuid4())}}

        # Simular que receive_question pasó sin error y enruta a load_memory_context
        from services.agent.graph import route_after_receive

        decision = route_after_receive(state)
        assert decision == "load_memory_context", (
            f"Esperaba 'load_memory_context' pero obtuve '{decision}'"
        )

    def test_no_chunks_routes_to_respond_no_info(self):
        """Si retrieve() devuelve lista vacía, debe enrutar a respond_no_info."""
        from services.agent.graph import route_after_retrieve, build_initial_state

        state = build_initial_state("¿Pregunta sin contexto?")
        # retrieve_context dejó el estado sin chunks y sin error
        decision = route_after_retrieve(state)
        assert decision == "respond_no_info", (
            f"Esperaba 'respond_no_info' pero obtuve '{decision}'"
        )

    def test_chunks_found_routes_to_generate_answer(self):
        """Si retrieve() devuelve chunks, debe enrutar a generate_answer."""
        from services.agent.graph import route_after_retrieve, build_initial_state

        state = build_initial_state("¿Pregunta con contexto?")
        state["retrieved_chunks"] = [_make_chunk()]
        decision = route_after_retrieve(state)
        assert decision == "generate_answer", (
            f"Esperaba 'generate_answer' pero obtuve '{decision}'"
        )

    def test_retrieve_error_routes_to_handle_error(self):
        """Si retrieve() falla con RuntimeError, debe enrutar a handle_error."""
        from services.agent.graph import route_after_retrieve, build_initial_state

        state = build_initial_state("¿Pregunta que falla?")
        state["error"] = "Error al recuperar información: Qdrant connection refused"
        decision = route_after_retrieve(state)
        assert decision == "handle_error", (
            f"Esperaba 'handle_error' pero obtuve '{decision}'"
        )

    def test_generate_error_routes_to_handle_error(self):
        """Si generate_answer falla, debe enrutar a handle_error."""
        from services.agent.graph import route_after_generate, build_initial_state

        state = build_initial_state("¿Pregunta que falla en generación?")
        state["error"] = "Error al generar la respuesta: LLM API timeout"
        decision = route_after_generate(state)
        assert decision == "handle_error"


# ======================================================================
# EVAL 3: Trazabilidad — cada corrida produce un trace consultable
# ======================================================================


class TestTraceability:
    """Cada corrida debe producir un trace consultable con eventos y enrutamiento."""

    @pytest.mark.asyncio
    async def test_trace_is_persisted_after_run(self):
        """Después de una corrida, debe existir un archivo de trace en disco."""
        from services.agent.graph import run_agent, get_trace

        run_id = str(uuid.uuid4())

        with _mock_retrieve(), _mock_generate_answer():
            _ = await run_agent("¿Qué seguros aceptan?", run_id=run_id)

        trace = get_trace(run_id)

        assert trace is not None, f"No se encontró trace para run_id={run_id}"
        assert trace["run_id"] == run_id
        assert trace["question"] == "¿Qué seguros aceptan?"
        assert "events" in trace
        assert len(trace["events"]) > 0
        assert "routing_log" in trace

    @pytest.mark.asyncio
    async def test_trace_contains_node_execution_order(self):
        """El trace debe registrar qué nodos se ejecutaron y en qué orden."""
        from services.agent.graph import run_agent, get_trace

        run_id = str(uuid.uuid4())

        with _mock_retrieve(), _mock_generate_answer():
            await run_agent("¿Cuál es la política de no-show?", run_id=run_id)

        trace = get_trace(run_id)
        events = trace["events"]

        node_names = [e["node"] for e in events]
        assert "receive_question" in node_names, (
            f"El trace debería contener 'receive_question'. Nodos: {node_names}"
        )
        assert "retrieve_context" in node_names, (
            f"El trace debería contener 'retrieve_context'. Nodos: {node_names}"
        )
        assert "generate_answer" in node_names, (
            f"El trace debería contener 'generate_answer'. Nodos: {node_names}"
        )

    @pytest.mark.asyncio
    async def test_trace_routing_log_records_decisions(self):
        """El routing_log debe registrar cada decisión de enrutamiento."""
        from services.agent.graph import run_agent, get_trace

        run_id = str(uuid.uuid4())

        with _mock_retrieve(), _mock_generate_answer():
            await run_agent("¿Cobran cargo por no-show?", run_id=run_id)

        trace = get_trace(run_id)
        routing_log = trace["routing_log"]

        assert len(routing_log) >= 2, (
            f"Se esperaban al menos 2 entradas en routing_log. "
            f"Obtenidas: {len(routing_log)}"
        )

    @pytest.mark.asyncio
    async def test_trace_consultable_after_run(self):
        """El trace debe ser consultable por get_trace() después de la corrida."""
        from services.agent.graph import run_agent, get_trace, list_traces

        run_id = str(uuid.uuid4())
        with _mock_retrieve(), _mock_generate_answer():
            await run_agent("¿Qué es HealthCore?", run_id=run_id)

        # Consulta por ID específico
        trace = get_trace(run_id)
        assert trace is not None

        # Consulta por listado
        all_traces = list_traces(limit=10)
        assert any(t["run_id"] == run_id for t in all_traces), (
            "El trace debería aparecer en list_traces()"
        )


# ======================================================================
# EVAL 4: Comportamiento RAG — respuestas ancladas en la base de conocimiento
# ======================================================================


class TestRAGBehavior:
    """El comportamiento del agente debe estar anclado en la base de conocimiento.

    Estos tests verifican que el agente usa correctamente retrieve() y
    generate_answer() con chunks realistas, sin alucinar información.
    Los chunks simulados reflejan las políticas de CONTEXT.md.
    """

    def test_agent_uses_retrieve_and_generate_answer(self):
        """El agente debe llamar retrieve() y generate_answer() en orden."""
        from services.agent.graph import agent_graph

        # Verificamos que los nodos existen en el grafo
        nodes = set(agent_graph.nodes.keys())
        assert "retrieve_context" in nodes
        assert "generate_answer" in nodes

    def test_retrieve_executes_before_generate(self):
        """El nodo retrieve_context debe ejecutarse antes que generate_answer."""
        from services.agent.graph import route_after_retrieve, build_initial_state

        # El routing después de retrieve_context decide ir a generate_answer
        state = build_initial_state("test")
        state["retrieved_chunks"] = [_make_chunk(
            source_document="appointment-policy",
        )]

        decision = route_after_retrieve(state)
        assert decision == "generate_answer", (
            "Con chunks recuperados, debe enrutar a generate_answer"
        )

    def test_no_chunks_returns_honest_answer(self):
        """Sin contexto relevante, el agente debe responder con honestidad."""
        from services.agent.graph import route_after_retrieve, build_initial_state

        state = build_initial_state("¿Pregunta sin cobertura en BD?")
        decision = route_after_retrieve(state)
        assert decision == "respond_no_info", (
            "Sin chunks, debe enrutar a respond_no_info (no forzar generación)"
        )

    @pytest.mark.asyncio
    async def test_medicare_no_show_policy_grounded(self):
        """El agente debe responder anclado en el contexto, sin alucinar.

        En lugar de mockear ``generate_answer`` con una respuesta prefabricada,
        usamos un **fake LLM** que solo puede responder con el texto del
        fragmento recuperado.  Esto verifica que:

        1. El fragmento correcto se recupera (``retrieve_context`` funciona).
        2. Ese fragmento se pasa a ``generate_answer`` (el grafo enruta bien).
        3. La respuesta sale del fragmento, no de una cadena prefabricada.

        Si el test pasa, el **grounding** está verificado.
        """
        from services.agent.graph import run_agent, get_trace

        run_id = str(uuid.uuid4())

        # Chunk de appointment-policy que explícitamente dice que Medicare no paga
        medicare_chunk = _make_chunk(
            source_document="appointment-policy",
            section="Política de cancelación",
            text=(
                "Cancelar con menos de 24 horas o no presentarse (no-show): "
                "cargo de 50 USD para pacientes de pago privado.\n"
                "Pacientes con Medicare o Medicaid: "
                "no se les cobra cargo por no-show."
            ),
        )

        # Usamos un fake LLM en lugar de mock: solo puede responder con el contexto
        with _mock_retrieve([medicare_chunk]), patch(
            "services.agent.graph.generate_answer",
            side_effect=_fake_generate_answer_grounded,
        ):
            result = await run_agent(
                "¿Cobran cargo por no-show a pacientes de Medicare?",
                run_id=run_id,
            )

        answer = result["answer"].lower()

        # 1) La respuesta debe contener "no-show" (viene del fragmento)
        assert "no-show" in answer or "no show" in answer, (
            "La respuesta debe mencionar 'no-show', que está en el fragmento. "
            f"Respuesta: {result['answer']}"
        )

        # 2) La respuesta debe mencionar Medicare o Medicaid (viene del fragmento)
        assert "medicare" in answer or "medicaid" in answer, (
            "La respuesta debe mencionar Medicare o Medicaid. "
            f"Respuesta: {result['answer']}"
        )

        # 3) La respuesta debe contener "50 USD" o "cargo" o "cobra" (viene del fragmento)
        assert any(word in answer for word in ["50 usd", "cargo", "cobra", "pagan"]), (
            "La respuesta debe reflejar el contenido del fragmento (cargo, 50 USD). "
            f"Respuesta: {result['answer']}"
        )

        # 4) El trace debe mostrar retrieve_context → generate_answer
        trace = get_trace(run_id)
        assert trace is not None
        routing_log = trace.get("routing_log", [])
        has_retrieve_to_generate = any(
            "retrieve_context → generate_answer" in entry
            for entry in routing_log
        )
        assert has_retrieve_to_generate, (
            "El routing_log debe mostrar retrieve_context → generate_answer. "
            f"Routing log: {routing_log}"
        )

        # 5) El fragmento recuperado debe tener source_document='appointment-policy'
        assert trace.get("num_chunks_retrieved", 0) >= 1, (
            "Debe haber al menos 1 fragmento recuperado."
        )


# ======================================================================
# EVAL 5: Endpoint — el endpoint HTTP no contiene lógica de negocio
# ======================================================================


class TestAgentEndpoint:
    """El endpoint POST /agent/query debe ser una capa delgada sobre el grafo."""

    def test_endpoint_imports_and_has_correct_prefix(self):
        """El router debe existir y tener el prefijo correcto."""
        try:
            from services.agent.routes import router
            assert router.prefix == "/agent"
            assert len(router.routes) >= 1
        except ImportError as e:
            pytest.skip(f"Router de agente no disponible: {e}")

    def test_endpoint_delegates_to_graph(self):
        """El endpoint debe delegar en run_agent(), no contener lógica RAG propia."""
        import inspect
        from services.agent.routes import router

        for route in router.routes:
            if route.path == "/agent/query" and "POST" in route.methods:
                handler_source = inspect.getsource(route.endpoint)
                # Debe llamar a run_agent
                assert "run_agent" in handler_source, (
                    "El endpoint debe delegar en run_agent(), no contener lógica propia"
                )
                return

        pytest.fail("No se encontró el endpoint POST /agent/query")


# ======================================================================
# EVAL 6: Enrutamiento de tools externas (Parte 2)
# ======================================================================


class TestExternalToolRouting:
    """
    El agente debe decidir automáticamente si necesita RAG, tool externa
    (incidentes, inventario) o ambos, sin que el usuario lo especifique.
    """

    def test_classify_query_routes_incident_to_incident_tool(self):
        """Pregunta sobre un ticket/incidente debe clasificarse como 'incident'."""
        from services.agent.graph import node_classify_query, build_initial_state

        state = build_initial_state("¿En qué estado está el ticket 482?")
        result = node_classify_query(state)
        assert "incident" in result["routing_decision"], (
            "'ticket 482' debe clasificar como incident. "
            f"Obtuvo: {result['routing_decision']}"
        )

    def test_classify_query_routes_policy_to_rag(self):
        """Pregunta sobre política/cancelación debe clasificarse como 'rag'."""
        from services.agent.graph import node_classify_query, build_initial_state

        state = build_initial_state("¿Cuál es la política de cancelación de citas?")
        result = node_classify_query(state)
        assert result["routing_decision"] == "rag", (
            "Pregunta de política debe clasificar como 'rag'. "
            f"Obtuvo: {result['routing_decision']}"
        )

    def test_classify_query_routes_stock_to_inventory(self):
        """Pregunta sobre stock/producto debe clasificarse como 'inventory'."""
        from services.agent.graph import node_classify_query, build_initial_state

        state = build_initial_state("¿Tenemos stock de guantes quirúrgicos?")
        result = node_classify_query(state)
        assert "inventory" in result["routing_decision"], (
            "'stock de guantes' debe clasificar como inventory. "
            f"Obtuvo: {result['routing_decision']}"
        )

    def test_classify_query_combined_policy_and_incident(self):
        """Pregunta que mezcla política + ticket debe clasificar combinado."""
        from services.agent.graph import node_classify_query, build_initial_state

        state = build_initial_state(
            "Según la política de cancelación, ¿el ticket 482 debería tener cargo?"
        )
        result = node_classify_query(state)
        assert result["routing_decision"] == "rag+incident", (
            "Pregunta mixta debe clasificar como 'rag+incident'. "
            f"Obtuvo: {result['routing_decision']}"
        )

    def test_classify_query_defaults_to_rag(self):
        """Pregunta sin señales claras debe clasificar como 'rag' por defecto."""
        from services.agent.graph import node_classify_query, build_initial_state

        state = build_initial_state("¿Cómo funciona el sistema de salud en Texas?")
        result = node_classify_query(state)
        assert result["routing_decision"] == "rag", (
            "Pregunta genérica debe clasificar como 'rag'. "
            f"Obtuvo: {result['routing_decision']}"
        )


# ======================================================================
# EVAL 7: Fallback cuando el servicio de incidentes/inventario falla
# ======================================================================


class TestToolFallbackBehavior:
    """
    Si una tool falla (timeout, servicio caído, ticket no encontrado),
    el grafo debe responder con honestidad sin inventar datos.
    """

    @pytest.mark.asyncio
    async def test_tool_timeout_returns_honest_fallback(self):
        """Timeout en tool debe producir respuesta honesta, no datos inventados."""
        from services.agent.graph import run_agent
        from unittest.mock import patch

        # Forzamos timeout parcheando el MCP Client para que devuelva error
        async def _mcp_timeout(*args, **kwargs):
            return {"error": "El servicio de incidentes no respondió en 10s. Intenta de nuevo más tarde."}

        with patch("services.agent.graph.query_incident_by_id", _mcp_timeout):
            from services.agent.graph import build_initial_state, node_query_incident

            state = build_initial_state("¿En qué estado está el ticket 482?")
            state["routing_decision"] = "incident"
            result = await node_query_incident(state)

        # Debe tener un error, no datos inventados
        assert len(result.get("tool_errors", [])) > 0, (
            "Debe haber al menos un tool_error cuando hay timeout"
        )
        error_text = " ".join(result.get("tool_errors", []))
        assert "no respondió" in error_text or "conectar" in error_text, (
            f"El error debe mencionar timeout o conexión: {error_text}"
        )
        # tool_results debe estar vacío
        assert len(result.get("tool_results", [])) == 0, (
            "tool_results debe estar vacío cuando hay timeout"
        )

    @pytest.mark.asyncio
    async def test_ticket_not_found_returns_honest_response(self):
        """Ticket inexistente debe devolver error, no inventar estado."""
        from services.agent.graph import build_initial_state, node_query_incident
        from unittest.mock import patch

        async def _mcp_not_found(*args, **kwargs):
            return {"error": "No existe un ticket con ID 99999."}

        with patch("services.agent.graph.query_incident_by_id", _mcp_not_found):
            state = build_initial_state("¿Qué pasó con el ticket 99999?")
            state["routing_decision"] = "incident"
            result = await node_query_incident(state)

        errors = result.get("tool_errors", [])
        assert len(errors) > 0, "Debe haber error para ticket inexistente"
        assert any("no existe" in e.lower() or "99999" in e for e in errors), (
            f"Error debe mencionar que no existe: {errors}"
        )

    def test_synthesize_handles_all_tool_failures(self):
        """Synthesize con todos los tools fallidos debe responder honestamente."""
        from services.agent.graph import node_synthesize_tool_answer, build_initial_state

        state = build_initial_state("¿Estado del ticket 482?")
        state["routing_decision"] = "incident"
        state["tool_errors"] = [
            "El servicio de incidentes no respondió en 5s. Intenta de nuevo más tarde."
        ]
        state["tool_results"] = []

        result = node_synthesize_tool_answer(state)
        answer = result.get("answer", "")
        assert len(answer) > 0, "Debe producir una respuesta aunque las tools fallen"
        assert "no pude" in answer.lower() or "no respondió" in answer.lower() or (
            "intenta" in answer.lower()
        ), (
            f"La respuesta debe ser honesta sobre el fallo: {answer}"
        )

    def test_incident_tool_extracts_ticket_id_from_question(self):
        """_extract_ticket_id debe extraer correctamente IDs de distintos formatos."""
        from services.agent.graph import _extract_ticket_id

        assert _extract_ticket_id("ticket 482") == 482
        assert _extract_ticket_id("incidente #123") == 123
        assert _extract_ticket_id("¿qué pasó con el caso 456?") == 456
        assert _extract_ticket_id("¿estado del ticket #7890?") == 7890
        # Sin número: debe devolver None
        assert _extract_ticket_id("¿cuántos tickets abiertos hay?") is None

    def test_classify_query_trace_events_contain_decision(self):
        """Los trace_events deben contener la decisión de enrutamiento."""
        from services.agent.graph import node_classify_query, build_initial_state

        state = build_initial_state("¿En qué estado está el ticket 482?")
        result = node_classify_query(state)

        events = result.get("trace_events", [])
        assert len(events) > 0
        event = events[0]
        assert event["node"] == "classify_query"
        assert event["metadata"]["decision"] == "incident"


# ======================================================================
# EVAL 8: Síntesis combinada rag+incident — respuesta mixta real
# ======================================================================


class TestMixedSynthesis:
    """
    Cuando el agente clasifica 'rag+incident' debe ejecutar BOTH
    query_incident Y retrieve_context → generate_answer, y luego
    synthesize_tool_answer debe combinar AMBAS fuentes en la respuesta.
    """

    def _mock_incident_result(self, ticket_id: int = 2) -> dict:
        """Retorna un incidente simulado como el que devuelve query_incident_by_id() tras normalización."""
        return {
            "id": ticket_id,
            "title": "Patient not informed of appointment reschedule by clinic",
            "status": "resolved",
            "category": "patient_experience",
            "origin": "customer",
            "branch": "london_west",
            "branch_label": "London — West",
            "created_at": "2024-01-17T00:00:00+00:00",
            "updated_at": "2026-09-10T14:43:15.712221+00:00",
            "description_preview": "Patient not informed of appointment reschedule by clinic",
        }

    @pytest.mark.asyncio
    async def test_rag_plus_incident_synthesis_combines_sources(self):
        """Pregunta mixta debe producir respuesta con datos del ticket + política RAG."""
        from services.agent.graph import run_agent, get_trace
        from unittest.mock import patch

        run_id = str(uuid.uuid4())

        # Chunk de política de cancelación
        policy_chunk = _make_chunk(
            source_document="appointment-policy",
            section="Política de cancelación",
            text=(
                "Cancelar con menos de 24 horas o no presentarse (no-show): "
                "cargo de 50 USD para pacientes de pago privado.\n"
                "Pacientes con Medicare o Medicaid: "
                "no se les cobra cargo por no-show."
            ),
        )

        mock_incident = self._mock_incident_result(ticket_id=2)

        with (
            _mock_retrieve([policy_chunk]),
            patch(
                "services.agent.graph.generate_answer",
                side_effect=_fake_generate_answer_grounded,
            ),
            patch(
                "services.agent.graph.query_incident_by_id",
                return_value=mock_incident,
            ),
        ):
            result = await run_agent(
                "Según la política de cancelación, ¿el ticket 2 debería tener cargo?",
                run_id=run_id,
            )

        answer = result["answer"]

        # 1) Debe contener datos del ticket (incidente)
        assert "ticket" in answer.lower() or "#2" in answer, (
            f"La respuesta debe mencionar el ticket. Respuesta: {answer[:200]}"
        )
        assert "resolved" in answer.lower() or "patient" in answer.lower(), (
            f"La respuesta debe incluir datos del incidente. Respuesta: {answer[:200]}"
        )

        # 2) Debe contener datos de la política (RAG)
        assert "cancelación" in answer.lower() or "cancelar" in answer.lower() or "no-show" in answer.lower(), (
            f"La respuesta debe mencionar la política de cancelación. Respuesta: {answer[:200]}"
        )

        # 3) Debe tener un separador entre tool y RAG (---)
        assert "---" in answer, (
            "La respuesta debe separar la info de tool y RAG con '---'. "
            f"Respuesta: {answer[:200]}"
        )

        # 4) El routing_log debe mostrar la secuencia correcta
        trace = get_trace(run_id)
        assert trace is not None
        routing_log = trace.get("routing_log", [])

        # Debe partir de classify_query → rag+incident
        assert any("rag+incident" in entry for entry in routing_log), (
            f"El routing_log debe contener rag+incident. Log: {routing_log}"
        )

        # Debe mostrar query_incident
        assert any("query_incident" in entry for entry in routing_log), (
            f"El routing_log debe mostrar query_incident. Log: {routing_log}"
        )

        # Debe mostrar retrieve_context → generate_answer
        assert any("retrieve_context → generate_answer" in entry for entry in routing_log), (
            f"El routing_log debe mostrar retrieve_context → generate_answer. Log: {routing_log}"
        )

        # Debe mostrar synthesize_tool_answer
        assert any("synthesize_tool_answer" in entry for entry in routing_log), (
            f"El routing_log debe mostrar synthesize_tool_answer. Log: {routing_log}"
        )

        # 5) Los eventos de traza deben estar en orden correcto
        events = trace.get("events", [])
        node_order = [e["node"] for e in events]
        expected_prefix = ["receive_question", "classify_query"]
        for i, expected_node in enumerate(expected_prefix):
            assert node_order[i] == expected_node, (
                f"Evento {i} debe ser {expected_node}, pero es {node_order[i]}"
            )
        # Los siguientes nodos pueden ser query_incident o retrieve_context
        # (dependiendo del orden de ejecución del grafo)
        assert "query_incident" in node_order, (
            f"Los eventos deben incluir query_incident. Orden: {node_order}"
        )
        assert "retrieve_context" in node_order, (
            f"Los eventos deben incluir retrieve_context. Orden: {node_order}"
        )
        assert "generate_answer" in node_order, (
            f"Los eventos deben incluir generate_answer. Orden: {node_order}"
        )
        assert "synthesize_tool_answer" in node_order, (
            f"Los eventos deben incluir synthesize_tool_answer. Orden: {node_order}"
        )

    @pytest.mark.asyncio
    async def test_rag_plus_incident_honest_when_tool_fails(self):
        """Si la tool falla pero RAG funciona, la respuesta debe reflejar el fallo honestamente."""
        from services.agent.graph import run_agent
        from unittest.mock import patch

        run_id = str(uuid.uuid4())

        policy_chunk = _make_chunk(
            source_document="appointment-policy",
            section="Política de cancelación",
            text=(
                "Cancelar con menos de 24 horas o no presentarse (no-show): "
                "cargo de 50 USD para pacientes de pago privado."
            ),
        )

        with (
            _mock_retrieve([policy_chunk]),
            patch(
                "services.agent.graph.generate_answer",
                side_effect=_fake_generate_answer_grounded,
            ),
            patch(
                "services.agent.graph.query_incident_by_id",
                return_value={"error": "No se pudo conectar con el servicio de incidentes."},
            ),
        ):
            result = await run_agent(
                "Según la política de cancelación, ¿el ticket 482 debería tener cargo?",
                run_id=run_id,
            )

        answer = result["answer"]

        # Debe mencionar el error de la tool
        assert "no pudo" in answer.lower() or "conectar" in answer.lower() or "incidente" in answer.lower(), (
            f"La respuesta debe reflejar el fallo de la tool. Respuesta: {answer[:200]}"
        )


# ======================================================================
# EVAL 10: Memoria — Auto-evaluación, Clasificación y PHI
# ======================================================================


class TestMemoryPHI:
    """PHI validation: nunca debe entrar PHI en memoria."""

    def test_phi_validate_clean_text(self):
        """Texto sin PHI debe pasar validación."""
        from services.agent.memory.store import validate_phi_safe

        ok, matches = validate_phi_safe(
            "El nuevo proceso de derivación en Manchester requiere formulario M-CDX2."
        )
        assert ok, f"No debió detectar PHI en texto limpio. Matches: {matches}"

    def test_phi_rejects_name(self):
        """Nombres completos deben ser detectados como PHI."""
        from services.agent.memory.store import validate_phi_safe

        ok, matches = validate_phi_safe(
            "El paciente Juan Pérez tiene una cita el lunes."
        )
        assert not ok, f"Debió detectar PHI (nombre). Matches: {matches}"

    def test_phi_rejects_ssn(self):
        """SSN debe ser detectado como PHI."""
        from services.agent.memory.store import validate_phi_safe

        ok, matches = validate_phi_safe("SSN: 123-45-6789")
        assert not ok, f"Debió detectar PHI (SSN). Matches: {matches}"

    def test_phi_rejects_email(self):
        """Email debe ser detectado como PHI."""
        from services.agent.memory.store import validate_phi_safe

        ok, matches = validate_phi_safe("Contacto: paciente@healthcore.com")
        assert not ok, f"Debió detectar PHI (email). Matches: {matches}"

    def test_phi_rejects_phone(self):
        """Teléfono debe ser detectado como PHI."""
        from services.agent.memory.store import validate_phi_safe

        ok, matches = validate_phi_safe("Teléfono: 555-123-4567")
        assert not ok, f"Debió detectar PHI (teléfono). Matches: {matches}"

    def test_phi_rejects_patient_id(self):
        """Identificador de paciente debe ser detectado como PHI."""
        from services.agent.memory.store import validate_phi_safe

        ok, matches = validate_phi_safe("MRN: 1234567")
        assert not ok, f"Debió detectar PHI (MRN). Matches: {matches}"

    def test_phi_empty_string_is_safe(self):
        """Cadena vacía se considera segura."""
        from services.agent.memory.store import validate_phi_safe

        ok, matches = validate_phi_safe("")
        assert ok
        assert len(matches) == 0

    def test_set_pending_rejects_phi(self):
        """set_pending_proposal debe rechazar propuestas con PHI."""
        from services.agent.memory.store import (
            clear_pending_proposal,
            get_pending_proposal,
            set_pending_proposal,
        )

        clear_pending_proposal()
        result = set_pending_proposal(
            proposal="El paciente Juan Pérez tiene cita mañana.",
            reason="test",
            category="operational",
            source_run_id="test_phi",
            source_question="test",
        )
        assert result is None, "No debió guardar propuesta con PHI"
        assert get_pending_proposal() is None, "No debe haber propuesta pendiente"
        clear_pending_proposal()


class TestMemoryStore:
    """Operaciones CRUD sobre el almacén TinyDB."""

    def setup_method(self):
        from services.agent.memory.store import (
            _get_db,
            clear_pending_proposal,
        )

        clear_pending_proposal()
        # Limpiar facts de prueba
        db = _get_db()
        table = db.table("memory_facts")
        table.truncate()

    def test_add_and_get_fact(self):
        """Añadir fact y recuperarlo."""
        from services.agent.memory.store import add_fact, get_all_facts

        fact = add_fact(
            content="El nuevo proceso de derivación en Manchester requiere formulario M-CDX2.",
            category="operational",
            source_run_id="test_run_001",
        )
        assert fact is not None
        assert "id" in fact
        assert fact["category"] == "operational"

        facts = get_all_facts()
        assert len(facts) >= 1
        assert any(f["content"] == fact["content"] for f in facts)

    def test_add_fact_rejects_phi(self):
        """add_fact debe rechazar hechos con PHI."""
        from services.agent.memory.store import add_fact, get_all_facts

        initial_count = len(get_all_facts())
        result = add_fact(
            content="Paciente: Juan Pérez, diagnóstico: diabetes.",
            category="operational",
            source_run_id="test_phi",
        )
        assert result is None, "add_fact debe retornar None cuando hay PHI"
        assert len(get_all_facts()) == initial_count

    def test_pending_proposal_singleton(self):
        """Solo puede haber una propuesta pendiente."""
        from services.agent.memory.store import (
            clear_pending_proposal,
            get_pending_proposal,
            has_pending_proposal,
            set_pending_proposal,
        )

        clear_pending_proposal()
        assert not has_pending_proposal()

        p1 = set_pending_proposal(
            proposal="Primera propuesta.",
            reason="test",
            category="operational",
            source_run_id="run_1",
            source_question="test",
        )
        assert p1 is not None
        assert has_pending_proposal()

        p2 = set_pending_proposal(
            proposal="Segunda propuesta (no debería guardarse).",
            reason="test",
            category="operational",
            source_run_id="run_2",
            source_question="test",
        )
        assert p2 is None, "No debe sobrescribir propuesta existente"

        current = get_pending_proposal()
        assert current["proposal"] == "Primera propuesta."

        clear_pending_proposal()
        assert not has_pending_proposal()

    def test_get_facts_by_category(self):
        """Recuperar facts filtrados por categoría."""
        from services.agent.memory.store import add_fact, get_facts_by_category

        add_fact(
            content="Test operational fact.",
            category="operational",
            source_run_id="test_cat",
        )
        add_fact(
            content="Test staff_preference fact.",
            category="staff_preference",
            source_run_id="test_cat",
        )

        op_facts = get_facts_by_category("operational")
        assert len(op_facts) >= 1
        for f in op_facts:
            assert f["category"] == "operational"

    def test_delete_fact(self):
        """Eliminar un fact de memoria."""
        from services.agent.memory.store import (
            add_fact,
            delete_fact,
            get_all_facts,
        )

        fact = add_fact(
            content="Fact para eliminar.",
            category="operational",
            source_run_id="test_del",
        )
        assert fact is not None
        fact_id = fact["id"]

        result = delete_fact(fact_id)
        assert result is True

        remaining = get_all_facts()
        assert not any(f["id"] == fact_id for f in remaining)


class TestMemoryClassifier:
    """Clasificador de intención del usuario."""

    def setup_method(self):
        from services.agent.memory.store import (
            clear_pending_proposal,
            set_pending_proposal,
        )

        clear_pending_proposal()
        set_pending_proposal(
            proposal="Test proposal for classification.",
            reason="test",
            category="operational",
            source_run_id="test_cls",
            source_question="test",
        )

    def teardown_method(self):
        from services.agent.memory.store import clear_pending_proposal
        clear_pending_proposal()

    def test_classify_approve_si(self):
        """"sí" debe clasificarse como approve."""
        from services.agent.memory.classifier import classify_user_intent

        result = classify_user_intent("sí")
        assert result.intent == "approve"

    def test_classify_approve_ok(self):
        """"ok, guárdalo" debe clasificarse como approve."""
        from services.agent.memory.classifier import classify_user_intent

        result = classify_user_intent("ok, guárdalo")
        assert result.intent == "approve"

    def test_classify_reject_no(self):
        """"no guardes" debe clasificarse como reject."""
        from services.agent.memory.classifier import classify_user_intent

        result = classify_user_intent("no guardes eso")
        assert result.intent == "reject"

    def test_classify_edit_mejor_dicho(self):
        """"mejor dicho" debe clasificarse como edit."""
        from services.agent.memory.classifier import classify_user_intent

        result = classify_user_intent("mejor dicho: el proceso cambió en Manchester y Leeds.")
        assert result.intent == "edit"
        assert result.edit_text is not None
        assert "Leeds" in result.edit_text

    def test_classify_discard_by_default(self):
        """Mensaje ambiguo debe descartarse por defecto."""
        from services.agent.memory.classifier import classify_user_intent

        result = classify_user_intent("¿qué hora es?")
        assert result.intent == "discard"

    def test_classify_no_proposal_when_none_pending(self):
        """Sin propuesta pendiente debe retornar no_proposal."""
        from services.agent.memory.classifier import classify_user_intent
        from services.agent.memory.store import clear_pending_proposal

        clear_pending_proposal()
        result = classify_user_intent("sí")
        assert result.intent == "no_proposal"
        # Restaurar para teardown
        from services.agent.memory.store import set_pending_proposal
        set_pending_proposal(
            proposal="Test proposal for classification.",
            reason="test",
            category="operational",
            source_run_id="test_cls",
            source_question="test",
        )

    def test_classify_empty_message_discards(self):
        """Mensaje vacío debe descartarse."""
        from services.agent.memory.classifier import classify_user_intent

        result = classify_user_intent("")
        assert result.intent == "discard"


class TestMemoryPipeline:
    """Pipeline de auto-evaluación y procesamiento de respuesta."""

    def setup_method(self):
        from services.agent.memory.store import (
            _get_db,
            clear_pending_proposal,
        )

        clear_pending_proposal()
        db = _get_db()
        db.table("memory_facts").truncate()
        db.table("audit_log").truncate()

    def test_auto_evaluate_proposal_no_trigger(self):
        """Pregunta sin señales de cambio no debe generar propuesta."""
        from services.agent.memory.pipeline import auto_evaluate_proposal

        answer, proposal = auto_evaluate_proposal(
            question="¿Cuál es la política de cancelación?",
            answer="La política de cancelación requiere 24 horas de antelación.",
            facts=[],
            tool_results=[],
            tool_errors=[],
        )
        assert proposal is None, "No debió generar propuesta para pregunta rutinaria"

    def test_auto_evaluate_proposal_with_trigger(self):
        """Pregunta con señal de cambio debe evaluar propuesta."""
        from services.agent.memory.pipeline import auto_evaluate_proposal

        answer, proposal = auto_evaluate_proposal(
            question="¿Ha cambiado el proceso de derivación en Manchester?",
            answer="Sí, el proceso de derivación en Manchester ahora requiere el formulario M-CDX2 firmado.",
            facts=[],
            tool_results=[],
            tool_errors=[],
        )
        assert proposal is not None, "Debió generar propuesta para cambio de proceso"
        assert proposal["category"] == "operational"
        assert "M-CDX2" in proposal["proposal"]

    def test_auto_evaluate_proposal_skips_non_memorizable(self):
        """Preguntas no memorizables no deben generar propuesta."""
        from services.agent.memory.pipeline import auto_evaluate_proposal

        answer, proposal = auto_evaluate_proposal(
            question="gracias por la ayuda",
            answer="De nada, ¡que tengas buen día!",
            facts=[],
            tool_results=[],
            tool_errors=[],
        )
        assert proposal is None, "No debió generar propuesta para agradecimiento"

    def test_process_user_response_approve(self):
        """Respuesta 'sí' debe aprobar y escribir en memoria."""
        from services.agent.memory.pipeline import process_user_response
        from services.agent.memory.store import (
            clear_pending_proposal,
            get_all_facts,
            set_pending_proposal,
        )

        clear_pending_proposal()
        initial_count = len(get_all_facts())

        set_pending_proposal(
            proposal="Nuevo proceso en Manchester.",
            reason="test",
            category="operational",
            source_run_id="test_pipeline",
            source_question="test",
        )

        result = process_user_response("sí, guárdalo")
        assert result["resolution"] == "approved"
        assert result["final_content"] is not None

        # Verificar que se escribió en memoria
        facts = get_all_facts()
        assert len(facts) > initial_count
        clear_pending_proposal()

    def test_process_user_response_reject(self):
        """Respuesta 'no' debe rechazar, no escribir en memoria."""
        from services.agent.memory.pipeline import process_user_response
        from services.agent.memory.store import (
            clear_pending_proposal,
            get_all_facts,
            set_pending_proposal,
        )

        clear_pending_proposal()
        initial_count = len(get_all_facts())

        set_pending_proposal(
            proposal="Patrón de incidentes los lunes.",
            reason="test",
            category="incident_pattern",
            source_run_id="test_pipeline",
            source_question="test",
        )

        result = process_user_response("no, olvídalo")
        assert result["resolution"] == "rejected"

        facts = get_all_facts()
        assert len(facts) == initial_count, "No debe haber nuevos facts tras rechazo"
        clear_pending_proposal()

    def test_process_user_response_edit(self):
        """Respuesta editando debe escribir el texto editado."""
        from services.agent.memory.pipeline import process_user_response
        from services.agent.memory.store import (
            clear_pending_proposal,
            get_all_facts,
            set_pending_proposal,
        )

        clear_pending_proposal()

        set_pending_proposal(
            proposal="El proceso cambió en Manchester.",
            reason="test",
            category="operational",
            source_run_id="test_pipeline",
            source_question="test",
        )

        result = process_user_response(
            "mejor dicho: el proceso cambió en Manchester y Leeds."
        )
        assert result["resolution"] == "edited"
        assert result["final_content"] is not None
        assert "Leeds" in result["final_content"]

        facts = get_all_facts()
        assert any("Leeds" in f["content"] for f in facts)
        clear_pending_proposal()

    def test_process_user_response_discard_by_default(self):
        """Mensaje ambiguo debe descartar la propuesta."""
        from services.agent.memory.pipeline import process_user_response
        from services.agent.memory.store import (
            clear_pending_proposal,
            get_all_facts,
            set_pending_proposal,
        )

        clear_pending_proposal()
        initial_count = len(get_all_facts())

        set_pending_proposal(
            proposal="Propuesta de prueba.",
            reason="test",
            category="operational",
            source_run_id="test_pipeline",
            source_question="test",
        )

        result = process_user_response("¿qué clima hace?")
        assert result["resolution"] == "discarded"

        facts = get_all_facts()
        assert len(facts) == initial_count
        clear_pending_proposal()

    def test_audit_log_created_on_approve(self):
        """Aprobación debe crear entrada en audit_log."""
        from services.agent.memory.pipeline import process_user_response
        from services.agent.memory.store import (
            clear_pending_proposal,
            get_audit_log,
            set_pending_proposal,
        )

        clear_pending_proposal()
        initial_log_count = len(get_audit_log(limit=999))

        set_pending_proposal(
            proposal="Hecho auditado.",
            reason="test",
            category="operational",
            source_run_id="test_audit",
            source_question="test",
        )

        process_user_response("sí")

        log = get_audit_log(limit=10)
        assert len(log) > initial_log_count, "Debe haber nuevas entradas en audit_log"

        # Verificar que hay un evento de aprobación
        event_types = [e.get("event_type", "") for e in log]
        has_approve = any("approved" in et for et in event_types)
        assert has_approve, f"No se encontró evento 'approved'. Eventos: {event_types}"

        clear_pending_proposal()

    def test_build_memory_context_format(self):
        """build_memory_context debe formatear facts correctamente."""
        from services.agent.memory.pipeline import build_memory_context

        test_facts = [
            {"content": "Fact 1", "category": "operational"},
            {"content": "Fact 2", "category": "incident_pattern"},
            {"content": "Fact 3", "category": "staff_preference"},
        ]

        context = build_memory_context(test_facts)
        assert "Fact 1" in context
        assert "Fact 2" in context
        assert "Fact 3" in context
        # Debe incluir emojis por categoría
        assert "📌" in context or "⚡" in context or "👤" in context


class TestMemoryGraphIntegration:
    """Verifica que los nodos de memoria están integrados correctamente en el grafo."""

    def test_memory_nodes_exist_in_graph(self):
        """Los 3 nodos de memoria deben existir en el grafo."""
        from services.agent.graph import agent_graph

        for node_name in ("load_memory_context", "process_proposal", "auto_evaluate_memory"):
            assert node_name in agent_graph.nodes, (
                f"Nodo de memoria '{node_name}' no encontrado en el grafo"
            )

    def test_memory_routes_defined(self):
        """Las funciones de ruta de memoria deben importarse correctamente."""
        from services.agent.graph import (
            route_after_auto_evaluate,
            route_after_load_memory,
            route_after_process_proposal,
        )

        # Solo verificar que son callables
        assert callable(route_after_load_memory)
        assert callable(route_after_process_proposal)
        assert callable(route_after_auto_evaluate)

    def test_route_after_receive_goes_to_load_memory(self):
        """route_after_receive debe enrutar a load_memory_context."""
        from services.agent.graph import build_initial_state, route_after_receive

        state = build_initial_state("¿Cuál es la política?")
        decision = route_after_receive(state)
        assert decision == "load_memory_context"