"""Confidence intervals, checked against values that exist outside this repository.

A statistics helper is the easiest place in a codebase to be confidently wrong: every
function returns two plausible-looking floats and nothing crashes. So the anchor here is
published Clopper-Pearson values at the conventional 95%, not self-consistency. The bug this
caught on the way in was a real one -- the lower bound inverted its tail and returned 0.000
for every input, while the upper bound was correct, so the interval looked half-right.

One property deliberately NOT asserted: that Clopper-Pearson contains Wilson. It is intuitive
(CP is the conservative one) and it is false. At k=0, n=188, alpha=1/64 CP's upper bound is
0.0219 and Wilson's is 0.0241 -- Wilson is wider on that side. The two are different
intervals with different coverage properties and neither is nested in the other.
"""

from __future__ import annotations

import pytest

from evalharness.intervals import (
    DEFAULT_ALPHA_PER_SIDE,
    Interval,
    bootstrap,
    clopper_pearson,
    wilson,
)


# --------------------------------------------------------------------------------------
# Anchored to published values.
# --------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "successes,n,low,high",
    [
        (5, 10, 0.187, 0.813),
        (0, 10, 0.000, 0.308),
        (10, 10, 0.692, 1.000),
        (3, 20, 0.032, 0.379),
    ],
)
def test_clopper_pearson_matches_published_values(successes, n, low, high):
    """Standard 95% two-sided intervals, as tabulated in any statistics reference."""
    interval = clopper_pearson(successes, n, alpha_per_side=0.025)
    assert interval.low == pytest.approx(low, abs=0.001)
    assert interval.high == pytest.approx(high, abs=0.001)


def test_wilson_matches_published_values():
    interval = wilson(5, 10, alpha_per_side=0.025)
    assert interval.low == pytest.approx(0.237, abs=0.001)
    assert interval.high == pytest.approx(0.763, abs=0.001)


def test_clopper_pearson_zero_and_full_have_closed_forms():
    """At the extremes the exact interval has an analytic value worth checking directly."""
    n, alpha = 188, DEFAULT_ALPHA_PER_SIDE
    assert clopper_pearson(0, n).low == 0.0
    assert clopper_pearson(0, n).high == pytest.approx(1 - alpha ** (1 / n), abs=1e-6)
    assert clopper_pearson(n, n).high == 1.0
    assert clopper_pearson(n, n).low == pytest.approx(alpha ** (1 / n), abs=1e-6)


# --------------------------------------------------------------------------------------
# Properties that must hold for any estimator here.
# --------------------------------------------------------------------------------------

@pytest.mark.parametrize("estimator", [clopper_pearson, wilson])
@pytest.mark.parametrize("successes", [0, 1, 47, 94, 175, 188])
def test_interval_brackets_its_point_estimate(estimator, successes):
    interval = estimator(successes, 188)
    assert interval.low <= interval.point <= interval.high
    assert 0.0 <= interval.low <= interval.high <= 1.0


@pytest.mark.parametrize("estimator", [clopper_pearson, wilson])
def test_intervals_move_right_as_successes_increase(estimator):
    lows = [estimator(k, 188).low for k in range(0, 189, 8)]
    highs = [estimator(k, 188).high for k in range(0, 189, 8)]
    assert lows == sorted(lows)
    assert highs == sorted(highs)


@pytest.mark.parametrize("estimator", [clopper_pearson, wilson])
def test_more_data_narrows_the_interval(estimator):
    """Same proportion, more observations, tighter bounds. If this fails, n is ignored.

    n=20000 is here on purpose. The first implementation computed the exact tail as
    `math.comb(n, i) * p**i * ...`, which raises OverflowError once the binomial coefficient
    outgrows a float -- around n=2000. It was correct at the 188 it was developed against
    and would have crashed the first time anyone pointed it at a large corpus.
    """
    widths = [estimator(int(0.93 * n), n).width for n in (50, 188, 500, 2000, 20000)]
    assert widths == sorted(widths, reverse=True)


