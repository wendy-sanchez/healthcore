#!/usr/bin/env python3
"""
🔁 rerun_rfp_ticket.py — Reprocesa un ticket RFP por ID

Carga el ticket desde la base de datos, encuentra el PDF original,
y ejecuta el pipeline completo de forma local (sin Celery).

Útil para debugging y desarrollo.

Uso:
    python scripts/rerun_rfp_ticket.py <ticket_id> [--no-celery]

Ejemplo:
    python scripts/rerun_rfp_ticket.py abc123-def456
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from sqlmodel import Session, select

from services.api.database import get_sql_engine
from services.api.models_rfp import TicketDB


async def rerun_pipeline(ticket_id: str) -> None:
    """Load ticket, run pipeline inline, update DB."""
    engine = get_sql_engine()
    with Session(engine) as session:
        ticket = session.exec(
            select(TicketDB).where(TicketDB.ticket_id == ticket_id)
        ).first()

        if not ticket:
            print(f"❌ Ticket no encontrado: {ticket_id}")
            sys.exit(1)

        print(f"📋 Ticket:     {ticket.ticket_id}")
        print(f"   Estado:     {ticket.status}")
        print(f"   PDF:        {ticket.raw_pdf_path}")
        print(f"   Cliente:    {ticket.rfp_id or '—'}")

        pdf_path = ticket.raw_pdf_path
        if not pdf_path or not Path(pdf_path).exists():
            print(f"❌ PDF no encontrado: {pdf_path}")
            sys.exit(1)

    # Import here to ensure all models are registered
    from data.pipelines.rfp_intake.graph import run_rfp_pipeline

    print(f"\n▶️  Ejecutando pipeline para {ticket_id} ...\n")
    result = await run_rfp_pipeline(ticket_id, pdf_path)
    print(f"\n✅ Pipeline completado. Estado final: {result.get('status', 'desconocido')}")


def main():
    parser = argparse.ArgumentParser(
        description="Reprocesa un ticket RFP por ID"
    )
    parser.add_argument("ticket_id", help="UUID del ticket a reprocesar")
    args = parser.parse_args()

    asyncio.run(rerun_pipeline(args.ticket_id))


if __name__ == "__main__":
    main()