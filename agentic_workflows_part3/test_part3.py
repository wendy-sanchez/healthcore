"""
Unit and Integration Tests for Part 3: Approval, Arbitrage, and Document Synthesis
Hito 9 — HealthCore RFP Intake Pipeline — Parte 3
"""

import pytest
from datetime import datetime
from pathlib import Path
import tempfile
import json

# Import models and pipeline
from .models_part3 import (
    Part3PipelineState,
    ApprovalStatus,
    ConflictTrigger,
    DepartmentApprovalResponse,
    ConflictState,
    ArbitrageOutcome,
    FinalDocument,
    TicketStatusPart3,
)
from .graph import create_part3_pipeline
from .graph_nodes import detect_conflicts_node, arbitrage_node
from .checkpointer import Part3SQLiteCheckpointer, CheckpointConfig


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def temp_db():
    """Create temporary SQLite database for testing"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    yield db_path
    # Cleanup
    Path(db_path).unlink(missing_ok=True)


@pytest.fixture
def sample_rfp_metadata():
    """Sample RFP metadata"""
    return {
        "client_name": "Meridian Manufacturing",
        "client_country": "US",
        "program_type": "occupational_health",
        "covered_population": 800,
        "deadline": "2026-02-15",
        "budget_range": None,
        "currency": "USD",
    }


@pytest.fixture
def sample_part2_handoff_us():
    """Sample Part 2 handoff for US client (requires BAA)"""
    return {
        "departments": [
            {
                "department_id": "revenue",
                "finalized_draft": "Annual contract value: $50,000 USD. Payment terms: net 30.",
                "evaluation": {
                    "readability": {"pass": True, "score": 45.0},
                    "relevance": {"pass": True, "missing_aspects": []},
                    "compliance": {"pass": True, "violations": []},
                    "overall_pass": True,
                },
            },
            {
                "department_id": "clinical",
                "finalized_draft": "Clinical capacity available at Austin clinic. Can cover 800 employees.",
                "evaluation": {
                    "readability": {"pass": True, "score": 48.0},
                    "relevance": {"pass": True, "missing_aspects": []},
                    "compliance": {"pass": True, "violations": []},
                    "overall_pass": True,
                },
            },
            {
                "department_id": "compliance",
                "finalized_draft": "BAA Business Associate Agreement included. HIPAA compliant framework.",
                "evaluation": {
                    "readability": {"pass": True, "score": 42.0},
                    "relevance": {"pass": True, "missing_aspects": []},
                    "compliance": {"pass": True, "violations": []},
                    "overall_pass": True,
                },
            },
        ],
        "synthesis": "Proposal ready for approval",
        "overall_status": "needs_human_review",
    }


@pytest.fixture
def sample_part2_handoff_uk():
    """Sample Part 2 handoff for UK client (requires DPA)"""
    return {
        "departments": [
            {
                "department_id": "revenue",
                "finalized_draft": "Annual contract value: £40,000 GBP. Payment terms: net 30.",
                "evaluation": {
                    "readability": {"pass": True, "score": 45.0},
                    "relevance": {"pass": True, "missing_aspects": []},
                    "compliance": {"pass": True, "violations": []},
                    "overall_pass": True,
                },
            },
            {
                "department_id": "clinical",
                "finalized_draft": "Clinical capacity available at London clinic. Can cover 500 students.",
                "evaluation": {
                    "readability": {"pass": True, "score": 48.0},
                    "relevance": {"pass": True, "missing_aspects": []},
                    "compliance": {"pass": True, "violations": []},
                    "overall_pass": True,
                },
            },
            {
                "department_id": "compliance",
                "finalized_draft": "DPA Data Processing Agreement with UK GDPR compliance.",
                "evaluation": {
                    "readability": {"pass": True, "score": 42.0},
                    "relevance": {"pass": True, "missing_aspects": []},
                    "compliance": {"pass": True, "violations": []},
                    "overall_pass": True,
                },
            },
        ],
    }


@pytest.fixture
def sample_part2_handoff_no_baa():
    """Sample Part 2 handoff for US client WITHOUT BAA (conflict trigger)"""
    return {
        "departments": [
            {
                "department_id": "revenue",
                "finalized_draft": "Annual contract value: $50,000 USD. No legal clauses.",
                "evaluation": {
                    "readability": {"pass": True, "score": 45.0},
                    "relevance": {"pass": True, "missing_aspects": []},
                    "compliance": {"pass": False, "violations": ["Missing BAA for US client"]},
                    "overall_pass": False,
                },
            },
            {
                "department_id": "clinical",
                "finalized_draft": "Clinical capacity available.",
                "evaluation": {
                    "readability": {"pass": True, "score": 48.0},
                    "relevance": {"pass": True, "missing_aspects": []},
                    "compliance": {"pass": True, "violations": []},
                    "overall_pass": True,
                },
            },
            {
                "department_id": "compliance",
                "finalized_draft": "No contractual clauses included.",
                "evaluation": {
                    "readability": {"pass": True, "score": 42.0},
                    "relevance": {"pass": True, "missing_aspects": []},
                    "compliance": {"pass": False, "violations": ["BAA", "Missing BAA for US client"]},
                    "overall_pass": False,
                },
            },
        ],
    }


@pytest.fixture
def sample_part2_handoff_with_phi():
    """Sample Part 2 handoff WITH PHI (conflict trigger)"""
    return {
        "departments": [
            {
                "department_id": "compliance",
                "finalized_draft": "Patient John Doe (ID: 12345) with diabetes diagnosis requires coverage.",
                "evaluation": {
                    "readability": {"pass": True, "score": 42.0},
                    "relevance": {"pass": True, "missing_aspects": []},
                    "compliance": {"pass": False, "violations": ["PHI detected: patient name and diagnosis"]},
                    "overall_pass": False,
                },
            },
        ],
    }


# ============================================================================
# Tests: Conflict Detection
# ============================================================================

def test_detect_conflicts_baa_missing_us_client(sample_rfp_metadata, sample_part2_handoff_no_baa):
    """Test: BAA-DPA mismatch trigger fires for US client without BAA"""
    state = Part3PipelineState(
        ticket_id="test-ticket-1",
        rfp_metadata=sample_rfp_metadata,
        part2_handoff=sample_part2_handoff_no_baa,
    )
    
    result = detect_conflicts_node(state)
    state.detected_conflicts = result["detected_conflicts"]
    
    # Should detect BAA mismatch
    assert len(state.detected_conflicts) > 0
    assert any(c.trigger_id == ConflictTrigger.BAA_DPA_MISMATCH for c in state.detected_conflicts)
    
    # Arbiter should be compliance
    baa_conflict = next(c for c in state.detected_conflicts if c.trigger_id == ConflictTrigger.BAA_DPA_MISMATCH)
    assert baa_conflict.arbiter_assigned == "Claire Whitfield"
    assert baa_conflict.arbiter_role == "compliance"


def test_detect_conflicts_phi_detected(sample_rfp_metadata, sample_part2_handoff_with_phi):
    """Test: PHI-detected trigger fires for patient data in sections"""
    state = Part3PipelineState(
        ticket_id="test-ticket-2",
        rfp_metadata=sample_rfp_metadata,
        part2_handoff=sample_part2_handoff_with_phi,
    )
    
    result = detect_conflicts_node(state)
    state.detected_conflicts = result["detected_conflicts"]
    
    # Should detect PHI
    assert len(state.detected_conflicts) > 0
    assert any(c.trigger_id == ConflictTrigger.PHI_DETECTED for c in state.detected_conflicts)


def test_detect_conflicts_no_conflicts_valid_proposal(sample_rfp_metadata, sample_part2_handoff_us):
    """Test: No conflicts for valid US proposal with BAA"""
    state = Part3PipelineState(
        ticket_id="test-ticket-3",
        rfp_metadata=sample_rfp_metadata,
        part2_handoff=sample_part2_handoff_us,
    )
    
    result = detect_conflicts_node(state)
    state.detected_conflicts = result["detected_conflicts"]
    
    # Should have no BAA mismatch (BAA is present)
    baa_conflicts = [c for c in state.detected_conflicts if c.trigger_id == ConflictTrigger.BAA_DPA_MISMATCH]
    assert len(baa_conflicts) == 0


# ============================================================================
# Tests: Arbitrage
# ============================================================================

def test_arbitrage_phi_rejection(sample_rfp_metadata, sample_part2_handoff_with_phi):
    """Test: PHI detected -> REJECT decision (parada dura)"""
    state = Part3PipelineState(
        ticket_id="test-ticket-4",
        rfp_metadata=sample_rfp_metadata,
        part2_handoff=sample_part2_handoff_with_phi,
    )
    
    # Detect conflicts first
    result = detect_conflicts_node(state)
    state.detected_conflicts = result["detected_conflicts"]
    
    # Arbitrage
    result = arbitrage_node(state)
    state.arbitrage_outcomes = result["arbitrage_outcomes"]
    
    # PHI should trigger REJECT
    phi_outcome = next(o for o in state.arbitrage_outcomes if o.conflict_id == ConflictTrigger.PHI_DETECTED)
    assert phi_outcome.decision == "reject"


def test_arbitrage_baa_mismatch_request_changes(sample_rfp_metadata, sample_part2_handoff_no_baa):
    """Test: BAA mismatch -> REQUEST_CHANGES decision"""
    state = Part3PipelineState(
        ticket_id="test-ticket-5",
        rfp_metadata=sample_rfp_metadata,
        part2_handoff=sample_part2_handoff_no_baa,
    )
    
    # Detect conflicts
    result = detect_conflicts_node(state)
    state.detected_conflicts = result["detected_conflicts"]
    
    # Arbitrage
    result = arbitrage_node(state)
    state.arbitrage_outcomes = result["arbitrage_outcomes"]
    
    # BAA mismatch should trigger REQUEST_CHANGES
    baa_outcome = next(o for o in state.arbitrage_outcomes if o.conflict_id == ConflictTrigger.BAA_DPA_MISMATCH)
    assert baa_outcome.decision == "request_changes"


# ============================================================================
# Tests: Checkpointing
# ============================================================================

def test_checkpointer_put_get(temp_db):
    """Test: Checkpointer can save and retrieve state"""
    checkpointer = Part3SQLiteCheckpointer(CheckpointConfig(db_path=temp_db))
    
    state_dict = {
        "ticket_id": "test-1",
        "rfp_metadata": {"client_name": "Test Corp"},
        "part2_handoff": {},
        "department_approvals": {},
        "detected_conflicts": [],
        "arbitrage_outcomes": [],
        "interrupted_departments": {},
        "all_departments_approved": False,
        "final_document": None,
        "execution_trace": [],
    }
    
    # Save
    checkpoint_id = checkpointer.put("test-1", state_dict)
    assert checkpoint_id is not None
    
    # Retrieve
    retrieved = checkpointer.get("test-1")
    assert retrieved is not None
    assert retrieved["ticket_id"] == "test-1"
    assert retrieved["rfp_metadata"]["client_name"] == "Test Corp"


def test_checkpointer_namespacing(temp_db):
    """Test: Checkpointer maintains separate namespaces by department"""
    checkpointer = Part3SQLiteCheckpointer(CheckpointConfig(db_path=temp_db))
    
    state_dict_1 = {"ticket_id": "test-1", "department_id": "revenue"}
    state_dict_2 = {"ticket_id": "test-1", "department_id": "clinical"}
    
    # Save both
    checkpointer.put("test-1", state_dict_1, department_id="revenue")
    checkpointer.put("test-1", state_dict_2, department_id="clinical")
    
    # Retrieve separately
    retrieved_revenue = checkpointer.get("test-1", department_id="revenue")
    retrieved_clinical = checkpointer.get("test-1", department_id="clinical")
    
    assert retrieved_revenue["department_id"] == "revenue"
    assert retrieved_clinical["department_id"] == "clinical"


def test_checkpointer_approval_history(temp_db):
    """Test: Checkpointer records approval history"""
    checkpointer = Part3SQLiteCheckpointer(CheckpointConfig(db_path=temp_db))
    
    now = datetime.utcnow().isoformat()
    checkpointer.record_approval(
        ticket_id="test-1",
        department_id="revenue",
        approval_status="approved",
        approver_name="Tom Callahan",
        approved_at_iso=now,
    )
    
    history = checkpointer.get_approval_history("test-1")
    assert len(history) == 1
    assert history[0]["department_id"] == "revenue"
    assert history[0]["approver_name"] == "Tom Callahan"


# ============================================================================
# Tests: Pipeline Integration
# ============================================================================

def test_part3_pipeline_no_conflicts(temp_db, sample_rfp_metadata, sample_part2_handoff_us):
    """Test: Full pipeline execution with no conflicts"""
    pipeline = create_part3_pipeline(checkpoint_db_path=temp_db)
    
    state = pipeline.run_part3_pipeline(
        ticket_id="test-ticket-6",
        part2_handoff=sample_part2_handoff_us,
        rfp_metadata=sample_rfp_metadata,
    )
    
    # Should detect no conflicts
    assert len(state.detected_conflicts) == 0
    # Should reach approval stage
    assert len(state.interrupted_departments) == 3  # All 3 departments waiting


def test_part3_pipeline_with_conflicts(temp_db, sample_rfp_metadata, sample_part2_handoff_no_baa):
    """Test: Full pipeline with conflict detection and arbitrage"""
    pipeline = create_part3_pipeline(checkpoint_db_path=temp_db)
    
    state = pipeline.run_part3_pipeline(
        ticket_id="test-ticket-7",
        part2_handoff=sample_part2_handoff_no_baa,
        rfp_metadata=sample_rfp_metadata,
    )
    
    # Should detect BAA mismatch
    assert len(state.detected_conflicts) > 0
    # Should have arbitrage outcome
    assert len(state.arbitrage_outcomes) > 0


def test_part3_parallel_department_approvals(temp_db, sample_rfp_metadata, sample_part2_handoff_us):
    """Test: Departments can be approved independently (paralelismo real)"""
    pipeline = create_part3_pipeline(checkpoint_db_path=temp_db)
    
    # Initial run: sets up approvals
    state = pipeline.run_part3_pipeline(
        ticket_id="test-ticket-8",
        part2_handoff=sample_part2_handoff_us,
        rfp_metadata=sample_rfp_metadata,
    )
    
    # Approve revenue independently
    state = pipeline.handle_approval_response(
        ticket_id="test-ticket-8",
        department_id="revenue",
        approval_response={
            "approval_status": "approved",
            "approver_name": "Tom Callahan",
            "approver_email": "tom@healthcore.com",
        },
    )
    
    assert state.department_approvals["revenue"].approval_status == ApprovalStatus.APPROVED
    # Clinical and compliance should still be pending
    assert "clinical" not in state.department_approvals or state.department_approvals.get("clinical") is None
    assert "compliance" not in state.department_approvals or state.department_approvals.get("compliance") is None


def test_part3_all_approvals_then_synthesis(temp_db, sample_rfp_metadata, sample_part2_handoff_us):
    """Test: Document synthesized after all approvals"""
    pipeline = create_part3_pipeline(checkpoint_db_path=temp_db)
    
    # Initial run
    state = pipeline.run_part3_pipeline(
        ticket_id="test-ticket-9",
        part2_handoff=sample_part2_handoff_us,
        rfp_metadata=sample_rfp_metadata,
    )
    
    # Approve all three departments
    for dept_id in ["revenue", "clinical", "compliance"]:
        state = pipeline.handle_approval_response(
            ticket_id="test-ticket-9",
            department_id=dept_id,
            approval_response={
                "approval_status": "approved",
                "approver_name": f"Approver {dept_id}",
                "approver_email": f"{dept_id}@healthcore.com",
            },
        )
    
    # After all approvals, should have final document
    assert state.final_document is not None
    assert state.all_departments_approved is True
    assert len(state.final_document.sections) == 3


# ============================================================================
# Tests: Execution Trace (Telemetry)
# ============================================================================

def test_execution_trace_captured(temp_db, sample_rfp_metadata, sample_part2_handoff_us):
    """Test: Execution trace is captured for all nodes"""
    pipeline = create_part3_pipeline(checkpoint_db_path=temp_db)
    
    state = pipeline.run_part3_pipeline(
        ticket_id="test-ticket-10",
        part2_handoff=sample_part2_handoff_us,
        rfp_metadata=sample_rfp_metadata,
    )
    
    # Should have trace entries for each node
    assert len(state.execution_trace) > 0
    node_names = {entry.get("node_name") for entry in state.execution_trace}
    assert "detect_conflicts" in node_names
    assert "arbitrage" in node_names
    assert "request_approvals" in node_names


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
