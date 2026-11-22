"""
data/pipelines/rag.py — Retrieval & generation pipeline.

Responsibilities:
  retrieve()        — Given a question string, embed it, search Qdrant, and
                      return top-k relevant chunk payloads.
  generate_answer() — Build prompt from chunks + question and call the
                      generation LLM.
  query()           — End-to-end: retrieve context, generate final answer via
                      LLM.  Returns a plain string.

These three functions are separated so:
  - retrieve() and generate_answer() can be used independently by future
    agents (e.g. a LangGraph agent that needs to reuse both without
    re-embedding or re-wrapping the monolithic query).
  - The team can swap out the retriever (e.g., replace Qdrant with Pinecone)
    or the generator (e.g., swap DeepSeek for GPT) independently.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────
GENERATION_MODEL: str = os.getenv(
    "GENERATION_MODEL",
    "madrid-spain/openrouter/deepseek/deepseek-v4-flash",
)
LLM_API_KEY: str | None = os.getenv("API_KEY")
LLM_HOST: str | None = os.getenv("HOST")

# Qdrant collection name (must match data/process/rag.py).
QDRANT_COLLECTION: str = "healthcore_knowledge"

# Retrieval defaults.
DEFAULT_K: int = 5
DEFAULT_MIN_SCORE: float = 0.3

# ── OpenAI client (dedicated for generation — NEVER used for embeddings) ──
_gen_client: OpenAI | None = None


def _get_gen_client() -> OpenAI:
    """Lazy-init the OpenAI client pointing at the LLM generation endpoint."""
    global _gen_client
    if _gen_client is None:
        if not LLM_API_KEY or not LLM_HOST:
            raise RuntimeError(
                "API_KEY / HOST not set in .env. "
                "Cannot initialise generation client."
            )
        _gen_client = OpenAI(
            api_key=LLM_API_KEY,
            base_url=LLM_HOST.rstrip("/") + "/v1",
        )
    return _gen_client


# ── Qdrant client ────────────────────────────────────────────────────
_qdrant_client: Any | None = None


def _get_qdrant_client() -> Any:
    """Lazy-init the Qdrant client."""
    global _qdrant_client
    if _qdrant_client is None:
        from qdrant_client import QdrantClient
        from urllib.parse import urlparse

        qdrant_url = os.getenv("QDRANT_URL", "http://localhost:6333")
        parsed = urlparse(qdrant_url)
        _qdrant_client = QdrantClient(
            host=parsed.hostname or "localhost",
            port=parsed.port or 6333,
        )
    return _qdrant_client


# ── retrieve() — embed + vector search ───────────────────────────────


def retrieve(
    query: str,
    *,
    k: int = DEFAULT_K,
    min_score: float = DEFAULT_MIN_SCORE,
) -> list[dict[str, Any]]:
    """Embed *query*, then search Qdrant for the *k* most similar chunks.

    Points below *min_score* cosine similarity are excluded.  Returns
    plain payload dicts (not raw Qdrant SDK objects).

    Args:
        query: Natural language question (embedded internally).
        k: Number of nearest neighbours to retrieve.
        min_score: Minimum cosine similarity threshold; results below
            this are discarded.  Set to 0.0 to disable.

    Returns:
        A list of payload dicts, each containing at minimum:
        ``source_document``, ``section``, ``text``, ``chunk_index``,
        ``language``, ``company``, and ``_score``.

        Ordered by descending similarity.  Empty list if no results
        meet the threshold.

    Raises:
        RuntimeError: If Qdrant or the embedding API is unreachable.
    """
    from data.process.rag import embed

    vector = embed(query)
    client = _get_qdrant_client()
    try:
        resp = client.query_points(
            collection_name=QDRANT_COLLECTION,
            query=vector,
            limit=k,
            score_threshold=min_score if min_score > 0.0 else None,
            with_payload=True,
        )
    except Exception as exc:
        raise RuntimeError(f"Qdrant search failed: {exc}") from exc

    chunks: list[dict[str, Any]] = []
    for hit in resp.points:
        payload = hit.payload or {}
        payload["_score"] = round(hit.score, 4)
        chunks.append(payload)

    logger.info("Retrieved %d chunks (k=%d, min_score=%.2f).", len(chunks), k, min_score)
    return chunks


# ── Generation ───────────────────────────────────────────────────────

SYSTEM_PROMPT: str = (
    "Eres un asistente experto de HealthCore, la red de clínicas ambulatorias. "
    "Responde la pregunta del coordinador usando ÚNICAMENTE la información "
    "de los fragmentos proporcionados a continuación.\n\n"
    "Si los fragmentos no contienen la información necesaria, responde:\n"
    '"No tengo información suficiente en la base de conocimiento para responder esta pregunta."\n\n'
    "Nunca inventes coberturas de seguro, tarifas, ni políticas que no aparezcan "
    "en los fragmentos. Si la pregunta es sobre cobertura de un seguro no listado, "
    "indica que debe verificarse con el equipo de facturación (Tom Callahan).\n\n"
    "=== Fragmentos recuperados ===\n{context}\n\n"
    "=== Pregunta ===\n{question}\n\n"
    "=== Respuesta ==="
)

NO_INFO_MESSAGE: str = (
    "No tengo información suficiente en la base de conocimiento "
    "para responder esta pregunta."
)

NO_CHUNKS_MESSAGE: str = (
    "No encontré información relevante en la base de conocimiento "
    "para responder tu pregunta. Intenta reformularla o contacta a "
    "tu coordinador de pacientes."
)


def _build_context(chunks: list[dict[str, Any]]) -> str:
    """Format retrieved chunks into a single context string for the prompt."""
    parts: list[str] = []
    for i, chunk in enumerate(chunks, 1):
        parts.append(
            f"[Fuente {i}: {chunk.get('source_document', 'desconocida')} "
            f"— {chunk.get('section', 'general')}]\n"
            f"{chunk.get('text', '')}"
        )
    return "\n\n".join(parts)


def _generate(
    question: str,
    context: str,
    system_prompt: str | None = None,
) -> str:
    """Call the generation LLM with a prompt built from context + question.

    Uses the **generation** model only — never the embedding model.
    """
    client = _get_gen_client()
    prompt = (system_prompt or SYSTEM_PROMPT).format(
        context=context,
        question=question,
    )

    try:
        resp = client.chat.completions.create(
            model=GENERATION_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=2000,
        )
    except Exception as exc:
        raise RuntimeError(f"Generation API call failed: {exc}") from exc

    message = resp.choices[0].message
    # Some reasoning models (e.g. DeepSeek v4 Flash) may return content as None
    # when all available tokens are consumed by reasoning.  In that case, try to
    # fall back to the last sentence of the reasoning content, or return the
    # standard no-info message.
    content = message.content

    if content is None:
        logger.warning("Generation returned content=None (reasoning consumed all tokens).")
        reasoning = getattr(message, "reasoning_content", None)
        if reasoning:
            # Extract last sentence as a fallback answer
            sentences = reasoning.strip().split(". ")
            if len(sentences) > 1:
                fallback = sentences[-1].rstrip(".")
                if fallback:
                    return fallback.strip()
        return NO_INFO_MESSAGE

    if not content.strip():
        logger.warning("Generation returned empty content.")
        return NO_INFO_MESSAGE

    return content.strip()


# ── generate_answer() — public, separate generation step ─────────────


def generate_answer(question: str, context: list[dict[str, Any]]) -> str:
    """Generate a final answer from *question* and retrieved *context* chunks.

    This is a **public** function so future agents (e.g. LangGraph) can call
    retrieve() and generate_answer() as independent steps without re-embedding
    or re-wrapping a monolithic query().

    Args:
        question: The original user question.
        context: List of chunk dicts returned by retrieve().

    Returns:
        The generated answer as a plain string.
    """
    if not context:
        logger.info("generate_answer called with empty context.")
        return NO_CHUNKS_MESSAGE

    context_str = _build_context(context)
    return _generate(
        question=question,
        context=context_str,
    )


# ── query() — end-to-end orchestrator ────────────────────────────────

# NOTE: This is the ONLY function that external consumers (endpoints, agents)
# should call.  It composes retrieve() + generate_answer().


def query(
    question: str,
    *,
    k: int = DEFAULT_K,
    min_score: float = DEFAULT_MIN_SCORE,
) -> str:
    """Answer a *question* using the RAG pipeline.

    This is the **only** function that external consumers should call.
    It composes ``retrieve()`` + ``generate_answer()``, ensuring the
    orchestration logic lives in one place.

    Steps:
        1. Retrieve relevant chunks from Qdrant (embeds internally).
        2. Generate a final answer via LLM using the chunks as context.

    The response **never** returns raw chunks to the user — it always goes
    through the LLM.

    Args:
        question: Natural language question from a coordinator.
        k: Number of chunks to retrieve.
        min_score: Minimum similarity threshold (cosine).

    Returns:
        The generated answer as a plain string.
    """
    logger.info("Query: '%s'", question[:100])

    chunks = retrieve(question, k=k, min_score=min_score)
    return generate_answer(question=question, context=chunks)


# ── generate_answer_with_memory() — structured output with memory proposal ──

_MEMORY_SYSTEM_PROMPT_SUFFIX: str = (
    "\n\n"
    "=== Instrucción adicional: auto-evaluación de memoria ===\n"
    "Además de responder la pregunta, evalúa si la interacción contiene "
    "información operativa que valga la pena recordar para futuras consultas.\n\n"
    "Al FINAL de tu respuesta (después de la respuesta al usuario), "
    "añade una línea exactamente con este formato:\n"
    "---MEMORY_PROPOSAL---\n"
    "Sigue uno de estos dos formatos JSON:\n\n"
    "Si HAY algo memorable:\n"
    '{"proposal": "texto del hecho a recordar", '
    '"category": "operational|incident_pattern|staff_preference", '
    '"reason": "por qué es útil recordarlo"}\n\n'
    "Si NO hay nada memorable:\n"
    "null\n\n"
    "Reglas para decidir qué es memorable:\n"
    "1. SÍ memorable: cambios operativos, nuevos protocolos, excepciones por clínica, "
    "patrones de incidentes conocidos, preferencias de staff sobre presentación.\n"
    "2. NO memorable: consultas puntuales de datos, preguntas hipotéticas, "
    "cierres de conversación (gracias, saludos), datos de pacientes (PHI).\n"
    "3. NUNCA memorable: información de salud protegida (PHI) como nombres de pacientes, "
    "diagnósticos, identificadores clínicos, números de seguro, o cualquier dato "
    "que permita re-identificar a un paciente.\n\n"
    "Ejemplos de SÍ memorable:\n"
    '- "En la clínica de Manchester el proceso de referidos cambió"\n'
    '- "La alerta de no-show en Austin fue por cierre de carretera, no por falla del sistema"\n'
    '- "El reporte semanal para Diane debe incluir vacantes por rol"\n\n'
    "Ejemplos de NO memorable:\n"
    '- "¿Cuál es la tasa de no-show?" (consulta puntual)\n'
    '- "Gracias, con eso resuelvo mi reporte" (cierre)\n'
    '- "El paciente Johnson canceló su cita" (contiene PHI)\n'
    '- "Si llueve mañana" (hipotético/condicional)\n\n'
    "IMPORTANTE: Si no estás seguro, responde null (nunca asumas que algo es memorable)."
)


def generate_answer_with_memory(
    question: str,
    context: list[dict[str, Any]],
    memory_facts: list[dict[str, Any]] | None = None,
) -> tuple[str, dict[str, Any] | None]:
    """Generate answer AND auto-evaluate memory in a single LLM call.

    This is the **structured output** version of ``generate_answer()``.
    It returns both the answer the user sees and an optional memory
    proposal — all from the same model call (no second call needed).

    Args:
        question: The original user question.
        context: List of chunk dicts returned by retrieve().
        memory_facts: Current memory facts (for context).
                      Passing them lets the LLM know what's already
                      remembered and avoid duplicates.

    Returns:
        Tuple (answer, memory_proposal_or_None).
        memory_proposal is a dict with keys: proposal, category, reason.
        None means nothing worth remembering.
    """
    if not context:
        logger.info("generate_answer_with_memory called with empty context.")
        return generate_answer(question, context), None

    context_str = _build_context(context)
    base_prompt = SYSTEM_PROMPT.format(context=context_str, question=question)

    # Add memory facts context if available
    memory_context = ""
    if memory_facts:
        lines = ["Hechos ya recordados en memoria:"]
        for f in memory_facts:
            lines.append(f"- {f.get('content', '')}")
        memory_context = "\n".join(lines) + "\n\n"

    prompt = memory_context + base_prompt + _MEMORY_SYSTEM_PROMPT_SUFFIX

    client = _get_gen_client()
    try:
        resp = client.chat.completions.create(
            model=GENERATION_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=2500,
        )
    except Exception as exc:
        raise RuntimeError(f"Generation+memory API call failed: {exc}") from exc

    message = resp.choices[0].message
    content = message.content

    if content is None:
        logger.warning("Generation+memory returned content=None.")
        reasoning = getattr(message, "reasoning_content", None)
        if reasoning:
            sentences = reasoning.strip().split(". ")
            if len(sentences) > 1:
                fallback = sentences[-1].rstrip(".")
                if fallback:
                    return fallback.strip(), None
        return NO_INFO_MESSAGE, None

    if not content.strip():
        logger.warning("Generation+memory returned empty content.")
        return NO_INFO_MESSAGE, None

    # Parse the ---MEMORY_PROPOSAL--- marker
    marker = "---MEMORY_PROPOSAL---"
    if marker in content:
        parts = content.split(marker, 1)
        answer = parts[0].strip()
        proposal_raw = parts[1].strip()

        import json
        try:
            proposal_data = json.loads(proposal_raw)
            if proposal_data is None:
                return answer, None
            if isinstance(proposal_data, dict) and "proposal" in proposal_data:
                # Validate category
                valid_cats = {"operational", "incident_pattern", "staff_preference"}
                cat = proposal_data.get("category", "operational")
                if cat not in valid_cats:
                    cat = "operational"
                return answer, {
                    "proposal": proposal_data["proposal"],
                    "category": cat,
                    "reason": proposal_data.get("reason", "Evaluado por el modelo."),
                }
        except (json.JSONDecodeError, TypeError):
            logger.warning("Failed to parse memory proposal JSON: %s", proposal_raw[:100])

        # Fallback: if marker present but unparseable, return answer without proposal
        return answer, None

    # No marker → no proposal
    return content.strip(), None
