"""Conversor PDF → Markdown para el pipeline RFP Intake.

Usa MarkItDown como conversor principal con fallback a PyPDF2.
El Markdown resultante se guarda en data/raw/{ticket_id}/converted.md.
"""

from __future__ import annotations

import logging
from pathlib import Path

from markitdown import MarkItDown

logger = logging.getLogger(__name__)

RAW_BASE = Path(__file__).resolve().parents[2] / "raw"  # data/raw/


def convert_pdf_to_markdown(pdf_path: str | Path, ticket_id: str) -> str:
    """Convierte un PDF a Markdown usando MarkItDown.

    Args:
        pdf_path: Ruta absoluta o relativa al archivo PDF.
        ticket_id: ID del ticket (se usa para crear el directorio de salida).

    Returns:
        Ruta absoluta al archivo Markdown generado.

    Raises:
        FileNotFoundError: Si el PDF no existe.
        ValueError: Si la conversión falla.
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF no encontrado: {pdf_path}")

    # Crear directorio del ticket en data/raw/
    ticket_dir = RAW_BASE / ticket_id
    ticket_dir.mkdir(parents=True, exist_ok=True)

    # Copiar PDF al directorio del ticket si no está ya allí
    target_pdf = ticket_dir / "original.pdf"
    if pdf_path.resolve() != target_pdf.resolve():
        import shutil
        try:
            shutil.copy2(pdf_path, target_pdf)
        except PermissionError:
            # Fallback: copiar sin metadatos (cross-device)
            shutil.copy(pdf_path, target_pdf)

    try:
        md = MarkItDown()
        result = md.convert(str(pdf_path))
        markdown_content = result.text_content
    except Exception as e:
        logger.warning("MarkItDown falló en %s: %s. Intentando PyPDF2 fallback.", pdf_path, e)
        markdown_content = _fallback_pypdf2(pdf_path)

    if not markdown_content or len(markdown_content.strip()) < 10:
        raise ValueError(f"Conversión PDF→Markdown produjo contenido vacío: {pdf_path}")

    # Guardar Markdown
    md_path = ticket_dir / "converted.md"
    md_path.write_text(markdown_content, encoding="utf-8")
    logger.info("PDF convertido a Markdown: %s → %s (%d chars)", pdf_path, md_path, len(markdown_content))

    return str(md_path.resolve())


def _fallback_pypdf2(pdf_path: Path) -> str:
    """Fallback de conversión usando PyPDF2."""
    from PyPDF2 import PdfReader
    reader = PdfReader(str(pdf_path))
    pages = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            pages.append(text)
    return "\n\n".join(pages)


def read_converted_markdown(markdown_path: str | Path) -> str:
    """Lee el contenido de un archivo Markdown convertido.

    Args:
        markdown_path: Ruta al archivo .md.

    Returns:
        Contenido del archivo como string.
    """
    md_path = Path(markdown_path)
    if not md_path.exists():
        raise FileNotFoundError(f"Markdown no encontrado: {md_path}")
    return md_path.read_text(encoding="utf-8")