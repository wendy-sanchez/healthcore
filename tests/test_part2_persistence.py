"""
Test Suite for Part 2 Real-Time Persistence (PostgreSQL).

Verifica que las funciones _persist_* de cycle.py persistan estados
intermedios en tiempo real usando el mismo engine SQLModel (PostgreSQL)
que el resto del pipeline.

Flujo de generation_status por departamento:
  null -> generando -> draft_complete -> evaluando -> completado

Flujo de estados del ticket:
  analisis_completo -> generando_borrador -> en_evaluacion -> completado
"""

import pytest
from unittest.mock import MagicMock, patch, ANY
from datetime import datetime
from typing import Optional


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_engine():
    """Crea un engine SQLModel mockeado."""
    return MagicMock(name="engine")


@pytest.fixture
def mock_session():
    """Crea una session mockeada con exec().first() retornando None por defecto."""
    session = MagicMock(name="session")
    exec_result = MagicMock(name="exec_result")
    exec_result.first.return_value = None
    session.exec.return_value = exec_result
    return session


@pytest.fixture
def session_factory(mock_session):
    """Parchea sqlmodel.Session como clase context manager.
    Session(engine) -> context manager -> __enter__() -> mock_session.
    Las funciones _persist_* hacen 'from sqlmodel import Session, select'.
    """
    session_instance = MagicMock(name="session_instance")
    session_instance.__enter__.return_value = mock_session
    mock_session_cls = MagicMock(return_value=session_instance)
    with patch("sqlmodel.Session", mock_session_cls) as patched:
        yield patched


# =============================================================================
# Helper para crear DepartmentSectionDB mockeado con atributos
# =============================================================================


def make_dept_db(ticket_id="rfp-001", department_id="revenue", **kwargs):
    """Crea un objeto DepartmentSectionDB simulado con atributos editables."""
    obj = MagicMock(name="DepartmentSectionDB")
    obj.ticket_id = ticket_id
    obj.department_id = department_id
    obj.generation_status = kwargs.get("generation_status", None)
    obj.generation_iteration = kwargs.get("generation_iteration", None)
    obj.draft_content = kwargs.get("draft_content", None)
    obj.evaluation_results = kwargs.get("evaluation_results", None)
    return obj


def make_ticket_db(ticket_id="rfp-001", status=None):
    """Crea un objeto TicketDB simulado con atributos editables."""
    obj = MagicMock(name="TicketDB")
    obj.ticket_id = ticket_id
    obj.status = status
    return obj


# =============================================================================
# Tests
# =============================================================================


class TestPersistGenerationStarted:
    """_persist_generation_started: null/iniciando -> generando + ticket GENERANDO_BORRADOR"""

    def test_sets_generation_status_and_ticket(self, mock_engine, mock_session, session_factory):
        """Debe setear generation_status='generando' y ticket.status='generando_borrador'."""
        dept_db = make_dept_db(generation_status=None)
        ticket_db = make_ticket_db(status="analisis_completo")

        # Configurar mock para que devuelva dept_db y ticket_db
        mock_session.exec.return_value.first.side_effect = [dept_db, ticket_db]

        from data.pipelines.rfp_intake.cycle import _persist_generation_started
        _persist_generation_started(mock_engine, "rfp-001", "revenue", iteration=1)

        # Verificar que se persistieron los cambios
        assert dept_db.generation_status == "generando"
        assert dept_db.generation_iteration == 1
        assert ticket_db.status == "generando_borrador"
        mock_session.add.assert_any_call(dept_db)
        mock_session.add.assert_any_call(ticket_db)
        mock_session.commit.assert_called_once()
        print("PASS: generation_status=generando, ticket=generando_borrador")

    def test_handles_missing_dept_db(self, mock_engine, mock_session, session_factory):
        """Si DepartmentSectionDB no existe, no debe fallar (solo actualiza ticket)."""
        ticket_db = make_ticket_db(status="analisis_completo")

        # Primera consulta (dept) -> None, segunda (ticket) -> ticket_db
        mock_session.exec.return_value.first.side_effect = [None, ticket_db]

        from data.pipelines.rfp_intake.cycle import _persist_generation_started
        _persist_generation_started(mock_engine, "rfp-001", "revenue", iteration=1)

        # Ticket debe actualizarse aunque dept no exista
        assert ticket_db.status == "generando_borrador"
        mock_session.commit.assert_called_once()
        print("PASS: No falla si DepartmentSectionDB no existe")


