"""Score the retention internal model result sheet against ground truth, and write the evaluation dashboard.

Reads the workbook ``internal_asr_llm_output.py`` produced and appends an ``Evaluation Dashboard`` plus the
per-call compare sheets behind it. This is the **only** scorer for MNP -- production runs one
evaluation, and so do we.

The scoring logic is production's, from ``sentiment-batch-retention`` ``src/modules/fact_checker.py``.
The maths lives in :mod:`src.google_model.metrics`; :func:`evaluate` takes DataFrames and nothing
else, so the whole scoring path runs offline with no credentials.

**The label sets are imported from the schema**, not written out again here: a second copy would
drift the moment the prompt gains a category, and the two would disagree silently. That also means
the vocabulary this module scores against is exactly the vocabulary the model was constrained to
generate. Presentation *order* comes from :mod:`src.google_model.reason_classes`, which is not the
enum's order -- both production evaluators and both stakeholder workbooks put ``other`` eighth.

Four things about this design are load-bearing:

* **Reasons are scored as a SET, with rank discarded.** ``reason_main``, ``reason_secondary`` and
  ``reason_third`` are unioned per call and each category scored by presence, which is what
  production does. Scoring the three ranks separately -- the previous design here -- made a model
  that named exactly the right reasons in a different order score zero on all three. On this corpus
  41 of 100 calls carry more than one reason, so that was not a corner case.
* **Cells are matched whole, never split on commas.** Production's ``get_reasons_set`` splits each
  cell on ``,``, which shatters ``true point, dtac reward`` into two fragments matching nothing, so
  that category can never score a true positive there. The MNP ground truth uses it on one call, so
  the bug is real and is not reproduced here.
* **Case and stray spaces fold; misspellings do not.** A cell matches the schema's spelling
  case-insensitively, which is what rescues the ground truth's ``Network`` and ``"save cost "``.
  ``Contact end`` (contract) and ``cutomer reason`` (customer) match nothing and land in the
  :data:`~src.google_model.reason_classes.UNKNOWN_CLASS` row, counted and logged. They are **not**
  corrected here: that is a business judgement and an edit to the ground-truth tab. They are not
  silently dropped either -- between them they would cost ``contract end`` and ``customer reason``
  a third and a half of their recall with nothing on the sheet to say why.
* **Every block names the ground-truth columns it scores**, in a caption under its title, so no
  number on the dashboard is unattributable. See :data:`BLOCK_SOURCES`.

The outcome vocabulary keeps production's ``undefine`` spelling (no trailing "d"). Production's own
``call_result_calculation`` scores ``undefined`` against it, so every real ``undefine`` is silently
excluded from its metrics -- another bug not reproduced here.
"""

import io
import os
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, get_args

import pandas as pd
from openpyxl.formatting.rule import ColorScaleRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo
from pandera.errors import SchemaError, SchemaErrors

from src.google_model.metrics import (
    ClassificationReport,
    ErrorRateReport,
    LabelScore,
    MultiLabelReport,
    SimilarityReport,
    aggregate_scores,
    character_error_rate,
    classification_report,
    cosine_similarity,
    error_rate_stats,
    mean_pool,
    multilabel_report,
    similarity_stats,
)
from src.google_model.reason_classes import UNKNOWN_CLASS, class_label, order_classes
from src.google_model.sentiment_retention.schema.input_gt_schema import InputGTSchema
from src.hook.gcp_embedding import VertexAIEmbedding
from src.hook.sharepoint import SharePointModule
from src.local_model.sentiment_retention.schema.model_response import (
    ProductCategory,
    ReasonCategory,
    RetentionOutcomeCategory,
)
from src.local_model.sentiment_retention.schema.output_schema import OutputSchema
from src.logger import Logger, TracedOperation

logger = Logger.get_logger(__name__)

KEY_COLUMN = "Voice File Name"

# Sheet spellings, in sheet order. "sumary" is the ground-truth tab's own typo; the sheet's
# spelling wins, because evaluate() joins the two sheets by identical column name.
OUTCOME_COLUMN = "call_sumary_call_result"

REASON_COLUMNS: tuple[str, ...] = (
    "reason_main",
    "reason_secondary",
    "reason_third",
)

# Read off the schema rather than written out again -- see the module docstring.
OUTCOME_LABELS: tuple[str, ...] = get_args(RetentionOutcomeCategory)

# The class standing for "the model produced no row for this product at all". The join is outer,
# so a product the model never mentioned leaves a row with a blank prediction; without this it
# would be excluded, and a missed product would cost the Call Result topic nothing.
MISSING_LABEL = "(missing)"
OUTCOME_SCORED_LABELS: tuple[str, ...] = (*OUTCOME_LABELS, MISSING_LABEL)

# Retention's third scored topic. MNP has no equivalent: its schema carries no product grain at
# all, so its dashboard has two topics where this one has three.
PRODUCT_COLUMN = "call_sumary_product"
PRODUCT_LABELS: tuple[str, ...] = get_args(ProductCategory)
# The catch-all for a graded product outside the vocabulary, mirroring the reason topic's.
UNKNOWN_PRODUCT = "unknown (out-of-vocabulary)"
PRODUCT_SCORED_LABELS: tuple[str, ...] = (*PRODUCT_LABELS, UNKNOWN_PRODUCT)

# The twelve reason classes in registry order -- `other` at 8, not last as the enum has it. See
# src/google_model/reason_classes.py for why the presentation order is not the schema's.
REASON_CLASSES: tuple[str, ...] = order_classes(get_args(ReasonCategory))

# The separator packing several reasons into one cell. A control character, never a comma:
# `true point, dtac reward` contains a comma, and splitting on one is precisely why production
# cannot score that class. Only retention's duplicate-row fold ever writes it; here nothing does,
# and _reason_set's split is a no-op.
POOL_SEPARATOR = "\x1f"

# Which columns _score_reason_set reads. NOT the three ranks: this use case folds rows sharing a
# (call, product) key, and unioning two ground-truth rows can yield more reasons than there are
# ranks. _merge packs the union into this one column; the ranks survive untouched for the compare
# sheet, which still shows what the workbook actually held.
REASON_POOL_COLUMN = "reason_pool"
REASON_SCORING_COLUMNS: tuple[str, ...] = (REASON_POOL_COLUMN,)
# UNKNOWN_CLASS is scored like any other class so a ground-truth typo lands somewhere visible
# instead of vanishing. It carries no class number: the prompt cannot ask for it.
REASON_SCORED_CLASSES: tuple[str, ...] = (*REASON_CLASSES, UNKNOWN_CLASS)

TOPIC_OUTCOME = "Call Result"
TOPIC_REASON = "Reason"
TOPIC_PRODUCT = "Product"

# Block titles, also the keys of the dict evaluate() returns. Insertion order is sheet order.
BLOCK_HEADER = "COMPARISON"
BLOCK_SUMMARY = "EVALUATION SUMMARY"
BLOCK_OUTCOME = "CALL RESULT (per class)"
BLOCK_REASONS = "CANCELLATION REASON (per class)"
BLOCK_PRODUCT = "PRODUCT (per class)"
BLOCK_LEGEND = "HOW TO READ THIS DASHBOARD"

# Keys of the per-call compare frames, mapped onto real sheet names by Config.
COMPARE_OUTCOME = "outcome"
COMPARE_REASONS = "reasons"
COMPARE_PRODUCT = "product"

# The two verdicts a Compare cell can hold, spelled as the stakeholders' workbook spells them so
# the two can be read side by side without a mental translation.
MATCH_TRUE = "T"
MATCH_FALSE = "F"

# Sub-headers of a compare sheet's three-cell group, in order.
COMPARE_SUBHEADERS: tuple[str, ...] = ("GT", "AI", "Compare")

# The compare sheets' fixed leading columns, spanning both header rows.
COMPARE_INDEX_COLUMNS: tuple[str, ...] = ("No", KEY_COLUMN)

# The reasons compare sheet's last group: the sets the dashboard actually scored. The three rank
# groups before it are diagnosis, not score -- seeing `reason_main F` beside `reasons (set) T` is
# what makes "the model was right, it just ranked differently" legible on one row.
COMPARE_SET_GROUP = "reasons (set)"

# The provenance line printed under a block's title, so no number on the sheet is unattributable
# to a ground-truth column. Blocks absent from this mapping get no subtitle row.
BLOCK_SOURCES: dict[str, str] = {
    BLOCK_OUTCOME: f"GT column: {OUTCOME_COLUMN}",
    BLOCK_REASONS: (
        "GT columns: "
        + " + ".join(REASON_COLUMNS)
        + "  --  scored as a SET; rank is ignored, exactly as production scores it"
    ),
    BLOCK_PRODUCT: (
        f"GT column: {PRODUCT_COLUMN}  --  scored as a SET per CALL, on its own join. "
        "It is the other two blocks' join key, so it cannot be scored there."
    ),
}


# --- Transcript scoring -----------------------------------------------------------------------

# Appended after the scored-column blocks and before the legend, and only when a run supplies
# both transcript mappings and an embedder -- see evaluate(). Neither ground-truth tab has a
# transcript column, so the human transcripts come from their own SharePoint folder.
FAMILY_TRANSCRIPT = "Transcript"
BLOCK_TRANSCRIPT = "TRANSCRIPT"

# Chunk size for embedding a transcript, in characters. gemini-embedding-001 caps input at 2048
# tokens; Thai runs roughly 1.5-3 characters per token, so 2000 characters leaves headroom at the
# dense end. Under-sizing costs a few more requests, over-sizing costs a 400 -- which is the point
# of VertexAIEmbedding.DEFAULT_AUTO_TRUNCATE being False.
DEFAULT_CHUNK_CHARS = 2000

# CER at or below which a transcript counts as a pass. Note the direction: this is the one metric
# on the sheet where a *smaller* number is better.
DEFAULT_CER_THRESHOLD = 0.20

# A callable taking texts and returning one vector each, in input order. Named so evaluate() can
# be handed VertexAIEmbedding.embed_texts in production and a stub in a test.
Embedder = Callable[[Sequence[str]], list[list[float]]]

