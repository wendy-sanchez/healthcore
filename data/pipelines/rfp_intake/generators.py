"""Generadores de borradores de propuesta económica por departamento.

Cada generador recibe los metadatos de la RFP y los key_aspects de su
departamento (extraídos en la Parte 1) y produce un borrador de sección
de propuesta económica.

Si el generador tiene acceso a una base de conocimiento semántica de la
empresa, puede consultarla para redactar con políticas y lenguaje de marca
reales en lugar de improvisar.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import openai
from openai import OpenAI

logger = logging.getLogger(__name__)

LLM_API_KEY: str | None = os.getenv("API_KEY")
LLM_HOST: str | None = os.getenv("HOST")
LLM_MODEL: str = os.getenv("RFP_GENERATOR_MODEL", "madrid-spain/openrouter/deepseek/deepseek-v4-flash")
LLM_TEMPERATURE = float(os.getenv("RFP_GENERATOR_TEMPERATURE", "0.3"))

DEPARTMENT_PROMPTS: dict[str, str] = {
    "revenue": """Eres Tom Callahan, Revenue Cycle Director de HealthCore.
Redacta la sección de propuesta económica para el departamento REVENUE CYCLE.

Contexto de la RFP:
- Cliente: {client_name}
- País: {client_country} (determina moneda: USD para US, GBP para UK)
- Programa: {program_type}
- Población cubierta: {covered_population}
- Fecha límite: {deadline}
- Presupuesto: {budget_range}
- Moneda: {currency}

Key aspects identificados: {key_aspects}

Debes incluir EN TU BORRADOR:
1. Resumen de términos financieros propuestos
2. Moneda correcta según país del cliente (USD para US, GBP para UK)
3. Estructura de pago sugerida (ej. tarifa mensual por empleado)
4. Precio de referencia (usa valores de mercado, NO inventes cifras sin base)
5. Cláusula de renovación y plazo del contrato
6. Nota sobre BAA/DPA según aplique

NO incluyas:
- Identificadores de pacientes (PHI) — bajo ninguna circunstancia
- Cifras inventadas sin contexto del mercado
- Departamentos que no sean Revenue Cycle

Formato: texto plano en español. Extensión: 200-400 palabras.""",

    "clinical": """Eres el Dr. Marcus Reid, Director de Clinical Operations de HealthCore.
Redacta la sección de propuesta económica para el departamento CLINICAL OPERATIONS.

Contexto de la RFP:
- Cliente: {client_name}
- País: {client_country}
- Programa: {program_type}
- Población cubierta: {covered_population}
- Fecha límite: {deadline}

Key aspects identificados: {key_aspects}

Debes incluir EN TU BORRADOR:
1. Plan de personal clínico propuesto para cubrir el programa
2. Capacidad operativa: qué clínicas o sedes pueden dar servicio
3. Servicios clínicos incluidos en la propuesta
4. Frecuencia propuesta de servicio
5. Mecanismos de calidad y reporte clínico

NO incluyas:
- Identificadores de pacientes (PHI) — bajo ninguna circunstancia
- Diagnósticos o datos clínicos de pacientes reales
- Precios o términos financieros (eso es de Revenue Cycle)

Formato: texto plano en español. Extensión: 200-400 palabras.""",

    "compliance": """Eres Claire Whitfield, Chief Compliance Officer de HealthCore.
Redacta la sección de propuesta económica para el departamento COMPLIANCE AND DATA GOVERNANCE.

Contexto de la RFP:
- Cliente: {client_name}
- País: {client_country} (US → HIPAA + BAA requerido; UK → UK GDPR + DPA requerido)
- Programa: {program_type}
- Población cubierta: {covered_population}

Key aspects identificados: {key_aspects}

LINEAMIENTOS OBLIGATORIOS (del CONTEXT.md §5):
1. ☐ NINGUNA sección puede contener nombres, diagnósticos o identificadores de paciente
2. ☐ Cliente US: debe incluir cláusula de Business Associate Agreement (BAA)
3. ☐ Cliente UK: debe incluir cláusula de Data Processing Agreement (DPA) referenciando UK GDPR
4. ☐ Precio en USD para US, en GBP para UK
5. ☐ Compliance es obligatorio en TODAS las propuestas sin excepción

Debes incluir EN TU BORRADOR:
1. Revisión regulatoria aplicable (HIPAA o UK GDPR según país)
2. Cláusula BAA o DPA correcta según país del cliente
3. Declaración de que la sección no contiene PHI
4. Cualquier requisito adicional de protección de datos

NO incluyas:
- Identificadores de pacientes (PHI) — bajo ninguna circunstancia
- Datos de salud de individuos
- Aprobaciones de otros departamentos

