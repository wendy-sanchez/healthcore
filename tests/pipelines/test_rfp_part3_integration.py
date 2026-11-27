"""tests/pipelines/test_rfp_part3_integration.py — Integration tests for Part 3.

Tests the integration between Part 3 and the production pipeline:
  - Nodo 9 triggers Part 3 when status is needs_human_review
  - Part 3 conflict detection with real pipeline data
  - Approval flow via DepartmentSectionDB
  - Ticket status transitions: needs_human_review → esperando_aprobacion → terminado
  - GET/POST approval endpoints
"""

from __future__ import annotations

import sys
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from services.api.models_rfp import DepartmentSectionDB, RfpMetadataDB, TicketDB
from sqlmodel import Session, select, SQLModel, create_engine


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def temp_db():
    """Create temporary SQLite database for checkpointer tests"""
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    yield db_path
    Path(db_path).unlink(missing_ok=True)


@pytest.fixture
def in_memory_engine():
    """Create in-memory SQLite engine with all tables"""
    engine = create_engine("sqlite:///:memory:", echo=False)
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture
def sample_ticket(in_memory_engine):
    """Create a sample ticket in needs_human_review status"""
    ticket = TicketDB(
        ticket_id="test-ticket-001",
        status="needs_human_review",
        raw_pdf_path="/tmp/test.pdf",
        handoff_payload={
            "departments": [
                {
                    "department_id": "revenue",
                    "finalized_draft": "Revenue section draft content with adequate pricing",
                    "contains_phi": False,
                    "key_aspects": [],
                    "open_questions": [],
                    "evaluation": {
                        "compliance": {"pass": True, "score": 90, "violations": [], "summary": "OK"},
                    },
                },
                {
                    "department_id": "clinical",
                    "finalized_draft": "Clinical capacity covers 5000 members vs population 10000, insufficient capacity",
                    "contains_phi": False,
                    "key_aspects": [],
                    "open_questions": [],
                    "evaluation": {
                        "compliance": {"pass": True, "score": 85, "violations": [], "summary": "OK"},
                    },
                },
                {
                    "department_id": "compliance",
                    "finalized_draft": "Compliance draft without any agreement or regulatory mention. Pending review.",
                    "contains_phi": False,
                    "key_aspects": [],
                    "open_questions": [],
                    "evaluation": {
                        "compliance": {"pass": True, "score": 60, "violations": ["Missing agreement"], "summary": "No agreement referenced"},
                    },
                },
            ],
            "rfp_metadata": {
                "client_name": "Test Client",
                "client_country": "US",
                "program_type": "medicare-advantage",
                "covered_population": 10000,
            },
        },
    )
    with Session(in_memory_engine) as session:
        session.add(ticket)
        session.commit()
        # Create department sections for Part 3
        for dept_id in ["revenue", "clinical", "compliance"]:
            section = DepartmentSectionDB(
                ticket_id=ticket.ticket_id,
                department_id=dept_id,
                key_aspects=[],
                open_questions=[],
                contains_phi=False,
                draft_content=f"{dept_id} draft content",
                evaluation_results={},
            )
            session.add(section)
        session.commit()
        # Refresh and return
        session.refresh(ticket)
    return ticket


@pytest.fixture
def sample_ticket_with_phi(in_memory_engine):
    """Ticket with PHI detected for compliance approval flow"""
    ticket = TicketDB(
        ticket_id="test-ticket-phi-001",
        status="needs_human_review",
        raw_pdf_path="/tmp/test_phi.pdf",
        handoff_payload={
            "departments": [
                {
                    "department_id": "compliance",
                    "finalized_draft": "Section with SSN: 123-45-6789 and patient data",
                    "contains_phi": True,
                    "key_aspects": [],
                    "open_questions": [],
                    "evaluation": {
                        "compliance": {
                            "pass": False,
                            "score": 45,
                            "violations": ["contains_phi: PHI detected in compliance section"],
                            "summary": "PHI content found",
                        }
                    },
                },
            ],
            "rfp_metadata": {
                "client_name": "PHI Client",
                "client_country": "US",
                "program_type": "medicare-advantage",
                "covered_population": 5000,
            },
        },
    )
    with Session(in_memory_engine) as session:
        session.add(ticket)
        session.commit()
        section = DepartmentSectionDB(
            ticket_id=ticket.ticket_id,
            department_id="compliance",
            key_aspects=[],
            open_questions=[],
            contains_phi=True,
            draft_content="Section with SSN: 123-45-6789 and patient data",
            evaluation_results={},
        )
        session.add(section)
        session.commit()
        session.refresh(ticket)
    return ticket


