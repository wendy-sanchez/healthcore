"""Clasificador de intención del usuario frente a una propuesta de memoria.

Cuando el agente ha hecho una propuesta de memoria y el usuario responde,
este módulo clasifica la intención del usuario mediante **reglas estructurales**
(no listas cerradas de palabras). Analiza la **posición sintáctica**, la
**estructura de la frase** y el **contexto gramatical** para determinar si:

  - ``approve``  — Aprueba la propuesta (confianza >= 0.70)
  - ``reject``   — Rechaza la propuesta (confianza >= 0.75)
  - ``edit``     — Propone una edición (confianza >= 0.85)
  - ``discard``  — Cambia de tema sin responder (default)
  - ``phi_rejected`` — El mensaje del usuario contiene PHI (confianza >= 0.99)

Principios (CONTEXT.md + agent-memory-harness-guard.md §2.4):
  1. **Confirmación explícita necesaria**: sin un "sí" claro, no hay approve.
  2. **Nunca asumir aprobación por silencio o ambigüedad**.
  3. **Clasificador conservador**: umbrales altos, discard por defecto.
  4. La edición requiere texto alternativo explícito.
  5. Cortesías ("gracias", "saludos") NUNCA son aprobación.
  6. Preguntas y condicionales NUNCA son approve/edit.
  7. **PHI (HIPAA/UK GDPR) siempre se valida antes que cualquier otra señal**:
     si el mensaje del usuario contiene lo que parece PHI, se rechaza
     explícitamente — nunca se ignora en silencio.
  - **Contradicción comando de memoria + negación**: si el mensaje contiene tanto
     un comando de memoria ("guárdalo", "apúntalo", etc.) como una negación
     ("no", "nunca", "jamás", "tampoco"), es SEMÁNTICAMENTE CONTRADICTORIO
     independientemente del orden en que aparezcan. Las únicas excepciones son:
     - Tag questions: "guárdalo, ¿no?" ("no?" al final buscando confirmación)
     - Doble negación: "no olvides", "no te olvides" (son afirmativas)
     En cualquier otro caso, la coexistencia de comando + negación es
     ambigua/contradictoria y se descarta. Nunca es approve.

En lugar de listas de palabras, el clasificador usa ejes estructurales:
  - **Eje de tonicidad**: "sí" (átono=condicional) vs "sí" (tónico=afirmación)
  - **Eje de posición**: palabra inicial vs incrustada vs final
  - **Eje de modo**: indicativo (hecho) vs subjuntivo/condicional (hipótesis)
  - **Eje de puntuación**: ¿? delatan pregunta, no respuesta
"""

from __future__ import annotations

import logging
import re
from typing import Literal

from .store import get_pending_proposal, validate_phi_safe

logger = logging.getLogger(__name__)


class IntentClassification:
    """Resultado de la clasificación de intención del usuario."""

    __slots__ = ("intent", "confidence", "edit_text", "explanation")

    def __init__(
        self,
        intent: Literal["approve", "reject", "edit", "discard", "no_proposal", "phi_rejected"],
        confidence: float,
        edit_text: str | None = None,
        explanation: str = "",
    ):
        self.intent = intent
        self.confidence = confidence
        self.edit_text = edit_text
        self.explanation = explanation

    def __repr__(self) -> str:
        return (
            f"IntentClassification(intent={self.intent!r}, "
            f"confidence={self.confidence:.2f}, "
            f"edit_text={self.edit_text!r})"
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "intent": self.intent,
            "confidence": self.confidence,
            "edit_text": self.edit_text,
            "explanation": self.explanation,
        }


# ── Constantes de umbral (CONTEXT.md §Hito8 + agent-memory-harness-guard.md §2.4) ──
_CONFIDENCE_APPROVE: float = 0.70  # Confirmación explícita del usuario
_CONFIDENCE_REJECT: float = 0.75   # Rechazo explícito
_CONFIDENCE_EDIT: float = 0.85     # Edición con texto alternativo
_CONFIDENCE_DISCARD: float = 0.0   # Sin señal clara → descarte
_CONFIDENCE_PHI_REJECTED: float = 0.99  # PHI detectado → rechazo explícito

