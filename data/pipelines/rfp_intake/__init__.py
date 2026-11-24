"""Pipeline RFP Intake — recepción, clasificación y descomposición de RFPs.

Este paquete implementa el pipeline agéntico de recepción de RFPs:
1. Conversión PDF → Markdown
2. Extracción de metadatos y legibilidad
3. Clasificación (¿es RFP?)
4. Orquestación por departamento
5. Síntesis final

Uso desde Celery:
    from data.pipelines.rfp_intake.tasks import process_rfp_pipeline
    process_rfp_pipeline.delay(ticket_id="...")

Uso directo:
    from data.pipelines.rfp_intake.graph import run_rfp_pipeline
    result = await run_rfp_pipeline(ticket_id="...")
"""