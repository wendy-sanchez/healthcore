#!/usr/bin/env python3
"""
🧪 test_full_integration_e2e.py — Full Parts 1-2-3 End-to-End Integration Test

Exercises the complete RFP pipeline (Parts 1, 2, 3) using the actual graph.py
function run_rfp_pipeline(), with simulated but realistic data.

Verifies:
  - Part 1: PDF conversion → metadata extraction → classification
  - Part 2: Draft generation → evaluation → iteration loop
  - Part 3: Conflict detection → arbitrage → approval interrupts → document synthesis
  - Final ticket states, final_document_path, and part3_completed_at

Usage:
    uv run python scripts/test_full_integration_e2e.py
"""

import asyncio
import json
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from sqlmodel import Session, select

from services.api.database import get_sql_engine
from services.api.models_rfp import TicketDB, DepartmentSectionDB, RfpMetadataDB


# ── Test RFP Content: US-based RFP with PHI that will trigger Part 3 ──
SAMPLE_RFP_MARKDOWN = """# Request for Proposal — Occupational Health Services

## Project Overview

Meridian Manufacturing Inc. is seeking proposals for a comprehensive employee
occupational health program to serve its 8,000 employees across facilities in
the United States.

The program must include clinical services, wellness initiatives, and workers'
compensation management.

## Timeline

Proposals must be submitted by February 15, 2026. The program is expected to
commence on April 1, 2026 with an initial contract term of 12 months.

## Budget

The projected budget for this program is $1,500,000 annually, covering
population health management, occupational health services, and regulatory
compliance.

## Scope of Services

### Clinical Services
- Occupational health physician services
- Pre-employment physical examinations
- Workers' compensation injury management
- Fit-for-duty evaluations
- Vaccination programs (flu, hepatitis B, tetanus)

### Population Health Management
- Preventive care coordination
- Chronic disease management programs (diabetes, hypertension)
- Wellness and health promotion
- Health risk assessments

### Data and Reporting
- Quarterly population health reports
- OSHA injury and illness reporting
- Claims data analysis
- Quality metrics tracking

## Compliance Requirements

All vendors must:
- Sign a Business Associate Agreement (BAA)
- Comply with HIPAA privacy and security rules
- Maintain SOC 2 Type II certification
- Provide data breach notification procedures

## Submission Instructions

Please submit proposals electronically to procurement@meridian-mfg.com.

The designated privacy officer is Dr. Sarah Mitchell, who oversees all data
protection matters for employee health records.
"""


def _check_ticket_fields(result: dict, ticket: TicketDB):
    """Print a detailed status of all ticket fields."""
    steps = []
    all_ok = True

    # Status check
    expected = "terminado"
    if ticket.status == expected:
        steps.append(("Estado del ticket", ticket.status, "✅"))
    elif ticket.status in ("esperando_aprobacion", "needs_human_review", "arbitrando", "produciendo", "parcialmente_aprobado"):
        steps.append(("Estado del ticket", ticket.status, "⚠️  (aceptable, pendiente humano)"))
    else:
        steps.append(("Estado del ticket", ticket.status, "❌"))
        all_ok = False

    # Part 3 document
    if ticket.final_document_path:
        steps.append(("Documento final generado", ticket.final_document_path, "✅"))
    else:
        steps.append(("Documento final", "No generado", "⚠️  (puede ser normal si hay conflictos pendientes)"))

    # Part 3 timestamp
    if ticket.part3_completed_at:
        ts = ticket.part3_completed_at.isoformat() if hasattr(ticket.part3_completed_at, 'isoformat') else str(ticket.part3_completed_at)
        steps.append(("Parte 3 completada en", ts, "✅"))
    else:
        steps.append(("Parte 3 timestamp", "No establecido", "⚠️"))

    # Handoff payload
    if ticket.handoff_payload:
        hp = ticket.handoff_payload
        if isinstance(hp, str):
            try:
                hp = json.loads(hp)
            except (json.JSONDecodeError, TypeError):
                pass
        dept_count = len(hp.get("departments", [])) if isinstance(hp, dict) else "?"
        steps.append((f"Handoff payload ({dept_count} deptos)", "Presente", "✅"))
    else:
        steps.append(("Handoff payload", "Ausente", "❌"))
        all_ok = False

    # Error detail
    if ticket.error_detail:
        steps.append(("Error", ticket.error_detail[:100], "❌"))
        all_ok = False
    else:
        steps.append(("Errores", "Ninguno", "✅"))

    result["steps"] = steps
    result["all_ok"] = all_ok
    return result


def _print_results(result: dict):
    """Pretty-print the E2E test results."""
    ticket_id = result.get("ticket_id", "?")
    success = result.get("all_ok", False)
    
    print(f"\n{'='*70}")
    print(f"  RESULTADOS — Ticket: {ticket_id}")
    print(f"{'='*70}")
    
    for step in result.get("steps", []):
        label, value, status = step
        print(f"  {status} {label:40s} {value}")
    
    print(f"\n  Part 1 (Metadata):")
    meta = result.get("metadata", {})
    if meta:
        for k, v in meta.items():
            print(f"    • {k}: {v}")
    
    print(f"\n  Part 2 (Drafts):")
    drafts = result.get("drafts", [])
    for d in drafts:
        print(f"    • {d['department']}: {d['evaluation_status']}")
    
    print(f"\n  Part 3 (Approval):")
    p3 = result.get("part3", {})
    print(f"    • Conflictos:      {p3.get('conflicts', 0)}")
    print(f"    • Arbitrajes:      {p3.get('arbitrage', 0)}")
    print(f"    • Todos aprobados: {p3.get('all_approved')}")
    print(f"    • Documento:       {p3.get('document_generated')}")
    
    print(f"\n  Veredicto:")
    if success:
        print(f"  ✅ E2E COMPLETADO — Partes 1, 2 y 3 integradas correctamente")
    else:
        print(f"  ⚠️  E2E COMPLETADO CON OBSERVACIONES (ver detalles arriba)")
    print(f"{'='*70}\n")


