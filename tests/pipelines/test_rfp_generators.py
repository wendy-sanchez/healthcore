"""
tests/pipelines/test_rfp_generators.py — Unit tests for Part 2 generators.

Tests cover:
  - Generator produces a draft for each department
  - Generator uses metadata and key_aspects
  - Generator returns Spanish text
  - Rule-based fallback works when LLM is unavailable
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.asyncio

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from data.pipelines.rfp_intake.generators import (
    generate_draft,
    _rule_based_fallback,
    DEPARTMENT_PROMPTS,
)
from data.pipelines.rfp_intake.models import RfpMetadata


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
def sample_metadata_uk() -> RfpMetadata:
    return RfpMetadata(
        client_name="NHS London Trust",
        client_country="UK",
        program_type="Clinical Services",
        covered_population=50000,
        deadline="June 1, 2025",
        budget_range="$2,000,000 - $3,000,000",
        currency="GBP",
    )


@pytest.fixture
def sample_key_aspects() -> list[dict]:
    return [
        {"aspect": "pricing", "detail": "Tarifa mensual por persona cubierta"},
        {"aspect": "BAA", "detail": "Business Associate Agreement requerido"},
        {"aspect": "renewal", "detail": "Opción de renovación anual"},
    ]


# ── Tests ─────────────────────────────────────────────────────────────


class TestGenerateDraft:
    """Tests for the LLM-based draft generator."""

    async def test_rule_based_fallback_revenue(self, sample_metadata):
        """Fallback produce borrador para revenue."""
        draft = _rule_based_fallback("revenue", sample_metadata, [])
        assert draft
        assert "REVENUE" in draft
        assert "USD" in draft
        assert "Meridian" in draft

    async def test_rule_based_fallback_clinical(self, sample_metadata):
        """Fallback produce borrador para clinical."""
        draft = _rule_based_fallback("clinical", sample_metadata, [])
        assert draft
        assert "CLINICAL" in draft
        assert "personal" in draft.lower()

    async def test_rule_based_fallback_compliance(self, sample_metadata):
        """Fallback produce borrador para compliance con cláusulas BAA."""
        draft = _rule_based_fallback("compliance", sample_metadata, [])
        assert draft
        assert "Compliance" in draft or "compliance" in draft.lower()
        assert "BAA" in draft or "Business Associate" in draft

    async def test_rule_based_fallback_compliance_uk(self, sample_metadata_uk):
        """Fallback para UK client incluye DPA no BAA."""
        draft = _rule_based_fallback("compliance", sample_metadata_uk, [])
        assert draft
        assert "DPA" in draft or "Data Processing" in draft

    async def test_generate_draft_llm_fallback(self, sample_metadata, sample_key_aspects):
        """generate_draft usa fallback cuando no hay LLM configurado."""
        with patch.dict("os.environ", {"API_KEY": "", "HOST": ""}):
            draft = await generate_draft("revenue", sample_metadata, sample_key_aspects)
        assert draft
        assert len(draft) > 50
        assert "Meridian" in draft

    async def test_generate_draft_all_departments_have_prompts(self):
        """Todos los departamentos tienen un prompt definido."""
        for dept_id in ("revenue", "clinical", "compliance"):
            assert dept_id in DEPARTMENT_PROMPTS, f"Falta prompt para {dept_id}"

    async def test_generate_draft_with_feedback(self, sample_metadata, sample_key_aspects):
        """generate_draft acepta previous_feedback."""
        with patch.dict("os.environ", {"API_KEY": "", "HOST": ""}):
            draft = await generate_draft(
                "revenue", sample_metadata, sample_key_aspects,
                previous_feedback="Falta mención de BAA"
            )
        assert draft
        assert len(draft) > 50

    async def test_generate_draft_unknown_department(self, sample_metadata):
        """Departamento desconocido levanta ValueError."""
        with pytest.raises(ValueError):
            await generate_draft("unknown_dept", sample_metadata, [])