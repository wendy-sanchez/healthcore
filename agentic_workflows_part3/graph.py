"""
LangGraph Pipeline for Part 3: Human-in-the-Loop Approval & Document Synthesis
Hito 9 — HealthCore RFP Intake Pipeline — Parte 3
"""

try:
    from langgraph.graph import StateGraph, END, START
    from langgraph.checkpoint.sqlite import SqliteSaver
    HAS_LANGGRAPH = True
except ImportError:
    HAS_LANGGRAPH = False
    StateGraph = None
    SqliteSaver = None
    END = "END"
    START = "START"

from typing import Optional
from .models_part3 import Part3PipelineState, TicketStatusPart3
from .graph_nodes import (
    detect_conflicts_node,
    arbitrage_node,
    request_approvals_node,
    check_approvals_complete,
    synthesize_document_node,
)
from .checkpointer import Part3SQLiteCheckpointer, CheckpointConfig


class Part3Pipeline:
    """
    LangGraph-based Part 3 workflow with:
    - Conflict detection (structured state triggers)
    - Arbitrage node (fixed arbiters, not LLM)
    - Per-department approval interrupts (parallel, non-blocking)
    - SQLite checkpointing with ticket_id:department_id namespacing
    - Document synthesis after all approvals
    """
    
    def __init__(self, checkpoint_db_path: Optional[str] = None):
        self.checkpoint_db_path = checkpoint_db_path or "/tmp/healthcore_part3_checkpoints.db"
        self.checkpointer_config = CheckpointConfig(db_path=self.checkpoint_db_path)
        self.checkpointer = Part3SQLiteCheckpointer(self.checkpointer_config)
        
        if HAS_LANGGRAPH:
            self.graph = self._build_graph()
            self.compiled_graph = None
        else:
            self.graph = None
            self.compiled_graph = None
    
    def _build_graph(self):
        """Build the LangGraph workflow"""
        if not HAS_LANGGRAPH:
            raise ImportError("LangGraph not installed. Run: pip install langgraph")
        
        graph = StateGraph(Part3PipelineState)
        
        # ---- NODES WITH CHECKPOINTER INJECTION ----
        # Create wrapper functions that capture self.checkpointer
        def _request_approvals_wrapper(state: Part3PipelineState) -> dict:
            """Wrapper that calls request_approvals_node with injected checkpointer"""
            return request_approvals_node(state, checkpointer=self.checkpointer)
        
        graph.add_node("detect_conflicts", detect_conflicts_node)
        graph.add_node("arbitrage", arbitrage_node)
        graph.add_node("request_approvals", _request_approvals_wrapper)
        graph.add_node("synthesize_document", synthesize_document_node)
        
        # ---- EDGES ----
        graph.add_edge(START, "detect_conflicts")
        graph.add_edge("detect_conflicts", "arbitrage")
        graph.add_edge("arbitrage", "request_approvals")
        
        graph.add_conditional_edges(
            "request_approvals",
            check_approvals_complete,
            {
                True: "synthesize_document",
                False: END,
            }
        )
        
        graph.add_edge("synthesize_document", END)
        
        # Compile with checkpointer if available
        try:
            sqlite_saver = SqliteSaver.from_conn_string(f"sqlite:///{self.checkpoint_db_path}")
            self.compiled_graph = graph.compile(checkpointer=sqlite_saver)
        except Exception:
            # Fallback: compile without checkpointer
            self.compiled_graph = graph.compile()
        
        return graph
    
    def run_part3_pipeline(
        self,
        ticket_id: str,
        part2_handoff: dict,
        rfp_metadata: dict,
        resume: bool = False,
        resume_department: Optional[str] = None,
    ) -> Part3PipelineState:
        """
        Execute Part 3 workflow.
        
        Args:
            ticket_id: RFP ticket ID
            part2_handoff: Output from Part 2 (sections, evaluations, drafts)
            rfp_metadata: RFP metadata (client_name, country, etc.)
            resume: If True, resume from checkpoint for this ticket
            resume_department: If provided, resume from specific department checkpoint
        
        Returns:
            Final Part3PipelineState with all approvals and final document
        """
        
        thread_id = f"rfp-{ticket_id}"
        if resume_department:
            thread_id = f"{thread_id}:{resume_department}"
        
        # Load checkpoint if resuming
        if resume:
            checkpoint_data = self.checkpointer.get(ticket_id, resume_department)
            if checkpoint_data:
                state = Part3PipelineState.model_validate(checkpoint_data)
            else:
                raise ValueError(f"No checkpoint found for {thread_id}")
        else:
            # Initialize new state
            state = Part3PipelineState(
                ticket_id=ticket_id,
                rfp_metadata=rfp_metadata,
                part2_handoff=part2_handoff,
            )
        
        # Save checkpoint before running
        self.checkpointer.put(ticket_id, state.model_dump(), resume_department)
        
        # Run the graph if LangGraph available, otherwise manual execution
        if self.compiled_graph:
            config = {"configurable": {"thread_id": thread_id}}
            result = self.compiled_graph.invoke(state.model_dump(), config)
            final_state = Part3PipelineState.model_validate(result)
        else:
            # Manual execution without LangGraph (for testing)
            final_state = state
            
            # Execute nodes manually
            result = detect_conflicts_node(final_state)
            final_state.detected_conflicts = result["detected_conflicts"]
            final_state.execution_trace = result["execution_trace"]
            
            result = arbitrage_node(final_state)
            final_state.arbitrage_outcomes = result["arbitrage_outcomes"]
            final_state.execution_trace = result["execution_trace"]
            
            # Pass checkpointer to request_approvals_node
            result = request_approvals_node(final_state, checkpointer=self.checkpointer)
            final_state.interrupted_departments = result["interrupted_departments"]
            final_state.execution_trace = result["execution_trace"]
            
            # Check if all approvals are complete and synthesize if so
            if check_approvals_complete(final_state):
                result = synthesize_document_node(final_state)
                final_state.final_document = result.get("final_document")
                final_state.execution_trace = result.get("execution_trace", final_state.execution_trace)
        
        # Save final checkpoint
        self.checkpointer.put(ticket_id, final_state.model_dump(), resume_department)
        
        return final_state
    
    def handle_approval_response(
        self,
        ticket_id: str,
        department_id: str,
        approval_response: dict,
    ) -> Part3PipelineState:
        """
        Process human approval response for a department.
        Resume the workflow with the approval recorded.
        
        Args:
            ticket_id: RFP ticket ID
            department_id: Department being approved (revenue, clinical, compliance)
            approval_response: {
                "approval_status": "approved" | "rejected" | "requested_changes",
                "approver_name": "...",
                "approver_email": "...",
                "rejection_reason": "..." (if rejected),
                "requested_changes": [...] (if requested_changes),
            }
        
        Returns:
            Updated Part3PipelineState
        """
        
        # Load checkpoint for this department
        checkpoint_data = self.checkpointer.get(ticket_id, department_id)
        if not checkpoint_data:
            raise ValueError(f"No checkpoint for {ticket_id}:{department_id}")
        
        state = Part3PipelineState.model_validate(checkpoint_data)
        
        # Record approval
        from .models_part3 import DepartmentApprovalResponse, ApprovalStatus
        from datetime import datetime
        
        response = DepartmentApprovalResponse(
            ticket_id=ticket_id,
            department_id=department_id,
            approval_status=ApprovalStatus(approval_response["approval_status"]),
            approver_name=approval_response["approver_name"],
            approver_email=approval_response["approver_email"],
            approved_at_utc=datetime.utcnow(),
            rejection_reason=approval_response.get("rejection_reason"),
            requested_changes=approval_response.get("requested_changes"),
        )
        
        state.department_approvals[department_id] = response
        state.interrupted_departments.pop(department_id, None)
        
        # Save updated state to checkpoint
        self.checkpointer.put(ticket_id, state.model_dump(), department_id)
        
        # Save to checkpointer history
        self.checkpointer.record_approval(
            ticket_id=ticket_id,
            department_id=department_id,
            approval_status=response.approval_status.value,
            approver_name=response.approver_name,
            approved_at_iso=response.approved_at_utc.isoformat(),
        )
        
        # Resume workflow
        return self.run_part3_pipeline(
            ticket_id=ticket_id,
            part2_handoff=state.part2_handoff,
            rfp_metadata=state.rfp_metadata,
            resume=True,
            resume_department=department_id,
        )


def create_part3_pipeline(checkpoint_db_path: Optional[str] = None) -> Part3Pipeline:
    """Factory function to create Part 3 pipeline"""
    return Part3Pipeline(checkpoint_db_path)