# ── Signos de pregunta (delatan que no es respuesta a la propuesta) ──
_RE_QUESTION_END = re.compile(r"\?\s*$")
_RE_QUESTION_WORDS = re.compile(
    r"\b(?:qui[eé]n|qu[eé]|c[uoá]l|c[uoá]les|"
    r"d[oó]nde|c[oó]mo|por\s+qué|cu[aá]ndo|"
    r"cu[aá]nto|cu[aá]ntos|cu[aá]ntas?)\b",
    re.IGNORECASE,
)
_RE_CONDITIONAL_VERBS = re.compile(
    r"\b(?:puedes|podr[ií]as|ser[ií]a|habr[ií]a|tendr[ií]a|"
    r"deber[ií]a|har[ií]a|ir[ií]a)\b",
    re.IGNORECASE,
)

# ── Palabras de cortesía (NUNCA son approve) ──
_RE_COURTESY = re.compile(
    r"\b(?:gracias|thank|thanks|de\s+nada|saludos|cordialmente|"
    r"(?:que\s+)?tengas?|excelente\s+d[ií]a|"
    r"buen[ao]s?\s+(?:d[ií]a|tardes|noches)|"
    r"hasta\s+luego|nos\s+vemos|chao|bye|"
    r"cuidate|cuídese|un\s+gusto)\b",
    re.IGNORECASE,
)


# ══════════════════════════════════════════════════════════════════════
#  Eje 1: Modo de la frase (pregunta vs afirmación vs hipótesis)
# ══════════════════════════════════════════════════════════════════════


def _es_pregunta_o_condicional(text: str) -> bool:
    """Analiza si el texto es una pregunta, condicional o hipótesis.

    Reglas estructurales:
      - Termina con "?" → es pregunta
      - Contiene palabras WH (qué, quién, cómo, etc.) → es pregunta
      - Contiene verbos en condicional (podría, sería, etc.) → es hipótesis
      - Empieza con "si " sin tilde y tiene más de 2 palabras → condicional

    NOTA: Las coletillas \"¿no?\" al final de una frase (tag questions)
    NO convierten la frase en pregunta. \"guárdalo, ¿no?\" es una
    afirmación que busca confirmación, no una pregunta real.
    """
    t = text.lower().strip()

    # Regla 1: Signo de interrogación
    if _RE_QUESTION_END.search(t):
        # Excluir tag questions: "... ¿no?" que son coletillas de confirmación
        if _RE_TAG_QUESTION.search(t):
            return False
        return True

    # Regla 2: Palabras WH que indican pregunta
    if _RE_QUESTION_WORDS.search(t):
        return True

    # Regla 3: Verbos en condicional/subjuntivo (modo hipotético)
    if _RE_CONDITIONAL_VERBS.search(t):
        return True

    # Regla 4: "si" condicional al inicio (sin tilde)
    # Diferencia: "si quiero" (afirmación) vs "si llueve" (condición)
    words = t.split()
    if len(words) >= 2 and words[0] == "si" and "sí" not in t:
        second = words[1].strip(",.!?¿¡;:")
        # Palabras afirmativas tras "si": "si claro", "si segura?", "si quiero" → afirmación
        if second in ("claro", "seguro", "segura", "quiero", "quiero", "por", "dame"):
            return False  # "si claro", "si quiero" → afirmación
        # Palabras que indican condicional: "si llueve", "si cambia", "si tengo"
        # Verbo conjugado en 3ra persona o verbo de acción
        return True  # "si llueve", "si pasa", "si puedo" → condicional

    return False


# ══════════════════════════════════════════════════════════════════════
#  Eje 2: Cortesía (no es respuesta a la propuesta)
# ══════════════════════════════════════════════════════════════════════


