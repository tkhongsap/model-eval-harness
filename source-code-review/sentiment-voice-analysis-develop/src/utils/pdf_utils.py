"""PDF page extraction utilities."""

from __future__ import annotations

import io


def extract_single_page(content: bytes, page_index: int) -> bytes:
    """Extract one page from a PDF as standalone PDF bytes.

    Args:
        content: Raw PDF bytes of the source document.
        page_index: Zero-based index of the page to extract.

    Returns:
        PDF bytes containing only the requested page.
    """
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(content)
    try:
        writer = pdfium.PdfDocument.new()
        writer.import_pages(doc, pages=[page_index])
        buf = io.BytesIO()
        writer.save(buf)
        writer.close()
        return buf.getvalue()
    finally:
        doc.close()
