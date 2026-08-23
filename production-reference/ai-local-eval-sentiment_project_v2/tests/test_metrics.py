"""Tests for the scoring primitives.

Every expected value here is hand-computed from a small, fully-enumerated example. That is the
point: these functions are what the migration decision rests on, so a test that merely re-derives
them with the same code proves nothing.
"""

import math

import pytest

from src.google_model.metrics import (
    character_error_rate,
    classification_report,
    confusion_matrix,
    cosine_similarity,
    error_rate_stats,
    match_report,
    mean_pool,
    multilabel_report,
    similarity_stats,
)
from src.google_model.sentiment.google_confusion_matrix import (
    BINARY_LABELS,
    CALL_TYPE_LABELS,
    CRITERION_LABELS,
    SENTIMENT_LABELS,
)

TERNARY = ("Meet", "Below", "N/A")


def score_for(report, label):
    """Pull one label's scores out of a report."""
    return next(s for s in report.per_label if s.label == label)


class TestConfusionMatrix:
    def test_counts_every_cell_including_zeros(self):
        matrix = confusion_matrix(["Meet", "Below"], ["Meet", "Meet"], TERNARY)

        assert matrix["Meet"]["Meet"] == 1
        assert matrix["Below"]["Meet"] == 1
        # A label absent from the data still gets a full row and column of zeros, which is what
        # keeps the report's shape stable across runs.
        assert matrix["N/A"] == {"Meet": 0, "Below": 0, "N/A": 0}

    def test_rejects_mismatched_lengths(self):
        with pytest.raises(ValueError, match="same length"):
            confusion_matrix(["Meet"], ["Meet", "Below"], TERNARY)

    def test_rejects_value_outside_label_set(self):
        with pytest.raises(ValueError, match="outside the label set"):
            confusion_matrix(["Meet"], ["Excellent"], TERNARY)


class TestClassificationReport:
    def test_na_is_scored_as_a_class_not_treated_as_missing(self):
        # 8 rows: N/A dominates, and the model answers N/A on everything.
        y_true = ["N/A"] * 6 + ["Meet", "Below"]
        y_pred = ["N/A"] * 8

        report = classification_report(y_true, y_pred, TERNARY)

        # N/A is a graded answer, so those 6 rows count as correct.
        assert report.accuracy == pytest.approx(6 / 8)

        na = score_for(report, "N/A")
        assert na.support == 6
        assert na.tp == 6
        assert na.fp == 2

        # ...and the macro-F1 is what exposes that nothing else was learned: Meet and Below both
        # score 0.0, so the average is one third of N/A's F1 alone.
        na_f1 = 2 * (6 / 8) * 1.0 / ((6 / 8) + 1.0)
        assert report.macro_f1 == pytest.approx(na_f1 / 3)
        assert report.macro_f1 < report.accuracy

    def test_one_vs_rest_counts_are_internally_consistent(self):
        y_true = ["Meet", "Meet", "Below", "N/A", "N/A", "Below"]
        y_pred = ["Meet", "Below", "Below", "N/A", "Meet", "Meet"]

        report = classification_report(y_true, y_pred, TERNARY)

        for score in report.per_label:
            assert score.tp + score.tn + score.fp + score.fn == report.n
            assert score.support == score.tp + score.fn
            expected_precision = (
                score.tp / (score.tp + score.fp) if (score.tp + score.fp) else 0.0
            )
            expected_recall = score.tp / (score.tp + score.fn) if (score.tp + score.fn) else 0.0
            assert score.precision == pytest.approx(expected_precision)
            assert score.recall == pytest.approx(expected_recall)

        meet = score_for(report, "Meet")
        assert (meet.tp, meet.fp, meet.fn, meet.tn) == (1, 2, 1, 2)

    def test_binary_label_set_excludes_na(self):
        report = classification_report(
            ["Meet", "Below"], ["Meet", "Meet"], ("Meet", "Below")
        )

        assert report.labels == ("Meet", "Below")
        assert [s.label for s in report.per_label] == ["Meet", "Below"]
        # Two classes, not three -- scoring a binary criterion against the ternary set would add
        # a zero-support class and cut its macro-F1 by a third.
        assert report.macro_f1 == pytest.approx(
            sum(s.f1 for s in report.per_label) / 2
        )

    def test_labels_are_not_inferred_from_the_data(self):
        # A class that never occurs still gets its own per-label row, so a column never silently
        # stops reporting a grade the model ought to be using. It leaves the macro-F1 denominator
        # alone -- that is macro_labels' job, covered by TestMacroF1Denominator.
        report = classification_report(["Meet", "N/A"], ["Meet", "N/A"], TERNARY)

        assert report.labels == TERNARY
        assert len(report.per_label) == 3
        assert score_for(report, "Below").support == 0

    def test_empty_input_yields_zeros_rather_than_raising(self):
        report = classification_report([], [], TERNARY)

        assert report.n == 0
        assert report.accuracy == 0.0
        assert report.macro_f1 == 0.0
        assert report.macro_labels == ()

    def test_rejects_empty_label_set(self):
        with pytest.raises(ValueError, match="labels must not be empty"):
            classification_report(["Meet"], ["Meet"], ())