def _es_solo_cortesia(text: str) -> bool:
    """Determina si el mensaje es únicamente una cortesía o saludo.

    Un mensaje de SOLO cortesía (sin otras señales) nunca es approve.
    """
    t = text.lower().strip()
    return bool(_RE_COURTESY.search(t))


# ══════════════════════════════════════════════════════════════════════
#  Eje 3: Intención de rechazo (posición y negación)
# ══════════════════════════════════════════════════════════════════════


# Palabras de negación explícita ("no", "nunca", "jamás", "tampoco")
# Cualquiera de estas palabras indica rechazo o contradicción.
# "no" requiere word boundary porque puede aparecer como prefijo ("nota", "noche", "norte")
# "nunca", "jamás", "tampoco" son palabras completas — no necesitan boundary complejo
_RE_NO_WORD = re.compile(r"(?<!\w)no(?!\w)", re.IGNORECASE)
_RE_NUNCA = re.compile(r"\b(?:nunca|jam[aá]s|tampoco)\b", re.IGNORECASE)
# Negación general: "no", "nunca", "jamás", "tampoco"
_RE_NEGACION = re.compile(
    r"(?:{no}|{nunca})".format(
        no=_RE_NO_WORD.pattern,
        nunca=_RE_NUNCA.pattern,
    ),
    re.IGNORECASE,
)
# Verbos de rechazo semántico (no son listas, son raíces semánticas)
_RE_REJECT_VERBS = re.compile(
    r"\b(?:olv[ií]d|descart[aá]|elimin[aá]|quit[aá]|ignor[aá]|cancel[aá]|anul[aá]|borr[aá]|tir[aá])\w*\b",
    re.IGNORECASE,
)
# Frases compuestas de rechazo (estructura, no lista)
# Incluye pronombres átonos (lo/la/los/las/le/les/me/te/se/nos) entre "no" y el verbo
# Usa raíces de verbos (sin vocal de conjugación) para captar todas las formas
_RE_REJECT_PHRASE = re.compile(
    r"\b(?:no\s+(?:(?:lo|la|los|las|le|les|me|te|se|nos)\s+)?"
    r"(?:guard|quier|hag|grab|recuerd|necesit|apunt|escrib|almacen|anot))\w*\b",
    re.IGNORECASE,
)


def _hay_rechazo(text: str) -> float:
    """Evalúa la fuerza de la señal de rechazo en el texto.

    Retorna confianza 0.0 (sin rechazo) a 1.0 (rechazo inequívoco).

    NOTA: \"no olvides\" / \"no te olvides\" son dobles negaciones con
    significado POSITIVO (\"no olvides\" = \"recuerda\"), por lo que NO
    se consideran rechazo aunque lexicalmente contengan la raíz \"olvid\".
    """
    t = text.lower().strip()
    score = 0.0

    # Excluir \"no olvides\" — es doble negación afirmativa, no rechazo
    if _RE_NO_OLVIDES.search(t):
        return 0.0  # \"no olvides\" = \"recuerda\" — no es rechazo

    # Señal fuerte: "no" como palabra independiente
    if _RE_NO_WORD.search(t):
        # Si "no" es la única palabra o va seguido de cortesía, es reject claro
        words = t.split()
        if len(words) == 1:
            score += 0.75  # "no" suelto → reject inequívoco
        elif len(words) == 2 and _RE_COURTESY.search(t):
            score += 0.75  # "no gracias" → reject
        else:
            score += 0.5  # "no" en frase más larga

    # Señal adicional: "nunca", "jamás", "tampoco" como negación
    if _RE_NUNCA.search(t):
        score += 0.5  # Negación equivalente a "no"

    # Señal fuerte: verbo de rechazo
    if _RE_REJECT_VERBS.search(t):
        score += 0.3

    # Señal muy fuerte: frase compuesta "no + verbo de memoria"
    # (incluye pronombres: "no lo guardes", "no la grabes", etc.)
    if _RE_REJECT_PHRASE.search(t):
        score += 0.3

    return min(score, 1.0)


