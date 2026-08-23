"""Score the telesale Google result sheet against ground truth, label by label.

Reads the workbook ``google_output.py`` produced, appends a ``Confusion Matrix Dashboard``, and
writes one verdict sheet per family beside it.

**The only telesale scorer.** The exact-match sibling was deleted: its ``Accuracy`` column was
verified identical to this module's on all 39 criteria (max absolute difference 0.0), and its
other three metrics were structurally degenerate -- ``match_report`` fixes ``fn`` and ``tn`` at
zero, which makes Precision equal Accuracy and Recall exactly 1.0. Its two columns that *did*
carry information, ``GT labels`` and ``Majority``, moved here, as did the compare sheets.

That degenerate framing is also what production and the stakeholders' workbook use.
``FactCheckTask._evaluate`` in ``D:/sentiment-voice-analysis`` sets ``FN = 0`` and ``TN = 0``
outright, so every ``Confusion_Matrix_*`` tab of ``Human Groundtruth.xlsx`` reports Recall
1.0000 on all 39 items. Measured on the real tab, a model that catches **none** of the 25
violations scores 0.9813 / 0.9904 / 0.9855 / 0.9935 by category under that formula. It is not
reproduced here; see the ``HOW TO READ THIS DASHBOARD`` block.

Every column shares one three-label vocabulary -- ``T`` (criterion met), ``F`` (violation),
``N/A`` (did not apply) -- taken from
:mod:`~src.google_model.sentiment_telesale.schema.sheet_columns` rather than restated here.
There is no ``(none)`` blank class as there is on the mnp sheet: ``N/A`` already *is* the
"nothing to judge" answer, and a genuinely empty result cell means the pipeline produced no
answer for that call. Those rows are excluded from the column and counted in ``Excluded``.

Scores roll up over three levels -- criterion, sub-category, category -- because production
groups its own evaluation by sub-category (the first element of every ``GT_FIELD_MAPPING``
value) and the workbook prints ``cate / sub_cate / item``. Each level carries Macro, Micro and
Weighted rows from the shared :func:`~src.google_model.metrics.aggregate_scores`, so this
dashboard, mnp's and retention's cannot compute an average three different ways.

**Read ``GROUND-TRUTH COVERAGE`` before any score.** 25 of the 39 criteria carry a single label
across all 26 calls, and 5 of the 14 sub-categories contain no violation at all, so on those a
constant answer and a perfect model are indistinguishable. With 26 calls, one disagreement is
0.0385.
"""

import io
import os
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NamedTuple

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
    SimilarityReport,
    aggregate_scores,
    character_error_rate,
    classification_report,
    cosine_similarity,
    error_rate_stats,
    mean_pool,
    similarity_stats,
)
from src.google_model.sentiment_telesale.schema.input_gt_schema import InputGTSchema
from src.google_model.sentiment_telesale.schema.output_schema import OutputSchema
from src.google_model.sentiment_telesale.schema.sheet_columns import (
    FALSE_LABEL,
    FAMILIES,
    FAMILY_TITLES,
    FLAG_LABELS,
    JOIN_KEY,
    NA_LABEL,
    SHEET_COLUMNS,
    SUB_CATEGORIES,
    SUB_CATEGORY_COLUMNS,
    SUB_CATEGORY_TITLES,
    normalise_gt_flags,
)
from src.hook.gcp_embedding import VertexAIEmbedding
from src.hook.sharepoint import SharePointModule
from src.logger import Logger, TracedOperation

logger = Logger.get_logger(__name__)

KEY_COLUMN = JOIN_KEY

# The 39 scored columns, their four families and their shared three-label vocabulary are
# imported, not restated: the sheet contract lives in one module, so the pipeline that writes a
# cell and the scorer that reads it cannot drift apart on what it may contain.

# Block titles, also the keys of the dict evaluate() returns. Insertion order is sheet order.
BLOCK_HEADER = "COMPARISON"
BLOCK_COVERAGE = "GROUND-TRUTH COVERAGE"
BLOCK_OVERALL = "OVERALL (by category)"
BLOCK_BY_SUBCATEGORY = "BY SUB-CATEGORY"
BLOCK_BY_FAMILY: dict[str, str] = {
    "OP": "OPERATIONS & PROFESSIONALISM (per criterion)",
    "SE": "SALES EFFECTIVENESS (per criterion)",
    "CX": "CUSTOMER EXPERIENCE (per criterion)",
    "CP": "COMPLIANCE (per criterion)",
}
assert set(BLOCK_BY_FAMILY) == set(FAMILIES)
BLOCK_LEGEND = "HOW TO READ THIS DASHBOARD"

# The three aggregate rows every rolled-up block repeats, in sheet order. Named here rather than
# read off AverageScores.kind at render time so the order is a property of the sheet, not of a
# dataclass field order somebody may reorder.
AVERAGE_KINDS: tuple[str, ...] = ("Macro-average", "Micro-average", "Weighted-average")

# The provenance line printed under a block's title, so no number on the sheet is unattributable
# to a ground-truth column. Blocks absent from this mapping get no subtitle row.
BLOCK_SOURCES: dict[str, str] = {
    BLOCK_COVERAGE: (
        "GT columns: all 39, counted before any join  --  describes the CORPUS, not the run, "
        "so it is identical on every result sheet"
    ),
    BLOCK_OVERALL: (
        "GT columns: all 39, grouped into 4 categories  --  Macro over criteria, Micro over "
        "pooled cells, Weighted by support"
    ),
    BLOCK_BY_SUBCATEGORY: (
        "GT columns: all 39, grouped into the 14 sub-categories production groups by"
    ),
    **{
        BLOCK_BY_FAMILY[family]: "GT columns: " + ", ".join(columns)
        for family, columns in FAMILIES.items()
    },
}

