"""Score the internal model's document-classification fields against ground truth, class by class.

Appends a per-class confusion-matrix ``Evaluation Dashboard`` to the workbook
:mod:`src.local_model.documents.internal_direct_output` produced; the sibling of
:mod:`src.local_model.documents.internal_exact_match`, which asks only whether the two sides
agreed. This module asks *which class* each side gave, so a majority-class shortcut collapses
Macro-F1 instead of hiding in the match rate; rows blank or outside the class vocabulary are
excluded and counted -- the opposite of the exact-match choice, and both counts are printed.
:func:`evaluate` takes DataFrames, so the whole scoring path runs offline, no credentials.
"""

import io
import os
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
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
    ClassificationReport,
    LabelScore,
    classification_report,
)
from src.hook.sharepoint import SharePointModule
from src.logger import Logger, TracedOperation

logger = Logger.get_logger(__name__)

# The join key: "<split page name>#<1-based printed line position>". Position rather than
# invoice number, because the invoice number is itself under evaluation.
KEY_COLUMN = "Unique identifier"

# The page-name extensions the two splits produce. internal_direct_output.split_doc RENDERS a
# PDF page to PNG -- the endpoint takes images and no PDFs -- where the google pipeline that
# built the ground truth re-saved the same page as a PDF, so one page arrives as
# "<stem>_p1.png#1" here and "<stem>_p1.pdf#1" there. The join folds that away.
#
# A whitelist rather than a blanket suffix strip: Path().stem (the sentiment modules' fold)
# would eat the tail of a stem that legitimately contains a dot -- "INV_2026.03_p1" becomes
# "INV_2026_p1" -- and would swallow a path separator besides.
_PAGE_EXTENSION_RE = re.compile(r"\.(?:pdf|png|jpe?g)$", re.IGNORECASE)

# The column _merge actually joins on, so KEY_COLUMN survives the merge unfolded on each side.
JOIN_COLUMN = "__page_key__"

# Unmatched keys logged per side. A key is a page name and a line position, never cell content.
UNMATCHED_SAMPLE = 10

DOC_TYPE_COLUMN = "Document type"

# The six types the DOC_TYPE Literal declares. Deliberately no synonym map -- folding
# "Invoice" into "TaxInvoice" is a business judgement; an unknown value drops the row.
DOC_TYPE_LABELS: tuple[str, ...] = (
    "TaxInvoice",
    "Receipt",
    "Quotation",
    "IDCard",
    "Suspicious",
    "Other",
)
_DOC_TYPE_BY_LOWER = {label.lower(): label for label in DOC_TYPE_LABELS}

# BooleanDtype in the schemas, "True"/"False" strings after the Excel round-trip. Scored as
# two-class problems so a flag the model never raises stays visible.
BOOLEAN_COLUMNS: tuple[str, ...] = (
    "Copy",
    "Payee signature flag",
    "Authorized receiver signature flag",
    "Authorized signatory signature flag",
    "Stamp",
)
BOOLEAN_LABELS: tuple[str, ...] = ("True", "False")

# Block titles, also the keys of the dict evaluate() returns. Insertion order is sheet order.
BLOCK_HEADER = "COMPARISON"
BLOCK_DOC_TYPE = "DOCUMENT TYPE"
BLOCK_DOC_TYPE_LABELS = "DOCUMENT TYPE (per label)"
BLOCK_FLAGS = "VISUAL FLAGS (per column)"
BLOCK_LEGEND = "HOW TO READ THIS DASHBOARD"

NOT_APPLICABLE = "n/a"

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

# Matched on the whole name and on the last word: the flags block prefixes per-class columns
# with the label, and "F1" and "True F1" must format alike.
RATIO_SUFFIXES = frozenset({"Precision", "Recall", "F1"})
RATIO_COLUMNS = frozenset({"Accuracy", "Macro-F1"}) | RATIO_SUFFIXES
COUNT_SUFFIXES = frozenset({"TP", "TN", "FP", "FN", "Support"})
# "Macro-F1 classes" is a count; left out it would fall through to the ratio scale on the
# strength of its prefix and paint a 6 solid green.
COUNT_COLUMNS = (
    frozenset({"N", "Scored", "Excluded", "Columns", "Rows", "Macro-F1 classes"}) | COUNT_SUFFIXES
)

# Rows of a Metric/Value block whose Value is a 0-1 ratio, matched by name so reordering
# cannot silently colour a count on a 0-1 scale.
RATIO_METRICS = frozenset({"Accuracy", "Macro-F1"})