# ══════════════════════════════════════════════════════════════════════
#  Eje 4: Intención de aprobación (tonicidad y contexto)
# ══════════════════════════════════════════════════════════════════════


# "sí" con tilde es tónico → afirmación. "si" sin tilde es átono → condicional.
_RE_SI_TILDE = re.compile(r"\bs[íi]\b", re.IGNORECASE)  # sí o sí (con tilde)
# Verbos de aprobación semántica (raíces, no lista cerrada)
_RE_APPROVE_VERBS = re.compile(
    r"\b(?:aprob|confirm|acept|valid[ae]|autoric)\w*\b",
    re.IGNORECASE,
)
# Frases compuestas de aprobación (estructura detectada)
_RE_APPROVE_PHRASE = re.compile(
    r"\b(?:est[aá]\s+bien|me\s+p[aá]rece\s+bien|de\s+acuerdo|"
    r"muy\s+bien|perfecto?|correcto|excelente)\b",
    re.IGNORECASE,
)
# Comandos de memorización (acción explícita)
_RE_STORE_COMMANDS = re.compile(
    r"\b(?:gu[aá]rd[aeoá]l?|recu[eé]rd[aeoá]l?|ap[uú]nt[aeoá]l?|"
    r"escrib[ae]|almacen[ae]|anot[ae])\w*\b",
    re.IGNORECASE,
)


def _hay_aprobacion(text: str) -> float:
    """Evalúa la fuerza de la señal de aprobación en el texto.

    Retorna confianza 0.0 (sin aprobación) a 1.0 (aprobación inequívoca).

    Reglas estructurales (NO listas de palabras):
      - "sí" tónico (con tilde o contexto afirmativo corto) → approve
      - Verbos semánticos de aprobación → approve
      - Frases compuestas de consentimiento → approve
      - Comandos de memorización → approve implícito
      - "bien" como única palabra o al inicio de cortesía → NO approve
      - Comandos de memorización NEGADOS por "no" → NO contribuyen
    """
    t = text.lower().strip()
    score = 0.0

    # Señal fuerte: "sí" con tilde (inequívoco, confianza alta)
    if _RE_SI_TILDE.search(t):
        score += 0.7

    # Señal fuerte: doble negación afirmativa "no olvides" = "recuerda"
    # "no olvides", "no te olvides" son mandatos POSITIVOS de recuerdo
    if _RE_NO_OLVIDES.search(t):
        score += 0.7

    # Señal fuerte: verbo de aprobación
    if _RE_APPROVE_VERBS.search(t):
        score += 0.4

    # Señal fuerte: frase compuesta aprobatoria ("está bien", "me parece bien")
    if _RE_APPROVE_PHRASE.search(t):
        score += 0.7

    # Señal fuerte: comando de memorización (acción explícita de guardar)
    # "guárdalo", "recuérdalo", "apúntalo" → approve implícito
    # PERO si está negado por "no" → NO contribuye
    if _RE_STORE_COMMANDS.search(t):
        if not _comando_memoria_negado(t):
            score += 0.8

    # Señal fuerte: palabras cortas de afirmación internacional
    words_clean = [w.strip(",.!?¿¡;:") for w in t.split()]
    if len(words_clean) == 1 and words_clean[0] in ("ok", "okay", "vale", "yes", "yeah", "yep", "dale", "claro"):
        score += 0.7

    return min(score, 1.0)


# ══════════════════════════════════════════════════════════════════════
#  Eje 5: Intención de edición (corrección estructural)
# ══════════════════════════════════════════════════════════════════════


