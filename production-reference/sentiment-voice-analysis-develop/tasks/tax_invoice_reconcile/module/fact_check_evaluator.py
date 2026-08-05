"""Compare the extraction report against ground truth and compute per-field accuracy metrics.

Ground truth and the reconcile ``ExtractionProcessing`` frame share the same grain — one row per
(file x tax-invoice document x invoice line) — so the evaluator pairs rows on a composite
document-line key: the extension-stripped file name plus the normalized tax invoice number, copy
flag, and invoice number (the manual-evaluation ``unique_identifier``). For each scored field
(:data:`FIELD_MAPPING`) it then runs a **correct-vs-incorrect** confusion matrix over the
ground-truth rows — matching the telesale/QA convention: ``TP`` = normalized values agree,
``FP`` = they differ, ``FN = TN = 0``. A ground-truth row with no extraction partner (a missed or
mis-keyed document line) counts as ``FP`` on every field. An ``overall`` row aggregates every
field's counts (micro-average). The result is a list of metric dicts ready for the AI-Operation log.

``F1`` is computed from the raw counts as ``2·TP / (2·TP + FP + FN)`` — the form the manual
evaluation workbook states. Read the emitted numbers with the convention in mind: because
``FN = TN = 0``, precision is *identical* to accuracy, recall is 100% whenever anything matched (and
0% when nothing did), and F1 collapses to exactly ``2A / (A + 1)`` for ``A`` = accuracy — a
strictly-increasing relabelling of accuracy that always reads higher than it. **Only accuracy
carries information**; do not compare F1 against the accuracy thresholds. See ``DEVELOPER_GUIDE`` §7.3.
"""

from __future__ import annotations

import os

import pandas as pd

from src.utils.logger import Logger
from tasks.tax_invoice_reconcile.helper.constant import (
    COMPARE_BOOL,
    COMPARE_TEXT,
    FIELD_MAPPING,
    NA_SENTINEL,
    OVERALL_LABEL,
    FactCheckField,
)
from tasks.tax_invoice_reconcile.module.value_normalizer import ValueNormalizer
from tasks.tax_invoice_reconcile.schema.ground_truth import GROUND_TRUTH_FILE_KEY

logger = Logger(__name__)

_JOIN_KEY = "_join_key"
_MATCHED = "_matched"
_EXTRACTION_FILE_COLUMN = "FILE_NAME"
_KEY_SEPARATOR = "|"
_PERCENT = 100.0

# Composite-key columns beyond the file name: (GT column, extraction column, compare type).
# Together with the file key they identify one document line — the manual-evaluation
# ``unique_identifier`` (filename - tax invoice number - copy - invoice number).
_KEY_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("tax_invoice_number", "TAX_INVOICE_NUMBER", COMPARE_TEXT),
    ("copy", "COPY", COMPARE_BOOL),
    ("invoice_number", "INVOICE_NUMBER", COMPARE_TEXT),
)