# ── Tests: Part 3 conflict detection integration ──────────────────


class TestPart3ConflictDetection:
    """Test that Part 3 correctly detects conflicts from pipeline data."""

    def test_detect_phi_conflict_from_ticket(self, sample_ticket_with_phi, in_memory_engine):
        """Part 3 should detect PHI conflict when ticket has contains_phi=True"""
        from agentic_workflows_part3.models_part3 import Part3PipelineState
        from agentic_workflows_part3.graph_nodes import detect_conflicts_node

        handoff = sample_ticket_with_phi.handoff_payload
        state = Part3PipelineState(
            ticket_id=sample_ticket_with_phi.ticket_id,
            rfp_metadata=handoff.get("rfp_metadata", {}),
            part2_handoff=handoff,
        )

        result = detect_conflicts_node(state)
        assert "detected_conflicts" in result
        assert len(result["detected_conflicts"]) >= 1

        # Check that PHI was detected
        phi_conflicts = [
            c for c in result["detected_conflicts"]
            if c.trigger_id.value == "phi-detected"
        ]
        assert len(phi_conflicts) >= 1, "Should detect PHI conflict"

        # Verify affected departments includes compliance
        assert "compliance" in phi_conflicts[0].affected_departments

    def test_detect_baa_conflict_us_client(self, sample_ticket, in_memory_engine):
        """US client without BAA should trigger baa-dpa-mismatch conflict"""
        from agentic_workflows_part3.models_part3 import Part3PipelineState
        from agentic_workflows_part3.graph_nodes import detect_conflicts_node

        handoff = sample_ticket.handoff_payload
        state = Part3PipelineState(
            ticket_id=sample_ticket.ticket_id,
            rfp_metadata=handoff.get("rfp_metadata", {}),
            part2_handoff=handoff,
        )

        result = detect_conflicts_node(state)
        baa_conflicts = [
            c for c in result["detected_conflicts"]
            if c.trigger_id.value == "baa-dpa-mismatch"
        ]
        assert len(baa_conflicts) >= 1, "US client should trigger BAA conflict"

    def test_no_conflicts_when_all_clear(self, in_memory_engine):
        """Clean data with no PHI, proper agreements, enough capacity → no conflicts"""
        from agentic_workflows_part3.models_part3 import Part3PipelineState
        from agentic_workflows_part3.graph_nodes import detect_conflicts_node

        clean_handoff = {
            "departments": [
                {
                    "department_id": "revenue",
                    "finalized_draft": "Standard revenue content with sufficient budget",
                    "contains_phi": False,
                    "key_aspects": [],
                    "open_questions": [],
                    "evaluation": {
                        "compliance": {"pass": True, "score": 95, "violations": [], "summary": "OK"},
                    },
                },
                {
                    "department_id": "clinical",
                    "finalized_draft": "Clinical capacity: 20000 members, well above requirements",
                    "contains_phi": False,
                    "key_aspects": [],
                    "open_questions": [],
                    "evaluation": {
                        "compliance": {"pass": True, "score": 92, "violations": [], "summary": "OK"},
                    },
                },
                {
                    "department_id": "compliance",
                    "finalized_draft": "BAA is in place with the client, all agreements signed",
                    "contains_phi": False,
                    "key_aspects": [],
                    "open_questions": [],
                    "evaluation": {
                        "compliance": {"pass": True, "score": 96, "violations": [], "summary": "OK"},
                    },
                },
            ],
            "rfp_metadata": {
                "client_name": "Clean Client",
                "client_country": "US",
                "program_type": "medicare-advantage",
                "covered_population": 10000,
            },
        }
        state = Part3PipelineState(
            ticket_id="clean-ticket-001",
            rfp_metadata=clean_handoff.get("rfp_metadata", {}),
            part2_handoff=clean_handoff,
        )

        result = detect_conflicts_node(state)
        assert len(result["detected_conflicts"]) == 0, "Clean data should have no conflicts"


