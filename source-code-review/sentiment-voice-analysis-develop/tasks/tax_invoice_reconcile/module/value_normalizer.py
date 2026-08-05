"""Per-field-type value normalization for the fact-check comparison.

Ground-truth cells (all text: ``"1,500.00"``, ``"02/03/2026"``, ``"Yes"``) and extraction
values (typed ``Decimal`` / ``date`` / ``bool`` / ``str``) are reduced to a single canonical
string per :data:`~tasks.tax_invoice_reconcile.helper.constant` compare type so the evaluator's
exact-match test is apples-to-apples. Null/blank on either side collapses to
:data:`~tasks.tax_invoice_reconcile.helper.constant.NA_SENTINEL`, so a labelled-blank GT cell and
an empty extraction agree (a true positive) — but only when **every** candidate extraction column is
blank. A field with two candidate columns (the Thai/English name and address pairs) would otherwise
let a null sibling column supply the sentinel and score a hallucinated value in the populated column
as correct; see ``FactCheckEvaluator._is_match``.

The text rule mirrors reconcile's SQL ``norm_text_sql`` (``helper/sql_normalize.py``) so a
fact-check text match agrees with what the reconcile step actually matches: NFC-normalize, strip
all whitespace + zero-width characters, then lowercase.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import pandas as pd

from tasks.tax_invoice_reconcile.helper.constant import (
    COMPARE_AMOUNT,
    COMPARE_BOOL,
    COMPARE_DATE,
    COMPARE_TAXID,
    NA_SENTINEL,
)


class ValueNormalizer:
    """Reduce a GT or extraction value to its canonical comparable string per compare type."""

    _THAI_TAX_ID_LEN = 13
    _DATE_INPUT_FORMATS = ("%d/%m/%Y", "%Y-%m-%d", "%d.%m.%Y", "%d-%m-%Y")
    _TRUE_TOKENS = frozenset({"true", "t", "yes", "y", "1"})
    _FALSE_TOKENS = frozenset({"false", "f", "no", "n", "0"})

    def normalize(self, value: Any, compare: str) -> str:
        """Reduce a single GT or extraction value to its canonical comparable string.

        Args:
            value: The raw cell value (GT text or a typed extraction value).
            compare: One of the ``COMPARE_*`` constants selecting the normalization rule.

        Returns:
            The canonical string, or :data:`NA_SENTINEL` for a null/blank/unparseable value.
        """
        if self._is_null(value):
            return NA_SENTINEL
        if compare == COMPARE_TAXID:
            return self._normalize_taxid(value)
        if compare == COMPARE_AMOUNT:
            return self._normalize_amount(value)
        if compare == COMPARE_DATE:
            return self._normalize_date(value)
        if compare == COMPARE_BOOL:
            return self._normalize_bool(value)
        return self._normalize_text(value)

    @staticmethod
    def _is_null(value: Any) -> bool:
        """Return True for None / NaN / NaT / pandas ``<NA>`` scalars."""
        if value is None:
            return True
        try:
            return bool(pd.isna(value))
        except (TypeError, ValueError):
            # Non-scalar (list/array) — not a null we handle here.
            return False

    @staticmethod
    def _normalize_text(value: Any) -> str:
        """NFC-normalize, strip all whitespace + zero-width chars, lowercase; blank -> sentinel.

        Mirrors ``norm_text_sql`` so a fact-check text match agrees with reconcile's SQL match:
        the zero-width class is ZWSP / ZWNJ / ZWJ / BOM.
        """
        text = unicodedata.normalize("NFC", str(value))
        text = re.sub(r"[\s​‌‍﻿]", "", text).lower()
        return text or NA_SENTINEL

    @classmethod
    def _normalize_taxid(cls, value: Any) -> str:
        """Keep digits only and left-pad to 13; no digits -> ``NA_SENTINEL``."""
        digits = re.sub(r"\D", "", str(value))
        return digits.zfill(cls._THAI_TAX_ID_LEN) if digits else NA_SENTINEL

    @classmethod
    def _normalize_amount(cls, value: Any) -> str:
        """Parse to a 2dp ``Decimal`` string (thousands separators / currency stripped)."""
        cleaned = str(value) if isinstance(value, (int, float, Decimal)) else re.sub(r"[^\d.\-]", "", str(value))
        try:
            return str(Decimal(cleaned).quantize(Decimal("0.01")))
        except (InvalidOperation, ValueError):
            return cls._normalize_text(value)

    @classmethod
    def _normalize_date(cls, value: Any) -> str:
        """Return an ISO ``YYYY-MM-DD`` date string; unparseable -> ``NA_SENTINEL``."""
        if isinstance(value, (date, datetime)):
            return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()
        text = str(value).strip()
        for fmt in cls._DATE_INPUT_FORMATS:
            try:
                return datetime.strptime(text, fmt).date().isoformat()
            except ValueError:
                continue
        return NA_SENTINEL

    @classmethod
    def _normalize_bool(cls, value: Any) -> str:
        """Map Yes/No/True/False/1/0 to ``"true"``/``"false"``; unknown -> ``NA_SENTINEL``."""
        if isinstance(value, bool):
            return "true" if value else "false"
        token = str(value).strip().casefold()
        if token in cls._TRUE_TOKENS:
            return "true"
        if token in cls._FALSE_TOKENS:
            return "false"
        return NA_SENTINEL