def test_the_default_alpha_is_the_project_alpha():
    """One report must not carry two confidence levels.

    `compare.exact_band` tests at 1/64 per side. Defaulting these to the conventional 95%
    would invite a reader to compare a 95% interval against a 96.875% test.
    """
    from evalharness.compare import exact_band

    assert DEFAULT_ALPHA_PER_SIDE == 1 / 64
    # `alpha_per_side` is keyword-only on exact_band, so it lives in __kwdefaults__.
    assert exact_band.__kwdefaults__["alpha_per_side"] == DEFAULT_ALPHA_PER_SIDE
    assert clopper_pearson(175, 188).confidence == pytest.approx(1 - 2 / 64)


# --------------------------------------------------------------------------------------
# The measured figure this was built for.
# --------------------------------------------------------------------------------------

def test_business_accuracy_interval_is_wider_than_the_gaps_being_argued_about():
    """175/188 = 93.1% is not a precise number, and the interval says so.

    Both arms scored exactly this. The point of quoting an interval beside it is that its
    width (~8 points) dwarfs every per-dimension difference in the comparison, which is the
    same conclusion the paired test reaches by a different route.
    """
    interval = wilson(175, 188)
    assert interval.point == pytest.approx(0.9309, abs=0.0001)
    assert interval.width > 0.07
    # The two arms are identical here, so their intervals are the same interval.
    assert interval.overlaps(wilson(175, 188))


# --------------------------------------------------------------------------------------
# Bootstrap.
# --------------------------------------------------------------------------------------

def test_bootstrap_is_deterministic():
    """An interval that moves between runs of identical data is not a fact about the data."""
    units = [1] * 175 + [0] * 13
    first = bootstrap(units, lambda s: sum(s) / len(s), resamples=2000)
    second = bootstrap(units, lambda s: sum(s) / len(s), resamples=2000)
    assert (first.low, first.high) == (second.low, second.high)


def test_bootstrap_agrees_with_the_closed_form_on_a_proportion():
    """Where an exact interval exists, the bootstrap should land near it.

    Not a tight equality -- the percentile bootstrap is approximate and known to be slightly
    narrow on proportions -- but a bootstrap that disagreed materially would mean the
    resampling is wrong.
    """
    units = [1] * 175 + [0] * 13
    boot = bootstrap(units, lambda s: sum(s) / len(s), resamples=5000)
    closed = wilson(175, 188)
    assert boot.point == pytest.approx(closed.point, abs=1e-9)
    assert boot.low == pytest.approx(closed.low, abs=0.03)
    assert boot.high == pytest.approx(closed.high, abs=0.03)


def test_bootstrap_refuses_an_empty_population():
    with pytest.raises(ValueError, match="nothing to resample"):
        bootstrap([], lambda s: 0.0)


def test_resampling_rows_instead_of_calls_understates_the_interval():
    """The reason `comparison_clusters` is the required unit, demonstrated.

    Three correlated rows per call, all agreeing. Treating them as independent observations
    triples the apparent sample size and narrows the interval by roughly sqrt(3) -- an
    interval that is confidently too tight, which is the worst kind.
    """
    calls = [1] * 60 + [0] * 15                 # 75 independent calls
    rows = [v for v in calls for _ in range(3)]  # the same evidence, counted three times

    mean = lambda s: sum(s) / len(s)            # noqa: E731
    by_call = bootstrap(calls, mean, resamples=4000)
    by_row = bootstrap(rows, mean, resamples=4000)

    assert by_row.point == pytest.approx(by_call.point, abs=1e-9)
    assert by_row.width < by_call.width * 0.75


# --------------------------------------------------------------------------------------
# Guards.
# --------------------------------------------------------------------------------------

@pytest.mark.parametrize("estimator", [clopper_pearson, wilson])
def test_invalid_inputs_refuse(estimator):
    with pytest.raises(ValueError):
        estimator(5, 0)
    with pytest.raises(ValueError):
        estimator(11, 10)
    with pytest.raises(ValueError):
        estimator(-1, 10)


def test_overlap_is_not_a_significance_test():
    """Documented explicitly because reading it as one is the common mistake.

    Two intervals that do not overlap do imply a difference; two that DO overlap imply
    nothing, especially for a paired comparison where overlap discards the pairing.
    """
    a = wilson(175, 188)
    b = wilson(170, 188)
    assert a.overlaps(b)
    assert "not a significance test" in Interval.overlaps.__doc__.lower()
