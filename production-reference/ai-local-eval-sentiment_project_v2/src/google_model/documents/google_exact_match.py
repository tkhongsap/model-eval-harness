"""Score the Gemini document-extraction sheet against human ground truth, field by field.

Appends an ``Evaluation Dashboard`` and four ``... Compare Result`` sheets to the workbook
:mod:`src.google_model.documents.google_output` produced; the sibling of
:mod:`src.google_model.documents.google_confusion_matrix`. Comparison folds formatting, never
content; the eight name/address columns match by character similarity (names 90%, addresses
80%), with Coverage, CER and format compliance beside exact match. :func:`evaluate` and
:func:`compare_rows` take DataFrames, so the whole scoring path runs offline, no credentials.
"""

import io
import os
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import pandas as pd
from openpyxl.formatting.rule import ColorScaleRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo
from pandera.errors import SchemaError, SchemaErrors

from src.google_model.documents.schema.input_gt_schema import InputGTSchema
from src.google_model.documents.schema.output_schema import OutputSchema
from src.google_model.metrics import (
    ErrorRateReport,
    MatchReport,
    character_error_rate,
    error_rate_stats,
    match_report,
)
from src.hook.sharepoint import SharePointModule
from src.logger import Logger, TracedOperation

logger = Logger.get_logger(__name__)

# The join key: "<split page name>#<1-based printed line position>". Position rather than
# invoice number, because the invoice number is itself under evaluation.
KEY_COLUMN = "Unique identifier"

DOC_TYPE_COLUMN = "Document type"
DATE_COLUMN = "Tax invoice date"

# The six money columns, Decimal(18,2) in the schemas. Excel round-trips Decimal("100.0") and
# Decimal("100.00") as different strings, so these fold to two decimals before comparing.
MONEY_COLUMNS: tuple[str, ...] = (
    "Total amount",
    "Vat amount",
    "Net amount",
    "Withholding tax",
    "Invoice amount",
    "Vat invoice",
)

# BooleanDtype in the schemas; Excel round-trips both sides to "True"/"False" text.
BOOLEAN_COLUMNS: tuple[str, ...] = (
    "Copy",
    "Payee signature flag",
    "Authorized receiver signature flag",
    "Authorized signatory signature flag",
    "Stamp",
)

# Every scored column, in the schemas' declaration order. "No", the join key and "File name"
# are identity, not extraction, and are left out.
SCORED_COLUMNS: tuple[str, ...] = (
    "Document name",
    DOC_TYPE_COLUMN,
    "Buyer name th",
    "Buyer address th",
    "Buyer name eng",
    "Buyer address eng",
    "Buyer tax id",
    "Buyer branch code",
    "Buyer branch name",
    "Vendor name th",
    "Vendor address th",
    "Vendor name eng",
    "Vendor address eng",
    "Vendor tax id",
    "Vendor branch code",
    "Vendor branch name",
    "Tax invoice number",
    DATE_COLUMN,
    "Total amount",
    "Vat amount",
    "Net amount",
    "Copy",
    "Payee signature flag",
    "Authorized receiver signature flag",
    "Authorized signatory signature flag",
    "Withholding tax",
    "Invoice number",
    "Invoice amount",
    "Vat invoice",
    "Stamp",
)

# CER runs on the party name/address fields only -- the long free-text columns where exact
# match reads ~0 however good the extraction is.
CER_COLUMNS: tuple[str, ...] = (
    "Buyer name th",
    "Buyer address th",
    "Buyer name eng",
    "Buyer address eng",
    "Vendor name th",
    "Vendor address th",
    "Vendor name eng",
    "Vendor address eng",
)

# CER at or below which a row counts as a pass -- the one metric where smaller is better.
DEFAULT_CER_THRESHOLD = 0.20

# The same eight columns *match* by character similarity (1 - CER over folded text) rather
# than strict equality; the thresholds are the business's. Everything else matches exactly.
NAME_SIMILARITY_THRESHOLD = 0.90
ADDRESS_SIMILARITY_THRESHOLD = 0.80
SIMILARITY_THRESHOLDS: dict[str, float] = {
    "Buyer name th": NAME_SIMILARITY_THRESHOLD,
    "Buyer name eng": NAME_SIMILARITY_THRESHOLD,
    "Vendor name th": NAME_SIMILARITY_THRESHOLD,
    "Vendor name eng": NAME_SIMILARITY_THRESHOLD,
    "Buyer address th": ADDRESS_SIMILARITY_THRESHOLD,
    "Buyer address eng": ADDRESS_SIMILARITY_THRESHOLD,
    "Vendor address th": ADDRESS_SIMILARITY_THRESHOLD,
    "Vendor address eng": ADDRESS_SIMILARITY_THRESHOLD,
}

FAMILY_CLASSIFICATION = "Document Classification"
FAMILY_EXTRACTION = "Document Extraction"
FAMILY_QUALITY = "Data Extraction Quality"

# Block titles, also the keys of the dict evaluate() returns. Insertion order is sheet order.
BLOCK_HEADER = "COMPARISON"
BLOCK_OVERALL = "OVERALL (by family)"
BLOCK_EXTRACTION = "EXTRACTION (per column)"
BLOCK_CER = "CER (per column)"
BLOCK_FORMAT = "FORMAT COMPLIANCE (prediction only)"
BLOCK_LEGEND = "HOW TO READ THIS DASHBOARD"

NOT_APPLICABLE = "n/a"

# Keys of the dict compare_rows() returns -- stable identifiers, deliberately not the sheet
# names, which are configurable and can pick up a numeric suffix at write time.
COMPARE_DOC = "doc"
COMPARE_BUYER = "buyer"
COMPARE_VENDOR = "vendor"
COMPARE_AMOUNT = "amount"

# Which scored columns each compare sheet carries. Together the four cover SCORED_COLUMNS
# exactly; one sheet of thirty triplets would be unreadable.
COMPARE_GROUPS: dict[str, tuple[str, ...]] = {
    COMPARE_DOC: (
        "Document name",
        DOC_TYPE_COLUMN,
        "Tax invoice number",
        DATE_COLUMN,
        *BOOLEAN_COLUMNS,
    ),
    COMPARE_BUYER: (
        "Buyer name th",
        "Buyer address th",
        "Buyer name eng",
        "Buyer address eng",
        "Buyer tax id",
        "Buyer branch code",
        "Buyer branch name",
    ),
    COMPARE_VENDOR: (
        "Vendor name th",
        "Vendor address th",
        "Vendor name eng",
        "Vendor address eng",
        "Vendor tax id",
        "Vendor branch code",
        "Vendor branch name",
    ),
    COMPARE_AMOUNT: (
        "Total amount",
        "Vat amount",
        "Net amount",
        "Withholding tax",
        "Invoice number",
        "Invoice amount",
        "Vat invoice",
    ),
}

# The two verdicts a Compare cell can hold, spelled as the sentiment workbook spells them.
MATCH_TRUE = "T"
MATCH_FALSE = "F"

# Sub-headers of a compare sheet's three-cell group, in order.
COMPARE_SUBHEADERS: tuple[str, ...] = ("GT", "AI", "Compare")

# The compare sheets' fixed leading columns, spanning both header rows.
COMPARE_INDEX_COLUMNS: tuple[str, ...] = ("No", KEY_COLUMN)

# --------------------------------------------------------------------------------------------
# Format compliance
# --------------------------------------------------------------------------------------------

# The Thai Unicode block, U+0E00-U+0E7F. Spelled as codepoints so the range is auditable
# without a hex inspector -- the two literal characters render as tone marks in most editors.
_THAI_CHAR_RE = re.compile(f"[{chr(0x0E00)}-{chr(0x0E7F)}]")
_LATIN_CHAR_RE = re.compile(r"[A-Za-z]")
# Excel round-trips a date cell as "YYYY-MM-DD 00:00:00"; strip the suffix before judging.
_DATE_MIDNIGHT_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})[ T]00:00:00(?:\.0+)?$")
_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_BRANCH_CODE_RE = re.compile(r"\d{5}")
_TAX_ID_RE = re.compile(r"\d{13}")


