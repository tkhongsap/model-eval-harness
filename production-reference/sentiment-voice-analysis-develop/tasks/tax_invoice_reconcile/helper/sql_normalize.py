"""DuckDB SQL-fragment builders for normalizing buyer keys before matching.

The reconcile step joins OCR output to the master-buyer file on tax-id and confirms
the match with Jaro-Winkler similarity on name/address. Both comparisons need the raw
text normalized first, and the normalization differs by field type:

- **Tax-id:** Excel reads the master "Tax ID" column as a number, dropping the leading
  zero (``0105553045044`` becomes ``105553045044``); the OCR side is a 13-char string.
  ``norm_taxid_sql`` strips non-digits and zero-pads to 13 so both sides align.
- **Name / address:** Thai has no case, so ``UPPER``/``lower`` only helps embedded Latin;
  what depresses the similarity score is spacing variants and invisible zero-width
  characters. ``norm_text_sql`` lowercases (for Latin), NFC-normalizes (tone/vowel
  ordering), and strips whitespace + zero-width characters.

Each function returns a SQL expression string to be interpolated into a larger query;
DuckDB uses the RE2 regex engine, so the character classes follow RE2 syntax.
"""

from __future__ import annotations


def norm_text_sql(col: str) -> str:
    """Return a SQL fragment that normalizes a name/address column for similarity.

    ``lower`` normalizes embedded Latin (a no-op on Thai), ``nfc_normalize`` unifies
    tone/vowel ordering, and the regex strips spaces plus zero-width characters
    (ZWSP/ZWNJ/ZWJ/BOM) that render identically but otherwise lower the score.

    Args:
        col: The SQL column expression to normalize.

    Returns:
        A SQL expression string.
    """
    whitespace_class = "[\\s​‌‍﻿]"
    return f"lower(regexp_replace(nfc_normalize(trim({col})), '{whitespace_class}', '', 'g'))"


def norm_taxid_sql(col: str) -> str:
    """Return a SQL fragment that strips non-digits and zero-pads a tax-id to 13 chars.

    Args:
        col: The SQL column expression to normalize.

    Returns:
        A SQL expression string.
    """
    return f"lpad(regexp_replace({col}, '[^0-9]', '', 'g'), 13, '0')"