class TestMacroF1Denominator:
    """A perfect prediction must score 1.0000, and inventing a grade must still cost something.

    The first was found by feeding the same sheet in as both ground truth and result: every cell
    should have read 1.0000 and two columns read 0.6667, because the macro average divided by the
    whole label set and counted an unused class as an F1 of 0.
    """

    @pytest.mark.parametrize(
        "labels",
        [
            CRITERION_LABELS,
            BINARY_LABELS,
            SENTIMENT_LABELS,
            CALL_TYPE_LABELS,
        ],
        ids=lambda labels: f"{len(labels)}-class",
    )
    @pytest.mark.parametrize("used", [1, 2, None], ids=["one-class", "two-classes", "all-classes"])
    def test_a_perfect_prediction_scores_one(self, labels, used):
        # Every label set the dashboard uses, against inputs exercising only part of it -- the
        # subset case is where the old formula broke, and greeting_standard on the live sheet is
        # exactly this shape: three allowed grades, only two ever used.
        y_true = list(labels[:used])

        report = classification_report(y_true, list(y_true), labels)

        assert report.accuracy == 1.0
        assert report.macro_f1 == 1.0
        assert set(report.macro_labels) == set(y_true)

    def test_a_class_neither_side_used_is_left_out_of_the_average(self):
        # greeting_standard on the live run: no row is graded N/A and the model never says N/A.
        # Meet scores P 2/3, R 1.0 -> F1 0.8; Below is missed entirely -> F1 0.0. The average is
        # over those two, 0.4, not (0.8 + 0.0 + 0.0) / 3 = 0.2667.
        report = classification_report(
            ["Meet", "Meet", "Below"], ["Meet", "Meet", "Meet"], TERNARY
        )

        assert score_for(report, "Meet").f1 == pytest.approx(0.8)
        assert score_for(report, "Below").f1 == 0.0
        assert report.macro_labels == ("Meet", "Below")
        assert report.macro_f1 == pytest.approx(0.4)

    def test_a_class_the_model_invented_is_kept_and_scores_zero(self):
        # beyond_scope_support on the live run: Below has Support 0 but FP 2. The human never used
        # that grade and the model produced it anyway, which is a real error.
        #
        # Testing support == 0 alone -- the obvious simplification -- would drop this class and
        # hand the model a free pass. That is what this assertion exists to stop.
        report = classification_report(["Meet", "Meet"], ["Meet", "Below"], TERNARY)

        below = score_for(report, "Below")
        assert below.support == 0
        assert below.fp == 1
        assert below.f1 == 0.0
        assert "Below" in report.macro_labels
        assert "N/A" not in report.macro_labels
        # Meet F1 = 2*1.0*0.5/1.5 = 2/3, averaged with Below's 0.0 over the two classes present.
        assert report.macro_f1 == pytest.approx(1 / 3)

    def test_all_classes_present_averages_over_the_full_label_set(self):
        # The regression guard: where every class occurs, the new denominator equals the old one.
        report = classification_report(
            ["Meet", "Below", "N/A"], ["Meet", "Below", "Meet"], TERNARY
        )

        assert report.macro_labels == TERNARY
        assert report.macro_f1 == pytest.approx(
            sum(s.f1 for s in report.per_label) / 3
        )

    def test_multilabel_perfect_prediction_scores_one(self):
        # Same fault, same fix, in the other report builder -- Sale and Retention unused here.
        y_true = [{"Enquiry"}, {"Enquiry", "Complaint"}]

        report = multilabel_report(y_true, list(y_true), CALL_TYPE_LABELS)

        assert report.exact_match == 1.0
        assert report.micro_f1 == 1.0
        assert report.macro_f1 == 1.0
        assert set(report.macro_labels) == {"Enquiry", "Complaint"}

    def test_multilabel_keeps_a_label_the_model_invented(self):
        report = multilabel_report(
            [{"Enquiry"}], [{"Enquiry", "Sale"}], CALL_TYPE_LABELS
        )

        assert score_for(report, "Sale").support == 0
        assert "Sale" in report.macro_labels
        assert "Retention" not in report.macro_labels
        # Enquiry F1 1.0 and Sale F1 0.0 over the two labels present.
        assert report.macro_f1 == pytest.approx(0.5)

    def test_multilabel_empty_input_yields_no_macro_labels(self):
        report = multilabel_report([], [], CALL_TYPE_LABELS)

        assert report.macro_f1 == 0.0
        assert report.macro_labels == ()