RATIO_FORMAT = "0.0000"
COUNT_FORMAT = "0"

# The legend, printed as the last block on the sheet, beside the code computing each metric.
LEGEND: tuple[tuple[str, str], ...] = (
    (
        "How this sheet scores",
        "Document type is scored against its six classes (TaxInvoice, Receipt, Quotation, "
        "IDCard, Suspicious, Other) and each visual flag as a two-class True/False problem: "
        "WHICH class each side gave matters here, not just whether they agreed. For the "
        "did-they-agree view of every extracted column, read the exact-match Evaluation "
        "Dashboard. One row is one printed line item, not one document: a page's "
        "classification repeats on every line-item row it produced, so a 20-line invoice "
        "weighs 20 times a single-line receipt.",
    ),
    (
        "Scored / Excluded",
        "Rows scored for this column, and rows dropped from it. A row is dropped when either "
        "side is blank or holds a value outside the class set - a ground-truth data-entry "
        "fault or a model answer the vocabulary does not contain, not a scoreable "
        "disagreement. The exact-match dashboard makes the opposite choice and counts those "
        "rows as mismatches; the two sheets say what they did, so the Ns can be reconciled.",
    ),
    (
        "Accuracy",
        "Share of scored rows where the model gave the same class as the human. Careful: "
        "Document type is almost all TaxInvoice, so a model that answers TaxInvoice every "
        "time scores near-perfect accuracy. NEVER READ IT WITHOUT MACRO-F1 BESIDE IT.",
    ),
    (
        "Macro-F1",
        "Average F1 across every class either side actually used, each class weighted equally "
        "regardless of how rare it is. This is the number a majority-class shortcut cannot "
        "inflate: a class the model never predicts scores F1 0 and drags the average down.",
    ),
    (
        "TP / TN / FP / FN (per label)",
        "Read one class at a time: TP = both sides gave this class, TN = neither did, FP = "
        "the model gave it and the human did not (over-calling), FN = the human gave it and "
        "the model did not (missed it).",
    ),
    (
        "Precision / Recall / F1 (per label)",
        "Precision = TP/(TP+FP), how often the model was right when it called this class. Recall "
        "= TP/(TP+FN), how much of the human's use of this class the model found. F1 balances "
        "the two.",
    ),
    (
        "Support",
        "How many scored rows the human labelled with this class (= TP+FN). Small support "
        "means the scores swing on one or two rows - read them with caution.",
    ),
    (
        "Support 0",
        "A class the human never used. If the model never used it either, the class is left out "
        "of Macro-F1 entirely and its Precision/Recall/F1 read n/a - it is a class nobody "
        "used, not a class anybody got wrong. If the model DID use it (FP above 0), it stays in "
        "at F1 0.00, because inventing a class the human never gave is a real error.",
    ),
    (
        "Macro-F1 classes",
        "How many classes went into Macro-F1 - 6 normally for Document type, 2 for a flag, "
        "fewer when a class was unused by both sides. It is here so a Macro-F1 that moved "
        "between runs can be told apart from a model that moved: a 4-class average and a "
        "6-class average are not comparable.",
    ),
    (
        "Visual flags",
        "Copy, the three signature flags and Stamp are True/False extraction fields - did the "
        "page carry this mark. Scored as two-class problems so the rare side of an unbalanced "
        "flag stays visible: a stamp that is almost never present scores high accuracy by "
        "always answering False, and the True class's recall is what says whether the stamps "
        "that DO exist were found.",
    ),
    (
        NOT_APPLICABLE,
        "Metric does not apply here - the scores of a class neither side ever used.",
    ),
)


@dataclass
class Config:
    """Runtime configuration, mirroring :class:`src.local_model.documents.internal_direct_output.Config`."""

    # SharePoint location of the workbook internal_direct_output.py wrote; run_prefix is
    # the run's timestamp folder, filled in by hand.
    dest_file: str = "/poc_internal_model_migration/documentfiles_internal_output"
    run_prefix: str = ""
    output_file_name: str = "model_comparison.xlsx"

    gt_sheet_name: str = "Doc - Groundtruth"
    result_sheet_name: str = "Doc - Internal Model Result"
    eval_sheet_name: str = "Evaluation Dashboard"

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


