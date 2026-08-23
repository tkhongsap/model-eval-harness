"""Tests for the retention evaluation pipeline: schema, prompt, output frame, and the scorer.

The scoring path is pure -- no SharePoint, no Vertex -- so every test below runs offline against
fabricated frames. The transcript block is the one part that needs an embedder, and that is
injected as a stub; without one, ``evaluate`` omits the block entirely.

Two things these tests exist to hold, both of which the previous design got wrong:

* **The grain is per product.** A client can churn from Postpaid while being saved on TOL, and the
  ground-truth tab grades calls that way. The schema, the sheet and the merge all carry it.
* **Reasons are scored as a set.** Rank is production's to ignore, and ignoring it here is what
  stops a correct answer in a different order from scoring zero.
"""

from typing import get_args

import pandas as pd
import pytest
from pydantic import ValidationError

from src.google_model.metrics import LabelScore, aggregate_scores
from src.google_model.reason_classes import class_label
from src.google_model.sentiment_retention import google_confusion_matrix as cm
from src.google_model.sentiment_retention.google_output import Config, build_output_df
from src.google_model.sentiment_retention.schema.model_response import (
    TRANSCRIPT_DESCRIPTION,
    ModelResponse,
    ProductAnalysis,
    ProductMap,
    ReasonCategory,
    ReasonSlot,
    SchemaHelper,
)
from src.google_model.sentiment_retention.schema.output_schema import OutputSchema

GT_COLUMNS = [
    "No",
    "Voice File Name",
    "call_sumary_call_result",
    "call_sumary_product",
    "reason_main",
    "reason_secondary",
    "reason_third",
]

# An area block with nothing named -- the shape every call that never locates a fault takes.
NO_AREA = {
    "area_tag_province": None,
    "area_tag_district": None,
    "area_tag_sub_district": None,
    "area_tag_landmark": None,
}

# A network block with no issue: the shape every call that never mentions the network takes.
NO_NETWORK = {
    "issue_type": None,
    "sub_reason": None,
    "problem_statement_list": None,
    "churn_probability": None,
    "area": NO_AREA,
}


def slot(reason, keyword="kw"):
    """One ranked reason paired with the client's own words, or null for both."""
    return {"reason": reason, "keyword": None if reason is None else keyword}


def analysis(main, secondary=None, third=None, outcome="churn", **overrides):
    """One product's analysis, ready to nest under a product key."""
    return {
        "main": slot(main),
        "secondary": slot(secondary),
        "third": slot(third),
        "retention_outcome": outcome,
        "network_issue": NO_NETWORK,
        **overrides,
    }


def products(**named):
    """A ProductMap payload: the named products analysed, the rest explicitly absent."""
    return {name: named.get(name) for name in ("Postpaid", "TOL", "TVS", "unknown")}


def payload(product_map, **overrides):
    """A full model answer, ready to validate. Overrides replace any top-level field."""
    return {
        "transcript": "Agent: hello",
        "product": product_map,
        "call_event_detection": None,
        "recommendation": None,
        **overrides,
    }


def response(product_map, **overrides):
    """A validated ModelResponse dumped the way build_output_df receives it."""
    return ModelResponse.model_validate(payload(product_map, **overrides)).model_dump(mode="json")


def frame(rows):
    """A sheet-shaped frame from ``(name, outcome, product, main, secondary, third)`` tuples."""
    return pd.DataFrame(
        [
            {
                "No": index,
                "Voice File Name": name,
                "call_sumary_call_result": outcome,
                "call_sumary_product": product,
                "reason_main": main,
                "reason_secondary": secondary,
                "reason_third": third,
            }
            for index, (name, outcome, product, main, secondary, third) in enumerate(
                rows, start=1
            )
        ],
        columns=GT_COLUMNS,
    )


# --- schema ---------------------------------------------------------------------------------

FULL_FIELDS = ["transcript", "product", "call_event_detection", "recommendation"]