NOT_APPLICABLE = "n/a"

# --------------------------------------------------------------------------------------------
# Presentation
# --------------------------------------------------------------------------------------------

# Every block except these two becomes a real Excel table (banded rows, filter dropdowns, a name
# formulas can reference). COMPARISON is a one-row banner, where a filter dropdown is noise, and
# the legend uses merged cells, which a table cannot contain.
UNTABLED_BLOCKS = frozenset({BLOCK_HEADER, BLOCK_LEGEND})

TABLE_STYLE = "TableStyleMedium2"

TITLE_FILL = PatternFill("solid", fgColor="1F3864")
TITLE_FONT = Font(bold=True, color="FFFFFF", size=11)
BANNER_FILL = PatternFill("solid", fgColor="D9E2F3")
# Greyed and italic so an inapplicable cell reads as "not measured here" rather than as a score
# of zero -- the distinction the legend's n/a entry exists to make.
MUTED_FONT = Font(color="808080", italic=True)

# Compare-sheet palette. Static fills, not conditional formatting -- see _style_compare_sheet.
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
# The provenance caption under a block title -- see BLOCK_SOURCES.
SOURCE_FONT = Font(color="1F3864", italic=True, size=9)
TAB_COLOR = "1F3864"

# Excel's own three-colour-scale stops, so the sheet looks native rather than themed by hand.
SCALE_LOW, SCALE_MID, SCALE_HIGH = "F8696B", "FFEB84", "63BE7B"

LABEL_COLUMN_WIDTH = 30
DATA_COLUMN_WIDTH = 13
# Columns the legend's Meaning cell is merged across. Its own column would otherwise have to be
# ~110 wide, and column B is "Scored" in the tables above it.
LEGEND_SPAN = 9
LEGEND_LINE_CHARS = 115
LEGEND_LINE_HEIGHT = 15

# Matched on the whole name and on the last word, because the per-column blocks prefix every
# per-class column with its label -- "F1" and "network F1" are the same metric and must format
# alike.
RATIO_SUFFIXES = frozenset({"Precision", "Recall", "F1"})
RATIO_COLUMNS = frozenset({"Accuracy"}) | RATIO_SUFFIXES
COUNT_SUFFIXES = frozenset({"TP", "TN", "FP", "FN", "Support"})
# "Scored" and "Excluded" are counts, not scores. Left out, "Scored" has no suffix match and
# would get no format at all.
COUNT_COLUMNS = frozenset({"N", "Scored", "Excluded", "Rows"}) | COUNT_SUFFIXES

# Rows of a Metric/Value block whose Value is a 0-1 ratio. Matched by name rather than position
# so reordering a block cannot silently colour a count on a 0-1 scale.
RATIO_METRICS = frozenset({"Exact set match", "Micro-F1", "Micro-Precision", "Micro-Recall",
                           "Macro-F1"})

# The transcript block's Metric/Value rows that hold a 0-1 ratio on the normal scale. Kept as its
# own set rather than merged into RATIO_METRICS above, so the whole transcript graft reads as one
# contiguous addition to a file that scored no transcripts before.
TRANSCRIPT_RATIO_METRICS = frozenset(
    {
        "Mean cosine",
        "Median cosine",
        "Lowest cosine",
        "Highest cosine",
        "Mean char accuracy (1 - CER)",
    }
)

# CER rows. Ratios like the sets above -- so they format at 4dp and get a colour scale -- but the
# scale is REVERSED, because 0.04 is an excellent transcript and the shared scale paints 0 red.
INVERTED_RATIO_METRICS = frozenset(
    {
        "Mean CER",
        "Median CER",
        "Best CER (lowest)",
        "Worst CER (highest)",
    }
)

# A tuple, not a single string: "CER pass rate (<= 0.20)" does not start with "Pass rate", and as
# a bare prefix it falls through to COUNT_FORMAT and renders 0.6141 as 1.
RATIO_METRIC_PREFIXES = ("Pass rate", "CER pass rate")

RATIO_FORMAT = "0.0000"
COUNT_FORMAT = "0"

# The legend lives here, beside the code computing each metric, rather than in a docstring nobody
# opens. It is the last block on the sheet.
LEGEND: tuple[tuple[str, str], ...] = (
    (
        "How this sheet scores",
        "Two topics are scored. CALL RESULT compares one label per call. CANCELLATION REASON "
        "unions the three ranked reason cells into a SET per call and then asks, for each "
        "category, whether each side named it. RANK IS IGNORED: naming the right reasons in a "
        "different order is right, which is how production scores it. Every block says which "
        "ground-truth columns it read, in the caption under its title.",
    ),
    (
        "Class",
        "The category's fixed number, shared with the retention dashboard and with the "
        "stakeholders' workbook so the same reason is the same class everywhere. The numbering "
        "is not the schema's order - `other` is class 8, not last. Retention has no class 11, so "
        "that gap is expected rather than missing.",
    ),
    (
        "N",
        "Rows this class was scored over. Equal to TP+FP+FN+TN on every row, by construction. It "
        "is the same for every class inside a block, and differs BETWEEN blocks: a call with no "
        "ground-truth reason at all is dropped from the reason topic only, matching production.",
    ),
    (
        "Support",
        "How many calls the human put in this class (= TP+FN). Read every score next to it. Most "
        "categories here are graded on a handful of calls, so one disagreement moves an F1 by a "
        "lot, and a class with support 1 tells you almost nothing on its own.",
    ),
    (
        "Accuracy",
        "(TP+TN)/N for this class alone - how often the model was right about THIS category. "
        "CAREFUL: TN dominates for a rare class, so a model that never predicts a category "
        "appearing on 3 of 100 calls still scores about 0.97 here. It is on the sheet because "
        "the stakeholders' report has it. Precision and Recall are what actually expose that "
        "model; never read Accuracy alone.",
    ),
    (
        "Precision",
        "TP/(TP+FP): when the model named this category, how often the human agreed. Low "
        "precision means the model over-uses the category.",
    ),
    (
        "Recall",
        "TP/(TP+FN): of the calls the human put in this category, how many the model found. Low "
        "recall means the model misses it.",
    ),
    (
        "F1",
        "The harmonic mean of Precision and Recall - one number that only rises when both do. "
        "This is the per-class score to read first.",
    ),
    (
        "Macro-average",
        "Every class weighted equally, no matter how rare. THIS IS THE NUMBER THAT EXPOSES A "
        "MODEL THAT ONLY HANDLES THE COMMON CATEGORIES: it scores high Micro and low Macro.",
    ),
    (
        "Micro-average",
        "Every individual decision pooled, so frequent categories dominate. Closest to how often "
        "the model was right overall.",
    ),
    (
        "Weighted-average",
        "Each class scaled by its Support - between Macro and Micro. This is the row the summary "
        "block at the top reports, and the one the stakeholders' dashboard quotes.",
    ),
    (
        "Why Macro and Micro accuracy are identical",
        "Not a bug and not a copy-paste. Every class in a block is scored over the same rows, so "
        "the mean of (TP+TN)/N equals the pooled (sum TP + sum TN)/(classes x N). The same "
        "algebra makes Weighted recall equal Micro recall. They are printed separately because "
        "the stakeholders' report prints them separately.",
    ),
    (
        "unknown (out-of-vocabulary)",
        "A graded value the schema has no category for - a typo on the ground-truth tab. It is "
        "scored as its own row so it is VISIBLE: the MNP tab holds `contact end` (contract) and "
        "`cutomer reason` (customer), which between them cost `contract end` and `customer "
        "reason` a third and a half of their recall. Fixing it is an edit to the tab, not to "
        "this code. This row has no class number because the prompt cannot ask for it.",
    ),
    (
        "Counts on the average rows",
        "TP/FP/FN/TN/N/Support on the three average rows are the POOLED totals over the classes "
        "that occur - one pool, three ways of averaging it. They are not a fourth score.",
    ),
    (
        "CER (Character Error Rate)",
        "Transcript only. The share of characters the model got wrong against the human "
        "transcript, counting insertions, deletions and substitutions. LOWER IS BETTER - this is "
        "the only metric on this sheet that reads that way, and its colours are reversed to "
        "match: green is a low CER. 0.00 is a perfect transcription. It can exceed 1.00 when the "
        "model produced far more text than the human did.",
    ),
    (
        "Char accuracy (1 - CER)",
        "Transcript only. The same number stated the usual way round, so it can be compared "
        "against the Accuracy column above it. 0.90 means roughly 9 characters in 10 are right.",
    ),
    (
        "CER pass rate",
        "Transcript only. Share of calls whose CER is at or BELOW the threshold - the opposite "
        "direction to every other pass rate, because a low CER is the good outcome.",
    ),
    (
        "Cosine vs CER",
        "Transcript only. Both sides transcribe the same call, so cosine sits near 1.00 for "
        "almost every row and mostly confirms the two files belong together. CER is the number "
        "that separates a good transcription from a poor one - read that one.",
    ),
    (
        "Chunks embedded",
        "Transcript only. A transcript longer than the embedding model's input limit is split "
        "into pieces, each embedded, and the results averaged in proportion to their length. "
        "This count is how many pieces that took. Nothing is truncated or dropped.",
    ),
    (
        NOT_APPLICABLE,
        "Metric does not apply on this row - e.g. the scores of a label neither side ever used.",
    ),
)