def _page_key(value: Any) -> str:
    """Fold one :data:`KEY_COLUMN` cell onto the identity both pipelines agree on.

    The key is ``<page name>#<printed line position>``, and only the page name's *extension*
    differs between the two splits, so that is all this drops. The page name's case is
    preserved -- two sources differing only in case are two sources -- and the ``#`` position
    is passed through untouched, so a line-count divergence still shows up as an unmatched
    key rather than being folded away.
    """
    key = _clean(value)
    page, separator, position = key.rpartition("#")
    if not separator:
        page, position = key, ""
    folded = _PAGE_EXTENSION_RE.sub("", page)
    return f"{folded}#{position}" if separator else folded


def _canonical_doc_type(value: Any) -> str | None:
    """Map a ``Document type`` cell onto its canonical class. None for blank or unknown.

    Case-insensitive; nothing beyond case is folded, since the label set carries no synonyms.
    """
    return _DOC_TYPE_BY_LOWER.get(_clean(value).lower())


def _canonical_bool(value: Any) -> str | None:
    """Map a flag cell onto ``"True"``/``"False"``. None for blank or unrecognised.

    ``1``/``0`` are admitted because a hand-edited ground-truth cell can arrive that way.
    """
    text = _clean(value).lower()
    return {"true": "True", "1": "True", "false": "False", "0": "False"}.get(text)


def _score_single_label(
    merged: pd.DataFrame,
    column: str,
    labels: Sequence[str],
    canonicalise: Callable[[Any], str | None],
) -> tuple[ClassificationReport, int]:
    """Score one single-label column, excluding rows either side graded outside ``labels``.

    An out-of-vocabulary value is not a scoreable disagreement, so the row is dropped from
    *this column only* and counted; the count reaches the dashboard's ``Scored``/``Excluded``
    cells, so an exclusion is never invisible.

    Returns:
        ``(report, dropped)``.
    """
    y_true: list[str] = []
    y_pred: list[str] = []
    dropped = 0

    for raw_true, raw_pred in zip(
        merged[f"{column}_gt"], merged[f"{column}_pred"], strict=True
    ):
        true, pred = canonicalise(raw_true), canonicalise(raw_pred)
        if true is None or pred is None:
            dropped += 1
            continue
        y_true.append(true)
        y_pred.append(pred)

    if dropped:
        # The count and the column, never the value. A stray cell here is document content --
        # the PII the prompt goes to some length to mask.
        logger.warning(
            "internal_document_confusion_matrix.column.excluded",
            column=column,
            dropped=dropped,
            scored=len(y_true),
        )

    return classification_report(y_true, y_pred, labels), dropped


def _score_cells(score: LabelScore, *, used: bool, prefix: str = "") -> dict[str, Any]:
    """The three score cells for one class, blanked when neither side used it.

    ``used`` is membership in the report's ``macro_labels``. A class absent from both sides
    scores 0.0000 only because every denominator was zero; printing the zeros would put a
    red cell on a perfect column.
    """
    if not used:
        return {f"{prefix}{metric}": NOT_APPLICABLE for metric in ("Precision", "Recall", "F1")}
    return {
        f"{prefix}Precision": round(score.precision, 4),
        f"{prefix}Recall": round(score.recall, 4),
        f"{prefix}F1": round(score.f1, 4),
    }


def _class_cells(report: ClassificationReport, labels: Sequence[str]) -> dict[str, Any]:
    """Flatten a report's per-label scores into ``"{label} {metric}"`` cells.

    ``labels`` is the *block's* full label set, so every row carries the same columns.
    """
    scored = {score.label: score for score in report.per_label}
    averaged = set(report.macro_labels)
    cells: dict[str, Any] = {}

    for label in labels:
        score = scored.get(label)
        if score is None:
            cells.update(
                {
                    f"{label} {metric}": NOT_APPLICABLE
                    for metric in ("TP", "TN", "FP", "FN", "Precision", "Recall", "F1", "Support")
                }
            )
            continue
        cells.update(
            {
                f"{label} TP": score.tp,
                f"{label} TN": score.tn,
                f"{label} FP": score.fp,
                f"{label} FN": score.fn,
                **_score_cells(score, used=label in averaged, prefix=f"{label} "),
                f"{label} Support": score.support,
            }
        )

    return cells


def _single_label_block(
    reports: dict[str, tuple[ClassificationReport, int]], labels: Sequence[str]
) -> pd.DataFrame:
    """Build a per-column block for a family of single-label columns."""
    rows = [
        {
            "Column": column,
            "Scored": report.n,
            "Excluded": dropped,
            "Accuracy": round(report.accuracy, 4),
            "Macro-F1": round(report.macro_f1, 4),
            "Macro-F1 classes": len(report.macro_labels),
            **_class_cells(report, labels),
        }
        for column, (report, dropped) in reports.items()
    ]
    return pd.DataFrame(rows)