class TestPersistDraftComplete:
    """_persist_draft_complete: generando -> draft_complete + borrador persistido"""

    def test_persists_draft_immediately(self, mock_engine, mock_session, session_factory):
        """Debe persistir el borrador y generation_status='draft_complete' INMEDIATAMENTE."""
        dept_db = make_dept_db(generation_status="generando", draft_content=None)
        mock_session.exec.return_value.first.return_value = dept_db

        from data.pipelines.rfp_intake.cycle import _persist_draft_complete
        _persist_draft_complete(mock_engine, "rfp-001", "revenue", "USD 50K annual, net 30")

        assert dept_db.draft_content == "USD 50K annual, net 30"
        assert dept_db.generation_status == "draft_complete"
        mock_session.add.assert_called_once_with(dept_db)
        mock_session.commit.assert_called_once()
        print("PASS: Borrador persistido inmediatamente con status draft_complete")

    def test_draft_available_before_evaluation(self, mock_engine, mock_session, session_factory):
        """
        Test CRITICO: El draft debe estar disponible en BD ANTES de que
        la evaluacion comience. Esto simula: generar -> PERSISTIR -> evaluar.
        """
        dept_db = make_dept_db(generation_status="generando", draft_content=None)
        mock_session.exec.return_value.first.return_value = dept_db

        from data.pipelines.rfp_intake.cycle import _persist_draft_complete
        draft = "Clinical trial feasibility: Yes"
        _persist_draft_complete(mock_engine, "rfp-001", "clinical", draft)

        # El draft debe estar en BD inmediatamente
        assert dept_db.draft_content == draft
        assert dept_db.generation_status == "draft_complete"
        # La evaluacion aun no deberia estar
        assert dept_db.evaluation_results is None
        print("PASS: Draft en BD antes de evaluacion (evaluation_results=None)")


class TestPersistEvaluationStarted:
    """_persist_evaluation_started: draft_complete -> evaluando + ticket EN_EVALUACION"""

    def test_transitions_to_evaluating_before_evaluation(self, mock_engine, mock_session, session_factory):
        """
        Test CRITICO: La transicion a EVALUANDO debe registrarse ANTES
        de llamar al evaluador LLM.
        """
        dept_db = make_dept_db(generation_status="draft_complete", draft_content="Draft ok")
        ticket_db = make_ticket_db(status="generando_borrador")

        mock_session.exec.return_value.first.side_effect = [dept_db, ticket_db]

        from data.pipelines.rfp_intake.cycle import _persist_evaluation_started
        _persist_evaluation_started(mock_engine, "rfp-001", "clinical")

        assert dept_db.generation_status == "evaluando"
        assert ticket_db.status == "en_evaluación"
        mock_session.commit.assert_called_once()
        print("PASS: Estado cambiado a evaluando + ticket en_evaluacion")


class TestPersistEvaluationComplete:
    """_persist_evaluation_complete: evaluando -> completado + evaluation_results"""

    def test_persists_evaluation_results(self, mock_engine, mock_session, session_factory):
        """Debe persistir evaluation_results y status='completado'."""
        dept_db = make_dept_db(generation_status="evaluando", evaluation_results=None)
        mock_session.exec.return_value.first.return_value = dept_db

        eval_dict = {
            "legibility_score": 85.0,
            "relevance_score": 90.0,
            "compliance_score": 100.0,
            "overall_pass": True,
            "feedback_for_generator": "Looks good, minor improvements needed",
        }

        from data.pipelines.rfp_intake.cycle import _persist_evaluation_complete
        _persist_evaluation_complete(mock_engine, "rfp-001", "revenue", eval_dict, passed=True)

        assert dept_db.generation_status == "completado"
        assert dept_db.evaluation_results == eval_dict
        mock_session.add.assert_called_once_with(dept_db)
        mock_session.commit.assert_called_once()
        print("PASS: Evaluation persistida con status completado")

    def test_persists_before_next_department(self, mock_engine, mock_session, session_factory):
        """
        Test CRITICO: Simula Dept A -> completo, luego Dept B -> generando.
        Verifica que Dept A esta completo en BD mientras Dept B esta en progreso.
        """
        from data.pipelines.rfp_intake.cycle import (
            _persist_evaluation_complete,
            _persist_generation_started,
        )

        # --- Dept A: completar evaluacion ---
        dept_a = make_dept_db(ticket_id="rfp-001", department_id="revenue", generation_status="evaluando")
        mock_session.exec.return_value.first.return_value = dept_a
        _persist_evaluation_complete(mock_engine, "rfp-001", "revenue", {"score": 90}, passed=True)

        assert dept_a.generation_status == "completado"

        # --- Dept B: iniciar generacion ---
        dept_b = make_dept_db(ticket_id="rfp-001", department_id="clinical", generation_status=None)
        ticket_db = make_ticket_db(status="completado")

        # Configurar nueva respuesta para la siguiente llamada
        mock_session.exec.return_value.first.side_effect = [dept_b, ticket_db]
        _persist_generation_started(mock_engine, "rfp-001", "clinical", iteration=1)

        assert dept_b.generation_status == "generando"
        assert dept_a.generation_status == "completado"  # A debe seguir completo
        print("PASS: Dept A completado en BD mientras Dept B genera")


