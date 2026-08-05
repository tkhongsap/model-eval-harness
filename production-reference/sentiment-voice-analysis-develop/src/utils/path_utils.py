"""Path, URI, and filename helpers shared across pipelines."""

import os
import re


def extract_date_from_path(path: str) -> str:
    """Return the last YYYYMMDD token between slashes, or "" if none found.

    Args:
        path: A filesystem path or GCS URI such as
            ``gs://bucket/tax_invoice_extraction/input/202601/20260115/file.pdf``.

    Returns:
        The last 8-digit date token bracketed by ``/``, or ``""``.
    """
    matches = re.findall(r"(?<=/)\d{8}(?=/)", path)
    return matches[-1] if matches else ""


def strip_gs_prefix(uri: str, bucket: str) -> str:
    """Strip the ``gs://{bucket}/`` prefix from a GCS URI if present.

    Args:
        uri: A GCS URI or already-stripped object path.
        bucket: The bucket name to strip.

    Returns:
        ``uri`` with the ``gs://{bucket}/`` prefix removed, or ``uri`` unchanged.
    """
    prefix = f"gs://{bucket}/"
    return uri[len(prefix) :] if uri.startswith(prefix) else uri


def strip_page_suffix(filename: str) -> str:
    """Strip the ``_p<N>`` page suffix from a filename's stem.

    Best-effort conversion such as ``invoice_001_p1.pdf`` -> ``invoice_001.pdf``.
    Falls back to the original filename when no suffix is present.

    Args:
        filename: A basename (no directory component required).

    Returns:
        The filename with ``_p<N>`` removed from the stem, or unchanged.
    """
    stem, ext = os.path.splitext(filename)
    match = re.match(r"^(.*?)_p\d+$", stem)
    return f"{match.group(1)}{ext}" if match else filename
