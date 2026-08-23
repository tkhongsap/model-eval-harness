"""Tests for the local (ASR + LLM) MNP pipeline: schema, output frame, pre-flight, scorers.

The scoring path here is pure -- no embedder, no SharePoint, no gateway -- so every test below
runs offline against fabricated frames.

Two of these deserve explanation. The parity tests compare this package against
``src/google_model/sentiment_mnp`` rather than re-asserting behaviour the google tests already
cover: the four modules are ports, and what actually threatens them is *drift*, not a bug in
logic that was correct when copied. A field added on one side only, or a label set that stops
matching, breaks the google-vs-local comparison the whole benchmark exists to make -- and it
breaks it silently, as two dashboards that no longer measure the same thing.
"""

import pandas as pd
import pytest
from pydantic import ValidationError

from src.google_model.sentiment_mnp import google_confusion_matrix as gcm
from src.google_model.sentiment_mnp.schema.model_response import (
    ModelResponse as GoogleModelResponse,
)
from src.google_model.sentiment_mnp.schema.output_schema import (
    OutputSchema as GoogleOutputSchema,
)
from src.local_model.sentiment_mnp import internal_confusion_matrix as cm
from src.local_model.sentiment_mnp.internal_asr_llm_output import (
    build_output_df,
    gt_source_overlap,
)
from src.local_model.sentiment_mnp.schema.model_response import ModelResponse, SchemaHelper
from src.local_model.sentiment_mnp.schema.output_schema import OutputSchema

COLUMNS = list(OutputSchema.to_schema().columns)


def response(main, secondary=None, third=None, call_result="churn"):
    """A decoded model answer, as build_output_df receives it."""
    return ModelResponse.model_validate(
        {
            "reasons": {"main": main, "secondary": secondary, "third": third},
            "call_result": call_result,
        }
    ).model_dump(mode="json")


def frame(rows):
    """A sheet-shaped frame with this package's columns, in order."""
    return pd.DataFrame(rows, columns=COLUMNS)


def row(name, outcome="churn", main="network", secondary="", third="", no=1):
    return {
        "No": no,
        "Voice File Name": name,
        "call_sumary_call_result": outcome,
        "reason_main": main,
        "reason_secondary": secondary,
        "reason_third": third,
    }


# --------------------------------------------------------------------------------------
# Parity with the google package
# --------------------------------------------------------------------------------------


def test_the_local_schema_is_deliberately_narrower_than_the_google_copy():
    """The two packages no longer share a shape, and that divergence is a decision, not drift.

    The google schema was widened back to the full production contract so its benchmark run
    measures the call production actually issues. This one stays trimmed: the local model is fed
    an ASR transcript rather than audio, so a ``transcript`` field here would come back as a
    verbatim echo of its own input, and the remaining production fields buy nothing on a
    pipeline whose cost is dominated by transcription.

    Pinned as an exact set rather than a subset check, so re-widening either side is a visible
    edit here first. What must NOT diverge is the scored vocabulary and the sheet -- the next
    two assertions -- because those are what make the two dashboards comparable at all.
    """
    google_only = set(GoogleModelResponse.model_fields) - set(ModelResponse.model_fields)
    assert google_only == {
        "transcript",
        "reasoning",
        "call_event_detection",
        "ai_recommendation",
        "additional_fields",
    }
    assert set(ModelResponse.model_fields) < set(GoogleModelResponse.model_fields)
    assert list(OutputSchema.to_schema().columns) == list(
        GoogleOutputSchema.to_schema().columns
    )


def test_scorer_label_sets_match_the_google_copy():
    """Both scorers read their vocabulary off the schema; the two schemas must agree."""
    assert cm.REASON_CLASSES == gcm.REASON_CLASSES
    assert cm.REASON_SCORED_CLASSES == gcm.REASON_SCORED_CLASSES
    assert cm.OUTCOME_LABELS == gcm.OUTCOME_LABELS
    assert cm.REASON_COLUMNS == gcm.REASON_COLUMNS
    assert cm.OUTCOME_COLUMN == gcm.OUTCOME_COLUMN
    assert cm.KEY_COLUMN == gcm.KEY_COLUMN


def test_output_columns_match_the_ground_truth_sheet_order():
    """The scorers merge with suffixes and read f"{column}_gt"; a rename breaks the join."""
    assert COLUMNS == [
        "No",
        "Voice File Name",
        "call_sumary_call_result",  # the sheet's typo, one "m", deliberately preserved
        "reason_main",
        "reason_secondary",
        "reason_third",
    ]


