"""Tests for the telesale evaluation pipeline: schema, prompt, output frame, and both scorers.

The scoring path is pure -- no SharePoint, no Vertex -- so every test below runs offline
against fabricated frames. The transcript block is the one part that needs an embedder, and
that is injected as a stub; without one, ``evaluate`` omits the block entirely.

The schema here is the **full production contract**, merged from the eight files production
runs as a package. Only 39 of its boolean leaves reach the sheet, and the tests hold that line
in both directions: the wide response must decode, and the narrow sheet must not widen.

Three things these tests exist to protect, in order of how quietly they would break:

1. **No field may acquire a default.** Every field in the production sources had one, and a
   default drops its field out of ``required`` -- OpenAI rejects the schema, and Vertex is free
   to omit the field entirely and still look valid.
2. **The 39 sheet columns must keep mapping onto real schema leaves.** The mapping is a
   bijection today; a rename on either side that breaks it must fail here rather than produce a
   column of blanks.
3. **The softened validators must record what they rewrote.** They exist so a malformed answer
   costs a field rather than a whole call, and they are honest only while the coercion is
   counted.
"""

import json
import pathlib
from pathlib import Path

import pandas as pd
import pytest
from pydantic import ValidationError

from src.google_model.sentiment_telesale import google_confusion_matrix as cm
from src.google_model.sentiment_telesale.google_output import Config, build_output_df
from src.google_model.sentiment_telesale.schema.input_gt_schema import InputGTSchema
from src.google_model.sentiment_telesale.schema.model_response import (
    TRANSCRIPT_DESCRIPTION,
    ModelResponse,
    SchemaHelper,
    collect_coercions,
)
from src.google_model.sentiment_telesale.schema.output_schema import OutputSchema
from src.google_model.sentiment_telesale.schema.sheet_columns import (
    ALL_COLUMNS,
    COLUMN_PATHS,
    FAMILIES,
    FLAG_LABELS,
    SHEET_COLUMNS,
    SUB_CATEGORIES,
    SUB_CATEGORY_COLUMNS,
    SUB_CATEGORY_TITLES,
    flatten,
    normalise_gt_flags,
)

TOP_LEVEL_FIELDS = [
    "transcript",
    "campaign_name",
    "campaign_ratio",
    "call_status",
    "operations_and_professionalism",
    "sales_effectiveness",
    "customer_experience",
    "compliance",
    "agent_strength",
    "agent_weakness",
    "sales_performance",
    "customer_insight",
]


def flags(names, value=True):
    return dict.fromkeys(names, value)


def payload(**overrides):
    """A complete, valid model answer. Overrides replace any top-level field."""
    base = {
        "transcript": "Agent: hello\nCustomer: hi",
        "campaign_name": "Mobile Upsell",
        "campaign_ratio": {"main": 0.756, "other": 0.244},
        "call_status": "Completed",
        "operations_and_professionalism": {
            "call_opening": {
                **flags(
                    [
                        "proper_identification",
                        "call_origin_disclosure",
                        "call_consent_before_engagement",
                    ]
                ),
                "support_detail": "ok",
            },
            "customer_identity_verification": {
                "customer_verification": None,
                "invalid_verification": None,
                "missing_verification": None,
                "support_detail": "no sale",
            },
            "language_and_tone": {
                **flags(["behavioral_violation", "clarity", "delivery_pace"]),
                "support_detail": "ok",
            },
            "active_listening": {
                **flags(
                    ["no_interruption", "correct_understanding", "acknowledgement_paraphrasing"]
                ),
                "support_detail": "ok",
            },
            "call_closing": {
                **flags(["confirm_resolution", "courteous_ending", "smooth_closing"]),
                "support_detail": "ok",
            },
        },
        "sales_effectiveness": {
            "customer_needs_analysis": {
                **flags(["usage_based_analysis", "benefit_highlight"]),
                "support_detail": "ok",
            },
            "offer_presentation_quality": {
                **flags(["clarity_of_explanation", "customer_benefit_highlight"]),
                "support_detail": "ok",
            },
            "effective_objection_handling": {
                **flags(["failure_to_listen", "confrontational_tone"]),
                "support_detail": "ok",
            },
            "sales_closing_attempt": {
                **flags(
                    [
                        "value_based_closing",
                        "unclear_separation",
                        "inadequate_addon_disclosure",
                    ]
                ),
                "support_detail": "ok",
            },
            "cross_sell_upsell": {
                "missed_crosssell_upsell": None,
                "unclear_addon_separation_crosssell": None,
                "inadequate_addon_disclosure_crosssell": None,
                "support_detail": "no opportunity",
            },
        },
        "customer_experience": {
            "positive_customer_experience": {
                **flags(
                    [
                        "failure_to_demonstrate_empathy",
                        "deflecting_responsibility",
                        "escalates_customer_emotion",
                    ]
                ),
                "support_detail": "ok",
            },
            "clarity_of_communication": {
                **flags(
                    [
                        "overly_technical_language",
                        "fails_to_clarify_limitations",
                        "no_adjustment_for_complexity",
                    ]
                ),
                "support_detail": "ok",
            },
            "building_trust": {
                **flags(
                    [
                        "provides_unclear_information",
                        "provides_misleading_information",
                        "fails_to_connect_value",
                    ]
                ),
                "support_detail": "ok",
            },
        },
        "compliance": {
            "compliance": {
                **flags(
                    [
                        "data_privacy_compliance",
                        "sales_integrity_compliance",
                        "professional_conduct_compliance",
                    ]
                ),
                "support_detail": "ok",
            }
        },
        "agent_strength": "strong",
        "agent_weakness": "weak",
        "sales_performance": {
            "main_package_offered": 1,
            "main_package_accepted": 1,
            "upsell_add_on_package_offered": 0,
            "upsell_add_on_package_accepted": 0,
            "crosssell_add_on_product_offered": 0,
            "crosssell_add_on_product_accepted": 0,
            "main_and_upsell_add_on_product_offered_list": ["Mobile"],
            "crosssell_add_on_product_offered_list": [],
        },
        "customer_insight": {
            "rejection_reason": None,
            "network_issue": None,
            "churn_risk_indicator": 30,
            "customer_sentiment_emotional": "Neutral",
        },
    }
    base.update(overrides)
    return base


