"""Score the Gemini result sheet against human ground truth and write an evaluation dashboard.

Reads the workbook :mod:`src.google_model.sentiment.google_output` produced -- ``Voice - Groundtruth`` and
``Voice - Google Result`` -- scores every judgement column, and appends an ``Evaluation Dashboard``
sheet to the same file.

The maths lives in :mod:`src.google_model.metrics` and the API call in
:mod:`src.hook.gcp_embedding`; what lives here is the part specific to *this* workbook: which
columns belong to which family, what each column's label set is, and how a raw cell becomes a
scoreable value. :func:`evaluate` takes DataFrames and an injected embedder, so the whole scoring
path runs offline against a local copy with no credentials.
"""

import io
import os
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
    character_error_rate,
    classification_report,
    cosine_similarity,
    error_rate_stats,
    mean_pool,
    multilabel_report,
    similarity_stats,
)
from src.google_model.sentiment.schema.input_gt_schema import InputGTSchema
from src.google_model.sentiment.schema.output_schema import OutputSchema
from src.hook.gcp_embedding import VertexAIEmbedding
from src.hook.sharepoint import SharePointModule
from src.logger import Logger, TracedOperation

logger = Logger.get_logger(__name__)

KEY_COLUMN = "Voice File Name"

# Sheet spellings, in sheet order. Two differ from ServiceQuality's field names --
# company_verification/self_service on the sheet are customer_verification/true_application in
# the schema -- and build_output_df in google_output.py owns that mapping. Here the sheet is the
# only side that exists, so the sheet's spelling is the one used throughout.
QA_CRITERIA: tuple[str, ...] = (
    "greeting_standard",
    "manners",
    "enthusiasm",
    "communication_skill",
    "ending_standard",
    "data_privacy",
    "legal_verification",
    "company_verification",
    "sla_notification",
    "transfer_standard",
    "problem_understanding",
    "compensation",
    "hold_standard",
    "wrap_up",
    "beyond_scope_support",
    "self_service",
    "case_ownership",
    "contact_confirm",
    "retention",
    "downsell",
    "mnp",
    "upselling",
)

# These four are BinaryCriterionEvaluation in model_response.py -- Literal["Meet", "Below"], with
# no N/A. Scoring them against the ternary label set would add a class with zero support to the
# macro-F1 denominator and drag every one of their scores down by a third for no reason.
BINARY_CRITERIA = frozenset(
    {"manners", "enthusiasm", "communication_skill", "problem_understanding"}
)

SENTIMENT_COLUMNS: tuple[str, ...] = (
    "overall_sentiment",
    "initial_sentiment",
    "final_sentiment",
)

CALL_TYPE_COLUMN = "call_type"
SUMMARY_COLUMN = "summary_story"

# "N/A" is a graded answer meaning "this criterion did not apply to this call", not a missing
# value. Every read below passes keep_default_na=False so pandas does not turn it into NaN.
CRITERION_LABELS: tuple[str, ...] = ("Meet", "Below", "N/A")
BINARY_LABELS: tuple[str, ...] = ("Meet", "Below")
SENTIMENT_LABELS: tuple[str, ...] = ("Positive", "Neutral", "Negative")

# Fixed at the five types model_response.py documents. Deliberately no synonym map: folding
# "Sales" into "Sale" or "Downsell" into "Retention" would be a business judgement, and metric
# code is the wrong place to make one. An unrecognised token is dropped and counted instead.
CALL_TYPE_LABELS: tuple[str, ...] = (
    "Enquiry",
    "Service Request",
    "Complaint",
    "Sale",
    "Retention",
)
_CALL_TYPE_BY_LOWER = {label.lower(): label for label in CALL_TYPE_LABELS}

FAMILY_QA = "QA Criteria"
FAMILY_SENTIMENT = "Sentiment"
FAMILY_CALL_TYPE = "Call Type"
FAMILY_SUMMARY = "Summary Story"
FAMILY_TRANSCRIPT = "Transcript"

# Block titles, also the keys of the dict evaluate() returns. Insertion order is sheet order.
BLOCK_HEADER = "COMPARISON"
BLOCK_OVERALL = "OVERALL (by family)"
BLOCK_QA = "QA CRITERIA (per column)"
BLOCK_SENTIMENT = "SENTIMENT (per column)"
BLOCK_CALL_TYPE_SUMMARY = "CALL TYPE"
BLOCK_CALL_TYPE_LABELS = "CALL TYPE (per label)"
BLOCK_SUMMARY = "SUMMARY STORY"
BLOCK_TRANSCRIPT = "TRANSCRIPT"
BLOCK_LEGEND = "HOW TO READ THIS DASHBOARD"

