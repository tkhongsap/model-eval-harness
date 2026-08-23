"""Tests for the local telesale pipeline: the split registry, the merge, and both scorers.

Everything here runs offline. The four section calls are never made: the tests build each
section's answer directly and push it through the same merge the pipeline uses, which is where
the cross-section rules live and therefore where the interesting failures are.

Two invariants this file exists to hold:

1. **The local schema is deliberately narrower than the google one**, and that narrowing is a
   decision, not drift. It is pinned as an exact field-set difference, so re-widening either
   side is a visible edit here first.
2. **The two result sheets must stay identical in shape.** The responses behind them differ;
   the 41 columns do not. That is the only reason the two dashboards can be compared.
"""

import pathlib

import pandas as pd
import pytest
from pydantic import ValidationError

from src.google_model.sentiment_telesale import google_confusion_matrix as gcm
from src.google_model.sentiment_telesale.schema.model_response import (
    ModelResponse as GoogleModelResponse,
)
from src.google_model.sentiment_telesale.schema.output_schema import (
    OutputSchema as GoogleOutputSchema,
)
from src.google_model.sentiment_telesale.schema.sheet_columns import (
    ALL_COLUMNS,
    FAMILIES,
    FLAG_LABELS,
    SHEET_COLUMNS,
    SUB_CATEGORIES,
    flatten,
)
from src.local_model.sentiment_telesale import internal_confusion_matrix as cm
from src.local_model.sentiment_telesale.internal_asr_llm_split_output import (
    Config,
    build_output_df,
)
from src.local_model.sentiment_telesale.schema.model_response import (
    ModelResponse,
    collect_coercions,
)
from src.local_model.sentiment_telesale.schema.output_schema import OutputSchema
from src.local_model.sentiment_telesale.schema.split_model_response import (
    EXPECTED_PROMPT_FILES,
    SECTIONS,
    SPREAD_KEYS,
)

PROMPT_DIR = pathlib.Path("src/local_model/sentiment_telesale/prompt")

GOOGLE_ONLY_FIELDS = {
    "transcript",
    "campaign_name",
    "campaign_ratio",
    "agent_strength",
    "agent_weakness",
    "sales_performance",
    "customer_insight",
}


def flags(names, value=True):
    return dict.fromkeys(names, value)


def section_payloads(**overrides):
    """One valid answer per section, as the four calls would return them."""
    payloads = {
        "operations": {
            "call_status": "Completed",
            "operations_and_professionalism": {
                "call_opening": flags(
                    [
                        "proper_identification",
                        "call_origin_disclosure",
                        "call_consent_before_engagement",
                    ]
                ),
                "customer_identity_verification": {
                    "customer_verification": None,
                    "invalid_verification": None,
                    "missing_verification": None,
                },
                "language_and_tone": flags(["behavioral_violation", "clarity", "delivery_pace"]),
                "active_listening": flags(
                    ["no_interruption", "correct_understanding", "acknowledgement_paraphrasing"]
                ),
                "call_closing": flags(
                    ["confirm_resolution", "courteous_ending", "smooth_closing"]
                ),
            },
        },
        "sales_effectiveness": {
            "customer_needs_analysis": flags(["usage_based_analysis", "benefit_highlight"]),
            "offer_presentation_quality": flags(
                ["clarity_of_explanation", "customer_benefit_highlight"]
            ),
            "effective_objection_handling": flags(["failure_to_listen", "confrontational_tone"]),
            "sales_closing_attempt": flags(
                ["value_based_closing", "unclear_separation", "inadequate_addon_disclosure"]
            ),
            "cross_sell_upsell": {
                "missed_crosssell_upsell": None,
                "unclear_addon_separation_crosssell": None,
                "inadequate_addon_disclosure_crosssell": None,
            },
        },
        "customer_experience": {
            "positive_customer_experience": flags(
                [
                    "failure_to_demonstrate_empathy",
                    "deflecting_responsibility",
                    "escalates_customer_emotion",
                ]
            ),
            "clarity_of_communication": flags(
                [
                    "overly_technical_language",
                    "fails_to_clarify_limitations",
                    "no_adjustment_for_complexity",
                ]
            ),
            "building_trust": flags(
                [
                    "provides_unclear_information",
                    "provides_misleading_information",
                    "fails_to_connect_value",
                ]
            ),
        },
        "compliance": {
            "compliance": flags(
                [
                    "data_privacy_compliance",
                    "sales_integrity_compliance",
                    "professional_conduct_compliance",
                ]
            )
        },
    }
    payloads.update(overrides)
    return payloads


