"""
Arbitration node — Explicit conflict resolution per CONTEXT §7.
Routes to fixed arbiter (not LLM consensus).
"""

from typing import Dict, Any, List, Optional
from datetime import datetime
from enum import Enum
from .models_part3 import ConflictTrigger, ArbitrageDecision, ArbitrageOutcome


class ArbitrationRule(str, Enum):
    """Deterministic rules per conflict trigger"""
    CONTEXT_PHI_REJECTION = "phi-detected-auto-reject"
    CONTEXT_BAA_MISSING_US = "baa-missing-for-us-request-changes"
    CONTEXT_DPA_MISSING_UK = "dpa-missing-for-uk-request-changes"
    CONTEXT_CAPACITY_TO_WAREHOUSE = "cap-volume-to-warehouse-capacity"


ARBITRATION_MAPPING = {
    # (trigger, details_key) → (rule, arbiter_name, decision)
    ConflictTrigger.PHI_DETECTED: {
        "rule": ArbitrationRule.CONTEXT_PHI_REJECTION,
        "arbiter": "Claire Whitfield",  # Compliance from CONTEXT.md §2
        "decision": ArbitrageDecision.REJECT,
        "explanation": "PHI detected in draft — cannot proceed. Manual review required by Compliance.",
    },
    ConflictTrigger.BAA_DPA_MISMATCH: {
        "rule": ArbitrationRule.CONTEXT_BAA_MISSING_US,
        "arbiter": "Claire Whitfield",  # Compliance from CONTEXT.md §2
        "decision": ArbitrageDecision.REQUEST_CHANGES,
        "explanation": "Missing BAA/DPA for client country. Request changes before approval.",
    },
    ConflictTrigger.CAPACITY_VS_POPULATION: {
        "rule": ArbitrationRule.CONTEXT_CAPACITY_TO_WAREHOUSE,
        "arbiter": "Tom Callahan",  # Revenue from CONTEXT.md §2
        "decision": ArbitrageDecision.REQUEST_CHANGES,
        "explanation": "Capacity insufficient for covered population. Request changes to scope.",
    },
}


def arbitrate_conflict(
    ticket_id: str,
    conflict: Dict[str, Any],
    iteration_count: int = 1,
    max_iterations: int = 3,
) -> ArbitrageOutcome:
    """
    Deterministic arbitration per conflict trigger.
    
    Args:
        ticket_id: RFP ticket ID
        conflict: ConflictState dict (trigger_id, affected_departments, details)
        iteration_count: Current approval iteration
        max_iterations: Max allowed iterations (default 3)
    
    Returns:
        ArbitrageOutcome with rule, arbiter, decision, next_action
    """
    trigger = conflict.get("trigger_id")
    if trigger not in ARBITRATION_MAPPING:
        raise ValueError(f"Unknown trigger: {trigger}")
    
    mapping = ARBITRATION_MAPPING[trigger]
    
    # If at max iterations, escalate to manual review
    if iteration_count >= max_iterations:
        decision = ArbitrageDecision.REJECT
        next_action = "escalate_to_manual_review"
        explanation = f"Max iterations ({max_iterations}) reached. Escalating to manual review."
    else:
        decision = mapping["decision"]
        next_action = "request_changes" if decision == ArbitrageDecision.REQUEST_CHANGES else "reject"
        explanation = mapping["explanation"]
    
    return ArbitrageOutcome(
        conflict_id=trigger,
        departments=conflict.get("affected_departments", []),
        arbiter=mapping["arbiter"],
        rule=mapping["rule"],
        resolution=explanation,
        next_action=next_action,
        decided_at_utc=datetime.utcnow(),
        iteration=iteration_count,
    )