NOT_APPLICABLE = "n/a"

# Chunk size for embedding a transcript, in characters. gemini-embedding-001 caps input at 2048
# tokens; Thai runs roughly 1.5-3 characters per token, so 2000 characters leaves headroom at the
# dense end. Under-sizing costs a few more requests, over-sizing costs a 400 -- which is the point
# of VertexAIEmbedding.DEFAULT_AUTO_TRUNCATE being False.
DEFAULT_CHUNK_CHARS = 2000

# CER at or below which a transcript counts as a pass. Note the direction: this is the one metric
# on the sheet where a *smaller* number is better.
DEFAULT_CER_THRESHOLD = 0.20

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
TAB_COLOR = "1F3864"

# Excel's own three-colour-scale stops, so the sheet looks native rather than themed by hand.
SCALE_LOW, SCALE_MID, SCALE_HIGH = "F8696B", "FFEB84", "63BE7B"

LABEL_COLUMN_WIDTH = 30
DATA_COLUMN_WIDTH = 13
# Columns the legend's Meaning cell is merged across. Its own column would otherwise have to be
# ~110 wide, and column B is "Scored" in three of the tables above it.
LEGEND_SPAN = 9
LEGEND_LINE_CHARS = 115
LEGEND_LINE_HEIGHT = 15

# Matched on the whole name and on the last word, because _class_cells prefixes every per-class
# column with its label -- "F1" and "Meet F1" are the same metric and must format alike.
RATIO_SUFFIXES = frozenset({"Precision", "Recall", "F1"})
RATIO_COLUMNS = frozenset({"Accuracy", "Macro-F1"}) | RATIO_SUFFIXES
COUNT_SUFFIXES = frozenset({"TP", "TN", "FP", "FN", "Support"})
# "Macro-F1 classes" is a class count, not a score. Left out it would fall through to the ratio
# scale on the strength of its prefix and paint a 3 solid green, reading as a perfect result.
COUNT_COLUMNS = (
    frozenset({"Scored", "Excluded", "Columns", "Rows", "Macro-F1 classes"}) | COUNT_SUFFIXES
)

# Rows of a Metric/Value block whose Value is a 0-1 ratio. Matched by name rather than position
# so reordering the block cannot silently colour a count on a 0-1 scale -- 298 would paint solid
# green. A pass rate carries its threshold in the label, so those are matched by prefix.
RATIO_METRICS = frozenset(
    {
        "Exact set match",
        "Micro-F1",
        "Micro-Precision",
        "Micro-Recall",
        "Macro-F1",
        "Mean cosine",
        "Median cosine",
        "Lowest cosine",
        "Highest cosine",
        "Mean char accuracy (1 - CER)",
    }
)