def merge_with_coercions(**overrides):
    """Reproduce the pipeline's merge, coercion bookkeeping included.

    The bookkeeping is the subtle part and the reason this helper mirrors the pipeline rather
    than shortcutting to ``ModelResponse.model_validate``: the coercion record is a
    ``PrivateAttr``, so ``model_dump()`` drops it, and re-reading it off the merged response
    finds nothing -- the merge re-validates data the section validators have already repaired.
    A section's coercions exist only at section time.
    """
    payloads = section_payloads(**overrides)
    merged: dict = {}
    coercions: list[str] = []
    for section in SECTIONS:
        parsed = section.model.model_validate(payloads[section.key])
        coercions.extend(collect_coercions(parsed))
        dump = parsed.model_dump(mode="json")
        if section.key in SPREAD_KEYS:
            merged.update(dump)
        else:
            merged[section.key] = dump
    response = ModelResponse.model_validate(merged)
    coercions.extend(collect_coercions(response))
    return response, coercions


def merge(**overrides):
    """The merged response alone, for tests that do not care about coercions."""
    return merge_with_coercions(**overrides)[0]


def row(name, no=1, value="T"):
    return {"No": no, "Voice File Name": name, **dict.fromkeys(SHEET_COLUMNS, value)}


def frame(rows):
    return pd.DataFrame(rows, columns=list(ALL_COLUMNS))


# --------------------------------------------------------------------------------------
# The deliberate narrowing
# --------------------------------------------------------------------------------------


def test_the_local_schema_is_deliberately_narrower_than_the_google_copy():
    """The two packages no longer share a shape, and that divergence is a decision.

    The google side runs the full production contract because the point of that benchmark is
    to measure the call production actually issues. This side runs against a slow local
    endpoint over an ASR transcript, so it asks only for what the sheet scores.

    Pinned as an exact set rather than a subset check, so re-widening either side is a visible
    edit here first.
    """
    google_only = set(GoogleModelResponse.model_fields) - set(ModelResponse.model_fields)
    assert google_only == GOOGLE_ONLY_FIELDS
    assert set(ModelResponse.model_fields) < set(GoogleModelResponse.model_fields)


def test_no_support_detail_survives_anywhere_in_the_local_schema():
    """13 free-text Thai fields, none of which reach the workbook -- the single largest
    output-token saving available on this pipeline."""
    schema = ModelResponse.model_json_schema()
    for definition in schema["$defs"].values():
        assert "support_detail" not in definition.get("properties", {})


def test_the_two_output_sheets_stay_identical():
    """What must NOT diverge. The responses differ; the 41 columns are what make the google and
    local dashboards comparable at all."""
    assert list(OutputSchema.to_schema().columns) == list(GoogleOutputSchema.to_schema().columns)
    assert list(OutputSchema.to_schema().columns) == list(ALL_COLUMNS)


def test_shared_criteria_are_described_in_identical_words():
    """A criterion asked differently on the two sides would make the delta between the two
    dashboards about the wording rather than about the model."""
    def leaves(model, prefix=()):
        out = {}
        for name, info in model.model_fields.items():
            annotation = info.annotation
            if hasattr(annotation, "model_fields"):
                out.update(leaves(annotation, (*prefix, name)))
            else:
                out[(*prefix, name)] = info.description
        return out

    local, google = leaves(ModelResponse), leaves(GoogleModelResponse)
    shared = set(local) & set(google)
    assert len(shared) >= 40
    for key in shared:
        assert local[key] == google[key], key


