"""Workers del pipeline RFP — analizan el documento por departamento.

Cada worker recibe metadatos compartidos + extractos relevantes a su
departamento. Nunca inventa volúmenes/cifras que no estén en la RFP.
Si faltan datos requeridos, registra preguntas abiertas en key_aspects.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from openai import OpenAI

from data.pipelines.rfp_intake.models import (
    DepartmentSection,
    KeyAspect,
    RfpMetadata,
)

logger = logging.getLogger(__name__)

LLM_MODEL = os.getenv("RFP_WORKER_MODEL", "gpt-4o-mini")
LLM_TEMPERATURE = float(os.getenv("RFP_WORKER_TEMPERATURE", "0.1"))

# Usa la API Key y Host de 4Geeks AI (definidas en .env) para todos los LLM
LLM_API_KEY: str | None = os.getenv("API_KEY")
LLM_HOST: str | None = os.getenv("HOST")

WORKER_SYSTEM_PROMPT_TEMPLATE = """Eres un analista especializado del departamento de {department_name} en HealthCore.

Tu tarea es leer los extractos de una RFP que son relevantes para tu departamento y extraer:

1. **Aspectos clave (key_aspects)**: información relevante para tu departamento. Cada aspecto debe tener:
   - aspect: nombre corto del aspecto
   - detail: descripción detallada
   - confidence: "high" si está explícito en el documento, "medium" si es inferencia razonable, "low" si es especulativo

2. **Preguntas abiertas (open_questions)**: información que tu departamento necesita pero que NO está en el documento. NUNCA inventes cifras.

3. **PHI Check**: indica si el texto contiene nombres de pacientes, diagnósticos con nombre, o PHI.

Reglas:
- NUNCA inventes volúmenes, cabezas de familia, precios o fechas que no estén en el documento
- Si una cifra requerida no está presente, agrégala a open_questions
- Sé específico y extrae solo lo que está en el texto

Responde SOLO con un JSON con esta estructura:
{{
    "key_aspects": [
        {{"aspect": "...", "detail": "...", "confidence": "high/medium/low"}}
    ],
    "open_questions": [...],
    "contains_phi": false
}}
"""

DEPARTMENT_INFO = {
    "revenue": {
        "name": "Revenue Cycle (Tom Callahan)",
        "remit": "términos financieros, moneda, estructura de pago, pricing, volúmenes, costos, presupuesto"
    },
    "clinical": {
        "name": "Clinical Operations (Dr. Marcus Reid)",
        "remit": "viabilidad clínica, capacidad de personal, ubicaciones, tipo de programa, servicios requeridos"
    },
    "compliance": {
        "name": "Compliance and Data Governance (Claire Whitfield)",
        "remit": "revisión regulatoria, HIPAA/UK GDPR, BAA/DPA, país del cliente, PHI"
    },
}


def run_worker(department_id: str, metadata: RfpMetadata, raw_extract: str | None) -> DepartmentSection:
    """Ejecuta el análisis de un worker para un departamento.

    Args:
        department_id: Identificador del departamento.
        metadata: Metadatos compartidos de la RFP.
        raw_extract: Extracto del documento relevante para este departamento.

    Returns:
        DepartmentSection con key_aspects y open_questions.
    """
    dept_info = DEPARTMENT_INFO.get(department_id, {"name": department_id, "remit": "general"})

    # Construir el prompt del worker
    system_prompt = WORKER_SYSTEM_PROMPT_TEMPLATE.format(
        department_name=dept_info["name"]
    )

    metadata_text = metadata.model_dump_json(indent=2) if metadata else "No hay metadatos disponibles."
    extract_text = raw_extract or "No se encontraron secciones relevantes para este departamento."

    user_prompt = f"""## Metadatos de la RFP
{metadata_text}

## Extracto relevante para {dept_info['name']}
Remit: {dept_info['remit']}