# CER rows. Ratios like the set above -- so they format at 4dp and get a colour scale -- but the
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
        "Scored",
        "Rows actually compared for this column. Below the compared-row count means the ground "
        "truth held a value outside the allowed list, so those rows were skipped.",
    ),
    (
        "Accuracy",
        "Share of rows where Google's answer equals the human grader's answer. Careful: a column "
        'that is 95% "N/A" scores ~0.95 just by always answering "N/A". Never read it without '
        "Macro-F1 beside it.",
    ),
    (
        "Macro-F1",
        "Average F1 across every class that either side actually used, each class weighted equally "
        "regardless of how rare it is. High Accuracy with low Macro-F1 = right on the common "
        "answer, wrong on the rare one. This is the number that shows real skill.",
    ),
    ("TP (True Positive)", "Human said this class, Google said this class. Correct hit."),
    (
        "TN (True Negative)",
        "Human said something else, Google said something else. Correct rejection.",
    ),
    ("FP (False Positive)", "Google said this class, the human did not. Over-calling."),
    ("FN (False Negative)", "Human said this class, Google did not. Missed it."),
    ("Precision", "Of the times Google said this class, how often it was right = TP/(TP+FP)."),
    ("Recall", "Of the times the human said this class, how often Google caught it = TP/(TP+FN)."),
    ("F1", "One score balancing Precision and Recall. 1.00 perfect, 0.00 worst."),
    (
        "Support",
        "How many rows the human graded as this class (= TP+FN). Small support means the scores "
        "swing on one or two rows - read them with caution.",
    ),
    (
        "Support 0",
        "A class the human never used in this column. If Google never used it either, the class is "
        "left out of Macro-F1 entirely and its Precision/Recall/F1 read n/a - it is a class nobody "
        "used, not a class anybody got wrong. If Google DID use it (FP above 0), it stays in at F1 "
        "0.00, because inventing a grade the human never gave is a real error. Small support of "
        "any size makes a score swing on one or two rows, so read a low Macro-F1 against the "
        "Support cells beside it before concluding the model failed.",
    ),
    (
        "Macro-F1 classes",
        "How many classes went into this column's Macro-F1 - 3 normally, fewer when a class was "
        "unused by both sides. It is here so a Macro-F1 that moved between runs can be told apart "
        "from a model that moved: a 2-class average and a 3-class average are not comparable.",
    ),
    (
        "Exact set match",
        "Call Type only. Share of calls where Google listed exactly the same set of call types as "
        "the human, in any order.",
    ),
    (
        "Micro-F1",
        "Call Type only. F1 over every individual call-type decision, so frequent types such as "
        "Service Request dominate it. Macro-F1 treats all five types equally.",
    ),
    (
        "Mean cosine",
        "Summary only. Average meaning-similarity between Google's Thai summary and the human's, "
        "0.00-1.00. 1.00 = same meaning even if the wording differs entirely.",
    ),
    ("Pass rate", "Summary only. Share of summaries scoring at or above the threshold."),
    (
        "CER (Character Error Rate)",
        "Transcript only. The share of characters Google got wrong against the human transcript, "
        "counting insertions, deletions and substitutions. LOWER IS BETTER - this is the only "
        "metric on this sheet that reads that way, and its colours are reversed to match: green "
        "is a low CER. 0.00 is a perfect transcription. It can exceed 1.00 when Google produced "
        "far more text than the human did.",
    ),
    (
        "Char accuracy (1 - CER)",
        "Transcript only. The same number stated the usual way round, so it can be compared "
        "against the Accuracy column above it. 0.90 means roughly 9 characters in 10 are right.",
    ),
    (
        "CER pass rate",
        "Transcript only. Share of calls whose CER is at or BELOW the threshold - the opposite "
        "direction to the summary's pass rate, because a low CER is the good outcome.",
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
        'Metric does not apply here - e.g. the "N/A" class on manners, enthusiasm, '
        "communication_skill and problem_understanding, which allow only Meet or Below.",
    ),
)

# A callable taking texts and returning one vector each, in input order. Named so evaluate() can
# be handed VertexAIEmbedding.embed_texts in production and a stub in a test.
Embedder = Callable[[Sequence[str]], list[list[float]]]


@dataclass
class Config:
    """Runtime configuration, mirroring :class:`src.google_model.sentiment.google_output.Config`."""

    # SharePoint location of the workbook google_output.py wrote. run_prefix is the run's
    # timestamp folder and is filled in by hand before each evaluation.
    dest_file: str = "/poc_internal_model_migration/voicefiles_output"
    run_prefix: str = ""
    output_file_name: str = "model_comparison.xlsx"

    gt_sheet_name: str = "Voice - Groundtruth"
    result_sheet_name: str = "Voice - Google Result"
    eval_sheet_name: str = "Evaluation Dashboard"

    gt_transcript_path: str = "/poc_internal_model_migration/voicefiles/Transcript"

    # Vertex embedding configuration. "global" is valid for the standard embedding models; a
    # model not enabled there answers 404, in which case set a region such as "us-central1".
    embedding_model: str = "gemini-embedding-001"
    embedding_location: str = "global"
    embedding_concurrency: int = 8
    similarity_threshold: float = 0.80

    # Transcript scoring. chunk_chars is the embedding split size; lower it if a live run trips
    # a 400 on input length, which would mean Thai tokenises denser than assumed.
    transcript_chunk_chars: int = DEFAULT_CHUNK_CHARS
    transcript_cer_threshold: float = DEFAULT_CER_THRESHOLD
    # SharePoint download fan-out. ~600 transcripts at two round-trips each is minutes of pure
    # latency sequentially, and the work is entirely I/O-bound.
    transcript_concurrency: int = 8

    timezone: str = "Asia/Bangkok"

    @property
    def run_folder(self) -> str:
        """SharePoint folder holding this run's outputs.

        The single place ``run_prefix`` is joined: the workbook and the model's transcripts are
        both written here by ``google_output.run()``, so they must not resolve it separately.
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


def _labels_for(column: str) -> tuple[str, ...]:
    """Return the fixed label set a QA criterion is scored against."""
    return BINARY_LABELS if column in BINARY_CRITERIA else CRITERION_LABELS


def _clean(value: Any) -> str:
    """Normalise a cell to a stripped string. NaN and None become ``""``."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