def _merge(gt_df: pd.DataFrame, result_df: pd.DataFrame) -> tuple[pd.DataFrame, int, int]:
    """Join the two sheets on :func:`_page_key`'s fold of :data:`KEY_COLUMN`.

    Joined on :data:`JOIN_COLUMN` rather than the raw key, so ``KEY_COLUMN`` survives the
    merge suffixed per side. A duplicated key multiplies rows through the inner join -- an
    upstream bug the warning makes explainable rather than silent; the fold can *create* one,
    two sources sharing a stem across extensions, which is the case ``split_doc`` already
    warns about.

    An empty join is logged at ERROR and then returned: the caller still writes and uploads
    its dashboard, and a sheet of zeros with nothing in the log would be the worse outcome.

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
    gt[JOIN_COLUMN] = gt[KEY_COLUMN].map(_page_key)
    result[JOIN_COLUMN] = result[KEY_COLUMN].map(_page_key)

    gt_duplicates = int(gt[JOIN_COLUMN].duplicated().sum())
    result_duplicates = int(result[JOIN_COLUMN].duplicated().sum())
    if gt_duplicates or result_duplicates:
        logger.warning(
            "internal_document_confusion_matrix.join.duplicate_keys",
            gt_duplicates=gt_duplicates,
            result_duplicates=result_duplicates,
        )

    merged = gt.merge(result, on=JOIN_COLUMN, how="inner", suffixes=("_gt", "_pred"))
    gt_keys = set(gt[JOIN_COLUMN])
    result_keys = set(result[JOIN_COLUMN])
    gt_only_keys = sorted(gt_keys - result_keys)
    result_only_keys = sorted(result_keys - gt_keys)

    if gt_only_keys or result_only_keys:
        # The keys themselves, capped -- the two counts alone cannot tell "the extensions
        # disagree" from "the model read fewer line items than the human recorded", and only
        # the second of those is a measurement rather than a bug.
        logger.warning(
            "internal_document_confusion_matrix.join.unmatched",
            gt_only=len(gt_only_keys),
            result_only=len(result_only_keys),
            gt_sample=gt_only_keys[:UNMATCHED_SAMPLE],
            result_sample=result_only_keys[:UNMATCHED_SAMPLE],
        )

    if not len(merged):
        logger.error(
            "internal_document_confusion_matrix.join.empty",
            gt_rows=len(gt),
            result_rows=len(result),
            gt_sample=gt_only_keys[:UNMATCHED_SAMPLE],
            result_sample=result_only_keys[:UNMATCHED_SAMPLE],
        )

    return merged, len(gt_only_keys), len(result_only_keys)


def evaluate(gt_df: pd.DataFrame, result_df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Score a result sheet against ground truth and return the dashboard's blocks.

    ``Document type`` against its six classes, the five visual flags as two-class problems.
    Free of I/O, so the whole scoring path runs offline against a local copy. Blocks are in
    sheet order; the keys are the titles printed above each table.

    Args:
        gt_df: The ground-truth sheet, as read with ``dtype=str``.
        result_df: The model result sheet, likewise.

    Returns:
        ``{block title: DataFrame}``, insertion-ordered.

    Raises:
        KeyError: If either frame is missing the join key.
    """
    started = time.monotonic()

    merged, gt_only, result_only = _merge(gt_df, result_df)

    logger.info(
        "internal_document_confusion_matrix.join.completed",
        gt_rows=len(gt_df),
        result_rows=len(result_df),
        matched=len(merged),
        gt_only=gt_only,
        result_only=result_only,
    )

    doc_type_report, doc_type_dropped = _score_single_label(
        merged, DOC_TYPE_COLUMN, DOC_TYPE_LABELS, _canonical_doc_type
    )
    flag_reports = {
        column: _score_single_label(merged, column, BOOLEAN_LABELS, _canonical_bool)
        for column in BOOLEAN_COLUMNS
    }

    # dtype=object throughout: a Metric/Value block mixes ratios with counts, and letting pandas
    # unify the column on float64 writes the counts to the sheet as 3625.0.
    doc_type_summary = pd.DataFrame(
        [
            {"Metric": "Accuracy", "Value": round(doc_type_report.accuracy, 4)},
            {"Metric": "Macro-F1", "Value": round(doc_type_report.macro_f1, 4)},
            {"Metric": "Macro-F1 classes", "Value": len(doc_type_report.macro_labels)},
            {"Metric": "Scored", "Value": doc_type_report.n},
            {"Metric": "Excluded (blank or unknown)", "Value": doc_type_dropped},
        ],
        dtype=object,
    )

    # A class neither side used blanks its three scores, matching _class_cells -- 0.0000 there
    # would read as "the model failed on this class" when in fact the class never came up.
    averaged_types = set(doc_type_report.macro_labels)
    doc_type_labels = pd.DataFrame(
        [
            {
                "Label": score.label,
                "TP": score.tp,
                "TN": score.tn,
                "FP": score.fp,
                "FN": score.fn,
                **_score_cells(score, used=score.label in averaged_types),
                "Support": score.support,
            }
            for score in doc_type_report.per_label
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
                    "per-class confusion matrices; see the exact-match dashboard for the "
                    "match-rate view"
                ),
            }
        ]
    )

    blocks = {
        BLOCK_HEADER: header,
        BLOCK_DOC_TYPE: doc_type_summary,
        BLOCK_DOC_TYPE_LABELS: doc_type_labels,
        BLOCK_FLAGS: _single_label_block(flag_reports, BOOLEAN_LABELS),
        BLOCK_LEGEND: pd.DataFrame(LEGEND, columns=["Term", "Meaning"]),
    }

    logger.info(
        "internal_document_confusion_matrix.evaluate.completed",
        matched=len(merged),
        doc_type_scored=doc_type_report.n,
        doc_type_excluded=doc_type_dropped,
        flag_columns=len(flag_reports),
        elapsed_ms=_elapsed_ms(started),
    )
    return blocks


