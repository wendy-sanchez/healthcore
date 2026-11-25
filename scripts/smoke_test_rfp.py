#!/usr/bin/env python3
"""
🧪 smoke_test_rfp.py — Smoke test rápido del pipeline RFP

Genera un documento de prueba, lo inserta en la base de datos,
ejecuta el pipeline completo y verifica el resultado.

Útil para validar que todo el pipeline funciona sin subir un PDF real.

Uso:
    python scripts/smoke_test_rfp.py
"""

import asyncio
import sys
import tempfile
import uuid
from pathlib import Path

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from sqlmodel import Session, select

from services.api.database import get_sql_engine
from services.api.models_rfp import TicketDB

# ── Test RFP content ────────────────────────────────────────────────

SAMPLE_RFP_MARKDOWN = """# Request for Proposal — Health Management Services

## Project Overview

Meridian Manufacturing Inc. is seeking proposals for a comprehensive employee
health management program to serve its 12,000 employees across facilities in
the United States.

## Timeline

Proposals must be submitted by January 15, 2026. The program is expected to
commence on March 1, 2026 with an initial contract term of 3 years.

## Budget

The projected budget for this program is $2,500,000 annually, covering
population health management, wellness initiatives, and chronic disease
management.

## Scope of Services

### Population Health Management
- Preventive care coordination
- Chronic disease management programs
- Wellness and health promotion
- Health risk assessments

### Clinical Services
- Telehealth primary care
- Behavioral health support
- Care coordination for high-risk members

### Data and Reporting
- Quarterly population health reports
- Claims data analysis
- Quality metrics tracking (HEDIS, STARs)

## Compliance Requirements

All vendors must:
- Sign a Business Associate Agreement (BAA)
- Comply with HIPAA privacy and security rules
- Maintain SOC 2 Type II certification
- Provide data breach notification procedures

## Submission Instructions

Please submit proposals electronically to procurement@meridian-mfg.com.
"""


async def run_smoke_test():
    """Creates a ticket, writes test PDF (as .md for now), runs pipeline."""
    ticket_id = str(uuid.uuid4())
    print(f"🧪 Smoke Test - Pipeline RFP")
    print(f"   Ticket ID: {ticket_id}")
    print()

    # Write test content to a file
    raw_dir = Path(PROJECT_ROOT) / "data" / "raw" / ticket_id
    raw_dir.mkdir(parents=True, exist_ok=True)

    # We write the markdown directly so we skip the PDF conversion step
    md_path = raw_dir / "converted.md"
    md_path.write_text(SAMPLE_RFP_MARKDOWN)
    print(f"✅ Test markdown escrito en {md_path}")

    # Also create a placeholder "original.pdf" so the ticket references exist
    pdf_path = raw_dir / "original.pdf"
    pdf_path.write_text(SAMPLE_RFP_MARKDOWN)  # not a real PDF, but enough for the test
    print(f"✅ Placeholder PDF en {pdf_path}")

    # Create TicketDB row manually
    engine = get_sql_engine()
    with Session(engine) as session:
        existing = session.exec(
            select(TicketDB).where(TicketDB.ticket_id == ticket_id)
        ).first()
        if existing:
            print(f"⚠️  Ticket ya existe: {ticket_id}")
            ticket = existing
        else:
            ticket = TicketDB(
                ticket_id=ticket_id,
                status="analizando",
                raw_pdf_path=str(pdf_path),
            )
            session.add(ticket)
            session.commit()
            print(f"✅ Ticket creado en DB")

    # Run pipeline
    from data.pipelines.rfp_intake.graph import run_rfp_pipeline

    print(f"\n▶️  Ejecutando pipeline...\n")
    result = await run_rfp_pipeline(ticket_id, str(pdf_path))
    print(f"\n✅ Pipeline completado.")

    # Verify results
    with Session(engine) as session:
        ticket = session.exec(
            select(TicketDB).where(TicketDB.ticket_id == ticket_id)
        ).first()

        print(f"\n📊 Resultados:")
        print(f"   Estado:        {ticket.status}")
        print(f"   Cliente:       {ticket.rfp_id or '—'}")
        print(f"   Error:         {ticket.error_detail or 'Ninguno'}")
        print(f"   Síntesis:      {'✓ Generada' if ticket.synthesis else '✗ Vacía'}")

        if ticket.handoff_payload:
            print(f"   Handoff payload: ✓")
        else:
            print(f"   Handoff payload: ✗ Vacío")

        # Check metadata
        meta = session.exec(
            select(__import__("services.api.models_rfp", fromlist=["RfpMetadataDB"]).RfpMetadataDB)
            .where(__import__("services.api.models_rfp", fromlist=["RfpMetadataDB"]).RfpMetadataDB.ticket_id == ticket_id)
        ).first()

        if meta:
            print(f"   Metadatos:     ✓")
            print(f"   Cliente:       {meta.client_name}")
            print(f"   País:          {meta.client_country}")
            print(f"   Programa:      {meta.program_type}")
            print(f"   Población:     {meta.covered_population}")
            print(f"   Deptos:        {meta.departments_needed}")
            print(f"   Legibilidad:   Flesch-Kincaid={meta.readability_fk_score}, FRE={meta.readability_fre_score}, GF={meta.readability_gf_score}")
        else:
            print(f"   Metadatos:     ✗ No encontrados")

    print(f"\n{'='*50}")
    print(f"🧪 Smoke test {'✓ EXITOSO' if ticket and ticket.status in ('analisis_completo',) else '⚠️  REVISAR'}")
    print(f"{'='*50}")


def main():
    asyncio.run(run_smoke_test())


if __name__ == "__main__":
    main()