def parse_call_types(value: Any) -> tuple[set[str], list[str]]:
    """Split a ``call_type`` cell into ``(labels, unknown)`` -- a set plus unrecognised tokens.

    Whitespace and ordering are formatting and fold away (raw string equality once read 0.691
    against a true 0.728). Vocabulary is not: a token outside :data:`CALL_TYPE_LABELS` is
    returned for the caller to count and log, never guessed into a neighbouring label.
    """
    labels: set[str] = set()
    unknown: list[str] = []

    for raw in _clean(value).split(","):
        token = raw.strip()
        if not token:
            continue
        canonical = _CALL_TYPE_BY_LOWER.get(token.lower())
        if canonical is None:
            unknown.append(token)
        else:
            labels.add(canonical)

    return labels, unknown


def _score_single_label(
    merged: pd.DataFrame, column: str, labels: Sequence[str]
) -> tuple[ClassificationReport, int]:
    """Score one single-label column, excluding rows either side graded outside ``labels``.

    An out-of-vocabulary value is a GT data-entry fault, not a model error: the row is dropped
    from this column only, and the count reaches the dashboard's ``Scored``/``Excluded`` cells.

    Returns:
        ``(report, dropped)``.
    """
    known = set(labels)
    y_true: list[str] = []
    y_pred: list[str] = []
    dropped = 0

    for raw_true, raw_pred in zip(
        merged[f"{column}_gt"], merged[f"{column}_pred"], strict=True
    ):
        true, pred = _clean(raw_true), _clean(raw_pred)
        if true not in known or pred not in known:
            dropped += 1
            continue
        y_true.append(true)
        y_pred.append(pred)

    if dropped:
        # The count and the column, never the value. A stray overall_sentiment cell is a Thai
        # narrative of the call -- the PII the pipeline goes to some length to mask.
        logger.warning(
            "google_sentiment_confusion_matrix.column.excluded",
            column=column,
            dropped=dropped,
            scored=len(y_true),
        )

    return classification_report(y_true, y_pred, labels), dropped


def _score_call_type(merged: pd.DataFrame) -> tuple[MultiLabelReport, int]:
    """Score the multi-label ``call_type`` column.

    An unrecognised token does not cost the row: it is dropped and the remaining labels still
    score, and the token is logged (labels are not call content).

    Returns:
        ``(report, unknown_token_count)``.
    """
    y_true: list[set[str]] = []
    y_pred: list[set[str]] = []
    unknown_tokens: list[str] = []

    for raw_true, raw_pred in zip(
        merged[f"{CALL_TYPE_COLUMN}_gt"], merged[f"{CALL_TYPE_COLUMN}_pred"], strict=True
    ):
        true_labels, true_unknown = parse_call_types(raw_true)
        pred_labels, pred_unknown = parse_call_types(raw_pred)
        y_true.append(true_labels)
        y_pred.append(pred_labels)
        unknown_tokens.extend(true_unknown)
        unknown_tokens.extend(pred_unknown)

    if unknown_tokens:
        logger.warning(
            "google_sentiment_confusion_matrix.call_type.unknown_tokens",
            count=len(unknown_tokens),
            tokens=sorted(set(unknown_tokens)),
        )

    return multilabel_report(y_true, y_pred, CALL_TYPE_LABELS), len(unknown_tokens)


def _score_summary(
    merged: pd.DataFrame, *, embedder: Embedder, threshold: float
) -> tuple[SimilarityReport, int]:
    """Score ``summary_story`` by embedding cosine; returns ``(report, dropped)``.

    Both sides go out in one :data:`Embedder` call (GT first, predictions second); the split
    relies on the embedder preserving input order. Raises ValueError on a wrong vector count.
    """
    gt_texts: list[str] = []
    pred_texts: list[str] = []
    dropped = 0

    for raw_true, raw_pred in zip(
        merged[f"{SUMMARY_COLUMN}_gt"], merged[f"{SUMMARY_COLUMN}_pred"], strict=True
    ):
        true, pred = _clean(raw_true), _clean(raw_pred)
        if not true or not pred:
            dropped += 1
            continue
        gt_texts.append(true)
        pred_texts.append(pred)

    if dropped:
        logger.warning(
            "google_sentiment_confusion_matrix.column.excluded",
            column=SUMMARY_COLUMN,
            dropped=dropped,
            scored=len(gt_texts),
            reason="blank_summary",
        )

    if not gt_texts:
        return similarity_stats([], threshold), dropped

    vectors = embedder([*gt_texts, *pred_texts])

    # A short or long result would silently pair summary N with summary N+1 and misscore every
    # row after it, so the count is checked rather than trusted.
    if len(vectors) != len(gt_texts) + len(pred_texts):
        raise ValueError(
            f"Embedder returned {len(vectors)} vectors for "
            f"{len(gt_texts) + len(pred_texts)} texts."
        )

    half = len(gt_texts)
    scores = [cosine_similarity(vectors[i], vectors[half + i]) for i in range(half)]
    return similarity_stats(scores, threshold), dropped