def row(name, no=1, value="T"):
    """One ground-truth row with every criterion set to ``value``."""
    return {"No": no, "Voice File Name": name, **dict.fromkeys(SHEET_COLUMNS, value)}


def frame(rows):
    return pd.DataFrame(rows, columns=list(ALL_COLUMNS))


# --------------------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------------------


def test_field_order_is_the_prompt_order():
    """Generation follows properties order: the transcript is written before anything is judged.

    Then the production contract's own order -- campaign, status, the four judged categories,
    the summary, and last the counts.
    """
    assert list(ModelResponse.model_json_schema()["properties"]) == TOP_LEVEL_FIELDS


def test_every_property_is_required_at_every_level():
    """The one the production defaults would have broken.

    Every field in the eight source files carried a default (``Field(None, ...)``,
    ``default=0``, ``default_factory=list``). A default drops its field out of ``required``,
    which OpenAI strict mode rejects and which lets Vertex omit the field and still validate.
    """
    schema = ModelResponse.model_json_schema()
    assert set(schema["required"]) == set(schema["properties"])
    for name, definition in schema["$defs"].items():
        if definition.get("type") == "object":
            assert set(definition["required"]) == set(definition["properties"]), name


def test_both_backend_renderings_succeed():
    """``raise_error_on_unsupported_field=True``, so this fails loudly on a bad field type.

    The nullable booleans and the ``list[Literal[...]]`` are the fields most likely to trip the
    OpenAPI-subset conversion.
    """
    assert SchemaHelper.vertex_schema().required == TOP_LEVEL_FIELDS
    openai = SchemaHelper.openai_response_format()["json_schema"]
    assert openai["strict"] is True
    assert set(openai["schema"]["required"]) == set(openai["schema"]["properties"])


def test_check_list_is_absent():
    """Production declares it "Mock field to be overridden in subclasses" and the prompt never
    specifies its shape, so asking for it would spend tokens on an unspecified answer."""
    assert "check_list" not in ModelResponse.model_json_schema()["properties"]


def test_transcript_spec_matches_the_other_pipelines_word_for_word():
    """Four pipelines ask for a transcript; a divergent spec makes their CER incomparable."""
    from src.google_model.sentiment.schema.model_response import (
        TRANSCRIPT_DESCRIPTION as QA_SPEC,
    )

    assert TRANSCRIPT_DESCRIPTION == QA_SPEC


def test_every_description_keeps_its_prompt_tag():
    """Each description opens ``[System Prompt: X.Y.Z]``, tying the field to a prompt section.

    That tag is how a reader of either artefact finds the other, and it is the only thing
    connecting a schema key to the differently-named heading that defines it.
    """
    schema = ModelResponse.model_json_schema()
    tagged = 0
    for definition in schema["$defs"].values():
        for name, prop in definition.get("properties", {}).items():
            if name == "support_detail":
                continue
            description = prop.get("description", "")
            if description.startswith("[System Prompt:"):
                tagged += 1
    assert tagged >= 39


# --------------------------------------------------------------------------------------
# Validators
# --------------------------------------------------------------------------------------


def test_a_clean_answer_records_no_coercion():
    parsed = ModelResponse.model_validate(payload())
    assert collect_coercions(parsed) == []


def test_campaign_ratio_is_rounded_not_rejected():
    """The prompt asks for two shares summing to 1.0 but nothing enforces it; rejecting 0.99
    would cost a whole call over a field nothing scores."""
    parsed = ModelResponse.model_validate(payload())
    assert (parsed.campaign_ratio.main, parsed.campaign_ratio.other) == (0.76, 0.24)


@pytest.mark.parametrize(
    ("invalid", "missing", "expected"),
    [
        (None, None, (None, None)),
        (None, True, (None, True)),
        (True, True, (True, True)),
        (False, False, (False, False)),
    ],
)
def test_the_four_legal_verification_combinations_pass_untouched(invalid, missing, expected):
    ops = payload()["operations_and_professionalism"]
    ops["customer_identity_verification"] = {
        "customer_verification": True,
        "invalid_verification": invalid,
        "missing_verification": missing,
        "support_detail": "asked",
    }
    parsed = ModelResponse.model_validate(payload(operations_and_professionalism=ops))
    block = parsed.operations_and_professionalism.customer_identity_verification
    assert (block.invalid_verification, block.missing_verification) == expected
    assert collect_coercions(parsed) == []


