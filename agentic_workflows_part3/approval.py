"""
Interrupt and Resume payloads for Part 3 approval workflow.
Validates human decisions before re-entering the graph.
"""

from pydantic import BaseModel, Field, validator
from typing import Optional, List
from datetime import datetime
from enum import Enum


class ApprovalDecision(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    REQUEST_CHANGES = "request_changes"


class InterruptPayload(BaseModel):
    """Shown to human for decision-making"""
    ticket_id: str
    department: str
    section_summary: str  # 2-3 lines: what's being approved
    evaluation_snapshot: dict  # {passed: bool, iterations: int, details: str}
    approval_deadline_utc: datetime
    draft_excerpt: str = Field(description="First 500 chars of finalized_draft")
    actions: List[str] = ["approve", "reject", "request_changes"]


class ResumePayload(BaseModel):
    """Validated before re-entering graph after human decision"""
    ticket_id: str
    department: str
    decision: ApprovalDecision
    actor: str  # Email or ID of approver
    comment: str = ""
    requested_changes: Optional[str] = None  # Required if decision == REQUEST_CHANGES
    
    @validator("requested_changes")
    def changes_required_if_request(cls, v, values):
        if values.get("decision") == ApprovalDecision.REQUEST_CHANGES and not v:
            raise ValueError("requested_changes required when decision is REQUEST_CHANGES")
        return v
    
    def is_valid(self) -> tuple[bool, Optional[str]]:
        """Return (is_valid, error_message) for graph routing"""
        try:
            self.validate()
            return (True, None)
        except Exception as e:
            return (False, str(e))


def route_human_decision(payload: ResumePayload) -> str:
    """
    Route based on human decision:
    - APPROVE → check_approvals_complete
    - REJECT → back to Part 2 (request_changes with feedback)
    - REQUEST_CHANGES → back to Part 2 regeneration
    """
    if payload.decision == ApprovalDecision.APPROVE:
        return "check_approvals_complete"
    elif payload.decision in [ApprovalDecision.REJECT, ApprovalDecision.REQUEST_CHANGES]:
        return "request_part2_regeneration"
    else:
        raise ValueError(f"Unknown decision: {payload.decision}")