def _is_valid_thai_tax_id(value: str) -> bool:
    """Whether a cell is a 13-digit Thai tax ID with a valid mod-11 check digit.

    Digits 1-12 weighted 13 down to 2; the 13th digit equals ``(11 - sum % 11) % 10``.
    """
    if not _TAX_ID_RE.fullmatch(value):
        return False
    weighted = sum(int(digit) * weight for digit, weight in zip(value[:12], range(13, 1, -1)))
    return (11 - weighted % 11) % 10 == int(value[12])


def _is_iso_date(value: str) -> bool:
    """Whether a cell is an ISO 8601 Gregorian date, after shedding Excel's midnight suffix.

    The 1900-2200 year bound catches unconverted Buddhist-era years and placeholder dates.
    """
    match = _DATE_MIDNIGHT_RE.match(value)
    text = match.group(1) if match else value
    if not _ISO_DATE_RE.fullmatch(text):
        return False
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        return False
    return 1900 <= parsed.year <= 2200


def _is_latin_script(value: str) -> bool:
    """Whether an ``*_eng`` cell is free of Thai characters, as the prompt requires."""
    return not _THAI_CHAR_RE.search(value)


def _is_thai_script(value: str) -> bool:
    """Whether a ``*_th`` cell is Thai script: one Thai character or more, no Latin letters."""
    return bool(_THAI_CHAR_RE.search(value)) and not _LATIN_CHAR_RE.search(value)


def _is_branch_code(value: str) -> bool:
    """Whether a branch-code cell is five digits, ``"00000"`` (head office) included."""
    return bool(_BRANCH_CODE_RE.fullmatch(value))


# The format-compliance registry: (check label, sheet column, predicate). One row each on the
# FORMAT block. Every predicate receives a non-blank _clean()ed cell.
FORMAT_CHECKS: tuple[tuple[str, str, Callable[[str], bool]], ...] = (
    ("Thai tax id (13-digit mod-11)", "Buyer tax id", _is_valid_thai_tax_id),
    ("Thai tax id (13-digit mod-11)", "Vendor tax id", _is_valid_thai_tax_id),
    ("ISO date (YYYY-MM-DD)", DATE_COLUMN, _is_iso_date),
    ("Latin script only", "Buyer name eng", _is_latin_script),
    ("Latin script only", "Buyer address eng", _is_latin_script),
    ("Latin script only", "Vendor name eng", _is_latin_script),
    ("Latin script only", "Vendor address eng", _is_latin_script),
    ("Thai script", "Buyer name th", _is_thai_script),
    ("Thai script", "Buyer address th", _is_thai_script),
    ("Thai script", "Vendor name th", _is_thai_script),
    ("Thai script", "Vendor address th", _is_thai_script),
    ("Branch code (00000 or 5 digits)", "Buyer branch code", _is_branch_code),
    ("Branch code (00000 or 5 digits)", "Vendor branch code", _is_branch_code),
)

# --------------------------------------------------------------------------------------------
# Presentation
# --------------------------------------------------------------------------------------------

# Every other block becomes a real Excel table. COMPARISON is a one-row banner, and the
# legend uses merged cells, which a table cannot contain.
UNTABLED_BLOCKS = frozenset({BLOCK_HEADER, BLOCK_LEGEND})

TABLE_STYLE = "TableStyleMedium2"

TITLE_FILL = PatternFill("solid", fgColor="1F3864")
TITLE_FONT = Font(bold=True, color="FFFFFF", size=11)
BANNER_FILL = PatternFill("solid", fgColor="D9E2F3")
# Greyed and italic so an inapplicable cell reads as "not measured here", not a zero score.
MUTED_FONT = Font(color="808080", italic=True)
TAB_COLOR = "1F3864"

# Excel's own three-colour-scale stops, so the sheet looks native rather than themed by hand.
SCALE_LOW, SCALE_MID, SCALE_HIGH = "F8696B", "FFEB84", "63BE7B"

LABEL_COLUMN_WIDTH = 30
DATA_COLUMN_WIDTH = 13
# Columns the legend's Meaning cell is merged across.
LEGEND_SPAN = 9
LEGEND_LINE_CHARS = 115
LEGEND_LINE_HEIGHT = 15

RATIO_SUFFIXES = frozenset({"Precision", "Recall", "F1"})
RATIO_COLUMNS = (
    frozenset({"Accuracy", "Coverage", "Pass rate", "Mean char accuracy (1 - CER)"})
    | RATIO_SUFFIXES
)
# "CER pass rate (<= 0.20)" carries its threshold in the header, so it is matched by prefix.
RATIO_COLUMN_PREFIXES = ("CER pass rate",)

COUNT_SUFFIXES = frozenset({"TP", "TN", "FP", "FN", "Support"})
COUNT_COLUMNS = (
    frozenset(
        {"N", "Scored", "Excluded", "Columns", "Rows", "GT filled", "Checked", "Passed", "Failed"}
    )
    | COUNT_SUFFIXES
)

# The raw CER columns: formatted like ratios but the colour scale is REVERSED, because a
# low CER is the good outcome and the shared scale paints 0 red.
INVERTED_RATIO_COLUMNS = frozenset(
    {
        "Mean CER",
        "Median CER",
        "Best CER (lowest)",
        "Worst CER (highest)",
    }
)

RATIO_FORMAT = "0.0000"
COUNT_FORMAT = "0"

# --- Compare sheets ---------------------------------------------------------------------------

# Excel's own "Bad"/"Good" cell styles, so a mismatch reads the way a spreadsheet user expects.
COMPARE_FALSE_FILL = PatternFill("solid", fgColor="FFC7CE")
COMPARE_FALSE_FONT = Font(color="9C0006")
COMPARE_TRUE_FILL = PatternFill("solid", fgColor="C6EFCE")
COMPARE_TRUE_FONT = Font(color="006100")

COMPARE_HEADER_FILL = PatternFill("solid", fgColor="1F3864")
COMPARE_HEADER_FONT = Font(bold=True, color="FFFFFF")
COMPARE_SUBHEADER_FILL = PatternFill("solid", fgColor="D9E2F3")
COMPARE_SUBHEADER_FONT = Font(bold=True)

COMPARE_KEY_COLUMN_WIDTH = 26
COMPARE_VALUE_COLUMN_WIDTH = 11
COMPARE_NUMBER_COLUMN_WIDTH = 5
COMPARE_TAB_COLOR = "548235"