@pytest.mark.parametrize(
    ("invalid", "missing", "expected_missing"),
    [(None, False, True), (True, None, True), (True, False, True), (False, True, False)],
)
def test_an_illegal_verification_combination_is_coerced_and_counted(
    invalid, missing, expected_missing
):
    """Production raises here. A raise costs all 39 scored flags of a call out of only 26, with
    no retry on the batch path, so the combination is repaired -- and recorded, because a
    repaired row that looks identical to a compliant one is the thing to avoid.
    """
    ops = payload()["operations_and_professionalism"]
    ops["customer_identity_verification"] = {
        "customer_verification": True,
        "invalid_verification": invalid,
        "missing_verification": missing,
        "support_detail": "asked",
    }
    parsed = ModelResponse.model_validate(payload(operations_and_professionalism=ops))
    block = parsed.operations_and_professionalism.customer_identity_verification
    assert block.missing_verification is expected_missing
    assert collect_coercions(parsed) == [
        "customer_identity_verification.missing_verification"
    ]


def test_no_verification_attempt_forces_data_privacy_compliance_false():
    """A rule overwriting a *scored* column with something other than the model's own answer.

    Production does this and the sheet was labelled under the same rule, so it stays -- but it
    is the reason a compliance verdict here is not purely a model output.
    """
    ops = payload()["operations_and_professionalism"]
    ops["customer_identity_verification"] = {
        "customer_verification": False,
        "invalid_verification": None,
        "missing_verification": None,
        "support_detail": "never asked",
    }
    parsed = ModelResponse.model_validate(payload(operations_and_professionalism=ops))
    assert parsed.compliance.compliance.data_privacy_compliance is False


def test_no_crosssell_attempt_zeroes_the_crosssell_counts():
    se = payload()["sales_effectiveness"]
    se["cross_sell_upsell"]["missed_crosssell_upsell"] = None
    performance = {
        **payload()["sales_performance"],
        "crosssell_add_on_product_offered": 3,
        "crosssell_add_on_product_accepted": 1,
        "crosssell_add_on_product_offered_list": ["TOL"],
    }
    parsed = ModelResponse.model_validate(
        payload(sales_effectiveness=se, sales_performance=performance)
    )
    assert parsed.sales_performance.crosssell_add_on_product_offered == 0
    assert parsed.sales_performance.crosssell_add_on_product_offered_list == []


def test_a_dangling_crosssell_child_is_coerced_and_counted():
    se = payload()["sales_effectiveness"]
    se["cross_sell_upsell"] = {
        "missed_crosssell_upsell": True,
        "unclear_addon_separation_crosssell": None,
        "inadequate_addon_disclosure_crosssell": True,
        "support_detail": "offered",
    }
    parsed = ModelResponse.model_validate(payload(sales_effectiveness=se))
    assert parsed.sales_effectiveness.cross_sell_upsell.unclear_addon_separation_crosssell is True
    assert collect_coercions(parsed) == [
        "cross_sell_upsell.unclear_addon_separation_crosssell"
    ]


def test_churn_risk_is_clamped_not_rejected():
    insight = {**payload()["customer_insight"], "churn_risk_indicator": 140}
    parsed = ModelResponse.model_validate(payload(customer_insight=insight))
    assert parsed.customer_insight.churn_risk_indicator == 100
    assert collect_coercions(parsed) == ["customer_insight.churn_risk_indicator"]


def test_product_dedup_is_order_stable():
    """``list(set(...))`` -- what production uses -- reorders unpredictably between processes,
    because CPython randomises string hashing. That writes a different cell on every run."""
    performance = {
        **payload()["sales_performance"],
        "main_and_upsell_add_on_product_offered_list": ["TVS", "Mobile", "TOL", "Mobile"],
    }
    orders = {
        tuple(
            ModelResponse.model_validate(
                payload(sales_performance=performance)
            ).sales_performance.main_and_upsell_add_on_product_offered_list
        )
        for _ in range(5)
    }
    assert orders == {("TVS", "Mobile", "TOL")}


def test_a_crosssell_category_shared_with_the_main_list_is_dropped():
    """The main package's category defines the upsell boundary, so anything sharing it is by
    definition not a cross-sell -- and once the list empties, its counts must go to 0 too."""
    se = payload()["sales_effectiveness"]
    se["cross_sell_upsell"] = {
        "missed_crosssell_upsell": True,
        "unclear_addon_separation_crosssell": True,
        "inadequate_addon_disclosure_crosssell": True,
        "support_detail": "offered",
    }
    performance = {
        **payload()["sales_performance"],
        "crosssell_add_on_product_offered": 2,
        "crosssell_add_on_product_offered_list": ["Mobile"],
    }
    parsed = ModelResponse.model_validate(
        payload(sales_effectiveness=se, sales_performance=performance)
    )
    assert parsed.sales_performance.crosssell_add_on_product_offered_list == []
    assert parsed.sales_performance.crosssell_add_on_product_offered == 0