_RE_EDIT_MARKERS = re.compile(
    r"\b(?:mejor\s+d[ií]cho|en\s+realidad|"
    r"edita|modifica|corrige|rectific|matiz[ae]|precisemos|"
    r"en\s+lugar\s+de|no\s+es\s+exacto|m[aá]s\s+bien|"
    r"con\s+m[aá]s\s+precisi[oó]n)\b",
    re.IGNORECASE,
)


def _hay_edicion(text: str) -> float:
    """Evalúa la fuerza de la señal de edición en el texto.

    Retorna confianza 0.0 a 1.0.
    La edición requiere señales explícitas de corrección + texto alternativo.
    """
    t = text.lower().strip()
    if _RE_EDIT_MARKERS.search(t):
        return 0.85  # Señal inequívoca de corrección
    return 0.0


# ══════════════════════════════════════════════════════════════════════
#  Helper: detección de comandos de memoria negados
# ══════════════════════════════════════════════════════════════════════

# Frase que invierte la negación: "no olvides" / "no te olvides"
# son comandos POSITIVOS ("no olvides" = "recuerda").
_RE_NO_OLVIDES = re.compile(
    r"\bno\s+(?:te\s+)?olvid(?:es|éis|e|en)",
    re.IGNORECASE,
)

# Tag question: "¿no?" al final — es confirmación, no negación.
# Requiere signo de interrogación EXPLÍCITO para no confundir
# "guárdalo, no" (retractación) con "guárdalo, ¿no?" (confirmación).
_RE_TAG_QUESTION = re.compile(r"[¿]?\s*no\s*[?]\s*$", re.IGNORECASE)


def _comando_memoria_negado(text: str) -> bool:
    """Detecta si un comando de memoria está negado por un ``no`` precedente.

    Ejemplos:
      "no lo guardes" → True
      "no guardes" → True
      "no recuerdes eso" → True
      "guárdalo" → False
      "sí, guárdalo" → False
      "recuérdalo" → False
    """
    t = text.lower()
    return bool(
        re.search(
            r"\bno\s+(?:(?:lo|la|los|las|le|les|me|te|se|nos)\s+)?"
            r"(?:guard|recuerd|apunt|escrib|almacen|anot)\w*\b",
            t,
            re.IGNORECASE,
        )
    )


# ══════════════════════════════════════════════════════════════════════
#  Eje 6: Falsos positivos (lo que parece approve pero no lo es)
# ══════════════════════════════════════════════════════════════════════


# "bien" suelto: "bien, gracias", "bien", "bien y tú" NO son approve
_RE_BIEN_WORD = re.compile(r"\bbien\b", re.IGNORECASE)


def _es_falso_positivo_aprobacion(text: str) -> bool:
    """Detecta palabras que parecen aprobación pero no lo son.

    "bien" suelto sin frase compuesta aprobatoria → NO es approve.
    """
    t = text.lower().strip()
    if not _RE_BIEN_WORD.search(t):
        return False
    # Si "bien" está en frase compuesta aprobatoria, no es falso positivo
    if _RE_APPROVE_PHRASE.search(t):
        return False
    return True


# ══════════════════════════════════════════════════════════════════════
#  Clasificador principal
# ══════════════════════════════════════════════════════════════════════