CALL_TYPES = ("Enquiry", "Service Request", "Complaint", "Sale", "Retention")


class TestMultiLabelReport:
    def test_exact_match_ignores_order(self):
        report = multilabel_report(
            [{"Enquiry", "Service Request"}],
            [{"Service Request", "Enquiry"}],
            CALL_TYPES,
        )

        assert report.exact_match == 1.0
        assert report.micro_f1 == 1.0

    def test_partial_row_fails_exact_match_but_scores_micro_f1(self):
        report = multilabel_report(
            [{"Enquiry", "Service Request"}],
            [{"Enquiry"}],
            CALL_TYPES,
        )

        assert report.exact_match == 0.0
        # 1 TP, 0 FP, 1 FN -> precision 1.0, recall 0.5, F1 0.667.
        assert report.micro_precision == pytest.approx(1.0)
        assert report.micro_recall == pytest.approx(0.5)
        assert report.micro_f1 == pytest.approx(2 / 3)

    def test_per_label_counts_are_internally_consistent(self):
        y_true = [{"Enquiry"}, {"Service Request", "Complaint"}, set()]
        y_pred = [{"Enquiry", "Sale"}, {"Service Request"}, {"Retention"}]

        report = multilabel_report(y_true, y_pred, CALL_TYPES)

        for score in report.per_label:
            assert score.tp + score.tn + score.fp + score.fn == report.n
            assert score.support == score.tp + score.fn

        assert score_for(report, "Sale").fp == 1
        assert score_for(report, "Complaint").fn == 1

    def test_rejects_value_outside_label_set(self):
        with pytest.raises(ValueError, match="outside the label set"):
            multilabel_report([{"Downsell"}], [{"Enquiry"}], CALL_TYPES)


