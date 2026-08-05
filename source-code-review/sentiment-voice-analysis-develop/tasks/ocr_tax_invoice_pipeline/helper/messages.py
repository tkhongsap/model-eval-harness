"""Business-facing message text for OCR tax-invoice extraction outcomes.

Single source of the short, plain-English strings written to the ``MESSAGE`` column so a
business user can tell *why* a page or line did not succeed — a rejected image, or an
unsupported / blank document — without seeing column names, scores, or formulas. Used by the
document processor (IQS reject reasons) and the result retriever (``BLANK`` / ``UNSUPPORTED``
rows). Domain-specific field/amount validation lives in each consuming domain, not here.
"""

from __future__ import annotations

from typing import Any

# Terminal statuses stamped at retrieval time (never pass through domain validation).
STATUS_MESSAGES: dict[str, str] = {
    "BLANK": "No line items found on the document",
    "UNSUPPORTED": "Document type is not supported",
}

# Per-IQS-dimension plain-language reject reasons (business-facing, no scores).
_IQS_DIMENSION_REASONS: dict[str, str] = {
    "vq": "image is blurry or low-resolution",
    "sq": "page is skewed or misaligned",
    "ct": "page has too little or too much readable text",
}


def unsupported_file_reason(ext: str) -> str:
    """Return the business-facing reject reason for a file type outside the supported set.

    Args:
        ext: Lowercase file extension including the leading dot (e.g. ``.webp``), or
            an empty string when the file has no extension.

    Returns:
        A short human-readable reject reason.
    """
    return f"Unsupported file type: {ext}" if ext else "Unsupported file type: file has no extension"


def iqs_reject_reason(score: dict[str, Any], iqs_config: dict[str, Any]) -> str:
    """Return a business-facing reason a page failed the IQS quality gate (no scores).

    Names every sub-floor the page fell below, and — when the weighted total is below the
    overall threshold — the single weakest dimension as the main contributor. Falls back to a
    generic message when nothing can be attributed.

    Args:
        score: A scored-page dict carrying ``vq`` / ``sq`` / ``ct`` / ``iqs`` keys.
        iqs_config: The loaded ``iqs_config.yml`` (``threshold`` + ``sub_thresholds``).

    Returns:
        A short human-readable reject reason.
    """
    threshold = float(iqs_config.get("threshold", 0.6))
    sub_thresholds = iqs_config.get("sub_thresholds") or {}
    reasons: list[str] = []
    for dim in ("vq", "sq", "ct"):
        floor = sub_thresholds.get(dim)
        if floor is not None and float(score.get(dim, 0.0)) < float(floor):
            reasons.append(_IQS_DIMENSION_REASONS[dim])
    if float(score.get("iqs", 0.0)) < threshold:
        weakest = min(("vq", "sq", "ct"), key=lambda d: float(score.get(d, 0.0)))
        if _IQS_DIMENSION_REASONS[weakest] not in reasons:
            reasons.append(_IQS_DIMENSION_REASONS[weakest])
    if not reasons:
        return "Image quality below the acceptable level"
    return "Rejected by image quality check: " + ", ".join(reasons)
