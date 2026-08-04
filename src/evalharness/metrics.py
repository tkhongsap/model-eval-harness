"""Clean-room scoring for the three Retention dimensions.

Pure Python, no pandas: the differential test compares this against production's
pandas implementation, so sharing a dataframe engine would let the same quirk hide
in both and the test would agree for the wrong reason.

**Three dimensions, three denominators.** This is the single most important fact in
the file, and a harness that computes one denominator and reuses it produces three
wrong numbers:

    call_result   rows whose GROUND TRUTH call_result is present  (dropna at :756)
    reason        every merged row, no dropna at all              (:849-962)
    product       call-grain groups, NaN phone keys dropped       (:1080-1081)

See tests/fixtures/WORKED-COMPUTATION.md for the arithmetic these must reproduce.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .records import Record


@dataclass(frozen=True)
class ClassMetrics:
    label: str
    tp: int
    fp: int
    fn: int
    tn: int

    @property
    def total(self) -> int:
        return self.tp + self.fp + self.fn + self.tn

    @property
    def weight(self) -> int:
        """Ground-truth positive count. Production uses tp+fn for reason/product
        and the crosstab row sum for call_result; both equal the GT count."""
        return self.tp + self.fn

    @property
    def accuracy(self) -> float:
        return (self.tp + self.tn) / self.total if self.total else 0.0

    @property
    def precision(self) -> float:
        d = self.tp + self.fp
        return self.tp / d if d else 0.0

    @property
    def recall(self) -> float:
        d = self.tp + self.fn
        return self.tp / d if d else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0


@dataclass(frozen=True)
class Coverage:
    """Made first-class because silent item loss is the failure mode that produces
    a confident wrong number. Every dimension reports it."""

    items_requested: int
    items_joined: int
    parse_failures: int
    items_scored: int

    @property
    def dropped(self) -> int:
        return self.items_requested - self.items_scored


@dataclass(frozen=True)
class DimensionResult:
    dimension: str
    classes: list[ClassMetrics]
    coverage: Coverage
    denominator: int

    def weighted(self, attr: str) -> float:
        """sum(metric * weight) / sum(weight), matching production's weighted_avg."""
        total_w = sum(c.weight for c in self.classes)
        if not total_w:
            return 0.0
        return sum(getattr(c, attr) * c.weight for c in self.classes) / total_w

    def totals(self) -> tuple[int, int, int, int]:
        return (
            sum(c.tp for c in self.classes),
            sum(c.fp for c in self.classes),
            sum(c.fn for c in self.classes),
            sum(c.tn for c in self.classes),
        )


def _one_vs_rest(
    label: str,
    pairs: list[tuple[bool, bool]],
) -> ClassMetrics:
    """pairs is [(in_ground_truth, in_prediction), ...] over the dimension's rows."""
    tp = sum(1 for g, p in pairs if g and p)
    fp = sum(1 for g, p in pairs if not g and p)
    fn = sum(1 for g, p in pairs if g and not p)
    tn = sum(1 for g, p in pairs if not g and not p)
    return ClassMetrics(label=label, tp=tp, fp=fp, fn=fn, tn=tn)


def outer_join(
    gt: list[Record], pred: list[Record]
) -> list[tuple[Record | None, Record | None]]:
    """Outer merge on (call_id, phone, product), mirroring fact_checker.py:1074-1078.

    Outer, not inner: a ground-truth row with no prediction must score FN rather
    than vanish. Sentiment QA inner-merges (fact_check_task.py:934) and therefore
    deletes exactly the items a weak model failed on, which flatters that model.
    """
    by_key_pred = {r.key: r for r in pred}
    by_key_gt = {r.key: r for r in gt}
    rows: list[tuple[Record | None, Record | None]] = []
    for key, g in by_key_gt.items():
        rows.append((g, by_key_pred.get(key)))
    for key, p in by_key_pred.items():
        if key not in by_key_gt:
            rows.append((None, p))
    return rows


