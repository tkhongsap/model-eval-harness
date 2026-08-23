"""Tests for the local (ASR + LLM) retention pipeline: schema, output frame, pre-flight, scorers.

The scoring path here is pure -- no embedder, no SharePoint, no gateway -- so every test below
runs offline against fabricated frames.

The parity tests compare this package against ``src/google_model/sentiment_retention`` rather
than re-asserting behaviour the google tests already cover: the four modules are ports, and
what threatens them is *drift*, not a bug in logic that was correct when copied. A field added
on one side only, or a label set that stops matching, breaks the google-vs-local comparison
the whole benchmark exists to make -- silently, as two dashboards that no longer measure the
same thing.

What is genuinely this package's own is ``product``: a multi-label column that reaches the
sheet as one comma-joined cell and is scored per label.
"""

import pandas as pd
import pytest
from pydantic import ValidationError

from src.google_model.sentiment_retention import google_confusion_matrix as gcm
from src.google_model.sentiment_retention.schema.model_response import (
    ModelResponse as GoogleModelResponse,
)
from src.google_model.sentiment_retention.schema.output_schema import (
    OutputSchema as GoogleOutputSchema,
)
from src.local_model.sentiment_retention import internal_confusion_matrix as cm
from src.local_model.sentiment_retention.internal_asr_llm_output import (
    build_output_df,
    gt_source_overlap,
)
from src.local_model.sentiment_retention.schema.model_response import ModelResponse, SchemaHelper
from src.local_model.sentiment_retention.schema.output_schema import OutputSchema

COLUMNS = list(OutputSchema.to_schema().columns)


def analysis(main, secondary=None, third=None, outcome="churn"):
    """One product's analysis, ready to nest under a product key."""
    return {
        "main": main,
        "secondary": secondary,
        "third": third,
        "retention_outcome": outcome,
    }


def products(**named):
    """A ProductMap payload: the named products analysed, the rest explicitly absent."""
    return {name: named.get(name) for name in ("Postpaid", "TOL", "TVS", "unknown")}


def response(main=None, secondary=None, third=None, outcome="churn", product_map=None):
    """A decoded model answer, as build_output_df receives it.

    Defaults to a single Postpaid block, which is what most of these tests want; pass
    ``product_map`` for the multi-product cases.
    """
    payload = product_map or products(Postpaid=analysis(main, secondary, third, outcome))
    return ModelResponse.model_validate({"product": payload}).model_dump(mode="json")


def frame(rows):
    """A sheet-shaped frame with this package's columns, in order."""
    return pd.DataFrame(rows, columns=COLUMNS)


def row(name, outcome="churn", product="Postpaid", main="network", secondary="", third="", no=1):
    return {
        "No": no,
        "Voice File Name": name,
        "call_sumary_call_result": outcome,
        "call_sumary_product": product,
        "reason_main": main,
        "reason_secondary": secondary,
        "reason_third": third,
    }


# --------------------------------------------------------------------------------------
# Parity with the google package
# --------------------------------------------------------------------------------------


def test_the_local_schema_is_deliberately_narrower_than_the_google_copy():
    """The two packages share a SHAPE and a vocabulary; they differ only in what is carried.

    The narrowing is about fields this schema never had -- the transcript (which would come back
    as a verbatim echo of its own input), the event, the recommendation, and the per-product
    network block. It is NOT about falling behind a change made to the google copy: the
    per-product grain is such a change, and it is mirrored here, which the next assertion holds.

    Pinned as an exact set rather than a subset check, so re-widening either side is a visible
    edit here first.
    """
    google_only = set(GoogleModelResponse.model_fields) - set(ModelResponse.model_fields)
    assert google_only == {"transcript", "call_event_detection", "recommendation"}
    assert set(ModelResponse.model_fields) < set(GoogleModelResponse.model_fields)
    assert list(OutputSchema.to_schema().columns) == list(
        GoogleOutputSchema.to_schema().columns
    )


