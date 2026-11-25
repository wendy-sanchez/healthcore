# Part 3: Human-in-the-Loop Approval, Arbitrage, and Document Synthesis

**Hito 9 — HealthCore RFP Intake Pipeline — Parte 3**

> This module completes the agentic RFP workflow with human-centered approval, structured conflict detection, and automatic document synthesis.

## Quick Start

### Installation

```bash
cd /workspaces/healthcore
pip install -e .  # Installs Pydantic, LangGraph, etc.
```

### Run E2E Test

```bash
python -m agentic_workflows_part3.e2e_script
```

Expected output:
```
=== HealthCore RFP Intake Workflow — End-to-End Test (Parts 1-3) ===
[PART 1] RFP Classification and Metadata Extraction
✓ Ticket created: rfp-e2e-test-001
...
[PART 3] Approvals, Arbitrage, and Document Synthesis
✓ Final proposal generated
=== WORKFLOW SUMMARY ===
Status: COMPLETED
```

### Run Tests

```bash
pytest agentic_workflows_part3/test_part3.py -v
# 15+ tests covering conflict detection, arbitrage, checkpointing, and E2E flow
```

## Module Structure

### Core Files

| File | Purpose | Key Classes |
|------|---------|-------------|
| `models_part3.py` | Pydantic data models | `Part3PipelineState`, `ConflictState`, `ArbitrageOutcome`, `FinalDocument` |
| `checkpointer.py` | SQLite-based state persistence | `Part3SQLiteCheckpointer` |
| `graph_nodes.py` | LangGraph node implementations | `detect_conflicts_node`, `arbitrage_node`, etc. |
| `graph.py` | LangGraph workflow orchestration | `Part3Pipeline`, `create_part3_pipeline()` |
| `test_part3.py` | Comprehensive test suite | 15+ tests |
| `e2e_script.py` | End-to-end workflow simulation | Full Parts 1-3 flow |

### Key Concepts

#### Conflict Detection (Structured State)

Conflicts are detected via deterministic rules, not LLM voting:

- **`phi-detected`**: Compliance evaluator flags PHI in sections
- **`baa-dpa-mismatch`**: US client without BAA or UK without DPA
- **`capacity-vs-population`**: Clinical capacity cannot support population

#### Arbitrage (Fixed Arbiters)

Each conflict type has a fixed arbiter (not LLM-based consensus):

```python
ConflictTrigger.PHI_DETECTED → Claire Whitfield (compliance)
ConflictTrigger.BAA_DPA_MISMATCH → Claire Whitfield (compliance)
ConflictTrigger.CAPACITY_VS_POPULATION → Tom Callahan (revenue)
```

Decision: `APPROVE`, `REQUEST_CHANGES`, or `REJECT` (parada dura).

#### Parallel, Non-Blocking Approvals

Each department's approval is independent:
- Revenue can be approved while Clinical is still pending
- Checkpointing per `ticket_id:department_id` enables resumable state
- LangGraph routing (`check_approvals_complete`) decides when to synthesize

#### Document Synthesis

Once all approvals collected, sections are consolidated into a final proposal:

```python
final_document = FinalDocument(
    ticket_id=...,
    client_name=...,
    sections=[...approved_sections...],
    document_path="/tmp/proposals/rfp-{ticket_id}_final_proposal.md"
)
```

## Usage Examples

### Initialize Pipeline

```python
from agentic_workflows_part3 import create_part3_pipeline

pipeline = create_part3_pipeline(checkpoint_db_path="/tmp/healthcore_checkpoints.db")
```

### Run Part 3 Workflow

```python
state = pipeline.run_part3_pipeline(
    ticket_id="rfp-123",
    part2_handoff={
        "departments": [
            {"department_id": "revenue", "finalized_draft": "...", "evaluation": {...}},
            {"department_id": "clinical", "finalized_draft": "...", "evaluation": {...}},
            {"department_id": "compliance", "finalized_draft": "...", "evaluation": {...}},
        ]
    },
    rfp_metadata={
        "client_name": "Meridian Manufacturing",
        "client_country": "US",
        "covered_population": 800,
    }
)

# state.detected_conflicts: List of ConflictState
# state.arbitrage_outcomes: List of ArbitrageOutcome
# state.interrupted_departments: Dict of departments awaiting approval
```

### Handle Approval Response

