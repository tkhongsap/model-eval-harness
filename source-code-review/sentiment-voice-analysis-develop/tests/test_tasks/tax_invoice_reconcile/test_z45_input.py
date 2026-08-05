"""Tests for the ZAPRPT45 positional loader/parser (tasks.tax_invoice_reconcile).

Focus: the date columns must parse from *either* the SAP ``dd.mm.yyyy`` text form or
the ISO datetime string that an Excel *date* cell yields once the loader reads the
sheet with ``dtype=str``. A general parse alone would misread ``05.03.2026`` as
month-first, so both forms must land on the same day.
"""

from __future__ import annotations

import pandas as pd

from tasks.tax_invoice_reconcile.schema.z45_input import Z45Input, validate_z45

_FIELDS = list(Z45Input.to_schema().columns.keys())


def _raw_row(payment_date: str) -> pd.DataFrame:
    """A single positional raw Z45 row (all strings) with the given payment_date cell."""
    row = dict.fromkeys(_FIELDS, "")
    row["payment_date"] = payment_date
    # validate_z45 renames by position, so the physical order must match the field order.
    return pd.DataFrame([[row[name] for name in _FIELDS]], columns=_FIELDS)


def test_validate_z45_text_ddmmyyyy_parses_payment_date():
    # Arrange — SAP text export form
    raw = _raw_row("05.03.2026")

    # Act
    out = validate_z45(raw)

    # Assert
    assert out["payment_date"].iloc[0] == pd.Timestamp(2026, 3, 5)


def test_validate_z45_excel_date_cell_iso_string_parses_payment_date():
    # Arrange — a real Excel date cell, stringified by read_excel(dtype=str)
    raw = _raw_row("2026-03-05 00:00:00")

    # Act
    out = validate_z45(raw)

    # Assert — same day as the text form, not NaT
    assert out["payment_date"].iloc[0] == pd.Timestamp(2026, 3, 5)


def test_validate_z45_junk_date_coerces_to_nat():
    # Arrange
    raw = _raw_row("not-a-date")

    # Act
    out = validate_z45(raw)

    # Assert
    assert pd.isna(out["payment_date"].iloc[0])
