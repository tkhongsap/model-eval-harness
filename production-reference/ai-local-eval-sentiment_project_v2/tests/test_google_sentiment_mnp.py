"""Tests for the MNP evaluation pipeline: schema, output frame, and both scorers.

The scoring path is pure -- no SharePoint, no Vertex -- so every test below runs offline
against fabricated frames. The transcript block is the one part that needs an embedder, and
that is injected as a stub; without one, ``evaluate`` omits the block entirely.

The schema here is the **full** production contract, not the trimmed one an earlier revision
scored with: the transcript, the reasoning, the per-reason Thai keyword evidence, the event,
the recommendation and the network block. Only four of its fields reach the sheet, and the
tests below hold that line in both directions -- the wide response must decode, and the narrow
sheet must not widen.
"""

from typing import get_args

import pandas as pd
import pytest
from pydantic import ValidationError

from src.google_model.metrics import LabelScore
from src.google_model.reason_classes import class_label
from src.google_model.sentiment_mnp import google_confusion_matrix as cm
from src.google_model.sentiment_mnp.google_output import build_output_df
from src.google_model.sentiment_mnp.schema.model_response import (
    TRANSCRIPT_DESCRIPTION,
    ModelResponse,
    ReasonCategory,
    SchemaHelper,
)
from src.google_model.sentiment_mnp.schema.output_schema import OutputSchema

GT_COLUMNS = [
    "No",
    "Voice File Name",
    "call_sumary_call_result",
    "reason_main",
    "reason_secondary",
    "reason_third",
]


# A network block with no issue: the shape every call that never mentions the network takes.
NO_NETWORK = {
    "issue_type": None,
    "sub_reason": None,
    "problem_statement_list": None,
    "churn_probability": None,
    "area_tag_province": None,
    "area_tag_district": None,
    "area_tag_sub_district": None,
    "area_tag_landmark": None,
    "product_type": None,
}


def rank(reason, keyword="phrase"):
    """One ranked reason as the model returns it, or None for an absent rank."""
    return None if reason is None else {"reason": reason, "keyword": keyword}


def payload(main, secondary=None, third=None, call_result="churn", **overrides):
    """A full model answer, ready to validate. Overrides replace any top-level field."""
    return {
        "transcript": "Agent: hello",
        "reasoning": "step by step",
        "reasons": {"main": rank(main), "secondary": rank(secondary), "third": rank(third)},
        "call_result": call_result,
        "call_event_detection": None,
        "ai_recommendation": None,
        "additional_fields": NO_NETWORK,
        **overrides,
    }


def response(main, secondary=None, third=None, call_result="churn"):
    """A validated ModelResponse dumped the way build_output_df receives it."""
    return ModelResponse.model_validate(
        payload(main, secondary, third, call_result)
    ).model_dump(mode="json")


def frame(rows):
    """A sheet-shaped frame from ``(name, outcome, main, secondary, third)`` tuples."""
    return pd.DataFrame(
        [
            {
                "No": index,
                "Voice File Name": name,
                "call_sumary_call_result": outcome,
                "reason_main": main,
                "reason_secondary": secondary,
                "reason_third": third,
            }
            for index, (name, outcome, main, secondary, third) in enumerate(rows, start=1)
        ],
        columns=GT_COLUMNS,
    )


# --- schema -----------------------------------------------------------------------------


FULL_FIELDS = [
    "transcript",
    "reasoning",
    "reasons",
    "call_result",
    "call_event_detection",
    "ai_recommendation",
    "additional_fields",
]


def test_vertex_schema_renders_without_unsupported_fields():
    """raise_error_on_unsupported_field=True, so this fails loudly on a bad field type.

    The nullable network block and its ``list[str] | None`` are the fields most likely to trip
    the OpenAPI-subset conversion, which is why the whole schema is checked here rather than
    only the four the sheet scores.
    """
    assert SchemaHelper.vertex_schema().required == FULL_FIELDS


def test_every_property_is_required():
    """OpenAI strict mode demands it, and a default would silently drop a field.

    Checked on the nested objects too: the restored schema reintroduced two of them, and a
    ``default_factory`` on ``problem_statement_list`` is exactly what the old file had.
    """
    json_schema = ModelResponse.model_json_schema()
    assert set(json_schema["required"]) == set(FULL_FIELDS)
    for name, definition in json_schema["$defs"].items():
        if definition.get("type") == "object":
            assert set(definition["required"]) == set(definition["properties"]), name