# Keys of the dict compare_rows() returns -- stable identifiers for the verdict frames,
# deliberately not the sheet names. The sheet names are configurable and can pick up a numeric
# suffix at write time; a caller matching frames to sheets must not have to guess which.
COMPARE_BY_FAMILY: dict[str, str] = {
    "OP": "operations",
    "SE": "sales",
    "CX": "experience",
    "CP": "compliance",
}

# The two verdicts a Compare cell can hold, spelled as the reference workbook spells them so the
# two sheets can be read side by side without a mental translation.
MATCH_TRUE = "T"
MATCH_FALSE = "F"

# Sub-headers of a compare sheet's three-cell group, in order.
COMPARE_SUBHEADERS: tuple[str, ...] = ("GT", "AI", "Compare")

# The compare sheets' fixed leading columns, spanning both header rows.
COMPARE_INDEX_COLUMNS: tuple[str, ...] = ("No", KEY_COLUMN)


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
RATIO_COLUMNS = frozenset({"Accuracy", "Macro-F1", "Majority"}) | RATIO_SUFFIXES

# "Majority" is what a model scores by ignoring the call entirely and always answering this
# criterion's commonest ground-truth label, so its scale runs the other way: at 1.0000 the
# column cannot tell a good model from a constant one, and an Accuracy of 1.0000 beside it is
# the floor rather than a result. Carried over from the deleted exact-match module.
INVERTED_RATIO_COLUMNS = frozenset({"Majority"})

