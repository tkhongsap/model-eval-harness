"""Scoring primitives for comparing a model's output against human ground truth.

Pure functions over plain Python values: no I/O, no pandas, no logging, so the whole
evaluation path can be tested offline. Covers single-label classification, multi-label
classification (``call_type``), row-by-row agreement (:func:`match_report`), continuous
similarity (embedding cosine), and transcription error rate (CER -- **lower is better**,
the only metric on the dashboard for which that is true).
"""

from __future__ import annotations

# Library imports
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from statistics import median

import jiwer
import numpy as np


@dataclass(frozen=True, slots=True)
class LabelScore:
    """One-vs-rest scores for a single class ("this class versus everything else").

    Two invariants hold by construction and are asserted by the tests:
    ``tp + tn + fp + fn == n`` and ``support == tp + fn``.
    """

    label: str
    tp: int
    tn: int
    fp: int
    fn: int
    precision: float
    recall: float
    f1: float
    support: int  # rows the ground truth assigned to this class

    @property
    def n(self) -> int:
        """Rows this class was scored over. Derived, because ``tp + tn + fp + fn == n`` holds."""
        return self.tp + self.tn + self.fp + self.fn

    @property
    def accuracy(self) -> float:
        """One-vs-rest accuracy, ``(tp + tn) / n``.

        The number the stakeholders' workbook calls "Overall accuracy". Read it with care: ``tn``
        dominates for a rare class, so a model that never predicts a class appearing on 3 of 100
        rows still scores ~0.97 here. ``precision``/``recall`` are what expose that.
        """
        total = self.n
        return (self.tp + self.tn) / total if total else 0.0

    @property
    def occurs(self) -> bool:
        """Whether either side used this class at all -- see :func:`_macro_f1`."""
        return bool(self.tp or self.fp or self.fn)


@dataclass(frozen=True, slots=True)
class ClassificationReport:
    """Single-label classification result for one column."""

    labels: tuple[str, ...]
    # confusion[true_label][pred_label] = count
    confusion: dict[str, dict[str, int]]
    per_label: tuple[LabelScore, ...]
    accuracy: float
    macro_f1: float
    # The classes that entered the macro average -- see _macro_f1.
    macro_labels: tuple[str, ...]
    n: int


@dataclass(frozen=True, slots=True)
class MultiLabelReport:
    """Multi-label classification result -- one set of labels per row."""

    labels: tuple[str, ...]
    per_label: tuple[LabelScore, ...]
    exact_match: float  # share of rows whose predicted set equals the true set
    micro_f1: float
    macro_f1: float
    macro_labels: tuple[str, ...]  # the classes that entered the macro average -- see _macro_f1
    micro_precision: float
    micro_recall: float
    n: int


@dataclass(frozen=True, slots=True)
class AverageScores:
    """One row of the per-class table's footer -- Macro, Micro or Weighted."""

    kind: str  # "Macro-average" | "Micro-average" | "Weighted-average"
    accuracy: float
    precision: float
    recall: float
    f1: float
    labels: tuple[str, ...]  # the classes that entered this average
    weight: int  # classes averaged (macro) or total support (micro/weighted)


@dataclass(frozen=True, slots=True)
class AggregateScores:
    """The three footer rows together, in the order they are written to the sheet."""

    macro: AverageScores
    micro: AverageScores
    weighted: AverageScores

    def rows(self) -> tuple[AverageScores, ...]:
        """The three averages in sheet order."""
        return (self.macro, self.micro, self.weighted)


@dataclass(frozen=True, slots=True)
class MatchReport:
    """Row-by-row agreement between two graders -- semantics documented in :func:`match_report`."""

    n: int
    tp: int  # rows where both sides gave the same answer
    fp: int  # rows where they differed
    fn: int  # structurally 0 -- see match_report
    tn: int  # structurally 0 -- see match_report
    accuracy: float
    precision: float
    recall: float
    f1: float


@dataclass(frozen=True, slots=True)
class SimilarityReport:
    """Continuous-similarity result for a free-text column."""

    mean: float
    median: float
    minimum: float
    maximum: float
    pass_rate: float  # share of rows scoring >= threshold
    threshold: float
    n: int


@dataclass(frozen=True, slots=True)
class ErrorRateReport:
    """Error-rate result for a transcribed column, where **lower is better**.

    Deliberately separate from :class:`SimilarityReport`: every field means the opposite of its
    similarity counterpart -- ``pass_rate`` tests ``<=``, ``best`` is the *minimum* and ``worst``
    the *maximum*.
    """

    mean: float
    median: float
    best: float  # lowest error rate in the run
    worst: float  # highest error rate in the run
    pass_rate: float  # share of rows scoring <= threshold
    threshold: float
    n: int


