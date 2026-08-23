"""Tests for the voice pipeline's result-sheet and Matrix-sheet row sets.

The content fixture is built by running a real ``ModelResponse.model_validate(...)`` and dumping
it, rather than hand-writing the dict the function reads -- the same choice
``test_google_documents_output`` makes and for the same reason: a hand-written fixture that has
drifted from the model passes here while every real prediction row fails.

The criterion payload is generated from ``ServiceQuality``'s own field list rather than spelled
out, so a criterion added to the model cannot leave this file asserting on a shape that no longer
exists.
"""

import pandas as pd
import pytest

from src.google_model.sentiment.google_output import build_metrics_df, build_output_df
from src.google_model.sentiment.schema.metrics_schema import MetricsSchema
from src.google_model.sentiment.schema.model_response import ModelResponse, ServiceQuality
from src.google_model.sentiment.schema.output_schema import OutputSchema
from src.google_model.usage_metrics import USAGE_FIELDS

# Every column the sheet carries beyond its two identity columns. Derived, so a schema change
# cannot leave a blank-row assertion checking a stale list.
CRITERION_COLUMNS = list(OutputSchema.to_schema().columns)[2:]


def make_content(**overrides):
    """A dumped ``ModelResponse``, exactly as ``run()`` hands it to ``build_output_df``."""
    payload = {
        "transcript": "agent: hello. customer: hello.",
        "service_number": "0800000000",
        "call_type": ["Enquiry"],
        "call_type_confident": "High",
        "customer_insight": {
            "summary_story": "Customer asked about a bill.",
            "product_category": "Mobile",
            "repeat_call": "New Call",
            "fcr": True,
            "churn_probability": "Low",
            "churn_reason": "Other",
            "customer_insight_summary": "Routine enquiry.",
            "standard_gsd_name": "Billing",
        },
        "service_quality": {
            # "Meet" is valid for both CriterionEvaluation and BinaryCriterionEvaluation, and
            # service_quality_performance_insight is the one bare str among them.
            name: "Insight."
            if name == "service_quality_performance_insight"
            else {"evaluation": "Meet", "reason": "Did the thing."}
            for name in ServiceQuality.model_fields
        },
        "sale_opportunity": {
            "opportunity_recognition_in_conversation": False,
            "product_suggested_by_ai": "",
            "agent_offer_product_presentation_and_explanation": False,
            "product_offer_by_agent": "",
            "sales_outcome_and_customer_decision": False,
            "sales_opportunities_performance_insight": "None.",
        },
        "customer_sentiment": {
            "overall_sentiment": "Neutral",
            "initial_sentiment": "Neutral",
            "final_sentiment": "Positive",
            "primary_sentiment_driver": "Agent Behavior and Communication",
            "csat": "4",
            "cs_performance_insight": "Fine.",
        },
        "customer_experience": {
            "agent_communication_and_attitude": "Good",
            "agent_communication_and_attitude_reason": "Polite.",
            "agent_understanding_and_resolution": "Good",
            "agent_understanding_and_resolution_reason": "Resolved.",
            "agent_responsiveness": "Good",
            "agent_responsiveness_reason": "Prompt.",
            "system_accessibility": True,
            "system_accessibility_reason": "Reachable.",
            "ivr_usability_and_design": True,
            "ivr_usability_and_design_reason": "Clear.",
            "ces": "2",
            "self_service_readiness": "High",
            "cx_performance_insight": "Fine.",
        },
        "network": {
            "issue_type": None,
            "problem_statement": [],
            "area_tag_province": None,
            "area_tag_district": None,
            "area_tag_sub_district": None,
            "area_tag_landmark": None,
        },
    }
    payload.update(overrides)
    return ModelResponse.model_validate(payload).model_dump(mode="json")


def make_item(file_name="a_IN.wav", **overrides):
    return {"file_name": file_name, "content": make_content(**overrides)}


def make_usage_row(file_name="a_IN.wav", status="Success", error_type="", **overrides):
    """One ``usage_rows`` record, as ``run()`` assembles it after the item loop."""
    return {
        "file_name": file_name,
        "status": status,
        "error_type": error_type,
        **dict.fromkeys(USAGE_FIELDS),
        **overrides,
    }