def test_an_out_of_vocabulary_call_status_is_rejected():
    """Coercion is for the fields whose *combination* is wrong, not for an invented label."""
    with pytest.raises(ValidationError):
        ModelResponse.model_validate(payload(call_status="Hung up"))


def test_the_response_revalidates_its_own_dump():
    parsed = ModelResponse.model_validate(payload())
    assert ModelResponse.model_validate(parsed.model_dump(mode="json")) is not None


# --------------------------------------------------------------------------------------
# Sheet contract
# --------------------------------------------------------------------------------------


def test_every_sheet_column_maps_onto_a_real_schema_leaf():
    """The mapping is a bijection, and it has to stay one: a rename on either side that breaks
    it would silently produce a column of blanks rather than an error."""
    fields = ModelResponse.model_fields
    for column, (top, sub, leaf) in COLUMN_PATHS.items():
        block = fields[top].annotation.model_fields[sub].annotation
        assert leaf in block.model_fields, column


def test_every_judged_leaf_is_claimed_by_exactly_one_column():
    """The other direction: a criterion the schema decodes but the sheet never scores would be
    generated and then thrown away."""
    fields = ModelResponse.model_fields
    claimed = set(COLUMN_PATHS.values())
    for top in ("operations_and_professionalism", "sales_effectiveness", "customer_experience",
                "compliance"):
        block = fields[top].annotation
        for sub in block.model_fields:
            leaf_model = block.model_fields[sub].annotation
            for leaf in leaf_model.model_fields:
                if leaf == "support_detail":
                    continue
                assert (top, sub, leaf) in claimed, f"{top}.{sub}.{leaf}"


def test_the_families_partition_the_scored_columns():
    assert sum(len(columns) for columns in FAMILIES.values()) == len(SHEET_COLUMNS)
    assert {c for columns in FAMILIES.values() for c in columns} == set(SHEET_COLUMNS)
    assert {k: len(v) for k, v in FAMILIES.items()} == {"OP": 15, "SE": 12, "CX": 9, "CP": 3}


def test_flatten_renders_none_as_the_na_label_never_as_the_text_none():
    """``coerce=True`` coerces via ``astype(str)``, so a ``None`` reaching the frame would land
    in the cell as the literal word "None"."""
    cells = flatten(ModelResponse.model_validate(payload()).model_dump(mode="json"))
    assert len(cells) == 39
    assert set(cells.values()) <= set(FLAG_LABELS)
    assert cells["op_customer_identity_verification_customer_verification"] == "N/A"
    assert cells["op_call_opening_proper_identification"] == "T"


def test_the_two_pandera_schemas_describe_the_same_sheet():
    assert list(InputGTSchema.to_schema().columns) == list(ALL_COLUMNS)
    assert list(OutputSchema.to_schema().columns) == list(ALL_COLUMNS)


def test_normalise_gt_flags_folds_a_blank_to_na():
    blank = frame([row("a_OUT.wav")])
    blank.loc[0, "op_call_opening_proper_identification"] = ""
    folded = normalise_gt_flags(blank)
    assert folded.loc[0, "op_call_opening_proper_identification"] == "N/A"


# --------------------------------------------------------------------------------------
# Output frame
# --------------------------------------------------------------------------------------


def test_build_output_df_produces_the_ground_truth_columns_in_order():
    content = ModelResponse.model_validate(payload()).model_dump(mode="json")
    df = build_output_df([{"file_name": "a_OUT.wav", "content": content}], ["a_OUT.wav"])
    assert list(df.columns) == list(ALL_COLUMNS)
    assert len(df) == 1
    assert df.loc[0, "op_call_opening_proper_identification"] == "T"


def test_an_unanswered_file_still_gets_a_row():
    """One row per *submitted* file, so 26 files in produce 26 rows out however many the model
    failed on. Which files are blank, and why, is what the Matrix sheet is for."""
    content = ModelResponse.model_validate(payload()).model_dump(mode="json")
    df = build_output_df(
        [{"file_name": "a_OUT.wav", "content": content}], ["a_OUT.wav", "b_OUT.wav"]
    )
    assert len(df) == 2
    assert pd.isna(df.loc[1, "op_call_opening_proper_identification"])


# --------------------------------------------------------------------------------------
# Prompt
# --------------------------------------------------------------------------------------


def test_the_prompt_asks_for_audio_and_a_transcript():
    """The google pipeline is handed a .wav; production's own prompt says it receives a
    transcript. Section 3 has to describe what this pipeline actually sends."""
    text = Path(Config.system_prompt_file).read_text(encoding="utf-8")
    section = text.split("## 3. Expected Input Format", 1)[1].split("## 4.", 1)[0]
    assert "audio recording" in section
    assert "You will receive a **call transcript**" not in section
    assert '"transcript": str' in text


def test_the_prompt_does_not_ask_for_check_list():
    """Dropped from the schema, so naming it as a fifth dimension would ask for an answer
    nothing can receive."""
    text = Path(Config.system_prompt_file).read_text(encoding="utf-8")
    assert "Check List" not in text


