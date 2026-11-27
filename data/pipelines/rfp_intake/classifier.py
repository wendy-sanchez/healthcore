"""Clasificador de documentos RFP.

Determina si un documento convertido a Markdown es una RFP legítima
para HealthCore. Si no lo es, detiene el flujo y deja el ticket en
descartado.

Criterios de clasificación:
  - Es una RFP si menciona un cliente institucional, programa de salud,
    población cubierta y está dirigido a un proveedor de servicios de salud.
  - NO es RFP si es un pitch de ventas, CV, artículo genérico, etc.
"""

from __future__ import annotations

import json
import logging
import os
import re

from openai import OpenAI

logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────

LLM_MODEL = os.getenv("RFP_CLASSIFIER_MODEL", "gpt-4o-mini")
LLM_TEMPERATURE = float(os.getenv("RFP_CLASSIFIER_TEMPERATURE", "0.1"))

# Usa la API Key y Host de 4Geeks AI (definidas en .env) para todos los LLM
LLM_API_KEY: str | None = os.getenv("API_KEY")
LLM_HOST: str | None = os.getenv("HOST")


# ── System prompt ────────────────────────────────────────────────────

CLASSIFIER_SYSTEM_PROMPT = """Eres un clasificador de documentos para HealthCore, una red de clínicas ambulatorias en EE.UU. y Reino Unido.

Tu única tarea es determinar si un documento es una **RFP (Request for Proposal) legítima** dirigida a HealthCore o a un proveedor de servicios de salud.

Una RFP legítima DEBE tener:
1. Un **cliente institucional** (empresa, universidad, organización) que solicita una propuesta
2. Un **programa de salud** específico (salud ocupacional, bienestar corporativo, red de referidos)
3. Una **población cubierta** (número de empleados, estudiantes, beneficiarios)
4. Está dirigida a un **proveedor de servicios de salud** (HealthCore o similar)

NO es una RFP:
- Pitch de ventas de un proveedor (EHR vendor, software, equipamiento)
- Currículum, CV o solicitud de empleo
- Artículo académico o presentación genérica
- Correspondencia interna sin estructura de RFP
- Documento promocional o marketing

Responde SOLO con un JSON válido con estos campos:
- "is_rfp": true/false
- "confidence": "high"/"medium"/"low"
- "reason": Explicación breve de la decisión (máx 2 oraciones)
- "client_name": Nombre del cliente si se identifica, o null
- "client_country": "US" o "UK" si se puede determinar, o null
- "program_type": "occupational_health", "corporate_wellness", "referral_network", u otro string, o null
- "covered_population": número entero si se menciona, o null
- "deadline": fecha en formato YYYY-MM-DD si se menciona, o null
- "has_phi": true/false — true si el documento contiene nombres de pacientes, diagnósticos con nombre, números de seguro social, o cualquier Protected Health Information
- "departments_needed": lista de departamentos de HealthCore necesarios para esta propuesta (opciones: "revenue", "clinical", "compliance") o lista vacía si no se mencionan
- "extra_departments_mentioned": lista de departamentos mencionados que NO existen en HealthCore, o lista vacía

No incluyas markdown, solo JSON."""