def test_every_field_is_required_with_no_defaults():
    schema = ModelResponse.model_json_schema()
    assert set(schema["required"]) == set(schema["properties"])
    for name, definition in schema["$defs"].items():
        if definition.get("type") == "object":
            assert set(definition["required"]) == set(definition["properties"]), name


# --------------------------------------------------------------------------------------
# The split registry and the merge
# --------------------------------------------------------------------------------------


def test_four_sections_one_per_scored_family():
    assert [section.key for section in SECTIONS] == [
        "operations",
        "sales_effectiveness",
        "customer_experience",
        "compliance",
    ]
    assert len(SECTIONS) == len(FAMILIES)


def test_the_sections_merge_into_a_complete_response():
    """The merge is where the pipeline stops being four answers and becomes one call's row."""
    parsed = merge()
    assert set(parsed.model_dump()) == set(ModelResponse.model_fields)
    assert parsed.call_status == "Completed"


def test_the_merged_response_fills_every_sheet_cell():
    cells = flatten(merge().model_dump(mode="json"))
    assert len(cells) == 39
    assert set(cells) == set(SHEET_COLUMNS)
    assert set(cells.values()) <= set(FLAG_LABELS)


def test_call_status_rides_with_operations_rather_than_its_own_request():
    """A fifth round-trip for one enum would be poor value on an endpoint this slow."""
    operations = next(section for section in SECTIONS if section.key == "operations")
    assert "call_status" in operations.model.model_fields
    assert operations.key in SPREAD_KEYS


def test_a_cross_section_rule_runs_at_merge_time():
    """``data_privacy_compliance`` is decided by the *operations* section's verification flags,
    but generated by the *compliance* section. Nothing below the merge can see both.
    """
    payloads = section_payloads()
    payloads["operations"]["operations_and_professionalism"][
        "customer_identity_verification"
    ] = {
        "customer_verification": False,
        "invalid_verification": None,
        "missing_verification": None,
    }
    parsed = merge(operations=payloads["operations"])
    assert parsed.compliance.compliance.data_privacy_compliance is False


def test_an_illegal_verification_combination_is_coerced_and_counted():
    payloads = section_payloads()
    payloads["operations"]["operations_and_professionalism"][
        "customer_identity_verification"
    ] = {
        "customer_verification": True,
        "invalid_verification": True,
        "missing_verification": None,
    }
    parsed, coercions = merge_with_coercions(operations=payloads["operations"])
    block = parsed.operations_and_professionalism.customer_identity_verification
    assert (block.invalid_verification, block.missing_verification) == (True, True)
    assert "customer_identity_verification.missing_verification" in coercions


def test_a_section_coercion_survives_the_merge():
    """The regression this guards: ``model_dump()`` drops the ``PrivateAttr`` the coercion is
    recorded on, so a pipeline that read the record off the merged response instead of off each
    section would report a clean run every time -- and the Matrix sheet's ``Coerced Fields``
    column would be empty exactly when it mattered.
    """
    payloads = section_payloads()
    payloads["operations"]["operations_and_professionalism"][
        "customer_identity_verification"
    ] = {
        "customer_verification": True,
        "invalid_verification": True,
        "missing_verification": None,
    }
    parsed, coercions = merge_with_coercions(operations=payloads["operations"])
    assert coercions != []
    # The merged response has already been repaired, so it records nothing on its own.
    assert collect_coercions(parsed) == []


def test_a_clean_answer_records_no_coercion():
    assert merge_with_coercions()[1] == []


def test_an_invented_call_status_still_fails_the_section():
    """Coercion repairs a wrong *combination*, never an invented label."""
    payloads = section_payloads()
    payloads["operations"]["call_status"] = "Hung up"
    with pytest.raises(ValidationError):
        merge(operations=payloads["operations"])


# --------------------------------------------------------------------------------------
# Prompts
# --------------------------------------------------------------------------------------