def _stem(value: Any) -> str:
    """Key a workbook row or a transcript file onto one identity (extension dropped).

    Sheets say ``<call>.wav``; transcripts are stored as ``<call>.txt``.
    """
    return Path(_clean(value)).stem


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
            "google_sentiment_confusion_matrix.transcript.excluded",
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


def _class_cells(
    report: ClassificationReport | MultiLabelReport, labels: Sequence[str]
) -> dict[str, Any]:
    """Flatten a report's per-label scores into ``"{label} {metric}"`` cells.

    ``labels`` is the *block's* full label set, not the report's: a binary criterion sits in the
    same table as the ternary ones, so its N/A group must exist as cells and read ``n/a`` rather
    than a misleading zero.

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


def _mean(values: Sequence[float]) -> float:
    """Arithmetic mean, 0.0 on an empty sequence."""
    return sum(values) / len(values) if values else 0.0


def evaluate(
    gt_df: pd.DataFrame,
    result_df: pd.DataFrame,
    *,
    embedder: Embedder,
    threshold: float = 0.80,
    gt_transcripts: Mapping[str, str] | None = None,
    pred_transcripts: Mapping[str, str] | None = None,
    cer_threshold: float = DEFAULT_CER_THRESHOLD,
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
) -> dict[str, pd.DataFrame]:
    """Score a result sheet against ground truth and return the dashboard's blocks.

    Free of I/O apart from ``embedder``, so a caller can run the whole scoring path offline with
    a stub. Blocks are returned in sheet order; the keys are the titles printed above each table.

    Transcripts are passed *in* (downloading is the caller's job, which keeps this testable
    offline); the ``TRANSCRIPT`` block is emitted only when both mappings are supplied, so an
    unreachable folder costs that block, not the evaluation.

    Args:
        gt_df: The ground-truth sheet.
        result_df: The model result sheet.
        embedder: Callable turning texts into vectors, preserving input order.
        threshold: Cosine score at or above which a summary or transcript counts as a pass.
        gt_transcripts: Ground-truth transcript text keyed by file stem, or None to skip the
            transcript block.
        pred_transcripts: Model transcript text keyed by file stem, or None to skip it.
        cer_threshold: CER at or **below** which a transcript counts as a pass.
        chunk_chars: Maximum characters per embedding chunk for transcripts.

    Returns:
        ``{block title: DataFrame}``, insertion-ordered.

    Raises:
        KeyError: If either frame is missing the join key.
        ValueError: If ``embedder`` returns the wrong number of vectors.
    """
    started = time.monotonic()

    for name, frame in (("ground truth", gt_df), ("result", result_df)):
        if KEY_COLUMN not in frame.columns:
            raise KeyError(f"The {name} sheet has no {KEY_COLUMN!r} column.")

    gt = gt_df.copy()
    result = result_df.copy()
    gt[KEY_COLUMN] = gt[KEY_COLUMN].map(_clean)
    result[KEY_COLUMN] = result[KEY_COLUMN].map(_clean)

    merged = gt.merge(result, on=KEY_COLUMN, how="inner", suffixes=("_gt", "_pred"))

    gt_only = len(set(gt[KEY_COLUMN]) - set(result[KEY_COLUMN]))
    result_only = len(set(result[KEY_COLUMN]) - set(gt[KEY_COLUMN]))

    logger.info(
        "google_sentiment_confusion_matrix.join.completed",
        gt_rows=len(gt),
        result_rows=len(result),
        matched=len(merged),
        gt_only=gt_only,
        result_only=result_only,
    )

    qa_reports = {
        column: _score_single_label(merged, column, _labels_for(column))
        for column in QA_CRITERIA
    }
    sentiment_reports = {
        column: _score_single_label(merged, column, SENTIMENT_LABELS)
        for column in SENTIMENT_COLUMNS
    }
    call_type_report, unknown_tokens = _score_call_type(merged)
    summary_report, summary_dropped = _score_summary(
        merged, embedder=embedder, threshold=threshold
    )

    score_transcripts = gt_transcripts is not None and pred_transcripts is not None
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

    overall = pd.DataFrame(
        [
            {
                "Family": FAMILY_QA,
                "Columns": len(qa_reports),
                "Rows": len(merged),
                "Accuracy": round(_mean([r.accuracy for r, _ in qa_reports.values()]), 4),
                "Macro-F1": round(_mean([r.macro_f1 for r, _ in qa_reports.values()]), 4),
                "Note": (
                    f"{len(BINARY_CRITERIA)} of {len(QA_CRITERIA)} columns are Meet/Below only"
                ),
            },
            {
                "Family": FAMILY_SENTIMENT,
                "Columns": len(sentiment_reports),
                "Rows": len(merged),
                "Accuracy": round(_mean([r.accuracy for r, _ in sentiment_reports.values()]), 4),
                "Macro-F1": round(_mean([r.macro_f1 for r, _ in sentiment_reports.values()]), 4),
                "Note": "",
            },
            {
                "Family": FAMILY_CALL_TYPE,
                "Columns": 1,
                "Rows": len(merged),
                # Exact set match is the multi-label analogue of accuracy: the whole row right.
                "Accuracy": round(call_type_report.exact_match, 4),
                "Macro-F1": round(call_type_report.macro_f1, 4),
                "Note": (
                    f"exact set match; micro-F1 {call_type_report.micro_f1:.4f}"
                    + (f"; {unknown_tokens} unknown tokens dropped" if unknown_tokens else "")
                ),
            },
            {
                "Family": FAMILY_SUMMARY,
                "Columns": 1,
                "Rows": len(merged),
                "Accuracy": NOT_APPLICABLE,
                "Macro-F1": NOT_APPLICABLE,
                "Note": (
                    f"mean cosine {summary_report.mean:.4f}; "
                    f"pass@{threshold:.2f} {summary_report.pass_rate:.4f}"
                ),
            },
        ]
    )

    if score_transcripts:
        overall = pd.concat(
            [
                overall,
                pd.DataFrame(
                    [
                        {
                            "Family": FAMILY_TRANSCRIPT,
                            "Columns": 1,
                            "Rows": len(merged),
                            # The one family here with a genuine accuracy: 1 - CER is the share
                            # of characters transcribed correctly, on the same 0-1 scale as the
                            # Accuracy above it. Cosine has no such reading, which is why it
                            # sits in the Note rather than in this cell.
                            "Accuracy": round(1.0 - transcript_errors.mean, 4),
                            "Macro-F1": NOT_APPLICABLE,
                            "Note": (
                                f"1 - mean CER; mean cosine {transcript_similarity.mean:.4f}; "
                                f"CER pass@{cer_threshold:.2f} "
                                f"{transcript_errors.pass_rate:.4f}"
                            ),
                        }
                    ]
                ),
            ],
            ignore_index=True,
        )

    # dtype=object throughout: a Metric/Value block mixes ratios with counts, and letting pandas
    # unify the column on float64 writes the counts to the sheet as 298.0.
    call_type_summary = pd.DataFrame(
        [
            {"Metric": "Exact set match", "Value": round(call_type_report.exact_match, 4)},
            {"Metric": "Micro-F1", "Value": round(call_type_report.micro_f1, 4)},
            {"Metric": "Micro-Precision", "Value": round(call_type_report.micro_precision, 4)},
            {"Metric": "Micro-Recall", "Value": round(call_type_report.micro_recall, 4)},
            {"Metric": "Macro-F1", "Value": round(call_type_report.macro_f1, 4)},
            {"Metric": "Macro-F1 classes", "Value": len(call_type_report.macro_labels)},
            {"Metric": "Scored", "Value": call_type_report.n},
            {"Metric": "Unknown tokens dropped", "Value": unknown_tokens},
        ],
        dtype=object,
    )

    # A call type neither side used blanks its three scores, matching _class_cells -- 0.0000 there
    # would read as "the model failed on this type" when in fact the type never came up.
    averaged_call_types = set(call_type_report.macro_labels)
    call_type_labels = pd.DataFrame(
        [
            {
                "Label": score.label,
                "TP": score.tp,
                "TN": score.tn,
                "FP": score.fp,
                "FN": score.fn,
                **_score_cells(score, used=score.label in averaged_call_types),
                "Support": score.support,
            }
            for score in call_type_report.per_label
        ]
    )

    summary_block = pd.DataFrame(
        [
            {"Metric": "Mean cosine", "Value": round(summary_report.mean, 4)},
            {"Metric": "Median cosine", "Value": round(summary_report.median, 4)},
            {"Metric": "Lowest cosine", "Value": round(summary_report.minimum, 4)},
            {"Metric": "Highest cosine", "Value": round(summary_report.maximum, 4)},
            {
                "Metric": f"Pass rate (>= {threshold:.2f})",
                "Value": round(summary_report.pass_rate, 4),
            },
            {"Metric": "Scored", "Value": summary_report.n},
            {"Metric": "Excluded (blank either side)", "Value": summary_dropped},
        ],
        dtype=object,
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
                    f"{len(merged)} of {len(gt)} ground-truth rows "
                    f"({gt_only} unmatched in ground truth, {result_only} unmatched in result)"
                ),
                "Pass threshold": threshold,
            }
        ]
    )

    blocks = {
        BLOCK_HEADER: header,
        BLOCK_OVERALL: overall,
        BLOCK_QA: _single_label_block(qa_reports, CRITERION_LABELS),
        BLOCK_SENTIMENT: _single_label_block(sentiment_reports, SENTIMENT_LABELS),
        BLOCK_CALL_TYPE_SUMMARY: call_type_summary,
        BLOCK_CALL_TYPE_LABELS: call_type_labels,
        BLOCK_SUMMARY: summary_block,
    }

    if transcript_block is not None:
        blocks[BLOCK_TRANSCRIPT] = transcript_block

    # The legend is always last, so it is appended after the optional block rather than declared
    # in the literal above -- dict insertion order is the sheet's row order.
    blocks[BLOCK_LEGEND] = pd.DataFrame(LEGEND, columns=["Term", "Meaning"])

    logger.info(
        "google_sentiment_confusion_matrix.evaluate.completed",
        matched=len(merged),
        qa_columns=len(qa_reports),
        sentiment_columns=len(sentiment_reports),
        summaries_scored=summary_report.n,
        transcripts_scored=transcript_errors.n if transcript_errors else 0,
        transcript_chunks=transcript_chunks,
        elapsed_ms=_elapsed_ms(started),
    )
    return blocks


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
            logger.warning("google_sentiment_confusion_matrix.transcript.skipped", path=_safe_name(path))
            return wanted[path], None

    transcripts: dict[str, str] = {}

    if targets:
        with ThreadPoolExecutor(max_workers=min(concurrency, len(targets))) as pool:
            for stem, text in pool.map(fetch, targets):
                if text is not None:
                    transcripts[stem] = text

    logger.info(
        "google_sentiment_confusion_matrix.transcripts.downloaded",
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

    The file names carry a customer's phone number and the agent's name, and the file *content*
    is the raw call. Only the call ID -- the leading segment of the name -- reaches the log, which
    is enough to find the file by hand without putting the rest into a search index.
    """
    return Path(path).stem.split("_", 1)[0]