# The legend, printed as the last block on the sheet, beside the code computing each metric.
LEGEND: tuple[tuple[str, str], ...] = (
    (
        "How this sheet scores",
        "Every extracted column is scored yes/no per row: did Google write the same value as "
        "the human ground truth. Formatting is folded before comparing - money to two decimals, "
        "dates to YYYY-MM-DD, upper/lower case and extra spaces ignored - so only a different "
        "VALUE counts as a mismatch. The eight buyer/vendor name and address columns match by "
        "CHARACTER SIMILARITY instead of strict equality - a name passes at 90%, an address at "
        "80% - and every row of the EXTRACTION table says which rule produced it in the Match "
        "rule column. The four \"Compare Result\" sheets show the yes/no for every row. One row "
        "here is one printed line item, not one document: a page's fields repeat on every "
        "line-item row it produced, so a 20-line invoice weighs 20 times a single-line receipt "
        "in every rate on this sheet.",
    ),
    (
        "N",
        "Rows compared for this column - the line items present in both sheets, joined on "
        "Unique identifier. Nothing is ever excluded, so this is the same number on every row "
        "of the EXTRACTION table.",
    ),
    (
        "Accuracy",
        "Share of rows where the two agreed UNDER THE ROW'S MATCH RULE - equality for most "
        "columns, the similarity threshold for names and addresses. THE ONLY NUMBER IN ITS "
        "BLOCK THAT CARRIES INFORMATION - the three beside it are calculated from it. Careful: "
        "two blank cells count as a match, so a column that is rarely printed scores high just "
        "by both sides leaving it blank. Read it against GT filled and Coverage.",
    ),
    ("TP", "Rows where the human and Google gave the same value. The matches."),
    ("FP", "Rows where they gave different values. The mismatches."),
    (
        "FN and TN",
        "Always 0 here, on every row, and that is correct rather than a gap. This method asks "
        'one question per row - "did they agree" - and a disagreement has no direction, so there '
        "is no missed case and no correct rejection to count.",
    ),
    (
        "Precision",
        "TP/(TP+FP), which with FN at 0 is TP/N - THE SAME NUMBER AS ACCURACY, on every row, "
        "always. It is printed so this sheet lines up column-for-column with the sentiment "
        "evaluation, not because it adds anything.",
    ),
    (
        "Recall",
        "TP/(TP+FN), which with FN at 0 is 1.0000 - on every row, always, whatever the model "
        "did. It measures nothing here. Same reason as Precision: continuity with the sentiment "
        "evaluation.",
    ),
    (
        "F1",
        "2 x Accuracy / (1 + Accuracy). Determined entirely by Accuracy and ALWAYS HIGHER THAN "
        "IT - an accuracy of 0.71 prints as an F1 of 0.83. If you read one number off a block, "
        "read Accuracy.",
    ),
    (
        "GT filled",
        "Rows where the human ground truth holds a value for this column - the denominator "
        "Coverage is measured against.",
    ),
    (
        "Coverage",
        "Of the rows the human filled, the share Google also filled - with ANYTHING, right or "
        "wrong. Completeness only; correctness is the Accuracy beside it. A column can read "
        "Coverage 1.0000 with a terrible Accuracy (everything extracted, everything wrong) and "
        "the reverse. n/a means the human never filled the column, so there is nothing to cover.",
    ),
    (
        "Match rule",
        "Which comparison produced the row's counts. \"exact\" = the folded values must be "
        "equal. \"similarity >= x\" = the row matches when character similarity (1 - CER, "
        "ground truth as the reference, case and spacing already folded) reaches x - names at "
        "0.90, addresses at 0.80. A one-character slip in a long address passes; a wrong "
        "building number in a short one can still fail. The CER table below shows the "
        "distribution behind these pass/fails.",
    ),
    (
        "Compare (T / F)",
        'On the four "Compare Result" sheets. T = the two values matched under the column\'s '
        "Match rule, F = they did not. The GT and AI cells print the raw workbook text, so a "
        "100.0 shown against a 100.00 - or a name one character off its ground truth - can "
        "still read T; the fold and the similarity rule are deliberate, not display bugs. Two "
        "blank cells count as a match - if a whole column reads 1.0000, check GT filled before "
        "believing it.",
    ),
    (
        "CER (Character Error Rate)",
        "The eight buyer/vendor name and address columns only. The share of characters Google "
        "got wrong against the ground truth, counting insertions, deletions and substitutions. "
        "LOWER IS BETTER - the only metric on this sheet that reads that way, and its colours "
        "are reversed to match: green is a low CER. Case-sensitive, unlike the exact match "
        "above. Rows the human left blank are excluded (nothing to err against); a blank "
        "prediction against a filled ground truth scores 1.00. It can exceed 1.00 when Google "
        "produced far more text than the human did.",
    ),
    (
        "Mean char accuracy (1 - CER)",
        "The same number stated the usual way round, so it can be compared against the Accuracy "
        "column above it. 0.90 means roughly 9 characters in 10 are right.",
    ),
    (
        "CER pass rate",
        "Share of scored rows whose CER is at or BELOW the threshold in the header - the "
        "opposite direction to every other rate on the sheet, because a low CER is the good "
        "outcome.",
    ),
    (
        "Format compliance",
        "Judged on the PREDICTION ALONE - no ground truth involved. Does the value obey the "
        "prompt's format rules: tax ids must be 13 digits with a valid Thai mod-11 check digit, "
        "the date must be an ISO YYYY-MM-DD Gregorian date (year 1900-2200, which is what "
        "catches an unconverted Buddhist-era year), eng fields must be free of Thai script, th "
        "fields must be Thai script with no Latin letters, branch codes must be 5 digits. Blank "
        "cells are NOT checked - completeness is Coverage's job - so Checked varies by row. A "
        "value can be format-perfect and still wrong: read this block with the EXTRACTION "
        "accuracy, not instead of it.",
    ),
    (
        NOT_APPLICABLE,
        "Metric does not apply on this row - e.g. Coverage of a column the human never filled, "
        "or the pass rate of a format check no non-blank prediction reached.",
    ),
)


@dataclass
class Config:
    """Runtime configuration, mirroring :class:`src.google_model.documents.google_output.Config`."""

    # SharePoint location of the workbook google_output.py wrote; run_prefix is the run's
    # timestamp folder, filled in by hand.
    dest_file: str = "/poc_internal_model_migration/documentfiles_output"
    run_prefix: str = ""
    output_file_name: str = "model_comparison.xlsx"

    gt_sheet_name: str = "Doc - Groundtruth"
    result_sheet_name: str = "Doc - Google Result"

    # The five sheets a run appends; resolve_sheet_names() gives them a shared numeric suffix.
    eval_sheet_name: str = "Evaluation Dashboard"
    doc_compare_sheet_name: str = "Doc Compare Result"
    buyer_compare_sheet_name: str = "Buyer Compare Result"
    vendor_compare_sheet_name: str = "Vendor Compare Result"
    amount_compare_sheet_name: str = "Amount Compare Result"

    cer_threshold: float = DEFAULT_CER_THRESHOLD

    timezone: str = "Asia/Bangkok"

    @property
    def run_folder(self) -> str:
        """SharePoint folder holding this run's outputs."""
        prefix = self.run_prefix.strip("/")
        return f"{self.dest_file}/{prefix}" if prefix else self.dest_file

    @property
    def workbook_path(self) -> str:
        """SharePoint path of the workbook to evaluate."""
        return f"{self.run_folder}/{self.output_file_name}"


def _elapsed_ms(started: float) -> float:
    """Milliseconds since a ``time.monotonic()`` reading, rounded for logging."""
    return round((time.monotonic() - started) * 1000, 1)


def _clean(value: Any) -> str:
    """Normalise a cell to a stripped string. NaN and None become ``""``."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


def _normalise_text(value: Any) -> str:
    """Fold a text cell: stripped, whitespace collapsed, case folded.

    Only the comparison folds -- the compare sheets print :func:`_clean`'s output.
    """
    return " ".join(_clean(value).split()).casefold()


def _to_decimal(text: str) -> Decimal | None:
    """Parse a money cell, tolerating thousands separators. None when it is not a number.

    The single money parser, so "what counts as a number" cannot drift between its callers.
    """
    try:
        return Decimal(text.replace(",", "").replace(" ", ""))
    except InvalidOperation:
        return None


def _normalise_money(value: Any) -> str:
    """Fold a money cell to two decimals, so the Decimal/Excel round-trip cannot read as an error.

    Beyond the fold the comparison is still equality -- a one-satang difference is real. A
    cell that does not parse falls back to :func:`_normalise_text`; :func:`_match_column`
    counts those cells.
    """
    text = _clean(value)
    if not text:
        return ""
    parsed = _to_decimal(text)
    if parsed is None:
        return _normalise_text(value)
    try:
        return f"{parsed:.2f}"
    except (InvalidOperation, ValueError):
        # A signalling NaN survives Decimal() but refuses to quantize for formatting.
        return _normalise_text(value)


def _normalise_date(value: Any) -> str:
    """Fold a date cell: shed the ``00:00:00`` Excel appends, then fold as text.

    Anything else -- a Thai-month date, a placeholder -- compares as the text it is.
    """
    text = _clean(value)
    match = _DATE_MIDNIGHT_RE.match(text)
    if match:
        text = match.group(1)
    return " ".join(text.split()).casefold()


def _normalise_for(column: str) -> Callable[[Any], str]:
    """The fold a column's cells compare under: money at 2dp, dates sans midnight, else text."""
    if column in MONEY_COLUMNS:
        return _normalise_money
    if column == DATE_COLUMN:
        return _normalise_date
    return _normalise_text