@dataclass
class Config:
    """Runtime configuration, mirroring
    :class:`src.local_model.sentiment_retention.internal_asr_llm_output.Config`."""

    # SharePoint location of the workbook internal_asr_llm_output.py wrote. run_prefix is the run's
    # timestamp folder and is filled in by hand before each evaluation.
    dest_file: str = "/poc_internal_model_migration/voicefiles_retention_internal_output"
    run_prefix: str = ""
    output_file_name: str = "model_comparison.xlsx"

    gt_sheet_name: str = "Voice_retention - Groundtruth"
    result_sheet_name: str = "Voice_retention - Internal Model Result"

    # The three sheets a run appends. resolve_sheet_names() gives them a shared numeric suffix
    # when any one of them is taken, so a run's output is always identifiable as one set.
    eval_sheet_name: str = "Evaluation Dashboard"
    outcome_compare_sheet_name: str = "Call Result Compare Result"
    reasons_compare_sheet_name: str = "Reasons Compare Result"
    product_compare_sheet_name: str = "Product Compare Result"

    # Transcript scoring. Neither ground-truth tab has a transcript column, so the human
    # transcripts come from their own SharePoint folder; the model's own land in ``run_folder``,
    # written there by the output pipeline as one <stem>.txt per call.
    gt_transcript_path: str = "/poc_internal_model_migration/voicefiles_retention/Transcript"

    # Vertex embedding configuration. "global" is valid for the standard embedding models; a
    # model not enabled there answers 404, in which case set a region such as "us-central1".
    embedding_model: str = "gemini-embedding-001"
    embedding_location: str = "global"
    embedding_concurrency: int = 8
    similarity_threshold: float = 0.80

    # chunk_chars is the embedding split size; lower it if a live run trips a 400 on input
    # length, which would mean Thai tokenises denser than assumed.
    transcript_chunk_chars: int = DEFAULT_CHUNK_CHARS
    transcript_cer_threshold: float = DEFAULT_CER_THRESHOLD
    # SharePoint download fan-out. ~200 transcripts at two round-trips each is minutes of pure
    # latency sequentially, and the work is entirely I/O-bound.
    transcript_concurrency: int = 8

    timezone: str = "Asia/Bangkok"

    @property
    def run_folder(self) -> str:
        """SharePoint folder holding this run's outputs.

        The single place ``run_prefix`` is joined, so the workbook path cannot resolve it
        differently from anything else that needs the folder.
        """
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


def _stem(value: Any) -> str:
    """Key a workbook row onto one identity, extension dropped.

    The MNP ground-truth sheet names calls by a bare ID while the result sheet carries the GCS
    object name (``<id>.wav``), so a join on the raw string matches nothing at all. Folding both
    to the stem is what makes the two sheets meet.
    """
    return Path(_clean(value)).stem


def _canonical(value: Any, by_lower: Mapping[str, str], blank_label: str | None) -> str | None:
    """Fold one cell onto the schema's spelling of its label.

    Case and surrounding whitespace are formatting and fold away -- this is what makes the
    ground truth's ``Network`` and ``"save cost "`` score against the schema's ``network`` and
    ``save cost``. Vocabulary does not fold: anything else returns None for the caller to
    exclude and count, never guessed into a neighbouring category.

    Args:
        value: The raw cell.
        by_lower: ``{lowercased label: label}`` for the column's label set.
        blank_label: What an empty cell means -- :data:`NONE_LABEL` on a column where "no
            answer" is itself a graded answer, or None where a blank is a fault to exclude.

    Returns:
        The canonical label, or None when the cell is outside the label set.
    """
    text = _clean(value)
    if not text:
        return blank_label
    return by_lower.get(text.casefold())


def _score_single_label(
    merged: pd.DataFrame,
    column: str,
    labels: Sequence[str],
    *,
    blank_label: str | None = None,
    missing_label: str | None = None,
) -> tuple[ClassificationReport, int]:
    """Score one single-label column, excluding rows either side graded outside ``labels``.

    An out-of-vocabulary value is a ground-truth data-entry fault, not a model error: the row is
    dropped from this column only, and the count reaches the dashboard's ``Scored``/``Excluded``
    cells.

    ``blank_label`` decides what an empty cell means on both sides.

    ``missing_label`` decides what an empty *prediction* means, and is what makes an outer join
    scoreable: the model produced no row for this key, which is a wrong answer rather than an
    absent one. A blank on the GROUND TRUTH side is still a drop -- there is nothing to be right
    or wrong about -- so an invented key costs its own topic and is not charged twice here.

    Returns:
        ``(report, dropped)``.
    """
    by_lower = {label.casefold(): label for label in labels}
    y_true: list[str] = []
    y_pred: list[str] = []
    dropped = 0

    for raw_true, raw_pred in zip(merged[f"{column}_gt"], merged[f"{column}_pred"], strict=True):
        true = _canonical(raw_true, by_lower, blank_label)
        if true is None:
            dropped += 1
            continue

        pred = _canonical(raw_pred, by_lower, blank_label)
        if pred is None:
            # An empty prediction is a miss when the caller supplies a label for it; anything
            # else unrecognised is a data fault and leaves the row out of this column.
            if missing_label is not None and not _clean(raw_pred):
                pred = missing_label
            else:
                dropped += 1
                continue

        y_true.append(true)
        y_pred.append(pred)

    if dropped:
        # The count and the column, never the value. A stray cell can hold call content, and
        # this log is indexed by Cloud Logging.
        logger.warning(
            "internal_sentiment_retention_confusion_matrix.column.excluded",
            column=column,
            dropped=dropped,
            scored=len(y_true),
        )

    return classification_report(y_true, y_pred, labels), dropped


def _metric_cells(score: LabelScore, *, used: bool) -> dict[str, Any]:
    """The four score cells for one class, blanked (n/a) when neither side used it.

    Zeros from all-zero denominators would paint a class nobody used solid red; the counts beside
    them (``TP 0 / Support 0``) show *why* the score is blank. Accuracy is blanked with the rest
    even though ``(tp+tn)/n`` is well defined at 1.0 for an unused class -- printing a green
    1.0000 for a category the prompt never had to find is the most flattering number on the sheet
    and the least informative.
    """
    if not used:
        return {
            metric: NOT_APPLICABLE
            for metric in ("Accuracy", "Precision", "Recall", "F1")
        }
    return {
        "Accuracy": round(score.accuracy, 4),
        "Precision": round(score.precision, 4),
        "Recall": round(score.recall, 4),
        "F1": round(score.f1, 4),
    }


def _per_class_block(
    per_label: Sequence[LabelScore], *, label_header: str, numbered: bool = True
) -> pd.DataFrame:
    """One row per class, then the Macro / Micro / Weighted footer.

    The shape the stakeholders' workbook uses, and the reason it beats the label-as-column layout
    this module used before: twelve reason classes times eight metrics is ninety-six columns
    nobody can read, while twelve rows fit on a screen.

    Counts on the three footer rows are the pooled totals over the classes that occur -- the same
    pool all three average, differing only in how. ``Class`` carries the class count there instead
    of a number, because an average has no class number.

    Args:
        per_label: One score per class, already in presentation order.
        label_header: Column name for the class's own name -- "Reason", "Label", "Product".
        numbered: Whether to print registry class numbers. False for the outcome block, whose
            labels have no place in the reason registry.

    Returns:
        A DataFrame of ``len(per_label) + 3`` rows.
    """
    rows: list[dict[str, Any]] = []

    for position, score in enumerate(per_label, start=1):
        rows.append(
            {
                "Class": class_label(score.label) if numbered else f"Class {position}",
                label_header: score.label,
                "TP": score.tp,
                "FP": score.fp,
                "FN": score.fn,
                "TN": score.tn,
                "N": score.n,
                "Support": score.support,
                **_metric_cells(score, used=score.occurs),
            }
        )

    aggregates = aggregate_scores(per_label)
    occurring = [score for score in per_label if score.occurs]
    pooled_tp = sum(score.tp for score in occurring)
    pooled_fp = sum(score.fp for score in occurring)
    pooled_fn = sum(score.fn for score in occurring)
    pooled_tn = sum(score.tn for score in occurring)

    for average in aggregates.rows():
        rows.append(
            {
                "Class": f"{len(average.labels)} classes",
                label_header: average.kind,
                "TP": pooled_tp,
                "FP": pooled_fp,
                "FN": pooled_fn,
                "TN": pooled_tn,
                "N": pooled_tp + pooled_fp + pooled_fn + pooled_tn,
                "Support": pooled_tp + pooled_fn,
                "Accuracy": round(average.accuracy, 4),
                "Precision": round(average.precision, 4),
                "Recall": round(average.recall, 4),
                "F1": round(average.f1, 4),
            }
        )

    return pd.DataFrame(rows)


def _reason_set(
    row: pd.Series, columns: Sequence[str], by_lower: Mapping[str, str]
) -> tuple[set[str], int]:
    """Fold one side's three ranked reason cells into one unordered set.

    Rank is discarded, which is the whole point: production scores the union, so a model that
    names exactly the right reasons in a different order is right, not wrong three times.

    Cells are matched **whole** against the vocabulary and never split on commas. Production
    splits ([mnp fact_checker.py] ``get_reasons_set``), which shatters ``true point, dtac reward``
    into two fragments that match nothing, so that class can never score a TP there. The MNP
    ground truth uses it on one call, so the bug is not academic and is not reproduced.

    An unrecognised value becomes :data:`UNKNOWN_CLASS` rather than being dropped -- a grader's
    typo has to land somewhere a reader can see it.

    Returns:
        ``(labels, unknown_cells)``.
    """
    labels: set[str] = set()
    unknown = 0

    for column in columns:
        cell = _clean(row[column])
        if not cell:
            continue
        # Split on the pool separator only. A cell normally holds exactly one label, so this is a
        # one-element loop; it is several only where a fold packed a unioned pool into one cell.
        for text in cell.split(POOL_SEPARATOR):
            text = text.strip()
            if not text:
                continue
            label = by_lower.get(text.casefold())
            if label is None:
                unknown += 1
                labels.add(UNKNOWN_CLASS)
            else:
                labels.add(label)

    return labels, unknown