def test_field_order_is_generation_order():
    """A model generates in `properties` insertion order; transcript must come first."""
    assert list(ModelResponse.model_json_schema()["properties"]) == FULL_FIELDS


def test_vertex_schema_renders_without_unsupported_fields():
    """The nullable per-product blocks are what is most likely to trip the OpenAPI conversion."""
    assert SchemaHelper.vertex_schema().required == FULL_FIELDS


def test_every_property_is_required_at_every_level():
    """OpenAI strict mode demands it, on the nested objects too.

    This is the invariant production's schema does *not* hold -- it marks `secondary`, `third`
    and every product key optional -- and the one this repo trades for an explicit `null`.
    """
    schema = SchemaHelper.openai_response_format()["json_schema"]["schema"]
    optional = []

    def walk(node, path="/"):
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                missing = set(node["properties"]) - set(node.get("required", []))
                if missing:
                    optional.append((path, sorted(missing)))
            for key, value in node.items():
                walk(value, f"{path}{key}/")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}{index}/")

    walk(schema)
    assert optional == []


def test_product_is_a_map_of_four_nullable_keys():
    """Production's `product` is an OBJECT keyed by product name, not a list of names.

    An earlier conversion here reduced it to `list[ProductCategory]` and serialised that to a
    comma string, which is what made a per-product answer impossible to express.
    """
    assert list(ProductMap.model_fields) == ["Postpaid", "TOL", "TVS", "unknown"]
    node = ModelResponse.model_json_schema()["properties"]["product"]
    assert "$ref" in node or node.get("type") == "object"
    assert "array" not in str(node)


def test_each_product_carries_its_own_reasons_and_outcome():
    assert list(ProductAnalysis.model_fields) == [
        "main",
        "secondary",
        "third",
        "retention_outcome",
        "network_issue",
    ]


def test_a_rank_is_a_reason_and_a_keyword():
    """Production's schema names this field `keyword`; only its prompt example says `Phrase`.

    Function calling binds to the schema, so `keyword` is what production actually receives and
    writes to its `keyword(main)` columns.
    """
    assert list(ReasonSlot.model_fields) == ["reason", "keyword"]


def test_area_is_nested_inside_the_network_block():
    """Production wraps the four area tags in an `area` object; an earlier conversion hoisted."""
    parsed = ModelResponse.model_validate(payload(products(Postpaid=analysis("network"))))
    area = parsed.product.Postpaid.network_issue.area
    assert area.area_tag_province is None
    assert not hasattr(parsed.product.Postpaid.network_issue, "area_tag_province")


def test_one_call_can_churn_on_one_product_and_be_saved_on_another():
    """The whole reason for the per-product grain, pinned as a test."""
    parsed = ModelResponse.model_validate(
        payload(
            products(
                Postpaid=analysis("network", outcome="save"),
                TOL=analysis("contract end", outcome="churn"),
            )
        )
    )
    assert parsed.product.Postpaid.retention_outcome == "save"
    assert parsed.product.TOL.retention_outcome == "churn"
    assert list(parsed.product.analysed()) == ["Postpaid", "TOL"]


def test_an_unmentioned_product_is_null_not_absent():
    parsed = ModelResponse.model_validate(payload(products(TVS=analysis("other"))))
    assert parsed.product.Postpaid is None
    assert list(parsed.product.analysed()) == ["TVS"]


def test_transcript_spec_matches_the_qa_pipeline_word_for_word():
    """Three pipelines asking for different transcripts would not have comparable CER."""
    from src.google_model.sentiment.schema.model_response import (
        TRANSCRIPT_DESCRIPTION as QA_DESCRIPTION,
    )

    assert TRANSCRIPT_DESCRIPTION == QA_DESCRIPTION