def test_scoring_agrees_with_the_google_scorer_on_identical_input():
    """The ports are byte-equivalent in behaviour, not just in shape.

    This is what keeps the two dashboards comparable: the families may run different models, but
    they must compute the same numbers over the same classes from the same frames.
    """
    gt = frame([row("a.wav", main="network", secondary="save cost"), row("b.wav", no=2)])
    result = frame([row("a.wav", main="network"), row("b.wav", outcome="save", no=2)])
    mine = cm.evaluate(gt.copy(), result.copy())
    theirs = gcm.evaluate(gt.copy(), result.copy())
    assert mine.keys() == theirs.keys()
    for title in mine:
        if title == cm.BLOCK_LEGEND:
            continue  # prose only, and it deliberately says "the model" instead of "Google"
        pd.testing.assert_frame_equal(mine[title], theirs[title])

    for key, frame_ in cm.compare_rows(gt.copy(), result.copy()).items():
        pd.testing.assert_frame_equal(frame_, gcm.compare_rows(gt.copy(), result.copy())[key])


# --------------------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------------------


def test_openai_response_format_renders_with_every_property_required():
    """The local gateway is OpenAI-compatible and its strict mode wants no optional keys."""
    fmt = SchemaHelper.openai_response_format()
    schema = fmt["json_schema"]["schema"]
    assert set(schema["required"]) == set(schema["properties"])
    reasons = schema["$defs"]["Reasons"] if "$defs" in schema else None
    if reasons is not None:
        assert set(reasons["required"]) == set(reasons["properties"])


def test_vertex_schema_still_renders():
    """Kept alongside the google copy so the two schemas cannot diverge in convertibility."""
    assert SchemaHelper.vertex_schema() is not None


@pytest.mark.parametrize(
    ("secondary", "third", "expected"),
    [
        ("network", None, (None, None)),  # secondary repeats main
        (None, "network", (None, None)),  # third repeats main
        ("save cost", "save cost", ("save cost", None)),  # third repeats secondary
        ("network", "network", (None, None)),  # all three the same collapse to main
    ],
)
def test_duplicate_reasons_collapse(secondary, third, expected):
    parsed = ModelResponse.model_validate(
        {
            "reasons": {"main": "network", "secondary": secondary, "third": third},
            "call_result": "churn",
        }
    )
    assert (parsed.reasons.secondary, parsed.reasons.third) == expected


def test_out_of_vocabulary_reason_is_rejected():
    with pytest.raises(ValidationError):
        ModelResponse.model_validate(
            {
                "reasons": {"main": "Contact end", "secondary": None, "third": None},
                "call_result": "churn",
            }
        )


# --------------------------------------------------------------------------------------
# build_output_df
# --------------------------------------------------------------------------------------


def test_output_frame_maps_every_column():
    df = build_output_df(
        [{"file_name": "a.wav", "content": response("network", "save cost", "other")}],
        ["a.wav"],
    )
    assert list(df.columns) == COLUMNS
    assert df.loc[0].to_dict() == {
        "No": 1,
        "Voice File Name": "a.wav",
        "call_sumary_call_result": "churn",
        "reason_main": "network",
        "reason_secondary": "save cost",
        "reason_third": "other",
    }


def test_absent_rank_is_blank_not_the_text_none():
    """OutputSchema coerces via astype(str), so a None would land as the literal "None"."""
    df = build_output_df(
        [{"file_name": "a.wav", "content": response("network")}],
        ["a.wav"],
    )
    assert df.loc[0, "reason_secondary"] == ""
    assert df.loc[0, "reason_third"] == ""


def test_unanswered_file_still_gets_a_row():
    """Blank, not a sentinel: the scorers compare raw cell text."""
    df = build_output_df(
        [{"file_name": "a.wav", "content": response("network")}],
        ["a.wav", "b.wav"],
    )
    assert len(df) == 2
    assert df.loc[1, "Voice File Name"] == "b.wav"
    assert pd.isna(df.loc[1, "reason_main"])


def test_empty_items_yield_a_valid_frame():
    df = build_output_df([], [])
    assert list(df.columns) == COLUMNS
    assert len(df) == 0


# --------------------------------------------------------------------------------------
# The pre-flight
# --------------------------------------------------------------------------------------