def _prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    """Precision, recall and F1 from one-vs-rest counts.

    A zero denominator yields 0.0 rather than raising, so macro-F1 penalises a class the model
    never predicted instead of quietly dropping it from the average.
    """
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return precision, recall, f1


def _macro_f1(per_label: Sequence[LabelScore]) -> tuple[float, tuple[str, ...]]:
    """Mean F1 over the classes that actually occur, and the classes averaged.

    A class absent from *both* sides (``tp + fp + fn == 0``) is dropped: nobody used it, and
    averaging its 0.0 in would stop a perfect prediction from scoring 1.0. A predicted class
    with ``support == 0`` is kept at F1 0.0 -- a grade the human never used and the model
    invented is a real error. ``macro_labels`` reports which classes entered the average, so a
    moved score cannot be mistaken for a moved model.

    Returns:
        ``(macro_f1, macro_labels)``. ``(0.0, ())`` when no class occurs at all, so a fully
        excluded column still produces a dashboard row instead of dividing by zero.
    """
    occurring = [score for score in per_label if score.tp or score.fp or score.fn]
    if not occurring:
        return 0.0, ()
    return (
        sum(score.f1 for score in occurring) / len(occurring),
        tuple(score.label for score in occurring),
    )


def aggregate_scores(per_label: Sequence[LabelScore]) -> AggregateScores:
    """Macro, Micro and Weighted averages over a per-class table.

    The three rows the stakeholders' evaluation workbook prints under every per-class table, and
    the reason all three are kept: they disagree in ways that identify *how* a model is wrong.
    Macro treats every class alike, so a rare class the model never learned drags it down. Micro
    pools every decision, so frequent classes dominate. Weighted sits between them, scaling each
    class by how often the graders used it. A model that only handles the common classes scores
    high Micro and low Macro.

    Only classes that **occur** -- used by either side -- are averaged, the same rule
    :func:`_macro_f1` applies and for the same reason: a class nobody used is not a class anybody
    got wrong, and averaging its 0.0 in would stop a perfect prediction from scoring 1.0.
    ``labels`` on each row reports which classes entered it, so an average that moved between runs
    cannot be mistaken for a model that moved.

    Two identities hold by construction, and are asserted by the tests so that a future reader does
    not "fix" one of them:

    * **Macro accuracy equals Micro accuracy.** Every class in a per-class table is scored over the
      same row set, so the mean of ``(tp+tn)/n`` equals ``(sum tp + sum tn)/(k*n)``. They are
      printed separately because the workbook does, not because they can differ.
    * **Weighted recall equals Micro recall.** ``sum((tp/support) * support) / sum(support)``
      collapses to ``sum(tp) / sum(support)``.

    Verified against the workbook's ``Score - Reason`` sheet: all eight precision/recall/F1 figures
    reproduce exactly. Its two accuracy figures differ by 0.0002 because that sheet's ``TN`` column
    does not sum consistently -- a defect its own footnote records. This function derives ``TN``
    from :attr:`LabelScore.n`, so it cannot drift the same way.

    Args:
        per_label: One :class:`LabelScore` per class, from :func:`classification_report` or
            :func:`multilabel_report`.

    Returns:
        An :class:`AggregateScores`. Empty input, or input where no class occurs, yields three
        all-zero rows with empty ``labels`` rather than raising -- a fully excluded column still
        has to produce a dashboard row.
    """
    occurring = [score for score in per_label if score.occurs]
    names = tuple(score.label for score in occurring)
    count = len(occurring)

    if not occurring:
        empty = tuple(
            AverageScores(kind=kind, accuracy=0.0, precision=0.0, recall=0.0, f1=0.0, labels=(), weight=0)
            for kind in ("Macro-average", "Micro-average", "Weighted-average")
        )
        return AggregateScores(macro=empty[0], micro=empty[1], weighted=empty[2])

    macro = AverageScores(
        kind="Macro-average",
        accuracy=sum(score.accuracy for score in occurring) / count,
        precision=sum(score.precision for score in occurring) / count,
        recall=sum(score.recall for score in occurring) / count,
        f1=sum(score.f1 for score in occurring) / count,
        labels=names,
        weight=count,
    )

    total_tp = sum(score.tp for score in occurring)
    total_fp = sum(score.fp for score in occurring)
    total_fn = sum(score.fn for score in occurring)
    total_tn = sum(score.tn for score in occurring)
    cells = total_tp + total_fp + total_fn + total_tn
    micro_precision, micro_recall, micro_f1 = _prf(total_tp, total_fp, total_fn)

    micro = AverageScores(
        kind="Micro-average",
        accuracy=(total_tp + total_tn) / cells if cells else 0.0,
        precision=micro_precision,
        recall=micro_recall,
        f1=micro_f1,
        labels=names,
        weight=total_tp + total_fn,
    )

    support = sum(score.support for score in occurring)
    if support:
        weighted = AverageScores(
            kind="Weighted-average",
            accuracy=sum(score.accuracy * score.support for score in occurring) / support,
            precision=sum(score.precision * score.support for score in occurring) / support,
            recall=sum(score.recall * score.support for score in occurring) / support,
            f1=sum(score.f1 * score.support for score in occurring) / support,
            labels=names,
            weight=support,
        )
    else:
        # Every occurring class was predicted but never graded -- all FP, no support to weight by.
        weighted = AverageScores(
            kind="Weighted-average", accuracy=0.0, precision=0.0, recall=0.0, f1=0.0,
            labels=names, weight=0,
        )

    return AggregateScores(macro=macro, micro=micro, weighted=weighted)


