"""Pure path construction for the reconcile-stage source/Z45 archive on SharePoint.

Builds the archive destinations for the processed source invoices and the Z45 source report
from the source ``FILE_PATH`` and the run's ``DATADATE`` value. No I/O — every function is a
deterministic string transform so it can be unit-tested in isolation.

Layout (roots are configured per task):

* Archived invoices -> ``{archive_invoice_root}/{E-TAX|Paper [Scan]}/{DATADATE}/{name}``
* Archived Z45 -> ``{archive_vat_root}/{DATADATE}/{name}``

The date folder is the flat ``DATADATE`` value (``YYYYMMDD``), matching the OutputExporter's
SQL-built workbook paths. The per-document Output workbook paths are built in
:mod:`tasks.tax_invoice_reconcile.module.output_exporter` (DuckDB), not here.
"""

from __future__ import annotations

import posixpath

# Source-type folder segments (Level 3 under the input root, reused for the archive).
INPUT_TYPE_ETAX = "E-TAX"
INPUT_TYPE_PAPER = "Paper [Scan]"


def classify(file_path: str) -> str:
    """Return the source type (``INPUT_TYPE_ETAX``/``INPUT_TYPE_PAPER``) from ``file_path``.

    The type is the ``E-TAX`` or ``Paper [Scan]`` folder segment in the SharePoint path
    (the filename itself is ignored).

    Raises:
        ValueError: When neither type segment is present in the path.
    """
    segments = file_path.split("/")[:-1]
    if INPUT_TYPE_ETAX in segments:
        return INPUT_TYPE_ETAX
    if INPUT_TYPE_PAPER in segments:
        return INPUT_TYPE_PAPER
    raise ValueError(f"Cannot classify source type (no E-TAX/Paper [Scan] segment): {file_path}")


def archive_invoice_dest(archive_root: str, file_path: str, datadate: int) -> str:
    """Return the archive path for a source invoice file (original name preserved)."""
    folder = classify(file_path)
    name = posixpath.basename(file_path)
    return f"{archive_root.rstrip('/')}/{folder}/{datadate}/{name}"


def archive_vat_dest(archive_root: str, z45_source_path: str, datadate: int) -> str:
    """Return the archive path for the Z45 source file (original name preserved)."""
    name = posixpath.basename(z45_source_path)
    return f"{archive_root.rstrip('/')}/{datadate}/{name}"


def reject_dest(reject_root: str, file_path: str, datadate: int, name: str | None = None) -> str:
    """Return the reject path for a source file or one of its pages.

    Unlike the archive tree, the reject tree is date-first with the E-TAX company/user
    subfolder preserved: ``{root}/{YYYYMMDD}/{XXXX[CompanyCode]_XXXX [User Name]}/{name}`` for
    E-TAX sources (the folder is the source file's immediate parent), ``{root}/{YYYYMMDD}/{name}``
    otherwise. ``name`` overrides the basename (use the page filename when copying a single page).
    """
    target = name or posixpath.basename(file_path)
    if INPUT_TYPE_ETAX in file_path.split("/")[:-1]:
        company_user = file_path.split("/")[-2]
        return f"{reject_root.rstrip('/')}/{datadate}/{company_user}/{target}"
    return f"{reject_root.rstrip('/')}/{datadate}/{target}"