def test_no_issue_type_nulls_the_whole_network_block_including_area():
    parsed = ModelResponse.model_validate(
        payload(
            products(
                Postpaid=analysis(
                    "network",
                    network_issue={
                        "issue_type": None,
                        "sub_reason": "should be dropped",
                        "problem_statement_list": ["also dropped"],
                        "churn_probability": 90,
                        "area": {
                            "area_tag_province": "Chiang Mai",
                            "area_tag_district": None,
                            "area_tag_sub_district": None,
                            "area_tag_landmark": None,
                        },
                    },
                )
            )
        )
    )
    block = parsed.product.Postpaid.network_issue
    assert (block.sub_reason, block.problem_statement_list, block.churn_probability) == (
        None,
        None,
        None,
    )
    assert block.area.area_tag_province is None


@pytest.mark.parametrize(
    ("secondary", "third", "expected"),
    [
        ("network", None, (None, None)),
        (None, "network", (None, None)),
        ("save cost", "save cost", ("save cost", None)),
    ],
)
def test_duplicate_reasons_collapse_within_a_product(secondary, third, expected):
    """The tab never lists a category twice within a product, so a repeat is always wrong."""
    parsed = ModelResponse.model_validate(
        payload(products(Postpaid=analysis("network", secondary, third)))
    )
    block = parsed.product.Postpaid
    assert (block.secondary.reason, block.third.reason) == expected


def test_a_collapsed_rank_hands_its_keywords_to_the_survivor():
    """The evidence is real even when the rank repeating it is not."""
    parsed = ModelResponse.model_validate(
        payload(
            products(
                Postpaid={
                    "main": slot("network", "เน็ตช้า"),
                    "secondary": slot("network", "หลุดบ่อย"),
                    "third": slot(None),
                    "retention_outcome": "churn",
                    "network_issue": NO_NETWORK,
                }
            )
        )
    )
    assert parsed.product.Postpaid.secondary.reason is None
    assert parsed.product.Postpaid.main.keyword == "เน็ตช้า, หลุดบ่อย"


def test_true_point_is_not_a_retention_category():
    """The retention prompt folds that case into `other`; only MNP has the category.

    Verified against production: retention's enum has eleven members, MNP's twelve.
    """
    assert "true point, dtac reward" not in get_args(ReasonCategory)
    assert len(get_args(ReasonCategory)) == 11
    with pytest.raises(ValidationError):
        ModelResponse.model_validate(
            payload(products(Postpaid=analysis("true point, dtac reward")))
        )


def test_empty_string_is_rejected():
    """`""` is not an allowed value anywhere; the prompt says so and the schema enforces it."""
    with pytest.raises(ValidationError):
        ModelResponse.model_validate(products(Postpaid=analysis("")))


# --- prompt / schema agreement --------------------------------------------------------------


def test_prompt_example_validates_against_the_schema():
    """The worked example is the shape the model is shown; the decoder must accept it."""
    import json
    import re
    from pathlib import Path

    markdown = Path(Config.system_prompt_file).read_text(encoding="utf-8")
    block = re.search(r"```json\n(.*?)\n    ```", markdown, re.S)
    assert block, "no worked example in the prompt"
    parsed = ModelResponse.model_validate(json.loads(block.group(1)))
    # And it must actually demonstrate the per-product shape, or it teaches the wrong thing.
    assert len(parsed.product.analysed()) >= 2
    outcomes = {block.retention_outcome for block in parsed.product.analysed().values()}
    assert len(outcomes) > 1, "the example must show products disagreeing on the outcome"


def test_prompt_names_every_field_the_schema_decodes():
    """A field the prompt never mentions is one the model was never asked for."""
    from pathlib import Path

    markdown = Path(Config.system_prompt_file).read_text(encoding="utf-8")
    for name in (*ModelResponse.model_fields, *ProductAnalysis.model_fields, "keyword", "area"):
        assert name in markdown, name


def test_prompt_no_longer_claims_one_answer_per_call():
    """The old contract said so explicitly, and it is now the opposite of true."""
    from pathlib import Path

    markdown = Path(Config.system_prompt_file).read_text(encoding="utf-8")
    assert "One call produces ONE answer" not in markdown


