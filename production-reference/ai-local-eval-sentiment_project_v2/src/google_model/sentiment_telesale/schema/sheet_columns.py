"""The ``Voice_telesale`` sheet contract: which cell holds which schema leaf.

One module, imported by both model families and all four scorers, because the sheet is the
only thing they share. Everything here is derived from the ground-truth tab and verified
against :class:`~src.google_model.sentiment_telesale.schema.model_response.ModelResponse`:
each of the 39 flag columns names exactly one boolean leaf, and every boolean leaf under the
four judging blocks is named by exactly one column.

**The mapping is written out rather than derived from the prefix.** It reads like a rule --
``op_call_opening_proper_identification`` is
``operations_and_professionalism.call_opening.proper_identification`` -- but the split point
between block and leaf is genuinely ambiguous (both halves contain underscores), and the
prompt's own section headings disagree with the schema in four places: the prompt calls
``call_consent_before_engagement`` "consent_before_engagement", and names the three
``call_closing`` leaves at greater length. The sheet and the schema agree; the prompt prose
does not. Writing the table out is what keeps that disagreement harmless.

Cells are **three-valued**. ``None`` in the schema is a real answer -- "this call needed no
verification", "there was no cross-sell opening" -- and the sheet records it as a blank, on 20
of 26 calls for the verification trio and 13 of 26 for the cross-sell trio. It is written as
``"N/A"`` rather than left empty because the output column is a pandera ``Series[str]`` with
``coerce=True``, which coerces via ``astype(str)`` and would render a ``None`` as the literal
text ``"None"``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pandas as pd

ROW_NUMBER = "No"
JOIN_KEY = "Voice File Name"

TRUE_LABEL = "T"
FALSE_LABEL = "F"
NA_LABEL = "N/A"

#: Every value a flag cell may hold, in the order the confusion matrix lists them.
FLAG_LABELS: tuple[str, ...] = (TRUE_LABEL, FALSE_LABEL, NA_LABEL)

#: Sheet column -> (top-level field, sub-block, boolean leaf) in ``ModelResponse``.
COLUMN_PATHS: dict[str, tuple[str, str, str]] = {
    "op_call_opening_proper_identification": ("operations_and_professionalism", "call_opening", "proper_identification"),
    "op_call_opening_call_origin_disclosure": ("operations_and_professionalism", "call_opening", "call_origin_disclosure"),
    "op_call_opening_call_consent_before_engagement": ("operations_and_professionalism", "call_opening", "call_consent_before_engagement"),
    "op_customer_identity_verification_customer_verification": ("operations_and_professionalism", "customer_identity_verification", "customer_verification"),
    "op_customer_identity_verification_invalid_verification": ("operations_and_professionalism", "customer_identity_verification", "invalid_verification"),
    "op_customer_identity_verification_missing_verification": ("operations_and_professionalism", "customer_identity_verification", "missing_verification"),
    "op_language_and_tone_behavioral_violation": ("operations_and_professionalism", "language_and_tone", "behavioral_violation"),
    "op_language_and_tone_clarity": ("operations_and_professionalism", "language_and_tone", "clarity"),
    "op_language_and_tone_delivery_pace": ("operations_and_professionalism", "language_and_tone", "delivery_pace"),
    "op_active_listening_no_interruption": ("operations_and_professionalism", "active_listening", "no_interruption"),
    "op_active_listening_correct_understanding": ("operations_and_professionalism", "active_listening", "correct_understanding"),
    "op_active_listening_acknowledgement_paraphrasing": ("operations_and_professionalism", "active_listening", "acknowledgement_paraphrasing"),
    "op_call_closing_confirm_resolution": ("operations_and_professionalism", "call_closing", "confirm_resolution"),
    "op_call_closing_courteous_ending": ("operations_and_professionalism", "call_closing", "courteous_ending"),
    "op_call_closing_smooth_closing": ("operations_and_professionalism", "call_closing", "smooth_closing"),
    "se_customer_needs_analysis_usage_based_analysis": ("sales_effectiveness", "customer_needs_analysis", "usage_based_analysis"),
    "se_customer_needs_analysis_benefit_highlight": ("sales_effectiveness", "customer_needs_analysis", "benefit_highlight"),
    "se_offer_presentation_quality_clarity_of_explanation": ("sales_effectiveness", "offer_presentation_quality", "clarity_of_explanation"),
    "se_offer_presentation_quality_customer_benefit_highlight": ("sales_effectiveness", "offer_presentation_quality", "customer_benefit_highlight"),
    "se_effective_objection_handling_failure_to_listen": ("sales_effectiveness", "effective_objection_handling", "failure_to_listen"),
    "se_effective_objection_handling_confrontational_tone": ("sales_effectiveness", "effective_objection_handling", "confrontational_tone"),
    "se_sales_closing_attempt_value_based_closing": ("sales_effectiveness", "sales_closing_attempt", "value_based_closing"),
    "se_sales_closing_attempt_unclear_separation": ("sales_effectiveness", "sales_closing_attempt", "unclear_separation"),
    "se_sales_closing_attempt_inadequate_addon_disclosure": ("sales_effectiveness", "sales_closing_attempt", "inadequate_addon_disclosure"),
    "se_cross_sell_upsell_missed_crosssell_upsell": ("sales_effectiveness", "cross_sell_upsell", "missed_crosssell_upsell"),
    "se_cross_sell_upsell_unclear_addon_separation_crosssell": ("sales_effectiveness", "cross_sell_upsell", "unclear_addon_separation_crosssell"),
    "se_cross_sell_upsell_inadequate_addon_disclosure_crosssell": ("sales_effectiveness", "cross_sell_upsell", "inadequate_addon_disclosure_crosssell"),
    "cx_positive_customer_experience_failure_to_demonstrate_empathy": ("customer_experience", "positive_customer_experience", "failure_to_demonstrate_empathy"),
    "cx_positive_customer_experience_deflecting_responsibility": ("customer_experience", "positive_customer_experience", "deflecting_responsibility"),
    "cx_positive_customer_experience_escalates_customer_emotion": ("customer_experience", "positive_customer_experience", "escalates_customer_emotion"),
    "cx_clarity_of_communication_overly_technical_language": ("customer_experience", "clarity_of_communication", "overly_technical_language"),
    "cx_clarity_of_communication_fails_to_clarify_limitations": ("customer_experience", "clarity_of_communication", "fails_to_clarify_limitations"),
    "cx_clarity_of_communication_no_adjustment_for_complexity": ("customer_experience", "clarity_of_communication", "no_adjustment_for_complexity"),
    "cx_building_trust_provides_unclear_information": ("customer_experience", "building_trust", "provides_unclear_information"),
    "cx_building_trust_provides_misleading_information": ("customer_experience", "building_trust", "provides_misleading_information"),
    "cx_building_trust_fails_to_connect_value": ("customer_experience", "building_trust", "fails_to_connect_value"),
    "cp_compliance_data_privacy_compliance": ("compliance", "compliance", "data_privacy_compliance"),
    "cp_compliance_sales_integrity_compliance": ("compliance", "compliance", "sales_integrity_compliance"),
    "cp_compliance_professional_conduct_compliance": ("compliance", "compliance", "professional_conduct_compliance"),
}

#: The 39 flag columns, in ground-truth sheet order.
SHEET_COLUMNS: tuple[str, ...] = tuple(COLUMN_PATHS)

#: Every column of the result sheet, in ground-truth sheet order.
ALL_COLUMNS: tuple[str, ...] = (ROW_NUMBER, JOIN_KEY, *SHEET_COLUMNS)

#: Scored family -> its columns. The four families are the prompt's four judged categories.
FAMILIES: dict[str, tuple[str, ...]] = {
    "OP": (
        "op_call_opening_proper_identification",
        "op_call_opening_call_origin_disclosure",
        "op_call_opening_call_consent_before_engagement",
        "op_customer_identity_verification_customer_verification",
        "op_customer_identity_verification_invalid_verification",
        "op_customer_identity_verification_missing_verification",
        "op_language_and_tone_behavioral_violation",
        "op_language_and_tone_clarity",
        "op_language_and_tone_delivery_pace",
        "op_active_listening_no_interruption",
        "op_active_listening_correct_understanding",
        "op_active_listening_acknowledgement_paraphrasing",
        "op_call_closing_confirm_resolution",
        "op_call_closing_courteous_ending",
        "op_call_closing_smooth_closing",
    ),
    "SE": (
        "se_customer_needs_analysis_usage_based_analysis",
        "se_customer_needs_analysis_benefit_highlight",
        "se_offer_presentation_quality_clarity_of_explanation",
        "se_offer_presentation_quality_customer_benefit_highlight",
        "se_effective_objection_handling_failure_to_listen",
        "se_effective_objection_handling_confrontational_tone",
        "se_sales_closing_attempt_value_based_closing",
        "se_sales_closing_attempt_unclear_separation",
        "se_sales_closing_attempt_inadequate_addon_disclosure",
        "se_cross_sell_upsell_missed_crosssell_upsell",
        "se_cross_sell_upsell_unclear_addon_separation_crosssell",
        "se_cross_sell_upsell_inadequate_addon_disclosure_crosssell",
    ),
    "CX": (
        "cx_positive_customer_experience_failure_to_demonstrate_empathy",
        "cx_positive_customer_experience_deflecting_responsibility",
        "cx_positive_customer_experience_escalates_customer_emotion",
        "cx_clarity_of_communication_overly_technical_language",
        "cx_clarity_of_communication_fails_to_clarify_limitations",
        "cx_clarity_of_communication_no_adjustment_for_complexity",
        "cx_building_trust_provides_unclear_information",
        "cx_building_trust_provides_misleading_information",
        "cx_building_trust_fails_to_connect_value",
    ),
    "CP": (
        "cp_compliance_data_privacy_compliance",
        "cp_compliance_sales_integrity_compliance",
        "cp_compliance_professional_conduct_compliance",
    ),
}

#: Human titles for the dashboard blocks.
FAMILY_TITLES: dict[str, str] = {
    "OP": "Operations & professionalism",
    "SE": "Sales effectiveness",
    "CX": "Customer experience",
    "CP": "Compliance",
}


# --- Sub-categories ---------------------------------------------------------------------------
#
# The middle level of the grain, between a family and one criterion. It is DERIVED from
# COLUMN_PATHS rather than restated: the second element of each path already names it, and a
# hand-written second copy is a second thing to keep in step. What is written out is the title
# table, because a display string is not derivable from a snake_case leaf without inventing
# hyphenation ("Cross-sell / upsell", not "Cross sell upsell").
#
# Production groups its evaluation by exactly this level -- the first element of every
# GT_FIELD_MAPPING value in `tasks/sentiment_telesale/schemas/metadata.py` is the sub-category,
# not the family -- and the stakeholders' workbook prints cate / sub_cate / item. Neither grain
# existed on this dashboard before, which is why a group like `cross_sell_upsell`, whose three
# criteria share one N/A pattern, could not be read as a group.


def _build_sub_categories() -> tuple[tuple[str, str], ...]:
    """``(family, sub-category)`` pairs in sheet order, first appearance wins."""
    ordered: list[tuple[str, str]] = []
    for family, columns in FAMILIES.items():
        for column in columns:
            pair = (family, COLUMN_PATHS[column][1])
            if pair not in ordered:
                ordered.append(pair)
    return tuple(ordered)


#: The 14 sub-categories as ``(family, leaf)``, in ground-truth sheet order.
SUB_CATEGORIES: tuple[tuple[str, str], ...] = _build_sub_categories()

#: Sub-category leaf -> its criteria, in sheet order.
SUB_CATEGORY_COLUMNS: dict[str, tuple[str, ...]] = {
    sub: tuple(column for column in SHEET_COLUMNS if COLUMN_PATHS[column][1] == sub)
    for _family, sub in SUB_CATEGORIES
}

#: Sub-category leaf -> the family it belongs to.
SUB_CATEGORY_FAMILY: dict[str, str] = {sub: family for family, sub in SUB_CATEGORIES}

#: Human titles for the sub-category rows.
SUB_CATEGORY_TITLES: dict[str, str] = {
    "call_opening": "Call opening",
    "customer_identity_verification": "Customer identity verification",
    "language_and_tone": "Language and tone",
    "active_listening": "Active listening",
    "call_closing": "Call closing",
    "customer_needs_analysis": "Customer needs analysis",
    "offer_presentation_quality": "Offer presentation quality",
    "effective_objection_handling": "Effective objection handling",
    "sales_closing_attempt": "Sales closing attempt",
    "cross_sell_upsell": "Cross-sell / upsell",
    "positive_customer_experience": "Positive customer experience",
    "clarity_of_communication": "Clarity of communication",
    "building_trust": "Building trust",
    "compliance": "Compliance",
}

# Keying SUB_CATEGORY_COLUMNS by the bare leaf is only safe while the leaves are unique across
# families, which they are today -- `compliance` is a sub-category of the `compliance` family and
# of nothing else. Asserted at import rather than left to a test, because a duplicate leaf would
# silently merge two groups' criteria into one dashboard row.
assert len(SUB_CATEGORY_COLUMNS) == len(SUB_CATEGORIES) == 14
assert sum(len(columns) for columns in SUB_CATEGORY_COLUMNS.values()) == len(SHEET_COLUMNS)
assert set(SUB_CATEGORY_TITLES) == set(SUB_CATEGORY_COLUMNS)


def flag(value: Any) -> str:
    """Render one boolean leaf as the sheet writes it.

    ``None`` becomes ``"N/A"`` -- not blank, and never the string ``"None"``.
    """
    if value is None:
        return NA_LABEL
    return TRUE_LABEL if value else FALSE_LABEL


def flatten(dump: Mapping[str, Any]) -> dict[str, str]:
    """Pull the 39 scored cells out of a ``ModelResponse.model_dump()``.

    Takes the dump rather than the model, so one helper serves the batch path (which decodes
    JSON off a GCS shard) and the local path (which holds the model in memory).
    """
    return {
        column: flag(dump[top][sub][leaf])
        for column, (top, sub, leaf) in COLUMN_PATHS.items()
    }


def normalise_gt_flags(df: pd.DataFrame) -> pd.DataFrame:
    """Bring the **ground-truth** sheet onto the ``T`` / ``F`` / ``N/A`` vocabulary.

    A blank ground-truth cell means "the criterion did not apply", so it becomes ``"N/A"``.
    Read the workbook with ``keep_default_na=False`` so a blank arrives as ``""`` rather than
    ``NaN``: ``"N/A"`` is a graded answer here, exactly as it is on the QA sheet, and letting
    pandas parse it as a null would erase the distinction this function exists to preserve.

    **Never apply this to a result sheet.** On the prediction side the two are different
    answers: this pipeline writes an explicit ``"N/A"`` when the model said the criterion did
    not apply, and leaves the cell genuinely empty when the call produced no answer at all --
    a failed parse, or a file Vertex never returned. Folding those together would score an
    unanswered call as correct on the twenty calls whose verification trio is legitimately
    ``N/A``, turning a pipeline failure into a perfect result.
    """
    out = df.copy()
    for column in SHEET_COLUMNS:
        if column not in out.columns:
            continue
        out[column] = (
            out[column].astype("string").fillna("").str.strip().replace("", NA_LABEL).astype(str)
        )
    return out


__all__ = [
    "ALL_COLUMNS",
    "COLUMN_PATHS",
    "FALSE_LABEL",
    "FAMILIES",
    "FAMILY_TITLES",
    "FLAG_LABELS",
    "JOIN_KEY",
    "NA_LABEL",
    "ROW_NUMBER",
    "SHEET_COLUMNS",
    "SUB_CATEGORIES",
    "SUB_CATEGORY_COLUMNS",
    "SUB_CATEGORY_FAMILY",
    "SUB_CATEGORY_TITLES",
    "TRUE_LABEL",
    "flag",
    "flatten",
    "normalise_gt_flags",
]
