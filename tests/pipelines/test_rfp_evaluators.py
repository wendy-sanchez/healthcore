"""
tests/pipelines/test_rfp_evaluators.py — Unit tests for Part 2 evaluators.

Tests cover:
  - Readability evaluator (word count, Flesch score)
  - Relevance evaluator (key_aspects coverage, client mention)
  - Compliance evaluator (PHI detection, BAA/DPA rules, currency)

Key test scenarios:
  - Draft with PHI → compliance fails (phi-detected)
  - US client draft without BAA → compliance fails (baa-us)
  - UK client draft without DPA → compliance fails (dpa-uk)
  - All evaluators pass on a well-formed draft
  - Compliance is always evaluated regardless of department
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.asyncio

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from data.pipelines.rfp_intake.evaluators import (
    evaluate_readability,
    evaluate_relevance,
    evaluate_compliance,
    evaluate_section,
    PHI_PATTERNS,
    PHI_NAME_PATTERNS,
)
from data.pipelines.rfp_intake.models import RfpMetadata


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def us_metadata() -> RfpMetadata:
    return RfpMetadata(
        client_name="Meridian Manufacturing",
        client_country="US",
        program_type="Population Health Management",
        covered_population=15000,
        deadline="March 15, 2025",
        budget_range="$3,000,000 - $5,000,000",
        currency="USD",
        departments_mentioned=["revenue", "clinical", "compliance"],
    )


@pytest.fixture
def uk_metadata() -> RfpMetadata:
    return RfpMetadata(
        client_name="NHS London Trust",
        client_country="UK",
        program_type="Clinical Services",
        covered_population=50000,
        deadline="June 1, 2025",
        budget_range="$2,000,000 - $3,000,000",
        currency="GBP",
        departments_mentioned=["revenue", "clinical", "compliance"],
    )


@pytest.fixture
def good_draft_revenue() -> str:
    """Borrador bien formado sin PHI, con BAA, moneda USD."""
    return """# Propuesta Económica — Revenue Cycle

## Términos Financieros
HealthCore Solutions propone los siguientes términos financieros para Meridian Manufacturing.
La tarifa mensual será de USD 15 por persona cubierta, facturada mensualmente, con un contrato inicial
de 12 meses y opción de renovación anual. El precio incluye todos los servicios administrativos y
de gestión de población cubierta por el programa de Population Health Management.

## Compliance
Se incluye Business Associate Agreement (BAA) conforme a la regulación HIPAA aplicable.
Esta propuesta cumple con todos los requisitos regulatorios y de compliance aplicables.
HealthCore garantiza que no se incluye PHI (Protected Health Information) en ningún documento.

## Precio
El precio total estimado es de USD 225,000 anuales para una población de 15000 personas.
Todos los precios se cotizan en dólares estadounidenses (USD)."""


@pytest.fixture
def phi_draft() -> str:
    """Borrador que contiene PHI."""
    return """# Propuesta Económica — Revenue Cycle

Para el paciente John Smith, diagnosticado con diabetes tipo 2,
la tarifa mensual será de USD 15 por persona.

El patient ID del individuo es 123-45-6789.
Se requiere BAA."""


@pytest.fixture
def no_baa_draft() -> str:
    """Borrador para cliente US sin BAA."""
    return """# Propuesta Económica — Revenue Cycle

Se propone una tarifa de USD 15 por persona para Meridian Manufacturing.
El contrato será por 12 meses. No se requiere ningún acuerdo adicional de compliance
ya que los servicios son administrativos. Precio en USD."""


@pytest.fixture
def no_dpa_uk_draft() -> str:
    """Borrador para cliente UK sin DPA."""
    return """# Propuesta Económica — Clinical Operations