# =============================================================================
# Tests de integracion: Flujo completo simulado
# =============================================================================


class TestFullCyclePersistence:
    """Simula el ciclo completo de persistencia para un departamento."""

    def test_full_department_lifecycle(self, mock_engine, mock_session, session_factory):
        """
        Simula el ciclo completo de persistencia para un departamento,
        desde generando -> draft_complete -> evaluando -> completado.
        Resetea el side_effect ANTES de cada llamada a funcion de persistencia.
        """
        from data.pipelines.rfp_intake.cycle import (
            _persist_generation_started,
            _persist_draft_complete,
            _persist_evaluation_started,
            _persist_evaluation_complete,
        )

        ticket_id = "rfp-002"
        dept_id = "revenue"

        # --- FASE 1: generation_started (2 queries: dept_db, ticket_db) ---
        dept_db = make_dept_db(ticket_id=ticket_id, department_id=dept_id, generation_status=None)
        ticket_db = make_ticket_db(ticket_id=ticket_id, status="analisis_completo")
        mock_session.exec.return_value.first.side_effect = [dept_db, ticket_db]
        _persist_generation_started(mock_engine, ticket_id, dept_id, iteration=1)
        assert dept_db.generation_status == "generando"
        assert dept_db.generation_iteration == 1
        assert ticket_db.status == "generando_borrador"

        # --- FASE 2: draft_complete (1 query: dept_db) ---
        mock_session.exec.return_value.first.side_effect = None
        mock_session.exec.return_value.first.return_value = dept_db
        _persist_draft_complete(mock_engine, ticket_id, dept_id, "Draft content v1")
        assert dept_db.generation_status == "draft_complete"
        assert dept_db.draft_content == "Draft content v1"

        # --- FASE 3: evaluation_started (2 queries: dept_db, ticket_db) ---
        mock_session.exec.return_value.first.side_effect = [dept_db, ticket_db]
        _persist_evaluation_started(mock_engine, ticket_id, dept_id)
        assert dept_db.generation_status == "evaluando"
        assert ticket_db.status == "en_evaluación"

        # --- FASE 4: evaluation_complete (1 query: dept_db) ---
        mock_session.exec.return_value.first.side_effect = None
        mock_session.exec.return_value.first.return_value = dept_db
        eval_dict = {"overall_pass": True, "score": 88.0}
        _persist_evaluation_complete(mock_engine, ticket_id, dept_id, eval_dict, passed=True)
        assert dept_db.generation_status == "completado"
        assert dept_db.evaluation_results == eval_dict

        print("PASS: Ciclo completo de persistencia verificado")


class TestEdgeCases:
    """Casos borde de persistencia."""

    def test_multiple_iterations_tracked(self, mock_engine, mock_session, session_factory):
        """Cada iteracion debe actualizar generation_iteration."""
        from data.pipelines.rfp_intake.cycle import _persist_generation_started

        dept_db = make_dept_db(generation_status=None)
        ticket_db = make_ticket_db()

        for it in range(1, 4):
            mock_session.exec.return_value.first.side_effect = [dept_db, ticket_db]
            _persist_generation_started(mock_engine, "rfp-001", "revenue", iteration=it)
            assert dept_db.generation_iteration == it, f"Iteracion {it} no registrada"
            mock_session.commit.assert_called()

        print("PASS: Multiples iteraciones registradas correctamente")

    def test_persistence_without_engine_does_nothing(self):
        """Si engine es None, ninguna funcion debe fallar.
        Parchea Session para evitar conexion real a BD."""
        from unittest.mock import patch as mock_patch
        from data.pipelines.rfp_intake.cycle import (
            _persist_generation_started,
            _persist_draft_complete,
            _persist_evaluation_started,
            _persist_evaluation_complete,
        )

        with mock_patch("sqlmodel.Session") as mock_session_cls:
            # Ninguna debe lanzar excepcion con engine=None
            _persist_generation_started(None, "rfp-001", "revenue", iteration=1)
            _persist_draft_complete(None, "rfp-001", "revenue", "draft")
            _persist_evaluation_started(None, "rfp-001", "revenue")
            _persist_evaluation_complete(None, "rfp-001", "revenue", {}, passed=True)

        print("PASS: Ninguna funcion falla con engine=None")