def confusion_matrix(
    y_true: Sequence[str], y_pred: Sequence[str], labels: Sequence[str]
) -> dict[str, dict[str, int]]:
    """Build ``confusion[true][pred] = count``.

    Args:
        y_true: Ground-truth label per row.
        y_pred: Predicted label per row.
        labels: The full label set. Every value in ``y_true``/``y_pred`` must appear in it --
            callers normalise and drop out-of-vocabulary values before scoring.

    Returns:
        A nested dict covering every ``labels`` x ``labels`` cell, zeros included.

    Raises:
        ValueError: If the two sequences differ in length, or a value is not in ``labels``.
    """
    if len(y_true) != len(y_pred):
        raise ValueError(
            f"y_true and y_pred must be the same length, got {len(y_true)} and {len(y_pred)}."
        )

    known = set(labels)
    matrix = {t: {p: 0 for p in labels} for t in labels}

    for true, pred in zip(y_true, y_pred, strict=True):
        # Label strings are safe to surface, and naming them is the only way to find the bad cell.
        if true not in known or pred not in known:
            raise ValueError(
                f"Value outside the label set {sorted(known)}: true={true!r}, pred={pred!r}."
            )
        matrix[true][pred] += 1

    return matrix


def classification_report(
    y_true: Sequence[str], y_pred: Sequence[str], labels: Sequence[str]
) -> ClassificationReport:
    """Confusion matrix, per-label one-vs-rest scores, accuracy and macro-F1.

    ``labels`` is **required**, not inferred from the data -- inference would silently drop a
    class the model ought to be using. The macro-F1 denominator is narrower than ``labels`` by
    design; see :func:`_macro_f1`, and ``macro_labels`` reports the answer. Accuracy and
    macro-F1 must be read together: always answering the majority class scores high accuracy
    and low macro-F1.

    Args:
        y_true: Ground-truth label per row.
        y_pred: Predicted label per row.
        labels: The fixed label set, in the order the report should present it.

    Returns:
        A :class:`ClassificationReport`. Empty input yields zeros rather than raising.

    Raises:
        ValueError: If the inputs differ in length, a value is outside ``labels``, or ``labels``
            is empty.
    """
    if not labels:
        raise ValueError("labels must not be empty.")

    labels = tuple(labels)
    matrix = confusion_matrix(y_true, y_pred, labels)
    n = len(y_true)

    per_label: list[LabelScore] = []
    correct = 0

    for label in labels:
        tp = matrix[label][label]
        fp = sum(matrix[t][label] for t in labels if t != label)
        fn = sum(matrix[label][p] for p in labels if p != label)
        # Everything not accounted for by the other three cells.
        tn = n - tp - fp - fn
        precision, recall, f1 = _prf(tp, fp, fn)

        per_label.append(
            LabelScore(
                label=label,
                tp=tp,
                tn=tn,
                fp=fp,
                fn=fn,
                precision=precision,
                recall=recall,
                f1=f1,
                support=tp + fn,
            )
        )
        correct += tp

    macro_f1, macro_labels = _macro_f1(per_label)

    return ClassificationReport(
        labels=labels,
        confusion=matrix,
        per_label=tuple(per_label),
        accuracy=correct / n if n else 0.0,
        macro_f1=macro_f1,
        macro_labels=macro_labels,
        n=n,
    )