def score_call_result(
    gt: list[Record], pred: list[Record], classes: tuple[str, ...]
) -> DimensionResult:
    """Denominator: rows whose GROUND TRUTH call_result is present.

    Production drops on `call_result_gt` (fact_checker.py:756), which removes the
    orphan prediction but KEEPS a ground-truth row whose prediction is missing:
    that row scores FN, which is the behaviour we want and must not lose.
    """
    joined = outer_join(gt, pred)
    scored = [(g, p) for g, p in joined if g is not None and g.call_result is not None]

    metrics = []
    for cls in classes:
        pairs = [
            (g.call_result == cls, p is not None and p.call_result == cls)
            for g, p in scored
        ]
        metrics.append(_one_vs_rest(cls, pairs))

    return DimensionResult(
        dimension="call_result",
        classes=metrics,
        coverage=Coverage(
            items_requested=len(gt),
            items_joined=sum(1 for g, p in joined if g is not None and p is not None),
            parse_failures=sum(1 for r in pred if not r.parse_ok),
            items_scored=len(scored),
        ),
        denominator=len(scored),
    )


def score_reason(
    gt: list[Record], pred: list[Record], classes: tuple[str, ...]
) -> DimensionResult:
    """Denominator: EVERY merged row. reason_calculation never calls dropna
    (fact_checker.py:849-962), so the orphan prediction is retained here even
    though call_result excluded it. Different denominator, deliberately."""
    joined = outer_join(gt, pred)

    metrics = []
    for cls in classes:
        pairs = [
            (
                g is not None and cls in g.reasons,
                p is not None and cls in p.reasons,
            )
            for g, p in joined
        ]
        metrics.append(_one_vs_rest(cls, pairs))

    return DimensionResult(
        dimension="reason",
        classes=metrics,
        coverage=Coverage(
            items_requested=len(gt),
            items_joined=sum(1 for g, p in joined if g is not None and p is not None),
            parse_failures=sum(1 for r in pred if not r.parse_ok),
            items_scored=len(joined),
        ),
        denominator=len(joined),
    )


def _group_products(records: list[Record]) -> dict[tuple[str, str | None], set[str]]:
    """Collapse to call grain, DROPPING groups whose phone key is None.

    This reproduces a production defect deliberately (fact_checker.py:1080-1081
    groups without dropna=False, and pandas drops NaN keys). Any call with a null,
    blank or zero phone number disappears from the product dimension while still
    being scored in call_result and reason. We reproduce it so the numbers stay
    comparable, and count it in coverage so the loss is visible rather than silent.
    """
    groups: dict[tuple[str, str | None], set[str]] = {}
    for r in records:
        if r.phone is None:  # the drop
            continue
        groups.setdefault(r.call_key, set())
        if r.product is not None:
            groups[r.call_key].add(r.product)
    return groups


def score_product(
    gt: list[Record], pred: list[Record], classes: tuple[str, ...]
) -> DimensionResult:
    """Denominator: call-grain groups surviving the NaN-phone drop."""
    gt_groups = _group_products(gt)
    pred_groups = _group_products(pred)
    keys = list(gt_groups) + [k for k in pred_groups if k not in gt_groups]

    metrics = []
    for cls in classes:
        pairs = [
            (cls in gt_groups.get(k, set()), cls in pred_groups.get(k, set()))
            for k in keys
        ]
        metrics.append(_one_vs_rest(cls, pairs))

    gt_calls = {r.call_key for r in gt}
    return DimensionResult(
        dimension="product",
        classes=metrics,
        coverage=Coverage(
            items_requested=len(gt_calls),
            items_joined=sum(1 for k in keys if k in gt_groups and k in pred_groups),
            parse_failures=sum(1 for r in pred if not r.parse_ok),
            items_scored=len(keys),
        ),
        denominator=len(keys),
    )