COUNT_SUFFIXES = frozenset({"TP", "TN", "FP", "FN", "Support"})
# "Scored", "Excluded" and "Macro-F1 classes" are counts, not scores. Left out, "Scored" has no
# suffix match and would get no format at all, and "Macro-F1 classes" would fall through to the
# ratio scale on the strength of its prefix and paint a 3 solid green, reading as perfect.
# The coverage block's columns are here for the same reason -- "Cells" at 1014 on a 0-1 scale
# would be painted solid green and formatted to 4 decimal places.
COUNT_COLUMNS = (
    frozenset(
        {
            "N",
            "Scored",
            "Excluded",
            "Columns",
            "Criteria",
            "Rows",
            "Macro-F1 classes",
            "GT labels",
            "Cells",
            "Violations (F)",
            "N/A cells",
            "Discriminating",
            "Non-discriminating",
            "Sub-categories",
        }
    )
    | COUNT_SUFFIXES
)

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
        "Every one of the 39 criteria is scored PER LABEL: for T, for F and for N/A, how often "
        "did Google and the human pick it, and when they disagreed, what did each one say. "
        "Scores roll up over three levels - criterion, sub-category, category - and each level "
        "carries a Macro, a Micro and a Weighted row. This is the only telesale scorer; the "
        "Compare Result sheets beside it show the per-call verdicts behind every count here.",
    ),
    (
        "What this sheet deliberately does NOT do",
        "It does not score agreement alone. The production evaluator and the human evaluation "
        "workbook both fix FN and TN at zero, which forces Precision to equal Accuracy and "
        "Recall to be exactly 1.0000 on every criterion - four columns carrying one number. "
        "Measured on this corpus, a model that catches NONE of the 25 violations scores 0.9813 "
        "to 0.9935 by category under that formula. Numbers on this sheet are not comparable "
        "with those rounds, and that is the point.",
    ),
    (
        "Macro / Micro / Weighted",
        "Three ways to average the criteria in a group, printed together because they disagree "
        "in ways that identify HOW a model is wrong. Macro counts every criterion equally, so a "
        "rare label the model never learned drags it down. Micro pools every cell, so the "
        "common answer dominates. Weighted sits between them. A LARGE MACRO-MICRO GAP IS THE "
        "SIGNAL: a model that always answers T scores high Micro and low Macro.",
    ),
    (
        "Accuracy vs Micro Precision/Recall/F1",
        "On the aggregate rows these are equal, and that is arithmetic rather than a "
        "coincidence: with one answer per call, a prediction that is wrong about one label is "
        "wrong about exactly one other, so pooled precision and recall both collapse to the "
        "share of calls answered correctly. Weighted Recall equals Micro Recall for the same "
        "reason. Do not read the agreement of these cells as confirmation of anything.",
    ),
    (
        "Non-discriminating",
        "How many of a group's criteria have only ONE label in the ground truth. Those criteria "
        "cannot tell a good model from a constant one, and 25 of the 39 are in that state. A "
        "group whose Non-discriminating count equals its Criteria count is not measuring the "
        "model at all.",
    ),
    (
        "GROUND-TRUTH COVERAGE (the block at the top)",
        "READ IT BEFORE ANY SCORE. It counts, per sub-category, how many violations the humans "
        "actually recorded. Five of the fourteen sub-categories contain NONE, so on those a "
        "hard-coded T and a genuinely excellent model both score 1.0000 and this dashboard "
        "cannot tell them apart. That is a property of the corpus - 26 calls, 25 violation "
        "cells out of 1014 - and no change to the scoring can fix it. Only more graded calls "
        "can.",
    ),
    (
        "T / F / N/A",
        "The three answers a criterion can have. T = the agent met it. F = the agent did not. "
        "N/A = it did not apply to this call - a REAL CLASS, not a gap. The graders mark N/A on "
        "20 of 26 calls for identity verification and 13 of 26 for cross-sell, and getting it "
        "right is a real skill: it is the model NOT judging something that never happened.",
    ),
    (
        "Scored",
        "Rows that entered the calculation for this criterion - the calls present in both "
        "sheets, minus the ones excluded below. It is NOT necessarily the same number on every "
        "row, so read it beside the Compared count in the banner at the top.",
    ),
    (
        "Excluded",
        "Rows dropped from this column because the result sheet's cell was empty or held "
        "something outside T/F/N-A. An empty result cell means the pipeline produced no answer "
        "for that call - a failed parse, or a file the batch never returned - so it is counted "
        "here rather than scored as a wrong answer. CHECK THE MATRIX SHEET when this is above "
        "0: a high Excluded count means the dashboard is describing fewer calls than it looks "
        "like. The Compare Result sheets still show those rows, so an excluded call is visible "
        "there even though no metric here counts it.",
    ),
    (
        "Accuracy",
        "Share of scored rows where Google picked the same label as the human. It means the "
        "same thing on every block of this sheet: the aggregate rows report the mean of their "
        "criteria (Macro) or the pooled cells (Micro), never a per-label cell count. Careful: "
        "25 of the 39 criteria have only ONE label in the ground truth, so on those a model "
        "that always answers T scores 1.0000 without having judged anything. Read GT labels and "
        "Majority beside it.",
    ),
    (
        "GT labels / Majority",
        "READ THESE BEFORE THE SCORES ON THE SAME ROW. GT labels is how many distinct answers "
        "the humans used for this criterion; Majority is what a model scores by ignoring the "
        "call entirely and always answering the commonest one. Where Majority is 1.0000 an "
        "Accuracy of 1.0000 is the FLOOR, not a result - which is why the Majority column is "
        "colour-scaled the other way round from every other ratio here.",
    ),
    (
        "Macro-F1",
        "Average F1 across every label either side actually used, each weighted equally no "
        "matter how rare. THIS IS THE NUMBER THAT EXPOSES A LAZY MODEL: always answering the "
        "majority label scores high Accuracy and low Macro-F1. Read it with Accuracy, never "
        "instead of it.",
    ),
    (
        "Macro-F1 classes",
        "How many labels went into Macro-F1. READ THIS FIRST. A 1 means the ground truth for "
        "this criterion never varied, so the Accuracy beside it is not evidence of anything - "
        "and 25 of the 39 criteria are in that state. It also tells a Macro-F1 that moved "
        "between runs apart from a model that moved: a 1-label average and a 3-label average "
        "are not comparable.",
    ),
    (
        "TP / TN / FP / FN (per label)",
        "Read one label at a time. TP = human and Google both chose it. TN = neither did. "
        "FP = Google chose it and the human did not (over-calling this answer). FN = the human "
        "chose it and Google did not (missing it).",
    ),
    (
        "Precision / Recall / F1 (per label)",
        "Precision = TP/(TP+FP): when Google gives this answer, how often is it right. "
        "Recall = TP/(TP+FN): of the calls that really had this answer, how many Google found. "
        "F1 balances them. THE F ROW IS THE ONE THAT MATTERS: F Recall is the share of real "
        "violations the model actually caught, and it is the only column on this sheet that a "
        "model answering T everywhere cannot score well on.",
    ),
    (
        "Support",
        "How many calls the human graded with this label (= TP+FN). With only 26 calls in this "
        "corpus, support is small everywhere: most F columns are graded on a single call, so "
        "one disagreement takes that label's F1 from 1.00 to 0.00. Read every per-label score "
        "with that in mind.",
    ),
    (
        "Support 0",
        "A label the human never used. If Google never used it either, the label is left out of "
        "Macro-F1 entirely and its Precision/Recall/F1 read n/a - it is an answer nobody used, "
        "not one anybody got wrong. If Google DID use it (FP above 0), it stays in at F1 0.00, "
        "because inventing an answer the human never gave is a real error.",
    ),
    (
        "Duplicate keys dropped",
        "Calls graded on more than one row. Only the first row of such a call is scored, "
        "because a repeated key would otherwise be cross-joined and counted several times over. "
        "A non-zero number here means the sheet holds gradings this dashboard did not score.",
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
    :class:`src.google_model.sentiment_telesale.google_output.Config`."""

    # SharePoint location of the workbook google_output.py wrote. run_prefix is the run's
    # timestamp folder and is filled in by hand before each evaluation.
    dest_file: str = "/poc_internal_model_migration/voicefiles_telesale_output"
    run_prefix: str = ""
    output_file_name: str = "model_comparison.xlsx"

    gt_sheet_name: str = "Voice_telesale - Groundtruth"
    result_sheet_name: str = "Voice_telesale - Google Result"

    # The five sheets a run appends. resolve_sheet_names() gives them a shared numeric suffix
    # when any one of them is taken, so a run's output is always identifiable as one set.
    eval_sheet_name: str = "Confusion Matrix Dashboard"
    # One compare sheet per family, keyed by COMPARE_BY_FAMILY's values. A dict cannot be a
    # bare dataclass default, hence default_factory.
    compare_sheet_names: dict[str, str] = field(
        default_factory=lambda: {
            "operations": "Operations Compare Result",
            "sales": "Sales Compare Result",
            "experience": "Experience Compare Result",
            "compliance": "Compliance Compare Result",
        }
    )

    # Transcript scoring. Neither ground-truth tab has a transcript column, so the human
    # transcripts come from their own SharePoint folder; the model's own land in ``run_folder``,
    # written there by the output pipeline as one <stem>.txt per call.
    gt_transcript_path: str = "/poc_internal_model_migration/voicefiles_telesale/Transcript"

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


def _normalise(value: Any) -> str:
    """Fold a cell to the form the compare sheets' verdict runs on: stripped, case-insensitive.

    Ported from the deleted exact-match module unchanged, so a verdict cell on a compare sheet
    means exactly what it meant before. It is deliberately *not* what :func:`_canonical` does for
    scoring: this folds and compares, that folds and validates against the vocabulary. A value
    outside ``T``/``F``/``N/A`` scores as a mismatch here rather than being repaired, and the
    sheets print :func:`_clean`'s output so a reader still sees the raw cell.
    """
    return _clean(value).casefold()


class ColumnShape(NamedTuple):
    """How much one criterion's ground truth actually varies.

    ``labels`` is how many distinct answers the graders used -- counting ``N/A``, which is a
    graded answer here; ``majority`` is the share held by the commonest one, i.e. the accuracy
    of always guessing it. A criterion with ``labels`` 1 and ``majority`` 1.0000 cannot
    distinguish a good model from a constant one, and 25 of the 39 telesale criteria are
    exactly that.

    Carried over from the deleted exact-match module. Without it the deletion would have taken
    the only two columns that make a 1.0000 on this corpus readable.
    """

    labels: int
    majority: float


def _column_shape(merged: pd.DataFrame, column: str) -> ColumnShape:
    """Describe one criterion's **ground truth**, independently of what the model answered."""
    values = [_normalise(value) for value in merged[f"{column}_gt"]]
    counts = Counter(value for value in values if value)
    return ColumnShape(
        labels=len(counts),
        majority=(max(counts.values()) / len(values)) if counts and values else 0.0,
    )


def _verdicts(merged: pd.DataFrame, column: str) -> list[str]:
    """One ``T``/``F`` verdict per merged row, for the compare sheets.

    Every row, including the ones :func:`_score_single_label` excludes: the compare sheet exists
    to show what the workbook held, and a row dropped from the metrics is exactly the row a
    reader most needs to see.
    """
    return [
        MATCH_TRUE if _normalise(true) == _normalise(pred) else MATCH_FALSE
        for true, pred in zip(merged[f"{column}_gt"], merged[f"{column}_pred"], strict=True)
    ]


def _stem(value: Any) -> str:
    """Key a workbook row onto one identity, extension dropped.

    Both telesale sheets name calls with the ``.wav`` extension today, so the fold is a no-op
    on the current workbook -- but it is what lets the result sheet carry a GCS object name
    while the ground truth carries a bare ID, as the mnp sheets already do, without the join
    silently matching nothing.
    """
    return Path(_clean(value)).stem


def _canonical(value: Any, by_lower: Mapping[str, str], blank_label: str | None) -> str | None:
    """Fold one cell onto the schema's spelling of its label.

    Case and surrounding whitespace are formatting and fold away -- this is what makes the
    ground truth's ``T`` and ``"N/A "`` score against ``T`` and ``N/A``. Vocabulary does not
    fold: anything else returns None for the caller to exclude and count, never guessed into a
    neighbouring category.

    Args:
        value: The raw cell.
        by_lower: ``{lowercased label: label}`` for the column's label set.
        blank_label: What an empty cell means -- a real label on a column where "no
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
) -> tuple[ClassificationReport, int]:
    """Score one single-label column, excluding rows either side graded outside ``labels``.

    An out-of-vocabulary value is dropped from this column only, and the count reaches the
    dashboard's ``Scored``/``Excluded`` cells. On telesale that is nearly always an empty
    *result* cell -- a call the pipeline produced no answer for.

    ``blank_label`` decides what an empty cell means, and telesale passes None: ``N/A`` is
    already a real label in this vocabulary, so an empty cell is an absent answer rather than
    a graded one, and folding the two would credit the model with an answer it never gave.

    Returns:
        ``(report, dropped)``.
    """
    by_lower = {label.casefold(): label for label in labels}
    y_true: list[str] = []
    y_pred: list[str] = []
    dropped = 0

    for raw_true, raw_pred in zip(merged[f"{column}_gt"], merged[f"{column}_pred"], strict=True):
        true = _canonical(raw_true, by_lower, blank_label)
        pred = _canonical(raw_pred, by_lower, blank_label)
        if true is None or pred is None:
            dropped += 1
            continue
        y_true.append(true)
        y_pred.append(pred)

    if dropped:
        # The count and the column, never the value. A stray cell can hold call content, and
        # this log is indexed by Cloud Logging.
        logger.warning(
            "google_sentiment_telesale_confusion_matrix.column.excluded",
            column=column,
            dropped=dropped,
            scored=len(y_true),
        )

    return classification_report(y_true, y_pred, labels), dropped


def _score_cells(score: LabelScore, *, used: bool, prefix: str = "") -> dict[str, Any]:
    """The three score cells for one class, blanked (n/a) when neither side used it.

    Zeros from all-zero denominators would paint a perfect column red; the caller's counts
    (``TP 0 / Support 0``) show *why* the score is blank.
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

    ``labels`` is the *block's* full label set, not the report's, so every column in the table
    exists on every row even when a particular column never sees a particular label.

    The same distinction applies one level down, to a class present in the report but used by
    neither side -- see :func:`_score_cells`.
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


#: Criterion -> the sub-category it belongs to. Inverted once, at import, rather than searched
#: per row: _single_label_block runs it 39 times per dashboard.
_SUB_CATEGORY_OF: dict[str, str] = {
    column: sub for sub, columns in SUB_CATEGORY_COLUMNS.items() for column in columns
}


def _single_label_block(
    reports: Mapping[str, tuple[ClassificationReport, int]],
    labels: Sequence[str],
    shapes: Mapping[str, ColumnShape],
) -> pd.DataFrame:
    """Build a per-criterion block for one family.

    ``GT labels`` and ``Majority`` sit immediately after the criterion's name, before any score,
    because they say whether the scores beside them can mean anything. See :class:`ColumnShape`.
    """
    rows = [
        {
            "Criterion": column,
            "Sub-category": SUB_CATEGORY_TITLES[_SUB_CATEGORY_OF[column]],
            "GT labels": shapes[column].labels,
            "Majority": round(shapes[column].majority, 4),
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


def _average_rows(
    reports: Mapping[str, tuple[ClassificationReport, int]],
    lead: Mapping[str, Any],
    shapes: Mapping[str, ColumnShape],
    note: str = "",
) -> list[dict[str, Any]]:
    """The Macro / Micro / Weighted rows for one group of criteria.

    Every criterion's per-class scores are pooled into one sequence and handed to the shared
    :func:`~src.google_model.metrics.aggregate_scores` -- the same helper the mnp and retention
    dashboards use, so the three cannot average three different ways. Pooling across
    ``(criterion, label)`` pairs is what makes the Micro row a per-cell number rather than a
    per-criterion one.

    **Accuracy is computed here rather than taken from aggregate_scores**, and the difference
    matters. That helper averages one-vs-rest LabelScores, so its accuracy counts three
    decisions per call -- one per label -- and a wrong answer still scores the two labels it
    correctly stayed away from. That is the right reading for mnp's and retention's per-class
    tables, whose rows *are* classes. It is the wrong reading here, where every row of the block
    below is a criterion whose Accuracy is plain "calls the two graders agreed on". Two columns
    named Accuracy on one sheet must mean one thing, so this one is the plain reading:

    * **Macro** -- the mean of the group's criteria accuracies, each criterion counting once.
    * **Micro** -- pooled: correct cells over scored cells.
    * **Weighted** -- each criterion's accuracy weighted by its own row count. That collapses to
      the Micro figure by construction, and is printed anyway so the column has a value on every
      row rather than a gap the reader has to interpret.

    Precision, Recall and F1 do come from the shared
    :func:`~src.google_model.metrics.aggregate_scores`, so this dashboard, mnp's and retention's
    cannot average them three different ways. One identity follows from single-label scoring and
    is stated in the legend rather than hidden: Micro Precision, Recall and F1 are all equal to
    the Micro accuracy above them, because a single-label prediction that is wrong about one
    class is wrong about exactly one other.
    """
    pooled = [score for report, _ in reports.values() for score in report.per_label]
    aggregates = aggregate_scores(pooled)
    blind = sum(1 for column in reports if shapes[column].labels < 2)

    # Summed from per_label rather than as accuracy * n: tp over every class of a single-label
    # report is exactly the number of rows the two graders agreed on, in integer arithmetic.
    correct = sum(
        score.tp for report, _ in reports.values() for score in report.per_label
    )
    scored = sum(report.n for report, _ in reports.values())
    accuracies = {
        "Macro-average": (
            sum(report.accuracy for report, _ in reports.values()) / len(reports)
            if reports
            else 0.0
        ),
        "Micro-average": correct / scored if scored else 0.0,
        "Weighted-average": correct / scored if scored else 0.0,
    }

    return [
        {
            **lead,
            "Average": average.kind,
            "Criteria": len(reports),
            "Rows": max((report.n for report, _ in reports.values()), default=0),
            "Accuracy": round(accuracies[average.kind], 4),
            "Precision": round(average.precision, 4),
            "Recall": round(average.recall, 4),
            "F1": round(average.f1, 4),
            "Non-discriminating": blind,
            "Excluded": sum(dropped for _, dropped in reports.values()),
            # On the Macro row only: repeating it three times per group would trade a warning
            # for wallpaper, and Macro is the row the caveat actually bears on.
            "Note": note if average.kind == AVERAGE_KINDS[0] else "",
        }
        for average in aggregates.rows()
    ]


def _coverage_block(gt_df: pd.DataFrame) -> pd.DataFrame:
    """What this corpus can and cannot measure, per sub-category.

    Built from the **ground-truth frame alone, before any join**: it describes the corpus, not
    the run, so it is identical on every result sheet and a reader can compare two runs' scores
    knowing the measuring stick did not move.

    It exists because 25 of the 39 telesale criteria hold one label across all 26 calls, and 5 of
    the 14 sub-categories contain no violation at all. On those, a hard-coded ``T`` and a
    genuinely excellent model both score 1.0000 -- and without this block a reader has no way to
    tell that the number is uninformative rather than good.
    """
    normalised = normalise_gt_flags(gt_df)
    calls = len(normalised)
    rows: list[dict[str, Any]] = []
    totals = {"criteria": 0, "cells": 0, "violations": 0, "na": 0, "discriminating": 0}

    for family, sub_category in SUB_CATEGORIES:
        columns = SUB_CATEGORY_COLUMNS[sub_category]
        present = [column for column in columns if column in normalised.columns]
        per_column = {
            column: int((normalised[column] == FALSE_LABEL).sum()) for column in present
        }
        violations = sum(per_column.values())
        na_cells = sum(int((normalised[column] == NA_LABEL).sum()) for column in present)
        discriminating = sum(1 for column in present if normalised[column].nunique() > 1)

        notes: list[str] = []
        if not violations:
            notes.append("no violation in ground truth -- a constant answer scores 1.0000 here")
        else:
            top_column, top_count = max(per_column.items(), key=lambda item: item[1])
            # Two thirds on one criterion means the group's average is really that criterion's
            # score wearing a group's name -- call_opening is 8 of 9 on one criterion. Below
            # three violations the concentration is arithmetic rather than information ("1 of 1
            # sits on one criterion" is true of every group with one violation), so it is not
            # reported.
            if len(present) > 1 and violations >= 3 and top_count * 3 >= violations * 2:
                notes.append(
                    f"{top_count} of the {violations} violations sit on one criterion, "
                    f"{top_column}"
                )
        if blind := len(present) - discriminating:
            notes.append(f"{blind} of {len(present)} criteria hold one label on every call")

        rows.append(
            {
                "Category": FAMILY_TITLES[family],
                "Sub-category": SUB_CATEGORY_TITLES[sub_category],
                "Criteria": len(present),
                "Cells": len(present) * calls,
                "Violations (F)": violations,
                "N/A cells": na_cells,
                "Discriminating": discriminating,
                "Note": "; ".join(notes),
            }
        )
        totals["criteria"] += len(present)
        totals["cells"] += len(present) * calls
        totals["violations"] += violations
        totals["na"] += na_cells
        totals["discriminating"] += discriminating

    rows.append(
        {
            "Category": "TOTAL",
            "Sub-category": "",
            "Criteria": totals["criteria"],
            "Cells": totals["cells"],
            "Violations (F)": totals["violations"],
            "N/A cells": totals["na"],
            "Discriminating": totals["discriminating"],
            "Note": (
                f"{totals['discriminating']} of {totals['criteria']} criteria can discriminate; "
                f"{totals['violations']} violation cells in {totals['cells']} over {calls} calls"
            ),
        }
    )
    return pd.DataFrame(rows)


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
            "google_sentiment_telesale_confusion_matrix.transcript.excluded",
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


def _merge(
    gt_df: pd.DataFrame, result_df: pd.DataFrame
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Join the two sheets on :data:`KEY_COLUMN`, keyed on the file **stem**.

    The stem, not the raw cell: the ground-truth tab names calls by a bare ID and the result
    sheet by the GCS object name, so a literal join would produce an empty frame and a dashboard
    reporting 0 of 100 rows compared.

    A key repeated on either sheet is reduced to its first row before the join -- see the
    comment on the deduplication below.

    Returns:
        ``(merged, counts)``, where ``counts`` carries ``gt_only``, ``result_only``,
        ``gt_duplicates`` and ``result_duplicates``. Raises KeyError if either frame lacks
        the key.
    """
    for name, frame in (("ground truth", gt_df), ("result", result_df)):
        if KEY_COLUMN not in frame.columns:
            raise KeyError(f"The {name} sheet has no {KEY_COLUMN!r} column.")

    gt = gt_df.copy()
    result = result_df.copy()
    gt[KEY_COLUMN] = gt[KEY_COLUMN].map(_stem)
    result[KEY_COLUMN] = result[KEY_COLUMN].map(_stem)

    # Deduplicate BEFORE merging. pandas cross-joins a repeated key, and the retention
    # ground-truth tab grades three calls on two rows each (one per product) -- which turns
    # those 6 rows into 12, pushes the scored count above the sheet's own row count, and pairs
    # one row's answer against the other's. Keeping the first occurrence is the only option
    # that does not invent a combined grade out of two real ones; the count is reported rather
    # than silently absorbed, because a dropped row is a call that stopped being scored.
    gt_duplicates = int(gt[KEY_COLUMN].duplicated().sum())
    result_duplicates = int(result[KEY_COLUMN].duplicated().sum())
    gt = gt.drop_duplicates(subset=KEY_COLUMN, keep="first")
    result = result.drop_duplicates(subset=KEY_COLUMN, keep="first")

    merged = gt.merge(result, on=KEY_COLUMN, how="inner", suffixes=("_gt", "_pred"))
    counts = {
        "gt_only": len(set(gt[KEY_COLUMN]) - set(result[KEY_COLUMN])),
        "result_only": len(set(result[KEY_COLUMN]) - set(gt[KEY_COLUMN])),
        "gt_duplicates": gt_duplicates,
        "result_duplicates": result_duplicates,
    }

    return merged, counts


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

    merged, join_counts = _merge(gt_df, result_df)

    logger.info(
        "google_sentiment_telesale_confusion_matrix.join.completed",
        gt_rows=len(gt_df),
        result_rows=len(result_df),
        matched=len(merged),
        **join_counts,
    )

    # WARNING, not INFO: a duplicate key means a graded call was dropped, so the
    # dashboard is scoring fewer calls than the sheet contains.
    if join_counts["gt_duplicates"] or join_counts["result_duplicates"]:
        logger.warning(
            "google_sentiment_telesale_confusion_matrix.join.duplicate_keys",
            gt_duplicates=join_counts["gt_duplicates"],
            result_duplicates=join_counts["result_duplicates"],
            matched=len(merged),
        )

    # No blank_label: "N/A" already IS this sheet's "nothing to judge" answer, so an empty
    # result cell is not a graded value but a call the pipeline never answered for. Those rows
    # are excluded from the column and counted in Excluded -- folding them into a category
    # would credit the model with an answer it never gave.
    reports = {
        family: {column: _score_single_label(merged, column, FLAG_LABELS) for column in columns}
        for family, columns in FAMILIES.items()
    }

    # Describes the ground truth, not the answer, so it is computed once per criterion and used
    # by both the per-criterion blocks and the Non-discriminating counts above them.
    shapes = {column: _column_shape(merged, column) for column in SHEET_COLUMNS}

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

    coverage = _coverage_block(gt_df)

    overall_rows: list[dict[str, Any]] = []
    for family, columns in FAMILIES.items():
        blind = sum(1 for column in columns if shapes[column].labels < 2)
        overall_rows.extend(
            _average_rows(
                reports[family],
                {"Category": FAMILY_TITLES[family]},
                shapes,
                note=(
                    f"{blind} of {len(columns)} criteria hold one label on every call, so their "
                    "1.0000 is the floor rather than a result"
                )
                if blind
                else "",
            )
        )

    if score_transcripts:
        overall_rows.append(
            {
                "Category": FAMILY_TRANSCRIPT,
                # Deliberately not one of AVERAGE_KINDS: there is one transcript per call, so
                # there are no classes to average over, and calling this "Macro-average" would
                # invite a comparison with the rows above that measure something else.
                "Average": "1 - mean CER",
                "Criteria": 1,
                "Rows": transcript_errors.n,
                # The one family here with a genuine character-level accuracy: 1 - CER is the
                # share of characters transcribed correctly, on the same 0-1 scale as the
                # accuracies above it. Cosine has no such reading, which is why it sits in the
                # Note rather than in a cell.
                "Accuracy": round(1.0 - transcript_errors.mean, 4),
                "Precision": NOT_APPLICABLE,
                "Recall": NOT_APPLICABLE,
                "F1": NOT_APPLICABLE,
                "Non-discriminating": 0,
                "Excluded": transcript_excluded,
                "Note": (
                    f"mean cosine {transcript_similarity.mean:.4f}; "
                    f"CER pass@{cer_threshold:.2f} {transcript_errors.pass_rate:.4f}"
                ),
            }
        )

    overall = pd.DataFrame(overall_rows)

    by_sub_category = pd.DataFrame(
        [
            row
            for family, sub_category in SUB_CATEGORIES
            for row in _average_rows(
                {
                    column: reports[family][column]
                    for column in SUB_CATEGORY_COLUMNS[sub_category]
                },
                {
                    "Category": FAMILY_TITLES[family],
                    "Sub-category": SUB_CATEGORY_TITLES[sub_category],
                },
                shapes,
            )
        ]
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
                # The denominator, printed rather than implied. The stakeholders' workbook
                # divides every item by 27 over a corpus of 26 graded calls; a reader can only
                # catch that class of error if the sheet says what it counted.
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
        BLOCK_COVERAGE: coverage,
        BLOCK_OVERALL: overall,
        BLOCK_BY_SUBCATEGORY: by_sub_category,
        **{
            BLOCK_BY_FAMILY[family]: _single_label_block(reports[family], FLAG_LABELS, shapes)
            for family in FAMILIES
        },
    }

    # Optional and inserted before the legend, because dict insertion order is the sheet's row
    # order: a run without transcripts simply has one block fewer.
    if transcript_block is not None:
        blocks[BLOCK_TRANSCRIPT] = transcript_block

    blocks[BLOCK_LEGEND] = pd.DataFrame(LEGEND, columns=["Term", "Meaning"])

    logger.info(
        "google_sentiment_telesale_confusion_matrix.evaluate.completed",
        matched=len(merged),
        transcripts_scored=transcript_errors.n if transcript_errors else 0,
        transcript_chunks=transcript_chunks,
        scored_columns=len(SHEET_COLUMNS),
        degenerate_columns=sum(
            1
            for family in reports
            for r, _ in reports[family].values()
            if len(r.macro_labels) < 2
        ),
        excluded=sum(d for family in reports for _, d in reports[family].values()),
        elapsed_ms=_elapsed_ms(started),
    )
    return blocks


def _compare_frame(
    merged: pd.DataFrame, columns: Sequence[str], verdicts: Mapping[str, Sequence[str]]
) -> pd.DataFrame:
    """One compare sheet's rows: a running number, the call, then GT/AI/Compare per criterion.

    The GT and AI cells hold :func:`_clean`'s output, not :func:`_normalise`'s. The sheet exists
    to show what the workbook actually held, so a reader chasing an ``F`` verdict sees ``N/A``
    against an empty cell rather than a tidied rewrite that would hide which of the two the
    pipeline failed to produce.

    Column names are flat (``"op_language_and_tone_clarity GT"``) even though the sheet renders a
    merged two-row header. The frame is the data; :func:`write_comparison_sheets` owns how it
    looks, and a frame with duplicate column labels could not be built at all.
    """
    if not len(merged):
        # An empty join still has to produce the right shape, or the sheet loses its header.
        headers = [
            *COMPARE_INDEX_COLUMNS,
            *(f"{column} {sub}" for column in columns for sub in COMPARE_SUBHEADERS),
        ]
        return pd.DataFrame(columns=headers)

    # Column-wise construction: same column order, values and dtypes as a per-row dict would
    # give, without materialising a Series per row.
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
    merged: pd.DataFrame,
    verdicts: Mapping[str, Mapping[str, Sequence[str]]],
) -> dict[str, pd.DataFrame]:
    """Assemble the compare-sheet frames from already-computed verdicts.

    One sheet per family rather than one for all 39 criteria: three cells per criterion would
    put 117 columns on a single sheet, which no reader can scan.
    """
    return {
        COMPARE_BY_FAMILY[family]: _compare_frame(merged, columns, verdicts[family])
        for family, columns in FAMILIES.items()
    }


def compare_rows(gt_df: pd.DataFrame, result_df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Build the per-call verdict frames behind the dashboard's blocks.

    The evidence for the counts: a criterion scoring 0.6923 says nothing about *which* calls it
    failed, and this is where that is answered. Free of I/O, and joins through :func:`_merge`, so
    its rows are the same rows :func:`evaluate` scored, in the same order.

    Ported from the deleted exact-match module, which is where these sheets used to live. The
    verdict is still plain cell equality: unlike mnp and retention, telesale has no set
    semantics, so there is nothing rank- or order-dependent for a verdict to get wrong.

    Args:
        gt_df: The ground-truth sheet.
        result_df: The model result sheet.

    Returns:
        ``{sheet key: frame}`` keyed by :data:`COMPARE_BY_FAMILY`'s values, insertion-ordered.
        The caller maps those keys onto real sheet names via :class:`Config`.

    Raises:
        KeyError: If either frame is missing the join key.
    """
    started = time.monotonic()
    merged, _ = _merge(gt_df, result_df)

    verdicts = {
        family: {column: _verdicts(merged, column) for column in columns}
        for family, columns in FAMILIES.items()
    }

    frames = _build_compare_frames(merged, verdicts)

    logger.info(
        "google_sentiment_telesale_confusion_matrix.compare_rows.completed",
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
            logger.warning("google_sentiment_telesale_confusion_matrix.transcript.skipped", path=_safe_name(path))
            return wanted[path], None

    transcripts: dict[str, str] = {}

    if targets:
        with ThreadPoolExecutor(max_workers=min(concurrency, len(targets))) as pool:
            for stem, text in pool.map(fetch, targets):
                if text is not None:
                    transcripts[stem] = text

    logger.info(
        "google_sentiment_telesale_confusion_matrix.transcripts.downloaded",
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
        # column A's width, which is sized for criterion names.
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
            worksheet.conditional_formatting.add(
                cells, _colour_scale(invert=column_name in INVERTED_RATIO_COLUMNS)
            )
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
        "google_sentiment_telesale_confusion_matrix.dashboard.written",
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

        for offset, sub_header in enumerate(COMPARE_SUBHEADERS):
            cell = worksheet.cell(row=2, column=start + offset, value=sub_header)
            cell.fill = COMPARE_SUBHEADER_FILL
            cell.font = COMPARE_SUBHEADER_FONT
            cell.alignment = Alignment(horizontal="center")

    worksheet.column_dimensions["A"].width = COMPARE_NUMBER_COLUMN_WIDTH
    worksheet.column_dimensions["B"].width = COMPARE_KEY_COLUMN_WIDTH
    for position in range(index_width + 1, width + 1):
        worksheet.column_dimensions[get_column_letter(position)].width = (
            COMPARE_VALUE_COLUMN_WIDTH
        )

    # Both header rows above, and both index columns to the left, stay put while scrolling --
    # with 15 triplets the call name is otherwise off-screen long before its Compare cell is.
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
        "google_sentiment_telesale_confusion_matrix.compare_sheets.written",
        # Sheet names and row counts only -- the sheets themselves hold graded call content.
        sheets=[sheet_names[key] for key in frames],
        rows=max((len(frame) for frame in frames.values()), default=0),
        bytes=len(updated),
        elapsed_ms=_elapsed_ms(started),
    )
    return updated


def run(config: Config | None = None) -> None:
    """Download the workbook, score it, and upload it back with five sheets appended.

    All five go up in one upload -- a dashboard without its verdict sheets would claim counts
    nothing on the workbook can be checked against.

    Omitted ``config`` takes the class defaults, including the hand-edited ``run_prefix``.
    """
    config = config if config is not None else Config()

    with TracedOperation(
        "google_sentiment_telesale_confusion_matrix.run",
        workbook=config.workbook_path,
    ):
        logger.info(
            "google_sentiment_telesale_confusion_matrix.run.starting",
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

        with TracedOperation("google_sentiment_telesale_confusion_matrix.load"):
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
                        "google_sentiment_telesale_confusion_matrix.run.aborted",
                        reason="sheet_missing",
                        file=config.workbook_path,
                        missing_sheets=missing,
                        # Sheet names only -- never cell content.
                        available=sheet_names,
                    )
                    return

                # keep_default_na=False and NOTHING in na_values: pandas' default NA set
                # contains the literal string 'N/A', which on this sheet is a graded answer --
                # "the criterion did not apply" -- on 20 of 26 calls for the verification trio.
                # Parsing it as a null would erase the very distinction the sheet encodes.
                read_kwargs = {
                    "dtype": str,
                    "header": 0,
                    "keep_default_na": False,
                    "na_values": [],
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
                        "google_sentiment_telesale_confusion_matrix.run.aborted",
                        reason="schema_invalid",
                        file=config.workbook_path,
                        sheet=label,
                        missing_columns=dropped,
                        error_type=type(e).__name__,
                        schema=getattr(e.schema, "name", None),
                    )
                    return

            # Ground truth only. A blank ground-truth cell means "did not apply" and folds to
            # "N/A"; a blank *result* cell means the pipeline produced no answer for that call,
            # and is left blank so it falls out of vocabulary and lands in Excluded rather than
            # being scored as a correct N/A. See sheet_columns.normalise_gt_flags.
            gt_df = normalise_gt_flags(frames[config.gt_sheet_name])
            result_df = frames[config.result_sheet_name]

            logger.info(
                "google_sentiment_telesale_confusion_matrix.load.completed",
                file=config.workbook_path,
                gt_rows=len(gt_df),
                result_rows=len(result_df),
                bytes=len(workbook_byte),
            )

        with TracedOperation("google_sentiment_telesale_confusion_matrix.transcripts"):
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
                    "google_sentiment_telesale_confusion_matrix.transcripts.skipped",
                    gt_folder=config.gt_transcript_path,
                    result_folder=config.run_folder,
                )

            if gt_transcripts is not None and pred_transcripts is not None:
                both = len(gt_transcripts.keys() & pred_transcripts.keys())
                logger.info(
                    "google_sentiment_telesale_confusion_matrix.transcripts.loaded",
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
                        "google_sentiment_telesale_confusion_matrix.transcripts.skipped",
                        reason="no_overlapping_stems",
                        gt_folder=config.gt_transcript_path,
                        result_folder=config.run_folder,
                    )

        with TracedOperation("google_sentiment_telesale_confusion_matrix.evaluate"):
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

        with TracedOperation("google_sentiment_telesale_confusion_matrix.compare"):
            compare_frames = compare_rows(gt_df, result_df)

        with TracedOperation("google_sentiment_telesale_confusion_matrix.write"):
            compare_bases = {
                key: config.compare_sheet_names[key] for key in COMPARE_BY_FAMILY.values()
            }
            requested = [config.eval_sheet_name, *compare_bases.values()]
            resolved = resolve_sheet_names(sheet_names, requested)
            sheet_name, *compare_resolved = resolved
            compare_names = dict(zip(compare_bases, compare_resolved, strict=True))

            if sheet_name != config.eval_sheet_name:
                logger.info(
                    "google_sentiment_telesale_confusion_matrix.sheet.renamed",
                    requested=requested,
                    resolved=resolved,
                )

            updated = write_dashboard(workbook_byte, blocks, sheet_name)
            del workbook_byte  # free the downloaded copy before the second openpyxl load
            updated = write_comparison_sheets(updated, compare_frames, compare_names)

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
                    "google_sentiment_telesale_confusion_matrix.output.skipped",
                    path=config.workbook_path,
                    sheet=sheet_name,
                )

        logger.info(
            "google_sentiment_telesale_confusion_matrix.run.completed",
            workbook=config.workbook_path,
            sheet=sheet_name,
            compare_sheets=list(compare_names.values()),
            gt_rows=len(gt_df),
            result_rows=len(result_df),
            blocks=len(blocks),
            uploaded=uploaded,
        )