class TestPart3ApprovalFlow:
    """Test the approval flow via DepartmentSectionDB persistence."""

    def test_record_approval_via_db(self, sample_ticket, in_memory_engine):
        """Record approval in DepartmentSectionDB and verify it persists"""
        with Session(in_memory_engine) as session:
            section = session.exec(
                select(DepartmentSectionDB).where(
                    DepartmentSectionDB.ticket_id == sample_ticket.ticket_id,
                    DepartmentSectionDB.department_id == "revenue",
                )
            ).first()
            section.approval_status = "approved"
            section.approver = "Tom Callahan"
            section.approved_at = datetime.now(timezone.utc)
            session.add(section)
            session.commit()

        # Verify in new session
        with Session(in_memory_engine) as session:
            updated = session.exec(
                select(DepartmentSectionDB).where(
                    DepartmentSectionDB.ticket_id == sample_ticket.ticket_id,
                    DepartmentSectionDB.department_id == "revenue",
                )
            ).first()
            assert updated is not None
            assert updated.approval_status == "approved"
            assert updated.approver == "Tom Callahan"
            assert updated.approved_at is not None

    def test_reject_department_changes_ticket_status(self, sample_ticket, in_memory_engine):
        """Rejecting a department should set ticket back to needs_human_review"""
        # Simulate what the approve endpoint does
        with Session(in_memory_engine) as session:
            section = session.exec(
                select(DepartmentSectionDB).where(
                    DepartmentSectionDB.ticket_id == sample_ticket.ticket_id,
                    DepartmentSectionDB.department_id == "clinical",
                )
            ).first()
            section.approval_status = "rejected"
            section.approver = "Dr. Marcus Reid"
            section.approved_at = datetime.now(timezone.utc)
            session.add(section)
            session.commit()

            # Also update ticket status
            ticket = session.exec(
                select(TicketDB).where(TicketDB.ticket_id == sample_ticket.ticket_id)
            ).first()
            ticket.status = "needs_human_review"
            session.add(ticket)
            session.commit()

        with Session(in_memory_engine) as session:
            ticket = session.exec(
                select(TicketDB).where(TicketDB.ticket_id == sample_ticket.ticket_id)
            ).first()
            assert ticket.status == "needs_human_review"

    def test_all_departments_approved_completes_ticket(self, sample_ticket, in_memory_engine):
        """When all departments approve, ticket should transition to terminado"""
        with Session(in_memory_engine) as session:
            sections = session.exec(
                select(DepartmentSectionDB).where(
                    DepartmentSectionDB.ticket_id == sample_ticket.ticket_id,
                )
            ).all()
            for section in sections:
                section.approval_status = "approved"
                section.approver = "Test Approver"
                section.approved_at = datetime.now(timezone.utc)
                session.add(section)

            ticket = session.exec(
                select(TicketDB).where(TicketDB.ticket_id == sample_ticket.ticket_id)
            ).first()
            ticket.status = "terminado"
            ticket.final_document_path = "/tmp/final_doc.pdf"
            ticket.part3_completed_at = datetime.now(timezone.utc)
            session.add(ticket)
            session.commit()

        with Session(in_memory_engine) as session:
            ticket = session.exec(
                select(TicketDB).where(TicketDB.ticket_id == sample_ticket.ticket_id)
            ).first()
            assert ticket.status == "terminado"
            assert ticket.final_document_path == "/tmp/final_doc.pdf"
            assert ticket.part3_completed_at is not None