def test_field_order_is_the_prompt_order():
    """Generation follows properties order: the transcript is written before anything is judged.

    Then the prompt's own numbering -- the reasoning, the ranked reasons, the outcome
    conditioned on them, and last the advisory fields.
    """
    assert list(ModelResponse.model_json_schema()["properties"]) == FULL_FIELDS


def test_the_production_fields_are_present():
    """The inverse of the old trim test: production issues all of these, so this must too."""
    properties = ModelResponse.model_json_schema()["properties"]
    for restored in ("reasoning", "ai_recommendation", "call_event_detection", "additional_fields"):
        assert restored in properties


def test_transcript_spec_matches_the_qa_pipeline_word_for_word():
    """Three pipelines ask for a transcript; a divergent spec makes their CER incomparable."""
    from src.google_model.sentiment.schema.model_response import (
        TRANSCRIPT_DESCRIPTION as QA_SPEC,
    )

    assert TRANSCRIPT_DESCRIPTION == QA_SPEC


def test_problem_statement_is_computed_not_generated():
    """The old schema paid for a field its own validator immediately overwrote.

    It must be absent from the grammar handed to the model and present in every dump.
    """
    block = ModelResponse.model_json_schema()["$defs"]["AdditionalFields"]
    assert "problem_statement" not in block["properties"]
    parsed = ModelResponse.model_validate(
        payload(
            "network",
            additional_fields={
                **NO_NETWORK,
                "issue_type": "Speed",
                "sub_reason": "slow in the evening",
                "problem_statement_list": ["one", "two"],
                "churn_probability": 60,
                "product_type": "Postpaid",
            },
        )
    )
    assert parsed.model_dump()["additional_fields"]["problem_statement"] == "one, two"


def test_no_issue_type_nulls_the_whole_network_block():
    """The prompt's activation rule, enforced in the one direction it actually specifies."""
    parsed = ModelResponse.model_validate(
        payload(
            "network",
            additional_fields={
                **NO_NETWORK,
                "sub_reason": "left over from a previous draft",
                "problem_statement_list": ["one"],
                "churn_probability": 90,
                "area_tag_province": "Chiang Mai",
                "product_type": "Postpaid",
            },
        )
    )
    block = parsed.additional_fields
    assert block.issue_type is None
    assert (block.sub_reason, block.problem_statement_list, block.churn_probability) == (
        None,
        None,
        None,
    )
    assert (block.area_tag_province, block.product_type) == (None, None)


@pytest.mark.parametrize(
    ("secondary", "third", "expected"),
    [
        ("network", "network", (None, None)),  # both repeat main
        ("save cost", "save cost", ("save cost", None)),  # third repeats secondary
        ("save cost", "network", ("save cost", None)),  # third repeats main
        ("save cost", "other", ("save cost", "other")),  # all distinct: untouched
        (None, "save cost", (None, "save cost")),  # a gap does not promote the third
    ],
)
def test_duplicate_reasons_collapse(secondary, third, expected):
    """A rank repeating a higher one is nulled; the ground truth never repeats a category."""
    parsed = ModelResponse.model_validate(payload("network", secondary, third))
    actual = (
        parsed.reasons.secondary.reason if parsed.reasons.secondary else None,
        parsed.reasons.third.reason if parsed.reasons.third else None,
    )
    assert actual == expected


def test_a_collapsed_rank_hands_its_keyword_to_the_survivor():
    """The phrase is real evidence for a category the model did choose, so it is kept.

    Discarding it would lose the customer's own words on a call where the model merely ranked
    one reason twice -- and the keyword is the only place that evidence exists.
    """
    parsed = ModelResponse.model_validate(
        payload("network")
        | {
            "reasons": {
                "main": rank("network", "first"),
                "secondary": rank("network", "second"),
                "third": None,
            }
        }
    )
    assert parsed.reasons.main.keyword == "first, second"
    assert parsed.reasons.secondary is None


def test_out_of_vocabulary_reason_is_rejected():
    """The schema is the decoding grammar; an invented category must not parse."""
    with pytest.raises(ValidationError):
        ModelResponse.model_validate(payload("not a category"))


# --- build_output_df --------------------------------------------------------------------


def test_output_frame_maps_every_column():
    df = build_output_df([{"file_name": "a.wav", "content": response("network", "save cost")}],
                         ["a.wav"])
    assert list(df.columns) == list(OutputSchema.to_schema().columns)
    row = df.iloc[0]
    assert row["Voice File Name"] == "a.wav"
    assert row["call_sumary_call_result"] == "churn"
    assert row["reason_main"] == "network"
    assert row["reason_secondary"] == "save cost"