{extract_text}
"""

    client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_HOST)
    try:
        response = client.chat.completions.create(
            model=LLM_MODEL,
            temperature=LLM_TEMPERATURE,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
        )

        result_text = response.choices[0].message.content or ""
        result = json.loads(result_text)

        key_aspects = [KeyAspect(**ka) for ka in result.get("key_aspects", [])]
        open_questions = result.get("open_questions", [])
        contains_phi = result.get("contains_phi", False)

        logger.info(
            "Worker %s: %d key_aspects, %d open_questions, contains_phi=%s",
            department_id,
            len(key_aspects),
            len(open_questions),
            contains_phi,
        )

        return DepartmentSection(
            department_id=department_id,
            key_aspects=key_aspects,
            open_questions=open_questions,
            contains_phi=contains_phi,
            raw_extract=extract_text,
        )

    except Exception as e:
        logger.error("Error en worker %s: %s", department_id, e)
        return DepartmentSection(
            department_id=department_id,
            key_aspects=[],
            open_questions=[f"Error procesando análisis: {e!s}"],
            contains_phi=False,
            raw_extract=extract_text,
        )


def run_worker_rule_based(department_id: str, metadata: RfpMetadata, raw_extract: str | None) -> DepartmentSection:
    """Versión basada en reglas del worker (sin LLM).

    Útil para tests o fallback si el LLM no está disponible.
    """
    dept_info = DEPARTMENT_INFO.get(department_id, {"name": department_id, "remit": "general"})
    extract_text = raw_extract or ""

    key_aspects = []

    # Reglas generales basadas en metadatos
    if metadata.client_name:
        key_aspects.append(KeyAspect(
            aspect="Cliente identificado",
            detail=f"El cliente es {metadata.client_name}",
            confidence="high",
        ))

    if metadata.program_type:
        key_aspects.append(KeyAspect(
            aspect="Tipo de programa",
            detail=f"Programa solicitado: {metadata.program_type}",
            confidence="high",
        ))

    if metadata.covered_population:
        key_aspects.append(KeyAspect(
            aspect="Población cubierta",
            detail=f"{metadata.covered_population} personas cubiertas",
            confidence="high",
        ))

    # Reglas específicas por departamento
    if department_id == "revenue":
        if metadata.client_country == "US":
            key_aspects.append(KeyAspect(
                aspect="Moneda",
                detail="La moneda debe ser USD para clientes de EE.UU.",
                confidence="high",
            ))
        elif metadata.client_country == "UK":
            key_aspects.append(KeyAspect(
                aspect="Moneda",
                detail="La moneda debe ser GBP para clientes del Reino Unido.",
                confidence="high",
            ))

    elif department_id == "compliance":
        if metadata.client_country == "US":
            key_aspects.append(KeyAspect(
                aspect="Acuerdo regulatorio",
                detail="Cliente en EE.UU.: requiere cláusula BAA (Business Associate Agreement).",
                confidence="high",
            ))
        elif metadata.client_country == "UK":
            key_aspects.append(KeyAspect(
                aspect="Acuerdo regulatorio",
                detail="Cliente en Reino Unido: requiere cláusula DPA (Data Processing Agreement) referenciando UK GDPR.",
                confidence="high",
            ))

        # Siempre activo para compliance
        key_aspects.append(KeyAspect(
            aspect="Revisión obligatoria",
            detail="Compliance es obligatorio en todas las RFPs, sin excepción.",
            confidence="high",
        ))

    # Preguntas abiertas
    open_questions = []
    if not metadata.covered_population:
        open_questions.append(f"¿Cuál es la población cubierta por el contrato? ({dept_info['name']})")
    if not metadata.deadline:
        open_questions.append(f"¿Cuál es la fecha límite para la propuesta? ({dept_info['name']})")

    if department_id == "revenue" and not metadata.budget_range:
        open_questions.append("¿Cuál es el presupuesto o rango de inversión del cliente?")

    return DepartmentSection(
        department_id=department_id,
        key_aspects=key_aspects,
        open_questions=open_questions,
        contains_phi=False,
        raw_extract=extract_text,
    )