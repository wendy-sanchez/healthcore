"""Cálculo de métricas de legibilidad para documentos RFP.

Usa py-readability-metrics sobre el Markdown convertido para estimar
el costo de procesamiento (Flesch-Kincaid, Gunning Fog, etc.).

Las métricas de legibilidad correlacionan con la complejidad del texto
y, por tanto, con el costo de procesamiento por un LLM.
"""

from __future__ import annotations

import logging
import re

from data.pipelines.rfp_intake.models import ReadabilityMetrics

logger = logging.getLogger(__name__)

try:
    from readability import Readability
    HAS_READABILITY = True
except ImportError:
    logger.warning("py-readability-metrics no disponible. Usando implementación simplificada.")
    HAS_READABILITY = False


def compute_readability(markdown_content: str) -> ReadabilityMetrics:
    """Calcula métricas de legibilidad del texto Markdown.

    Args:
        markdown_content: Contenido Markdown del documento convertido.

    Returns:
        ReadabilityMetrics con todas las métricas disponibles.
    """
    # Limpiar el Markdown: eliminar caracteres de formato para contar solo texto
    clean_text = _strip_markdown(markdown_content)

    if not clean_text or len(clean_text.strip()) < 20:
        logger.warning("Texto demasiado corto para métricas de legibilidad.")
        return ReadabilityMetrics()

    metrics = ReadabilityMetrics()
    metrics.character_count = len(clean_text)
    metrics.word_count = _count_words(clean_text)
    metrics.sentence_count = _count_sentences(clean_text)
    metrics.syllable_count = _count_syllables(clean_text)

    if HAS_READABILITY:
        try:
            r = Readability(clean_text)

            try:
                fk = r.flesch_kincaid()
                metrics.flesch_kincaid_grade = fk.score
            except Exception:
                pass

            try:
                fes = r.flesch()
                metrics.flesch_reading_ease = fes.score
            except Exception:
                pass

            try:
                gf = r.gunning_fog()
                metrics.gunning_fog = gf.score
            except Exception:
                pass

            try:
                cl = r.coleman_liau()
                metrics.coleman_liau_index = cl.score
            except Exception:
                pass

            try:
                ari = r.ari()
                metrics.automated_readability_index = ari.score
            except Exception:
                pass

        except Exception as e:
            logger.warning("Error calculando métricas de legibilidad: %s", e)

    return metrics


def _strip_markdown(text: str) -> str:
    """Elimina la sintaxis Markdown para obtener solo texto plano.

    Remove headers, bold, italic, links, images, code blocks, lists, etc.
    """
    # Remove code blocks
    text = re.sub(r'```.*?```', '', text, flags=re.DOTALL)
    # Remove inline code
    text = re.sub(r'`[^`]+`', '', text)
    # Remove images
    text = re.sub(r'!\[.*?\]\(.*?\)', '', text)
    # Remove links, keep text
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    # Remove bold/italic markers
    text = re.sub(r'[*_~]{1,3}', '', text)
    # Remove headers markers
    text = re.sub(r'^#+\s*', '', text, flags=re.MULTILINE)
    # Remove horizontal rules
    text = re.sub(r'^[-*_]{3,}\s*$', '', text, flags=re.MULTILINE)
    # Remove blockquotes
    text = re.sub(r'^>\s*', '', text, flags=re.MULTILINE)
    # Remove list markers
    text = re.sub(r'^[\s]*[-*+]\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^[\s]*\d+\.\s+', '', text, flags=re.MULTILINE)
    # Remove table formatting
    text = re.sub(r'\|', ' ', text)
    text = re.sub(r'[-]{2,}', ' ', text)

    return text.strip()


def _count_words(text: str) -> int:
    """Cuenta palabras en el texto."""
    words = re.findall(r'\b\w+\b', text)
    return len(words)


def _count_sentences(text: str) -> int:
    """Cuenta oraciones en el texto."""
    sentences = re.split(r'[.!?]+', text)
    return len([s for s in sentences if s.strip()])


def _count_syllables(text: str) -> int:
    """Estima el número de sílabas en el texto.

    Usa una heurística simple: cada grupo de vocales consecutivas es una sílaba.
    """
    vowels = 'aeiouáéíóúüAEIOUÁÉÍÓÚÜ'
    syllable_count = 0
    words = re.findall(r'\b\w+\b', text)
    for word in words:
        # Count vowel groups
        count = 0
        prev_was_vowel = False
        for char in word:
            if char in vowels:
                if not prev_was_vowel:
                    count += 1
                prev_was_vowel = True
            else:
                prev_was_vowel = False
        # Silent 'e' adjustment
        if word.endswith('e') and count > 1:
            count -= 1
        syllable_count += max(count, 1)
    return syllable_count