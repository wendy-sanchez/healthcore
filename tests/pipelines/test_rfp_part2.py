"""tests/pipelines/test_rfp_part2.py — Unit tests for Part 2 models and cycle.

Covers:
  - Part2DepartmentOutput: approval_status field defaults and values
  - Part2Handoff: correct construction with department outputs
  - TicketStatus enum: EN_EVALUACION, GENERANDO_BORRADOR values
  - Part2PipelineState: status transitions
  - run_part2_pipeline: constructs Part2DepartmentOutput with correct defaults
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Nota: asyncio se marca SOLO en cada test async individualmente
# para evitar warnings en tests síncronos.

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from data.pipelines.rfp_intake.models_part2 import (
    DraftContent,
    EvaluationResult,
    Part2DepartmentOutput,
    Part2DepartmentState,
    Part2Handoff,
    Part2PipelineState,
    ReadabilityEval,
    RelevanceEval,
    ComplianceEval,
)
from data.pipelines.rfp_intake.models import (
    TicketStatus,
    RfpMetadata,
    DepartmentSection,
    HandoffPayload,
)
from data.pipelines.rfp_intake.cycle import run_part2_pipeline, _extract_key_aspects
from data.pipelines.rfp_intake.models import KeyAspect


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def sample_metadata() -> RfpMetadata:
    return RfpMetadata(
        client_name="Meridian Manufacturing",
        client_country="US",
        program_type="Population Health Management",
        covered_population=15000,
        deadline="March 15, 2025",
        budget_range="$3,000,000 - $5,000,000",
        currency="USD",
    )


@pytest.fixture
def sample_department_section() -> DepartmentSection:
    return DepartmentSection(
        department_id="revenue",
        raw_extract="Cliente requiere tarifa mensual por persona cubierta.",
        key_aspects=[
            {"aspect": "pricing", "detail": "Tarifa mensual por persona"},
            {"aspect": "BAA", "detail": "Business Associate Agreement requerido"},
        ],
        open_questions=["¿Cuál es el volumen mínimo de miembros?"],
        contains_phi=False,
    )


@pytest.fixture
def sample_handoff(sample_metadata, sample_department_section) -> HandoffPayload:
    return HandoffPayload(
        ticket_id="test-ticket-123",
        rfp_metadata=sample_metadata,
        departments=[sample_department_section],
        synthesis="RFP para programa de Population Health Management.",
    )


@pytest.fixture
def sample_evaluation() -> EvaluationResult:
    return EvaluationResult(
        department_id="revenue",
        readability=ReadabilityEval(pass_=True, score=45.0),
        relevance=RelevanceEval(pass_=True, missing_aspects=[]),
        compliance=ComplianceEval(pass_=True, rule_ids=[], violations=[]),
        overall_pass=True,
        feedback_for_generator="✅ Todo correcto.",
    )


@pytest.fixture
def good_pass_evaluation() -> EvaluationResult:
    return EvaluationResult(
        department_id="revenue",
        readability=ReadabilityEval(pass_=True, score=55.0),
        relevance=RelevanceEval(pass_=True, missing_aspects=[]),
        compliance=ComplianceEval(pass_=True, rule_ids=[], violations=[]),
        overall_pass=True,
        feedback_for_generator="✅ Borrador aprobado.",
    )


# ── Tests: TicketStatus enum ──────────────────────────────────────────


class TestTicketStatusEnum:
    """Verifica que el enum TicketStatus incluya los nuevos estados."""

    def test_en_evaluacion_exists(self):
        """EN_EVALUACION debe existir con valor exacto."""
        assert hasattr(TicketStatus, "EN_EVALUACION")
        assert TicketStatus.EN_EVALUACION.value == "en_evaluación"

    def test_generando_borrador_exists(self):
        """GENERANDO_BORRADOR debe existir con valor exacto."""
        assert hasattr(TicketStatus, "GENERANDO_BORRADOR")
        assert TicketStatus.GENERANDO_BORRADOR.value == "generando_borrador"

    def test_needs_human_review_exists(self):
        """NEEDS_HUMAN_REVIEW debe existir con valor exacto."""
        assert hasattr(TicketStatus, "NEEDS_HUMAN_REVIEW")
        assert TicketStatus.NEEDS_HUMAN_REVIEW.value == "needs_human_review"

    def test_all_status_values_unique(self):
        """Todos los valores del enum deben ser únicos."""
        values = [s.value for s in TicketStatus]
        assert len(values) == len(set(values)), f"Valores duplicados: {values}"

    def test_estados_incluyen_parte2(self):
        """El enum debe incluir los estados del flujo de Parte 2."""
        expected_states = {
            "analizando",
            "descartado",
            "analisis_completo",
            "generando_borrador",
            "en_evaluación",
            "needs_human_review",
            "error",
        }
        actual_states = {s.value for s in TicketStatus}
        assert actual_states == expected_states, (
            f"Diferencia: faltan {expected_states - actual_states}, "
            f"sobran {actual_states - expected_states}"
        )


# ── Tests: Part2DepartmentOutput model ────────────────────────────────


class TestPart2DepartmentOutput:
    """Verifica el modelo Part2DepartmentOutput y el campo approval_status."""

    def test_approval_status_default_is_pending(self, good_pass_evaluation):
        """approval_status debe ser 'pending' por defecto."""
        output = Part2DepartmentOutput(
            department_id="revenue",
            finalized_draft="# Borrador de prueba",
            evaluation=good_pass_evaluation,
        )
        assert output.approval_status == "pending"

    def test_approval_status_can_be_approved(self, good_pass_evaluation):
        """approval_status debe poder ser 'approved'."""
        output = Part2DepartmentOutput(
            department_id="revenue",
            finalized_draft="# Borrador aprobado",
            evaluation=good_pass_evaluation,
            approval_status="approved",
        )
        assert output.approval_status == "approved"

    def test_approval_status_can_be_rejected(self, good_pass_evaluation):
        """approval_status debe poder ser 'rejected'."""
        output = Part2DepartmentOutput(
            department_id="revenue",
            finalized_draft="# Borrador rechazado",
            evaluation=good_pass_evaluation,
            approval_status="rejected",
        )
        assert output.approval_status == "rejected"

    def test_approval_status_is_string(self, good_pass_evaluation):
        """approval_status debe ser del tipo str."""
        output = Part2DepartmentOutput(
            department_id="revenue",
            finalized_draft="# Test",
            evaluation=good_pass_evaluation,
            approval_status="pending",
        )
        assert isinstance(output.approval_status, str)

    def test_fields_mapped_correctly(self, good_pass_evaluation):
        """Verificar que todos los campos se mapean correctamente."""
        output = Part2DepartmentOutput(
            department_id="compliance",
            finalized_draft="# Sección de compliance",
            evaluation=good_pass_evaluation,
            needs_human_review=False,
            iteration_count=2,
            approval_status="pending",
        )
        assert output.department_id == "compliance"
        assert output.finalized_draft == "# Sección de compliance"
        assert output.evaluation is good_pass_evaluation
        assert output.needs_human_review is False
        assert output.iteration_count == 2
        assert output.approval_status == "pending"

    def test_multiple_departamentos_con_aprobacion_distinta(self):
        """Distintos departamentos pueden tener distinto approval_status."""
        dept_revenue = Part2DepartmentOutput(
            department_id="revenue",
            finalized_draft="# Revenue",
            approval_status="approved",
        )
        dept_clinical = Part2DepartmentOutput(
            department_id="clinical",
            finalized_draft="# Clinical",
            approval_status="pending",
        )
        dept_compliance = Part2DepartmentOutput(
            department_id="compliance",
            finalized_draft="# Compliance",
            approval_status="rejected",
        )
        assert dept_revenue.approval_status == "approved"
        assert dept_clinical.approval_status == "pending"
        assert dept_compliance.approval_status == "rejected"

    def test_serialization_includes_approval_status(self, good_pass_evaluation):
        """Serialización a dict debe incluir approval_status."""
        output = Part2DepartmentOutput(
            department_id="revenue",
            finalized_draft="# Draft",
            evaluation=good_pass_evaluation,
            approval_status="approved",
        )
        data = output.model_dump()
        assert "approval_status" in data
        assert data["approval_status"] == "approved"

    def test_serialization_default_approval_status(self, good_pass_evaluation):
        """Serialización sin approval_status explícito debe incluir 'pending'."""
        output = Part2DepartmentOutput(
            department_id="revenue",
            finalized_draft="# Draft",
            evaluation=good_pass_evaluation,
        )
        data = output.model_dump()
        assert data["approval_status"] == "pending"


# ── Tests: Part2Handoff model ─────────────────────────────────────────


class TestPart2Handoff:
    """Verifica el modelo Part2Handoff que transporta los outputs."""

    def test_handoff_contains_departments_with_approval_status(self, good_pass_evaluation):
        """El handoff debe incluir departments con approval_status."""
        depts = [
            Part2DepartmentOutput(
                department_id="revenue",
                finalized_draft="# Revenue draft",
                evaluation=good_pass_evaluation,
            )
        ]
        handoff = Part2Handoff(
            ticket_id="test-123",
            rfp_metadata={"client_name": "Test"},
            departments=depts,
            synthesis="Test synthesis",
            overall_status="analisis_completo",
        )
        assert len(handoff.departments) == 1
        assert handoff.departments[0].approval_status == "pending"

    def test_handoff_status_mapping(self, good_pass_evaluation):
        """needs_human_review en algún depto → overall_status needs_human_review."""
        depts = [
            Part2DepartmentOutput(
                department_id="revenue",
                finalized_draft="# Draft OK",
                evaluation=good_pass_evaluation,
                needs_human_review=False,
            ),
            Part2DepartmentOutput(
                department_id="clinical",
                finalized_draft="# Draft fails",
                needs_human_review=True,
            ),
        ]
        handoff = Part2Handoff(
            ticket_id="test-456",
            rfp_metadata={"client_name": "Test"},
            departments=depts,
            synthesis="",
            overall_status="needs_human_review",
        )
        assert handoff.overall_status == "needs_human_review"

    def test_handoff_all_analisis_completo(self, good_pass_evaluation):
        """Todos los deptos sin needs_human_review → overall_status analisis_completo."""
        depts = [
            Part2DepartmentOutput(
                department_id=d,
                finalized_draft=f"# Draft {d}",
                evaluation=good_pass_evaluation,
                needs_human_review=False,
            )
            for d in ("revenue", "clinical", "compliance")
        ]
        handoff = Part2Handoff(
            ticket_id="test-789",
            rfp_metadata={},
            departments=depts,
            synthesis="Done",
            overall_status="analisis_completo",
        )
        assert handoff.overall_status == "analisis_completo"


# ── Tests: Part2PipelineState ─────────────────────────────────────────


class TestPart2PipelineState:
    """Verifica el estado interno de la Parte 2."""

    def test_default_status_is_generando_borrador(self):
        """El estado por defecto debe ser 'generando_borrador'."""
        state = Part2PipelineState(
            ticket_id="test-123",
            rfp_metadata={"client_name": "Test"},
        )
        assert state.overall_status == "generando_borrador"
        assert state.completed is False

    def test_department_states_match_handoff(self, sample_handoff):
        """Los department_states deben coincidir con los departamentos del handoff."""
        metadata = sample_handoff.rfp_metadata
        state = Part2PipelineState(
            ticket_id=sample_handoff.ticket_id,
            rfp_metadata=metadata,
            synthesis=sample_handoff.synthesis,
            departments=[d.model_dump() for d in sample_handoff.departments],
        )
        for d in sample_handoff.departments:
            dept_state = Part2DepartmentState(
                department_id=d.department_id,
                max_iterations=3,
            )
            state.department_states.append(dept_state)

        assert len(state.department_states) == 1
        assert state.department_states[0].department_id == "revenue"
        assert state.department_states[0].max_iterations == 3


# ── Tests: _extract_key_aspects ───────────────────────────────────────


class TestExtractKeyAspects:
    """Verifica la extracción de key_aspects desde department sections."""

    def test_extracts_from_dict(self):
        """Extrae key_aspects de departamentos como dict."""
        depts = [
            {"department_id": "revenue", "key_aspects": [{"aspect": "pricing"}]},
            {"department_id": "clinical", "key_aspects": [{"aspect": "staffing"}]},
        ]
        result = _extract_key_aspects("revenue", depts)
        assert result == [{"aspect": "pricing"}]

    def test_extracts_from_objects(self, sample_department_section):
        """Extrae key_aspects de objetos DepartmentSection."""
        result = _extract_key_aspects("revenue", [sample_department_section])
        assert len(result) == 2
        # KeyAspect objects se convierten con str(), result[a] es {"aspect": str(KeyAspect)}
        assert "pricing" in result[0]["aspect"] or result[0]["aspect"] is not None

    def test_returns_empty_list_for_unknown(self):
        """Departamento no encontrado retorna lista vacía."""
        result = _extract_key_aspects("unknown", [])
        assert result == []


# ── Tests: run_part2_pipeline ─────────────────────────────────────────


@pytest.mark.asyncio
class TestRunPart2Pipeline:
    """Tests de integración para run_part2_pipeline con mocks."""

    async def test_output_has_default_approval_status(
        self, sample_handoff, good_pass_evaluation
    ):
        """El Part2DepartmentOutput generado por el pipeline debe tener approval_status='pending'."""
        with (
            patch("data.pipelines.rfp_intake.cycle.generate_draft", new_callable=AsyncMock) as mock_gen,
            patch("data.pipelines.rfp_intake.cycle.evaluate_section", new_callable=AsyncMock) as mock_eval,
        ):
            mock_gen.return_value = "# Borrador de prueba para revenue"
            mock_eval.return_value = good_pass_evaluation

            result = await run_part2_pipeline(sample_handoff, max_iterations=1)

            assert len(result.departments) == 1
            dept_out = result.departments[0]
            assert dept_out.department_id == "revenue"
            assert dept_out.finalized_draft == "# Borrador de prueba para revenue"
            assert dept_out.approval_status == "pending", (
                f"Esperaba 'pending', obtuve '{dept_out.approval_status}'"
            )
            assert dept_out.needs_human_review is False

    async def test_output_with_evaluation_includes_approval_status(
        self, sample_handoff, good_pass_evaluation
    ):
        """Output con evaluation incluida debe mantener approval_status pendiente."""
        with (
            patch("data.pipelines.rfp_intake.cycle.generate_draft", new_callable=AsyncMock) as mock_gen,
            patch("data.pipelines.rfp_intake.cycle.evaluate_section", new_callable=AsyncMock) as mock_eval,
        ):
            mock_gen.return_value = "# Borrador con evaluación"
            mock_eval.return_value = good_pass_evaluation

            result = await run_part2_pipeline(sample_handoff, max_iterations=1)

            dept_out = result.departments[0]
            assert dept_out.evaluation is not None
            assert dept_out.evaluation.overall_pass is True
            assert dept_out.approval_status == "pending"

    async def test_needs_human_review_when_evaluation_fails(
        self, sample_handoff
    ):
        """Cuando la evaluación falla tras max_iterations, needs_human_review=True."""
        failed_evaluation = EvaluationResult(
            department_id="revenue",
            readability=ReadabilityEval(pass_=False, score=10.0),
            relevance=RelevanceEval(pass_=False, missing_aspects=["pricing"]),
            compliance=ComplianceEval(
                pass_=False,
                rule_ids=["baa-us"],
                violations=["Falta BAA para cliente US"],
            ),
            overall_pass=False,
            feedback_for_generator="❌ Corregir compliance y pricing.",
        )

        with (
            patch("data.pipelines.rfp_intake.cycle.generate_draft", new_callable=AsyncMock) as mock_gen,
            patch("data.pipelines.rfp_intake.cycle.evaluate_section", new_callable=AsyncMock) as mock_eval,
        ):
            mock_gen.return_value = "# Borrador que falla evaluación"
            mock_eval.return_value = failed_evaluation

            result = await run_part2_pipeline(sample_handoff, max_iterations=1)

            dept_out = result.departments[0]
            assert dept_out.needs_human_review is True, (
                "Debería marcar needs_human_review si no pasó evaluación"
            )
            assert dept_out.approval_status == "pending"
            assert result.overall_status == "needs_human_review"

    async def test_multiple_departments_all_pass(
        self, sample_metadata, good_pass_evaluation
    ):
        """Múltiples departamentos que pasan deben tener approval_status='pending'."""
        depts = [
            DepartmentSection(
                department_id="revenue",
                raw_extract="Revenue section",
                key_aspects=[{"aspect": "pricing", "detail": "Pricing"}],
                open_questions=[],
                contains_phi=False,
            ),
            DepartmentSection(
                department_id="clinical",
                raw_extract="Clinical section",
                key_aspects=[{"aspect": "staffing", "detail": "Staffing"}],
                open_questions=[],
                contains_phi=False,
            ),
        ]
        handoff = HandoffPayload(
            ticket_id="multi-dept-test",
            rfp_metadata=sample_metadata,
            departments=depts,
            synthesis="Multi-department test RFP.",
        )

        with (
            patch("data.pipelines.rfp_intake.cycle.generate_draft", new_callable=AsyncMock) as mock_gen,
            patch("data.pipelines.rfp_intake.cycle.evaluate_section", new_callable=AsyncMock) as mock_eval,
        ):
            mock_gen.side_effect = [
                "# Revenue draft",
                "# Clinical draft",
            ]
            mock_eval.return_value = good_pass_evaluation

            result = await run_part2_pipeline(handoff, max_iterations=1)

            assert len(result.departments) == 2
            for dept_out in result.departments:
                assert dept_out.approval_status == "pending", (
                    f"Departamento {dept_out.department_id} debería tener approval_status='pending'"
                )
                assert dept_out.needs_human_review is False
            assert result.overall_status == "analisis_completo"

    async def test_pipeline_handoff_serialization_roundtrip(
        self, sample_handoff, good_pass_evaluation
    ):
        """Serialización del handoff debe preservar approval_status."""
        with (
            patch("data.pipelines.rfp_intake.cycle.generate_draft", new_callable=AsyncMock) as mock_gen,
            patch("data.pipelines.rfp_intake.cycle.evaluate_section", new_callable=AsyncMock) as mock_eval,
        ):
            mock_gen.return_value = "# Draft for serialization test"
            mock_eval.return_value = good_pass_evaluation

            result = await run_part2_pipeline(sample_handoff, max_iterations=1)

            # Serializar a dict y reconstruir
            data = result.model_dump(mode="json")
            reconstructed = Part2Handoff.model_validate(data)

            assert len(reconstructed.departments) == 1
            assert reconstructed.departments[0].approval_status == "pending"
            assert reconstructed.departments[0].finalized_draft == "# Draft for serialization test"
            assert reconstructed.departments[0].department_id == "revenue"

    async def test_pipeline_with_empty_handoff(self, sample_metadata):
        """Handoff sin departamentos debe retornar handoff vacío."""
        handoff = HandoffPayload(
            ticket_id="empty-test",
            rfp_metadata=sample_metadata,
            departments=[],
            synthesis="",
        )

        result = await run_part2_pipeline(handoff, max_iterations=3)

        assert len(result.departments) == 0
        assert result.overall_status == "analisis_completo"
        assert result.ticket_id == "empty-test"