def _score_reason_set(merged: pd.DataFrame) -> tuple[MultiLabelReport, dict[str, int]]:
    """Score the three reason ranks as one set per call.

    Rows whose ground-truth set is empty are dropped, matching production: a call the graders
    gave no reason at all is not evidence about the model. The count is returned rather than
    absorbed, because it silently shrinks the denominator behind every class.

    Returns:
        ``(report, counts)`` with ``gt_empty``, ``gt_unknown`` and ``pred_unknown`` cell counts.
    """
    by_lower = {label.casefold(): label for label in REASON_CLASSES}
    gt_columns = [f"{column}_gt" for column in REASON_SCORING_COLUMNS]
    pred_columns = [f"{column}_pred" for column in REASON_SCORING_COLUMNS]

    y_true: list[set[str]] = []
    y_pred: list[set[str]] = []
    counts = {"gt_empty": 0, "gt_unknown": 0, "pred_unknown": 0}

    for _, row in merged.iterrows():
        true, true_unknown = _reason_set(row, gt_columns, by_lower)
        pred, pred_unknown = _reason_set(row, pred_columns, by_lower)

        if not true:
            counts["gt_empty"] += 1
            continue

        counts["gt_unknown"] += true_unknown
        counts["pred_unknown"] += pred_unknown
        y_true.append(true)
        y_pred.append(pred)

    if counts["gt_unknown"]:
        # WARNING and counts only: the value itself can hold call content, and a ground-truth
        # value outside the vocabulary costs its intended class real recall with nothing on the
        # sheet to explain it. The UNKNOWN_CLASS row is the visible half of this.
        logger.warning(
            "internal_sentiment_retention_confusion_matrix.reasons.gt_out_of_vocabulary",
            cells=counts["gt_unknown"],
            scored=len(y_true),
        )
    if counts["gt_empty"]:
        logger.warning(
            "internal_sentiment_retention_confusion_matrix.reasons.gt_empty",
            rows=counts["gt_empty"],
            scored=len(y_true),
        )

    return multilabel_report(y_true, y_pred, REASON_SCORED_CLASSES), counts


def chunk_text(text: str, limit: int) -> list[str]:
    """Split a text into chunks of at most ``limit`` characters, on line boundaries.

    One speaker turn per line, so a newline break never cuts an utterance in half. An over-long
    line is hard-split rather than emitted oversized (``auto_truncate=False`` would 400).
    Concatenation of the chunks is the input; blank text yields ``[]``. Raises ValueError on a
    non-positive ``limit``.
    """
    if limit < 1:
        raise ValueError(f"limit must be >= 1, got {limit}.")

    if not text.strip():
        return []

    chunks: list[str] = []
    current = ""

    for line in text.splitlines(keepends=True):
        # A single over-long line: flush what is held, then carve the line down to size.
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]

        if len(current) + len(line) > limit:
            chunks.append(current)
            current = line
        else:
            current += line

    if current:
        chunks.append(current)

    # A trailing chunk of pure whitespace embeds to nothing and would be rejected as blank text.
    # Dropped only at the end, so the concatenation property still holds for every real chunk.
    return [chunk for chunk in chunks if chunk.strip()]


def embed_pooled(
    texts: Sequence[str], *, embedder: Embedder, chunk_chars: int
) -> tuple[list[list[float]], int]:
    """Embed texts of any length; returns ``(pooled_vectors, chunks_embedded)``.

    Every chunk of every text goes out in a **single** ``embedder`` call, then the flat result is
    re-split by per-text chunk counts and mean-pooled weighted by chunk length. Raises ValueError
    on a blank text or a wrong vector count.
    """
    if not texts:
        return [], 0

    flat: list[str] = []
    counts: list[int] = []

    for index, text in enumerate(texts):
        chunks = chunk_text(text, chunk_chars)
        if not chunks:
            raise ValueError(f"Cannot embed blank text at position {index}.")
        flat.extend(chunks)
        counts.append(len(chunks))

    vectors = embedder(flat)

    # Checked rather than trusted: a short or long result shifts every subsequent text's slice by
    # one chunk, which does not raise anywhere downstream -- it just scores the wrong pairs.
    if len(vectors) != len(flat):
        raise ValueError(f"Embedder returned {len(vectors)} vectors for {len(flat)} chunks.")

    pooled: list[list[float]] = []
    offset = 0

    for count in counts:
        group = vectors[offset : offset + count]
        # A single-chunk text is passed through rather than pooled: mean_pool would normalise it,
        # and cosine_similarity normalises again anyway, so the round trip only costs precision.
        weights = [len(chunk) for chunk in flat[offset : offset + count]]
        pooled.append(mean_pool(group, weights) if count > 1 else list(group[0]))
        offset += count

    return pooled, len(flat)


def _score_transcript(
    merged: pd.DataFrame,
    gt_transcripts: Mapping[str, str],
    pred_transcripts: Mapping[str, str],
    *,
    embedder: Embedder,
    threshold: float,
    cer_threshold: float,
    chunk_chars: int,
) -> tuple[SimilarityReport, ErrorRateReport, int, int]:
    """Score the joined calls' transcripts by embedding cosine and by CER.

    Cosine mostly confirms the two files belong to the same call; CER is what separates an
    accurate transcription from a poor one.

    Returns:
        ``(similarity, error_rate, excluded, chunks_embedded)`` -- a call missing or blank on
        either side is excluded and counted. Raises ValueError on a wrong vector count.
    """
    gt_texts: list[str] = []
    pred_texts: list[str] = []
    excluded = 0

    for raw_key in merged[KEY_COLUMN]:
        stem = _stem(raw_key)
        true = _clean(gt_transcripts.get(stem, ""))
        pred = _clean(pred_transcripts.get(stem, ""))

        if not true or not pred:
            excluded += 1
            continue

        gt_texts.append(true)
        pred_texts.append(pred)

    if excluded:
        # Counts only. A transcript is the raw call content -- the PII the pipeline goes to some
        # length to mask -- and the file names carry a customer phone number and an agent's name.
        logger.warning(
            "internal_sentiment_retention_confusion_matrix.transcript.excluded",
            excluded=excluded,
            scored=len(gt_texts),
            reason="missing_or_blank",
        )

    if not gt_texts:
        return (
            similarity_stats([], threshold),
            error_rate_stats([], cer_threshold),
            excluded,
            0,
        )

    # CER first: it is local and free, so a fault in the pairing shows up before any money is
    # spent at Vertex.
    rates = [
        character_error_rate(true, pred)
        for true, pred in zip(gt_texts, pred_texts, strict=True)
    ]

    vectors, chunks = embed_pooled(
        [*gt_texts, *pred_texts], embedder=embedder, chunk_chars=chunk_chars
    )

    half = len(gt_texts)
    scores = [cosine_similarity(vectors[i], vectors[half + i]) for i in range(half)]

    return (
        similarity_stats(scores, threshold),
        error_rate_stats(rates, cer_threshold),
        excluded,
        chunks,
    )


def _mean(values: Sequence[float]) -> float:
    """Arithmetic mean, 0.0 on an empty sequence."""
    return sum(values) / len(values) if values else 0.0


def _canonical_product(value: Any, by_lower: Mapping[str, str]) -> str:
    """Fold a product cell onto the schema's spelling, keeping an unknown one as itself.

    Unlike the reason and outcome columns, an unrecognised product is **not** replaced by a
    catch-all here: this value is half the join key, and collapsing every unknown spelling onto
    one token would join two unrelated rows together. It keeps its own (folded) text, joins only
    against an identical spelling, and is counted as out-of-vocabulary by the product topic.
    """
    text = _clean(value)
    if not text:
        return ""
    return by_lower.get(text.casefold(), text.casefold())


def _fold_products(frame: pd.DataFrame, side: str) -> tuple[pd.DataFrame, int]:
    """Collapse rows sharing a ``(call, product)`` key, unioning their reasons.

    The ground-truth tab grades three calls on two rows each. Two of those pairs name the **same**
    product -- the grader using a second row to record more than three reasons -- so keying on
    ``(call, product)`` does not separate them and pandas would cross-join. Keeping only the first
    row, which the previous scorer did, silently discarded real ground truth: on one call it threw
    away two of four graded reasons.

    So the rows are unioned. The three rank columns keep the first row's values, for the compare
    sheet to display; the union is packed into :data:`REASON_POOL_COLUMN`, which is what is
    actually scored.

    Outcomes are expected to agree within a key -- they do on every pair in this corpus. A
    disagreement keeps the first and warns rather than raising: it is a ground-truth fault, and
    losing the whole evaluation to one is the wrong trade.

    Returns:
        ``(folded, duplicate_rows_absorbed)``.
    """
    if not len(frame):
        return frame.assign(**{REASON_POOL_COLUMN: pd.Series(dtype=str)}), 0

    pooled: list[str] = []
    for _, row in frame.iterrows():
        cells = [_clean(row[column]) for column in REASON_COLUMNS]
        pooled.append(POOL_SEPARATOR.join(cell for cell in cells if cell))
    frame = frame.assign(**{REASON_POOL_COLUMN: pooled})

    keys = [KEY_COLUMN, PRODUCT_COLUMN]
    absorbed = int(frame.duplicated(subset=keys).sum())
    if not absorbed:
        return frame, 0

    kept: list[dict[str, Any]] = []
    conflicts = 0
    for _, group in frame.groupby(keys, sort=False, dropna=False):
        record = group.iloc[0].to_dict()
        if len(group) > 1:
            parts: list[str] = []
            for value in group[REASON_POOL_COLUMN]:
                parts.extend(part for part in _clean(value).split(POOL_SEPARATOR) if part)
            # dict.fromkeys, not set(): string hashing is randomised per process, and a set would
            # pack the pool in a different order every run for no reason.
            record[REASON_POOL_COLUMN] = POOL_SEPARATOR.join(dict.fromkeys(parts))
            if group[OUTCOME_COLUMN].map(_clean).str.casefold().nunique() > 1:
                conflicts += 1
        kept.append(record)

    if conflicts:
        # Counts and the side only -- the values are graded call content.
        logger.warning(
            "internal_sentiment_retention_confusion_matrix.fold.outcome_conflict",
            side=side,
            keys=conflicts,
        )
    logger.info(
        "internal_sentiment_retention_confusion_matrix.fold.completed",
        side=side,
        rows_in=len(frame),
        rows_out=len(kept),
        absorbed=absorbed,
    )
    return pd.DataFrame(kept, columns=frame.columns), absorbed


def _call_products(frame: pd.DataFrame, by_lower: Mapping[str, str]) -> dict[str, set[str]]:
    """The set of products each call names, keyed by file stem.

    The product topic is scored on this, not on the main merge: ``call_sumary_product`` is half
    that merge's key, so every row there matches by construction and the column cannot grade
    itself. Production splits the same way, for the same reason.
    """
    products: dict[str, set[str]] = {}
    for _, row in frame.iterrows():
        name = _clean(row[KEY_COLUMN])
        label = _canonical_product(row[PRODUCT_COLUMN], by_lower)
        bucket = products.setdefault(name, set())
        if label:
            bucket.add(label if label in by_lower.values() else UNKNOWN_PRODUCT)
    return products


