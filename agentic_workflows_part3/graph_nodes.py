"""
LangGraph nodes for Part 3: Approval, Arbitrage, and Document Synthesis
Hito 9 — HealthCore RFP Intake Pipeline — Parte 3
"""

import time
from datetime import datetime
from typing import Optional, Dict, Any, List
from .models_part3 import (
    Part3PipelineState,
    DepartmentApprovalResponse,
    ConflictState,
    ConflictTrigger,
    ArbitrageOutcome,
    ArbitrageDecision,
    FinalDocument,
    FinalDocumentSection,
    ApprovalStatus,
    add_trace,
)


# ============================================================================
# Constants: Department Approvers (from CONTEXT.md §2.1)
# ============================================================================

DEPARTMENT_APPROVERS = {
    "revenue": {"name": "Tom Callahan", "email": "tom@healthcore.com", "role": "Revenue Cycle Director"},
    "clinical": {"name": "Dr. Marcus Reid", "email": "marcus@healthcore.com", "role": "Clinical Operations Director"},
    "compliance": {"name": "Claire Whitfield", "email": "claire@healthcore.com", "role": "Compliance Director"},
}

# Arbitrage assignment: compliance always for PHI/regulatory, revenue for capacity/volume
ARBITRAGE_ASSIGNMENT = {
    ConflictTrigger.PHI_DETECTED: {"arbiter": "Claire Whitfield", "role": "compliance"},
    ConflictTrigger.BAA_DPA_MISMATCH: {"arbiter": "Claire Whitfield", "role": "compliance"},
    ConflictTrigger.CAPACITY_VS_POPULATION: {"arbiter": "Tom Callahan", "role": "revenue"},
}

MAX_APPROVAL_ITERATIONS = 3


# ============================================================================
# Node: Detect Conflicts
# ============================================================================

