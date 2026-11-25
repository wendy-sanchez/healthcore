"""
End-to-End Test: Parts 1-3 Complete RFP Workflow
Hito 9 — HealthCore RFP Intake Pipeline

This script simulates a complete RFP flow from Parts 1-3:
1. Part 1: RFP classification and metadata extraction
2. Part 2: Draft generation and evaluation
3. Part 3: Approvals, arbitrage, and document synthesis

Run with: python e2e_script.py
"""

import sys
import json
from datetime import datetime
from pathlib import Path

# Import Part 3 components
from .models_part3 import (
    Part3PipelineState,
    ApprovalStatus,
    TicketStatusPart3,
)
from .graph import create_part3_pipeline
from .checkpointer import Part3SQLiteCheckpointer, CheckpointConfig


def simulate_part1_output():
    """Simulate Part 1 output: RFP classification and metadata"""
    return {
        "ticket_id": "rfp-e2e-test-001",
        "status": "analisis_completo",
        "rfp_metadata": {
            "client_name": "Meridian Manufacturing, Inc.",
            "client_country": "US",
            "program_type": "occupational_health",
            "covered_population": 800,
            "deadline": "2026-02-15",
            "budget_range": None,
            "currency": "USD",
        },
        "departments": ["revenue", "clinical", "compliance"],
    }


def simulate_part2_output(ticket_id, rfp_metadata):
    """Simulate Part 2 output: Drafts and evaluations"""
    return {
        "ticket_id": ticket_id,
        "status": "needs_human_review",
        "rfp_metadata": rfp_metadata,
        "departments": [
            {
                "department_id": "revenue",
                "finalized_draft": """
                REVENUE PROPOSAL - Meridian Manufacturing
                
                Annual Contract Value: $50,000 USD
                Duration: 12 months
                Payment Terms: Net 30
                Renewal: Automatic upon agreement renewal
                
                Business Associate Agreement (BAA) included per HIPAA requirements.
                All charges subject to annual CPI adjustment.
                """,
                "evaluation": {
                    "readability": {"pass": True, "score": 45.2},
                    "relevance": {"pass": True, "missing_aspects": []},
                    "compliance": {"pass": True, "violations": []},
                    "overall_pass": True,
                },
            },
            {
                "department_id": "clinical",
                "finalized_draft": """
                CLINICAL FEASIBILITY - Meridian Manufacturing
                
                Proposed Service Locations: Austin, Texas clinic
                Clinical Capacity: Can accommodate 800 employees with existing staff
                Staffing Model: Dedicated occupational health physician + nurse practitioner
                Hours: 8 AM - 5 PM, Monday-Friday
                Emergency Coverage: After-hours telehealth via partner network
                
                Recommendation: FEASIBLE. Existing capacity sufficient for stated population.
                """,
                "evaluation": {
                    "readability": {"pass": True, "score": 48.1},
                    "relevance": {"pass": True, "missing_aspects": []},
                    "compliance": {"pass": True, "violations": []},
                    "overall_pass": True,
                },
            },
            {
                "department_id": "compliance",
                "finalized_draft": """
                REGULATORY & DATA GOVERNANCE - Meridian Manufacturing
                
                Jurisdiction: United States (HIPAA)
                Regulatory Framework: HIPAA Privacy Rule + Security Rule
                Business Associate Agreement: Included below
                
                [BAA Terms Summary]
                - HealthCore acts as Business Associate
                - Meridian is Covered Entity
                - Data encryption at rest and in transit
                - 60-day breach notification protocol
                - Annual compliance audit by third party
                
                Data Retention: 7 years per industry standard
                Audit Rights: Meridian may audit HealthCore's compliance annually
                """,
                "evaluation": {
                    "readability": {"pass": True, "score": 42.3},
                    "relevance": {"pass": True, "missing_aspects": []},
                    "compliance": {"pass": True, "violations": []},
                    "overall_pass": True,
                },
            },
        ],
        "synthesis": "All sections passed evaluation. Ready for departmental approvals.",
    }