# --- output frame ---------------------------------------------------------------------------


def test_output_frame_fans_out_one_row_per_product():
    """The sheet grain follows the schema grain, or the scorer cannot join on it."""
    dump = response(
        products(
            Postpaid=analysis("network", "save cost", outcome="save"),
            TOL=analysis("contract end", outcome="churn"),
        )
    )
    df = build_output_df([{"file_name": "a.wav", "content": dump}], ["a.wav"])
    assert len(df) == 2
    assert list(df["call_sumary_product"]) == ["Postpaid", "TOL"]
    assert list(df["call_sumary_call_result"]) == ["save", "churn"]
    assert list(df["reason_main"]) == ["network", "contract end"]
    assert list(df["reason_secondary"]) == ["save cost", ""]
    # `No` is a row counter now, not a file counter.
    assert list(df["No"]) == [1, 2]


def test_a_file_naming_no_product_still_gets_one_blank_row():
    """A submitted file must never silently vanish from the sheet."""
    df = build_output_df(
        [{"file_name": "a.wav", "content": response(products())}], ["a.wav", "b.wav"]
    )
    assert len(df) == 2
    assert pd.isna(df.loc[0, "call_sumary_product"])
    assert list(df["Voice File Name"]) == ["a.wav", "b.wav"]


def test_output_columns_are_the_ground_truth_sheet_columns():
    assert list(OutputSchema.to_schema().columns) == GT_COLUMNS


def test_empty_items_yield_a_valid_frame():
    assert len(build_output_df([], [])) == 0


# --- scorer: grain and the duplicate-row fold -----------------------------------------------


def test_merge_is_keyed_on_call_and_product():
    """One call, two products, two records -- and each keeps its own outcome."""
    gt = frame(
        [
            ("a", "save", "Postpaid", "network", "", ""),
            ("a", "churn", "TOL", "contract end", "", ""),
        ]
    )
    merged, gt_products, _, counts = cm._merge(gt, gt.copy())
    assert len(merged) == 2
    assert counts["gt_duplicates"] == 0
    assert gt_products["a"] == {"Postpaid", "TOL"}


def test_rows_sharing_a_call_and_product_union_their_reasons():
    """The grader used a second row to record more than three reasons.

    Keeping only the first -- what the old scorer did -- discarded real ground truth. On the real
    tab that cost one call two of its four graded reasons.
    """
    gt = frame(
        [
            ("a", "save", "TOL", "network", "dissatisfied service", ""),
            ("a", "save", "TOL", "save cost", "promotion related", ""),
        ]
    )
    merged, _, _, counts = cm._merge(gt, gt.copy())
    assert len(merged) == 1
    assert counts["gt_duplicates"] == 1

    by_lower = {label.casefold(): label for label in cm.REASON_CLASSES}
    pooled, _ = cm._reason_set(merged.iloc[0], [f"{cm.REASON_POOL_COLUMN}_gt"], by_lower)
    assert pooled == {"network", "dissatisfied service", "save cost", "promotion related"}


def test_the_real_ground_truth_folds_to_98_records():
    """100 rows, 97 calls, 98 (call, product) records -- the arithmetic the plan committed to."""
    gt = pd.read_excel(
        "resources/AI Benchmark Report.xlsx",
        sheet_name="Voice_retention - Groundtruth",
        dtype=str,
        keep_default_na=False,
        na_values=[],
    )
    merged, gt_products, _, counts = cm._merge(gt, gt.copy())
    assert len(gt) == 100
    assert len(merged) == 98
    assert counts["gt_duplicates"] == 2
    assert len(gt_products) == 97