def resolve_sheet_name(existing: Sequence[str], base: str) -> str:
    """Pick a free sheet name, appending ``_1``, ``_2``, ... on collision."""
    taken = set(existing)

    if base not in taken:
        return base

    suffix = 1
    while f"{base}_{suffix}" in taken:
        suffix += 1
    return f"{base}_{suffix}"


def _is_ratio_column(name: str) -> bool:
    """Whether a column holds 0-1 ratios, so it can be colour-scaled and formatted as one.

    Matches the bare metric names and the per-class ones ``_class_cells`` produces.
    """
    return name in RATIO_COLUMNS or name.rsplit(" ", 1)[-1] in RATIO_SUFFIXES


def _is_count_column(name: str) -> bool:
    """Whether a column holds counts, so it is formatted as an integer and never colour-scaled."""
    return name in COUNT_COLUMNS or name.rsplit(" ", 1)[-1] in COUNT_SUFFIXES


def _is_ratio_metric(name: Any) -> bool:
    """Whether a Metric/Value row's Value is a 0-1 ratio rather than a count."""
    return isinstance(name, str) and name in RATIO_METRICS


def _colour_scale(invert: bool = False) -> ColorScaleRule:
    """A red-yellow-green scale pinned to 0.0 / 0.5 / 1.0.

    Absolute stops, not ``min``/``max``: a scale relative to each block's own range would
    paint the best column of a uniformly bad family solid green.

    Args:
        invert: Unused here; kept so the helper stays identical across evaluation modules.
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
    """Apply the table, number formats, colour scales and muted cells for one block."""
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

    metric_values = frame.iloc[:, 0] if "Metric" in frame.columns[:1] else None

    for position, column_name in enumerate(frame.columns, start=1):
        letter = get_column_letter(position)
        cells = f"{letter}{first_data_row}:{letter}{last_data_row}"

        if _is_ratio_column(column_name):
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

        # A Metric/Value block mixes ratios with counts in one column, so the scale and the
        # format are decided per row, off the metric's name.
        if metric_values is not None and column_name == "Value":
            for offset, metric in enumerate(metric_values):
                cell = worksheet.cell(row=first_data_row + offset, column=position)
                if _is_ratio_metric(metric):
                    worksheet.conditional_formatting.add(cell.coordinate, _colour_scale())
                    cell.number_format = RATIO_FORMAT
                else:
                    cell.number_format = COUNT_FORMAT


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
        sheet_name: Name to write, resolved by :func:`resolve_sheet_name`; the guard below
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
                    f"Sheet {sheet_name!r} already exists; resolve_sheet_name picks a free name."
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
        "internal_document_confusion_matrix.dashboard.written",
        sheet=sheet_name,
        blocks=len(blocks),
        rows=row,
        bytes=len(updated),
        elapsed_ms=_elapsed_ms(started),
    )
    return updated


def _to_bool(value: Any) -> bool | None:
    """Parse an Excel-round-tripped flag cell. None for a blank or unrecognised value.

    Validation-gate only -- :func:`_canonical_bool` is the scoring-side mapping. Excel stores
    ``BooleanDtype`` back as ``'True'``/``'False'`` strings, which pandera's coercion rejects.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    return {"true": True, "false": False, "1": True, "0": False}.get(text)


