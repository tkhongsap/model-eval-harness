import pandas as pd
import pandera.pandas as pa
from pandera import Field


class GroundTruthSchema(pa.DataFrameModel):
    filename: pa.typing.Series[str] = Field(nullable=True)
    # Customer Interaction
    greeting_standard: pa.typing.Series[str] = Field(nullable=True)
    greeting_standard_keyword_or_detail: pa.typing.Series[str] = Field(nullable=True)
    manners: pa.typing.Series[str] = Field(nullable=True)
    manners_keyword_or_detail: pa.typing.Series[str] = Field(nullable=True)
    enthusiasm: pa.typing.Series[str] = Field(nullable=True)
    enthusiasm_keyword_or_detail: pa.typing.Series[str] = Field(nullable=True)
    communication_skill: pa.typing.Series[str] = Field(nullable=True)
    communication_skill_keyword_or_detail: pa.typing.Series[str] = Field(nullable=True)
    ending_standard: pa.typing.Series[str] = Field(nullable=True)
    ending_standard_keyword_or_detail: pa.typing.Series[str] = Field(nullable=True)
    total_by_sub_cate_customer_interaction: pa.typing.Series[str] = Field(nullable=True)
    # Compliance & Verification
    data_privacy: pa.typing.Series[str] = Field(nullable=True)
    data_privacy_keyword_or_detail: pa.typing.Series[str] = Field(nullable=True)
    legal_verification: pa.typing.Series[str] = Field(nullable=True)
    legal_verification_keyword_or_detail: pa.typing.Series[str] = Field(nullable=True)
    company_verification: pa.typing.Series[str] = Field(nullable=True)
    company_verification_keyword_or_detail: pa.typing.Series[str] = Field(nullable=True)
    sla_notification: pa.typing.Series[str] = Field(nullable=True)
    sla_notification_keyword_or_detail: pa.typing.Series[str] = Field(nullable=True)
    transfer_standard: pa.typing.Series[str] = Field(nullable=True)
    transfer_standard_keyword_or_detail: pa.typing.Series[str] = Field(nullable=True)
    total_by_sub_cate_compliance_and_verification: pa.typing.Series[str] = Field(nullable=True)
    # Problem Handling & Resolution
    problem_understanding: pa.typing.Series[str] = Field(nullable=True)
    problem_understanding_keyword_or_detail: pa.typing.Series[str] = Field(nullable=True)
    compensation: pa.typing.Series[str] = Field(nullable=True)
    compensation_keyword_or_detail: pa.typing.Series[str] = Field(nullable=True)
    hold_standard: pa.typing.Series[str] = Field(nullable=True)
    hold_standard_keyword_or_detail: pa.typing.Series[str] = Field(nullable=True)
    wrap_up: pa.typing.Series[str] = Field(nullable=True)
    wrap_up_keyword_or_detail: pa.typing.Series[str] = Field(nullable=True)
    beyond_scope_support: pa.typing.Series[str] = Field(nullable=True)
    beyond_scope_support_keyword_or_detail: pa.typing.Series[str] = Field(nullable=True)
    total_by_sub_cate_problem_handling_and_resolution: pa.typing.Series[str] = Field(nullable=True)
    # Service Ownership & Support
    self_service: pa.typing.Series[str] = Field(nullable=True)
    self_service_keyword_or_detail: pa.typing.Series[str] = Field(nullable=True)
    case_ownership: pa.typing.Series[str] = Field(nullable=True)
    case_ownership_keyword_or_detail: pa.typing.Series[str] = Field(nullable=True)
    contact_confirm: pa.typing.Series[str] = Field(nullable=True)
    contact_confirm_keyword_or_detail: pa.typing.Series[str] = Field(nullable=True)
    omotenashi: pa.typing.Series[str] = Field(nullable=True)
    omotenashi_keyword_or_detail: pa.typing.Series[str] = Field(nullable=True)
    total_by_sub_cate_service_ownership_and_support: pa.typing.Series[str] = Field(nullable=True)
    # Action within Service
    retention: pa.typing.Series[str] = Field(nullable=True)
    retention_keyword_or_detail: pa.typing.Series[str] = Field(nullable=True)
    downsell: pa.typing.Series[str] = Field(nullable=True)
    downsell_keyword_or_detail: pa.typing.Series[str] = Field(nullable=True)
    mnp: pa.typing.Series[str] = Field(nullable=True)
    mnp_keyword_or_detail: pa.typing.Series[str] = Field(nullable=True)
    upselling: pa.typing.Series[str] = Field(nullable=True)
    upselling_keyword_or_detail: pa.typing.Series[str] = Field(nullable=True)
    total_by_sub_cate_action_within_service: pa.typing.Series[str] = Field(nullable=True)
    # Call type
    call_type: pa.typing.Series[str] = Field(nullable=True)

    class Config:
        coerce = True  # Automatically convert data types when validating the DataFrame
        strict = False  # Allow extra columns — GT sheet may contain helper/comment columns

    @pa.dataframe_parser
    def coalesce_blank_to_null(cls, df: pd.DataFrame) -> pd.DataFrame:
        str_cols = df.select_dtypes(include="object").columns
        df[str_cols] = df[str_cols].replace(r"^\s*$", pd.NA, regex=True)
        return df