def test_a_product_only_one_side_named_still_scores():
    """The join is outer, and each topic charges the mistake exactly once.

    The model named TOL where the graders named Postpaid. The product topic charges both halves.
    The reason topic charges the missed product's reasons as false negatives but does NOT charge
    the invented product's -- its ground-truth side is empty, so there is nothing to be wrong
    about, and the product topic has already booked that error. Charging it here too would
    double-count one mistake.

    Call Result charges the miss through the `(missing)` class, which is what stops an unmatched
    row being silently excluded -- before that class existed this fixture scored 0 of 2 rows and
    the block came out empty rather than bad.
    """
    gt = frame([("a", "save", "Postpaid", "network", "", "")])
    result = frame([("a", "save", "TOL", "network", "", "")])
    blocks = cm.evaluate(gt, result)

    reasons = blocks[cm.BLOCK_REASONS].set_index("Reason")
    assert reasons.loc["network", "FN"] == 1
    assert reasons.loc["network", "FP"] == 0

    product = blocks[cm.BLOCK_PRODUCT].set_index("Product")
    assert product.loc["Postpaid", "FN"] == 1
    assert product.loc["TOL", "FP"] == 1

    outcome = blocks[cm.BLOCK_OUTCOME].set_index("Label")
    assert outcome.loc["save", "FN"] == 1
    assert outcome.loc[cm.MISSING_LABEL, "FP"] == 1


def test_an_unmatched_row_is_scored_not_excluded():
    """The regression behind the `(missing)` class: excluding it hid the model's misses."""
    gt = frame([("a", "save", "Postpaid", "network", "", "")])
    result = frame([("a", "save", "TOL", "network", "", "")])
    summary = cm.evaluate(gt, result)[cm.BLOCK_SUMMARY].set_index("Topic")
    assert summary.loc[cm.TOPIC_OUTCOME, "N"] == 1
    assert summary.loc[cm.TOPIC_OUTCOME, "Recall"] == 0.0


# --- scorer: reasons as a set ---------------------------------------------------------------


def test_rank_permutation_scores_identically():
    """The regression the scorer was rebuilt for."""
    gt = frame([("a", "save", "Postpaid", "network", "save cost", "other")])
    permuted = frame([("a", "save", "Postpaid", "other", "network", "save cost")])
    pd.testing.assert_frame_equal(
        cm.evaluate(gt, gt.copy())[cm.BLOCK_REASONS],
        cm.evaluate(gt, permuted)[cm.BLOCK_REASONS],
    )


def test_reason_classes_skip_class_eleven():
    """Retention has no `true point, dtac reward`, so its numbering has a hole -- by design."""
    numbers = [class_label(name) for name in cm.REASON_CLASSES]
    assert "Class 11" not in numbers
    assert "Class 12" in numbers
    assert class_label("other") == "Class 8"


def test_grader_typo_would_land_in_the_unknown_row():
    gt = frame([("a", "save", "Postpaid", "Contact end", "", "")])
    result = frame([("a", "save", "Postpaid", "contract end", "", "")])
    reasons = cm.evaluate(gt, result)[cm.BLOCK_REASONS].set_index("Reason")
    assert reasons.loc[cm.UNKNOWN_CLASS, "Support"] == 1
    assert reasons.loc["contract end", "FP"] == 1


def test_clean_ground_truth_puts_nothing_in_the_unknown_row():
    """The retention tab is clean, unlike MNP's; this is what would catch a regression."""
    gt = pd.read_excel(
        "resources/AI Benchmark Report.xlsx",
        sheet_name="Voice_retention - Groundtruth",
        dtype=str,
        keep_default_na=False,
        na_values=[],
    )
    reasons = cm.evaluate(gt, gt.copy())[cm.BLOCK_REASONS].set_index("Reason")
    assert reasons.loc[cm.UNKNOWN_CLASS, "Support"] == 0


# --- scorer: the product topic --------------------------------------------------------------


def test_product_is_scored_at_call_grain_on_its_own_join():
    """It is the other blocks' join key, so it cannot grade itself there."""
    gt = frame(
        [
            ("a", "save", "Postpaid", "network", "", ""),
            ("a", "churn", "TOL", "network", "", ""),
        ]
    )
    blocks = cm.evaluate(gt, gt.copy())
    # Two records for reasons, but only one call for products.
    assert blocks[cm.BLOCK_REASONS].loc[0, "N"] == 2
    assert blocks[cm.BLOCK_PRODUCT].loc[0, "N"] == 1
    assert cm.PRODUCT_COLUMN in cm.BLOCK_SOURCES[cm.BLOCK_PRODUCT]