def test_every_section_has_a_prompt_file_on_disk():
    """``run()`` aborts before any spend when one is missing; this catches it before the run."""
    for name in EXPECTED_PROMPT_FILES:
        path = PROMPT_DIR / name
        assert path.exists(), name
        assert path.read_text(encoding="utf-8").strip(), name


def test_each_prompt_names_every_field_its_section_must_produce():
    """The drift guard. The production prompt and schema disagree on four criterion names, so
    each prompt carries a mapping table; this is what proves the table covers everything.
    """

    def leaf_names(model):
        names = []
        for name, info in model.model_fields.items():
            annotation = info.annotation
            if hasattr(annotation, "model_fields"):
                names.extend(leaf_names(annotation))
            else:
                names.append(name)
        return names

    for section in SECTIONS:
        text = (PROMPT_DIR / section.prompt_file).read_text(encoding="utf-8")
        missing = [name for name in leaf_names(section.model) if name not in text]
        assert missing == [], (section.key, missing)


def test_no_prompt_asks_for_a_field_this_contract_cannot_emit():
    """A line the response schema then forbids is the worst kind of prompt line. The scope
    block names the dropped fields in order to forbid them, so only the body is checked."""
    for section in SECTIONS:
        text = (PROMPT_DIR / section.prompt_file).read_text(encoding="utf-8")
        body = text.split("## 4. ", 1)[1]
        for dropped in sorted(GOOGLE_ONLY_FIELDS - {"transcript"}):
            assert dropped not in body, (section.prompt_file, dropped)


def test_the_topic_prompts_are_far_smaller_than_the_production_prompt():
    """The whole reason this pipeline is split: the endpoint slows sharply on a long context."""
    production = pathlib.Path(
        "src/google_model/sentiment_telesale/prompt/system_prompt.txt"
    ).stat().st_size
    largest = max((PROMPT_DIR / name).stat().st_size for name in EXPECTED_PROMPT_FILES)
    assert largest < production / 3


def test_the_local_prompts_describe_a_transcript_not_audio():
    """The mirror of the google prompt's rewrite: this pipeline really is handed a transcript,
    produced by its own ASR and labelling legs."""
    for name in EXPECTED_PROMPT_FILES:
        text = (PROMPT_DIR / name).read_text(encoding="utf-8")
        assert "You will receive a **call transcript**" in text
        assert "audio recording" not in text


# --------------------------------------------------------------------------------------
# Output frame and configuration
# --------------------------------------------------------------------------------------


def test_build_output_df_produces_the_ground_truth_columns_in_order():
    content = merge().model_dump(mode="json")
    df = build_output_df([{"file_name": "a_OUT.wav", "content": content}], ["a_OUT.wav"])
    assert list(df.columns) == list(ALL_COLUMNS)
    assert df.loc[0, "op_call_opening_proper_identification"] == "T"
    assert df.loc[0, "op_customer_identity_verification_customer_verification"] == "N/A"


def test_the_pipeline_writes_to_its_own_folders():
    """The clone this replaced pointed at the QA package's folders."""
    config = Config()
    assert config.src_file == "/poc_internal_model_migration/voicefiles_telesale"
    assert config.dest_file == (
        "/poc_internal_model_migration/voicefiles_telesale_internal_split_output"
    )
    assert config.gcs_payload_path.startswith(
        "poc_internal_model/sentiment_telesale/internal_split/"
    )
    assert config.gt_sheet_name == "Voice_telesale - Groundtruth"
    assert config.output_sheet_name == "Voice_telesale - Internal Model Result"


def test_run_accepts_a_config():
    """The clone's ``def run():`` would TypeError from the menu, which dispatches
    ``action.run(config)``."""
    import inspect

    from src.local_model.sentiment_telesale.internal_asr_llm_split_output import run

    assert list(inspect.signature(run).parameters) == ["config"]


def test_the_matrix_sheet_carries_the_coercion_column():
    from src.local_model.sentiment_telesale.schema.metrics_schema import MetricsSchema

    assert "Coerced Fields" in list(MetricsSchema.to_schema().columns)


# --------------------------------------------------------------------------------------
# Scorers
# --------------------------------------------------------------------------------------