def test_absent_rank_is_blank_not_the_text_none():
    """A None rank must not reach the sheet as the literal string "None"."""
    df = build_output_df([{"file_name": "a.wav", "content": response("network")}], ["a.wav"])
    assert df.loc[0, "reason_third"] == ""
    assert df.loc[0, "reason_third"] != "None"


def test_unanswered_file_still_gets_a_row():
    """One row per submitted file, so 2 in produce 2 out however many the model failed on."""
    df = build_output_df([{"file_name": "a.wav", "content": response("network")}],
                         ["a.wav", "b.wav"])
    assert len(df) == 2
    assert df.loc[1, "Voice File Name"] == "b.wav"
    assert pd.isna(df.loc[1, "reason_main"])


def test_empty_items_yield_a_valid_frame():
    df = build_output_df([], ["a.wav"])
    assert list(df.columns) == list(OutputSchema.to_schema().columns)
    assert len(df) == 1


# --- scoring: the reason set ---------------------------------------------------------------


def test_join_is_on_the_stem():
    """The GT sheet names calls by a bare ID; the result sheet carries the .wav."""
    gt = frame([("abc123", "churn", "network", None, None)])
    result = frame([("abc123.wav", "churn", "network", None, None)])
    reasons = cm.evaluate(gt, result)[cm.BLOCK_REASONS]
    assert reasons.loc[0, "N"] == 1


def test_rank_permutation_scores_identically():
    """The regression this whole scorer exists for.

    Production unions the three ranks, so naming the right reasons in a different order is
    right. The previous per-rank design scored this same answer at zero on every column.
    """
    gt = frame(
        [
            ("a", "churn", "network", "save cost", "other"),
            ("b", "save", "post to pre", "contract end", None),
        ]
    )
    permuted = frame(
        [
            ("a.wav", "churn", "other", "network", "save cost"),
            ("b.wav", "save", "contract end", "post to pre", None),
        ]
    )
    straight = cm.evaluate(gt, gt.copy())[cm.BLOCK_REASONS]
    shuffled = cm.evaluate(gt, permuted)[cm.BLOCK_REASONS]
    pd.testing.assert_frame_equal(straight, shuffled)

    weighted = shuffled[shuffled["Reason"] == "Weighted-average"].iloc[0]
    assert weighted["F1"] == 1.0


def test_case_and_trailing_space_fold():
    """The GT tab mixes 'Network' with 'network' and carries 'save cost ' with a space."""
    gt = frame([("a", "Churn", "Network", "Save Cost ", None)])
    result = frame([("a.wav", "churn", "network", "save cost", None)])
    reasons = cm.evaluate(gt, result)[cm.BLOCK_REASONS].set_index("Reason")
    assert reasons.loc["network", "TP"] == 1
    assert reasons.loc["save cost", "TP"] == 1
    assert reasons.loc[cm.UNKNOWN_CLASS, "Support"] == 0


def test_grader_typo_lands_in_the_unknown_row():
    """A ground-truth typo must be visible, not silently dropped.

    The real MNP tab holds `contact end` and `cutomer reason`. Excluding them would quietly cost
    `contract end` and `customer reason` real recall with nothing on the sheet to say why.
    """
    gt = frame(
        [
            ("a", "churn", "Contact end", None, None),
            ("b", "churn", "cutomer reason", None, None),
            ("c", "churn", "network", None, None),
        ]
    )
    result = frame(
        [
            ("a.wav", "churn", "contract end", None, None),
            ("b.wav", "churn", "customer reason", None, None),
            ("c.wav", "churn", "network", None, None),
        ]
    )
    reasons = cm.evaluate(gt, result)[cm.BLOCK_REASONS].set_index("Reason")
    # Both typos are graded, so the catch-all carries their support and the model misses both.
    assert reasons.loc[cm.UNKNOWN_CLASS, "Support"] == 2
    assert reasons.loc[cm.UNKNOWN_CLASS, "FN"] == 2
    # And the categories the grader meant are charged the false positives, visibly.
    assert reasons.loc["contract end", "FP"] == 1
    assert reasons.loc["customer reason", "FP"] == 1