def test_preflight_matches_on_the_stem_across_extensions():
    """The mnp ground truth lists bare ids; the GCS objects carry .wav."""
    counts = gt_source_overlap(["111", "222"], ["prefix/111.wav", "prefix/222.wav"])
    assert counts == {
        "source_files": 2,
        "gt_rows": 2,
        "matched": 2,
        "gt_without_source": 0,
        "source_without_gt": 0,
    }


def test_preflight_reports_zero_overlap_for_a_wrong_corpus():
    """This is the count run() aborts on, before a single transcription is paid for."""
    counts = gt_source_overlap(["111", "222"], ["prefix/qa_a.wav", "prefix/qa_b.wav"])
    assert counts["matched"] == 0
    assert counts["gt_without_source"] == 2
    assert counts["source_without_gt"] == 2


def test_preflight_ignores_blank_ground_truth_names():
    counts = gt_source_overlap(["111", "", "   "], ["prefix/111.wav"])
    assert counts["gt_rows"] == 1
    assert counts["matched"] == 1


def test_preflight_reports_a_partial_overlap_without_aborting():
    counts = gt_source_overlap(["111", "222", "333"], ["prefix/111.wav"])
    assert counts["matched"] == 1
    assert counts["gt_without_source"] == 2


# --------------------------------------------------------------------------------------
# Scorers
# --------------------------------------------------------------------------------------


def test_join_is_on_the_stem():
    gt = frame([row("2025110913012317105818244684036801")])  # no extension, as the sheet has
    result = frame([row("2025110913012317105818244684036801.wav")])
    merged, counts = cm._merge(gt, result)
    assert len(merged) == 1
    assert counts["gt_only"] == 0
    assert counts["result_only"] == 0


def test_case_and_trailing_space_fold_onto_the_schema_spelling():
    """The mnp ground truth is dirty: Network, save cost with a trailing space."""
    gt = frame([row("a.wav", main="Network", secondary="save cost "), row("b.wav", no=2)])
    result = frame([row("a.wav", main="network", secondary="save cost"), row("b.wav", no=2)])
    reasons = cm.evaluate(gt, result)[cm.BLOCK_REASONS].set_index("Reason")
    # Row b carries the default `network` too, hence 2; the point is that the dirty spellings
    # folded rather than landing in the out-of-vocabulary row.
    assert reasons.loc["network", "TP"] == 2
    assert reasons.loc["save cost", "TP"] == 1
    assert reasons.loc[cm.UNKNOWN_CLASS, "Support"] == 0


def test_grader_typo_is_surfaced_not_corrected():
    """`cutomer reason` is a ground-truth data error; metric code must not silently fix it.

    Nor silently drop it -- which is what the previous design did, quietly costing
    `customer reason` its recall with nothing on the sheet to explain the gap.
    """
    gt = frame([row("a.wav", main="cutomer reason"), row("b.wav", no=2)])
    result = frame([row("a.wav", main="customer reason"), row("b.wav", no=2)])
    reasons = cm.evaluate(gt, result)[cm.BLOCK_REASONS].set_index("Reason")
    assert reasons.loc[cm.UNKNOWN_CLASS, "Support"] == 1
    assert reasons.loc[cm.UNKNOWN_CLASS, "FN"] == 1
    assert reasons.loc["customer reason", "FP"] == 1


def test_rank_permutation_scores_identically():
    """The defect this scorer was rebuilt to fix, pinned on the local side too."""
    gt = frame([row("a.wav", main="network", secondary="save cost", third="other")])
    permuted = frame([row("a.wav", main="other", secondary="network", third="save cost")])
    pd.testing.assert_frame_equal(
        cm.evaluate(gt, gt.copy())[cm.BLOCK_REASONS],
        cm.evaluate(gt, permuted)[cm.BLOCK_REASONS],
    )


def test_a_call_with_no_ground_truth_reason_is_dropped_from_the_reason_topic():
    """Production drops it: a call the graders left ungraded is not evidence about the model."""
    gt = frame([row("a.wav", main="network"), row("b.wav", main="", no=2)])
    blocks = cm.evaluate(gt, gt.copy())
    reasons = blocks[cm.BLOCK_REASONS]
    # One row scored for reasons, but the outcome topic still sees both calls.
    assert reasons.loc[0, "N"] == 1
    assert blocks[cm.BLOCK_OUTCOME].loc[0, "N"] == 2


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