Formato: texto plano en español. Extensión: 200-400 palabras."""
}


def _build_system_prompt(department_id: str, metadata: Any, key_aspects: list[dict]) -> str:
    """Construye el prompt del generador para el departamento."""
    template = DEPARTMENT_PROMPTS.get(department_id, "")
    if not template:
        raise ValueError(f"Departamento desconocido: {department_id}")

    client_country = getattr(metadata, "client_country", "US")
    currency = getattr(metadata, "currency", "USD" if client_country == "US" else "GBP")

    fmt_key_aspects = json.dumps(key_aspects, indent=2, ensure_ascii=False)

    return template.format(
        client_name=getattr(metadata, "client_name", "Cliente no identificado"),
        client_country=client_country,
        program_type=getattr(metadata, "program_type", "No especificado"),
        covered_population=getattr(metadata, "covered_population", "No especificada"),
        deadline=getattr(metadata, "deadline", "No especificada"),
        budget_range=getattr(metadata, "budget_range", "No especificado"),
        currency=currency,
        key_aspects=fmt_key_aspects,
    )


async def generate_draft(
    department_id: str,
    rfp_metadata: Any,
    key_aspects: list[dict],
    previous_feedback: str | None = None,
) -> str:
    """Genera un borrador de sección de propuesta económica.

    Args:
        department_id: revenue, clinical, compliance.
        rfp_metadata: Metadatos de la RFP (RfpMetadata o dict-like).
        key_aspects: Lista de key_aspects del handoff de Parte 1.
        previous_feedback: Feedback de iteración anterior (si hay).

    Returns:
        Borrador en texto plano.
    """
    system_prompt = _build_system_prompt(department_id, rfp_metadata, key_aspects)

    if previous_feedback:
        system_prompt += f"""

FEEDBACK DE LA ITERACIÓN ANTERIOR (debes corregir esto):
{previous_feedback}

IMPORTANTE: Revisa tu borrador anterior y corrige específicamente los puntos
mencionados en el feedback. No repitas los mismos errores."""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": (
            f"Genera el borrador de la propuesta económica para el departamento "
            f"{department_id} de HealthCore basado en la RFP del cliente."
        )},
    ]

    try:
        if not LLM_API_KEY or not LLM_HOST:
            logger.warning("LLM no configurado. Usando fallback rule-based.")
            return _rule_based_fallback(department_id, rfp_metadata, key_aspects)

        client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_HOST)
        response = await asyncio.to_thread(
            client.chat.completions.create,
            model=LLM_MODEL,
            messages=messages,
            temperature=LLM_TEMPERATURE,
            max_tokens=1024,
        )

        content = response.choices[0].message.content or ""
        if not content.strip():
            return _rule_based_fallback(department_id, rfp_metadata, key_aspects)
        return content.strip()

    except Exception as e:
        logger.warning("LLM falló para generador %s: %s. Usando fallback.", department_id, e)
        return _rule_based_fallback(department_id, rfp_metadata, key_aspects)


def _rule_based_fallback(department_id: str, metadata: Any, key_aspects: list[dict]) -> str:
    """Generador rule-based de respaldo cuando el LLM no está disponible."""
    company = getattr(metadata, "client_name", "el cliente")
    country = getattr(metadata, "client_country", "US")
    population = getattr(metadata, "covered_population", "—")
    currency = "USD" if country == "US" else "GBP"

    lines = [f"# Propuesta Económica — {department_id.upper()}\n"]
    lines.append(f"**Cliente:** {company}")
    lines.append(f"**País:** {country}")
    lines.append(f"**Moneda:** {currency}\n")

    if department_id == "revenue":
        lines.append("**Términos financieros propuestos:**")
        lines.append(f"- Tarifa mensual estimada por persona cubierta en {currency}")
        lines.append("- Contrato inicial de 12 meses con opción a renovación")
        lines.append("- Facturación mensual")
        if population and population != "—":
            lines.append(f"- Población estimada: {population} personas")
        lines.append("\n**Notas:**")
        lines.append("- Los precios unitarios deben confirmarse con el equipo de Pricing")
        lines.append("- Se requiere BAA/DPA según país del cliente")

    elif department_id == "clinical":
        lines.append("**Plan de personal y capacidad clínica:**")
        lines.append("- Personal clínico asignado según volumen de población cubierta")
        lines.append("- Servicios: atención ocupacional, exámenes de bienestar, manejo de crónicos")
        lines.append("- Frecuencia propuesta: según necesidad definida en la RFP")
        if population and population != "—":
            lines.append(f"- Capacidad para atender a {population} personas")

    elif department_id == "compliance":
        lines.append("**Revisión regulatoria:**")
        if country == "US":
            lines.append("- Aplica HIPAA. Se incluirá BAA (Business Associate Agreement).")
        else:
            lines.append("- Aplica UK GDPR. Se incluirá DPA (Data Processing Agreement).")
        lines.append("- Esta sección NO contiene PHI (Protected Health Information).")
        lines.append("- Compliance es requisito obligatorio para toda propuesta institucional.")

    lines.append(f"\n*Borrador generado automáticamente — pendiente de revisión.*")
    return "\n".join(lines)