def detect_conflicts_node(state: Part3PipelineState) -> Dict[str, Any]:
    """
    Scan Part 2 handoff for detectable conflict triggers in structured state.
    Returns updated state with detected_conflicts populated.
    
    Triggers:
    - phi-detected: Any department section has contains_phi=true or PHI patterns
    - baa-dpa-mismatch: US client without BAA or UK client without DPA
    - capacity-vs-population: Clinical capacity < population or missing data
    """
    start = time.time()
    conflicts: List[ConflictState] = []
    
    # Extract Part 2 departments
    part2_departments = state.part2_handoff.get("departments", [])
    client_country = state.rfp_metadata.get("client_country")
    covered_population = state.rfp_metadata.get("covered_population")
    
    # ---- TRIGGER 1: PHI Detection ----
    for dept in part2_departments:
        dept_id = dept.get("department_id")
        evaluation = dept.get("evaluation", {})
        compliance_eval = evaluation.get("compliance", {})
        
        # Check if compliance eval flagged PHI
        if compliance_eval.get("pass") is False:
            violations = compliance_eval.get("violations", [])
            for violation in violations:
                if "phi" in violation.lower() or "contains_phi" in str(violation).lower():
                    conflict = ConflictState(
                        trigger_id=ConflictTrigger.PHI_DETECTED,
                        ticket_id=state.ticket_id,
                        affected_departments=[dept_id],
                        detected_at_utc=datetime.utcnow(),
                        details={
                            "violation": violation,
                            "department": dept_id,
                            "section": dept.get("finalized_draft", "")[:200],  # First 200 chars
                        },
                        arbiter_assigned=ARBITRAGE_ASSIGNMENT[ConflictTrigger.PHI_DETECTED]["arbiter"],
                        arbiter_role=ARBITRAGE_ASSIGNMENT[ConflictTrigger.PHI_DETECTED]["role"],
                    )
                    conflicts.append(conflict)
    
    # ---- TRIGGER 2: BAA/DPA Mismatch ----
    if client_country == "US":
        # Must have BAA in some form
        baa_found = False
        for dept in part2_departments:
            draft = dept.get("finalized_draft", "").lower()
            if "baa" in draft or "business associate agreement" in draft:
                baa_found = True
                break
        
        if not baa_found:
            conflict = ConflictState(
                trigger_id=ConflictTrigger.BAA_DPA_MISMATCH,
                ticket_id=state.ticket_id,
                affected_departments=[d.get("department_id") for d in part2_departments],
                detected_at_utc=datetime.utcnow(),
                details={
                    "expected": "BAA (Business Associate Agreement)",
                    "client_country": "US",
                    "found": False,
                },
                arbiter_assigned=ARBITRAGE_ASSIGNMENT[ConflictTrigger.BAA_DPA_MISMATCH]["arbiter"],
                arbiter_role=ARBITRAGE_ASSIGNMENT[ConflictTrigger.BAA_DPA_MISMATCH]["role"],
            )
            conflicts.append(conflict)
    
    elif client_country == "UK":
        # Must have DPA or UK GDPR reference
        dpa_found = False
        for dept in part2_departments:
            draft = dept.get("finalized_draft", "").lower()
            if "dpa" in draft or "uk gdpr" in draft or "data processing agreement" in draft:
                dpa_found = True
                break
        
        if not dpa_found:
            conflict = ConflictState(
                trigger_id=ConflictTrigger.BAA_DPA_MISMATCH,
                ticket_id=state.ticket_id,
                affected_departments=[d.get("department_id") for d in part2_departments],
                detected_at_utc=datetime.utcnow(),
                details={
                    "expected": "DPA (Data Processing Agreement) or UK GDPR reference",
                    "client_country": "UK",
                    "found": False,
                },
                arbiter_assigned=ARBITRAGE_ASSIGNMENT[ConflictTrigger.BAA_DPA_MISMATCH]["arbiter"],
                arbiter_role=ARBITRAGE_ASSIGNMENT[ConflictTrigger.BAA_DPA_MISMATCH]["role"],
            )
            conflicts.append(conflict)
    
    # ---- TRIGGER 3: Capacity vs. Population ----
    if covered_population and covered_population > 0:
        # Check if clinical section mentions capacity
        clinical_depts = [d for d in part2_departments if d.get("department_id") == "clinical"]
        if clinical_depts:
            clinical_draft = clinical_depts[0].get("finalized_draft", "").lower()
            # Heuristic: if draft says "cannot" or "insufficient" or lists capacity < population
            if any(word in clinical_draft for word in ["cannot", "insufficient", "unable to", "max capacity"]):
                conflict = ConflictState(
                    trigger_id=ConflictTrigger.CAPACITY_VS_POPULATION,
                    ticket_id=state.ticket_id,
                    affected_departments=["clinical", "revenue"],
                    detected_at_utc=datetime.utcnow(),
                    details={
                        "covered_population": covered_population,
                        "clinical_capacity_indicator": "insufficient or not explicitly confirmed",
                        "action": "Reduce population or add sites",
                    },
                    arbiter_assigned=ARBITRAGE_ASSIGNMENT[ConflictTrigger.CAPACITY_VS_POPULATION]["arbiter"],
                    arbiter_role=ARBITRAGE_ASSIGNMENT[ConflictTrigger.CAPACITY_VS_POPULATION]["role"],
                )
                conflicts.append(conflict)
    
    # Update state
    state.detected_conflicts.extend(conflicts)
    duration_ms = (time.time() - start) * 1000
    add_trace(
        state,
        node_name="detect_conflicts",
        agent_id=None,
        input_summary=f"ticket={state.ticket_id}, depts={len(part2_departments)}",
        output_summary=f"{len(conflicts)} conflicts detected",
        duration_ms=duration_ms,
    )
    
    return {"detected_conflicts": state.detected_conflicts, "execution_trace": state.execution_trace}


# ============================================================================
# Node: Arbitrage (Human-fixed decision point)
# ============================================================================