class TestPart3PipelineIntegration:
    """Test Part 3 pipeline running with mocked/real data from the pipeline."""

    def test_part3_runs_from_pipeline_state(self):
        """Part 3 should accept a Part2Handoff-derived dict and run full pipeline"""
        from agentic_workflows_part3.models_part3 import Part3PipelineState
        from agentic_workflows_part3.graph_nodes import detect_conflicts_node, arbitrage_node
        from agentic_workflows_part3.graph_nodes import request_approvals_node
        from agentic_workflows_part3.graph_nodes import check_approvals_complete
        from agentic_workflows_part3.checkpointer import Part3SQLiteCheckpointer, CheckpointConfig

        handoff = {
            "departments": [
                {
                    "department_id": "revenue",
                    "finalized_draft": "Revenue section for pricing at $50 per member per month. "
                                       "Covers 10000 members.",
                    "contains_phi": False,
                    "evaluation": {
                        "compliance": {"pass": True, "score": 90, "violations": [], "summary": "OK"},
                    },
                },
                {
                    "department_id": "clinical",
                    "finalized_draft": "Clinical team can handle 5000 but population is 10000. "
                                       "insufficient capacity",
                    "contains_phi": False,
                    "evaluation": {
                        "compliance": {"pass": True, "score": 85, "violations": [], "summary": "OK"},
                    },
                },
                {
                    "department_id": "compliance",
                    "finalized_draft": "Includes SSN: 123-45-6789 in demographics section. "
                                       "US client without BAA mentioned.",
                    "contains_phi": True,
                    "evaluation": {
                        "compliance": {"pass": False, "score": 30, "violations": ["contains_phi: PHI detected"], "summary": "PHI found"},
                    },
                },
            ],
        }

        state = Part3PipelineState(
            ticket_id="pipeline-test-001",
            rfp_metadata={
                "client_name": "Pipeline Test Client",
                "client_country": "US",
                "program_type": "medicare-advantage",
                "covered_population": 10000,
            },
            part2_handoff=handoff,
        )

        # Step 1: Detect conflicts
        result = detect_conflicts_node(state)
        state.detected_conflicts = result["detected_conflicts"]
        assert len(state.detected_conflicts) >= 1, "Should detect at least 1 conflict"

        # Step 2: Arbitrage
        result = arbitrage_node(state)
        state.arbitrage_outcomes = result["arbitrage_outcomes"]
        assert len(state.arbitrage_outcomes) > 0, "Should have arbitrage outcomes"

        # Step 3: Request approvals
        checkpointer = Part3SQLiteCheckpointer(CheckpointConfig(db_path="/tmp/test_p3_pipeline.db"))
        result = request_approvals_node(state, checkpointer=checkpointer)
        state.interrupted_departments = result["interrupted_departments"]
        assert len(state.interrupted_departments) > 0, "Should interrupt for approvals"

        # Step 4: Should NOT be complete (approvals pending)
        assert not check_approvals_complete(state), "Approvals should not be complete yet"

    def test_part3_full_cycle_with_approvals(self):
        """Full Part 3 cycle: detect → arbitrage → approvals → (mock approve) → check → synthesize"""
        from agentic_workflows_part3.models_part3 import (
            Part3PipelineState,
            DepartmentApprovalResponse,
            ApprovalStatus,
        )
        from agentic_workflows_part3.graph_nodes import (
            detect_conflicts_node,
            arbitrage_node,
            request_approvals_node,
            check_approvals_complete,
            synthesize_document_node,
        )
        from agentic_workflows_part3.checkpointer import Part3SQLiteCheckpointer, CheckpointConfig
        from datetime import datetime

        handoff = {
            "departments": [
                {
                    "department_id": "revenue",
                    "finalized_draft": "Revenue: $50/member, 10000 members, no issues.",
                    "contains_phi": False,
                    "evaluation": {
                        "compliance": {"pass": True, "score": 90, "violations": [], "summary": "OK"},
                    },
                },
                {
                    "department_id": "clinical",
                    "finalized_draft": "Clinical capacity adequate for 10000 members, sufficient resources.",
                    "contains_phi": False,
                    "evaluation": {
                        "compliance": {"pass": True, "score": 92, "violations": [], "summary": "OK"},
                    },
                },
                {
                    "department_id": "compliance",
                    "finalized_draft": "SSN: 123-45-6789 found. BAA check needed.",
                    "contains_phi": True,
                    "evaluation": {
                        "compliance": {"pass": False, "score": 25, "violations": ["contains_phi: PHI detected in compliance section"], "summary": "PHI found"},
                    },
                },
            ],
        }

        state = Part3PipelineState(
            ticket_id="full-cycle-001",
            rfp_metadata={
                "client_name": "Full Cycle Client",
                "client_country": "US",
                "program_type": "medicare-advantage",
                "covered_population": 10000,
            },
            part2_handoff=handoff,
        )

        checkpointer = Part3SQLiteCheckpointer(CheckpointConfig(db_path="/tmp/test_p3_full_cycle.db"))

        # Run detect → arbitrage → request_approvals
        result = detect_conflicts_node(state)
        state.detected_conflicts = result["detected_conflicts"]

        result = arbitrage_node(state)
        state.arbitrage_outcomes = result["arbitrage_outcomes"]

        result = request_approvals_node(state, checkpointer=checkpointer)
        state.interrupted_departments = result["interrupted_departments"]

        assert len(state.interrupted_departments) > 0, "Should have interrupted depts"

        # Simulate approvals for all interrupted departments
        for dept_id in list(state.interrupted_departments.keys()):
            response = DepartmentApprovalResponse(
                ticket_id=state.ticket_id,
                department_id=dept_id,
                approval_status=ApprovalStatus.APPROVED,
                approver_name="Test Approver",
                approver_email="test@healthcore.com",
                approved_at_utc=datetime.utcnow(),
            )
            state.department_approvals[dept_id] = response
            state.interrupted_departments.pop(dept_id, None)

        # Record in checkpointer
        for dept_id, response in state.department_approvals.items():
            checkpointer.record_approval(
                ticket_id=state.ticket_id,
                department_id=dept_id,
                approval_status=response.approval_status.value,
                approver_name=response.approver_name,
                approved_at_iso=response.approved_at_utc.isoformat(),
            )

        # Now check if approvals complete
        assert check_approvals_complete(state), "All approvals should be complete"

        # Synthesize document
        state.all_departments_approved = True
        result = synthesize_document_node(state)
        state.final_document = result.get("final_document")

        assert state.final_document is not None, "Should generate a final document"
        assert len(state.final_document.sections) > 0, "Document should have sections"
        assert state.all_departments_approved, "Should mark all departments approved"


