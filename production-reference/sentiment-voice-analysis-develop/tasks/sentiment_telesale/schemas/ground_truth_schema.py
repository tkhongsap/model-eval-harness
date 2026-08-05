import pandas as pd
import pandera.pandas as pa
from pandera import Field


class GroundTruthSchema(pa.DataFrameModel):
    filename: pa.typing.Series[str] = Field(nullable=True)
    # Operations & Professionalism — Call Opening
    op_call_opening_proper_identification: pa.typing.Series[str] = Field(nullable=True)
    op_call_opening_call_origin_disclosure: pa.typing.Series[str] = Field(nullable=True)
    op_call_opening_call_consent_before_engagement: pa.typing.Series[str] = Field(nullable=True)
    # Operations & Professionalism — Customer Identity Verification
    op_customer_identity_verification_customer_verification: pa.typing.Series[str] = Field(nullable=True)
    op_customer_identity_verification_invalid_verification: pa.typing.Series[str] = Field(nullable=True)
    op_customer_identity_verification_missing_verification: pa.typing.Series[str] = Field(nullable=True)
    # Operations & Professionalism — Language and Tone
    op_language_and_tone_behavioral_violation: pa.typing.Series[str] = Field(nullable=True)
    op_language_and_tone_clarity: pa.typing.Series[str] = Field(nullable=True)
    op_language_and_tone_delivery_pace: pa.typing.Series[str] = Field(nullable=True)
    # Operations & Professionalism — Active Listening
    op_active_listening_no_interruption: pa.typing.Series[str] = Field(nullable=True)
    op_active_listening_correct_understanding: pa.typing.Series[str] = Field(nullable=True)
    op_active_listening_acknowledgement_paraphrasing: pa.typing.Series[str] = Field(nullable=True)
    # Operations & Professionalism — Call Closing
    op_call_closing_confirm_resolution: pa.typing.Series[str] = Field(nullable=True)
    op_call_closing_courteous_ending: pa.typing.Series[str] = Field(nullable=True)
    op_call_closing_smooth_closing: pa.typing.Series[str] = Field(nullable=True)
    # Sales Effectiveness — Customer Needs Analysis
    se_customer_needs_analysis_usage_based_analysis: pa.typing.Series[str] = Field(nullable=True)
    se_customer_needs_analysis_benefit_highlight: pa.typing.Series[str] = Field(nullable=True)
    # Sales Effectiveness — Offer Presentation Quality
    se_offer_presentation_quality_clarity_of_explanation: pa.typing.Series[str] = Field(nullable=True)
    se_offer_presentation_quality_customer_benefit_highlight: pa.typing.Series[str] = Field(nullable=True)
    # Sales Effectiveness — Effective Objection Handling
    se_effective_objection_handling_failure_to_listen: pa.typing.Series[str] = Field(nullable=True)
    se_effective_objection_handling_confrontational_tone: pa.typing.Series[str] = Field(nullable=True)
    # Sales Effectiveness — Sales Closing Attempt
    se_sales_closing_attempt_value_based_closing: pa.typing.Series[str] = Field(nullable=True)
    se_sales_closing_attempt_unclear_separation: pa.typing.Series[str] = Field(nullable=True)
    se_sales_closing_attempt_inadequate_addon_disclosure: pa.typing.Series[str] = Field(nullable=True)
    # Sales Effectiveness — Cross-sell / Upsell
    se_cross_sell_upsell_missed_crosssell_upsell: pa.typing.Series[str] = Field(nullable=True)
    se_cross_sell_upsell_unclear_addon_separation_crosssell: pa.typing.Series[str] = Field(nullable=True)
    se_cross_sell_upsell_inadequate_addon_disclosure_crosssell: pa.typing.Series[str] = Field(nullable=True)
    # Customer Experience — Positive Customer Experience
    cx_positive_customer_experience_failure_to_demonstrate_empathy: pa.typing.Series[str] = Field(nullable=True)
    cx_positive_customer_experience_deflecting_responsibility: pa.typing.Series[str] = Field(nullable=True)
    cx_positive_customer_experience_escalates_customer_emotion: pa.typing.Series[str] = Field(nullable=True)
    # Customer Experience — Clarity of Communication
    cx_clarity_of_communication_overly_technical_language: pa.typing.Series[str] = Field(nullable=True)
    cx_clarity_of_communication_fails_to_clarify_limitations: pa.typing.Series[str] = Field(nullable=True)
    cx_clarity_of_communication_no_adjustment_for_complexity: pa.typing.Series[str] = Field(nullable=True)
    # Customer Experience — Building Trust
    cx_building_trust_provides_unclear_information: pa.typing.Series[str] = Field(nullable=True)
    cx_building_trust_provides_misleading_information: pa.typing.Series[str] = Field(nullable=True)
    cx_building_trust_fails_to_connect_value: pa.typing.Series[str] = Field(nullable=True)
    # Compliance
    cp_compliance_data_privacy_compliance: pa.typing.Series[str] = Field(nullable=True)
    cp_compliance_sales_integrity_compliance: pa.typing.Series[str] = Field(nullable=True)
    cp_compliance_professional_conduct_compliance: pa.typing.Series[str] = Field(nullable=True)

    class Config:
        coerce = True  # Automatically convert data types when validating the DataFrame
        strict = False  # Allow extra columns — GT sheet may contain helper/comment columns

    @pa.dataframe_parser
    def coalesce_blank_to_null(cls, df: pd.DataFrame) -> pd.DataFrame:
        str_cols = df.select_dtypes(include="object").columns
        df[str_cols] = df[str_cols].replace(r"^\s*$", pd.NA, regex=True)
        return df