def resolve_sheet_name(existing: Sequence[str], base: str) -> str:
    """Pick a free sheet name: ``base`` if free, else the first free ``{base}_{n}``."""
    taken = set(existing)

    if base not in taken:
        return base

    suffix = 1
    while f"{base}_{suffix}" in taken:
        suffix += 1
    return f"{base}_{suffix}"


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


def _style_block(worksheet, frame: pd.DataFrame, title: str, title_row: int, index: int) -> None:
    """Apply the table, number formats, colour scales and muted cells for one block.

    Args:
        worksheet: The openpyxl worksheet.
        frame: The block's data.
        title: The block's title, printed in the banner row above it.
        title_row: 1-based Excel row the banner occupies.
        index: The block's position, used to build a workbook-unique table name.
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
                        cell.coordinate, _colour_scale(invert=_is_inverted_metric(metric))
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
        "google_sentiment_confusion_matrix.dashboard.written",
        sheet=sheet_name,
        blocks=len(blocks),
        rows=row,
        bytes=len(updated),
        elapsed_ms=_elapsed_ms(started),
    )
    return updated


def run(config: Config | None = None) -> None:
    """Download the workbook, score it, and upload it back with the dashboard appended.

    Args:
        config: Configuration to score against. Omitted, the class defaults apply -- which
            includes the hand-edited ``run_prefix``, so a caller that wants a *different* run
            scored must pass one. main.py builds it from the interactive run selection.
    """
    config = config if config is not None else Config()

    with TracedOperation(
        "google_sentiment_confusion_matrix.run",
        workbook=config.workbook_path,
        model=config.embedding_model,
    ):
        logger.info(
            "google_sentiment_confusion_matrix.run.starting",
            workbook=config.workbook_path,
            gt_sheet=config.gt_sheet_name,
            result_sheet=config.result_sheet_name,
            eval_sheet=config.eval_sheet_name,
            embedding_model=config.embedding_model,
            embedding_location=config.embedding_location,
            threshold=config.similarity_threshold,
            gt_transcript_path=config.gt_transcript_path,
            cer_threshold=config.transcript_cer_threshold,
            chunk_chars=config.transcript_chunk_chars,
        )

        client_sb = SharePointModule(
            client_id=os.environ["SANDBOX_CLIENT_ID"],
            client_secret=os.environ["SANDBOX_CLIENT_SECRET"],
            tenant_id=os.environ["SANDBOX_TENANT_ID"],
            site_domain=os.environ["SANDBOX_SITE_DOMAIN"],
            site_path=os.environ["SANDBOX_SITE_PATH"],
            timezone=config.timezone,
        )

        with TracedOperation("google_sentiment_confusion_matrix.load"):
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
                        "google_sentiment_confusion_matrix.run.aborted",
                        reason="sheet_missing",
                        file=config.workbook_path,
                        missing_sheets=missing,
                        # Sheet names only -- never cell content.
                        available=sheet_names,
                    )
                    return

                # keep_default_na=False with the empty cell re-added: pandas' default NA set
                # contains 'N/A', which is a graded answer on 18 of these columns, not a blank.
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
                        "google_sentiment_confusion_matrix.run.aborted",
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
                "google_sentiment_confusion_matrix.load.completed",
                file=config.workbook_path,
                gt_rows=len(gt_df),
                result_rows=len(result_df),
                bytes=len(workbook_byte),
            )

        with TracedOperation("google_sentiment_confusion_matrix.transcripts"):
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
                # Degrade rather than abort: the sheets are already loaded and the other four
                # families cost nothing more to score. Losing a completed evaluation over an
                # unreachable transcript folder is the worse outcome.
                gt_transcripts = pred_transcripts = None
                logger.warning(
                    "google_sentiment_confusion_matrix.transcripts.skipped",
                    gt_folder=config.gt_transcript_path,
                    result_folder=config.run_folder,
                )

            if gt_transcripts is not None and pred_transcripts is not None:
                both = len(gt_transcripts.keys() & pred_transcripts.keys())
                logger.info(
                    "google_sentiment_confusion_matrix.transcripts.loaded",
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
                        "google_sentiment_confusion_matrix.transcripts.skipped",
                        reason="no_overlapping_stems",
                        gt_folder=config.gt_transcript_path,
                        result_folder=config.run_folder,
                    )

        with TracedOperation("google_sentiment_confusion_matrix.evaluate"):
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

        with TracedOperation("google_sentiment_confusion_matrix.write"):
            sheet_name = resolve_sheet_name(sheet_names, config.eval_sheet_name)

            if sheet_name != config.eval_sheet_name:
                logger.info(
                    "google_sentiment_confusion_matrix.sheet.renamed",
                    requested=config.eval_sheet_name,
                    resolved=sheet_name,
                )

            updated = write_dashboard(workbook_byte, blocks, sheet_name)

            # Guarded: the evaluation is already computed and a fault here -- a lock, a transient
            # 503 -- must not read as a scoring failure. SharePointModule already logged the
            # cause at its single ERROR site, so this records only that the run carried on.
            uploaded = False
            try:
                client_sb.upload_file(upload_path=config.workbook_path, content=updated, archive_on_lock=True)
                uploaded = True
            except Exception:
                logger.warning(
                    "google_sentiment_confusion_matrix.output.skipped",
                    path=config.workbook_path,
                    sheet=sheet_name,
                )

        logger.info(
            "google_sentiment_confusion_matrix.run.completed",
            workbook=config.workbook_path,
            sheet=sheet_name,
            gt_rows=len(gt_df),
            result_rows=len(result_df),
            blocks=len(blocks),
            # So a run that lost the transcript folders reads as partial in one record, rather
            # than having to be inferred from a block missing off the sheet.
            transcripts_scored=BLOCK_TRANSCRIPT in blocks,
            uploaded=uploaded,
        )