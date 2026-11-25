"""
Part 3 Models — Approval, Interrupts, Arbitrage, and Document Synthesis
Hito 9 — HealthCore RFP Intake Pipeline — Parte 3
"""

from enum import Enum
from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class ApprovalStatus(str, Enum):
    """Status of a department section approval"""
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    REQUESTED_CHANGES = "requested_changes"


class TicketStatusPart3(str, Enum):
    """Ticket states for Part 3"""
    NEEDS_HUMAN_REVIEW = "needs_human_review"
    ESPERANDO_APROBACION = "esperando_aprobación"
    TERMINADO = "terminado"


class ConflictTrigger(str, Enum):
    """Enum of detectable conflict triggers"""
    PHI_DETECTED = "phi-detected"
    BAA_DPA_MISMATCH = "baa-dpa-mismatch"
    CAPACITY_VS_POPULATION = "capacity-vs-population"


class ArbitrageDecision(str, Enum):
    """Arbitrage node decision"""
    APPROVE = "approve"
    REQUEST_CHANGES = "request_changes"
    REJECT = "reject"


class DepartmentApprovalRequest(BaseModel):
    """Human-readable approval request"""
    ticket_id: str
    department_id: str
    section_summary: str
    draft_content: str
    evaluation_summary: Optional[str] = None
    approval_deadline_utc: Optional[datetime] = None


class DepartmentApprovalResponse(BaseModel):
    """Human response to approval request"""
    ticket_id: str
    department_id: str
    approval_status: ApprovalStatus
    approver_name: str
    approver_email: str
    approved_at_utc: datetime
    signature_key: Optional[str] = None
    rejection_reason: Optional[str] = None
    requested_changes: Optional[List[str]] = None


class InterruptedDepartmentState(BaseModel):
    """State of a department while interrupted"""
    ticket_id: str
    department_id: str
    draft_content: str
    is_interrupted: bool = True
    interrupt_reason: str = "awaiting_approval"
    checkpointer_thread_id: str


class ConflictState(BaseModel):
    """Structured conflict state"""
    trigger_id: ConflictTrigger
    ticket_id: str
    affected_departments: List[str] = Field(default_factory=list)
    detected_at_utc: datetime
    details: Dict[str, Any]
    arbiter_assigned: str
    arbiter_role: str


class ArbitrageRequest(BaseModel):
    """Request sent to arbitrage node"""
    conflict: ConflictState
    involved_sections: Dict[str, str]
    arbitrage_options: List[str] = Field(default_factory=list)


class ArbitrageOutcome(BaseModel):
    """Arbitrage decision + rationale"""
    conflict_id: ConflictTrigger
    decision: ArbitrageDecision
    rationale: str
    arbitrated_by: str
    arbitrated_at_utc: datetime
    next_step: str


class DepartmentSignOff(BaseModel):
    """Final sign-off for a department section"""
    ticket_id: str
    department_id: str
    section_id: str
    final_draft_content: str
    approval_status: ApprovalStatus
    approver: str
    approved_at_utc: datetime
    is_final: bool = True


class FinalDocumentSection(BaseModel):
    """Consolidated section in the final document"""
    department_id: str
    title: str
    content: str
    approval_status: ApprovalStatus
    approved_by: str
    approved_at_utc: datetime


class FinalDocument(BaseModel):
    """Generated final proposal document"""
    ticket_id: str
    client_name: str
    client_country: str
    program_type: str
    sections: List[FinalDocumentSection]
    generated_at_utc: datetime
    generated_by_system: str = "Part3Synthesizer"
    document_path: str
    is_complete: bool = True


class Part3PipelineState(BaseModel):
    """Full state carried through Part 3 graph"""
    ticket_id: str
    rfp_metadata: Dict[str, Any]
    part2_handoff: Dict[str, Any]
    
    department_approvals: Dict[str, Optional[DepartmentApprovalResponse]] = Field(
        default_factory=dict
    )
    
    detected_conflicts: List[ConflictState] = Field(default_factory=list)
    arbitrage_outcomes: List[ArbitrageOutcome] = Field(default_factory=list)
    
    interrupted_departments: Dict[str, InterruptedDepartmentState] = Field(
        default_factory=dict
    )
    all_departments_approved: bool = False
    
    final_document: Optional[FinalDocument] = None
    
    created_at_utc: datetime = Field(default_factory=datetime.utcnow)
    updated_at_utc: datetime = Field(default_factory=datetime.utcnow)
    execution_trace: List[Dict[str, Any]] = Field(default_factory=list)


class Part3Handoff(BaseModel):
    """Handoff from Part 3 to downstream"""
    ticket_id: str
    status: TicketStatusPart3
    final_document: Optional[FinalDocument] = None
    all_approvals: Dict[str, DepartmentApprovalResponse]
    arbitrage_history: List[ArbitrageOutcome]
    generated_at_utc: datetime
    is_success: bool


class ExecutionTraceEntry(BaseModel):
    """Telemetry entry for a single agent/node action"""
    timestamp_utc: datetime = Field(default_factory=datetime.utcnow)
    node_name: str
    agent_id: Optional[str] = None
    input_summary: str
    output_summary: str
    duration_ms: float
    status: str = "success"
    error_detail: Optional[str] = None


def add_trace(
    state: Part3PipelineState,
    node_name: str,
    agent_id: Optional[str],
    input_summary: str,
    output_summary: str,
    duration_ms: float,
    status: str = "success",
    error_detail: Optional[str] = None,
) -> None:
    """Add execution trace entry to state"""
    entry = ExecutionTraceEntry(
        node_name=node_name,
        agent_id=agent_id,
        input_summary=input_summary,
        output_summary=output_summary,
        duration_ms=duration_ms,
        status=status,
        error_detail=error_detail,
    )
    state.execution_trace.append(entry.model_dump())