def arbitrage_node(state: Part3PipelineState) -> Dict[str, Any]:
    """
    Process detected conflicts with fixed arbiters (not LLM voting).
    For each conflict, make a decision (APPROVE, REQUEST_CHANGES, REJECT).
    
    This is a simplified simulation; in production, would wait for human input.
    """
    start = time.time()
    outcomes: List[ArbitrageOutcome] = []
    
    for conflict in state.detected_conflicts:
        # Simulate arbitrage decision based on trigger type
        if conflict.trigger_id == ConflictTrigger.PHI_DETECTED:
            # PHI is non-negotiable: parada dura
            decision = ArbitrageDecision.REJECT
            rationale = "PHI detected in proposal: cannot proceed. Sections must be redacted."
            next_step = "request_changes_all_departments"
        
        elif conflict.trigger_id == ConflictTrigger.BAA_DPA_MISMATCH:
            # Missing contractual clause: forced request_changes
            decision = ArbitrageDecision.REQUEST_CHANGES
            details = conflict.details
            if details.get("client_country") == "US":
                rationale = "US client requires BAA. Compliance must add Business Associate Agreement clause."
            else:
                rationale = "UK client requires DPA. Compliance must add Data Processing Agreement per UK GDPR."
            next_step = "resume_generation_compliance"
        
        elif conflict.trigger_id == ConflictTrigger.CAPACITY_VS_POPULATION:
            # Capacity issue: allow continuation but flag for decision
            decision = ArbitrageDecision.REQUEST_CHANGES
            details = conflict.details
            population = details.get("covered_population", "unknown")
            rationale = f"Clinical capacity may not support {population} employees. Recommend: reduce scope or add sites."
            next_step = "resume_generation_revenue_clinical"
        
        else:
            decision = ArbitrageDecision.APPROVE
            rationale = "Conflict resolved by default approval"
            next_step = "continue"
        
        outcome = ArbitrageOutcome(
            conflict_id=conflict.trigger_id,
            decision=decision,
            rationale=rationale,
            arbitrated_by=conflict.arbiter_assigned,
            arbitrated_at_utc=datetime.utcnow(),
            next_step=next_step,
        )
        outcomes.append(outcome)
    
    # Update state
    state.arbitrage_outcomes.extend(outcomes)
    duration_ms = (time.time() - start) * 1000
    add_trace(
        state,
        node_name="arbitrage",
        agent_id=None,
        input_summary=f"{len(state.detected_conflicts)} conflicts",
        output_summary=f"{len(outcomes)} arbitrage decisions",
        duration_ms=duration_ms,
    )
    
    return {"arbitrage_outcomes": state.arbitrage_outcomes, "execution_trace": state.execution_trace}


# ============================================================================
# Node: Request Approvals (by department, with interrupt)
# ============================================================================