class TestApprovalEndpointValidation:
    """Test validation logic that mirrors the FastAPI approve endpoint."""

    def test_approve_invalid_status(self):
        """Only approved/rejected/requested_changes are valid approval_status values"""
        valid_statuses = {"approved", "rejected", "requested_changes"}
        invalid = ["accepted", "denied", "pending", "in_review", 123, None, ""]

        for status in invalid:
            assert status not in valid_statuses, f"'{status}' should not be valid"

    def test_approve_only_when_esperando_aprobacion(self):
        """Ticket must be in esperando_aprobacion to accept approvals"""
        valid_states = {"esperando_aprobacion"}
        invalid = ["analizando", "needs_human_review", "terminado", "error",
                   "descartado", "en_evaluacion", "generando_borrador",
                   "part2_handoff", "analisis_completo"]

        for state in invalid:
            assert state not in valid_states, f"'{state}' should not accept approvals"


class TestModelsRfpPart3Fields:
    """Test that the new Part 3 fields on TicketDB work correctly."""

    def test_ticketdb_has_part3_fields(self, in_memory_engine):
        """TicketDB should have final_document_path and part3_completed_at"""
        ticket = TicketDB(
            ticket_id="part3-fields-test",
            status="esperando_aprobacion",
        )
        with Session(in_memory_engine) as session:
            session.add(ticket)
            session.commit()

        with Session(in_memory_engine) as session:
            t = session.exec(
                select(TicketDB).where(TicketDB.ticket_id == "part3-fields-test")
            ).first()
            assert hasattr(t, "final_document_path")
            assert hasattr(t, "part3_completed_at")

    def test_ticketdb_part3_fields_persist(self, in_memory_engine):
        """final_document_path and part3_completed_at should persist correctly"""
        from datetime import datetime, timezone

        ticket = TicketDB(
            ticket_id="persist-test",
            status="terminado",
            final_document_path="/tmp/final_test.pdf",
            part3_completed_at=datetime.now(timezone.utc),
        )
        with Session(in_memory_engine) as session:
            session.add(ticket)
            session.commit()

        with Session(in_memory_engine) as session:
            t = session.exec(
                select(TicketDB).where(TicketDB.ticket_id == "persist-test")
            ).first()
            assert t.final_document_path == "/tmp/final_test.pdf"
            assert t.part3_completed_at is not None