def test_the_grain_is_mirrored_from_the_google_copy():
    """Both families answer per product, or the two dashboards are not comparable."""
    from src.google_model.sentiment_retention.schema.model_response import (
        ProductAnalysis as GoogleProductAnalysis,
    )
    from src.local_model.sentiment_retention.schema.model_response import (
        ProductAnalysis,
        ProductMap,
    )

    assert list(ModelResponse.model_fields) == ["product"]
    assert list(ProductMap.model_fields) == ["Postpaid", "TOL", "TVS", "unknown"]
    # Same ranks and the same outcome; the google copy adds only the unscored network block, and
    # its ranks carry the `keyword` evidence this one does not.
    assert set(GoogleProductAnalysis.model_fields) - set(ProductAnalysis.model_fields) == {
        "network_issue"
    }


def test_scorer_label_sets_match_the_google_copy():
    """Both scorers read their vocabulary off the schema; the two schemas must agree."""
    assert cm.REASON_CLASSES == gcm.REASON_CLASSES
    assert cm.REASON_SCORED_CLASSES == gcm.REASON_SCORED_CLASSES
    assert cm.OUTCOME_LABELS == gcm.OUTCOME_LABELS
    assert cm.PRODUCT_LABELS == gcm.PRODUCT_LABELS
    assert cm.REASON_COLUMNS == gcm.REASON_COLUMNS
    assert cm.PRODUCT_COLUMN == gcm.PRODUCT_COLUMN
    assert cm.KEY_COLUMN == gcm.KEY_COLUMN


def test_output_columns_match_the_ground_truth_sheet_order():
    """The scorers merge with suffixes and read f"{column}_gt"; a rename breaks the join."""
    assert COLUMNS == [
        "No",
        "Voice File Name",
        "call_sumary_call_result",  # the sheet's typo, one "m", deliberately preserved
        "call_sumary_product",
        "reason_main",
        "reason_secondary",
        "reason_third",
    ]


def test_scoring_agrees_with_the_google_scorer_on_identical_input():
    """The ports are equivalent in behaviour, not just in shape.

    This is what keeps the two dashboards comparable: the families run different models, but they
    must compute the same numbers over the same classes from the same frames.
    """
    gt = frame(
        [
            row("a.wav", main="network", secondary="save cost"),
            row("a.wav", product="TOL", outcome="save", main="contract end", no=2),
            row("b.wav", no=3),
        ]
    )
    result = frame(
        [
            row("a.wav", main="save cost", secondary="network"),
            row("b.wav", outcome="save", no=2),
        ]
    )
    mine = cm.evaluate(gt.copy(), result.copy())
    theirs = gcm.evaluate(gt.copy(), result.copy())
    assert mine.keys() == theirs.keys()
    for title in mine:
        if title == cm.BLOCK_LEGEND:
            continue  # prose only, and it deliberately says "the model" instead of "Google"
        pd.testing.assert_frame_equal(mine[title], theirs[title])

    for key, produced in cm.compare_rows(gt.copy(), result.copy()).items():
        pd.testing.assert_frame_equal(
            produced, gcm.compare_rows(gt.copy(), result.copy())[key]
        )


# --------------------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------------------


def test_openai_response_format_renders_with_every_property_required():
    """The local gateway is OpenAI-compatible and its strict mode wants no optional keys."""
    fmt = SchemaHelper.openai_response_format()
    schema = fmt["json_schema"]["schema"]
    assert set(schema["required"]) == set(schema["properties"])


def test_vertex_schema_still_renders():
    """Kept alongside the google copy so the two schemas cannot diverge in convertibility."""
    assert SchemaHelper.vertex_schema() is not None


def test_product_is_a_map_not_a_list():
    """An earlier revision made this a list of names serialised to a comma cell.

    That shape cannot carry an outcome per product, which is the whole point of the grain.
    """
    from src.local_model.sentiment_retention.schema.model_response import ProductMap

    assert list(ProductMap.model_fields) == ["Postpaid", "TOL", "TVS", "unknown"]
    assert "array" not in str(ModelResponse.model_json_schema()["properties"]["product"])


def test_one_call_can_churn_on_one_product_and_be_saved_on_another():
    parsed = ModelResponse.model_validate(
        {
            "product": products(
                Postpaid=analysis("network", outcome="save"),
                TOL=analysis("contract end", outcome="churn"),
            )
        }
    )
    assert parsed.product.Postpaid.retention_outcome == "save"
    assert parsed.product.TOL.retention_outcome == "churn"
    assert list(parsed.product.analysed()) == ["Postpaid", "TOL"]