class TestRowParity:
    """Every submitted file reaches the sheet, whether or not the model returned anything.

    The scorers join this sheet to the ground truth with ``how="inner"`` and nothing downstream
    checks row counts, so a dropped file does not misalign anything -- it quietly reduces N on
    every metric while the dashboard still reads as a complete score. These are the tests that
    keep "300 files in" producing "300 rows out".
    """

    def test_a_file_that_never_parsed_still_gets_a_row(self):
        df = build_output_df([make_item("a_IN.wav")], ["a_IN.wav", "b_OUT.wav"])

        assert len(df) == 2
        assert list(df["Voice File Name"]) == ["a_IN.wav", "b_OUT.wav"]

    def test_the_failed_row_is_blank_rather_than_carrying_a_sentinel(self):
        df = build_output_df([make_item("a_IN.wav")], ["a_IN.wav", "b_OUT.wav"])
        blank = df[df["Voice File Name"] == "b_OUT.wav"]

        # Blank, never "N/A" or "ERROR": the scorers compare raw cell text, and "N/A" is a real
        # grader-entered value in this sheet -- a sentinel would be scored as a prediction.
        assert blank[CRITERION_COLUMNS].isna().all().all()
        # The parsed row beside it is untouched.
        assert df.loc[0, "greeting_standard"] == "Meet"

    def test_no_parsed_items_yields_a_full_sheet_not_an_empty_one(self):
        submitted = [f"call_{n}.wav" for n in range(1, 11)]
        df = build_output_df([], submitted)

        # The case the whole change exists to make visible: a run that returned nothing usable
        # used to ship a 0-row sheet, which scored as "nothing to compare" rather than "all bad".
        assert len(df) == 10
        assert list(df["No"]) == list(range(1, 11))
        assert df[CRITERION_COLUMNS].isna().all().all()

    def test_three_hundred_submitted_is_three_hundred_rows(self):
        submitted = [f"call_{n:03d}.wav" for n in range(300)]
        # 297 parsed, 3 lost -- the shape of the run that prompted this.
        items = [make_item(name) for name in submitted[:297]]

        df = build_output_df(items, submitted)

        assert len(df) == 300
        assert df["Voice File Name"].is_unique
        assert df[CRITERION_COLUMNS].isna().all(axis=1).sum() == 3

    def test_numbering_is_contiguous_and_sorted_by_name(self):
        df = build_output_df([make_item("b.wav")], ["c.wav", "a.wav", "b.wav"])

        assert list(df["No"]) == [1, 2, 3]
        # Sorted on the *submitted* list, so the numbering is stable between runs even as the
        # failure set changes.
        assert list(df["Voice File Name"]) == ["a.wav", "b.wav", "c.wav"]

    def test_a_parsed_item_that_was_never_submitted_is_appended_not_dropped(self):
        df = build_output_df([make_item("stale.wav")], ["a.wav"])

        # An unexplained row is a fault to look at; silently discarding one is how this code
        # lost the failures in the first place.
        assert len(df) == 2
        assert list(df["Voice File Name"]) == ["a.wav", "stale.wav"]

    def test_duplicate_submitted_names_collapse_to_one_row(self):
        df = build_output_df([], ["a.wav", "a.wav"])

        assert len(df) == 1

    def test_columns_are_the_schema_in_declaration_order(self):
        df = build_output_df([make_item()], ["a_IN.wav"])

        assert list(df.columns) == list(OutputSchema.to_schema().columns)

    def test_an_empty_run_still_yields_a_valid_zero_row_frame(self):
        df = build_output_df([], [])

        assert len(df) == 0
        assert list(df.columns) == list(OutputSchema.to_schema().columns)


class TestBuildMetricsDf:
    def test_matrix_covers_every_submitted_file(self):
        submitted = ["a.wav", "b.wav", "c.wav"]
        df = build_metrics_df([make_usage_row("b.wav")], submitted)

        assert len(df) == 3
        assert list(df["Voice File Name"]) == submitted
        assert list(df["Status"]) == ["Failed", "Success", "Failed"]

    def test_a_file_that_never_returned_has_blank_tokens_not_zero(self):
        df = build_metrics_df([], ["a.wav"])

        # Blank, not 0: a 0 would be summed into the run's totals as if the file had genuinely
        # cost nothing, and summarize_usage counts notna() to find what was actually reported.
        assert df["Total Tokens"].isna().all()
        assert df["Prompt Tokens"].isna().all()

    def test_a_file_that_never_returned_has_no_error_type(self):
        df = build_metrics_df([], ["a.wav"])

        # Blank rather than an exception name -- nothing came back to fail. That is what keeps
        # the two causes legible without a third status value.
        assert df.loc[0, "Error Type"] == ""

    def test_a_failed_row_keeps_the_tokens_it_burned(self):
        df = build_metrics_df(
            [
                make_usage_row(
                    "a.wav",
                    status="Failed",
                    error_type="ValidationError",
                    prompt_tokens=50757,
                    total_tokens=60360,
                )
            ],
            ["a.wav"],
        )

        # A malformed model response is the common failure, and those tokens are real spend.
        assert df.loc[0, "Status"] == "Failed"
        assert df.loc[0, "Error Type"] == "ValidationError"
        assert df.loc[0, "Total Tokens"] == 60360

    def test_token_columns_are_nullable_integers(self):
        df = build_metrics_df([make_usage_row(prompt_tokens=10)], ["a_IN.wav"])

        # Int64, not float: a float column renders 50757.0 in the sheet, and a plain int column
        # would fail validation the moment a counter came back None.
        assert df["Prompt Tokens"].dtype == "Int64"
        assert pd.isna(df.loc[0, "Total Tokens"])

    def test_matches_the_output_sheet_row_for_row(self):
        submitted = ["a.wav", "b.wav", "c.wav"]
        output = build_output_df([make_item("a.wav")], submitted)
        metrics = build_metrics_df([make_usage_row("a.wav")], submitted)

        # Both seeded from the same submitted list, so the two sheets read side by side. They
        # still join on the file name, never on No -- the rule the docstring states.
        assert list(output["Voice File Name"]) == list(metrics["Voice File Name"])

    def test_empty_input_yields_a_valid_zero_row_frame(self):
        df = build_metrics_df([], [])

        assert len(df) == 0
        assert list(df.columns) == list(MetricsSchema.to_schema().columns)

    @pytest.mark.parametrize(
        "column",
        ["Prompt Audio Tokens", "Prompt Text Tokens", "Prompt Modalities", "Traffic Type"],
        ids=lambda column: column.replace(" ", "_"),
    )
    def test_carries_the_column(self, column):
        assert column in list(build_metrics_df([], ["a.wav"]).columns)
