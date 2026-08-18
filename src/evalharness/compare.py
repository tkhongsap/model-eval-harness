"""Paired incumbent-vs-candidate comparison.

The unit is the ITEM, not the aggregate. A portfolio accuracy delta cannot tell you
whether a candidate failed on scattered edge cases or on one coherent slice, and the
framework is explicit that the per-item regression list is what the review reads.

Two refusals are built in rather than left to the caller:

  * `CoverageMismatch` when the arms did not score the same items. Comparing an arm
    that answered 100 items against one that answered 80 is not a comparison, and
    the arm that answered less scores HIGHER because its failures were dropped.
  * `accuracy` is never reported as the headline. Recall and F1 carry the gate.
    See tests/test_differential.py::test_production_really_does_inflate_the_empty_arm.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import comb
from typing import Literal

from .metrics import DimensionResult
from .records import Record

# Arms may differ in coverage by at most this fraction before comparison refuses.
DEFAULT_COVERAGE_EPSILON = 0.02


class CoverageMismatch(RuntimeError):
    """Raised when two arms did not score a comparable set of items."""


@dataclass(frozen=True)
class Disagreement:
    """The 2x2. Counts MUST sum to the number of compared items."""

    dimension: str
    both_right: int
    both_wrong: int
    incumbent_only_right: int
    candidate_only_right: int

    @property
    def total(self) -> int:
        return (
            self.both_right
            + self.both_wrong
            + self.incumbent_only_right
            + self.candidate_only_right
        )

    @property
    def net(self) -> int:
        """Positive means the candidate won more items than it lost."""
        return self.candidate_only_right - self.incumbent_only_right


PairedVerdictName = Literal[
    "AHEAD", "BEHIND", "INDISTINGUISHABLE", "UNDERPOWERED"
]


@dataclass(frozen=True)
class PairedVerdict:
    """Exact directional verdict over the discordant pairs only."""

    dimension: str
    discordant: int
    net: int
    band: int | None
    alpha_per_side: float
    verdict: PairedVerdictName


def _p_net_at_least(discordant: int, net: int) -> float:
    """P(candidate net >= `net`) under X~Binomial(discordant, 1/2)."""
    first_candidate_wins = (discordant + net + 1) // 2
    return sum(
        comb(discordant, wins)
        for wins in range(first_candidate_wins, discordant + 1)
    ) / (2**discordant)


def exact_band(discordant: int, *, alpha_per_side: float = 1 / 64) -> int | None:
    """Smallest parity-compatible net whose one-sided null tail is within alpha.

    None is the result when even a clean sweep cannot meet the evidence threshold.
    At alpha 1/64 that is every d below six.
    """
    if discordant < 0:
        raise ValueError("discordant count cannot be negative")
    if not 0 < alpha_per_side < 0.5:
        raise ValueError("alpha_per_side must be between 0 and 0.5")
    for band in range(discordant % 2, discordant + 1, 2):
        if _p_net_at_least(discordant, band) <= alpha_per_side:
            return band
    return None


def paired_verdict(
    table: Disagreement, *, alpha_per_side: float = 1 / 64
) -> PairedVerdict:
    """Classify one disagreement table without treating no evidence as a tie."""
    discordant = table.incumbent_only_right + table.candidate_only_right
    band = exact_band(discordant, alpha_per_side=alpha_per_side)
    if band is None:
        verdict: PairedVerdictName = "UNDERPOWERED"
    elif table.net >= band:
        verdict = "AHEAD"
    elif table.net <= -band:
        verdict = "BEHIND"
    else:
        verdict = "INDISTINGUISHABLE"
    return PairedVerdict(
        dimension=table.dimension,
        discordant=discordant,
        net=table.net,
        band=band,
        alpha_per_side=alpha_per_side,
        verdict=verdict,
    )


@dataclass(frozen=True)
class RegressionRow:
    """One item the incumbent got right and the candidate got wrong.

    Carries no customer identifier: `item_key` is the hashed key. Resolving it back
    to a call is done inside True's systems, by whoever holds the HMAC key.
    """

    item_key: str
    dimension: str
    gt_label: str
    incumbent_label: str
    candidate_label: str
    error_type: str


def _correct(gt: Record | None, pred: Record | None, dimension: str) -> bool | None:
    """Item-level correctness, including absence and invalid-output semantics.

    An absent ground-truth row represents the negative side of an outer join: an arm
    is right only when it makes no claim in that dimension.  A present prediction
    whose ``parse_ok`` is false is never evidence of correctness, even if a failure
    skeleton happens to carry values copied from the ground truth to retain its row
    shape.

    ``None`` means the ground-truth row itself is outside the dimension's scoring
    population (currently only a missing ``call_result``).
    """
    if gt is None:
        if pred is None:
            return True
        if not pred.parse_ok:
            return False
        if dimension == "call_result":
            return pred.call_result is None
        if dimension == "reason":
            return not pred.reasons
        if dimension == "product":
            return pred.product is None
        raise ValueError(f"unknown dimension {dimension!r}")
    if dimension == "call_result":
        if gt.call_result is None:
            return None
        return (
            pred is not None
            and pred.parse_ok
            and pred.call_result == gt.call_result
        )
    if dimension == "reason":
        return pred is not None and pred.parse_ok and pred.reasons == gt.reasons
    if dimension == "product":
        return pred is not None and pred.parse_ok and pred.product == gt.product
    raise ValueError(f"unknown dimension {dimension!r}")


def _label(rec: Record | None, dimension: str) -> str:
    if rec is None:
        return "<no prediction>"
    if dimension == "call_result":
        return rec.call_result or "<empty>"
    if dimension == "reason":
        return ", ".join(sorted(rec.reasons)) or "<empty>"
    return rec.product or "<empty>"


def _unique_by_key(records: list[Record], role: str) -> dict[tuple, Record]:
    """Index records without silently applying dict's last-row-wins behaviour.

    Merge keys can contain customer identifiers, so refusal messages deliberately
    report only counts.  The private source data is where a duplicate is resolved.
    """
    indexed: dict[tuple, Record] = {}
    duplicates = 0
    for record in records:
        if record.key in indexed:
            duplicates += 1
        else:
            indexed[record.key] = record
    if duplicates:
        raise CoverageMismatch(
            f"{role} contains {duplicates} duplicate prediction/ground-truth merge "
            "key(s). Paired comparison refuses last-row-wins ambiguity."
        )
    return indexed


def _identity_set(
    records: list[Record], dimension: str, role: str
) -> frozenset[tuple]:
    """The items an arm covers in one dimension, at CALL grain in all three.

    Call grain, not merge grain, and the difference is what decides whether this gate
    can be switched on at all.  ``Record.key`` carries the product, so at merge grain a
    model that answers TOL where the ground truth says Postpaid reads as one missing
    identity plus one extra one -- indistinguishable, here, from an item that vanished
    from the arm.  That is not a coverage fact.  It is the product dimension's finding,
    already scored, and already printed as a regression row with ``error_type``
    ``extra_output``.  Measured: pooled product accuracy over packs A+B is 181/188 and
    180/188 (docs/migration-decision.md), so roughly seven calls per arm name a product
    set the ground truth does not, and a merge-grain gate would have refused to produce
    the Experiment 7 comparison at all rather than reporting those seven calls.

    Coverage asks the narrower question -- was this call scored by both arms -- and
    that is also the grain the paired tables are counted on (``comparison_clusters``)
    and the grain ``scripts/pooled_bands.py`` depends on when it requires all three 2x2
    tables to sum to the same 188.  The product dimension was already written this way;
    the other two were not, and closing that inconsistency is what makes the strict gate
    usable rather than merely present.

    What makes call grain a real gate rather than a softer one: ``flatten.to_rows``
    takes ``call_id`` and ``phone_number`` from the testset item and never from the
    payload, precisely so a model cannot invent a merge key.  An arm therefore cannot
    gain or lose a call identity by answering badly.  At this grain the gate fires only
    when the pipeline itself dropped an item, which is the event it exists to catch.

    ``_unique_by_key`` still runs on the full merge key, so duplicate rows are refused
    here exactly as they are in every other paired path.
    """
    _unique_by_key(records, role)
    if dimension in {"call_result", "reason"}:
        return frozenset(record.call_key for record in records)
    if dimension == "product":
        # The production scorer's explicit null-phone drop: a call with no phone is
        # outside this dimension's population, so it is outside its coverage too.
        return frozenset(
            record.call_key for record in records if record.phone is not None
        )
    raise ValueError(f"unknown dimension {dimension!r}")


def check_exact_coverage(
    gt: list[Record],
    incumbent: list[Record],
    candidate: list[Record],
    dimension: str,
) -> None:
    """Refuse unless both arms cover exactly the ground truth's call identities.

    This is the decision path's gate, wired in at ``evalgen.cli`` as ``--coverage
    strict`` (the default).  ``check_coverage`` remains available for exploratory runs
    over partial data: it reads aggregate counts and tolerates 2% of drift, which on
    188 items is about four items that can be scored in one arm and not the other.
    Equal counts are insufficient anyway -- one missing expected item and one unrelated
    extra item leave every count untouched and still make a different population.

    ``_identity_set`` documents the grain and why the product dimension additionally
    mirrors the production scorer's null-phone drop.  No identity values appear in the
    exception because phone is part of them.
    """
    expected = _identity_set(gt, dimension, "ground truth")
    problems: list[str] = []
    for role, records in (("incumbent", incumbent), ("candidate", candidate)):
        observed = _identity_set(records, dimension, role)
        missing = len(expected - observed)
        extra = len(observed - expected)
        if missing or extra:
            problems.append(f"{role}: {missing} missing, {extra} extra")
    if problems:
        raise CoverageMismatch(
            f"{dimension}: exact coverage differs from ground truth ("
            + "; ".join(problems)
            + "). Refusing to compare different unit identities."
        )


def check_coverage(
    incumbent: DimensionResult,
    candidate: DimensionResult,
    epsilon: float = DEFAULT_COVERAGE_EPSILON,
) -> None:
    """Refuse to compare arms that did not score the same items.

    This is the guard against the failure mode the empty-arm test demonstrates: an
    arm whose unparseable items were dropped has a smaller, easier denominator.
    """
    a, b = incumbent.coverage.items_scored, candidate.coverage.items_scored
    if a == 0 or b == 0:
        raise CoverageMismatch(
            f"{incumbent.dimension}: an arm scored zero items "
            f"(incumbent={a}, candidate={b}). Nothing to compare."
        )
    drift = abs(a - b) / max(a, b)
    if drift > epsilon:
        raise CoverageMismatch(
            f"{incumbent.dimension}: coverage differs by {drift:.1%} "
            f"(incumbent scored {a}, candidate scored {b}, "
            f"parse failures {incumbent.coverage.parse_failures} vs "
            f"{candidate.coverage.parse_failures}). The arm scoring fewer items has "
            "an easier denominator, so any accuracy comparison would favour it. "
            "Fix the coverage gap or report the arms separately."
        )


@dataclass(frozen=True)
class ComparisonUnit:
    """One canonical paired event shared by tables and regression rows."""

    source: Record
    gt_label: str
    incumbent_label: str
    candidate_label: str
    incumbent_right: bool
    candidate_right: bool
    candidate_error_type: str
    rule_labels: frozenset[str]


def _rule_labels(record: Record | None, dimension: str) -> frozenset[str]:
    if record is None:
        return frozenset()
    if dimension == "call_result":
        return frozenset({record.call_result}) if record.call_result else frozenset()
    if dimension == "reason":
        return record.reasons
    if dimension == "product":
        return frozenset({record.product}) if record.product else frozenset()
    raise ValueError(f"unknown dimension {dimension!r}")


def _ordered_union(*groups: dict[tuple, object]) -> list[tuple]:
    ordered: list[tuple] = []
    seen: set[tuple] = set()
    for group in groups:
        for key in group:
            if key not in seen:
                seen.add(key)
                ordered.append(key)
    return ordered


def _record_has_claim(record: Record | None, dimension: str) -> bool:
    if record is None:
        return False
    if not record.parse_ok:
        return True
    if dimension == "call_result":
        return record.call_result is not None
    if dimension == "reason":
        return bool(record.reasons)
    raise ValueError(f"unknown record-grain dimension {dimension!r}")


def _record_error_type(
    gt_record: Record | None, candidate: Record | None, dimension: str
) -> str:
    if candidate is None:
        return "missing_output"
    if not candidate.parse_ok:
        return "invalid_output"
    if gt_record is None and _record_has_claim(candidate, dimension):
        return "extra_output"
    return "wrong_label"


def _record_units(
    gt: list[Record],
    incumbent: list[Record],
    candidate: list[Record],
    dimension: str,
) -> list[ComparisonUnit]:
    gt_by_key = _unique_by_key(gt, "ground truth")
    inc_by_key = _unique_by_key(incumbent, "incumbent")
    cand_by_key = _unique_by_key(candidate, "candidate")
    units: list[ComparisonUnit] = []

    for key in _ordered_union(gt_by_key, inc_by_key, cand_by_key):
        g, i, c = gt_by_key.get(key), inc_by_key.get(key), cand_by_key.get(key)
        if g is not None and dimension == "call_result" and g.call_result is None:
            continue
        # An orphan row that makes no claim in either arm is not a unit in this
        # dimension.  If either arm makes a claim, absence in the other arm is the
        # correct negative and the false positive becomes a paired event.
        if g is None and not (
            _record_has_claim(i, dimension) or _record_has_claim(c, dimension)
        ):
            continue
        i_ok, c_ok = _correct(g, i, dimension), _correct(g, c, dimension)
        if i_ok is None or c_ok is None:  # guarded above; defensive for new dimensions
            continue
        source = g or i or c
        assert source is not None
        units.append(
            ComparisonUnit(
                source=source,
                gt_label="<no ground truth>" if g is None else _label(g, dimension),
                incumbent_label=_label(i, dimension),
                candidate_label=_label(c, dimension),
                incumbent_right=i_ok,
                candidate_right=c_ok,
                candidate_error_type=_record_error_type(g, c, dimension),
                rule_labels=(
                    _rule_labels(g, dimension)
                    | _rule_labels(i, dimension)
                    | _rule_labels(c, dimension)
                ),
            )
        )
    return units


def _product_groups(
    records: list[Record], role: str
) -> dict[tuple[str, str | None], list[Record]]:
    _unique_by_key(records, role)
    groups: dict[tuple[str, str | None], list[Record]] = {}
    for record in records:
        # Preserve the scorer's documented production-fidelity null-phone drop.
        if record.phone is None:
            continue
        groups.setdefault(record.call_key, []).append(record)
    return groups


def _product_label(records: list[Record] | None, *, ground_truth: bool = False) -> str:
    if records is None:
        return "<no ground truth>" if ground_truth else "<no prediction>"
    products = sorted(
        {record.product for record in records if record.product is not None}
    )
    return ", ".join(products) or "<empty>"


def _product_units(
    gt: list[Record], incumbent: list[Record], candidate: list[Record]
) -> list[ComparisonUnit]:
    gt_groups = _product_groups(gt, "ground truth")
    inc_groups = _product_groups(incumbent, "incumbent")
    cand_groups = _product_groups(candidate, "candidate")
    units: list[ComparisonUnit] = []

    for key in _ordered_union(gt_groups, inc_groups, cand_groups):
        g, i, c = gt_groups.get(key), inc_groups.get(key), cand_groups.get(key)
        gt_products = {row.product for row in (g or []) if row.product is not None}
        inc_products = {row.product for row in (i or []) if row.product is not None}
        cand_products = {row.product for row in (c or []) if row.product is not None}
        i_ok = (
            i is None or all(row.parse_ok for row in i)
        ) and inc_products == gt_products
        c_ok = (
            c is None or all(row.parse_ok for row in c)
        ) and cand_products == gt_products

        source_row = (g or i or c or [None])[0]
        assert source_row is not None
        # Product comparison is call-grain.  Hash a canonical call-level record so
        # the item key does not depend on which product happened to appear first.
        source = Record(
            call_id=source_row.call_id,
            phone=source_row.phone,
            product=None,
            call_result=None,
        )
        if c is None:
            candidate_error_type = "missing_output"
        elif not all(row.parse_ok for row in c):
            candidate_error_type = "invalid_output"
        elif cand_products - gt_products:
            candidate_error_type = "extra_output"
        else:
            candidate_error_type = "wrong_label"
        units.append(
            ComparisonUnit(
                source=source,
                gt_label=_product_label(g, ground_truth=True),
                incumbent_label=_product_label(i),
                candidate_label=_product_label(c),
                incumbent_right=i_ok,
                candidate_right=c_ok,
                candidate_error_type=candidate_error_type,
                rule_labels=frozenset(
                    product
                    for product in (gt_products | inc_products | cand_products)
                    if product is not None
                ),
            )
        )
    return units


def _comparison_units(
    gt: list[Record],
    incumbent: list[Record],
    candidate: list[Record],
    dimension: str,
) -> list[ComparisonUnit]:
    if dimension in {"call_result", "reason"}:
        return _record_units(gt, incumbent, candidate, dimension)
    if dimension == "product":
        return _product_units(gt, incumbent, candidate)
    raise ValueError(f"unknown dimension {dimension!r}")


@dataclass(frozen=True)
class ComparisonCluster:
    """One statistically independent call-level paired event.

    The normalized scorer remains product-row shaped, and regression review keeps
    that actionable grain.  Inference must not pretend that two products from one
    call are two independent customers, however.  A cluster is right only when the
    arm is right on every scored unit from that call.
    """

    source: Record
    gt_label: str
    incumbent_label: str
    candidate_label: str
    incumbent_right: bool
    candidate_right: bool
    unit_count: int
    rule_labels: frozenset[str]


def _cluster_label(units: list[ComparisonUnit], field: str) -> str:
    values = [getattr(unit, field) for unit in units]
    if len(set(values)) == 1:
        return values[0]
    tagged = sorted(
        (
            unit.source.product or "<call>",
            getattr(unit, field),
        )
        for unit in units
    )
    return " | ".join(f"{product}: {label}" for product, label in tagged)


def comparison_clusters(
    gt: list[Record],
    incumbent: list[Record],
    candidate: list[Record],
    dimension: str,
) -> list[ComparisonCluster]:
    """Return canonical paired evidence clustered once per call.

    This is the population used by McNemar/exact paired verdicts and by the advisory
    judge.  Multiple product rows from one transcript therefore move one Bernoulli
    unit, not several correlated units.  Product-level regression rows are still
    produced separately by :func:`regressions`.
    """
    grouped: dict[tuple[str, str | None], list[ComparisonUnit]] = {}
    for unit in _comparison_units(gt, incumbent, candidate, dimension):
        grouped.setdefault(unit.source.call_key, []).append(unit)

    clusters: list[ComparisonCluster] = []
    for units in grouped.values():
        first = units[0].source
        clusters.append(
            ComparisonCluster(
                source=Record(
                    call_id=first.call_id,
                    phone=first.phone,
                    product=None,
                    call_result=None,
                ),
                gt_label=_cluster_label(units, "gt_label"),
                incumbent_label=_cluster_label(units, "incumbent_label"),
                candidate_label=_cluster_label(units, "candidate_label"),
                incumbent_right=all(unit.incumbent_right for unit in units),
                candidate_right=all(unit.candidate_right for unit in units),
                unit_count=len(units),
                rule_labels=frozenset().union(
                    *(unit.rule_labels for unit in units)
                ),
            )
        )
    return clusters


def label_set(record: Record | None, dimension: str) -> frozenset[str]:
    """The set of labels one record asserts in one dimension.

    Public alias of the set this module already builds for rule lookup.  Exposed so a
    diagnostic can ask what an arm claimed without re-deriving the per-dimension shapes
    (one value for ``call_result``, a union of three columns for ``reason``) a second
    time somewhere else.
    """
    return _rule_labels(record, dimension)


def comparison_units(
    gt: list[Record],
    incumbent: list[Record],
    candidate: list[Record],
    dimension: str,
) -> list[ComparisonUnit]:
    """The canonical paired population at scored-row grain, before call clustering.

    :func:`comparison_clusters` is the population for inference and stays that way.
    This is the same population at the grain a regression row is actionable on, which
    is what a per-answer diagnostic has to read: a cluster's label can concatenate
    several products (``_cluster_label``), so it is a display string and not a label
    set.  Sharing this rather than re-deriving correctness is the same rule
    ``comparison_clusters`` documents for the judge.
    """
    return _comparison_units(gt, incumbent, candidate, dimension)


def disagreement(
    gt: list[Record],
    incumbent: list[Record],
    candidate: list[Record],
    dimension: str,
) -> Disagreement:
    """Build the 2x2 over independent call clusters, including orphan claims."""
    br = bw = ior = cor = 0
    for unit in comparison_clusters(gt, incumbent, candidate, dimension):
        if unit.incumbent_right and unit.candidate_right:
            br += 1
        elif not unit.incumbent_right and not unit.candidate_right:
            bw += 1
        elif unit.incumbent_right:
            ior += 1
        else:
            cor += 1

    return Disagreement(
        dimension=dimension,
        both_right=br,
        both_wrong=bw,
        incumbent_only_right=ior,
        candidate_only_right=cor,
    )


def regressions(
    gt: list[Record],
    incumbent: list[Record],
    candidate: list[Record],
    dimension: str,
    hash_key: str,
) -> list[RegressionRow]:
    """Items the incumbent got right and the candidate got wrong.

    This is the deliverable. An aggregate delta says a candidate is 2 points worse;
    this says which 14 calls, so an app owner can decide whether the pattern matters.
    """
    from .keys import item_key

    rows: list[RegressionRow] = []
    for unit in _comparison_units(gt, incumbent, candidate, dimension):
        if not unit.incumbent_right or unit.candidate_right:
            continue
        rows.append(
            RegressionRow(
                item_key=item_key(unit.source, hash_key),
                dimension=dimension,
                gt_label=unit.gt_label,
                incumbent_label=unit.incumbent_label,
                candidate_label=unit.candidate_label,
                error_type=unit.candidate_error_type,
            )
        )
    return rows