def _merge(
    gt_df: pd.DataFrame, result_df: pd.DataFrame
) -> tuple[pd.DataFrame, dict[str, set[str]], dict[str, set[str]], dict[str, int]]:
    """Join the two sheets on ``(file stem, product)``, and separately on the stem alone.

    Two joins, mirroring production. The first is the grain everything about a churning service is
    scored at -- a client can churn from Postpaid while being saved on TOL, and one row per call
    cannot express that. The second is at call grain and exists only so the product column can be
    scored at all, since it is the first join's key.

    The join is **outer**: a product the model missed keeps its ground-truth row, with the
    prediction side blank, so the miss costs it. A product the model invented keeps its row too,
    with the ground-truth side blank, so the invention costs it. An inner join would quietly score
    the model only on the products it already got right.

    Returns:
        ``(merged, gt_products, pred_products, counts)``.
    """
    for name, frame in (("ground truth", gt_df), ("result", result_df)):
        for column in (KEY_COLUMN, PRODUCT_COLUMN):
            if column not in frame.columns:
                raise KeyError(f"The {name} sheet has no {column!r} column.")

    by_lower = {label.casefold(): label for label in PRODUCT_LABELS}

    gt = gt_df.copy()
    result = result_df.copy()
    for frame in (gt, result):
        frame[KEY_COLUMN] = frame[KEY_COLUMN].map(_stem)
        frame[PRODUCT_COLUMN] = frame[PRODUCT_COLUMN].map(
            lambda value: _canonical_product(value, by_lower)
        )

    gt_products = _call_products(gt, by_lower)
    pred_products = _call_products(result, by_lower)

    gt, gt_absorbed = _fold_products(gt, "ground_truth")
    result, result_absorbed = _fold_products(result, "result")

    merged = gt.merge(
        result, on=[KEY_COLUMN, PRODUCT_COLUMN], how="outer", suffixes=("_gt", "_pred")
    )

    gt_keys = set(zip(gt[KEY_COLUMN], gt[PRODUCT_COLUMN], strict=True))
    result_keys = set(zip(result[KEY_COLUMN], result[PRODUCT_COLUMN], strict=True))
    counts = {
        "gt_only": len(gt_keys - result_keys),
        "result_only": len(result_keys - gt_keys),
        "gt_duplicates": gt_absorbed,
        "result_duplicates": result_absorbed,
    }

    return merged, gt_products, pred_products, counts


def _score_product_set(
    gt_products: Mapping[str, set[str]], pred_products: Mapping[str, set[str]]
) -> tuple[MultiLabelReport, int]:
    """Score which products each call named, at call grain.

    Rows are the calls either side mentions, so a call the model skipped entirely still scores --
    as a full set of false negatives, which is what missing it means.

    Returns:
        ``(report, gt_unknown_calls)``.
    """
    names = sorted(set(gt_products) | set(pred_products))
    y_true = [gt_products.get(name, set()) for name in names]
    y_pred = [pred_products.get(name, set()) for name in names]
    unknown = sum(1 for labels in y_true if UNKNOWN_PRODUCT in labels)

    if unknown:
        logger.warning(
            "internal_sentiment_retention_confusion_matrix.products.gt_out_of_vocabulary",
            calls=unknown,
            scored=len(names),
        )

    return multilabel_report(y_true, y_pred, PRODUCT_SCORED_LABELS), unknown


def evaluate(
    gt_df: pd.DataFrame,
    result_df: pd.DataFrame,
    *,
    embedder: Embedder | None = None,
    gt_transcripts: Mapping[str, str] | None = None,
    pred_transcripts: Mapping[str, str] | None = None,
    threshold: float = 0.80,
    cer_threshold: float = DEFAULT_CER_THRESHOLD,
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
) -> dict[str, pd.DataFrame]:
    """Score a result sheet against ground truth and return the dashboard's blocks.

    Every column per label -- see the module docstring. **Free of I/O**, so the whole
    scoring path runs offline: the only outward call is the injected ``embedder``, and it
    is only made when transcripts are supplied. Blocks are returned in sheet order; the
    keys are the titles printed above each table.

    Transcripts are passed *in* (downloading is the caller's job, which keeps this testable
    offline); the ``TRANSCRIPT`` block is emitted only when an ``embedder`` and both mappings are
    supplied, so an unreachable folder costs that block rather than the evaluation, and a caller
    that wants only the scored columns spends nothing at Vertex.

    Args:
        gt_df: The ground-truth sheet.
        result_df: The model result sheet.
        embedder: Callable turning texts into vectors, preserving input order, or None to
            skip the transcript block entirely and keep the whole call offline.
        gt_transcripts: Human transcript text keyed by file stem, or None to skip the block.
        pred_transcripts: Model transcript text keyed by file stem, or None to skip it.
        threshold: Cosine score at or above which a transcript counts as a pass.
        cer_threshold: CER at or **below** which a transcript counts as a pass -- the one
            threshold on this sheet where a smaller number is the good outcome.
        chunk_chars: Maximum characters per embedding chunk for a transcript.

    Returns:
        ``{block title: DataFrame}``, insertion-ordered.

    Raises:
        KeyError: If either frame is missing the join key or a scored column.
        ValueError: If ``embedder`` returns the wrong number of vectors.
    """
    started = time.monotonic()

    merged, gt_products, pred_products, join_counts = _merge(gt_df, result_df)

    logger.info(
        "internal_sentiment_retention_confusion_matrix.join.completed",
        gt_rows=len(gt_df),
        result_rows=len(result_df),
        matched=len(merged),
        **join_counts,
    )

    # WARNING, not INFO: a duplicate key means a graded call was dropped, so the
    # dashboard is scoring fewer calls than the sheet contains.
    if join_counts["gt_duplicates"] or join_counts["result_duplicates"]:
        logger.warning(
            "internal_sentiment_retention_confusion_matrix.join.duplicate_keys",
            gt_duplicates=join_counts["gt_duplicates"],
            result_duplicates=join_counts["result_duplicates"],
            matched=len(merged),
        )

    outcome_report, outcome_excluded = _score_single_label(
        merged, OUTCOME_COLUMN, OUTCOME_SCORED_LABELS, missing_label=MISSING_LABEL
    )
    # One report for all three ranks, not one per rank: production unions them, and scoring the
    # ranks separately is what made a correctly-identified reason in the wrong slot score zero.
    reason_report, reason_counts = _score_reason_set(merged)

    product_report, product_unknown = _score_product_set(gt_products, pred_products)

    outcome_aggregates = aggregate_scores(outcome_report.per_label)
    reason_aggregates = aggregate_scores(reason_report.per_label)
    product_aggregates = aggregate_scores(product_report.per_label)

    # All three, not just the mappings: unlike the QA sibling this module has no summary column,
    # so a caller that only wants the scored-column blocks passes no embedder at all and the
    # whole scoring path stays offline and free.
    score_transcripts = (
        embedder is not None and gt_transcripts is not None and pred_transcripts is not None
    )
    transcript_similarity = transcript_errors = None
    transcript_excluded = transcript_chunks = 0

    if score_transcripts:
        (
            transcript_similarity,
            transcript_errors,
            transcript_excluded,
            transcript_chunks,
        ) = _score_transcript(
            merged,
            gt_transcripts,
            pred_transcripts,
            embedder=embedder,
            threshold=threshold,
            cer_threshold=cer_threshold,
            chunk_chars=chunk_chars,
        )

    summary = pd.DataFrame(
        [
            {
                "Topic": TOPIC_OUTCOME,
                "Classes": len(outcome_aggregates.weighted.labels),
                "N": outcome_report.n,
                "Accuracy": round(outcome_aggregates.weighted.accuracy, 4),
                "Precision": round(outcome_aggregates.weighted.precision, 4),
                "Recall": round(outcome_aggregates.weighted.recall, 4),
                "F1": round(outcome_aggregates.weighted.f1, 4),
                "Note": (
                    f"weighted average over {len(OUTCOME_LABELS)} categories plus "
                    f"{MISSING_LABEL}; "
                    f"{outcome_excluded} row(s) excluded as out-of-vocabulary"
                ),
            },
            {
                "Topic": TOPIC_REASON,
                "Classes": len(reason_aggregates.weighted.labels),
                "N": reason_report.n,
                "Accuracy": round(reason_aggregates.weighted.accuracy, 4),
                "Precision": round(reason_aggregates.weighted.precision, 4),
                "Recall": round(reason_aggregates.weighted.recall, 4),
                "F1": round(reason_aggregates.weighted.f1, 4),
                "Note": (
                    f"weighted average; set union of {len(REASON_COLUMNS)} ranks, rank ignored; "
                    f"{reason_counts['gt_empty']} row(s) had no ground-truth reason, "
                    f"{reason_counts['gt_unknown']} ground-truth cell(s) out-of-vocabulary"
                ),
            },
            {
                "Topic": TOPIC_PRODUCT,
                "Classes": len(product_aggregates.weighted.labels),
                "N": product_report.n,
                "Accuracy": round(product_aggregates.weighted.accuracy, 4),
                "Precision": round(product_aggregates.weighted.precision, 4),
                "Recall": round(product_aggregates.weighted.recall, 4),
                "F1": round(product_aggregates.weighted.f1, 4),
                "Note": (
                    "weighted average; set per CALL, on its own join -- N is calls, not rows; "
                    f"{product_unknown} call(s) named an out-of-vocabulary product"
                ),
            },
        ]
    )

    if score_transcripts:
        summary = pd.concat(
            [
                summary,
                pd.DataFrame(
                    [
                        {
                            "Topic": FAMILY_TRANSCRIPT,
                            "Classes": NOT_APPLICABLE,
                            "N": transcript_errors.n,
                            # 1 - CER is the share of characters transcribed correctly, on the
                            # same 0-1 scale as the Accuracy above it. Precision/Recall/F1 have
                            # no reading for free text, so they are blanked rather than faked.
                            "Accuracy": round(1.0 - transcript_errors.mean, 4),
                            "Precision": NOT_APPLICABLE,
                            "Recall": NOT_APPLICABLE,
                            "F1": NOT_APPLICABLE,
                            "Note": (
                                f"1 - mean CER; mean cosine {transcript_similarity.mean:.4f}; "
                                f"CER pass@{cer_threshold:.2f} "
                                f"{transcript_errors.pass_rate:.4f}; "
                                f"{transcript_excluded} excluded"
                            ),
                        }
                    ]
                ),
            ],
            ignore_index=True,
        )

    transcript_block = None
    if score_transcripts:
        transcript_block = pd.DataFrame(
            [
                {"Metric": "Mean cosine", "Value": round(transcript_similarity.mean, 4)},
                {"Metric": "Median cosine", "Value": round(transcript_similarity.median, 4)},
                {"Metric": "Lowest cosine", "Value": round(transcript_similarity.minimum, 4)},
                {"Metric": "Highest cosine", "Value": round(transcript_similarity.maximum, 4)},
                {
                    "Metric": f"Pass rate (>= {threshold:.2f})",
                    "Value": round(transcript_similarity.pass_rate, 4),
                },
                {"Metric": "Mean CER", "Value": round(transcript_errors.mean, 4)},
                {"Metric": "Median CER", "Value": round(transcript_errors.median, 4)},
                {"Metric": "Best CER (lowest)", "Value": round(transcript_errors.best, 4)},
                {"Metric": "Worst CER (highest)", "Value": round(transcript_errors.worst, 4)},
                {
                    "Metric": f"CER pass rate (<= {cer_threshold:.2f})",
                    "Value": round(transcript_errors.pass_rate, 4),
                },
                {
                    "Metric": "Mean char accuracy (1 - CER)",
                    "Value": round(1.0 - transcript_errors.mean, 4),
                },
                {"Metric": "Scored", "Value": transcript_errors.n},
                {"Metric": "Excluded (missing either side)", "Value": transcript_excluded},
                {"Metric": "Chunks embedded", "Value": transcript_chunks},
            ],
            dtype=object,
        )

    header = pd.DataFrame(
        [
            {
                "Compared": (
                    f"{len(merged)} of {len(gt_df)} ground-truth rows "
                    f"({join_counts['gt_only']} unmatched in ground truth, "
                    f"{join_counts['result_only']} unmatched in result)"
                ),
                # On the sheet, not only in the log: a dropped duplicate is a call that
                # stopped being scored, and the reader has to see that beside the counts.
                "Duplicate keys dropped": (
                    f"{join_counts['gt_duplicates']} ground truth, "
                    f"{join_counts['result_duplicates']} result"
                ),
                "Scoring": "per label, joined on file stem; see the legend at the foot",
            }
        ]
    )

    blocks = {
        BLOCK_HEADER: header,
        BLOCK_SUMMARY: summary,
        BLOCK_OUTCOME: _per_class_block(
            outcome_report.per_label, label_header="Label", numbered=False
        ),
        BLOCK_REASONS: _per_class_block(reason_report.per_label, label_header="Reason"),
        BLOCK_PRODUCT: _per_class_block(
            product_report.per_label, label_header="Product", numbered=False
        ),
    }

    # Optional and inserted before the legend, because dict insertion order is the sheet's row
    # order: a run without transcripts simply has one block fewer.
    if transcript_block is not None:
        blocks[BLOCK_TRANSCRIPT] = transcript_block

    blocks[BLOCK_LEGEND] = pd.DataFrame(LEGEND, columns=["Term", "Meaning"])

    logger.info(
        "internal_sentiment_retention_confusion_matrix.evaluate.completed",
        matched=len(merged),
        transcripts_scored=transcript_errors.n if transcript_errors else 0,
        transcript_chunks=transcript_chunks,
        reason_classes=len(REASON_SCORED_CLASSES),
        outcome_excluded=outcome_excluded,
        reason_gt_empty=reason_counts["gt_empty"],
        reason_gt_unknown=reason_counts["gt_unknown"],
        product_calls=product_report.n,
        product_gt_unknown=product_unknown,
        elapsed_ms=_elapsed_ms(started),
    )
    return blocks