```python
approved_state = pipeline.handle_approval_response(
    ticket_id="rfp-123",
    department_id="revenue",
    approval_response={
        "approval_status": "approved",
        "approver_name": "Tom Callahan",
        "approver_email": "tom@healthcore.com",
    }
)

# Workflow resumes; checks if all approvals collected
# If all approved → synthesize_document_node() runs → final_document generated
```

### Retrieve Approval History

```python
from agentic_workflows_part3 import Part3SQLiteCheckpointer, CheckpointConfig

checkpointer = Part3SQLiteCheckpointer(CheckpointConfig(db_path="..."))
approvals = checkpointer.get_approval_history("rfp-123")
# [{"department_id": "revenue", "approval_status": "approved", ...}]

arbitrage_history = checkpointer.get_arbitrage_history("rfp-123")
# [{"conflict_id": "baa-dpa-mismatch", "decision": "request_changes", ...}]
```

## Integration with Backend

### Suggested FastAPI Endpoint

```python
from fastapi import APIRouter, HTTPException
from agentic_workflows_part3 import create_part3_pipeline, DepartmentApprovalResponse

router = APIRouter()
pipeline = create_part3_pipeline()

@router.post("/rfp/tickets/{ticket_id}/approve")
async def approve_department_section(
    ticket_id: str,
    approval: DepartmentApprovalResponse,
):
    try:
        final_state = pipeline.handle_approval_response(
            ticket_id=ticket_id,
            department_id=approval.department_id,
            approval_response=approval.model_dump(),
        )
        
        # Update ticket status in DB
        status = "terminado" if final_state.all_departments_approved else "esperando_aprobación"
        db.update_ticket(ticket_id, status=status)
        
        return {"status": "success", "ticket_status": status}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
```

## Execution Trace (Telemetry)

Every node execution is logged:

```python
state.execution_trace
# [
#   {"node_name": "detect_conflicts", "agent_id": null, "status": "success", "duration_ms": 45.2},
#   {"node_name": "arbitrage", "agent_id": null, "status": "success", "duration_ms": 12.5},
#   {"node_name": "request_approvals", "agent_id": null, "status": "success", "duration_ms": 8.3},
#   ...
# ]
```

For audit and debugging.

## Known Limitations

1. **No email notifications**: Approval requests are logged but not sent to email/Slack (integration needed)
2. **UI not yet built**: Approval buttons exist in code but need React components in `uis/backoffice`
3. **Document generation**: Final document state saved but not rendered to PDF/Markdown on disk
4. **Manual arbitrage input**: Complex conflicts may require human arbiter decision (not implemented)
5. **Regeneration on rejection**: If a dept rejects, re-queuing Part 2 is manual (not automated)

## Testing

### Run All Tests

```bash
pytest agentic_workflows_part3/test_part3.py -v
# 15+ tests
# - Conflict detection (3 tests)
# - Arbitrage decisions (2 tests)
# - Checkpointing (3 tests)
# - Pipeline integration (4 tests)
# - Execution trace (1 test)
# - E2E (1 test)
```

### Key Test Scenarios

- **Parallel approvals**: Department B approved while A is pending
- **Conflict triggers**: BAA missing, PHI detected, capacity mismatch
- **Checkpoint recovery**: Resume from interrupted state
- **Document synthesis**: Final proposal generated after all approvals

## Architecture Diagram

```
Part2 Handoff (sections + evals)
    ↓
detect_conflicts_node
    ↓ (Part3PipelineState)
arbitrage_node
    ↓
request_approvals_node
    ↓ (INTERRUPT + checkpoint)
[Human Approvals via API]
    ↓ (handle_approval_response × N)
check_approvals_complete (routing)
    ├─ False → END (waiting for more approvals)
    └─ True → synthesize_document_node
                    ↓
                FinalDocument
                    ↓
                END (terminado)
```

## Contributing

- Add new conflict triggers in `detect_conflicts_node()`
- Add new arbitrage rules in `arbitrage_node()`
- Extend `Part3PipelineState` for additional tracking
- Add tests for any new feature

## References

- **Main Workflow**: `/workspaces/healthcore/agentic-workflows.md` (Parte 3 section)
- **Context & Requirements**: `/workspaces/healthcore/CONTEXT.md` (Hito 9 section)
- **Part 1 (Classification)**: `agentic_workflows.md` (Parte 1 section)
- **Part 2 (Generation & Evaluation)**: `agentic_workflows.md` (Parte 2 section)