def _row_matches(true: str, pred: str, threshold: float | None) -> bool:
    """Whether one folded pair counts as a match under its column's rule.

    ``threshold`` None is strict equality; otherwise the pair matches when ``1 - CER`` (ground
    truth as the reference) reaches the threshold. Blanks need no special-casing here --
    ``character_error_rate`` already scores them the way equality would.
    """
    if threshold is None:
        return true == pred
    return 1.0 - character_error_rate(true, pred) >= threshold


def _match_column(merged: pd.DataFrame, column: str) -> tuple[MatchReport, list[str]]:
    """Score one column row by row, and return the per-row verdicts alongside it.

    Name/address columns match by similarity (:data:`SIMILARITY_THRESHOLDS`); everything else
    by strict equality after its fold. Both outputs come from one pass so the dashboard and
    the compare sheet can never disagree. Nothing is excluded: two blank cells count as a
    match, so the blank count is logged -- the count only, never the value, which is the PII
    the prompt goes to some length to mask -- and Coverage sits beside Accuracy.

    Returns:
        ``(report, verdicts)`` -- the verdicts are :data:`MATCH_TRUE`/:data:`MATCH_FALSE`, one
        per row of ``merged``, in its order.
    """
    normalise = _normalise_for(column)
    threshold = SIMILARITY_THRESHOLDS.get(column)
    verdicts: list[str] = []
    blank_both = 0
    unparsable = 0

    for raw_true, raw_pred in zip(merged[f"{column}_gt"], merged[f"{column}_pred"], strict=True):
        true, pred = normalise(raw_true), normalise(raw_pred)
        if not true and not pred:
            blank_both += 1
        if column in MONEY_COLUMNS:
            for raw in (raw_true, raw_pred):
                text = _clean(raw)
                if text and _to_decimal(text) is None:
                    unparsable += 1
        verdicts.append(MATCH_TRUE if _row_matches(true, pred, threshold) else MATCH_FALSE)

    if blank_both:
        logger.warning(
            "google_document_exact_match.column.blank_both_sides",
            column=column,
            rows=blank_both,
            scored=len(verdicts),
        )
    if unparsable:
        logger.warning(
            "google_document_exact_match.money.unparsable",
            column=column,
            cells=unparsable,
        )

    # The report is built off the verdicts scored against all-match, so both rules flow
    # through one tested constructor and the degenerate Precision/Recall/F1 identities hold.
    return match_report(verdicts, [MATCH_TRUE] * len(verdicts)), verdicts


def _coverage(merged: pd.DataFrame, column: str) -> tuple[int, int]:
    """Count the rows ground truth filled, and how many of those the prediction also filled.

    Conditioned on the ground truth on purpose: a prediction-side fill rate would reward
    writing something into every cell.

    Returns:
        ``(gt_filled, covered)`` -- the ratio is the caller's, so zero can print n/a.
    """
    gt_filled = 0
    covered = 0
    for raw_true, raw_pred in zip(merged[f"{column}_gt"], merged[f"{column}_pred"], strict=True):
        if not _clean(raw_true):
            continue
        gt_filled += 1
        if _clean(raw_pred):
            covered += 1
    return gt_filled, covered


def _score_cer_column(
    merged: pd.DataFrame, column: str, threshold: float
) -> tuple[ErrorRateReport, int]:
    """Score one name/address column by character error rate.

    Cells are :func:`_clean`'ed but **not** case-folded: CER counts real character errors.
    Blank ground truth is excluded and counted (nothing to err against); a blank prediction
    against a filled ground truth stays in and scores 1.0 naturally.

    Returns:
        ``(report, excluded)``.
    """
    rates: list[float] = []
    excluded = 0

    for raw_true, raw_pred in zip(merged[f"{column}_gt"], merged[f"{column}_pred"], strict=True):
        reference = _clean(raw_true)
        if not reference:
            excluded += 1
            continue
        rates.append(character_error_rate(reference, _clean(raw_pred)))

    if excluded:
        logger.warning(
            "google_document_exact_match.column.excluded",
            column=column,
            dropped=excluded,
            scored=len(rates),
            reason="blank_ground_truth",
        )

    return error_rate_stats(rates, threshold), excluded


def _score_format(merged: pd.DataFrame) -> pd.DataFrame:
    """Judge every prediction cell against :data:`FORMAT_CHECKS`, one block row per check.

    Prediction side only; blank cells are skipped (completeness is Coverage's number), and an
    unreached check prints :data:`NOT_APPLICABLE` rather than a fake rate.
    """
    rows: list[dict[str, Any]] = []
    summary: dict[str, int] = {}

    for label, column, predicate in FORMAT_CHECKS:
        checked = 0
        passed = 0
        for raw in merged[f"{column}_pred"]:
            value = _clean(raw)
            if not value:
                continue
            checked += 1
            if predicate(value):
                passed += 1

        failed = checked - passed
        rows.append(
            {
                "Check": label,
                "Column": column,
                "Checked": checked,
                "Passed": passed,
                "Failed": failed,
                "Pass rate": round(passed / checked, 4) if checked else NOT_APPLICABLE,
            }
        )
        summary[column] = failed

    # One record, counts only -- the failing values are tax IDs, dates and addresses.
    logger.info(
        "google_document_exact_match.format.completed",
        checks=len(rows),
        failed_cells={column: count for column, count in summary.items() if count},
    )
    return pd.DataFrame(rows)


def _extraction_block(
    reports: Mapping[str, MatchReport], coverage: Mapping[str, tuple[int, int]]
) -> pd.DataFrame:
    """Build the per-column EXTRACTION block: match stats plus coverage.

    The first ten columns are the sentiment evaluation's, verbatim; ``Match rule`` says which
    comparison produced each row's counts. See :class:`src.google_model.metrics.MatchReport`
    for the degenerate match columns.
    """
    rows = []
    for column, report in reports.items():
        gt_filled, covered = coverage[column]
        threshold = SIMILARITY_THRESHOLDS.get(column)
        rows.append(
            {
                "Column": column,
                "N": report.n,
                "TP": report.tp,
                "FP": report.fp,
                "FN": report.fn,
                "TN": report.tn,
                "Accuracy": round(report.accuracy, 4),
                "Precision": round(report.precision, 4),
                "Recall": round(report.recall, 4),
                "F1": round(report.f1, 4),
                "GT filled": gt_filled,
                "Coverage": round(covered / gt_filled, 4) if gt_filled else NOT_APPLICABLE,
                "Match rule": (
                    "exact" if threshold is None else f"similarity >= {threshold:.2f}"
                ),
            }
        )
    return pd.DataFrame(rows)