def test_missing_one_product_of_two_is_a_false_negative():
    gt = frame(
        [
            ("a", "save", "Postpaid", "network", "", ""),
            ("a", "save", "TOL", "network", "", ""),
        ]
    )
    result = frame([("a", "save", "Postpaid", "network", "", "")])
    product = cm.evaluate(gt, result)[cm.BLOCK_PRODUCT].set_index("Product")
    assert product.loc["Postpaid", "TP"] == 1
    assert product.loc["TOL", "FN"] == 1


def test_a_product_label_neither_side_used_reads_n_a():
    gt = frame([("a", "save", "Postpaid", "network", "", "")])
    product = cm.evaluate(gt, gt.copy())[cm.BLOCK_PRODUCT].set_index("Product")
    assert product.loc["TVS", "F1"] == cm.NOT_APPLICABLE
    assert product.loc["TVS", "Support"] == 0


# --- dashboard shape ------------------------------------------------------------------------


def test_blocks_are_in_sheet_order():
    gt = frame([("a", "save", "Postpaid", "network", "", "")])
    assert list(cm.evaluate(gt, gt.copy())) == [
        cm.BLOCK_HEADER,
        cm.BLOCK_SUMMARY,
        cm.BLOCK_OUTCOME,
        cm.BLOCK_REASONS,
        cm.BLOCK_PRODUCT,
        cm.BLOCK_LEGEND,
    ]


def test_every_scored_block_names_its_ground_truth_columns():
    assert set(cm.BLOCK_SOURCES) == {cm.BLOCK_OUTCOME, cm.BLOCK_REASONS, cm.BLOCK_PRODUCT}
    for column in cm.REASON_COLUMNS:
        assert column in cm.BLOCK_SOURCES[cm.BLOCK_REASONS]


def test_summary_reports_all_three_topics():
    gt = frame([("a", "save", "Postpaid", "network", "", "")])
    summary = cm.evaluate(gt, gt.copy())[cm.BLOCK_SUMMARY]
    assert list(summary["Topic"]) == [cm.TOPIC_OUTCOME, cm.TOPIC_REASON, cm.TOPIC_PRODUCT]


def test_tn_is_consistent_with_n_on_every_class_row():
    """The stakeholders' own sheet fails this on 7 of 11 rows; ours cannot."""
    gt = frame([("a", "save", "Postpaid", "network", "save cost", "")])
    for block in (cm.BLOCK_REASONS, cm.BLOCK_OUTCOME, cm.BLOCK_PRODUCT):
        rows = cm.evaluate(gt, gt.copy())[block]
        label = rows.columns[1]
        classes = rows[~rows[label].str.endswith("average")]
        counted = classes["TP"] + classes["FP"] + classes["FN"] + classes["TN"]
        assert (counted == classes["N"]).all(), block


def test_per_class_metrics_match_the_stakeholder_workbook():
    """Class 1 of their `Score - Reason` sheet, reproduced from its raw counts."""
    score = LabelScore(
        label="network",
        tp=43,
        tn=157,
        fp=5,
        fn=4,
        precision=43 / 48,
        recall=43 / 47,
        f1=2 * 43 / (2 * 43 + 5 + 4),
        support=47,
    )
    assert score.n == 209
    assert round(score.accuracy, 4) == 0.9569
    assert round(score.precision, 4) == 0.8958
    assert round(score.recall, 4) == 0.9149
    assert round(score.f1, 4) == 0.9053