def test_true_point_is_not_split_on_its_comma():
    """Production splits reason cells on ',', which makes this class unscoreable there.

    `true point, dtac reward` contains a comma; splitting it yields two fragments that match no
    category, so it can never score a true positive in production. The MNP ground truth uses it,
    so the bug is real -- and not reproduced.
    """
    gt = frame([("a", "churn", "true point, dtac reward", None, None)])
    reasons = cm.evaluate(gt, gt.copy())[cm.BLOCK_REASONS].set_index("Reason")
    assert reasons.loc["true point, dtac reward", "TP"] == 1
    assert reasons.loc[cm.UNKNOWN_CLASS, "Support"] == 0


def test_missing_and_invented_reasons_are_attributable():
    """A miss is an FN on the class the human used; an invention is an FP on the class used."""
    gt = frame([("a", "churn", "network", "save cost", None)])
    result = frame([("a.wav", "churn", "network", "other", None)])
    reasons = cm.evaluate(gt, result)[cm.BLOCK_REASONS].set_index("Reason")
    assert reasons.loc["network", "TP"] == 1
    assert reasons.loc["save cost", "FN"] == 1
    assert reasons.loc["other", "FP"] == 1


# --- scoring: class registry and metric definitions ----------------------------------------


def test_reason_classes_come_from_the_schema_in_registry_order():
    """One definition of the vocabulary (the schema) and one of the order (the registry)."""
    assert set(cm.REASON_CLASSES) == set(get_args(ReasonCategory))
    # Registry order, not the enum's: `other` is class 8, not last.
    assert cm.REASON_CLASSES.index("other") == 7
    assert cm.REASON_CLASSES[0] == "network"
    assert cm.REASON_SCORED_CLASSES[-1] == cm.UNKNOWN_CLASS


def test_class_numbering_matches_production():
    """The numbers are quoted against the stakeholders' workbook, so they are pinned here."""
    assert [class_label(name) for name in cm.REASON_CLASSES[:4]] == [
        "Class 1",
        "Class 2",
        "Class 3",
        "Class 4",
    ]
    assert class_label("other") == "Class 8"
    assert class_label("true point, dtac reward") == "Class 11"
    assert class_label("down sell not success") == "Class 12"
    # The catch-all is not a class the prompt can ask for, so it carries no number.
    assert class_label(cm.UNKNOWN_CLASS) == ""


def test_outcome_keeps_productions_undefine_spelling():
    """Production's fact_checker scores 'undefined' against a schema emitting 'undefine'.

    That mismatch silently excludes every real one from its metrics. We score what the schema
    can actually produce.
    """
    assert cm.OUTCOME_LABELS == ("save", "churn", "unknown", "undefine")


def test_tn_is_consistent_with_n():
    """The stakeholders' own sheet fails this on 7 of 11 rows; ours cannot."""
    gt = frame(
        [
            ("a", "churn", "network", "save cost", None),
            ("b", "save", "other", None, None),
        ]
    )
    reasons = cm.evaluate(gt, gt.copy())[cm.BLOCK_REASONS]
    classes = reasons[~reasons["Reason"].str.endswith("average")]
    assert ((classes["TP"] + classes["FP"] + classes["FN"] + classes["TN"]) == classes["N"]).all()
    assert (classes["Support"] == classes["TP"] + classes["FN"]).all()


def test_per_class_metrics_match_the_stakeholder_workbook():
    """Class 1 of their `Score - Reason` sheet, reproduced from its raw counts.

    TP=43 FP=5 FN=4 TN=157 over N=209 -> 0.9569 / 0.8958 / 0.9149 / 0.9053.
    """
    score = LabelScore(
        label="network", tp=43, tn=157, fp=5, fn=4,
        precision=43 / 48, recall=43 / 47, f1=2 * 43 / (2 * 43 + 5 + 4), support=47,
    )
    assert score.n == 209
    assert round(score.accuracy, 4) == 0.9569
    assert round(score.precision, 4) == 0.8958
    assert round(score.recall, 4) == 0.9149
    assert round(score.f1, 4) == 0.9053


def test_unused_class_reads_n_a_rather_than_zero():
    """A category nobody used is not a category anybody got wrong."""
    gt = frame([("a", "churn", "network", None, None)])
    reasons = cm.evaluate(gt, gt.copy())[cm.BLOCK_REASONS].set_index("Reason")
    assert reasons.loc["other", "F1"] == cm.NOT_APPLICABLE
    assert reasons.loc["other", "Accuracy"] == cm.NOT_APPLICABLE
    assert reasons.loc["other", "Support"] == 0


