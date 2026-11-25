"""
Part 3: Human-in-the-Loop Approval, Arbitrage, and Document Synthesis
Hito 9 — HealthCore RFP Intake Pipeline — Parte 3

Main exports for integration with LangGraph and backends.
"""

from .models_part3 import (
    Part3PipelineState,
    Part3Handoff,
    ApprovalStatus,
    TicketStatusPart3,
    ConflictTrigger,
    ArbitrageDecision,
    DepartmentApprovalRequest,
    DepartmentApprovalResponse,
    ConflictState,
    ArbitrageOutcome,
    FinalDocument,
    ExecutionTraceEntry,
    add_trace,
)

from .graph import create_part3_pipeline, Part3Pipeline

from .checkpointer import Part3SQLiteCheckpointer, CheckpointConfig

from .graph_nodes import (
    detect_conflicts_node,
    arbitrage_node,
    request_approvals_node,
    check_approvals_complete,
    synthesize_document_node,
)

__all__ = [
    # Models
    "Part3PipelineState",
    "Part3Handoff",
    "ApprovalStatus",
    "TicketStatusPart3",
    "ConflictTrigger",
    "ArbitrageDecision",
    "DepartmentApprovalRequest",
    "DepartmentApprovalResponse",
    "ConflictState",
    "ArbitrageOutcome",
    "FinalDocument",
    "ExecutionTraceEntry",
    "add_trace",
    # Graph & Pipeline
    "Part3Pipeline",
    "create_part3_pipeline",
    # Checkpointer
    "Part3SQLiteCheckpointer",
    "CheckpointConfig",
    # Nodes
    "detect_conflicts_node",
    "arbitrage_node",
    "request_approvals_node",
    "check_approvals_complete",
    "synthesize_document_node",
]

__version__ = "1.0.0"
__description__ = "Part 3: RFP Workflow with Human-in-the-Loop Approvals"