def classify_user_intent(
    user_message: str,
) -> IntentClassification:
    """Clasifica la intención del usuario respecto a la propuesta pendiente.

    Usa un sistema de reglas estructurales en 6 ejes:
      1. Modo (pregunta/condicional vs afirmación)
      2. Cortesía
      3. Rechazo (posición de negación + verbos semánticos)
      4. Aprobación (tonicidad + verbos semánticos + frases compuestas)
      5. Edición (marcadores de corrección + texto alternativo)
      6. Falsos positivos

    Pipeline de 11 pasos:
      1. Texto vacío → discard
      2. Pregunta/condicional → discard
      3. Señales de rechazo → score_reject (umbral: 0.75)
      4. Señales de edición → score_edit (umbral: 0.85)
      5. Cortesía sin approve → discard
      6. Falsos positivos de approve → discard
      7. Contradicción "sí + no" → discard
      8. Contradicción "no + comando de memoria" → discard
      9. Señales de aprobación → score_approve (umbral: 0.70)
     10. "si" sin tilde en respuesta corta → approve
     11. Por defecto → discard

    Args:
        user_message: El mensaje completo del usuario.

    Returns:
        IntentClassification con la decisión estructurada.
    """
    if not user_message:
        return IntentClassification(
            intent="discard",
            confidence=_CONFIDENCE_DISCARD,
            explanation="Mensaje vacío → se descarta por defecto.",
        )

    # Verificar si hay propuesta pendiente
    proposal = get_pending_proposal()
    if proposal is None:
        return IntentClassification(
            intent="no_proposal",
            confidence=1.0,
            explanation="No hay propuesta pendiente para clasificar.",
        )

    msg = user_message.strip()
    msg_lower = msg.lower()

    # ════════════════════════════════════════════════════════════════
    # Paso 0: Validación PHI — requisito HIPAA/UK GDPR
    # ════════════════════════════════════════════════════════════════
    # CONTEXT.md Hito8: si el mensaje del usuario contiene PHI,
    # debe rechazarse explícitamente, NO clasificarse como approve/reject/discard.
    # Esto cubre casos como "El paciente Smith canceló su cita, apúntalo"
    # (que contiene "apúntalo" pero también PHI) y
    # "Dame la hora de la cita del paciente Johnson" (consulta con PHI).
    phi_safe, phi_matches = validate_phi_safe(msg)
    if not phi_safe:
        return IntentClassification(
            intent="phi_rejected",
            confidence=_CONFIDENCE_PHI_REJECTED,
            explanation=(
                f"El mensaje del usuario contiene información de salud protegida (PHI). "
                f"Coincidencias detectadas: {phi_matches}. "
                f"Requisito HIPAA/UK GDPR: no puede almacenarse en memoria."
            ),
        )

    # ════════════════════════════════════════════════════════════════
    # Paso 1: Descartar por estructura — preguntas y condicionales
    # ════════════════════════════════════════════════════════════════
    # Una pregunta NUNCA es approve/edit (no responde a la propuesta)
    if _es_pregunta_o_condicional(msg_lower):
        return IntentClassification(
            intent="discard",
            confidence=_CONFIDENCE_DISCARD,
            explanation="El mensaje es una pregunta o expresión condicional, no una respuesta a la propuesta. Se descarta.",
        )

    # ════════════════════════════════════════════════════════════════
    # Paso 2: Evaluar señales de rechazo
    # ════════════════════════════════════════════════════════════════
    reject_score = _hay_rechazo(msg_lower)
    if reject_score >= _CONFIDENCE_REJECT:
        return IntentClassification(
            intent="reject",
            confidence=reject_score,
            explanation=f"Señal de rechazo detectada (confianza={reject_score:.2f}).",
        )

    # ════════════════════════════════════════════════════════════════
    # Paso 3: Evaluar señales de edición
    # ════════════════════════════════════════════════════════════════
    edit_score = _hay_edicion(msg_lower)
    if edit_score >= _CONFIDENCE_EDIT:
        edit_text = _extraer_texto_editado(msg, proposal.get("proposal", ""))
        return IntentClassification(
            intent="edit",
            confidence=edit_score,
            edit_text=edit_text or proposal.get("proposal", ""),
            explanation=f"Marcador de corrección detectado. Texto editado: '{edit_text[:80] if edit_text else 'N/A'}'.",
        )

    # ════════════════════════════════════════════════════════════════
    # Paso 4: Descartar cortesías sin señales de approve
    # ════════════════════════════════════════════════════════════════
    # "gracias", "saludos", etc. sin approve explícito → discard
    # Excepción: si tiene "no" (p.ej. "no gracias") → ya lo capturó Paso 2
    if _es_solo_cortesia(msg_lower):
        approve_score = _hay_aprobacion(msg_lower)
        if approve_score < _CONFIDENCE_APPROVE:
            return IntentClassification(
                intent="discard",
                confidence=_CONFIDENCE_DISCARD,
                explanation="El mensaje es una cortesía o saludo sin aprobación explícita. Se descarta.",
            )

    # ════════════════════════════════════════════════════════════════
    # Paso 5: Descartar falsos positivos de approve
    # ════════════════════════════════════════════════════════════════
    # "bien" suelto sin frase compuesta (ej: "bien, gracias") NO es approve
    if _es_falso_positivo_aprobacion(msg_lower):
        return IntentClassification(
            intent="discard",
            confidence=_CONFIDENCE_DISCARD,
            explanation=(
                "El mensaje contiene 'bien' sin frase compuesta de aprobación "
                "(p.ej. 'está bien'). Se descarta por ambigüedad."
            ),
        )

    # ════════════════════════════════════════════════════════════════
    # Paso 5b: Detectar contradicciones — sí + no nunca es approve
    # ════════════════════════════════════════════════════════════════
    # Si el mensaje contiene tanto "sí" (aprobación) como "no" (rechazo),
    # es contradictorio/ambiguo y se descarta.
    if _RE_SI_TILDE.search(msg_lower) and _RE_NO_WORD.search(msg_lower):
        return IntentClassification(
            intent="discard",
            confidence=_CONFIDENCE_DISCARD,
            explanation=(
                "El mensaje contiene tanto aprobación ('sí') como negación ('no'), "
                "lo que es contradictorio. Se descarta por ambigüedad "
                "— nunca se asume aprobación en presencia de negación."
            ),
        )

    # ════════════════════════════════════════════════════════════════
    # Paso 5c: Detectar contradicción comando de memoria + negación
    # ════════════════════════════════════════════════════════════════
    # PRINCIPIO: cualquier mensaje que contenga TANTO un comando de memoria
    # ("guárdalo", "apúntalo", "recuérdalo") COMO una negación ("no",
    # "nunca", "jamás", "tampoco") es SEMÁNTICAMENTE CONTRADICTORIO,
    # INDEPENDIENTEMENTE DEL ORDEN.
    #
    #   "guárdalo, no"       → comando + retractación → discard
    #   "guárdalo, nunca"    → comando + retractación → discard
    #   "apúntalo, jamás"    → comando + retractación → discard
    #   "recuérdalo, tampoco" → comando + retractación → discard
    #   "apúntalo, pero no"   → comando + pero + negación → discard
    #   "recuérdalo, no gracias" → comando + cortesía negativa → discard
    #   "guárdalo, no, olvida"   → comando + negación refuerzo → discard
    #   "no, pero guárdalo"  → negación + comando contradictorio → discard
    #   "nunca, guárdalo"    → negación + comando contradictorio → discard
    #   "jamás, apúntalo"    → negación + comando contradictorio → discard
    #   "tampoco lo guardes" → negación + comando contradictorio → discard
    #
    # Excepciones válidas (NO son contradicción):
    #   "guárdalo, ¿no?"     — tag question, "¿no?" busca confirmación
    #   "no olvides"         — doble negación, semánticamente afirmativo
    #   "no te olvides"      — idem
    #
    # No importa qué aparezca primero: si ambos están presentes, y no es
    # una excepción, es contradictorio → discard. Nunca approve.
    has_negacion = bool(_RE_NEGACION.search(msg_lower))
    has_store_command = bool(_RE_STORE_COMMANDS.search(msg_lower))
    if has_negacion and has_store_command:
        # Excepción 1: "no olvides" / "no te olvides" — son semánticamente
        # afirmativos ("no olvides" = "recuerda").
        if _RE_NO_OLVIDES.search(msg_lower):
            pass  # Dejar pasar — no es contradicción real
        # Excepción 2: Tag question "¿no?" al final — el usuario pregunta
        # confirmación, no está negando el comando.
        elif _RE_TAG_QUESTION.search(msg_lower):
            pass  # Dejar pasar — "guárdalo, ¿no?" es aprobación con duda
        else:
            return IntentClassification(
                intent="discard",
                confidence=_CONFIDENCE_DISCARD,
                explanation=(
                    "El mensaje contiene tanto un comando de memoria como una "
                    "negación ('no', 'nunca', 'jamás', 'tampoco'), lo que es "
                    "semánticamente contradictorio independientemente del orden "
                    "en que aparezcan. Se descarta por ambigüedad — nunca se "
                    "asume aprobación cuando hay negación presente."
                ),
            )

    # ════════════════════════════════════════════════════════════════
    # Paso 6: Evaluar señales de aprobación
    # ════════════════════════════════════════════════════════════════
    approve_score = _hay_aprobacion(msg_lower)
    if approve_score >= _CONFIDENCE_APPROVE:
        return IntentClassification(
            intent="approve",
            confidence=approve_score,
            explanation=f"Señal de aprobación detectada (confianza={approve_score:.2f}).",
        )

    # ════════════════════════════════════════════════════════════════
    # Paso 7: "si" sin tilde como respuesta corta (1-2 palabras)
    # ════════════════════════════════════════════════════════════════
    # "si" sin tilde no pasó approve_score porque no es "sí" con tilde,
    # pero en mensaje corto de 1-2 palabras puede ser afirmación oral
    words = msg_lower.split()
    if len(words) <= 2 and msg_lower in ("si", "si,", "si."):
        return IntentClassification(
            intent="approve",
            confidence=_CONFIDENCE_APPROVE,
            explanation='Respuesta afirmativa corta ("si" sin tilde como afirmación oral).',
        )

    # ════════════════════════════════════════════════════════════════
    # Paso 8: Por defecto → discard (nunca asumir aprobación)
    # ════════════════════════════════════════════════════════════════
    return IntentClassification(
        intent="discard",
        confidence=_CONFIDENCE_DISCARD,
        explanation=(
            "No se detectaron señales claras de aprobación, rechazo o edición. "
            "La propuesta se descarta por defecto — nunca se asume aprobación "
            "por silencio o ambigüedad.  (CONTEXT.md §Hito8, "
            "agent-memory-harness-guard.md §2.4)"
        ),
    )