class FactCheckEvaluator:
    """Score extraction output against ground truth, one metric row per field + overall."""

    def __init__(
        self,
        normalizer: ValueNormalizer | None = None,
        field_mapping: tuple[FactCheckField, ...] = FIELD_MAPPING,
    ) -> None:
        """Initialise with an injected value normalizer and the field mapping to score.

        Args:
            normalizer: The per-field-type value normalizer (defaults to a fresh
                :class:`ValueNormalizer`).
            field_mapping: The scored-field table (defaults to :data:`FIELD_MAPPING`).
        """
        self._normalizer = normalizer or ValueNormalizer()
        self._fields = field_mapping

    def evaluate(self, processing_df: pd.DataFrame, gt_df: pd.DataFrame) -> list[dict]:
        """Return per-field + overall metric dicts for the matched documents.

        Args:
            processing_df: The reconcile ``ExtractionProcessing`` frame (per document line).
            gt_df: The validated ground-truth frame (canonical ``snake_case`` columns).

        Returns:
            One dict per scored field plus a final ``label="overall"`` row; each carries
            ``label``, ``accuracy``, ``precision``, ``recall``, ``f1_score``. Empty when no
            ground-truth file appears in the extraction frame at all.
        """
        merged = self._join(processing_df, gt_df)
        if merged.empty:
            logger.warning("No documents matched ground truth by file name; nothing to score.")
            return []
        rows = merged.to_dict("records")
        results: list[dict] = []
        total_tp = total_fp = 0
        for field in self._fields:
            tp, fp = self._score_field(field, rows)
            total_tp += tp
            total_fp += fp
            results.append({"label": field.label, **self.confusion_metrics(tp, fp, 0, 0)})
        results.append({"label": OVERALL_LABEL, **self.confusion_metrics(total_tp, total_fp, 0, 0)})
        logger.info(f"Scored {len(self._fields)} fields over {len(rows)} matched document line(s).")
        return results

    def _join(self, processing_df: pd.DataFrame, gt_df: pd.DataFrame) -> pd.DataFrame:
        """Pair GT rows to extraction rows on the composite document-line key.

        Ground truth is first restricted to files present in the extraction frame (file-level
        overlap, as before), then **left**-joined on the full key so a document line the
        extraction missed or mis-keyed survives as an unpaired row (``_matched == False``) and
        is scored as incorrect, instead of silently dropping out.
        """
        proc = processing_df.copy()
        proc[_JOIN_KEY] = proc.apply(lambda row: self._composite_key(row, _EXTRACTION_FILE_COLUMN, ex_col=True), axis=1)
        proc = proc.drop_duplicates(subset=_JOIN_KEY, keep="first")
        processed_files = {self._file_key(value) for value in proc[_EXTRACTION_FILE_COLUMN]}
        gt = gt_df[gt_df[GROUND_TRUTH_FILE_KEY].map(self._file_key).isin(processed_files)].copy()
        if gt.empty:
            return gt
        gt[_JOIN_KEY] = gt.apply(lambda row: self._composite_key(row, GROUND_TRUTH_FILE_KEY, ex_col=False), axis=1)
        merged = gt.merge(proc, on=_JOIN_KEY, how="left", suffixes=("_gt", "_ex"), indicator=_MATCHED)
        merged[_MATCHED] = merged[_MATCHED].eq("both")
        return merged

    def _composite_key(self, row: pd.Series, file_column: str, ex_col: bool) -> str:
        """Return the document-line join key: file key + normalized key-column values."""
        parts = [self._file_key(row.get(file_column))]
        for gt_column, extraction_column, compare in _KEY_COLUMNS:
            column = extraction_column if ex_col else gt_column
            parts.append(self._normalizer.normalize(row.get(column), compare))
        return _KEY_SEPARATOR.join(parts)

    def _score_field(self, field: FactCheckField, rows: list[dict]) -> tuple[int, int]:
        """Return ``(TP, FP)`` for one field; unpaired GT rows count as ``FP`` on every field."""
        tp = 0
        unparseable = 0
        for row in rows:
            if not row.get(_MATCHED, False):
                continue
            raw_gt = row.get(field.gt_field)
            gt_norm = self._normalizer.normalize(raw_gt, field.compare)
            unparseable += int(self._is_unparseable(raw_gt, gt_norm))
            ex_norms = {self._normalizer.normalize(row.get(col), field.compare) for col in field.extraction_columns}
            tp += int(self._is_match(gt_norm, ex_norms))
        if unparseable:
            logger.warning(
                f"{unparseable} ground-truth cell(s) for '{field.label}' hold a value that does not parse as "
                f"'{field.compare}'; they are scored as blank. Check the ground-truth workbook."
            )
        return tp, len(rows) - tp

    @staticmethod
    def _is_match(gt_norm: str, ex_norms: set[str]) -> bool:
        """Return True when the extraction agrees with ground truth for one field.

        A populated GT value matches when **any** candidate column carries it — the Thai/English
        pair fields hold the value in whichever language the document used. A blank GT value
        matches only when **every** candidate column is blank: a null sibling column contributes
        :data:`NA_SENTINEL` to ``ex_norms``, which would otherwise let a hallucinated value in the
        populated column score as correct against a blank label.
        """
        if gt_norm == NA_SENTINEL:
            return all(value == NA_SENTINEL for value in ex_norms)
        return gt_norm in ex_norms

    @staticmethod
    def _is_unparseable(raw: object, normalized: str) -> bool:
        """Return True when a populated GT cell normalized to the blank sentinel.

        An unparseable date / bool token / digit-free tax id collapses to :data:`NA_SENTINEL` and is
        then scored as a labelled blank. That is a ground-truth data bug, and it is otherwise silent.
        """
        if normalized != NA_SENTINEL or raw is None:
            return False
        try:
            if pd.isna(raw):
                return False
        except (TypeError, ValueError):
            pass
        return bool(str(raw).strip())

    @staticmethod
    def _file_key(value: object) -> str:
        """Return the extension-stripped, casefolded file name used to join GT and extraction."""
        if value is None or pd.isna(value):
            return ""
        return os.path.splitext(str(value))[0].strip().casefold()

    @staticmethod
    def confusion_metrics(tp: int, fp: int, fn: int, tn: int) -> dict[str, float]:
        """Return accuracy/precision/recall/f1 (percentages, 2dp) from a confusion matrix.

        F1 is taken straight from the raw counts — ``2·TP / (2·TP + FP + FN)``, the form the manual
        evaluation workbook states — rather than as the harmonic mean of ``precision`` and ``recall``.
        The two are algebraically identical, but the raw-count form does not silently depend on those
        two being passed in unrounded. Each metric is ``0.0`` when its denominator is zero.
        """
        total = tp + fp + fn + tn
        f1_den = 2 * tp + fp + fn
        accuracy = (tp + tn) / total * _PERCENT if total else 0.0
        precision = tp / (tp + fp) * _PERCENT if (tp + fp) else 0.0
        recall = tp / (tp + fn) * _PERCENT if (tp + fn) else 0.0
        f1 = 2 * tp / f1_den * _PERCENT if f1_den else 0.0
        return {
            "accuracy": round(accuracy, 2),
            "precision": round(precision, 2),
            "recall": round(recall, 2),
            "f1_score": round(f1, 2),
        }