def _compare_frame(
    merged: pd.DataFrame, groups: Sequence[tuple[str, list[str], list[str], list[str]]]
) -> pd.DataFrame:
    """One compare sheet's rows: a running number, the call, then GT/AI/Compare per group.

    The GT and AI cells hold :func:`_clean`'s output, not a folded rewrite. The sheet exists to
    show what the workbook actually held, so a reader chasing an ``F`` sees ``Network`` against
    ``save cost`` -- and sees the raw ``Contact end`` that landed in the out-of-vocabulary row,
    rather than a tidied version that would make a grader's typo look like the model's fault.

    Column names are flat (``"reason_main GT"``) even though the sheet renders a merged two-row
    header. The frame is the data; :func:`write_comparison_sheets` owns how it looks, and a frame
    with duplicate column labels could not be built at all.

    Args:
        merged: The joined frame, only for its length and key column.
        groups: ``(name, gt_values, ai_values, verdicts)`` per three-cell group, in sheet order.
    """
    if not len(merged):
        # An empty join still has to produce the right shape, or the sheet loses its header.
        headers = [
            *COMPARE_INDEX_COLUMNS,
            *(f"{name} {sub}" for name, *_ in groups for sub in COMPARE_SUBHEADERS),
        ]
        return pd.DataFrame(columns=headers)

    data: dict[str, Any] = {
        "No": range(1, len(merged) + 1),
        KEY_COLUMN: [_clean(value) for value in merged[KEY_COLUMN]],
    }
    for name, gt_values, ai_values, verdicts in groups:
        data[f"{name} GT"] = gt_values
        data[f"{name} AI"] = ai_values
        data[f"{name} Compare"] = verdicts
    return pd.DataFrame(data)


def _render_set(labels: set[str]) -> str:
    """A reason set as one readable cell: sorted, comma-joined, blank when empty.

    Sorted so the same set always renders identically -- an unsorted join would make two equal
    sets look different on the sheet and send a reader hunting a difference that is not there.
    """
    return ", ".join(sorted(labels))