def test_the_prompt_names_every_top_level_field_the_schema_decodes():
    text = Path(Config.system_prompt_file).read_text(encoding="utf-8")
    for field in TOP_LEVEL_FIELDS:
        assert field in text, field


def test_the_prompts_example_output_starts_with_the_transcript():
    """Property order is generation order, and the example is the other place the model reads
    that order from."""
    text = Path(Config.system_prompt_file).read_text(encoding="utf-8")
    skeleton = text.split("**Structure/Schema:**", 1)[1]
    keys = [
        line.strip().split('"')[1]
        for line in skeleton.splitlines()
        if line.startswith('  "')
    ]
    assert keys[0] == "transcript"
    assert keys[1] == "campaign_name"


# --------------------------------------------------------------------------------------
# Scorers
# --------------------------------------------------------------------------------------


BENCHMARK = pathlib.Path("resources/AI Benchmark Report.xlsx")
GT_SHEET = "Voice_telesale - Groundtruth"

# The real corpus, read off the benchmark workbook. Pinned as literals so a tab that was
# re-graded, re-ordered or truncated fails here rather than silently moving every score on the
# dashboard. Counted 2026-08-21.
REAL_CALLS = 26
REAL_CRITERIA = 39
REAL_CELLS = 1014
REAL_VIOLATIONS = 25
REAL_NA_CELLS = 110
REAL_DISCRIMINATING = 14


def real_gt():
    """The ground-truth tab, or a skip when the workbook is not checked out."""
    if not BENCHMARK.exists():
        pytest.skip(f"{BENCHMARK} is not present")
    return normalise_gt_flags(
        pd.read_excel(
            BENCHMARK, sheet_name=GT_SHEET, dtype=str, keep_default_na=False, na_values=[]
        )
    )


def test_ground_truth_against_itself_scores_one_everywhere():
    gt = frame([row("a_OUT.wav"), row("b_OUT.wav", no=2, value="F")])
    blocks = cm.evaluate(gt.copy(), gt.copy())
    for family in FAMILIES:
        block = blocks[cm.BLOCK_BY_FAMILY[family]]
        assert (block["Accuracy"] == 1.0).all(), family
        assert (block["Macro-F1"] == 1.0).all(), family


def test_a_single_valued_column_is_marked_degenerate():
    """25 of the 39 real criteria are in this state, and their Accuracy is not evidence of
    anything. ``GT labels`` and ``Majority`` are what say so on the sheet.

    Both columns were carried over from the deleted exact-match module; this is the test that
    keeps them from being dropped as redundant.
    """
    gt = frame([row("a_OUT.wav"), row("b_OUT.wav", no=2)])
    block = cm.evaluate(gt.copy(), gt.copy())[cm.BLOCK_BY_FAMILY["CP"]]
    assert (block["GT labels"] == 1).all()
    assert (block["Majority"] == 1.0).all()


def test_the_confusion_matrix_excludes_and_counts_a_blank_prediction():
    """An absent answer cannot be placed in a cell, so the row is dropped and said to be."""
    gt = frame([row("a_OUT.wav"), row("b_OUT.wav", no=2)])
    result = gt.copy()
    result.loc[0, list(SHEET_COLUMNS)] = ""
    block = cm.evaluate(gt, result)[cm.BLOCK_BY_FAMILY["CP"]]
    assert (block["Excluded"] == 1).all()
    assert (block["Scored"] == 1).all()


def test_a_blank_prediction_is_never_folded_into_na():
    """The trap this pipeline is built to avoid.

    ``N/A`` is a graded answer on 110 of this corpus's cells. Treating an unanswered call as one
    would credit the model for the calls whose verification trio legitimately did not apply,
    turning a pipeline failure into a perfect result.
    """
    gt = frame([row("a_OUT.wav", value="N/A")])
    result = gt.copy()
    result.loc[0, list(SHEET_COLUMNS)] = ""
    block = cm.evaluate(gt, result)[cm.BLOCK_BY_FAMILY["CP"]]
    assert (block["Scored"] == 0).all()
    assert (block["Excluded"] == 1).all()


# --------------------------------------------------------------------------------------
# Three-level grain
# --------------------------------------------------------------------------------------


def test_the_sub_categories_partition_the_criteria():
    """14 groups over 39 criteria, each criterion claimed exactly once."""
    assert len(SUB_CATEGORIES) == 14
    claimed = [column for _, sub in SUB_CATEGORIES for column in SUB_CATEGORY_COLUMNS[sub]]
    assert claimed == list(SHEET_COLUMNS)


def test_the_sub_categories_match_productions_grouping():
    """Production groups its own evaluation by these names -- the first element of every
    ``GT_FIELD_MAPPING`` value in ``tasks/sentiment_telesale/schemas/metadata.py``.

    Pinned as a literal so a rename on either side is a visible edit here first; the two repos
    have no import between them and nothing else would catch a drift.
    """
    assert [sub for _, sub in SUB_CATEGORIES] == [
        "call_opening",
        "customer_identity_verification",
        "language_and_tone",
        "active_listening",
        "call_closing",
        "customer_needs_analysis",
        "offer_presentation_quality",
        "effective_objection_handling",
        "sales_closing_attempt",
        "cross_sell_upsell",
        "positive_customer_experience",
        "clarity_of_communication",
        "building_trust",
        "compliance",
    ]


