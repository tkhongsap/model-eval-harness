"""Pandera schema for the sentiment_telesale local model-result sheet (one row per call).

The same 41 columns, in the same order, as the google package's
:class:`~src.google_model.sentiment_telesale.schema.output_schema.OutputSchema` -- deliberately,
so the two model families' result sheets diff against the ground-truth tab and against each
other by identical column name. The *responses* behind them differ (see
:mod:`~src.local_model.sentiment_telesale.schema.model_response`); the sheet does not.

Values are ``T`` / ``F`` / ``N/A``, never blank-for-null: ``coerce=True`` coerces via
``astype(str)``, which would render a ``None`` as the literal text ``"None"``. A genuinely
empty cell means the pipeline produced no answer for that call.
"""

import pandera.pandas as pa
from pandera import Field


class OutputSchema(pa.DataFrameModel):
    """Schema for the output sheet, keyed by voice file name."""

    no: pa.typing.Series[int] = Field(alias="No", nullable=True)
    voice_file_name: pa.typing.Series[str] = Field(alias="Voice File Name", nullable=True)
    op_call_opening_proper_identification: pa.typing.Series[str] = Field(alias="op_call_opening_proper_identification", nullable=True)
    op_call_opening_call_origin_disclosure: pa.typing.Series[str] = Field(alias="op_call_opening_call_origin_disclosure", nullable=True)
    op_call_opening_call_consent_before_engagement: pa.typing.Series[str] = Field(alias="op_call_opening_call_consent_before_engagement", nullable=True)
    op_customer_identity_verification_customer_verification: pa.typing.Series[str] = Field(alias="op_customer_identity_verification_customer_verification", nullable=True)
    op_customer_identity_verification_invalid_verification: pa.typing.Series[str] = Field(alias="op_customer_identity_verification_invalid_verification", nullable=True)
    op_customer_identity_verification_missing_verification: pa.typing.Series[str] = Field(alias="op_customer_identity_verification_missing_verification", nullable=True)
    op_language_and_tone_behavioral_violation: pa.typing.Series[str] = Field(alias="op_language_and_tone_behavioral_violation", nullable=True)
    op_language_and_tone_clarity: pa.typing.Series[str] = Field(alias="op_language_and_tone_clarity", nullable=True)
    op_language_and_tone_delivery_pace: pa.typing.Series[str] = Field(alias="op_language_and_tone_delivery_pace", nullable=True)
    op_active_listening_no_interruption: pa.typing.Series[str] = Field(alias="op_active_listening_no_interruption", nullable=True)
    op_active_listening_correct_understanding: pa.typing.Series[str] = Field(alias="op_active_listening_correct_understanding", nullable=True)
    op_active_listening_acknowledgement_paraphrasing: pa.typing.Series[str] = Field(alias="op_active_listening_acknowledgement_paraphrasing", nullable=True)
    op_call_closing_confirm_resolution: pa.typing.Series[str] = Field(alias="op_call_closing_confirm_resolution", nullable=True)
    op_call_closing_courteous_ending: pa.typing.Series[str] = Field(alias="op_call_closing_courteous_ending", nullable=True)
    op_call_closing_smooth_closing: pa.typing.Series[str] = Field(alias="op_call_closing_smooth_closing", nullable=True)
    se_customer_needs_analysis_usage_based_analysis: pa.typing.Series[str] = Field(alias="se_customer_needs_analysis_usage_based_analysis", nullable=True)
    se_customer_needs_analysis_benefit_highlight: pa.typing.Series[str] = Field(alias="se_customer_needs_analysis_benefit_highlight", nullable=True)
    se_offer_presentation_quality_clarity_of_explanation: pa.typing.Series[str] = Field(alias="se_offer_presentation_quality_clarity_of_explanation", nullable=True)
    se_offer_presentation_quality_customer_benefit_highlight: pa.typing.Series[str] = Field(alias="se_offer_presentation_quality_customer_benefit_highlight", nullable=True)
    se_effective_objection_handling_failure_to_listen: pa.typing.Series[str] = Field(alias="se_effective_objection_handling_failure_to_listen", nullable=True)
    se_effective_objection_handling_confrontational_tone: pa.typing.Series[str] = Field(alias="se_effective_objection_handling_confrontational_tone", nullable=True)
    se_sales_closing_attempt_value_based_closing: pa.typing.Series[str] = Field(alias="se_sales_closing_attempt_value_based_closing", nullable=True)
    se_sales_closing_attempt_unclear_separation: pa.typing.Series[str] = Field(alias="se_sales_closing_attempt_unclear_separation", nullable=True)
    se_sales_closing_attempt_inadequate_addon_disclosure: pa.typing.Series[str] = Field(alias="se_sales_closing_attempt_inadequate_addon_disclosure", nullable=True)
    se_cross_sell_upsell_missed_crosssell_upsell: pa.typing.Series[str] = Field(alias="se_cross_sell_upsell_missed_crosssell_upsell", nullable=True)
    se_cross_sell_upsell_unclear_addon_separation_crosssell: pa.typing.Series[str] = Field(alias="se_cross_sell_upsell_unclear_addon_separation_crosssell", nullable=True)
    se_cross_sell_upsell_inadequate_addon_disclosure_crosssell: pa.typing.Series[str] = Field(alias="se_cross_sell_upsell_inadequate_addon_disclosure_crosssell", nullable=True)
    cx_positive_customer_experience_failure_to_demonstrate_empathy: pa.typing.Series[str] = Field(alias="cx_positive_customer_experience_failure_to_demonstrate_empathy", nullable=True)
    cx_positive_customer_experience_deflecting_responsibility: pa.typing.Series[str] = Field(alias="cx_positive_customer_experience_deflecting_responsibility", nullable=True)
    cx_positive_customer_experience_escalates_customer_emotion: pa.typing.Series[str] = Field(alias="cx_positive_customer_experience_escalates_customer_emotion", nullable=True)
    cx_clarity_of_communication_overly_technical_language: pa.typing.Series[str] = Field(alias="cx_clarity_of_communication_overly_technical_language", nullable=True)
    cx_clarity_of_communication_fails_to_clarify_limitations: pa.typing.Series[str] = Field(alias="cx_clarity_of_communication_fails_to_clarify_limitations", nullable=True)
    cx_clarity_of_communication_no_adjustment_for_complexity: pa.typing.Series[str] = Field(alias="cx_clarity_of_communication_no_adjustment_for_complexity", nullable=True)
    cx_building_trust_provides_unclear_information: pa.typing.Series[str] = Field(alias="cx_building_trust_provides_unclear_information", nullable=True)
    cx_building_trust_provides_misleading_information: pa.typing.Series[str] = Field(alias="cx_building_trust_provides_misleading_information", nullable=True)
    cx_building_trust_fails_to_connect_value: pa.typing.Series[str] = Field(alias="cx_building_trust_fails_to_connect_value", nullable=True)
    cp_compliance_data_privacy_compliance: pa.typing.Series[str] = Field(alias="cp_compliance_data_privacy_compliance", nullable=True)
    cp_compliance_sales_integrity_compliance: pa.typing.Series[str] = Field(alias="cp_compliance_sales_integrity_compliance", nullable=True)
    cp_compliance_professional_conduct_compliance: pa.typing.Series[str] = Field(alias="cp_compliance_professional_conduct_compliance", nullable=True)

    class Config:
        coerce = True
        strict = False