@pytest.mark.parametrize(
    ("secondary", "third", "expected"),
    [
        ("network", None, (None, None)),
        (None, "network", (None, None)),
        ("save cost", "save cost", ("save cost", None)),
        ("network", "network", (None, None)),
    ],
)
def test_duplicate_reasons_collapse(secondary, third, expected):
    """Collapse happens per product now: the tab never repeats a category within one."""
    parsed = ModelResponse.model_validate(
        {"product": products(Postpaid=analysis("network", secondary, third))}
    )
    block = parsed.product.Postpaid
    assert (block.secondary, block.third) == expected


def test_true_point_is_not_a_retention_category():
    """MNP has it; this prompt folds that case into `other`, and the schemas must differ."""
    with pytest.raises(ValidationError):
        ModelResponse.model_validate(
            {"product": products(Postpaid=analysis("true point, dtac reward"))}
        )


# --------------------------------------------------------------------------------------
# build_output_df
# --------------------------------------------------------------------------------------


def test_output_frame_fans_out_one_row_per_product():
    """The sheet grain follows the schema grain, or the scorer cannot join on it."""
    dump = response(
        product_map=products(
            Postpaid=analysis("network", "save cost", outcome="save"),
            TOL=analysis("contract end", outcome="churn"),
        )
    )
    df = build_output_df([{"file_name": "a.wav", "content": dump}], ["a.wav"])
    assert len(df) == 2
    assert list(df["call_sumary_product"]) == ["Postpaid", "TOL"]
    assert list(df["call_sumary_call_result"]) == ["save", "churn"]
    assert list(df["reason_main"]) == ["network", "contract end"]
    # `No` is a row counter now, not a file counter.
    assert list(df["No"]) == [1, 2]


def test_absent_rank_is_blank_not_the_text_none():
    """pandera coerces via astype(str), which would write the literal "None" into the cell."""
    dump = response("network")
    df = build_output_df([{"file_name": "a.wav", "content": dump}], ["a.wav"])
    assert df.loc[0, "reason_secondary"] == ""
    assert df.loc[0, "reason_third"] == ""


def test_a_file_naming_no_product_still_gets_one_blank_row():
    """A submitted file must never silently vanish from the sheet."""
    df = build_output_df(
        [{"file_name": "a.wav", "content": response(product_map=products())}],
        ["a.wav", "b.wav"],
    )
    assert len(df) == 2
    assert pd.isna(df.loc[0, "call_sumary_product"])
    assert list(df["Voice File Name"]) == ["a.wav", "b.wav"]


def test_empty_items_yield_a_valid_frame():
    assert len(build_output_df([], [])) == 0


# --------------------------------------------------------------------------------------
# Pre-flight
# --------------------------------------------------------------------------------------


def test_preflight_matches_on_the_stem_across_extensions():
    overlap = gt_source_overlap(["a.wav", "b.wav"], ["a.wav", "b.wav"])
    assert overlap["matched"] == 2


def test_preflight_reports_zero_overlap_for_a_wrong_corpus():
    overlap = gt_source_overlap(["a.wav"], ["z.wav"])
    assert overlap["matched"] == 0


# --------------------------------------------------------------------------------------
# Scorer
# --------------------------------------------------------------------------------------


def test_join_is_on_the_stem_and_the_product():
    gt = frame([row("a"), row("a", product="TOL", no=2)])
    result = frame([row("a.wav"), row("a.wav", product="TOL", no=2)])
    merged, gt_products, _, counts = cm._merge(gt, result)
    assert len(merged) == 2
    assert counts["gt_only"] == 0 and counts["result_only"] == 0
    assert gt_products["a"] == {"Postpaid", "TOL"}


def test_rows_sharing_a_call_and_product_union_their_reasons():
    """Keeping only the first -- the old behaviour -- discarded real ground truth."""
    gt = frame(
        [
            row("a.wav", main="network", secondary="dissatisfied service"),
            row("a.wav", main="save cost", secondary="promotion related", no=2),
        ]
    )
    merged, _, _, counts = cm._merge(gt, gt.copy())
    assert len(merged) == 1
    assert counts["gt_duplicates"] == 1
    by_lower = {label.casefold(): label for label in cm.REASON_CLASSES}
    pooled, _ = cm._reason_set(merged.iloc[0], [f"{cm.REASON_POOL_COLUMN}_gt"], by_lower)
    assert pooled == {"network", "dissatisfied service", "save cost", "promotion related"}


