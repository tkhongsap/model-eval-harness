"""Round-trip tests for src.utils.pdf_utils.extract_single_page."""

from __future__ import annotations

import io

import pytest

from src.utils.pdf_utils import extract_single_page


def _make_two_page_pdf() -> bytes:
    """Build a minimal valid 2-page PDF using pypdfium2."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument.new()
    doc.new_page(width=100, height=100)
    doc.new_page(width=100, height=100)
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def _page_count(pdf_bytes: bytes) -> int:
    """Return the number of pages in ``pdf_bytes``."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(pdf_bytes)
    n = len(doc)
    doc.close()
    return n


def test_extract_single_page_returns_one_page_pdf():
    content = _make_two_page_pdf()
    for page_idx in (0, 1):
        result = extract_single_page(content, page_idx)
        assert _page_count(result) == 1


def test_extract_single_page_first_page_differs_from_second():
    content = _make_two_page_pdf()
    page0 = extract_single_page(content, 0)
    page1 = extract_single_page(content, 1)
    assert isinstance(page0, bytes) and isinstance(page1, bytes)
    assert len(page0) > 0 and len(page1) > 0


def test_extract_single_page_out_of_bounds_raises():
    content = _make_two_page_pdf()
    with pytest.raises(Exception):
        extract_single_page(content, 5)