def test_ground_truth_against_itself_scores_one_everywhere():
    gt = frame([row("a_OUT.wav"), row("b_OUT.wav", no=2, value="F")])
    blocks = cm.evaluate(gt.copy(), gt.copy())
    for family in FAMILIES:
        block = blocks[cm.BLOCK_BY_FAMILY[family]]
        assert (block["Accuracy"] == 1.0).all(), family


def test_the_local_scorer_reads_the_local_result_sheet():
    config = cm.Config()
    assert config.result_sheet_name == "Voice_telesale - Internal Model Result"
    assert config.dest_file == (
        "/poc_internal_model_migration/voicefiles_telesale_internal_split_output"
    )


def test_the_scorer_grain_matches_the_google_copy():
    """Both dashboards must group the same 39 criteria the same way, or their Overall and
    By sub-category blocks are not comparable row by row."""
    assert cm.BLOCK_BY_FAMILY == gcm.BLOCK_BY_FAMILY
    assert cm.COMPARE_BY_FAMILY == gcm.COMPARE_BY_FAMILY
    assert cm.AVERAGE_KINDS == gcm.AVERAGE_KINDS
    assert cm.BLOCK_COVERAGE == gcm.BLOCK_COVERAGE
    assert cm.BLOCK_BY_SUBCATEGORY == gcm.BLOCK_BY_SUBCATEGORY
    # The sub-category table is imported from one shared module, so this asserts the import
    # rather than a copy -- which is exactly the property worth pinning.
    assert len(SUB_CATEGORIES) == 14


def test_scoring_agrees_with_the_google_scorer_on_identical_input():
    """The ports are equivalent in behaviour, not just in shape.

    This is what keeps the two dashboards comparable: the families run different models, but
    they must compute the same numbers over the same criteria from the same frames. Re-pointed
    at the confusion-matrix pair when the exact-match modules were deleted; it is the reason the
    test survived that deletion rather than going with it.
    """
    gt = frame(
        [
            row("a_OUT.wav"),
            row("b_OUT.wav", no=2, value="F"),
            row("c_OUT.wav", no=3, value="N/A"),
        ]
    )
    result = gt.copy()
    result.loc[1, list(SHEET_COLUMNS)] = "T"

    mine = cm.evaluate(gt.copy(), result.copy())
    theirs = gcm.evaluate(gt.copy(), result.copy())
    assert mine.keys() == theirs.keys()
    for title in mine:
        if title == cm.BLOCK_LEGEND:
            continue  # prose only, and it deliberately says "the model" instead of "Google"
        pd.testing.assert_frame_equal(mine[title], theirs[title])

    ours = cm.compare_rows(gt.copy(), result.copy())
    others = gcm.compare_rows(gt.copy(), result.copy())
    assert ours.keys() == others.keys()
    for key in ours:
        pd.testing.assert_frame_equal(ours[key], others[key])


def test_the_local_legend_says_the_model_not_google():
    """The one deliberate difference between the two modules' text."""
    legend = dict(cm.LEGEND)["Accuracy"]
    assert "the model" in legend
    assert "Google" not in legend


def stub_embedder(texts):
    """Deterministic vectors, so cosine is reproducible without touching Vertex."""
    return [[float(len(text) % 7), 1.0, 2.0] for text in texts]


def test_no_embedder_means_no_transcript_block():
    gt = frame([row("a_OUT.wav")])
    assert cm.BLOCK_TRANSCRIPT not in cm.evaluate(gt.copy(), gt.copy())


def test_a_perfect_transcript_reads_cer_zero():
    gt = frame([row("a_OUT.wav")])
    blocks = cm.evaluate(
        gt.copy(),
        gt.copy(),
        embedder=stub_embedder,
        gt_transcripts={"a_OUT": "Agent: hello"},
        pred_transcripts={"a_OUT": "Agent: hello"},
    )
    values = blocks[cm.BLOCK_TRANSCRIPT].set_index("Metric")["Value"]
    assert values["Mean CER"] == 0.0
    assert values["Scored"] == 1