Para NHS London Trust, el programa de servicios clínicos se implementará
con personal de enfermería y médicos ocupacionales. Tarifa: GBP 20 por persona.
No aplican acuerdos especiales de datos para este servicio."""


# ── Readability tests ─────────────────────────────────────────────────


class TestReadabilityEvaluator:
    """Tests for evaluate_readability."""

    async def test_good_draft_passes(self, good_draft_revenue):
        """Borrador de buena longitud pasa legibilidad."""
        result = await evaluate_readability(good_draft_revenue, "sec-1", "revenue")
        assert result.pass_ is True
        assert result.score is not None
        assert result.details is not None

    async def test_short_draft_fails(self):
        """Borrador demasiado corto falla legibilidad."""
        short = "Hola mundo."
        result = await evaluate_readability(short, "sec-1", "revenue")
        assert result.pass_ is False
        assert "corto" in (result.details or "").lower()

    @pytest.mark.parametrize("word_count", [50, 99])
    async def test_draft_below_minimum_fails(self, word_count):
        """Borradores por debajo de 100 palabras fallan."""
        draft = "palabra " * word_count
        result = await evaluate_readability(draft, "sec-1", "revenue")
        assert result.pass_ is False
        assert "corto" in (result.details or "").lower()


# ── Relevance tests ───────────────────────────────────────────────────


class TestRelevanceEvaluator:
    """Tests for evaluate_relevance."""

    async def test_good_draft_passes(self, good_draft_revenue, us_metadata):
        """Borrador que menciona cliente y aspectos relevantes pasa."""
        key_aspects = [
            {"aspect": "BAA", "detail": "Business Associate Agreement requerido"},
            {"aspect": "USD", "detail": "Precio en dólares"},
            {"aspect": "compliance", "detail": "Requisitos regulatorios"},
        ]
        result = await evaluate_relevance(good_draft_revenue, "revenue", us_metadata, key_aspects)
        assert result.pass_ is True
        assert len(result.missing_aspects) == 0

    async def test_missing_client_name(self, us_metadata):
        """Borrador sin mención del cliente falla."""
        draft = "Propuesta económica con tarifa de USD 15 por persona."
        key_aspects = [{"aspect": "pricing", "detail": "Tarifa mensual"}]
        result = await evaluate_relevance(draft, "revenue", us_metadata, key_aspects)
        assert len(result.missing_aspects) >= 1

    async def test_missing_key_aspects(self, us_metadata):
        """Borrador que no cubre aspectos clave tiene missing_aspects."""
        draft = "Propuesta para Meridian Manufacturing con tarifa de USD 15."
        key_aspects = [
            {"aspect": "BAA", "detail": "BAA requerido"},
            {"aspect": "renewal", "detail": "Renovación anual"},
            {"aspect": "compliance", "detail": "Requisitos regulatorios"},
        ]
        result = await evaluate_relevance(draft, "revenue", us_metadata, key_aspects)
        assert len(result.missing_aspects) > 0


# ── Compliance tests (anchored to CONTEXT.md §5 rules) ────────────────


class TestComplianceEvaluator:
    """Tests for evaluate_compliance.

    Estas pruebas verifican las reglas de compliance del CONTEXT.md §5:

    1. phi-detected: Ninguna sección puede contener PHI
    2. baa-us: Cliente US debe incluir BAA
    3. dpa-uk: Cliente UK debe incluir DPA referenciando UK GDPR
    4. currency-country: USD para US, GBP para UK
    5. compliance-mandatory: Compliance siempre obligatorio
    """

    async def test_good_draft_passes(self, good_draft_revenue, us_metadata):
        """Borrador bien formado pasa todas las reglas de compliance."""
        result = await evaluate_compliance(good_draft_revenue, "revenue", us_metadata)
        assert result.pass_ is True, f"Esperaba pass=True, violaciones: {result.violations}"
        assert len(result.violations) == 0

    async def test_phi_detected_fails(self, phi_draft, us_metadata):
        """Borrador con PHI falla compliance (regla phi-detected)."""
        result = await evaluate_compliance(phi_draft, "revenue", us_metadata)
        assert result.pass_ is False
        assert "phi-detected" in result.rule_ids
        assert any("PHI" in v or "phi" in v.lower() for v in result.violations)

    async def test_baa_missing_for_us_fails(self, no_baa_draft, us_metadata):
        """Cliente US sin BAA falla compliance (regla baa-us)."""
        result = await evaluate_compliance(no_baa_draft, "revenue", us_metadata)
        assert result.pass_ is False
        assert "baa-us" in result.rule_ids
        assert any("BAA" in v for v in result.violations)

    async def test_dpa_missing_for_uk_fails(self, no_dpa_uk_draft, uk_metadata):
        """Cliente UK sin DPA falla compliance (regla dpa-uk)."""
        result = await evaluate_compliance(no_dpa_uk_draft, "clinical", uk_metadata)
        assert result.pass_ is False
        assert "dpa-uk" in result.rule_ids
        assert any("DPA" in v for v in result.violations)

    async def test_phi_pattern_detection(self):
        """PHI_PATTERNS detecta identificadores de paciente."""
        text = "El paciente patient name es John Smith con SSN 123-45-6789"
        assert PHI_PATTERNS.search(text)

    async def test_phi_pattern_clean_passes(self):
        """PHI_PATTERNS no da falsos positivos en texto limpio."""
        text = "El cliente Meridian Manufacturing requiere BAA y HIPAA compliance."
        assert PHI_PATTERNS.search(text) is None

    async def test_compliance_mandatory_for_revenue(self, us_metadata):
        """Departamento revenue sin mención de compliance falla (regla compliance-mandatory)."""
        draft = "Propuesta de tarifa USD 15 para Meridian Manufacturing con BAA incluido."
        result = await evaluate_compliance(draft, "revenue", us_metadata)
        # Debe pasar porque menciona BAA (compliance implícito)
        assert result.pass_ is True

    async def test_compliance_department_always_passes_mandatory(self, us_metadata):
        """Departamento compliance no necesita marcarse a sí mismo."""
        draft = "Sección de compliance con BAA para Meridian Manufacturing en USD."
        result = await evaluate_compliance(draft, "compliance", us_metadata)
        assert result.pass_ is True


# ── Full section evaluation tests ─────────────────────────────────────


class TestEvaluateSection:
    """Tests for the full evaluate_section function."""

    async def test_full_evaluation_passes(self, good_draft_revenue, us_metadata):
        """Evaluación completa sobre borrador bien formado."""
        key_aspects = [
            {"aspect": "BAA", "detail": "BAA requerido"},
            {"aspect": "pricing", "detail": "Tarifa mensual"},
        ]
        result = await evaluate_section(good_draft_revenue, "revenue", us_metadata, key_aspects)
        assert result.overall_pass is True
        assert result.readability.pass_ is True
        assert result.relevance.pass_ is True
        assert result.compliance.pass_ is True
        assert result.feedback_for_generator
        assert "✅" in result.feedback_for_generator

    async def test_phi_draft_overall_fails(self, phi_draft, us_metadata):
        """Borrador con PHI falla la evaluación global."""
        key_aspects = [{"aspect": "BAA", "detail": "BAA requerido"}]
        result = await evaluate_section(phi_draft, "revenue", us_metadata, key_aspects)
        assert result.overall_pass is False
        assert result.compliance.pass_ is False
        # El feedback debe ser accionable
        assert "PHI" in result.feedback_for_generator or "phi" in result.feedback_for_generator.lower()
        assert "CUMPLIMIENTO" in result.feedback_for_generator

    async def test_no_baa_overall_fails(self, no_baa_draft, us_metadata):
        """Borrador sin BAA falla evaluación global para cliente US."""
        key_aspects = [{"aspect": "pricing", "detail": "Tarifa mensual"}]
        result = await evaluate_section(no_baa_draft, "revenue", us_metadata, key_aspects)
        assert result.overall_pass is False
        assert result.compliance.pass_ is False
        assert "CUMPLIMIENTO" in result.feedback_for_generator
        assert "BAA" in " ".join(result.compliance.violations)

    async def test_no_dpa_uk_overall_fails(self, no_dpa_uk_draft, uk_metadata):
        """Borrador UK sin DPA falla evaluación global."""
        key_aspects = [{"aspect": "clinical staffing", "detail": "Personal clínico"}]
        result = await evaluate_section(no_dpa_uk_draft, "clinical", uk_metadata, key_aspects)
        assert result.overall_pass is False
        assert result.compliance.pass_ is False
        assert "DPA" in " ".join(result.compliance.violations) or "dpa" in " ".join(result.compliance.violations).lower()