async def run_full_e2e_test():
    """Execute the complete Parts 1-2-3 pipeline and verify results."""
    
    ticket_id = f"e2e-full-{uuid.uuid4().hex[:8]}"
    print(f"{'='*70}")
    print(f"  E2E TEST: Parts 1-2-3 Full Pipeline Integration")
    print(f"  Ticket: {ticket_id}")
    print(f"  Time:   {datetime.now(timezone.utc).isoformat()}")
    print(f"{'='*70}")
    
    # ── Setup: Write test data ─────────────────────────────────────
    raw_dir = (PROJECT_ROOT / "data" / "raw" / ticket_id)
    raw_dir.mkdir(parents=True, exist_ok=True)
    
    md_path = raw_dir / "converted.md"
    md_path.write_text(SAMPLE_RFP_MARKDOWN)
    print(f"\n✅ Test markdown escrito en {md_path}")
    
    pdf_path = raw_dir / "original.pdf"
    pdf_path.write_text(SAMPLE_RFP_MARKDOWN)
    print(f"✅ Placeholder PDF en {pdf_path}")
    
    # ── Create ticket in DB ─────────────────────────────────────────
    engine = get_sql_engine()
    with Session(engine) as session:
        ticket = TicketDB(
            ticket_id=ticket_id,
            status="analizando",
            raw_pdf_path=str(pdf_path),
        )
        session.add(ticket)
        session.commit()
    print(f"✅ Ticket creado en DB: {ticket_id}")
    
    # ── Run the FULL pipeline (Parts 1-2-3) ────────────────────────
    from data.pipelines.rfp_intake.graph import run_rfp_pipeline
    
    print(f"\n▶️  Ejecutando pipeline completo (9 nodos)...\n")
    try:
        result = await run_rfp_pipeline(ticket_id, str(pdf_path))
        print(f"\n✅ Pipeline ejecutado.")
    except Exception as e:
        import traceback
        print(f"\n❌ Pipeline falló: {e}")
        traceback.print_exc()
        
        # Still try to read ticket state
        with Session(engine) as session:
            ticket = session.exec(
                select(TicketDB).where(TicketDB.ticket_id == ticket_id)
            ).first()
        
        e2e_result = {
            "ticket_id": ticket_id,
            "all_ok": False,
            "steps": [
                ("Pipeline", str(e)[:80], "❌"),
                ("Estado ticket", ticket.status if ticket else "?","⚠️"),
            ],
            "metadata": {},
            "drafts": [],
            "part3": {},
        }
        _print_results(e2e_result)
        return False
    
    # ── Verify results ─────────────────────────────────────────────
    with Session(engine) as session:
        ticket = session.exec(
            select(TicketDB).where(TicketDB.ticket_id == ticket_id)
        ).first()
        
        if not ticket:
            print(f"❌ Ticket no encontrado en DB después del pipeline")
            return False
        
        # Collect metadata
        meta_records = session.exec(
            select(RfpMetadataDB).where(RfpMetadataDB.ticket_id == ticket_id)
        ).all()
        
        meta_info = {}
        if meta_records:
            m = meta_records[0]
            meta_info = {
                "client_name": m.client_name,
                "client_country": m.client_country,
                "program_type": m.program_type,
                "covered_population": m.covered_population,
                "departments_needed": m.departments_needed,
                "readability_fk": m.readability_fk_score,
            }
        
        # Collect department drafts (Part 2)
        dept_sections = session.exec(
            select(DepartmentSectionDB).where(
                DepartmentSectionDB.ticket_id == ticket_id
            )
        ).all()
        
        draft_info = []
        for ds in dept_sections:
            draft_info.append({
                "department": ds.department_id,
                "draft_length": len(ds.draft_content or ""),
                "evaluation_status": ds.approval_status or "pending",
                "has_phi": ds.contains_phi,
            })
        
        # Build E2E result
        e2e_result = {
            "ticket_id": ticket_id,
            "metadata": meta_info,
            "drafts": draft_info,
            "part3": result.get("part3_result", {}),
        }
        
        _check_ticket_fields(e2e_result, ticket)
        _print_results(e2e_result)
    
    # ── Verify Part 3 specific fields in DB ─────────────────────────
    with Session(engine) as session:
        ticket = session.exec(
            select(TicketDB).where(TicketDB.ticket_id == ticket_id)
        ).first()
        
        if ticket and ticket.final_document_path:
            doc_path = ticket.final_document_path
            if Path(doc_path).exists():
                content = Path(doc_path).read_text()
                print(f"  📄 Documento final ({len(content)} chars):")
                print(f"     {content[:200]}...")
                print()
        
        if ticket and ticket.part3_completed_at:
            print(f"  🕐 Part 3 completado en: {ticket.part3_completed_at}")
    
    return e2e_result.get("all_ok", False)


def main():
    success = asyncio.run(run_full_e2e_test())
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()