def test_the_three_averages_match_the_stakeholder_workbook():
    """All eleven of their reason classes, reproduced from TP/FP/FN alone.

    Their Accuracy column reads 0.9180/0.8704 against our 0.9178/0.8702: that sheet's TN column
    sums to 2306 against a true 2299, a defect its own footnote records. Every precision, recall
    and F1 figure matches exactly.
    """
    raw = [
        ("network", 43, 5, 4),
        ("promotion related", 48, 31, 9),
        ("device promotion related", 4, 1, 9),
        ("save cost", 72, 14, 28),
        ("contract end", 2, 3, 7),
        ("sale upsell problem", 8, 3, 7),
        ("dissatisfied service", 14, 11, 18),
        ("other", 2, 6, 10),
        ("post to pre", 39, 8, 8),
        ("customer reason", 1, 3, 2),
        ("down sell not success", 1, 0, 2),
    ]
    n = 209
    per_label = []
    for name, tp, fp, fn in raw:
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_label.append(
            LabelScore(
                label=name,
                tp=tp,
                tn=n - tp - fp - fn,
                fp=fp,
                fn=fn,
                precision=precision,
                recall=recall,
                f1=f1,
                support=tp + fn,
            )
        )

    aggregates = aggregate_scores(per_label)
    assert [round(aggregates.macro.precision, 4), round(aggregates.macro.recall, 4)] == [
        0.6507,
        0.5128,
    ]
    assert round(aggregates.macro.f1, 4) == 0.5489
    assert [round(aggregates.micro.precision, 4), round(aggregates.micro.recall, 4)] == [
        0.7335,
        0.6923,
    ]
    assert round(aggregates.micro.f1, 4) == 0.7123
    assert [round(aggregates.weighted.precision, 4), round(aggregates.weighted.recall, 4)] == [
        0.7368,
        0.6923,
    ]
    assert round(aggregates.weighted.f1, 4) == 0.7019
    # The two identities the module documents, so nobody "fixes" one of them.
    assert aggregates.macro.accuracy == pytest.approx(aggregates.micro.accuracy)
    assert aggregates.weighted.recall == pytest.approx(aggregates.micro.recall)


def test_ground_truth_scored_against_itself_is_perfect():
    gt = pd.read_excel(
        "resources/AI Benchmark Report.xlsx",
        sheet_name="Voice_retention - Groundtruth",
        dtype=str,
        keep_default_na=False,
        na_values=[],
    )
    blocks = cm.evaluate(gt, gt.copy())
    summary = blocks[cm.BLOCK_SUMMARY]
    assert list(summary["Accuracy"]) == [1.0, 1.0, 1.0]
    assert list(summary["F1"]) == [1.0, 1.0, 1.0]
    assert list(summary["N"]) == [98, 98, 97]


# --- compare sheets -------------------------------------------------------------------------


def test_compare_frames_cover_all_three_topics():
    gt = frame([("a", "save", "Postpaid", "network", "", "")])
    frames = cm.compare_rows(gt, gt.copy())
    assert list(frames) == [cm.COMPARE_OUTCOME, cm.COMPARE_REASONS, cm.COMPARE_PRODUCT]


def test_compare_set_verdict_ignores_rank_but_the_rank_columns_still_show_it():
    """The row that explains the change: ranks differ, the answer is still right."""
    gt = frame([("a", "save", "Postpaid", "network", "save cost", "")])
    result = frame([("a", "save", "Postpaid", "save cost", "network", "")])
    reasons = cm.compare_rows(gt, result)[cm.COMPARE_REASONS]
    assert reasons.loc[0, "reason_main (rank) Compare"] == cm.MATCH_FALSE
    assert reasons.loc[0, f"{cm.COMPARE_SET_GROUP} Compare"] == cm.MATCH_TRUE


def test_sheet_names_share_one_suffix():
    bases = (
        "Evaluation Dashboard",
        "Call Result Compare Result",
        "Reasons Compare Result",
        "Product Compare Result",
    )
    assert cm.resolve_sheet_names([], bases) == list(bases)
    assert cm.resolve_sheet_names(["Product Compare Result"], bases) == [
        f"{base}_1" for base in bases
    ]