def compare_rows(gt_df: pd.DataFrame, result_df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Build the per-call verdict frames behind the dashboard's blocks.

    The evidence for the counts: a class scoring 0.31 says nothing about *which* calls it failed,
    and this is where that is answered. Free of I/O, and joins through :func:`_merge`, so its rows
    are the rows :func:`evaluate` scored, in the same order.

    The reasons sheet carries four groups: one per rank, then the sets. Only the last is scored --
    the rank groups exist so a reader can tell a genuine miss from a difference of ordering, which
    the dashboard deliberately no longer penalises.

    Returns:
        ``{sheet key: frame}`` keyed by :data:`COMPARE_OUTCOME` and :data:`COMPARE_REASONS`.

    Raises:
        KeyError: If either frame is missing the join key.
    """
    started = time.monotonic()
    merged, gt_products, pred_products, _ = _merge(gt_df, result_df)

    outcome_by_lower = {label.casefold(): label for label in OUTCOME_LABELS}
    outcome_gt = [_clean(value) for value in merged[f"{OUTCOME_COLUMN}_gt"]]
    outcome_ai = [_clean(value) for value in merged[f"{OUTCOME_COLUMN}_pred"]]
    outcome_verdicts = [
        MATCH_TRUE
        if _canonical(gt, outcome_by_lower, None) is not None
        and _canonical(gt, outcome_by_lower, None) == _canonical(ai, outcome_by_lower, None)
        else MATCH_FALSE
        for gt, ai in zip(outcome_gt, outcome_ai, strict=True)
    ]

    reason_groups: list[tuple[str, list[str], list[str], list[str]]] = []
    for column in REASON_COLUMNS:
        gt_values = [_clean(value) for value in merged[f"{column}_gt"]]
        ai_values = [_clean(value) for value in merged[f"{column}_pred"]]
        reason_groups.append(
            (
                f"{column} (rank)",
                gt_values,
                ai_values,
                [
                    MATCH_TRUE if gt.casefold() == ai.casefold() else MATCH_FALSE
                    for gt, ai in zip(gt_values, ai_values, strict=True)
                ],
            )
        )

    by_lower = {label.casefold(): label for label in REASON_CLASSES}
    gt_columns = [f"{column}_gt" for column in REASON_SCORING_COLUMNS]
    pred_columns = [f"{column}_pred" for column in REASON_SCORING_COLUMNS]
    set_gt: list[str] = []
    set_ai: list[str] = []
    set_verdicts: list[str] = []
    for _, row in merged.iterrows():
        true, _ = _reason_set(row, gt_columns, by_lower)
        pred, _ = _reason_set(row, pred_columns, by_lower)
        set_gt.append(_render_set(true))
        set_ai.append(_render_set(pred))
        set_verdicts.append(MATCH_TRUE if true == pred else MATCH_FALSE)
    reason_groups.append((COMPARE_SET_GROUP, set_gt, set_ai, set_verdicts))

    # The product sheet is at CALL grain, unlike the other two, because that is where the
    # product topic is scored. Built as its own frame rather than squeezed into the row-grain
    # ones, where every call with two products would appear twice with the same set.
    call_names = sorted(set(gt_products) | set(pred_products))
    product_index = pd.DataFrame({KEY_COLUMN: call_names})
    product_gt = [_render_set(gt_products.get(name, set())) for name in call_names]
    product_ai = [_render_set(pred_products.get(name, set())) for name in call_names]
    product_verdicts = [
        MATCH_TRUE if gt_products.get(name, set()) == pred_products.get(name, set()) else MATCH_FALSE
        for name in call_names
    ]

    frames = {
        COMPARE_OUTCOME: _compare_frame(
            merged, [(OUTCOME_COLUMN, outcome_gt, outcome_ai, outcome_verdicts)]
        ),
        COMPARE_REASONS: _compare_frame(merged, reason_groups),
        COMPARE_PRODUCT: _compare_frame(
            product_index, [(PRODUCT_COLUMN, product_gt, product_ai, product_verdicts)]
        ),
    }

    logger.info(
        "internal_sentiment_retention_confusion_matrix.compare_rows.completed",
        matched=len(merged),
        sheets=len(frames),
        elapsed_ms=_elapsed_ms(started),
    )
    return frames


def resolve_sheet_names(existing: Sequence[str], bases: Sequence[str]) -> list[str]:
    """Pick free sheet names for one run, giving all of them the **same** suffix.

    The sheets are only readable as a set; independent resolution would pair a ``_2`` dashboard
    with another run's verdict sheets. The lowest suffix free for *every* base wins.
    """
    taken = set(existing)

    if not any(base in taken for base in bases):
        return list(bases)

    suffix = 1
    while any(f"{base}_{suffix}" in taken for base in bases):
        suffix += 1
    return [f"{base}_{suffix}" for base in bases]


def resolve_sheet_name(existing: Sequence[str], base: str) -> str:
    """Pick a free sheet name: ``base`` if free, else the first free ``{base}_{n}``."""
    taken = set(existing)

    if base not in taken:
        return base

    suffix = 1
    while f"{base}_{suffix}" in taken:
        suffix += 1
    return f"{base}_{suffix}"


def download_transcripts(
    client: SharePointModule, folder: str, stems: set[str], *, concurrency: int
) -> dict[str, str]:
    """Download the transcript files in ``folder`` whose stem is in ``stems``.

    Fanned out over a thread pool (pure I/O), and only the requested stems are fetched.

    Returns:
        ``{stem: text}``; a failed download is omitted so the caller's join drops that call.
        Raises SharePointError only when the folder itself cannot be listed.
    """
    started = time.monotonic()
    paths = client.list_files(folder, pattern=r"\.txt$")
    wanted = {path: _stem(Path(path).name) for path in paths}
    targets = [path for path, stem in wanted.items() if stem in stems]

    def fetch(path: str) -> tuple[str, str | None]:
        try:
            # errors="replace" rather than strict: one malformed byte in a 24KB transcript must
            # not cost the whole call its score, and CER already counts the damage.
            return wanted[path], client.download_file(path).content.decode(
                "utf-8", errors="replace"
            )
        except Exception:
            # SharePointModule already logged the cause at its single ERROR site; this records
            # only that the run carried on past it.
            logger.warning("internal_sentiment_retention_confusion_matrix.transcript.skipped", path=_safe_name(path))
            return wanted[path], None

    transcripts: dict[str, str] = {}

    if targets:
        with ThreadPoolExecutor(max_workers=min(concurrency, len(targets))) as pool:
            for stem, text in pool.map(fetch, targets):
                if text is not None:
                    transcripts[stem] = text

    logger.info(
        "internal_sentiment_retention_confusion_matrix.transcripts.downloaded",
        folder=folder,
        listed=len(paths),
        requested=len(targets),
        downloaded=len(transcripts),
        failed=len(targets) - len(transcripts),
        elapsed_ms=_elapsed_ms(started),
    )
    return transcripts


def _safe_name(path: str) -> str:
    """A transcript path reduced to something safe to log.

    The file names can carry a customer's phone number and the agent's name, and the file
    *content* is the raw call. Only the call ID -- the leading segment of the name -- reaches the
    log, which is enough to find the file by hand without putting the rest into a search index.
    """
    return Path(path).stem.split("_", 1)[0]


def _is_ratio_column(name: str) -> bool:
    """Whether a column holds 0-1 ratios, so it can be colour-scaled and formatted as one.

    Matches both the bare metric names and the per-class ones ``_class_cells`` produces
    (``"Meet F1"``, ``"Positive Precision"``), while leaving the count columns beside them alone.
    """
    return name in RATIO_COLUMNS or name.rsplit(" ", 1)[-1] in RATIO_SUFFIXES


def _is_count_column(name: str) -> bool:
    """Whether a column holds counts, so it is formatted as an integer and never colour-scaled."""
    return name in COUNT_COLUMNS or name.rsplit(" ", 1)[-1] in COUNT_SUFFIXES


def _is_inverted_metric(name: Any) -> bool:
    """Whether a Metric/Value row's Value is a ratio where **lower is better**.

    The raw CER rows only. ``CER pass rate`` is deliberately *not* here: it is the share of calls
    that passed, so a high value is the good outcome and it takes the normal scale.
    """
    return isinstance(name, str) and name in INVERTED_RATIO_METRICS


def _is_ratio_metric(name: Any) -> bool:
    """Whether a Metric/Value row's Value is a 0-1 ratio rather than a count."""
    return isinstance(name, str) and (
        name in RATIO_METRICS
        or name in TRANSCRIPT_RATIO_METRICS
        or name in INVERTED_RATIO_METRICS
        or name.startswith(RATIO_METRIC_PREFIXES)
    )


def _colour_scale(invert: bool = False) -> ColorScaleRule:
    """A red-yellow-green scale pinned to 0.0 / 0.5 / 1.0.

    Absolute stops: a per-block relative scale would paint the best column of a uniformly bad
    family solid green. ``invert`` swaps the ends for lower-is-better metrics (CER only).
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


def _style_block(
    worksheet,
    frame: pd.DataFrame,
    title: str,
    title_row: int,
    index: int,
    *,
    source: str | None = None,
) -> None:
    """Apply the table, number formats, colour scales and muted cells for one block.

    Args:
        worksheet: The openpyxl worksheet.
        frame: The block's data.
        title: The block's title, printed in the banner row above it.
        title_row: 1-based Excel row the banner occupies.
        index: The block's position, used to build a workbook-unique table name.
        source: The block's ground-truth provenance line, occupying one row between the title
            and the table header. None for blocks that name no ground-truth column.
    """
    width = max(len(frame.columns), 1)
    # The caption sits between the title and the header, so everything below shifts by one.
    header_row = title_row + (2 if source else 1)
    first_data_row = header_row + 1
    last_data_row = header_row + len(frame)
    last_letter = get_column_letter(width)

    for column in range(1, width + 1):
        worksheet.cell(row=title_row, column=column).fill = TITLE_FILL
    worksheet.cell(row=title_row, column=1).font = TITLE_FONT

    if source:
        caption_row = title_row + 1
        for column in range(1, width + 1):
            worksheet.cell(row=caption_row, column=column).fill = BANNER_FILL
        # Merged so a long provenance line reads as one caption instead of being clipped at
        # column A's width, which is sized for class names.
        worksheet.merge_cells(
            start_row=caption_row, start_column=1, end_row=caption_row, end_column=width
        )
        worksheet.cell(row=caption_row, column=1).font = SOURCE_FONT

    if title not in UNTABLED_BLOCKS and len(frame):
        # Table names are workbook-scoped and must be identifier-like, so the sheet name is
        # folded in: a second evaluation writing "Evaluation Dashboard_1" cannot collide with
        # the tables the first one left behind.
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
                    worksheet.conditional_formatting.add(
                        cell.coordinate,
                        _colour_scale(invert=_is_inverted_metric(metric)),
                    )
                    cell.number_format = RATIO_FORMAT
                else:
                    cell.number_format = COUNT_FORMAT


def _style_legend(worksheet, frame: pd.DataFrame, title_row: int) -> None:
    """Merge, wrap and size the legend rows.

    The explanations are paragraphs. Unmerged they run across every column to the right and hide
    the tables beside them; unwrapped they are clipped to one line. Row heights are set here
    rather than left to Excel, which does not reliably auto-fit a generated file's wrapped rows.
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
    """Append the dashboard blocks to a workbook, in memory; returns the updated bytes.

    Appends into the original bytes so existing sheets keep rows and formatting. Raises
    ValueError if ``sheet_name`` is taken -- the backstop behind :func:`resolve_sheet_name`.
    """
    started = time.monotonic()

    # Titles are written after the frames: the worksheet does not exist until the first to_excel
    # call, so its row offsets are computed up front and filled in at the end.
    title_rows: list[tuple[int, str]] = []
    row = 0

    with io.BytesIO(workbook_byte) as buffer:
        # "overlay", not "error": every block after the first writes into a sheet this call has
        # already created, and "error" fires on exactly that. The collision check therefore has
        # to happen here, once, against the sheets the workbook arrived with.
        with pd.ExcelWriter(
            buffer, engine="openpyxl", mode="a", if_sheet_exists="overlay"
        ) as writer:
            if sheet_name in writer.book.sheetnames:
                raise ValueError(
                    f"Sheet {sheet_name!r} already exists; resolve_sheet_name picks a free name."
                )

            for title, frame in blocks.items():
                title_rows.append((row, title))
                # A block naming its ground-truth columns needs one extra row between its title
                # and its header; the text itself is written in the styling pass below.
                caption = 1 if title in BLOCK_SOURCES else 0
                frame.to_excel(
                    writer, sheet_name=sheet_name, startrow=row + 1 + caption, index=False
                )
                # title + optional provenance caption + header + body + one blank row
                row += len(frame) + 3 + caption

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
                source = BLOCK_SOURCES.get(title)
                if source is not None:
                    worksheet.cell(row=offset + 2, column=1, value=source)
                _style_block(
                    worksheet, blocks[title], title, offset + 1, index, source=source
                )

            # The one-row banner is a statement, not data, so it gets a light fill instead of the
            # table styling every other block above the legend receives.
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
        "internal_sentiment_retention_confusion_matrix.dashboard.written",
        sheet=sheet_name,
        blocks=len(blocks),
        rows=row,
        bytes=len(updated),
        elapsed_ms=_elapsed_ms(started),
    )
    return updated