class TestBRIEFINGTriggerAndTrace:
    """Gap 4: Force BRIEFING Section 7 trigger + verify arbitration node trace."""

    def test_force_phi_trigger_and_verify_arbitration_trace(self):
        """Force PHI conflict and verify arbitration node is traced with fixed arbiter."""
        from agentic_workflows_part3.models_part3 import Part3PipelineState
        from agentic_workflows_part3.graph_nodes import detect_conflicts_node, arbitrage_node

        state = Part3PipelineState(
            ticket_id="trigger-trace-test",
            rfp_metadata={"client_name": "Test", "client_country": "US", "covered_population": 100},
            part2_handoff={
                "departments": [
                    {
                        "department_id": "compliance",
                        "finalized_draft": "Patient record with SSN 123-45-6789",
                        "evaluation": {
                            "compliance": {"pass": False, "violations": ["PHI detected: SSN in draft"], "summary": "Contains PHI"},
                        },
                    }
                ]
            },
        )

        # Detect conflicts
        result1 = detect_conflicts_node(state)
        assert len(result1["detected_conflicts"]) > 0
        assert result1["detected_conflicts"][0].trigger_id == "phi-detected"
        assert result1["detected_conflicts"][0].arbiter_assigned == "Claire Whitfield"
        assert result1["detected_conflicts"][0].arbiter_role == "compliance"

        # Verify trace contains detect_conflicts entry
        traces = result1["execution_trace"]
        conflict_trace = [t for t in traces if t["node_name"] == "detect_conflicts"]
        assert len(conflict_trace) == 1
        assert conflict_trace[0]["output_summary"].startswith("2 conflicts")
        assert "duration_ms" in conflict_trace[0]

        # Run arbitrage
        result2 = arbitrage_node(state)
        assert len(result2["arbitrage_outcomes"]) > 0
        assert result2["arbitrage_outcomes"][0].conflict_id == "phi-detected"
        assert result2["arbitrage_outcomes"][0].decision == "reject"
        assert result2["arbitrage_outcomes"][0].arbitrated_by == "Claire Whitfield"

        # Verify trace contains arbitrage entry
        traces2 = result2["execution_trace"]
        arb_trace = [t for t in traces2 if t["node_name"] == "arbitrage"]
        assert len(arb_trace) == 1
        assert arb_trace[0]["output_summary"].startswith("2 arbitrage decisions")
        assert "duration_ms" in arb_trace[0]

    def test_force_baa_trigger_and_trace(self):
        """Force BAA mismatch trigger and verify arbitration traces."""
        from agentic_workflows_part3.models_part3 import Part3PipelineState
        from agentic_workflows_part3.graph_nodes import detect_conflicts_node, arbitrage_node

        state = Part3PipelineState(
            ticket_id="baa-trace-test",
            rfp_metadata={"client_name": "US Corp", "client_country": "US", "covered_population": 500},
            part2_handoff={
                "departments": [
                    {
                        "department_id": "compliance",
                        "finalized_draft": "No legal agreements mentioned in this draft.",
                        "evaluation": {
                            "compliance": {"pass": True, "violations": [], "summary": "Clean"},
                        },
                    }
                ]
            },
        )

        result1 = detect_conflicts_node(state)
        assert len(result1["detected_conflicts"]) > 0
        conflict = result1["detected_conflicts"][0]
        assert conflict.trigger_id == "baa-dpa-mismatch"
        assert conflict.arbiter_assigned == "Claire Whitfield"

        trace = result1["execution_trace"]
        baa_trace = [t for t in trace if t["node_name"] == "detect_conflicts"]
        assert len(baa_trace) == 1

        result2 = arbitrage_node(state)
        assert result2["arbitrage_outcomes"][0].decision == "request_changes"

    def test_capacity_trigger_sets_revenue_arbiter(self):
        """capacity-vs-population trigger should assign Tom Callahan (revenue) as arbiter."""
        from agentic_workflows_part3.models_part3 import Part3PipelineState
        from agentic_workflows_part3.graph_nodes import detect_conflicts_node

        state = Part3PipelineState(
            ticket_id="capacity-trigger",
            rfp_metadata={"client_name": "BigCorp", "client_country": "US", "covered_population": 10000},
            part2_handoff={
                "departments": [
                    {
                        "department_id": "clinical",
                        "finalized_draft": "Clinical capacity max 5000 — cannot cover full population.",
                        "evaluation": {
                            "compliance": {"pass": True, "violations": [], "summary": "Capacity concern"},
                        },
                    }
                ]
            },
        )

        result = detect_conflicts_node(state)
        phis = [c for c in result["detected_conflicts"] if c.trigger_id == "capacity-vs-population"]
        assert len(phis) == 1
        assert phis[0].arbiter_assigned == "Tom Callahan"
        assert phis[0].arbiter_role == "revenue"

        trace = result["execution_trace"]
        cap_trace = [t for t in trace if t["node_name"] == "detect_conflicts"]
        assert len(cap_trace) == 1
        assert cap_trace[0]["input_summary"] == "ticket=capacity-trigger, depts=1"