def test_averages_are_the_three_the_workbook_prints():
    """Macro / Micro / Weighted, in that order, on every per-class block."""
    gt = frame([("a", "churn", "network", "save cost", None)])
    for block in (cm.BLOCK_REASONS, cm.BLOCK_OUTCOME):
        rows = cm.evaluate(gt, gt.copy())[block]
        label = "Reason" if block == cm.BLOCK_REASONS else "Label"
        assert list(rows[label])[-3:] == [
            "Macro-average",
            "Micro-average",
            "Weighted-average",
        ]


# --- dashboard shape ------------------------------------------------------------------------


def test_blocks_are_in_sheet_order():
    gt = frame([("a", "churn", "network", None, None)])
    assert list(cm.evaluate(gt, gt.copy())) == [
        cm.BLOCK_HEADER,
        cm.BLOCK_SUMMARY,
        cm.BLOCK_OUTCOME,
        cm.BLOCK_REASONS,
        cm.BLOCK_LEGEND,
    ]


def test_every_scored_block_names_its_ground_truth_columns():
    """No number on the dashboard may be unattributable to a ground-truth column."""
    assert set(cm.BLOCK_SOURCES) == {cm.BLOCK_OUTCOME, cm.BLOCK_REASONS}
    assert cm.OUTCOME_COLUMN in cm.BLOCK_SOURCES[cm.BLOCK_OUTCOME]
    for column in cm.REASON_COLUMNS:
        assert column in cm.BLOCK_SOURCES[cm.BLOCK_REASONS]
    assert "SET" in cm.BLOCK_SOURCES[cm.BLOCK_REASONS]


def test_summary_reports_one_row_per_topic():
    gt = frame([("a", "churn", "network", None, None)])
    summary = cm.evaluate(gt, gt.copy())[cm.BLOCK_SUMMARY]
    assert list(summary["Topic"]) == [cm.TOPIC_OUTCOME, cm.TOPIC_REASON]
    assert list(summary.columns) == [
        "Topic", "Classes", "N", "Accuracy", "Precision", "Recall", "F1", "Note",
    ]


def test_duplicate_key_does_not_cross_join():
    """A repeated key would otherwise be counted several times over and mis-paired."""
    gt = frame(
        [
            ("a", "churn", "network", None, None),
            ("a", "save", "save cost", None, None),
            ("b", "churn", "other", None, None),
        ]
    )
    blocks = cm.evaluate(gt, gt.copy())
    assert blocks[cm.BLOCK_REASONS].loc[0, "N"] == 2
    assert "1 ground truth, 1 result" in blocks[cm.BLOCK_HEADER].loc[0, "Duplicate keys dropped"]


# --- compare sheets -------------------------------------------------------------------------


def test_compare_frames_print_the_raw_cell():
    """A reader chasing an F must see what the workbook held, not a folded rewrite."""
    gt = frame([("a", "churn", "Network", None, None)])
    result = frame([("a.wav", "churn", "save cost", None, None)])
    reasons = cm.compare_rows(gt, result)[cm.COMPARE_REASONS]
    assert reasons.loc[0, "reason_main (rank) GT"] == "Network"
    assert reasons.loc[0, "reason_main (rank) AI"] == "save cost"
    assert reasons.loc[0, f"{cm.COMPARE_SET_GROUP} Compare"] == cm.MATCH_FALSE


def test_compare_set_verdict_ignores_rank_but_the_rank_columns_still_show_it():
    """The row that explains the whole change: ranks differ, the answer is still right."""
    gt = frame([("a", "churn", "network", "save cost", None)])
    result = frame([("a.wav", "churn", "save cost", "network", None)])
    reasons = cm.compare_rows(gt, result)[cm.COMPARE_REASONS]
    assert reasons.loc[0, "reason_main (rank) Compare"] == cm.MATCH_FALSE
    assert reasons.loc[0, f"{cm.COMPARE_SET_GROUP} Compare"] == cm.MATCH_TRUE
    assert reasons.loc[0, f"{cm.COMPARE_SET_GROUP} GT"] == "network, save cost"


def test_compare_sheets_cover_both_scored_topics():
    gt = frame([("a", "churn", "network", None, None)])
    frames = cm.compare_rows(gt, gt.copy())
    assert list(frames) == [cm.COMPARE_OUTCOME, cm.COMPARE_REASONS]


def test_sheet_names_share_one_suffix():
    """A run's dashboard and its verdict sheets must be readable as one set."""
    bases = ("Evaluation Dashboard", "Call Result Compare Result", "Reasons Compare Result")
    assert cm.resolve_sheet_names([], bases) == list(bases)
    assert cm.resolve_sheet_names(["Reasons Compare Result"], bases) == [
        f"{base}_1" for base in bases
    ]