def _style_compare_sheet(worksheet, frame: pd.DataFrame, groups: Sequence[str]) -> None:
    """Turn a flat compare frame into the reference workbook's two-row header layout.

    Row 1 becomes merged group names, row 2 the ``GT``/``AI``/``Compare`` sub-headers. Not a
    native Excel Table (those demand a single header row); autofilter + freeze pane stand in.
    """
    index_width = len(COMPARE_INDEX_COLUMNS)
    first_data_row = 3
    last_data_row = first_data_row + len(frame) - 1
    width = index_width + len(groups) * len(COMPARE_SUBHEADERS)

    # Row 2 has to exist before anything is written into it. to_excel put the frame's header on
    # row 1 and its first record on row 2, so the records shift down one and row 2 is freed.
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

    # Both header rows above, and both index columns to the left, stay put while scrolling -- with
    # 22 triplets the criterion name is otherwise off-screen long before its Compare cell is.
    worksheet.freeze_panes = f"{get_column_letter(index_width + 1)}{first_data_row}"
    worksheet.sheet_properties.tabColor = COMPARE_TAB_COLOR

    if not len(frame):
        return

    worksheet.auto_filter.ref = f"A2:{get_column_letter(width)}{last_data_row}"

    # Static fills rather than a conditional-formatting rule: the verdicts are computed here and
    # never change, and a rule would recolour the cells if someone edited the sheet by hand --
    # making an edited copy look like a scored one.
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
    """Append the per-call verdict sheets to a workbook, in memory; returns updated bytes.

    Chain after :func:`write_dashboard`: pass this the bytes that one returned. Raises KeyError
    if a frame has no sheet name, ValueError if a resolved name is already taken.
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
        "internal_sentiment_retention_confusion_matrix.compare_sheets.written",
        # Sheet names and row counts only -- the sheets themselves hold graded call content.
        sheets=[sheet_names[key] for key in frames],
        rows=max((len(frame) for frame in frames.values()), default=0),
        bytes=len(updated),
        elapsed_ms=_elapsed_ms(started),
    )
    return updated


def run(config: Config | None = None) -> None:
    """Download the workbook, score it, and upload it back with the dashboard appended.

    Omitted ``config`` takes the class defaults, including the hand-edited ``run_prefix``.
    """
    config = config if config is not None else Config()

    with TracedOperation(
        "internal_sentiment_retention_confusion_matrix.run",
        workbook=config.workbook_path,
    ):
        logger.info(
            "internal_sentiment_retention_confusion_matrix.run.starting",
            workbook=config.workbook_path,
            gt_transcript_path=config.gt_transcript_path,
            embedding_model=config.embedding_model,
            embedding_location=config.embedding_location,
            threshold=config.similarity_threshold,
            cer_threshold=config.transcript_cer_threshold,
            chunk_chars=config.transcript_chunk_chars,
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

        with TracedOperation("internal_sentiment_retention_confusion_matrix.load"):
            workbook_byte = client_sb.download_file(config.workbook_path).content

            with io.BytesIO(workbook_byte) as f:
                # One ExcelFile, so sheet_names comes off the same parse the frames are read from
                # rather than parsing the file twice.
                book = pd.ExcelFile(f, engine="openpyxl")
                sheet_names = list(book.sheet_names)

                missing = [
                    name
                    for name in (config.gt_sheet_name, config.result_sheet_name)
                    if name not in sheet_names
                ]
                if missing:
                    logger.error(
                        "internal_sentiment_retention_confusion_matrix.run.aborted",
                        reason="sheet_missing",
                        file=config.workbook_path,
                        missing_sheets=missing,
                        # Sheet names only -- never cell content.
                        available=sheet_names,
                    )
                    return

                # keep_default_na=False with the empty cell re-added: pandas' default NA set
                # contains 'NA' and 'None', and a blank reason rank is a graded answer here --
                # it must arrive as an empty cell so _canonical can map it onto (none).
                read_kwargs = {
                    "dtype": str,
                    "header": 0,
                    "keep_default_na": False,
                    "na_values": [""],
                }
                gt_df = book.parse(config.gt_sheet_name, **read_kwargs)
                result_df = book.parse(config.result_sheet_name, **read_kwargs)

            # strict=False admits extra columns but not missing ones, so this is a real gate on
            # both sheets' shape rather than a formality.
            frames = {}
            for label, frame, schema in (
                (config.gt_sheet_name, gt_df, InputGTSchema),
                (config.result_sheet_name, result_df, OutputSchema),
            ):
                try:
                    frames[label] = schema.validate(frame)
                except (SchemaError, SchemaErrors) as e:
                    # Column names computed here rather than taken off the exception: e.data and
                    # e.failure_cases carry the offending cell values, and str(e.schema) is a
                    # multi-KB repr of the whole schema.
                    dropped = sorted(set(schema.to_schema().columns) - set(frame.columns))
                    logger.error(
                        "internal_sentiment_retention_confusion_matrix.run.aborted",
                        reason="schema_invalid",
                        file=config.workbook_path,
                        sheet=label,
                        missing_columns=dropped,
                        error_type=type(e).__name__,
                        schema=getattr(e.schema, "name", None),
                    )
                    return

            gt_df = frames[config.gt_sheet_name]
            result_df = frames[config.result_sheet_name]

            logger.info(
                "internal_sentiment_retention_confusion_matrix.load.completed",
                file=config.workbook_path,
                gt_rows=len(gt_df),
                result_rows=len(result_df),
                bytes=len(workbook_byte),
            )

        with TracedOperation("internal_sentiment_retention_confusion_matrix.transcripts"):
            # The stems both sheets agree on -- the same set evaluate() will join on, so nothing
            # is downloaded for a call that will not be scored.
            matched_stems = {_stem(name) for name in gt_df[KEY_COLUMN]} & {
                _stem(name) for name in result_df[KEY_COLUMN]
            }

            gt_transcripts: dict[str, str] | None = None
            pred_transcripts: dict[str, str] | None = None

            try:
                gt_transcripts = download_transcripts(
                    client_sb,
                    config.gt_transcript_path,
                    matched_stems,
                    concurrency=config.transcript_concurrency,
                )
                pred_transcripts = download_transcripts(
                    client_sb,
                    config.run_folder,
                    matched_stems,
                    concurrency=config.transcript_concurrency,
                )
            except Exception:
                # Degrade rather than abort: the sheets are already loaded and the scored-column
                # families cost nothing more to score. Losing a completed evaluation over an
                # unreachable transcript folder is the worse outcome.
                gt_transcripts = pred_transcripts = None
                logger.warning(
                    "internal_sentiment_retention_confusion_matrix.transcripts.skipped",
                    gt_folder=config.gt_transcript_path,
                    result_folder=config.run_folder,
                )

            if gt_transcripts is not None and pred_transcripts is not None:
                both = len(gt_transcripts.keys() & pred_transcripts.keys())
                logger.info(
                    "internal_sentiment_retention_confusion_matrix.transcripts.loaded",
                    matched_rows=len(matched_stems),
                    ground_truth=len(gt_transcripts),
                    result=len(pred_transcripts),
                    scoreable=both,
                    missing_ground_truth=len(matched_stems) - len(gt_transcripts),
                    missing_result=len(matched_stems) - len(pred_transcripts),
                )
                # Nothing on both sides is the same as nothing at all, and an empty block on the
                # sheet reads as "the transcripts were perfect" to anyone skimming it.
                if not both:
                    gt_transcripts = pred_transcripts = None
                    logger.warning(
                        "internal_sentiment_retention_confusion_matrix.transcripts.skipped",
                        reason="no_overlapping_stems",
                        gt_folder=config.gt_transcript_path,
                        result_folder=config.run_folder,
                    )

        with TracedOperation("internal_sentiment_retention_confusion_matrix.evaluate"):
            client_embed = VertexAIEmbedding(
                project_id=os.environ["GCP_PROJECT_ID"],
                location=config.embedding_location,
                model=config.embedding_model,
                concurrency=config.embedding_concurrency,
            )
            blocks = evaluate(
                gt_df,
                result_df,
                embedder=client_embed.embed_texts,
                threshold=config.similarity_threshold,
                gt_transcripts=gt_transcripts,
                pred_transcripts=pred_transcripts,
                cer_threshold=config.transcript_cer_threshold,
                chunk_chars=config.transcript_chunk_chars,
            )

        with TracedOperation("internal_sentiment_retention_confusion_matrix.compare"):
            # Recomputed from the same frames rather than threaded out of evaluate(): compare_rows
            # joins through the same _merge, so its rows are the rows that were scored, and
            # evaluate() stays a pure block-builder that a test can call on its own.
            compare_frames = compare_rows(gt_df, result_df)

        with TracedOperation("internal_sentiment_retention_confusion_matrix.write"):
            # All three names resolved together, so a run's dashboard and its verdict sheets
            # always carry the same suffix and can be read as one set.
            bases = (
                config.eval_sheet_name,
                config.outcome_compare_sheet_name,
                config.reasons_compare_sheet_name,
                config.product_compare_sheet_name,
            )
            sheet_name, outcome_sheet, reasons_sheet, product_sheet = resolve_sheet_names(
                sheet_names, bases
            )

            if sheet_name != config.eval_sheet_name:
                logger.info(
                    "internal_sentiment_retention_confusion_matrix.sheet.renamed",
                    requested=config.eval_sheet_name,
                    resolved=sheet_name,
                )

            updated = write_dashboard(workbook_byte, blocks, sheet_name)
            del workbook_byte  # free the downloaded copy before the upload
            updated = write_comparison_sheets(
                updated,
                compare_frames,
                {
                    COMPARE_OUTCOME: outcome_sheet,
                    COMPARE_REASONS: reasons_sheet,
                    COMPARE_PRODUCT: product_sheet,
                },
            )

            # Guarded: the evaluation is already computed and a fault here -- a lock, a transient
            # 503 -- must not read as a scoring failure. SharePointModule already logged the
            # cause at its single ERROR site, so this records only that the run carried on.
            uploaded = False
            try:
                client_sb.upload_file(
                    upload_path=config.workbook_path, content=updated, archive_on_lock=True
                )
                uploaded = True
            except Exception:
                logger.warning(
                    "internal_sentiment_retention_confusion_matrix.output.skipped",
                    path=config.workbook_path,
                    sheet=sheet_name,
                )

        logger.info(
            "internal_sentiment_retention_confusion_matrix.run.completed",
            workbook=config.workbook_path,
            sheet=sheet_name,
            gt_rows=len(gt_df),
            result_rows=len(result_df),
            blocks=len(blocks),
            uploaded=uploaded,
        )