def classify_document(markdown_content: str) -> dict:
    """Clasifica un documento como RFP válida o no.

    Args:
        markdown_content: Contenido del documento en Markdown.

    Returns:
        Diccionario con: is_rfp, confidence, reason, client_name,
        client_country, program_type, covered_population, deadline, has_phi,
        departments_needed, extra_departments_mentioned.
    """
    if not markdown_content or len(markdown_content.strip()) < 20:
        return {
            "is_rfp": False,
            "confidence": "high",
            "reason": "El documento está vacío o es demasiado corto para ser una RFP.",
            "client_name": None,
            "client_country": None,
            "program_type": None,
            "covered_population": None,
            "deadline": None,
            "has_phi": False,
            "departments_needed": [],
            "extra_departments_mentioned": [],
        }

    # Primero: clasificación rápida basada en reglas (pre-filtro)
    rule_based = _rule_based_classification(markdown_content)
    if rule_based["is_rfp"] is False and rule_based["confidence"] == "high":
        logger.info("Clasificación por reglas: NO es RFP (%s)", rule_based["reason"])
        return rule_based

    # Si las reglas ya detectaron RFP con alta confianza, devolver sin LLM
    if rule_based["is_rfp"] is True and rule_based["confidence"] == "high":
        logger.info("Clasificación por reglas: SÍ es RFP (%s)", rule_based["reason"])
        try:
            # Intentar extracción adicional vía LLM para enriquecer metadatos
            client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_HOST)
            content = markdown_content[:15000] if len(markdown_content) > 15000 else markdown_content
            response = client.chat.completions.create(
                model=LLM_MODEL,
                temperature=LLM_TEMPERATURE,
                messages=[
                    {"role": "system", "content": CLASSIFIER_SYSTEM_PROMPT},
                    {"role": "user", "content": f"Documento a clasificar:\n\n{content}"},
                ],
                response_format={"type": "json_object"},
            )
            result_text = response.choices[0].message.content or ""
            result = json.loads(result_text)
            return result
        except Exception as e:
            logger.warning("LLM falló para RFP confirmada por reglas: %s. Usando clasificación por reglas.", e)
            return rule_based

    # Si las reglas no son concluyentes, usar LLM
    client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_HOST)
    try:
        # Truncar si es muy largo (ahorrar tokens)
        content = markdown_content[:15000] if len(markdown_content) > 15000 else markdown_content

        response = client.chat.completions.create(
            model=LLM_MODEL,
            temperature=LLM_TEMPERATURE,
            messages=[
                {"role": "system", "content": CLASSIFIER_SYSTEM_PROMPT},
                {"role": "user", "content": f"Documento a clasificar:\n\n{content}"},
            ],
            response_format={"type": "json_object"},
        )

        result_text = response.choices[0].message.content or ""
        result = json.loads(result_text)

        # Validar campos requeridos
        if "is_rfp" not in result:
            raise ValueError("Respuesta del clasificador sin campo is_rfp")

        logger.info(
            "Clasificación LLM: is_rfp=%s, confidence=%s, reason=%s",
            result.get("is_rfp"),
            result.get("confidence"),
            result.get("reason", ""),
        )

        return result

    except Exception as e:
        logger.error("Error en clasificación LLM: %s. Usando fallback por reglas.", e)
        # Fallback: usar solo reglas
        return rule_based


def _rule_based_classification(markdown_content: str) -> dict:
    """Clasificación basada en reglas sin LLM.

    Detecta patrones claros de NO-RFP (pitch de ventas, CV) y
    patrones claros de SÍ-RFP.
    """
    text_lower = markdown_content.lower()

    # Patrones de NO-RFP (alta confianza)
    no_rfp_patterns = [
        # Pitch de ventas (EHR vendor, software)
        (r"\b(?:our platform|our solution|our product|schedule a demo|book a demo)\b",
         "Parece un pitch de ventas o demo de producto, no una RFP."),
        # CV / Resume
        (r"\b(?:resume|cv|curriculum vitae|professional experience|work history)\b",
         "Parece un currículum o CV, no una RFP."),
        # Puro marketing / newsletter
        (r"\b(?:subscribe to our|newsletter|sign up for|follow us on)\b",
         "Parece material promocional o newsletter, no una RFP."),
        # EHR vendor pitch
        (r"\b(?:ehr platform|modernize your|our platform was built for|unified ehr|our platform could replace)\b",
         "Parece un pitch de proveedor de EHR/software, no una RFP."),
    ]

    for pattern, reason in no_rfp_patterns:
        if re.search(pattern, text_lower):
            return {
                "is_rfp": False,
                "confidence": "high",
                "reason": reason,
                "client_name": None,
                "client_country": None,
                "program_type": None,
                "covered_population": None,
                "deadline": None,
                "has_phi": False,
                "departments_needed": [],
                "extra_departments_mentioned": [],
            }

    # Patrones de SÍ-RFP (alta confianza)
    rfp_patterns = [
        (r"\b(?:request for proposal|rfp|request for quotation|rfq|solicitud de propuesta)\b",
         "El documento se identifica explícitamente como RFP/RFQ."),
        (r"\b(?:proposal for|scope of work|statement of work|healthcore|health core)\b.*\b(?:occupational|wellness|employee health|referral)\b",
         "El documento describe un programa de salud institucional."),
        # Partnership / referral network enquiry (informal RFP)
        (r"\b(?:(?:referral|refer) partnership|referral network|student health|partnership enquiry)\b",
         "El documento describe una solicitud de alianza o red de referidos."),
    ]

    for pattern, reason in rfp_patterns:
        if re.search(pattern, text_lower):
            return {
                "is_rfp": True,
                "confidence": "high",
                "reason": reason,
                "client_name": None,
                "client_country": None,
                "program_type": None,
                "covered_population": None,
                "deadline": None,
                "has_phi": False,
                "departments_needed": [],
                "extra_departments_mentioned": [],
            }

    # Si no hay patrón claro, devolver None para que el LLM decida
    return {
        "is_rfp": None,
        "confidence": "low",
        "reason": "Clasificación incierta por reglas. Se requiere LLM.",
        "client_name": None,
        "client_country": None,
        "program_type": None,
        "covered_population": None,
        "deadline": None,
        "has_phi": False,
        "departments_needed": [],
        "extra_departments_mentioned": [],
    }