class TestMatchReport:
    """The exact-match framing google_exact_match.py reports.

    Every expectation here is a count off a five-row example, not a re-derivation. The identity
    tests matter most: three of the nine numbers this produces are determined by the fourth, and
    an "improvement" that quietly broke one of them would still look plausible on the sheet.
    """

    def test_counts_agreements_and_disagreements(self):
        report = match_report(
            ["meet", "meet", "below", "n/a", "meet"],
            ["meet", "below", "below", "meet", "meet"],
        )

        assert report.n == 5
        assert report.tp == 3
        assert report.fp == 2
        assert report.accuracy == pytest.approx(0.6)

    def test_a_perfect_prediction_scores_one_on_all_four(self):
        values = ["meet", "below", "n/a", "meet"]

        report = match_report(values, list(values))

        assert report.tp == report.n == 4
        assert report.fp == 0
        assert report.accuracy == 1.0
        assert report.precision == 1.0
        assert report.recall == 1.0
        assert report.f1 == 1.0

    def test_total_disagreement_scores_zero_without_dividing_by_zero(self):
        report = match_report(["meet", "meet"], ["below", "n/a"])

        assert (report.tp, report.fp) == (0, 2)
        assert report.accuracy == 0.0
        assert report.precision == 0.0
        assert report.f1 == 0.0
        # Recall stays 1.0 even here: fn is 0, so TP/(TP+FN) is 0/0, and the convention is the
        # same one _prf uses. It is the clearest demonstration that the number means nothing.
        assert report.recall == 1.0

    def test_negatives_are_structurally_zero(self):
        report = match_report(["meet", "below"], ["below", "below"])

        # Not an omission: with the grade discarded, a disagreement has no direction, so there is
        # no missed case and no correct rejection to count.
        assert report.fn == 0
        assert report.tn == 0

    @pytest.mark.parametrize("matches", range(6))
    def test_the_three_identities_hold_at_every_match_count(self, matches):
        y_true = ["meet"] * 5
        y_pred = ["meet"] * matches + ["below"] * (5 - matches)

        report = match_report(y_true, y_pred)
        accuracy = matches / 5

        # These three are what the legend promises the reader, so they are pinned rather than
        # left to the implementation. Precision equals Accuracy; Recall is 1.0 whatever happened;
        # F1 is a fixed function of Accuracy and is never below it.
        assert report.precision == pytest.approx(accuracy)
        assert report.recall == 1.0
        assert report.f1 == pytest.approx(2 * accuracy / (1 + accuracy))
        assert report.f1 >= report.accuracy

    def test_comparison_is_exact_with_no_normalisation(self):
        # Case folding belongs to the caller -- google_exact_match._normalise -- because it is a
        # fact about one workbook's data entry, not about the arithmetic.
        assert match_report(["meet"], ["Meet"]).tp == 0
        assert match_report([" meet"], ["meet"]).tp == 0

    def test_empty_input_gives_zeros_rather_than_raising(self):
        report = match_report([], [])

        assert report.n == 0
        assert (report.tp, report.fp, report.fn, report.tn) == (0, 0, 0, 0)
        assert report.accuracy == 0.0
        assert report.precision == 0.0
        # 0.0, not 1.0: nothing was compared, so claiming perfect recall would put a green cell
        # on a column that was never scored.
        assert report.recall == 0.0
        assert report.f1 == 0.0

    def test_mismatched_lengths_raise(self):
        with pytest.raises(ValueError):
            match_report(["meet", "below"], ["meet"])

    def test_reproduces_the_reference_workbook_totals(self):
        """The anchor: 108 matched of 152 is Confusion_Matrix_R119 row 28, Greeting Standard.

        Hand-built from that sheet's own counts rather than read off the file, so the assertion
        survives debug/ being cleaned out. The end-to-end check against the real workbook lives in
        the plan's verification section.
        """
        report = match_report(["meet"] * 152, ["meet"] * 108 + ["below"] * 44)

        assert (report.n, report.tp, report.fp) == (152, 108, 44)
        assert round(report.accuracy, 4) == 0.7105
        assert round(report.f1, 4) == 0.8308


class TestCosineSimilarity:
    def test_identical_vectors_score_one_and_never_exceed_it(self):
        score = cosine_similarity([0.3, 0.7, 0.1], [0.3, 0.7, 0.1])

        assert score == pytest.approx(1.0)
        # The clip is what guarantees the upper bound: floating-point error can otherwise land
        # marginally above 1.0, and a similarity column reading 1.0000000000000002 invites a bug
        # report about the maths.
        assert score <= 1.0

    def test_orthogonal_vectors_score_zero(self):
        assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_zero_vector_scores_zero_rather_than_raising(self):
        assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0

    def test_rejects_mismatched_dimensions(self):
        with pytest.raises(ValueError, match="same length"):
            cosine_similarity([1.0, 0.0], [1.0, 0.0, 0.0])


class TestSimilarityStats:
    def test_reports_the_tail_not_just_the_mean(self):
        report = similarity_stats([0.95, 0.90, 0.85, 0.41], threshold=0.80)

        assert report.mean == pytest.approx(0.7775)
        assert report.median == pytest.approx(0.875)
        assert report.minimum == 0.41
        assert report.maximum == 0.95
        assert report.pass_rate == pytest.approx(0.75)

    def test_threshold_is_inclusive(self):
        assert similarity_stats([0.80], threshold=0.80).pass_rate == 1.0

    def test_empty_input_yields_zeros(self):
        report = similarity_stats([], threshold=0.80)

        assert report.n == 0
        assert report.mean == 0.0
        assert report.pass_rate == 0.0