def _cer_block(
    reports: Mapping[str, tuple[ErrorRateReport, int]], threshold: float
) -> pd.DataFrame:
    """Build the per-column CER block for the eight name/address fields."""
    rows = []
    for column, (report, excluded) in reports.items():
        rows.append(
            {
                "Column": column,
                "Scored": report.n,
                "Excluded": excluded,
                "Mean CER": round(report.mean, 4),
                "Median CER": round(report.median, 4),
                "Best CER (lowest)": round(report.best, 4),
                "Worst CER (highest)": round(report.worst, 4),
                f"CER pass rate (<= {threshold:.2f})": round(report.pass_rate, 4),
                "Mean char accuracy (1 - CER)": round(1.0 - report.mean, 4),
            }
        )
    return pd.DataFrame(rows)


def _mean(values: Sequence[float]) -> float:
    """Arithmetic mean, 0.0 on an empty sequence."""
    return sum(values) / len(values) if values else 0.0


def _merge(gt_df: pd.DataFrame, result_df: pd.DataFrame) -> tuple[pd.DataFrame, int, int]:
    """Join the two sheets on :data:`KEY_COLUMN`, cleaning the key on both sides first.

    Shared by :func:`evaluate` and :func:`compare_rows` so both score the same rows in the
    same order. A duplicated key multiplies rows through the inner join -- an upstream bug
    the warning makes explainable rather than silent.

    Returns:
        ``(merged, gt_only, result_only)`` -- the inner join, and how many keys each side held
        alone.

    Raises:
        KeyError: If either frame is missing the join key.
    """
    for name, frame in (("ground truth", gt_df), ("result", result_df)):
        if KEY_COLUMN not in frame.columns:
            raise KeyError(f"The {name} sheet has no {KEY_COLUMN!r} column.")

    gt = gt_df.copy()
    result = result_df.copy()
    gt[KEY_COLUMN] = gt[KEY_COLUMN].map(_clean)
    result[KEY_COLUMN] = result[KEY_COLUMN].map(_clean)

    gt_duplicates = int(gt[KEY_COLUMN].duplicated().sum())
    result_duplicates = int(result[KEY_COLUMN].duplicated().sum())
    if gt_duplicates or result_duplicates:
        logger.warning(
            "google_document_exact_match.join.duplicate_keys",
            gt_duplicates=gt_duplicates,
            result_duplicates=result_duplicates,
        )

    merged = gt.merge(result, on=KEY_COLUMN, how="inner", suffixes=("_gt", "_pred"))
    gt_only = len(set(gt[KEY_COLUMN]) - set(result[KEY_COLUMN]))
    result_only = len(set(result[KEY_COLUMN]) - set(gt[KEY_COLUMN]))

    return merged, gt_only, result_only


def evaluate(
    gt_df: pd.DataFrame,
    result_df: pd.DataFrame,
    *,
    cer_threshold: float = DEFAULT_CER_THRESHOLD,
    with_compare: bool = False,
) -> dict[str, pd.DataFrame] | tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame]]:
    """Score a result sheet against ground truth and return the dashboard's blocks.

    Exact match with coverage per column, CER on the name/address columns, format compliance
    on the prediction alone. Free of I/O, so the whole scoring path runs offline against a
    local copy. Blocks are in sheet order; keys are the titles printed above each table.

    Args:
        gt_df: The ground-truth sheet, as read with ``dtype=str``.
        result_df: The model result sheet, likewise.
        cer_threshold: CER at or **below** which a row counts as a pass.
        with_compare: When True, also return the compare-sheet frames built from the same
            single scoring pass, as ``(blocks, frames)`` -- saving the second join and column
            sweep that a separate :func:`compare_rows` call would spend.

    Returns:
        ``{block title: DataFrame}``, insertion-ordered -- or ``(blocks, frames)`` when
        ``with_compare`` is True.

    Raises:
        KeyError: If either frame is missing the join key.
    """
    started = time.monotonic()

    merged, gt_only, result_only = _merge(gt_df, result_df)

    logger.info(
        "google_document_exact_match.join.completed",
        gt_rows=len(gt_df),
        result_rows=len(result_df),
        matched=len(merged),
        gt_only=gt_only,
        result_only=result_only,
    )

    scored = {column: _match_column(merged, column) for column in SCORED_COLUMNS}
    reports = {column: pair[0] for column, pair in scored.items()}
    coverage = {column: _coverage(merged, column) for column in SCORED_COLUMNS}
    cer_reports = {
        column: _score_cer_column(merged, column, cer_threshold) for column in CER_COLUMNS
    }
    format_block = _score_format(merged)

    classification_report = reports[DOC_TYPE_COLUMN]
    extraction_reports = {
        column: report for column, report in reports.items() if column != DOC_TYPE_COLUMN
    }
    covered_ratios = [
        covered / gt_filled for gt_filled, covered in coverage.values() if gt_filled
    ]
    format_pass_rates = [
        row["Pass rate"] for _, row in format_block.iterrows() if row["Checked"]
    ]
    mean_cer = _mean([report.mean for report, _ in cer_reports.values()])

    overall = pd.DataFrame(
        [
            {
                "Family": FAMILY_CLASSIFICATION,
                "Columns": 1,
                "Rows": len(merged),
                "Accuracy": round(classification_report.accuracy, 4),
                "F1": round(classification_report.f1, 4),
                "Note": (
                    "exact match on Document type; per-class scores are on the "
                    "confusion-matrix dashboard"
                ),
            },
            {
                "Family": FAMILY_EXTRACTION,
                "Columns": len(extraction_reports),
                "Rows": len(merged),
                # Nothing is excluded, so every column scored the same N and the mean of
                # the column accuracies IS the pooled match rate, not an approximation.
                "Accuracy": round(_mean([r.accuracy for r in extraction_reports.values()]), 4),
                "F1": round(_mean([r.f1 for r in extraction_reports.values()]), 4),
                "Note": (
                    f"exact match over {len(extraction_reports)} columns; F1 = 2a/(1+a); "
                    f"mean coverage {_mean(covered_ratios):.4f}"
                ),
            },
            {
                "Family": FAMILY_QUALITY,
                "Columns": len(cer_reports),
                "Rows": len(merged),
                "Accuracy": NOT_APPLICABLE,
                "F1": NOT_APPLICABLE,
                "Note": (
                    f"mean CER {mean_cer:.4f} over {len(cer_reports)} columns; "
                    f"format pass {_mean(format_pass_rates):.4f} over "
                    f"{len(format_block)} checks"
                ),
            },
        ]
    )

    header = pd.DataFrame(
        [
            {
                "Compared": (
                    f"{len(merged)} of {len(gt_df)} ground-truth rows "
                    f"({gt_only} unmatched in ground truth, {result_only} unmatched in result)"
                ),
                "Scoring": (
                    "exact match after folding money/dates/case; names/addresses by character "
                    "similarity (names >= 0.90, addresses >= 0.80) -- see the legend at the foot"
                ),
                "CER pass threshold": cer_threshold,
            }
        ]
    )

    blocks = {
        BLOCK_HEADER: header,
        BLOCK_OVERALL: overall,
        BLOCK_EXTRACTION: _extraction_block(reports, coverage),
        BLOCK_CER: _cer_block(cer_reports, cer_threshold),
        BLOCK_FORMAT: format_block,
        BLOCK_LEGEND: pd.DataFrame(LEGEND, columns=["Term", "Meaning"]),
    }

    logger.info(
        "google_document_exact_match.evaluate.completed",
        matched=len(merged),
        scored_columns=len(reports),
        cer_columns=len(cer_reports),
        format_checks=len(format_block),
        elapsed_ms=_elapsed_ms(started),
    )
    if not with_compare:
        return blocks

    frames = _build_compare_frames(merged, {column: pair[1] for column, pair in scored.items()})
    return blocks, frames


