"""Almacén persistente de memoria del agente.

Usa TinyDB como backend de persistencia — ya está como dependencia del
proyecto, es zero-config, y suficiente para el volumen de hechos operativos
que el agente necesita recordar (centenares, no millones).

Estructura en TinyDB:
  - ``memory_facts`` (table) — Hechos operativos consolidados y aprobados.
    Cada fact tiene:
      - id (int, auto)
      - content (str): El hecho en sí
      - category (str): 'operational', 'incident_pattern', 'staff_preference'
      - source_run_id (str): run_id que originó la propuesta
      - approved_at (str): timestamp ISO de aprobación
      - consolidated (bool): si es un consolidado de facts anteriores
      - expires_at (str | None): timestamp ISO de expiración

  - ``pending_proposal`` (table) — Una única propuesta pendiente (0 o 1 docs).
    Cada propuesta tiene:
      - id (int, auto)
      - proposal (str): Qué se propone recordar
      - reason (str): Por qué vale la pena recordarlo
      - category (str): Categoría del fact propuesto
      - source_run_id (str): run_id que originó la propuesta
      - source_question (str): Pregunta del usuario que motivó la propuesta
      - proposed_at (str): timestamp ISO
      - phi_validated (bool): True si pasó validación PHI

  - ``audit_log`` (table) — Registro auditable de cada decisión.
    Cada entrada tiene:
      - id (int, auto)
      - event_type (str): 'proposed' | 'approved' | 'rejected' | 'discarded'
                          | 'consolidated' | 'expired' | 'phi_rejected'
      - run_id (str): run_id asociado
      - proposal (dict | None): La propuesta completa en el momento del evento
      - user_message (str | None): Mensaje del usuario que provocó la decisión
      - classification (str | None): Cómo se clasificó la intención del usuario
      - detail (str | None): Detalle adicional
      - timestamp (str): timestamp ISO

PHI validation:
  - Cada propuesta pasa por ``validate_phi_safe()`` antes de mostrarse al usuario.
  - La consolidación también re-valida PHI antes de escribir.
  - Lista de patrones PHI configurada en el validador.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tinydb import Query, TinyDB

logger = logging.getLogger(__name__)

# ── Rutas ────────────────────────────────────────────────────────────

_MEMORY_DIR = Path(
    os.getenv(
        "AGENT_MEMORY_DIR",
        str(Path(__file__).resolve().parent.parent.parent.parent / "data" / "process" / "agent_memory"),
    )
)
_MEMORY_DIR.mkdir(parents=True, exist_ok=True)

_MEMORY_DB_PATH = _MEMORY_DIR / "agent_memory.json"

# ── Singleton de la BD ──────────────────────────────────────────────

_db: TinyDB | None = None


def _get_db() -> TinyDB:
    """Obtiene (o crea) la conexión TinyDB singleton."""
    global _db
    if _db is None:
        _db = TinyDB(str(_MEMORY_DB_PATH), create_dirs=True, encoding="utf-8")
        logger.info("Memoria del agente: %s", _MEMORY_DB_PATH)
    return _db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_plus_days(days: int) -> str:
    """Retorna timestamp ISO de ahora + N días."""
    import datetime as dt
    return (datetime.now(timezone.utc) + dt.timedelta(days=days)).isoformat()


# ═══════════════════════════════════════════════════════════════════════
#  PHI Validation
# ═══════════════════════════════════════════════════════════════════════

# Patrones PHI que detectan información de paciente.
# Esta lista cubre tanto HIPAA (US) como UK GDPR (UK).
# HIPAA: nombres, fechas de nacimiento, teléfonos, SSN, MRN, etc.
# UK GDPR: NHS number, cualquier combinación re-identificable.

# ── Lista blanca: términos que NUNCA deben ser PHI ─────────────────
# HealthCore staff, clínicas y ubicaciones conocidas.
# CONTEXT.md: preferencias de staff SÍ son memorables; datos operativos
# de clínicas (Austin, Manchester, etc.) también.
_PHI_WHITELIST: set[str] = {
    # HealthCore Staff
    "Diane Foster", "Claire Whitfield", "James Osei",
    "Marcus Reid", "Sandra Okonkwo", "Priya Nair",
    # Clínicas y ubicaciones operativas
    "Austin", "Manchester", "London", "Miami",
    "Dallas", "Houston", "San Antonio", "Orlando",
    "Tampa", "Atlanta", "Savannah", "Central",
    "Texas", "Florida", "Georgia",
}
# Combinaciones preposición+ubicación (ej. "En Austin") que NO son nombre
_PHI_WHITELIST_PREFIXED: set[str] = {
    "en austin", "en manchester", "en london", "en miami",
    "en dallas", "en houston", "en san antonio", "en orlando",
    "en tampa", "en atlanta", "en savannah",
    "la austin", "la manchester", "la london",
}

_PHI_PATTERNS: list[re.Pattern] = [
    # ── Identificadores personales (US + UK) ─────────────────────────
    re.compile(r"\b[A-Z][a-zA-ZáéíóúñÁÉÍÓÚ]+ [A-Z][a-zA-ZáéíóúñÁÉÍÓÚ]+\b"),  # Nombre completo (ej. "John Smith", "Juan Pérez")
    # ── Datos de contacto ────────────────────────────────────────────
    re.compile(r"\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b"),       # Teléfono USA (555-123-4567)
    re.compile(r"\b\d{5}\s\d{6,7}\b"),                       # NHS Number UK (123 4567890)
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),                    # SSN (123-45-6789)
    # ── Teléfono internacional (español/europeo: +34 612 345 678) ────
    re.compile(r"(?:(?<=\s)|(?<=^))\+?\d{1,3}[\s.-]?\d{2,4}(?:[\s.-]?\d{2,4}){2,3}\b"),
    # ── Información clínica (términos y diagnósticos) ────────────────
    re.compile(r"\b(?:diagn[oó]stico|diagnosis|treatment|tratamiento|"
               r"prescripci[oó]n|prescription|medication|medicaci[oó]n|"
               r"resultados?\s+de\s+laboratorio|lab\s+results?)\s*:?\s*\S",
               re.IGNORECASE),
    # ── Términos clínicos sensibles (condiciones, enfermedades) ─────
    re.compile(r"\b(diabetes|c[ná]ncer|tumor|VIH|SIDA|leucemia|quimioterapia|"
               r"hipertensión|cardiopatía|insuficiencia|hepatitis|cirrosis|"
               r"enfermedad\s+cr[oó]nica|diagnóstico|patología|neoplasia|"
               r"met[áa]stasis|infecci[oó]n|síndrome|trastorno|"
               r"alzhéimer|parkinson|esclerosis|artritis|asma|epilepsia|"
               r"depresi[oó]n|ansiedad|obesidad|osteoporosis)"
               r"(?:\s+(?:tipo\s+\d|cr[oó]nica|aguda|leve|grave|moderada))?",
               re.IGNORECASE),
    # ── Palabra "paciente" en contexto narrativo (no operativo) ────
    re.compile(r"\b(?:el\s+)?paciente\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+\s", re.IGNORECASE),
    # ── Fechas de nacimiento / edad exacta ───────────────────────────
    re.compile(r"\b(?:fecha\s+de\s+nacimiento|dob|birth\s*date|born|"
               r"nacid[oó]?\s+el)\s*:?\s*\d{1,2}[/-]\d{1,2}[/-]\d{2,4}",
               re.IGNORECASE),
    # ── Fecha suelta en contexto clínico (sin prefijo, pero con año 19xx/20xx) ──
    re.compile(r"\b\d{2}[/-]\d{2}[/-](?:19|20)\d{2}\b"),
    # ── Identificadores de paciente (MRN, EXP, PAT, HCC, etc.) ──────
    re.compile(r"\b(?:patient\s+(?:id|number|#|n[uo]mero)|"
               r"paciente\s+(?:id|n[uo]mero|#)|"
               r"historia\s+cl[ií]nica|medical\s+record\s+number)\s*:?\s*\w+",
               re.IGNORECASE),
    # ── MRN / EXP / PAT + dígitos (con o sin guion o dos puntos) ─────
    re.compile(r"\b(MRN|EX[AP]|HCC|PAT|HIST|NHS|SSN)\s*[-:_]?\s*\d{4,}\b", re.IGNORECASE),
    # ── Email ────────────────────────────────────────────────────────
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),
    # ── Dirección física (US/UK) ─────────────────────────────────────
    re.compile(r"\b\d{1,5}\s+[A-Za-z]+\s+(?:Street|St|Avenue|Av|Road|Rd|"
               r"Drive|Dr|Lane|Ln|Boulevard|Blvd|Circle|Cir)\b", re.IGNORECASE),
    # ── Apellidos precedidos de título o rol ─────────────────────────
    # NOTA: Dr[aá]?\.? — el ? después de [aá] es crucial: "Dra." y "Dr." deben
    # detectarse. Sin el ?, "Dr. Smith" pasaría como PHI-safe.
    re.compile(r"\b(?:Sr[aá]?\.?|Sra\.|Dr[aá]?\.?|Paciente|Socio|Cliente)\s+"
               r"[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)?", re.IGNORECASE),
    # ── Apellidos hispanos comunes en contexto narrativo ──────────────
    # Detecta apellidos como García, López, Pérez, Rodríguez cuando aparecen
    # como palabras capitalizadas standalone (sin nombre de pila).
    re.compile(r"\b(?:García|López|Pérez|Rodríguez|Martínez|Hernández|"
               r"González|Sánchez|Díaz|Álvarez|Fernández|Gutiérrez|"
               r"Jiménez|Morales|Ruiz|Ramírez|Moreno|Muñoz|Romero|"
               r"Navarro|Torres|Domínguez|Vázquez|Ramos|Cruz|Flores|"
               r"Rivera|Reyes|Castillo|Ortiz|Medina|Silva|Garza|Vargas|"
               r"Campos|Guzmán|Delgado|Pena|Contreras|Valdez|Estrada|"
               r"Luna|Juárez|Aguilar|Rojas|Salazar|Chávez|Núñez|Cabrera|"
               r"Acosta|Figueroa|Mendoza|Vega|Soto|Molina|Cortés|"
               r"Suárez|Castro|Pacheco|Velázquez|Ibáñez|Márquez|"
               r"Guerrero|Santos|Valencia|Peña|Peralta|Rivas|Meza)\b"),
]


def validate_phi_safe(text: str) -> tuple[bool, list[str]]:
    """Valida que un texto NO contenga PHI (Protected Health Information).

    Args:
        text: Texto a validar (propuesta de memoria).

    Returns:
        Tuple (es_segura: bool, coincidencias: list[str]).
        ``es_segura=True`` si no se detectó PHI.
        ``coincidencias`` lista de los patrones que coincidieron (vacíos si segura).
    """
    if not text:
        return True, []

    matches: list[str] = []
    for pattern in _PHI_PATTERNS:
        found = pattern.findall(text)
        matches.extend(found)

    # ── Filtrar falsos positivos contra lista blanca ────────────────
    # HealthCore staff (Diane Foster, Claire Whitfield, etc.) y nombres
    # de clínicas (Austin, Manchester, etc.) NO son PHI, incluso si
    # coinciden con patrones de "Nombre Apellido".
    # CONTEXT.md: preferencias de staff y datos operativos de clínicas
    # SÍ son memorables y no deben ser bloqueados por PHI validation.
    filtered: list[str] = []
    for m in matches:
        m_lower = m.lower().strip()
        # Saltar si el match exacto está en whitelist
        if m_lower in _PHI_WHITELIST or m_lower in _PHI_WHITELIST_PREFIXED:
            continue
        # Saltar si "preposición + ubicación" (ej. "en austin")
        # Patrones de nombre completo como "En Austin" son preposición+ciudad
        skip = False
        for wp in _PHI_WHITELIST_PREFIXED:
            if wp in m_lower:
                skip = True
                break
        if skip:
            continue
        # Saltar si es una ubicación conocida como palabra suelta
        # (ej. solo "Austin" sin apellido)
        for loc in _PHI_WHITELIST:
            loc_lower = loc.lower()
            if m_lower == loc_lower or m_lower == loc_lower + " ":
                skip = True
                break
        if skip:
            continue
        # Saltar si el match contiene un nombre de staff whitelisted
        # (ej. "Dr. Marcus Reid" contiene "Marcus Reid")
        staff_skip = False
        for wl in _PHI_WHITELIST:
            wl_lower = wl.lower()
            if len(wl_lower.split()) >= 2 and wl_lower in m_lower:
                staff_skip = True
                break
        if staff_skip:
            continue
        # Saltar patrones de título sin nombre propio real
        # "Dr. recomienda" (título + verbo en minúscula) NO es PHI.
        # Solo cuando el título es seguido DIRECTAMENTE de una palabra en minúscula.
        # NO aplicar cuando el título va seguido de "id:", "número:", "#" etc. (identificadores).
        _TITULOS = ("sr", "sra", "dr", "dra", "paciente", "socio", "cliente", "mr", "mrs", "ms")
        _es_titulo_sin_nombre = False
        for t in _TITULOS:
            idx = m_lower.find(t)
            if idx >= 0:
                if idx > 0 and m_lower[idx - 1].isalpha():
                    continue
                end_of_title = idx + len(t)
                rest_of_m = m[end_of_title:]
                if rest_of_m.startswith("."):
                    rest_of_m = rest_of_m[1:]
                rest_of_m = rest_of_m.lstrip()
                if rest_of_m and rest_of_m[0].islower():
                    # Verificar que lo que sigue NO es un identificador (id, número, #)
                    next_word = rest_of_m.split(None, 1)[0].rstrip(".:,#")
                    if next_word.lower() not in ("id", "número", "numero", "#", "no", "nº"):
                        _es_titulo_sin_nombre = True
                        break
        if _es_titulo_sin_nombre:
            continue
        # Saltar falsos positivos del patrón de Nombre Completo (patrón 0)
        # "El Dr", "El paciente", "La clínica" no son nombres de persona.
        # Con re.IGNORECASE, \b[A-Z][a-z]+ [A-Z][a-z]+ matchea artículos + palabras.
        _NO_NAME_PREFIXES = ("el ", "la ", "los ", "las ", "un ", "una ", "en ")
        m_for_check = m_lower
        for prefix in _NO_NAME_PREFIXES:
            if m_for_check.startswith(prefix):
                # Saltar solo si NO es un nombre claro
                # "El paciente Johnson" debe seguir siendo PHI
                second_word = m_for_check[len(prefix):].split(None, 1)
                if second_word and second_word[0] in ("dr", "sr", "sra", "paciente", "señor", "señora"):
                    # "El Dr", "El paciente" — no son PHI por sí solos
                    break
                elif second_word and second_word[0][0].isalpha():
                    # Verificar si la siguiente palabra después del artículo es un título
                    # o si el match total sigue siendo sospechoso
                    rest_after_article = m[len(prefix):].lstrip()
                    if rest_after_article and rest_after_article[0].islower():
                        # "El paciente está..." — comienza con minúscula después del artículo
                        # Esto significa que el matcheo es por IGNORECASE de una palabra en minúscula
                        break
                    # Si la palabra después del artículo empieza con mayúscula real
                    # podría ser nombre propio o podría ser "El Dr" (Dr con mayúscula)
                    # Solo saltar si es muy corto (1-2 palabras)
                    words = m_for_check.split()
                    if len(words) <= 3:
                        # "El Dr" de 2 palabras → probable falso positivo
                        break
                break
        else:
            filtered.append(m)
            continue
        continue

    # Si hay coincidencias no filtradas, no es seguro
    if filtered:
        return False, list(set(filtered))

    return True, []


# ═══════════════════════════════════════════════════════════════════════
#  Helpers de serialización
# ═══════════════════════════════════════════════════════════════════════


def _serializable(doc: dict[str, Any]) -> dict[str, Any]:
    """Convierte un documento TinyDB a dict serializable (sin el doc_id interno)."""
    # TinyDB asigna doc_id internamente; lo preservamos como 'id' para trazabilidad
    result = dict(doc)
    return result


# ═══════════════════════════════════════════════════════════════════════
#  Propuesta pendiente (0 o 1)
# ═══════════════════════════════════════════════════════════════════════


def has_pending_proposal() -> bool:
    """Verifica si existe una propuesta pendiente."""
    db = _get_db()
    table = db.table("pending_proposal")
    return len(table) > 0


def get_pending_proposal() -> dict[str, Any] | None:
    """Obtiene la propuesta pendiente actual, o None."""
    db = _get_db()
    table = db.table("pending_proposal")
    docs = table.all()
    if not docs:
        return None
    return _serializable(docs[0])


def set_pending_proposal(
    proposal: str,
    reason: str,
    category: str,
    source_run_id: str,
    source_question: str,
) -> dict[str, Any] | None:
    """Establece una propuesta pendiente (falla si ya hay una).

    Args:
        proposal: Qué se propone recordar.
        reason: Por qué vale la pena.
        category: Categoría del hecho.
        source_run_id: run_id que originó la propuesta.
        source_question: Pregunta del usuario que motivó la propuesta.

    Returns:
        La propuesta creada, o None si ya hay una pendiente.
    """
    if has_pending_proposal():
        logger.warning("Intento de crear propuesta cuando ya hay una pendiente.")
        return None

    # Validación PHI antes de guardar
    phi_safe, phi_matches = validate_phi_safe(proposal)
    if not phi_safe:
        logger.warning(
            "Propuesta rechazada por PHI: coincidencias=%s, propuesta='%s'",
            phi_matches, proposal[:100],
        )
        _record_audit(
            event_type="phi_rejected",
            run_id=source_run_id,
            proposal={"proposal": proposal, "reason": reason, "category": category},
            detail=f"Contenía PHI: {phi_matches}",
        )
        return None

    db = _get_db()
    table = db.table("pending_proposal")
    doc = {
        "proposal": proposal,
        "reason": reason,
        "category": category,
        "source_run_id": source_run_id,
        "source_question": source_question,
        "proposed_at": _now(),
        "phi_validated": True,
    }
    doc_id = table.insert(doc)
    table.update({"id": doc_id}, doc_ids=[doc_id])
    doc["id"] = doc_id

    _record_audit(
        event_type="proposed",
        run_id=source_run_id,
        proposal=doc,
        detail=f"Propuesta creada (categoría: {category})",
    )

    logger.info("Propuesta de memoria creada (id=%s): '%s'", doc_id, proposal[:80])
    return _serializable(doc)


def clear_pending_proposal() -> None:
    """Elimina la propuesta pendiente actual."""
    db = _get_db()
    table = db.table("pending_proposal")
    table.truncate()
    logger.debug("Propuesta pendiente eliminada.")


# ═══════════════════════════════════════════════════════════════════════
#  Hechos de memoria (memory_facts)
# ═══════════════════════════════════════════════════════════════════════


def add_fact(
    content: str,
    category: str,
    source_run_id: str,
    approved_at: str | None = None,
    consolidated: bool = False,
    expires_at: str | None = None,
) -> dict[str, Any] | None:
    """Agrega un hecho a la memoria del agente.

    Args:
        content: El hecho en sí.
        category: Categoría ('operational', 'incident_pattern', 'staff_preference').
        source_run_id: run_id que originó la propuesta.
        approved_at: Timestamp de aprobación (None = usar ahora).
        consolidated: Si es un registro de consolidación.
        expires_at: Timestamp de expiración opcional.

    Returns:
        El hecho creado, o None si el contenido contenía PHI.
    """
    # Validación PHI antes de escribir
    phi_safe, phi_matches = validate_phi_safe(content)
    if not phi_safe:
        logger.warning(
            "add_fact rechazado por PHI: %s. Content: '%s'",
            phi_matches,
            content[:80],
        )
        _record_audit(
            event_type="phi_rejected",
            run_id=source_run_id,
            proposal={"proposal": content, "category": category},
            detail=f"add_fact rechazó escritura por PHI: {phi_matches}",
        )
        return None

    db = _get_db()
    table = db.table("memory_facts")

    if approved_at is None:
        approved_at = _now()

    # Asignar TTL por defecto si no se especifica expiración
    # y no es un hecho consolidado
    if expires_at is None and not consolidated:
        expires_at = _now_plus_days(_DEFAULT_TTL_DAYS)

    doc = {
        "content": content,
        "category": category,
        "source_run_id": source_run_id,
        "approved_at": approved_at,
        "consolidated": consolidated,
        "expires_at": expires_at,
    }
    doc_id = table.insert(doc)
    # Persistir el doc_id como campo "id" en la BD para poder referenciarlo después
    table.update({"id": doc_id}, doc_ids=[doc_id])
    doc["id"] = doc_id
    logger.info("Hecho agregado a memoria (id=%s): '%s'", doc_id, content[:80])
    return _serializable(doc)


def get_all_facts() -> list[dict[str, Any]]:
    """Obtiene todos los hechos de memoria no expirados."""
    db = _get_db()
    table = db.table("memory_facts")
    now = _now()
    facts = []
    for doc in table.all():
        expires_at = doc.get("expires_at")
        if expires_at and expires_at < now:
            continue
        facts.append(_serializable(doc))
    return facts


def get_facts_by_category(category: str) -> list[dict[str, Any]]:
    """Obtiene hechos de una categoría específica."""
    return [f for f in get_all_facts() if f.get("category") == category]


def delete_fact(fact_id: int) -> bool:
    """Elimina un hecho por su ID."""
    db = _get_db()
    table = db.table("memory_facts")
    doc = table.get(doc_id=fact_id)
    if doc is None:
        return False
    table.remove(doc_ids=[fact_id])
    logger.info("Hecho eliminado de memoria (id=%s).", fact_id)
    return True


def count_facts() -> int:
    """Número actual de hechos en memoria (no expirados)."""
    return len(get_all_facts())


# ═══════════════════════════════════════════════════════════════════════
#  Auditoría
# ═══════════════════════════════════════════════════════════════════════


def _record_audit(
    event_type: str,
    run_id: str,
    proposal: dict[str, Any] | None = None,
    user_message: str | None = None,
    classification: str | None = None,
    detail: str | None = None,
) -> dict[str, Any]:
    """Registra un evento de auditoría.

    Args:
        event_type: Tipo de evento ('proposed', 'approved', 'rejected', etc.).
        run_id: run_id asociado.
        proposal: La propuesta en el momento del evento (opcional).
        user_message: Mensaje del usuario (opcional).
        classification: Clasificación de intención (opcional).
        detail: Detalle adicional (opcional).

    Returns:
        La entrada de auditoría creada.
    """
    db = _get_db()
    table = db.table("audit_log")
    entry = {
        "event_type": event_type,
        "run_id": run_id,
        "proposal": proposal,
        "user_message": user_message,
        "classification": classification,
        "detail": detail,
        "timestamp": _now(),
    }
    doc_id = table.insert(entry)
    entry["id"] = doc_id
    logger.info("Auditoría [%s]: %s", event_type, detail or "")
    return _serializable(entry)


def get_audit_log(
    limit: int = 50,
    event_type: str | None = None,
) -> list[dict[str, Any]]:
    """Obtiene entradas del registro de auditoría.

    Args:
        limit: Número máximo de entradas.
        event_type: Filtrar por tipo de evento (opcional).

    Returns:
        Lista de entradas de auditoría, ordenadas por timestamp descendente.
    """
    db = _get_db()
    table = db.table("audit_log")
    all_entries = table.all()
    # Orden descendente por timestamp
    sorted_entries = sorted(
        all_entries,
        key=lambda e: e.get("timestamp", ""),
        reverse=True,
    )
    result = [_serializable(e) for e in sorted_entries]
    if event_type:
        result = [e for e in result if e.get("event_type") == event_type]
    return result[:limit]


# ═══════════════════════════════════════════════════════════════════════
#  Consolidación
# ═══════════════════════════════════════════════════════════════════════

# Límite máximo de hechos antes de forzar consolidación
_MAX_FACTS_BEFORE_CONSOLIDATION = 50

# TTL por defecto para hechos (90 días)
_DEFAULT_TTL_DAYS = 90


def consolidate_memory(force: bool = False) -> list[dict[str, Any]]:
    """Consolida la memoria si es necesario.

    Estrategia:
      1. Si hay más de ``_MAX_FACTS_BEFORE_CONSOLIDATION`` hechos, se consolida.
      2. Se agrupan hechos similares por categoría.
      3. Se genera un resumen para cada grupo.
      4. Los hechos originales se marcan como consolidated y se les asigna expires.
      5. El resumen consolidado se agrega como nuevo hecho con consolidated=True.
      6. Cada consolidación se registra en auditoría.

    La consolidación re-valida PHI antes de escribir.

    Args:
        force: Si True, consolida aunque no se haya superado el límite.

    Returns:
        Lista de hechos consolidados creados (vacía si no hubo consolidación).
    """
    facts = get_all_facts()
    if not force and len(facts) < _MAX_FACTS_BEFORE_CONSOLIDATION:
        logger.info(
            "Consolidación no necesaria: %s hechos (límite: %s)",
            len(facts), _MAX_FACTS_BEFORE_CONSOLIDATION,
        )
        return []

    # Agrupar por categoría
    by_category: dict[str, list[dict[str, Any]]] = {}
    for fact in facts:
        cat = fact.get("category", "other")
        by_category.setdefault(cat, []).append(fact)

    created: list[dict[str, Any]] = []
    now = _now()

    for category, cat_facts in by_category.items():
        if len(cat_facts) < 3 and not force:
            # Poca densidad → no consolidar aún
            continue

        # Construir resumen
        contents = [f.get("content", "") for f in cat_facts]
        summary = _build_summary(category, contents)

        if not summary:
            continue

        # Re-validar PHI en el resumen
        phi_safe, phi_matches = validate_phi_safe(summary)
        if not phi_safe:
            logger.warning(
                "Consolidación omitida para categoría '%s': el resumen contiene PHI: %s",
                category, phi_matches,
            )
            _record_audit(
                event_type="consolidated",
                run_id="_consolidation_",
                proposal=None,
                detail=f"Consolidación omitida para '{category}': resumen contenía PHI",
            )
            continue

        # Crear hecho consolidado
        consolidated_fact = add_fact(
            content=summary,
            category=category,
            source_run_id="_consolidation_",
            approved_at=now,
            consolidated=True,
            expires_at=None,  # Los consolidados no expiran por TTL automático
        )
        created.append(consolidated_fact)

        # Marcar originales como consolidated y ponerles TTL
        db = _get_db()
        table = db.table("memory_facts")
        import datetime as dt
        expires_dt = datetime.now(timezone.utc) + dt.timedelta(days=_DEFAULT_TTL_DAYS)
        expires_str = expires_dt.isoformat()

        for fact in cat_facts:
            fact_id = fact.get("id")
            if fact_id is not None:
                table.update(
                    {"consolidated": True, "expires_at": expires_str},
                    doc_ids=[fact_id],
                )

        _record_audit(
            event_type="consolidated",
            run_id="_consolidation_",
            proposal=None,
            detail=(
                f"Consolidados {len(cat_facts)} hechos de '{category}' "
                f"en 1 resumen. Originales expiran en {_DEFAULT_TTL_DAYS} días."
            ),
        )

    expired_count = _purge_expired()
    if expired_count:
        logger.info("%s hechos expirados eliminados durante consolidación.", expired_count)

    return created


def _build_summary(category: str, contents: list[str]) -> str:
    """Construye un resumen legible de hechos similares.

    Para este hito, usa una aproximación determinista basada en
    categorización. En producción se usaría un LLM con validación PHI.
    """
    if not contents:
        return ""

    if category == "operational":
        # Resumen operacional: listar patrones
        unique = list(set(contents))
        if len(unique) <= 2:
            return " | ".join(unique)
        return (
            f"Hechos operativos ({len(unique)} registros): "
            + " | ".join(unique[:3])
            + (f" y {len(unique)-3} más." if len(unique) > 3 else "")
        )
    elif category == "incident_pattern":
        unique = list(set(contents))
        return (
            f"Patrones de incidentes ({len(unique)} registros): "
            + " | ".join(unique[:3])
            + (f" y {len(unique)-3} más." if len(unique) > 3 else "")
        )
    elif category == "staff_preference":
        unique = list(set(contents))
        return (
            f"Preferencias de staff ({len(unique)} registros): "
            + " | ".join(unique[:3])
            + (f" y {len(unique)-3} más." if len(unique) > 3 else "")
        )
    else:
        unique = list(set(contents))
        return " | ".join(unique[:5])


def _purge_expired() -> int:
    """Elimina hechos expirados de la BD.

    Returns:
        Número de hechos eliminados.
    """
    db = _get_db()
    table = db.table("memory_facts")
    now = _now()
    to_remove = []
    for doc in table.all():
        expires_at = doc.get("expires_at")
        if expires_at and expires_at < now:
            to_remove.append(doc.doc_id)

    if to_remove:
        table.remove(doc_ids=to_remove)
    return len(to_remove)


def run_maintenance() -> dict[str, int]:
    """Ejecuta tareas de mantenimiento: purge + consolidación.

    Returns:
        Dict con estadísticas de la operación.
    """
    expired = _purge_expired()
    consolidated = consolidate_memory()
    return {
        "expired_purged": expired,
        "consolidated_created": len(consolidated),
        "total_facts": count_facts(),
    }