def multilabel_report(
    y_true: Sequence[set[str]], y_pred: Sequence[set[str]], labels: Sequence[str]
) -> MultiLabelReport:
    """Score a multi-label column, where each row carries a *set* of labels.

    Three numbers because they disagree in useful ways: ``exact_match`` is the strict per-row
    view, ``micro_f1`` pools every label decision (frequent labels dominate), and ``macro_f1``
    averages per-label F1 over the labels that occur (see :func:`_macro_f1`), exposing a rare
    label the model never learned.

    Args:
        y_true: Ground-truth label set per row.
        y_pred: Predicted label set per row.
        labels: The fixed label set. Values outside it must be dropped by the caller.

    Returns:
        A :class:`MultiLabelReport`.

    Raises:
        ValueError: If the inputs differ in length, ``labels`` is empty, or a set holds a value
            outside ``labels``.
    """
    if not labels:
        raise ValueError("labels must not be empty.")
    if len(y_true) != len(y_pred):
        raise ValueError(
            f"y_true and y_pred must be the same length, got {len(y_true)} and {len(y_pred)}."
        )

    labels = tuple(labels)
    known = set(labels)
    n = len(y_true)

    for true, pred in zip(y_true, y_pred, strict=True):
        unknown = (true | pred) - known
        if unknown:
            raise ValueError(f"Values outside the label set {sorted(known)}: {sorted(unknown)}.")

    per_label: list[LabelScore] = []
    total_tp = total_fp = total_fn = 0

    for label in labels:
        tp = fp = fn = 0
        for true, pred in zip(y_true, y_pred, strict=True):
            in_true, in_pred = label in true, label in pred
            tp += in_true and in_pred
            fp += (not in_true) and in_pred
            fn += in_true and not in_pred
        tn = n - tp - fp - fn
        precision, recall, f1 = _prf(tp, fp, fn)

        per_label.append(
            LabelScore(
                label=label,
                tp=tp,
                tn=tn,
                fp=fp,
                fn=fn,
                precision=precision,
                recall=recall,
                f1=f1,
                support=tp + fn,
            )
        )
        total_tp += tp
        total_fp += fp
        total_fn += fn

    micro_precision, micro_recall, micro_f1 = _prf(total_tp, total_fp, total_fn)
    exact = sum(1 for true, pred in zip(y_true, y_pred, strict=True) if true == pred)
    macro_f1, macro_labels = _macro_f1(per_label)

    return MultiLabelReport(
        labels=labels,
        per_label=tuple(per_label),
        exact_match=exact / n if n else 0.0,
        micro_f1=micro_f1,
        macro_f1=macro_f1,
        macro_labels=macro_labels,
        micro_precision=micro_precision,
        micro_recall=micro_recall,
        n=n,
    )


def match_report(y_true: Sequence[str], y_pred: Sequence[str]) -> MatchReport:
    """Count the rows on which two graders agreed.

    Unlike :func:`classification_report`, the grade itself is discarded -- only "did they give
    the same one" is scored. "Agreed" is the only positive class and nothing is ever negative,
    so ``fn`` and ``tn`` are structurally zero, and the identities follow: ``precision`` equals
    ``accuracy`` (``tp / n``), ``recall`` is 1.0 whenever ``n`` is non-zero, and
    ``f1 = 2a/(1+a)``. Only ``accuracy`` carries information. Comparison is plain ``==``;
    normalisation is the caller's job.

    Returns:
        A :class:`MatchReport`. Empty input gives every field zero rather than raising.

    Raises:
        ValueError: If the two sequences differ in length, via ``zip(strict=True)``.
    """
    n = len(y_true)
    tp = sum(1 for true, pred in zip(y_true, y_pred, strict=True) if true == pred)
    fp = n - tp
    accuracy = tp / n if n else 0.0

    return MatchReport(
        n=n,
        tp=tp,
        fp=fp,
        fn=0,
        tn=0,
        accuracy=accuracy,
        precision=accuracy,
        recall=1.0 if n else 0.0,
        f1=2 * accuracy / (1 + accuracy) if accuracy else 0.0,
    )


def cosine_similarity(a: Iterable[float], b: Iterable[float]) -> float:
    """Cosine similarity between two vectors, clipped to [-1.0, 1.0].

    The clip bounds floating-point error to a real cosine.

    Returns:
        The cosine similarity, or 0.0 if either vector is all zeros -- an undefined angle, and a
        zero embedding means the text was empty, which is a miss rather than a match.

    Raises:
        ValueError: If the vectors have different lengths.
    """
    vec_a = np.asarray(list(a), dtype=np.float64)
    vec_b = np.asarray(list(b), dtype=np.float64)

    if vec_a.shape != vec_b.shape:
        raise ValueError(f"Vectors must be the same length, got {vec_a.shape} and {vec_b.shape}.")

    norm = float(np.linalg.norm(vec_a) * np.linalg.norm(vec_b))

    if norm == 0.0 or math.isnan(norm):
        return 0.0

    return float(np.clip(float(np.dot(vec_a, vec_b)) / norm, -1.0, 1.0))