def test_every_group_carries_three_aggregate_rows():
    gt = frame([row("a_OUT.wav"), row("b_OUT.wav", no=2, value="F")])
    blocks = cm.evaluate(gt.copy(), gt.copy())
    assert len(blocks[cm.BLOCK_OVERALL]) == len(FAMILIES) * 3
    assert len(blocks[cm.BLOCK_BY_SUBCATEGORY]) == len(SUB_CATEGORIES) * 3
    assert list(blocks[cm.BLOCK_OVERALL]["Average"][:3]) == list(cm.AVERAGE_KINDS)


def test_macro_and_micro_disagree_when_a_rare_label_is_missed():
    """The gap is the signal, and the reason all three averages are printed.

    Three calls, one of which the humans graded ``F`` on every criterion. A model answering
    ``T`` everywhere gets two of three calls right, so Micro stays high while Macro -- which
    counts the ``F`` class as much as the ``T`` class -- collapses.
    """
    gt = frame(
        [row("a_OUT.wav"), row("b_OUT.wav", no=2), row("c_OUT.wav", no=3, value="F")]
    )
    lazy = gt.copy()
    lazy.loc[:, list(SHEET_COLUMNS)] = "T"
    overall = cm.evaluate(gt, lazy)[cm.BLOCK_OVERALL].set_index("Average")
    macro = overall.loc["Macro-average", "F1"].iloc[0]
    micro = overall.loc["Micro-average", "F1"].iloc[0]
    assert macro < micro


def test_the_two_documented_identities_hold():
    """Stated in the legend, so they must not quietly stop being true.

    For single-label scoring a prediction that is wrong about one class is wrong about exactly
    one other, so pooled precision and recall both collapse to the share of calls answered
    correctly -- and weighting each class's recall by its own support collapses the same way.
    """
    gt = frame(
        [row("a_OUT.wav"), row("b_OUT.wav", no=2, value="F"), row("c_OUT.wav", no=3, value="N/A")]
    )
    result = gt.copy()
    result.loc[1, list(SHEET_COLUMNS)] = "T"
    overall = cm.evaluate(gt, result)[cm.BLOCK_OVERALL].set_index("Average")
    micro = overall.loc["Micro-average"]
    weighted = overall.loc["Weighted-average"]
    assert (micro["Accuracy"] == micro["Precision"]).all()
    assert (micro["Accuracy"] == micro["Recall"]).all()
    assert (micro["Accuracy"] == micro["F1"]).all()
    assert (weighted["Recall"].to_numpy() == micro["Recall"].to_numpy()).all()


def test_aggregate_accuracy_means_what_the_per_criterion_column_means():
    """Two columns named Accuracy on one sheet must be one quantity.

    ``aggregate_scores`` averages one-vs-rest LabelScores, so its accuracy counts three
    decisions per call and would not equal the per-criterion Accuracy below it. The aggregate
    rows therefore compute accuracy from the criteria directly: Macro is their mean, Micro is
    the pooled figure.
    """
    gt = frame(
        [row("a_OUT.wav"), row("b_OUT.wav", no=2, value="F"), row("c_OUT.wav", no=3)]
    )
    result = gt.copy()
    result.loc[1, list(SHEET_COLUMNS)] = "T"
    blocks = cm.evaluate(gt, result)
    overall = blocks[cm.BLOCK_OVERALL].set_index(["Category", "Average"])
    for family in FAMILIES:
        per_criterion = blocks[cm.BLOCK_BY_FAMILY[family]]
        title = cm.FAMILY_TITLES[family]
        assert overall.loc[(title, "Macro-average"), "Accuracy"] == round(
            per_criterion["Accuracy"].mean(), 4
        )
        pooled = (
            per_criterion["Accuracy"].mul(per_criterion["Scored"]).sum()
            / per_criterion["Scored"].sum()
        )
        assert overall.loc[(title, "Micro-average"), "Accuracy"] == round(pooled, 4)


def test_tn_is_derived_from_n_on_every_criterion_row():
    """``TN = N - TP - FP - FN``. The stakeholders' workbook keeps TN in its own column and it
    does not sum consistently; deriving it is what stops the same drift here."""
    gt = frame(
        [row("a_OUT.wav"), row("b_OUT.wav", no=2, value="F"), row("c_OUT.wav", no=3, value="N/A")]
    )
    result = gt.copy()
    result.loc[1, list(SHEET_COLUMNS)] = "T"
    for family in FAMILIES:
        block = cm.evaluate(gt.copy(), result.copy())[cm.BLOCK_BY_FAMILY[family]]
        for label in FLAG_LABELS:
            counted = block[[f"{label} {m}" for m in ("TP", "TN", "FP", "FN")]].sum(axis=1)
            assert (counted == block["Scored"]).all(), (family, label)


# --------------------------------------------------------------------------------------
# Ground-truth coverage
# --------------------------------------------------------------------------------------