def test_rank_permutation_scores_identically():
    """The defect the scorer was rebuilt to fix, pinned on the local side too."""
    gt = frame([row("a.wav", main="network", secondary="save cost", third="other")])
    permuted = frame([row("a.wav", main="other", secondary="network", third="save cost")])
    pd.testing.assert_frame_equal(
        cm.evaluate(gt, gt.copy())[cm.BLOCK_REASONS],
        cm.evaluate(gt, permuted)[cm.BLOCK_REASONS],
    )


def test_product_is_scored_at_call_grain_on_its_own_join():
    """It is the other blocks' join key, so it cannot grade itself there."""
    gt = frame([row("a.wav"), row("a.wav", product="TOL", no=2)])
    blocks = cm.evaluate(gt, gt.copy())
    assert blocks[cm.BLOCK_REASONS].loc[0, "N"] == 2
    assert blocks[cm.BLOCK_PRODUCT].loc[0, "N"] == 1


def test_a_missed_product_costs_the_call_result_topic():
    """Before the `(missing)` class existed, an unmatched row was silently excluded."""
    gt = frame([row("a.wav", outcome="save")])
    result = frame([row("a.wav", product="TOL", outcome="save")])
    outcome = cm.evaluate(gt, result)[cm.BLOCK_OUTCOME].set_index("Label")
    assert outcome.loc["save", "FN"] == 1
    assert outcome.loc[cm.MISSING_LABEL, "FP"] == 1


# --------------------------------------------------------------------------------------
# Transcript scoring
# --------------------------------------------------------------------------------------


def stub_embedder(texts):
    """Deterministic vectors, so cosine is reproducible without touching Vertex."""
    return [[float(len(text) % 7), 1.0, 2.0] for text in texts]


def test_no_embedder_means_no_transcript_block():
    """Scoring stays offline and free unless a caller asks for the transcript family.

    The QA sibling requires an embedder because it always has a summary column to embed; these
    packages have nothing to embed unless transcripts are supplied, so the argument is optional
    and its absence must cost the block rather than raise.
    """
    gt = frame([row("a.wav")])
    for scorer in (cm,):
        assert scorer.BLOCK_TRANSCRIPT not in scorer.evaluate(gt.copy(), gt.copy())


def test_a_perfect_transcript_reads_cer_zero():
    """CER separates a good transcription from a poor one, and is the one metric on the
    dashboard where lower is better."""
    gt = frame([row("a.wav")])
    blocks = cm.evaluate(
        gt.copy(),
        gt.copy(),
        embedder=stub_embedder,
        gt_transcripts={"a": "Agent: hello"},
        pred_transcripts={"a": "Agent: hello"},
    )
    values = blocks[cm.BLOCK_TRANSCRIPT].set_index("Metric")["Value"]
    assert values["Mean CER"] == 0.0
    assert values["Mean char accuracy (1 - CER)"] == 1.0
    assert values["Scored"] == 1
    assert values["Excluded (missing either side)"] == 0


def test_a_call_missing_a_transcript_is_excluded_and_counted():
    """Silently scoring it as a total miss would blame the model for a missing file."""
    gt = frame([row("a.wav"), row("b.wav", no=2)])
    blocks = cm.evaluate(
        gt.copy(),
        gt.copy(),
        embedder=stub_embedder,
        gt_transcripts={"a": "Agent: hello"},
        pred_transcripts={"a": "Agent: hello", "b": "Agent: hello"},
    )
    values = blocks[cm.BLOCK_TRANSCRIPT].set_index("Metric")["Value"]
    assert values["Scored"] == 1
    assert values["Excluded (missing either side)"] == 1


def test_the_transcript_block_sits_before_the_legend():
    """Dict insertion order is the sheet's row order, and the legend explains the block."""
    gt = frame([row("a.wav")])
    blocks = cm.evaluate(
        gt.copy(),
        gt.copy(),
        embedder=stub_embedder,
        gt_transcripts={"a": "Agent: hello"},
        pred_transcripts={"a": "Agent: hallo"},
    )
    titles = list(blocks)
    assert titles.index(cm.BLOCK_TRANSCRIPT) == len(titles) - 2
    assert titles[-1] == cm.BLOCK_LEGEND