# The six Decimal(18,2) money columns, needed here only for the validation gate.
MONEY_COLUMNS: tuple[str, ...] = (
    "Total amount",
    "Vat amount",
    "Net amount",
    "Withholding tax",
    "Invoice amount",
    "Vat invoice",
)


def _to_decimal(text: str) -> Decimal | None:
    """Parse a money cell, tolerating thousands separators. None when it is not a number."""
    try:
        return Decimal(text.replace(",", "").replace(" ", ""))
    except InvalidOperation:
        return None


def _coerce_for_validation(frame: pd.DataFrame) -> pd.DataFrame:
    """Pre-coerce a string frame so the pandera schemas can gate it.

    Pandera's coercion fails on ``'1,234.56'`` and ``'True'``, the exact strings Excel
    round-trips. The copy exists only for the validation gate; scoring runs on the raw
    string frame. A missing column is left for the schema to report.
    """
    copied = frame.copy()

    for column in MONEY_COLUMNS:
        if column not in copied.columns:
            continue
        copied[column] = [
            _to_decimal(text) if (text := _clean(value)) else None
            for value in copied[column]
        ]

    for column in BOOLEAN_COLUMNS:
        if column not in copied.columns:
            continue
        copied[column] = copied[column].map(_to_bool).astype("boolean")

    return copied


def run(config: Config | None = None) -> None:
    """Download the workbook, score it, and upload it back with the dashboard appended.

    Args:
        config: Omitted, the class defaults apply -- including the hand-edited ``run_prefix``.
    """
    config = config if config is not None else Config()

    with TracedOperation(
        "internal_document_confusion_matrix.run",
        workbook=config.workbook_path,
    ):
        logger.info(
            "internal_document_confusion_matrix.run.starting",
            workbook=config.workbook_path,
            gt_sheet=config.gt_sheet_name,
            result_sheet=config.result_sheet_name,
            eval_sheet=config.eval_sheet_name,
        )

        client_sb = SharePointModule(
            client_id=os.environ["SANDBOX_CLIENT_ID"],
            client_secret=os.environ["SANDBOX_CLIENT_SECRET"],
            tenant_id=os.environ["SANDBOX_TENANT_ID"],
            site_domain=os.environ["SANDBOX_SITE_DOMAIN"],
            site_path=os.environ["SANDBOX_SITE_PATH"],
            timezone=config.timezone,
        )

        with TracedOperation("internal_document_confusion_matrix.load"):
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
                        "internal_document_confusion_matrix.run.aborted",
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
                        "internal_document_confusion_matrix.run.aborted",
                        reason="schema_invalid",
                        file=config.workbook_path,
                        sheet=label,
                        missing_columns=dropped,
                        error_type=type(e).__name__,
                        schema=getattr(e.schema, "name", None),
                    )
                    return

            logger.info(
                "internal_document_confusion_matrix.load.completed",
                file=config.workbook_path,
                gt_rows=len(gt_df),
                result_rows=len(result_df),
                bytes=len(workbook_byte),
            )

        with TracedOperation("internal_document_confusion_matrix.evaluate"):
            blocks = evaluate(gt_df, result_df)

        with TracedOperation("internal_document_confusion_matrix.write"):
            sheet_name = resolve_sheet_name(sheet_names, config.eval_sheet_name)

            if sheet_name != config.eval_sheet_name:
                logger.info(
                    "internal_document_confusion_matrix.sheet.renamed",
                    requested=config.eval_sheet_name,
                    resolved=sheet_name,
                )

            updated = write_dashboard(workbook_byte, blocks, sheet_name)

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
                    "internal_document_confusion_matrix.output.skipped",
                    path=config.workbook_path,
                    sheet=sheet_name,
                )

        logger.info(
            "internal_document_confusion_matrix.run.completed",
            workbook=config.workbook_path,
            sheet=sheet_name,
            gt_rows=len(gt_df),
            result_rows=len(result_df),
            blocks=len(blocks),
            uploaded=uploaded,
        )