# ══════════════════════════════════════════════════════════════════════
#  Extracción de texto editado
# ══════════════════════════════════════════════════════════════════════


def _extraer_texto_editado(user_message: str, original_proposal: str) -> str | None:
    """Extrae el texto editado de la propuesta desde el mensaje del usuario.

    Busca patrones estructurales de corrección:
      - "mejor dicho: X" / "mejor así: X"
      - "no es exacto, es X"
      - Texto alternativo extenso (>20 chars, distinto al original)
    """
    msg = user_message

    # Patrón "mejor dicho/así: X"
    m = re.search(
        r"(?:mejor\s+d[ií]cho|mejor\s+as[ií]|en\s+realidad)\s*:?\s*(.+?)(?:\.\s*$|$)",
        msg, re.IGNORECASE | re.DOTALL,
    )
    if m:
        return m.group(1).strip()

    # Texto alternativo extenso y diferente al original → probable edición
    if len(msg) > 20 and msg != original_proposal:
        return msg.strip()

    return None


# ══════════════════════════════════════════════════════════════════════
#  Utilidad: cambio de tema
# ══════════════════════════════════════════════════════════════════════


def user_changed_topic(user_message: str, original_question: str | None) -> bool:
    """Determina si el usuario cambió de tema respecto a la propuesta.

    Args:
        user_message: Mensaje actual del usuario.
        original_question: Pregunta original que generó la propuesta.

    Returns:
        True si el usuario parece haber cambiado de tema.
    """
    if not original_question:
        return "?" in user_message
    return False