def _compare_frame(
    merged: pd.DataFrame, columns: Sequence[str], verdicts: Mapping[str, Sequence[str]]
) -> pd.DataFrame:
    """One compare sheet's rows: a running number, the key, then GT/AI/Compare per column.

    The GT and AI cells hold :func:`_clean`'s output, not the folded form -- the sheet shows
    what the workbook actually held. Column names are flat (``"Buyer tax id GT"``);
    :func:`write_comparison_sheets` owns the merged two-row rendering.
    """
    if not len(merged):
        # An empty join still has to produce the right shape, or the sheet loses its header.
        headers = [
            *COMPARE_INDEX_COLUMNS,
            *(f"{column} {sub}" for column in columns for sub in COMPARE_SUBHEADERS),
        ]
        return pd.DataFrame(columns=headers)

    # Column-wise construction: same column order, values and dtypes as the previous
    # per-row dicts, without materialising a Series per row.
    data: dict[str, Any] = {
        "No": range(1, len(merged) + 1),
        KEY_COLUMN: [_clean(value) for value in merged[KEY_COLUMN]],
    }
    for column in columns:
        data[f"{column} GT"] = [_clean(value) for value in merged[f"{column}_gt"]]
        data[f"{column} AI"] = [_clean(value) for value in merged[f"{column}_pred"]]
        data[f"{column} Compare"] = list(verdicts[column])
    return pd.DataFrame(data)


def _build_compare_frames(
    merged: pd.DataFrame, verdicts: Mapping[str, Sequence[str]]
) -> dict[str, pd.DataFrame]:
    """Assemble the compare-sheet frames from already-computed verdicts."""
    return {
        key: _compare_frame(merged, columns, verdicts)
        for key, columns in COMPARE_GROUPS.items()
    }