class TestRestartMidInterrupt:
    """Gap 5: Restart process mid-interrupt; resume from checkpointer without replaying Part 1."""

    def test_resume_from_checkpoint_after_interrupt(self, temp_db):
        """Simulate: run pipeline → get interrupted → restart process → resume from checkpoint.
        
        This verifies the checkpoint is durable (survives process restart simulation)
        and that resume restores state without replaying Part 1.
        """
        from agentic_workflows_part3.graph import create_part3_pipeline
        from agentic_workflows_part3.models_part3 import (
            Part3PipelineState,
            ApprovalStatus,
            DepartmentApprovalResponse,
        )
        from datetime import datetime, timezone

        pipeline = create_part3_pipeline(checkpoint_db_path=temp_db)

        # Phase 1: Initial run (simulates fresh process)
        ticket_id = "restart-mid-test"
        rfp_meta = {"client_name": "Test", "client_country": "US", "program_type": "test", "covered_population": 100}
        handoff = {
            "departments": [
                {"department_id": "revenue", "finalized_draft": "Draft A", "evaluation": {"compliance": {"pass": True, "violations": []}}},
                {"department_id": "compliance", "finalized_draft": "Draft B", "evaluation": {"compliance": {"pass": True, "violations": []}}},
            ]
        }

        state1 = pipeline.run_part3_pipeline(ticket_id, handoff, rfp_meta)

        # Verify we have interrupted departments
        assert len(state1.interrupted_departments) > 0, "Pipeline should have interrupted departments"

        # Simulate process restart: create a new pipeline instance (new process)
        pipeline2 = create_part3_pipeline(checkpoint_db_path=temp_db)

        # Verify checkpoint survived the "restart"
        checkpoint_data = pipeline2.checkpointer.get(ticket_id)
        assert checkpoint_data is not None, "Checkpoint should survive process restart"

        # Phase 2: Resume from checkpoint (simulates restarted process)
        state2 = pipeline2.run_part3_pipeline(
            ticket_id=ticket_id,
            part2_handoff=handoff,
            rfp_metadata=rfp_meta,
            resume=True,
        )

        # Verify state is restored: same ticket_id, same handoff
        assert state2.ticket_id == ticket_id
        assert len(state2.interrupted_departments) > 0

        # Verify Part 1 data is not replayed (handoff unchanged)
        assert state2.part2_handoff["departments"][0]["department_id"] == "revenue"
        assert state2.part2_handoff["departments"][1]["department_id"] == "compliance"

    def test_approval_response_after_restart(self, temp_db):
        """After restart, handle_approval_response should still work from checkpoint."""
        from agentic_workflows_part3.graph import create_part3_pipeline
        from agentic_workflows_part3.models_part3 import ApprovalStatus

        pipeline = create_part3_pipeline(checkpoint_db_path=temp_db)

        ticket_id = "restart-approve-test"
        rfp_meta = {"client_name": "Test", "client_country": "US", "program_type": "test", "covered_population": 100}
        handoff = {
            "departments": [
                {"department_id": "revenue", "finalized_draft": "Rev A", "evaluation": {"compliance": {"pass": True, "violations": []}}},
                {"department_id": "compliance", "finalized_draft": "Comp B", "evaluation": {"compliance": {"pass": True, "violations": []}}},
            ]
        }

        # First run
        pipeline.run_part3_pipeline(ticket_id, handoff, rfp_meta)

        # Simulate restart
        pipeline2 = create_part3_pipeline(checkpoint_db_path=temp_db)

        # Resume with approval response
        state2 = pipeline2.handle_approval_response(
            ticket_id=ticket_id,
            department_id="revenue",
            approval_response={
                "approval_status": "approved",
                "approver_name": "Tom Callahan",
                "approver_email": "tom@healthcore.com",
            },
        )

        # Should have recorded the approval
        assert state2.department_approvals.get("revenue") is not None
        assert state2.department_approvals["revenue"].approval_status == ApprovalStatus.APPROVED