def request_approvals_node(state: Part3PipelineState) -> Dict[str, Any]:
    """
    For each department, prepare approval request and pause the graph.
    Uses interrupt() to signal the graph to stop and wait for human input.
    
    Each department is handled independently (no blocking between depts).
    """
    start = time.time()
    
    # List departments that need approval
    part2_departments = state.part2_handoff.get("departments", [])
    
    for dept in part2_departments:
        dept_id = dept.get("department_id")
        
        # Skip if already approved
        if dept_id in state.department_approvals and state.department_approvals[dept_id]:
            continue
        
        # Prepare summary for human
        draft_content = dept.get("finalized_draft", "")
        evaluation = dept.get("evaluation", {})
        eval_summary = f"Readability: {evaluation.get('readability', {}).get('details', 'N/A')} | " \
                       f"Relevance: {evaluation.get('relevance', {}).get('details', 'N/A')} | " \
                       f"Compliance: {evaluation.get('compliance', {}).get('details', 'N/A')}"
        
        approver_info = DEPARTMENT_APPROVERS.get(dept_id, {})
        
        # Mark department as awaiting approval
        from .models_part3 import InterruptedDepartmentState
        state.interrupted_departments[dept_id] = InterruptedDepartmentState(
            ticket_id=state.ticket_id,
            department_id=dept_id,
            draft_content=draft_content,
            is_interrupted=True,
            interrupt_reason="awaiting_approval",
            checkpointer_thread_id=f"rfp-{state.ticket_id}:{dept_id}",
        )
        
        # In production: send approval request to email, Slack, or approval UI
        # For now, just log it
        print(f"[INTERRUPT] Approval needed for {dept_id}")
        print(f"  Approver: {approver_info.get('name', 'Unknown')}")
        print(f"  Eval: {eval_summary[:100]}...")
    
    duration_ms = (time.time() - start) * 1000
    add_trace(
        state,
        node_name="request_approvals",
        agent_id=None,
        input_summary=f"{len(part2_departments)} departments",
        output_summary=f"{len(state.interrupted_departments)} awaiting approval",
        duration_ms=duration_ms,
    )
    
    return {"interrupted_departments": state.interrupted_departments, "execution_trace": state.execution_trace}


# ============================================================================
# Node: Check All Approvals Collected
# ============================================================================

def check_approvals_complete(state: Part3PipelineState) -> bool:
    """
    Routing condition: returns True if all departments have been approved.
    """
    part2_departments = state.part2_handoff.get("departments", [])
    required_depts = {d.get("department_id") for d in part2_departments}
    
    # Check compliance is in required (always mandatory per CONTEXT.md)
    if "compliance" not in required_depts:
        required_depts.add("compliance")
    
    # All required departments must have approval
    for dept_id in required_depts:
        approval = state.department_approvals.get(dept_id)
        if approval is None or approval.approval_status != ApprovalStatus.APPROVED:
            return False
    
    return True


# ============================================================================
# Node: Synthesize Final Document
# ============================================================================

def synthesize_document_node(state: Part3PipelineState) -> Dict[str, Any]:
    """
    After all approvals collected, synthesize the final proposal document.
    Consolidates approved sections with metadata and generates output.
    """
    start = time.time()
    
    part2_departments = state.part2_handoff.get("departments", [])
    sections: List[FinalDocumentSection] = []
    
    for dept in part2_departments:
        dept_id = dept.get("department_id")
        approval = state.department_approvals.get(dept_id)
        
        if approval and approval.approval_status == ApprovalStatus.APPROVED:
            section_title = {
                "revenue": "Financial & Commercial Terms",
                "clinical": "Clinical Operations & Feasibility",
                "compliance": "Regulatory & Data Governance",
            }.get(dept_id, "Department Section")
            
            section = FinalDocumentSection(
                department_id=dept_id,
                title=section_title,
                content=dept.get("finalized_draft", ""),
                approval_status=ApprovalStatus.APPROVED,
                approved_by=approval.approver_name,
                approved_at_utc=approval.approved_at_utc,
            )
            sections.append(section)
    
    # Create final document
    document = FinalDocument(
        ticket_id=state.ticket_id,
        client_name=state.rfp_metadata.get("client_name", "Unknown Client"),
        client_country=state.rfp_metadata.get("client_country", "US"),
        program_type=state.rfp_metadata.get("program_type", "unknown"),
        sections=sections,
        generated_at_utc=datetime.utcnow(),
        document_path=f"/tmp/proposals/{state.ticket_id}_final_proposal.md",
        is_complete=True,
    )
    
    state.final_document = document
    state.all_departments_approved = True
    
    duration_ms = (time.time() - start) * 1000
    add_trace(
        state,
        node_name="synthesize_document",
        agent_id="Part3Synthesizer",
        input_summary=f"{len(part2_departments)} departments",
        output_summary=f"Document generated: {document.document_path}",
        duration_ms=duration_ms,
    )
    
    return {"final_document": state.final_document, "execution_trace": state.execution_trace}