def compare_rows(gt_df: pd.DataFrame, result_df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Build the per-row verdict frames behind the dashboard's EXTRACTION block.

    Free of I/O, and joins through :func:`_merge`, so its rows are the same rows
    :func:`evaluate` scored, in the same order.

    Returns:
        ``{sheet key: frame}`` keyed by the ``COMPARE_*`` constants, insertion-ordered.

    Raises:
        KeyError: If either frame is missing the join key.
    """
    started = time.monotonic()
    merged, _, _ = _merge(gt_df, result_df)

    verdicts = {column: _match_column(merged, column)[1] for column in SCORED_COLUMNS}
    frames = _build_compare_frames(merged, verdicts)

    logger.info(
        "google_document_exact_match.compare_rows.completed",
        matched=len(merged),
        sheets=len(frames),
        elapsed_ms=_elapsed_ms(started),
    )
    return frames


def resolve_sheet_names(existing: Sequence[str], bases: Sequence[str]) -> list[str]:
    """Pick free sheet names for one run, giving all of them the **same** suffix."""
    taken = set(existing)

    if not any(base in taken for base in bases):
        return list(bases)

    suffix = 1
    while any(f"{base}_{suffix}" in taken for base in bases):
        suffix += 1
    return [f"{base}_{suffix}" for base in bases]


def _is_ratio_column(name: str) -> bool:
    """Whether a column holds 0-1 ratios where higher is better, for scale and format.

    The inverted CER columns are handled separately in :func:`_style_block`.
    """
    return (
        name in RATIO_COLUMNS
        or name.rsplit(" ", 1)[-1] in RATIO_SUFFIXES
        or name.startswith(RATIO_COLUMN_PREFIXES)
    )


def _is_count_column(name: str) -> bool:
    """Whether a column holds counts, so it is formatted as an integer and never colour-scaled."""
    return name in COUNT_COLUMNS or name.rsplit(" ", 1)[-1] in COUNT_SUFFIXES


def _colour_scale(invert: bool = False) -> ColorScaleRule:
    """A red-yellow-green scale pinned to 0.0 / 0.5 / 1.0.

    Absolute stops, not ``min``/``max``: a scale relative to each block's own range would
    paint the best column of a uniformly bad family solid green.

    Args:
        invert: Swap the end colours for a lower-is-better metric -- CER, here.
    """
    low, high = (SCALE_HIGH, SCALE_LOW) if invert else (SCALE_LOW, SCALE_HIGH)
    return ColorScaleRule(
        start_type="num",
        start_value=0,
        start_color=low,
        mid_type="num",
        mid_value=0.5,
        mid_color=SCALE_MID,
        end_type="num",
        end_value=1,
        end_color=high,
    )


def _style_block(worksheet, frame: pd.DataFrame, title: str, title_row: int, index: int) -> None:
    """Apply the table, number formats, colour scales and muted cells for one block.

    Delta from the sentiment version: the inverted CER scale is decided per *column*
    (:data:`INVERTED_RATIO_COLUMNS`), because this dashboard has no Metric/Value blocks.
    """
    width = max(len(frame.columns), 1)
    header_row = title_row + 1
    first_data_row = header_row + 1
    last_data_row = header_row + len(frame)
    last_letter = get_column_letter(width)

    for column in range(1, width + 1):
        worksheet.cell(row=title_row, column=column).fill = TITLE_FILL
    worksheet.cell(row=title_row, column=1).font = TITLE_FONT

    if title not in UNTABLED_BLOCKS and len(frame):
        # Table names are workbook-scoped and identifier-like; folding the sheet name in
        # keeps a second evaluation from colliding with tables the first left behind.
        prefix = "".join(c if c.isalnum() else "_" for c in worksheet.title)
        table = Table(
            displayName=f"t_{prefix}_{index}",
            ref=f"A{header_row}:{last_letter}{last_data_row}",
        )
        table.tableStyleInfo = TableStyleInfo(
            name=TABLE_STYLE, showRowStripes=True, showColumnStripes=False
        )
        worksheet.add_table(table)

    if not len(frame):
        return

    for position, column_name in enumerate(frame.columns, start=1):
        letter = get_column_letter(position)
        cells = f"{letter}{first_data_row}:{letter}{last_data_row}"

        if column_name in INVERTED_RATIO_COLUMNS:
            worksheet.conditional_formatting.add(cells, _colour_scale(invert=True))
            number_format = RATIO_FORMAT
        elif _is_ratio_column(column_name):
            worksheet.conditional_formatting.add(cells, _colour_scale())
            number_format = RATIO_FORMAT
        elif _is_count_column(column_name):
            number_format = COUNT_FORMAT
        else:
            number_format = None

        for offset in range(len(frame)):
            cell = worksheet.cell(row=first_data_row + offset, column=position)
            if cell.value == NOT_APPLICABLE:
                cell.font = MUTED_FONT
            elif number_format:
                cell.number_format = number_format


def _style_legend(worksheet, frame: pd.DataFrame, title_row: int) -> None:
    """Merge, wrap and size the legend rows.

    Row heights are set here because Excel does not reliably auto-fit generated wrapped rows.
    """
    first_data_row = title_row + 2

    for offset, meaning in enumerate(frame["Meaning"]):
        row = first_data_row + offset
        worksheet.merge_cells(
            start_row=row, start_column=2, end_row=row, end_column=1 + LEGEND_SPAN
        )
        cell = worksheet.cell(row=row, column=2)
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        worksheet.cell(row=row, column=1).font = Font(bold=True)
        lines = max(1, -(-len(str(meaning)) // LEGEND_LINE_CHARS))
        worksheet.row_dimensions[row].height = lines * LEGEND_LINE_HEIGHT


def write_dashboard(
    workbook_byte: bytes,
    blocks: dict[str, pd.DataFrame],
    sheet_name: str,
) -> bytes:
    """Append the dashboard blocks to a workbook, in memory.

    Appends into the original bytes, so every existing sheet keeps its rows and formatting.

    Args:
        workbook_byte: The workbook as downloaded.
        blocks: ``{title: frame}``, written top to bottom with a blank row between.
        sheet_name: Name to write, resolved by :func:`resolve_sheet_names`; the guard below
            is the backstop against silently overwriting a previous evaluation.

    Returns:
        The updated workbook.

    Raises:
        ValueError: If ``sheet_name`` is already in the workbook.
    """
    started = time.monotonic()

    # The worksheet does not exist until the first to_excel call, so title-row offsets are
    # computed up front and filled in at the end.
    title_rows: list[tuple[int, str]] = []
    row = 0

    with io.BytesIO(workbook_byte) as buffer:
        # "overlay", not "error": every block after the first writes into a sheet this call
        # already created. The collision check happens once, against the arriving sheets.
        with pd.ExcelWriter(
            buffer, engine="openpyxl", mode="a", if_sheet_exists="overlay"
        ) as writer:
            if sheet_name in writer.book.sheetnames:
                raise ValueError(
                    f"Sheet {sheet_name!r} already exists; resolve_sheet_names picks free names."
                )

            for title, frame in blocks.items():
                title_rows.append((row, title))
                frame.to_excel(writer, sheet_name=sheet_name, startrow=row + 1, index=False)
                # title + header + body + one blank row
                row += len(frame) + 3

            worksheet = writer.sheets[sheet_name]
            worksheet.sheet_properties.tabColor = TAB_COLOR

            widest = max((len(frame.columns) for frame in blocks.values()), default=1)
            worksheet.column_dimensions["A"].width = LABEL_COLUMN_WIDTH
            for position in range(2, widest + 1):
                worksheet.column_dimensions[get_column_letter(position)].width = (
                    DATA_COLUMN_WIDTH
                )

            for index, (offset, title) in enumerate(title_rows):
                worksheet.cell(row=offset + 1, column=1, value=title)
                _style_block(worksheet, blocks[title], title, offset + 1, index)

            # The one-row banner is a statement, not data: a light fill, no table styling.
            header_frame = blocks.get(BLOCK_HEADER)
            if header_frame is not None:
                banner = next(r for r, t in title_rows if t == BLOCK_HEADER) + 3
                for column in range(1, len(header_frame.columns) + 1):
                    worksheet.cell(row=banner, column=column).fill = BANNER_FILL

            legend_frame = blocks.get(BLOCK_LEGEND)
            if legend_frame is not None:
                legend_title = next(r for r, t in title_rows if t == BLOCK_LEGEND) + 1
                _style_legend(worksheet, legend_frame, legend_title)

        buffer.seek(0)
        updated = buffer.read()

    logger.info(
        "google_document_exact_match.dashboard.written",
        sheet=sheet_name,
        blocks=len(blocks),
        rows=row,
        bytes=len(updated),
        elapsed_ms=_elapsed_ms(started),
    )
    return updated


def _style_compare_sheet(worksheet, frame: pd.DataFrame, groups: Sequence[str]) -> None:
    """Turn a flat compare frame into the two-row merged-header layout.

    Not a native Excel Table -- a table demands a single header row; the autofilter and
    freeze pane give back what a table would have been used for.
    """
    index_width = len(COMPARE_INDEX_COLUMNS)
    first_data_row = 3
    last_data_row = first_data_row + len(frame) - 1
    width = index_width + len(groups) * len(COMPARE_SUBHEADERS)

    # to_excel put the header on row 1 and the first record on row 2; inserting a row frees
    # row 2 for the sub-headers.
    worksheet.insert_rows(2)

    for offset, name in enumerate(COMPARE_INDEX_COLUMNS, start=1):
        worksheet.merge_cells(start_row=1, start_column=offset, end_row=2, end_column=offset)
        cell = worksheet.cell(row=1, column=offset, value=name)
        cell.fill = COMPARE_HEADER_FILL
        cell.font = COMPARE_HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center")
        worksheet.cell(row=2, column=offset).fill = COMPARE_HEADER_FILL

    for group_index, group in enumerate(groups):
        start = index_width + group_index * len(COMPARE_SUBHEADERS) + 1
        end = start + len(COMPARE_SUBHEADERS) - 1

        worksheet.merge_cells(start_row=1, start_column=start, end_row=1, end_column=end)
        title = worksheet.cell(row=1, column=start, value=group)
        title.fill = COMPARE_HEADER_FILL
        title.font = COMPARE_HEADER_FONT
        title.alignment = Alignment(horizontal="center")
        for column in range(start, end + 1):
            worksheet.cell(row=1, column=column).fill = COMPARE_HEADER_FILL

        for offset, sub in enumerate(COMPARE_SUBHEADERS):
            cell = worksheet.cell(row=2, column=start + offset, value=sub)
            cell.fill = COMPARE_SUBHEADER_FILL
            cell.font = COMPARE_SUBHEADER_FONT
            cell.alignment = Alignment(horizontal="center")

    worksheet.column_dimensions["A"].width = COMPARE_NUMBER_COLUMN_WIDTH
    worksheet.column_dimensions["B"].width = COMPARE_KEY_COLUMN_WIDTH
    for position in range(index_width + 1, width + 1):
        worksheet.column_dimensions[get_column_letter(position)].width = (
            COMPARE_VALUE_COLUMN_WIDTH
        )

    # Header rows and index columns stay put while scrolling.
    worksheet.freeze_panes = f"{get_column_letter(index_width + 1)}{first_data_row}"
    worksheet.sheet_properties.tabColor = COMPARE_TAB_COLOR

    if not len(frame):
        return

    worksheet.auto_filter.ref = f"A2:{get_column_letter(width)}{last_data_row}"

    # Static fills, not a conditional-formatting rule: a rule would recolour hand-edited
    # cells, making an edited copy look like a scored one.
    for group_index in range(len(groups)):
        column = index_width + group_index * len(COMPARE_SUBHEADERS) + len(COMPARE_SUBHEADERS)
        for row in range(first_data_row, last_data_row + 1):
            cell = worksheet.cell(row=row, column=column)
            cell.alignment = Alignment(horizontal="center")
            if cell.value == MATCH_FALSE:
                cell.fill = COMPARE_FALSE_FILL
                cell.font = COMPARE_FALSE_FONT
            elif cell.value == MATCH_TRUE:
                cell.fill = COMPARE_TRUE_FILL
                cell.font = COMPARE_TRUE_FONT


def write_comparison_sheets(
    workbook_byte: bytes,
    frames: Mapping[str, pd.DataFrame],
    sheet_names: Mapping[str, str],
) -> bytes:
    """Append the per-row verdict sheets to a workbook, in memory.

    Appends into the given bytes, so every existing sheet -- including a just-written
    dashboard -- stays untouched; chain it on :func:`write_dashboard`'s return.

    Args:
        workbook_byte: The workbook so far.
        frames: ``{compare key: frame}`` from :func:`compare_rows`.
        sheet_names: ``{compare key: sheet name}``, resolved by :func:`resolve_sheet_names`.

    Returns:
        The updated workbook.

    Raises:
        KeyError: If a frame has no sheet name.
        ValueError: If a resolved name is already in the workbook.
    """
    started = time.monotonic()

    with io.BytesIO(workbook_byte) as buffer:
        with pd.ExcelWriter(
            buffer, engine="openpyxl", mode="a", if_sheet_exists="error"
        ) as writer:
            for key, frame in frames.items():
                name = sheet_names[key]
                if name in writer.book.sheetnames:
                    raise ValueError(
                        f"Sheet {name!r} already exists; resolve_sheet_names picks free names."
                    )

                frame.to_excel(writer, sheet_name=name, index=False)
                # Every third column from the first triplet onwards is a Compare cell, so the
                # group names come off the frame rather than being passed in twice.
                groups = [
                    str(column).rsplit(" ", 1)[0]
                    for column in frame.columns[len(COMPARE_INDEX_COLUMNS) :: 3]
                ]
                _style_compare_sheet(writer.sheets[name], frame, groups)

        buffer.seek(0)
        updated = buffer.read()

    logger.info(
        "google_document_exact_match.compare_sheets.written",
        # Sheet names and row counts only -- the sheets hold extracted document content.
        sheets=[sheet_names[key] for key in frames],
        rows=max((len(frame) for frame in frames.values()), default=0),
        bytes=len(updated),
        elapsed_ms=_elapsed_ms(started),
    )
    return updated


def _to_bool(value: Any) -> bool | None:
    """Parse an Excel-round-tripped flag cell. None for a blank or unrecognised value.

    Excel stores ``BooleanDtype`` back as ``'True'``/``'False'`` strings, which pandera's
    boolean coercion rejects, so flags are mapped before validation.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    return {"true": True, "false": False, "1": True, "0": False}.get(text)


def _coerce_for_validation(frame: pd.DataFrame) -> pd.DataFrame:
    """Pre-coerce a string frame so the pandera schemas can gate it.

    Pandera's coercion fails on ``'1,234.56'`` and ``'True'``, the exact strings Excel
    round-trips. The copy exists only for the validation gate; scoring runs on the raw
    string frame. An unparsable money cell becomes None and is counted; a missing column
    is left for the schema to report.
    """
    copied = frame.copy()
    unparsable: dict[str, int] = {}

    for column in MONEY_COLUMNS:
        if column not in copied.columns:
            continue
        # One pass per column: the previous count-then-map ran _clean and
        # _to_decimal up to twice per cell each.
        converted: list[Any] = []
        bad = 0
        for value in copied[column]:
            text = _clean(value)
            if not text:
                converted.append(None)
                continue
            number = _to_decimal(text)
            if number is None:
                bad += 1
            converted.append(number)
        if bad:
            unparsable[column] = bad
        copied[column] = converted

    for column in BOOLEAN_COLUMNS:
        if column not in copied.columns:
            continue
        copied[column] = copied[column].map(_to_bool).astype("boolean")

    if unparsable:
        # Counts and column names only -- the offending cells are document content.
        logger.warning("google_document_exact_match.money.unparsable_at_load", columns=unparsable)

    return copied


def run(config: Config | None = None) -> None:
    """Download the workbook, score it, and upload it back with five sheets appended.

    One set of bytes, uploaded once -- a partial upload would leave the dashboard claiming
    counts nothing in the workbook can account for.

    Args:
        config: Omitted, the class defaults apply -- including the hand-edited ``run_prefix``.
    """
    config = config if config is not None else Config()

    with TracedOperation(
        "google_document_exact_match.run",
        workbook=config.workbook_path,
    ):
        logger.info(
            "google_document_exact_match.run.starting",
            workbook=config.workbook_path,
            gt_sheet=config.gt_sheet_name,
            result_sheet=config.result_sheet_name,
            eval_sheet=config.eval_sheet_name,
            cer_threshold=config.cer_threshold,
        )

        client_sb = SharePointModule(
            client_id=os.environ["SANDBOX_CLIENT_ID"],
            client_secret=os.environ["SANDBOX_CLIENT_SECRET"],
            tenant_id=os.environ["SANDBOX_TENANT_ID"],
            site_domain=os.environ["SANDBOX_SITE_DOMAIN"],
            site_path=os.environ["SANDBOX_SITE_PATH"],
            timezone=config.timezone,
        )

        with TracedOperation("google_document_exact_match.load"):
            workbook_byte = client_sb.download_file(config.workbook_path).content

            with io.BytesIO(workbook_byte) as f:
                # One ExcelFile, so sheet_names comes off the same parse as the frames.
                book = pd.ExcelFile(f, engine="openpyxl")
                sheet_names = list(book.sheet_names)

                missing = [
                    name
                    for name in (config.gt_sheet_name, config.result_sheet_name)
                    if name not in sheet_names
                ]
                if missing:
                    logger.error(
                        "google_document_exact_match.run.aborted",
                        reason="sheet_missing",
                        file=config.workbook_path,
                        missing_sheets=missing,
                        # Sheet names only -- never cell content.
                        available=sheet_names,
                    )
                    return

                # keep_default_na=False: pandas' default NA set contains 'N/A'/'NA', which
                # a free-text field can legitimately hold; "" stays the one blank.
                read_kwargs = {
                    "dtype": str,
                    "header": 0,
                    "keep_default_na": False,
                    "na_values": [""],
                }
                gt_df = book.parse(config.gt_sheet_name, **read_kwargs)
                result_df = book.parse(config.result_sheet_name, **read_kwargs)

            # The schemas gate shape only: the validated (coerced) copies are deliberately
            # discarded and the raw string frames go to scoring, keeping the offline and
            # production paths byte-identical. strict=False admits extra, not missing, columns.
            for label, frame, schema in (
                (config.gt_sheet_name, gt_df, InputGTSchema),
                (config.result_sheet_name, result_df, OutputSchema),
            ):
                try:
                    schema.validate(_coerce_for_validation(frame))
                except (SchemaError, SchemaErrors) as e:
                    # Computed here rather than off the exception: e.data and e.failure_cases
                    # carry offending cell values, and str(e.schema) is a multi-KB repr.
                    dropped = sorted(set(schema.to_schema().columns) - set(frame.columns))
                    logger.error(
                        "google_document_exact_match.run.aborted",
                        reason="schema_invalid",
                        file=config.workbook_path,
                        sheet=label,
                        missing_columns=dropped,
                        error_type=type(e).__name__,
                        schema=getattr(e.schema, "name", None),
                    )
                    return

            logger.info(
                "google_document_exact_match.load.completed",
                file=config.workbook_path,
                gt_rows=len(gt_df),
                result_rows=len(result_df),
                bytes=len(workbook_byte),
            )

        with TracedOperation("google_document_exact_match.evaluate"):
            # One call scores everything once: the compare frames come from the same join and
            # verdicts as the dashboard, so the two cannot disagree and nothing runs twice.
            blocks, compare_frames = evaluate(
                gt_df, result_df, cer_threshold=config.cer_threshold, with_compare=True
            )
            logger.info(
                "google_document_exact_match.compare_rows.completed",
                matched=len(next(iter(compare_frames.values()))),
                sheets=len(compare_frames),
            )

        with TracedOperation("google_document_exact_match.write"):
            compare_bases = {
                COMPARE_DOC: config.doc_compare_sheet_name,
                COMPARE_BUYER: config.buyer_compare_sheet_name,
                COMPARE_VENDOR: config.vendor_compare_sheet_name,
                COMPARE_AMOUNT: config.amount_compare_sheet_name,
            }
            requested = [config.eval_sheet_name, *compare_bases.values()]
            resolved = resolve_sheet_names(sheet_names, requested)
            sheet_name, *compare_resolved = resolved
            compare_names = dict(zip(compare_bases, compare_resolved, strict=True))

            if sheet_name != config.eval_sheet_name:
                logger.info(
                    "google_document_exact_match.sheet.renamed",
                    requested=requested,
                    resolved=resolved,
                )

            updated = write_dashboard(workbook_byte, blocks, sheet_name)
            del workbook_byte  # free the downloaded copy before the second openpyxl load
            updated = write_comparison_sheets(updated, compare_frames, compare_names)

            # A fault here -- a lock, a transient 503 -- must not read as a scoring failure;
            # SharePointModule already logged the cause at its single ERROR site.
            uploaded = False
            try:
                client_sb.upload_file(
                    upload_path=config.workbook_path, content=updated, archive_on_lock=True
                )
                uploaded = True
            except Exception:
                logger.warning(
                    "google_document_exact_match.output.skipped",
                    path=config.workbook_path,
                    sheet=sheet_name,
                )

        logger.info(
            "google_document_exact_match.run.completed",
            workbook=config.workbook_path,
            sheet=sheet_name,
            compare_sheets=list(compare_names.values()),
            gt_rows=len(gt_df),
            result_rows=len(result_df),
            blocks=len(blocks),
            uploaded=uploaded,
        )