def mean_pool(
    vectors: Sequence[Sequence[float]], weights: Sequence[float] | None = None
) -> list[float]:
    """Combine several chunk embeddings into one vector for the whole text.

    Each vector is **L2-normalised first**, so the pool depends on direction alone -- an
    unnormalised average would let one chunk's magnitude dominate. ``weights`` are normally the
    chunks' character counts, so long exchanges outweigh short closing lines.

    Args:
        vectors: One vector per chunk. All must share a dimension.
        weights: Relative weight per chunk, or None for a plain mean.

    Returns:
        The pooled vector, as a plain list.

    Raises:
        ValueError: If ``vectors`` is empty, the dimensions are ragged, ``weights`` differs in
            length from ``vectors``, or the weights sum to zero.
    """
    if not vectors:
        raise ValueError("mean_pool needs at least one vector.")

    rows = [list(vector) for vector in vectors]

    # Checked before np.asarray, whose "inhomogeneous shape" error names no chunk.
    widths = {len(row) for row in rows}
    if len(widths) != 1:
        raise ValueError(f"All vectors must be the same length, got {sorted(widths)}.")

    matrix = np.asarray(rows, dtype=np.float64)

    if weights is None:
        weight_array = np.ones(len(matrix), dtype=np.float64)
    else:
        if len(weights) != len(matrix):
            raise ValueError(
                f"Got {len(weights)} weights for {len(matrix)} vectors; they must match."
            )
        weight_array = np.asarray(list(weights), dtype=np.float64)

    total = float(weight_array.sum())
    if total <= 0.0:
        raise ValueError("Weights must sum to a positive value.")

    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    # A zero vector stays zeros rather than dividing to NaN and poisoning the pool.
    unit = np.divide(matrix, norms, out=np.zeros_like(matrix), where=norms > 0)

    return list(np.average(unit, axis=0, weights=weight_array))


def character_error_rate(reference: str, hypothesis: str) -> float:
    """Character Error Rate between a reference transcript and a hypothesis.

    ``(insertions + deletions + substitutions) / len(reference)`` via ``jiwer`` -- the right
    measure for Thai, which has no word boundaries for a word-level rate. Deliberately **not
    clipped at 1.0**: a hypothesis longer than its reference genuinely costs more edits than
    the reference has characters.

    Returns:
        The error rate. 0.0 is a perfect match; **lower is better**, unlike every other metric
        in this module.
    """
    # jiwer raises on an empty reference -- it is the denominator. Both empty is a vacuous match
    # rather than an error; a hypothesis against no reference is entirely insertions, so 1.0.
    if not reference:
        return 0.0 if not hypothesis else 1.0

    return float(jiwer.cer(reference, hypothesis))


def error_rate_stats(rates: Sequence[float], threshold: float) -> ErrorRateReport:
    """Summarise a column of error rates -- the mirror of :func:`similarity_stats`.

    Every comparison is reversed: a row passes at or **below** the threshold, ``best`` is the
    smallest rate and ``worst`` the largest.

    Returns:
        An :class:`ErrorRateReport`. Empty input yields zeros rather than raising.
    """
    if not rates:
        return ErrorRateReport(
            mean=0.0,
            median=0.0,
            best=0.0,
            worst=0.0,
            pass_rate=0.0,
            threshold=threshold,
            n=0,
        )

    return ErrorRateReport(
        mean=sum(rates) / len(rates),
        median=float(median(rates)),
        best=min(rates),
        worst=max(rates),
        pass_rate=sum(1 for rate in rates if rate <= threshold) / len(rates),
        threshold=threshold,
        n=len(rates),
    )


def similarity_stats(scores: Sequence[float], threshold: float) -> SimilarityReport:
    """Summarise a column of similarity scores.

    The minimum and pass rate travel with the mean, which alone would hide a bad tail.

    Returns:
        A :class:`SimilarityReport`. Empty input yields zeros rather than raising.
    """
    if not scores:
        return SimilarityReport(
            mean=0.0,
            median=0.0,
            minimum=0.0,
            maximum=0.0,
            pass_rate=0.0,
            threshold=threshold,
            n=0,
        )

    return SimilarityReport(
        mean=sum(scores) / len(scores),
        median=float(median(scores)),
        minimum=min(scores),
        maximum=max(scores),
        pass_rate=sum(1 for s in scores if s >= threshold) / len(scores),
        threshold=threshold,
        n=len(scores),
    )