def run_e2e_workflow():
    """Execute complete workflow Parts 1-3"""
    
    print("=" * 80)
    print("HealthCore RFP Intake Workflow — End-to-End Test (Parts 1-3)")
    print("=" * 80)
    
    # ---- PART 1: Classification ----
    print("\n[PART 1] RFP Classification and Metadata Extraction")
    part1_output = simulate_part1_output()
    print(f"✓ Ticket created: {part1_output['ticket_id']}")
    print(f"✓ Client: {part1_output['rfp_metadata']['client_name']}")
    print(f"✓ Country: {part1_output['rfp_metadata']['client_country']}")
    print(f"✓ Status: {part1_output['status']}")
    
    # ---- PART 2: Generation & Evaluation ----
    print("\n[PART 2] Draft Generation and Evaluation")
    part2_output = simulate_part2_output(
        part1_output["ticket_id"],
        part1_output["rfp_metadata"],
    )
    print(f"✓ Drafts generated for {len(part2_output['departments'])} departments")
    for dept in part2_output["departments"]:
        status = "✓ PASS" if dept["evaluation"]["overall_pass"] else "✗ FAIL"
        print(f"  {status} {dept['department_id'].upper()}")
    
    # ---- PART 3: Approvals and Synthesis ----
    print("\n[PART 3] Approvals, Arbitrage, and Document Synthesis")
    
    # Create pipeline with temporary database
    temp_db = "/tmp/healthcore_e2e_test.db"
    pipeline = create_part3_pipeline(checkpoint_db_path=temp_db)
    
    # Initial Part 3 run
    print(f"\n[Part 3.1] Conflict Detection & Arbitrage")
    state = pipeline.run_part3_pipeline(
        ticket_id=part1_output["ticket_id"],
        part2_handoff=part2_output,
        rfp_metadata=part1_output["rfp_metadata"],
    )
    
    print(f"✓ Conflicts detected: {len(state.detected_conflicts)}")
    for conflict in state.detected_conflicts:
        print(f"  - {conflict.trigger_id.value}")
    
    print(f"✓ Arbitrage outcomes: {len(state.arbitrage_outcomes)}")
    for outcome in state.arbitrage_outcomes:
        print(f"  - {outcome.conflict_id.value}: {outcome.decision}")
    
    # Approval requests
    print(f"\n[Part 3.2] Requesting Departmental Approvals")
    print(f"✓ Approvals requested for {len(state.interrupted_departments)} departments")
    for dept_id, dept_state in state.interrupted_departments.items():
        print(f"  → {dept_id.upper()} (awaiting approval from team lead)")
    
    # Simulate approvals
    print(f"\n[Part 3.3] Processing Approvals (simulated)")
    approval_sequence = [
        ("revenue", "Tom Callahan", "tom@healthcore.com"),
        ("compliance", "Claire Whitfield", "claire@healthcore.com"),
        ("clinical", "Dr. Marcus Reid", "marcus@healthcore.com"),
    ]
    
    for dept_id, approver_name, approver_email in approval_sequence:
        state = pipeline.handle_approval_response(
            ticket_id=part1_output["ticket_id"],
            department_id=dept_id,
            approval_response={
                "approval_status": "approved",
                "approver_name": approver_name,
                "approver_email": approver_email,
            },
        )
        print(f"✓ {dept_id.upper()} approved by {approver_name}")
    
    # Final document synthesis
    print(f"\n[Part 3.4] Document Synthesis")
    if state.final_document:
        print(f"✓ Final proposal generated")
        print(f"  Client: {state.final_document.client_name}")
        print(f"  Sections: {len(state.final_document.sections)}")
        print(f"  Generated: {state.final_document.generated_at_utc.isoformat()}")
        print(f"  Path: {state.final_document.document_path}")
    else:
        print("✗ No final document generated")
    
    # Execution trace
    print(f"\n[Part 3.5] Execution Trace")
    print(f"✓ Total trace entries: {len(state.execution_trace)}")
    for entry in state.execution_trace:
        print(f"  → {entry['node_name']}: {entry['status']}")
    
    # Summary
    print("\n" + "=" * 80)
    print("WORKFLOW SUMMARY")
    print("=" * 80)
    print(f"Ticket ID: {state.ticket_id}")
    print(f"Client: {state.rfp_metadata.get('client_name')}")
    print(f"Status: {state.execution_trace[-1]['status'] if state.execution_trace else 'unknown'}")
    print(f"All Approvals Collected: {state.all_departments_approved}")
    print(f"Final Document Ready: {state.final_document is not None}")
    print(f"Execution Duration: {len(state.execution_trace)} steps")
    print("=" * 80)
    
    # Return summary
    return {
        "ticket_id": state.ticket_id,
        "status": "COMPLETED" if state.final_document else "INCOMPLETE",
        "conflicts_detected": len(state.detected_conflicts),
        "arbitrage_decisions": len(state.arbitrage_outcomes),
        "approvals": {
            dept_id: approval.approval_status.value
            for dept_id, approval in state.department_approvals.items()
            if approval
        },
        "document_path": state.final_document.document_path if state.final_document else None,
    }


if __name__ == "__main__":
    try:
        result = run_e2e_workflow()
        print("\n✓ E2E Test Completed Successfully")
        print(json.dumps(result, indent=2))
        sys.exit(0)
    except Exception as e:
        print(f"\n✗ E2E Test Failed: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
