"""Confidence intervals, which this project has never had.

WHAT WAS MISSING AND WHY IT MATTERS. Every verdict here comes from `compare.exact_band`, a
paired sign test. It answers one question well -- "is the candidate different from the
incumbent?" -- and returns UNDERPOWERED rather than a tie when it cannot tell. What it never
answers is the other question: "how precisely do we know this arm's accuracy?"

Those are different questions and the repository has been careful about not conflating them.
`experiments.py:1041-1044` says outright that its loss guard is "not a confidence interval
and not a claim of statistical non-inferiority"; `pooled_bands.py` says a band is "a
threshold on a point estimate. It is not a significance test." Both are correct, and both
describe an absence. This module fills it.

A worked reason it is needed: business accuracy on the pooled text packs is 175/188 = 93.1%
for both arms. Quoted bare, that reads as a precise measurement. It is not -- the interval
is roughly +/-4 points, which is wider than most of the differences anyone has argued about
in this project.

THE ALPHA IS THE PROJECT'S, NOT A NEW ONE. `exact_band` uses alpha 1/64 per side. Defaulting
these intervals to the conventional 95% would put two different confidence levels in one
report and invite the reader to compare a 95% interval with a 96.875% test. So the default
here is the same 1/64 per side, two-sided, and it is stated in the output rather than
assumed.

THREE ESTIMATORS, FOR THREE SHAPES OF STATISTIC:

  * `clopper_pearson` -- a proportion (accuracy, per-class recall). Exact, built on the same
    binomial tail `compare._p_net_at_least` uses, so it never disagrees with the sign test
    about what the binomial distribution is. Conservative by construction: real coverage is
    at least the nominal level, never below.
  * `wilson` -- the same job, closed-form and tighter. Better behaved than the textbook
    normal approximation at the extremes, which matters here because accuracy sits near 0.95
    where the naive interval runs past 1.0.
  * `bootstrap` -- anything with no closed form. Weighted F1 is the case that forces it:
    it is a ratio of sums over classes with different denominators, and there is no exact
    interval for that.

THE RESAMPLING UNIT IS THE CALL, NOT THE ROW. `compare.comparison_clusters` exists precisely
because one transcript can produce several product rows, and those rows are correlated --
they come from the same call, the same audio, the same model invocation. Bootstrapping rows
would treat a 3-row call as three independent observations and report an interval roughly
sqrt(3) too narrow. Every caller must resample clusters.

NO NEW DEPENDENCY. `requirements.txt` pins what the production scorer computes, so this uses
`math` and `statistics.NormalDist` from the standard library rather than adding scipy for
two quantiles.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from statistics import NormalDist
from typing import Callable, Sequence

# The same alpha the paired test uses, per side. Two-sided that is 1/32, i.e. a 96.875%
# interval. Deliberately not 0.05: one report should not carry two confidence levels.
DEFAULT_ALPHA_PER_SIDE = 1 / 64


@dataclass(frozen=True)
class Interval:
    """A point estimate and its bounds, carrying the alpha that produced it."""

    point: float
    low: float
    high: float
    alpha_per_side: float
    method: str
    n: int

    @property
    def confidence(self) -> float:
        return 1 - 2 * self.alpha_per_side

    @property
    def width(self) -> float:
        return self.high - self.low

    def __str__(self) -> str:
        return f"{self.point:.3f} [{self.low:.3f}, {self.high:.3f}]"

    def overlaps(self, other: "Interval") -> bool:
        """Whether two intervals overlap.

        Deliberately NOT a significance test, and the reason is worth stating because the
        mistake is common: two overlapping intervals can still come from significantly
        different populations, and for a PAIRED comparison -- which is what this project
        runs -- overlap says almost nothing, because it throws away the pairing. Use
        `compare.paired_verdict` for that. This exists to render a figure, not to decide.
        """
        return not (self.high < other.low or other.high < self.low)


def _log_pmf(n: int, i: int, p: float) -> float:
    """log P(X = i) for X ~ Binomial(n, p), via lgamma.

    Computed in log space rather than as `math.comb(n, i) * p**i * (1-p)**(n-i)`, which is
    the obvious formulation and overflows. `math.comb(2000, 1000)` is a ~600-digit integer;
    multiplying it by a float raises OverflowError, so the direct version crashed on any
    corpus past roughly a thousand items while looking correct on the 188 it was tried on.
    """
    if p <= 0.0:
        return 0.0 if i == 0 else -math.inf
    if p >= 1.0:
        return 0.0 if i == n else -math.inf
    return (
        math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1)
        + i * math.log(p) + (n - i) * math.log1p(-p)
    )


def _binomial_tail_at_least(n: int, k: int, p: float) -> float:
    """P(X >= k) for X ~ Binomial(n, p)."""
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    # Sum the shorter tail and complement when that is the smaller of the two, so a tail of
    # a few terms never costs a pass over the whole distribution.
    if k > n // 2:
        return math.fsum(math.exp(_log_pmf(n, i, p)) for i in range(k, n + 1))
    return 1.0 - math.fsum(math.exp(_log_pmf(n, i, p)) for i in range(0, k))


def _binomial_tail_at_most(n: int, k: int, p: float) -> float:
    """P(X <= k) for X ~ Binomial(n, p)."""
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    if k < n // 2:
        return math.fsum(math.exp(_log_pmf(n, i, p)) for i in range(0, k + 1))
    return 1.0 - math.fsum(math.exp(_log_pmf(n, i, p)) for i in range(k + 1, n + 1))


def _solve(target: float, predicate: Callable[[float], float]) -> float:
    """Bisect for the p where `predicate(p) == target`. Monotone predicates only."""
    lo, hi = 0.0, 1.0
    for _ in range(80):          # 80 halvings is far below float resolution; exact enough
        mid = (lo + hi) / 2
        if predicate(mid) > target:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def clopper_pearson(
    successes: int, n: int, *, alpha_per_side: float = DEFAULT_ALPHA_PER_SIDE
) -> Interval:
    """Exact interval for a proportion. Conservative: coverage >= nominal, never below.

    Built by inverting the exact binomial test rather than through the Beta quantile, so it
    depends on nothing beyond `math.comb` -- the same primitive `compare` already uses.
    """
    if n <= 0:
        raise ValueError("n must be positive")
    if not 0 <= successes <= n:
        raise ValueError(f"successes {successes} outside 0..{n}")
    if not 0 < alpha_per_side < 0.5:
        raise ValueError("alpha_per_side must be between 0 and 0.5")

    point = successes / n
    # Both bounds invert an exact tail, and both predicates must be INCREASING in p for the
    # bisection to converge -- which is why the upper one is written as a complement.
    #   lower: the p at which seeing this many or more successes is already unlikely
    #   upper: the p at which seeing this many or fewer is already unlikely
    low = 0.0 if successes == 0 else _solve(
        alpha_per_side, lambda p: _binomial_tail_at_least(n, successes, p)
    )
    high = 1.0 if successes == n else _solve(
        1 - alpha_per_side, lambda p: 1 - _binomial_tail_at_most(n, successes, p)
    )
    return Interval(point, low, high, alpha_per_side, "clopper-pearson", n)


def wilson(
    successes: int, n: int, *, alpha_per_side: float = DEFAULT_ALPHA_PER_SIDE
) -> Interval:
    """Closed-form interval for a proportion. Tighter than Clopper-Pearson, still sane at
    the extremes where the textbook normal interval runs past 0 or 1."""
    if n <= 0:
        raise ValueError("n must be positive")
    if not 0 <= successes <= n:
        raise ValueError(f"successes {successes} outside 0..{n}")

    z = NormalDist().inv_cdf(1 - alpha_per_side)
    phat = successes / n
    denom = 1 + z**2 / n
    centre = (phat + z**2 / (2 * n)) / denom
    spread = z * math.sqrt(phat * (1 - phat) / n + z**2 / (4 * n**2)) / denom
    return Interval(phat, max(0.0, centre - spread), min(1.0, centre + spread),
                    alpha_per_side, "wilson", n)


def bootstrap(
    units: Sequence,
    statistic: Callable[[Sequence], float],
    *,
    alpha_per_side: float = DEFAULT_ALPHA_PER_SIDE,
    resamples: int = 10_000,
    seed: int = 20260818,
) -> Interval:
    """Percentile bootstrap over `units`, for statistics with no closed form.

    `units` MUST be independent observations -- call clusters, not product rows. See the
    module docstring: resampling rows understates the interval by roughly sqrt(rows/calls).

    Seeded, because a confidence interval that moves between two runs of the same data is
    not a fact about the data. The seed is recorded in the method string.
    """
    if not units:
        raise ValueError("nothing to resample")
    if not 0 < alpha_per_side < 0.5:
        raise ValueError("alpha_per_side must be between 0 and 0.5")

    rng = random.Random(seed)
    n = len(units)
    draws = []
    for _ in range(resamples):
        sample = [units[rng.randrange(n)] for _ in range(n)]
        draws.append(statistic(sample))
    draws.sort()

    def percentile(q: float) -> float:
        # Nearest rank, matching the convention model_comparison_report.py uses for latency
        # percentiles, so two percentiles in one report never mean two different things.
        idx = max(0, min(len(draws) - 1, math.ceil(q * len(draws)) - 1))
        return draws[idx]

    return Interval(
        statistic(units), percentile(alpha_per_side), percentile(1 - alpha_per_side),
        alpha_per_side, f"bootstrap-{resamples}-seed{seed}", n,
    )
