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

from .metrics import DimensionResult, outer_join
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
    """Item-level correctness. None means the item is not scorable in this dimension."""
    if gt is None:
        return None
    if dimension == "call_result":
        if gt.call_result is None:
            return None
        return pred is not None and pred.call_result == gt.call_result
    if dimension == "reason":
        return pred is not None and pred.reasons == gt.reasons
    if dimension == "product":
        return pred is not None and pred.product == gt.product
    raise ValueError(f"unknown dimension {dimension!r}")


def _label(rec: Record | None, dimension: str) -> str:
    if rec is None:
        return "<no prediction>"
    if dimension == "call_result":
        return rec.call_result or "<empty>"
    if dimension == "reason":
        return ", ".join(sorted(rec.reasons)) or "<empty>"
    return rec.product or "<empty>"


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


def disagreement(
    gt: list[Record],
    incumbent: list[Record],
    candidate: list[Record],
    dimension: str,
) -> Disagreement:
    """Build the 2x2 over items scorable for BOTH arms."""
    inc_by_key = {r.key: r for r in incumbent}
    cand_by_key = {r.key: r for r in candidate}

    br = bw = ior = cor = 0
    for g, _ in outer_join(gt, []):
        if g is None:
            continue
        i_ok = _correct(g, inc_by_key.get(g.key), dimension)
        c_ok = _correct(g, cand_by_key.get(g.key), dimension)
        if i_ok is None or c_ok is None:
            continue
        if i_ok and c_ok:
            br += 1
        elif not i_ok and not c_ok:
            bw += 1
        elif i_ok:
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

    inc_by_key = {r.key: r for r in incumbent}
    cand_by_key = {r.key: r for r in candidate}

    rows: list[RegressionRow] = []
    for g in gt:
        i, c = inc_by_key.get(g.key), cand_by_key.get(g.key)
        i_ok, c_ok = _correct(g, i, dimension), _correct(g, c, dimension)
        if i_ok is None or c_ok is None or not i_ok or c_ok:
            continue
        rows.append(
            RegressionRow(
                item_key=item_key(g, hash_key),
                dimension=dimension,
                gt_label=_label(g, dimension),
                incumbent_label=_label(i, dimension),
                candidate_label=_label(c, dimension),
                error_type="missing_output" if c is None or not c.parse_ok else "wrong_label",
            )
        )
    return rows