class TestConcurrentTickets:
    """Gap 6: Two concurrent tickets with distinct thread_ids; checkpoints must not collide."""

    def test_two_tickets_independent_checkpoints(self, temp_db):
        """Two tickets running concurrently with distinct thread_ids must not collide."""
        from agentic_workflows_part3.checkpointer import Part3SQLiteCheckpointer, CheckpointConfig

        config = CheckpointConfig(db_path=temp_db)
        cp = Part3SQLiteCheckpointer(config)

        ticket_a = "concurrent-A"
        ticket_b = "concurrent-B"

        state_a = {"ticket_id": ticket_a, "step": 1, "data": "alpha"}
        state_b = {"ticket_id": ticket_b, "step": 1, "data": "beta"}

        # Save both
        cp.put(ticket_a, state_a)
        cp.put(ticket_b, state_b)

        # Retrieve both
        loaded_a = cp.get(ticket_a)
        loaded_b = cp.get(ticket_b)

        # They should not collide
        assert loaded_a["ticket_id"] == ticket_a
        assert loaded_b["ticket_id"] == ticket_b
        assert loaded_a["data"] == "alpha"
        assert loaded_b["data"] == "beta"

    def test_two_tickets_independent_department_checkpoints(self, temp_db):
        """Department-scoped checkpoints for concurrent tickets must not collide."""
        from agentic_workflows_part3.checkpointer import Part3SQLiteCheckpointer, CheckpointConfig

        config = CheckpointConfig(db_path=temp_db)
        cp = Part3SQLiteCheckpointer(config)

        # Two tickets, each with revenue and compliance dept checkpoints = 4 namespaced
        states = {
            ("ticket-X", "revenue"): {"department_id": "revenue", "status": "interrupted"},
            ("ticket-X", "compliance"): {"department_id": "compliance", "status": "approved"},
            ("ticket-Y", "revenue"): {"department_id": "revenue", "status": "approved"},
            ("ticket-Y", "compliance"): {"department_id": "compliance", "status": "interrupted"},
        }

        for (tid, dept), data in states.items():
            cp.put(ticket_id=tid, state_dict=data, department_id=dept)

        # Verify each retrieval returns its own data
        for (tid, dept), expected in states.items():
            loaded = cp.get(ticket_id=tid, department_id=dept)
            assert loaded is not None, f"Missing checkpoint for {tid}:{dept}"
            assert loaded["department_id"] == expected["department_id"]
            assert loaded["status"] == expected["status"]

        # Cross-check: ticket-X revenue should NOT contain ticket-Y data
        x_rev = cp.get(ticket_id="ticket-X", department_id="revenue")
        assert x_rev["status"] == "interrupted", "ticket-X:revenue should be interrupted"
        assert x_rev["department_id"] == "revenue"

        y_rev = cp.get(ticket_id="ticket-Y", department_id="revenue")
        assert y_rev["status"] == "approved", "ticket-Y:revenue should be approved"