class TestMeanPool:
    def test_two_orthogonal_unit_vectors_pool_to_their_bisector(self):
        pooled = mean_pool([[1.0, 0.0], [0.0, 1.0]])

        assert pooled == pytest.approx([0.5, 0.5])
        # And the bisector is 45 degrees from each input, which is what a pooled vector should be.
        assert cosine_similarity(pooled, [1.0, 0.0]) == pytest.approx(math.sqrt(0.5))

    def test_weights_shift_the_pool_toward_the_longer_chunk(self):
        even = mean_pool([[1.0, 0.0], [0.0, 1.0]])
        weighted = mean_pool([[1.0, 0.0], [0.0, 1.0]], weights=[3.0, 1.0])

        assert weighted == pytest.approx([0.75, 0.25])
        # A 2000-character chunk must not count the same as a 40-character closing line.
        assert weighted[0] > even[0]

    def test_magnitude_does_not_dominate_because_vectors_are_normalised_first(self):
        # Without the pre-normalisation the second vector, 100x longer, would swamp the first and
        # the pool would sit almost exactly on it.
        pooled = mean_pool([[1.0, 0.0], [0.0, 100.0]])

        assert pooled == pytest.approx([0.5, 0.5])

    def test_single_vector_pools_to_its_own_direction(self):
        assert mean_pool([[3.0, 4.0]]) == pytest.approx([0.6, 0.8])

    def test_zero_vector_contributes_nothing_rather_than_nan(self):
        pooled = mean_pool([[0.0, 0.0], [1.0, 0.0]])

        # Dividing by a zero norm would yield NaN and poison every dimension of the result.
        assert pooled == pytest.approx([0.5, 0.0])

    def test_empty_input_raises(self):
        with pytest.raises(ValueError, match="at least one vector"):
            mean_pool([])

    def test_ragged_dimensions_raise(self):
        with pytest.raises(ValueError, match="same length"):
            mean_pool([[1.0, 0.0], [1.0]])

    def test_weight_count_must_match(self):
        with pytest.raises(ValueError, match="weights for"):
            mean_pool([[1.0, 0.0], [0.0, 1.0]], weights=[1.0])

    def test_weights_summing_to_zero_raise(self):
        with pytest.raises(ValueError, match="positive value"):
            mean_pool([[1.0, 0.0], [0.0, 1.0]], weights=[0.0, 0.0])


class TestCharacterErrorRate:
    def test_identical_text_scores_zero(self):
        assert character_error_rate("สวัสดีค่ะ", "สวัสดีค่ะ") == 0.0

    def test_one_substitution_over_a_known_length(self):
        # 5 reference characters, 1 substituted -> 1/5.
        assert character_error_rate("abcde", "abcdX") == pytest.approx(0.2)

    def test_deletions_and_insertions_both_count(self):
        assert character_error_rate("abcd", "abc") == pytest.approx(0.25)
        assert character_error_rate("abcd", "abcde") == pytest.approx(0.25)

    def test_both_empty_is_a_match_not_a_crash(self):
        # jiwer raises on an empty reference -- it is the denominator.
        assert character_error_rate("", "") == 0.0

    def test_content_against_an_empty_reference_is_wholly_wrong(self):
        assert character_error_rate("", "unexpected output") == 1.0

    def test_can_exceed_one_when_the_hypothesis_is_far_longer(self):
        # Not clipped: a run that hallucinated two extra minutes of dialogue is worse than one
        # that merely got every character wrong, and the number must be able to say so.
        assert character_error_rate("ab", "ab" + "x" * 10) > 1.0


class TestErrorRateStats:
    def test_pass_rate_counts_rows_at_or_below_the_threshold(self):
        report = error_rate_stats([0.10, 0.20, 0.30], threshold=0.20)

        # The inversion: two of three pass, because for CER a *smaller* number is the good one.
        assert report.pass_rate == pytest.approx(2 / 3)

    def test_best_is_the_lowest_and_worst_is_the_highest(self):
        report = error_rate_stats([0.10, 0.20, 0.30], threshold=0.20)

        # Reusing SimilarityReport would have mapped these onto minimum/maximum and reported the
        # worst transcript in the run as the best one.
        assert report.best == 0.10
        assert report.worst == 0.30
        assert report.mean == pytest.approx(0.20)
        assert report.median == pytest.approx(0.20)
        assert report.n == 3

    def test_threshold_is_inclusive(self):
        assert error_rate_stats([0.20], threshold=0.20).pass_rate == 1.0

    def test_empty_input_yields_zeros(self):
        report = error_rate_stats([], threshold=0.20)

        assert report.n == 0
        assert report.mean == 0.0
        assert report.pass_rate == 0.0