def test_the_coverage_block_counts_the_real_corpus():
    """The numbers the whole dashboard has to be read against, pinned to the real tab."""
    blocks = cm.evaluate(real_gt(), real_gt())
    coverage = blocks[cm.BLOCK_COVERAGE]
    assert len(coverage) == len(SUB_CATEGORIES) + 1
    total = coverage.iloc[-1]
    assert total["Category"] == "TOTAL"
    assert total["Criteria"] == REAL_CRITERIA
    assert total["Cells"] == REAL_CELLS
    assert total["Violations (F)"] == REAL_VIOLATIONS
    assert total["N/A cells"] == REAL_NA_CELLS
    assert total["Discriminating"] == REAL_DISCRIMINATING


def test_the_coverage_block_names_the_groups_with_no_violation():
    """Five sub-categories contain none, and on those a constant answer is indistinguishable
    from a perfect model. The block has to say so or the 1.0000 beside them reads as a result."""
    coverage = cm.evaluate(real_gt(), real_gt())[cm.BLOCK_COVERAGE].set_index("Sub-category")
    blind = [
        SUB_CATEGORY_TITLES[sub]
        for sub in (
            "language_and_tone",
            "offer_presentation_quality",
            "effective_objection_handling",
            "positive_customer_experience",
            "clarity_of_communication",
        )
    ]
    for name in blind:
        assert coverage.loc[name, "Violations (F)"] == 0
        assert "no violation in ground truth" in coverage.loc[name, "Note"]


def test_the_coverage_block_does_not_depend_on_the_result_sheet():
    """It describes the corpus, so two runs' dashboards are comparable on it."""
    gt = real_gt()
    lazy = gt.copy()
    for column in SHEET_COLUMNS:
        lazy[column] = gt[column].where(gt[column] == "N/A", "T")
    pd.testing.assert_frame_equal(
        cm.evaluate(gt.copy(), gt.copy())[cm.BLOCK_COVERAGE],
        cm.evaluate(gt.copy(), lazy)[cm.BLOCK_COVERAGE],
    )


def test_a_model_that_catches_no_violation_reads_zero_recall_on_f():
    """THE REGRESSION THIS SCORER EXISTS TO PREVENT.

    Production and the human-evaluation workbook both fix FN and TN at zero, which makes Recall
    exactly 1.0000 and reports this same model at 0.98-0.99 by category. Here every criterion
    that has violation support must read ``F Recall`` 0.0000, and the F1 with it.
    """
    gt = real_gt()
    lazy = gt.copy()
    for column in SHEET_COLUMNS:
        lazy[column] = gt[column].where(gt[column] == "N/A", "T")
    blocks = cm.evaluate(gt, lazy)

    with_support = 0
    for family in FAMILIES:
        block = blocks[cm.BLOCK_BY_FAMILY[family]].set_index("Criterion")
        for criterion, scored in block.iterrows():
            if scored["F Support"]:
                with_support += 1
                assert scored["F Recall"] == 0.0, criterion
                assert scored["F F1"] == 0.0, criterion
    # Ten criteria, not fourteen. The coverage block counts 14 as discriminating, but four of
    # those vary only between T and N/A -- the cross-sell trio and customer_verification -- and
    # carry no violation at all. Only these ten can be failed, and 8 of the 25 violation cells
    # sit on one of them, op_call_opening_call_consent_before_engagement.
    assert with_support == 10


# --------------------------------------------------------------------------------------
# Transcript
# --------------------------------------------------------------------------------------


def test_the_scorer_omits_the_transcript_block_without_an_embedder():
    """Scoring stays offline and free unless a caller asks for the transcript family."""
    gt = frame([row("a_OUT.wav")])
    assert cm.BLOCK_TRANSCRIPT not in cm.evaluate(gt.copy(), gt.copy())


def stub_embedder(texts):
    """Deterministic vectors, so cosine is reproducible without touching Vertex."""
    return [[float(len(text) % 7), 1.0, 2.0] for text in texts]


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
    assert values["Mean char accuracy (1 - CER)"] == 1.0
    assert values["Scored"] == 1


def test_a_call_missing_a_transcript_is_excluded_and_counted():
    """Silently scoring it as a total miss would blame the model for a missing file."""
    gt = frame([row("a_OUT.wav"), row("b_OUT.wav", no=2)])
    blocks = cm.evaluate(
        gt.copy(),
        gt.copy(),
        embedder=stub_embedder,
        gt_transcripts={"a_OUT": "Agent: hello"},
        pred_transcripts={"a_OUT": "Agent: hello", "b_OUT": "Agent: hello"},
    )
    values = blocks[cm.BLOCK_TRANSCRIPT].set_index("Metric")["Value"]
    assert values["Scored"] == 1
    assert values["Excluded (missing either side)"] == 1


def test_the_transcript_row_is_not_one_of_the_averages():
    """It measures characters, not classes; labelling it Macro-average would invite a
    comparison with the rows above that is not meaningful."""
    gt = frame([row("a_OUT.wav")])
    overall = cm.evaluate(
        gt.copy(),
        gt.copy(),
        embedder=stub_embedder,
        gt_transcripts={"a_OUT": "Agent: hello"},
        pred_transcripts={"a_OUT": "Agent: hallo"},
    )[cm.BLOCK_OVERALL]
    transcript = overall[overall["Category"] == cm.FAMILY_TRANSCRIPT].iloc[0]
    assert transcript["Average"] not in cm.AVERAGE_KINDS
    assert transcript["Precision"] == cm.NOT_APPLICABLE


def test_the_transcript_block_sits_before_the_legend():
    """Dict insertion order is the sheet's row order, and the legend explains the block."""
    gt = frame([row("a_OUT.wav")])
    blocks = cm.evaluate(
        gt.copy(),
        gt.copy(),
        embedder=stub_embedder,
        gt_transcripts={"a_OUT": "Agent: hello"},
        pred_transcripts={"a_OUT": "Agent: hallo"},
    )
    titles = list(blocks)
    assert titles.index(cm.BLOCK_TRANSCRIPT) == len(titles) - 2
    assert titles[-1] == cm.BLOCK_LEGEND


# --------------------------------------------------------------------------------------
# Sheet layout
# --------------------------------------------------------------------------------------


def test_the_blocks_are_in_sheet_order():
    """Coverage first, because every score below has to be read against it."""
    gt = frame([row("a_OUT.wav")])
    titles = list(cm.evaluate(gt.copy(), gt.copy()))
    assert titles[:4] == [
        cm.BLOCK_HEADER,
        cm.BLOCK_COVERAGE,
        cm.BLOCK_OVERALL,
        cm.BLOCK_BY_SUBCATEGORY,
    ]
    assert titles[-1] == cm.BLOCK_LEGEND


def test_every_scored_block_names_its_ground_truth_columns():
    """No number on the sheet may be unattributable to a ground-truth column."""
    gt = frame([row("a_OUT.wav")])
    blocks = cm.evaluate(gt.copy(), gt.copy())
    captioned = set(cm.BLOCK_SOURCES)
    assert cm.BLOCK_COVERAGE in captioned
    assert {cm.BLOCK_BY_FAMILY[family] for family in FAMILIES} <= captioned
    assert captioned <= set(blocks)


def test_the_compare_sheets_split_by_family():
    """Three cells per criterion would put 117 columns on one sheet, which no reader can scan."""
    gt = frame([row("a_OUT.wav")])
    frames = cm.compare_rows(gt.copy(), gt.copy())
    assert set(frames) == set(cm.COMPARE_BY_FAMILY.values())
    operations = frames[cm.COMPARE_BY_FAMILY["OP"]]
    assert len(operations.columns) == 2 + 3 * len(FAMILIES["OP"])


def test_a_compare_sheet_shows_the_raw_cells_and_the_verdict():
    """It is the evidence behind the counts, so it prints what the workbook held -- including
    the blank the metrics excluded."""
    gt = frame([row("a_OUT.wav")])
    result = gt.copy()
    column = FAMILIES["CP"][0]
    result.loc[0, column] = ""
    frames = cm.compare_rows(gt, result)
    compliance = frames[cm.COMPARE_BY_FAMILY["CP"]]
    assert compliance.loc[0, f"{column} GT"] == "T"
    assert compliance.loc[0, f"{column} AI"] == ""
    assert compliance.loc[0, f"{column} Compare"] == cm.MATCH_FALSE


def test_the_verdict_sheets_share_one_suffix_with_the_dashboard():
    """Independent resolution would pair a _2 dashboard with another run's verdict sheets."""
    bases = ["Confusion Matrix Dashboard", "Operations Compare Result"]
    assert cm.resolve_sheet_names(["Sheet1"], bases) == bases
    assert cm.resolve_sheet_names(["Operations Compare Result"], bases) == [
        "Confusion Matrix Dashboard_1",
        "Operations Compare Result_1",
    ]


def test_the_scorer_defaults_to_the_telesale_transcript_folder():
    """A wrong folder here would score this corpus against another one's transcripts."""
    assert (
        cm.Config().gt_transcript_path
        == "/poc_internal_model_migration/voicefiles_telesale/Transcript"
    )


def test_the_output_pipeline_writes_to_its_own_folders():
    """The clone this replaced pointed at the QA package's folders and would have overwritten
    the QA run's workbook."""
    config = Config()
    assert config.src_file == "/poc_internal_model_migration/voicefiles_telesale"
    assert config.dest_file == "/poc_internal_model_migration/voicefiles_telesale_output"
    assert config.gcs_src_path.startswith("poc_internal_model/sentiment_telesale/google/")
    assert config.gt_sheet_name == "Voice_telesale - Groundtruth"
    assert config.output_sheet_name == "Voice_telesale - Google Result"


def test_the_sampling_matches_production():
    """Production's telesale settings, from telesale_pipeline_fact_check.yml. At temperature 1
    two runs over the same audio are not comparable, and an unbounded thinking budget is
    billed."""
    generation = Config().generation_config
    assert generation["temperature"] == 0
    assert generation["topP"] == 1
    assert generation["seed"] == 0
    assert generation["thinkingConfig"]["thinkingBudget"] == 0


def test_the_dashboard_json_round_trips():
    """Blocks are frames, and the writer indexes them by title -- a non-str title would break
    the sheet silently."""
    gt = frame([row("a_OUT.wav")])
    blocks = cm.evaluate(gt.copy(), gt.copy())
    assert all(isinstance(title, str) for title in blocks)
    assert json.loads(blocks[cm.BLOCK_OVERALL].to_json(orient="records"))
