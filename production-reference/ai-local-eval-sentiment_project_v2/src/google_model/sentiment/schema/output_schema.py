"""Pandera schema for the sentiment model-result sheet (one row per scored call)."""

import pandera.pandas as pa
from pandera import Field


class OutputSchema(pa.DataFrameModel):
    """Schema for the output sheet, keyed by voice file name."""

    no: pa.typing.Series[int] = Field(alias="No", nullable=True)
    voice_file_name: pa.typing.Series[str] = Field(alias="Voice File Name", nullable=True)
    greeting_standard: pa.typing.Series[str] = Field(alias="greeting_standard", nullable=True)
    manners: pa.typing.Series[str] = Field(alias="manners", nullable=True)
    enthusiasm: pa.typing.Series[str] = Field(alias="enthusiasm", nullable=True)
    communication_skill: pa.typing.Series[str] = Field(alias="communication_skill", nullable=True)
    ending_standard: pa.typing.Series[str] = Field(alias="ending_standard", nullable=True)
    data_privacy: pa.typing.Series[str] = Field(alias="data_privacy", nullable=True)
    legal_verification: pa.typing.Series[str] = Field(alias="legal_verification", nullable=True)
    company_verification: pa.typing.Series[str] = Field(alias="company_verification", nullable=True)
    sla_notification: pa.typing.Series[str] = Field(alias="sla_notification", nullable=True)
    transfer_standard: pa.typing.Series[str] = Field(alias="transfer_standard", nullable=True)
    problem_understanding: pa.typing.Series[str] = Field(alias="problem_understanding", nullable=True)
    compensation: pa.typing.Series[str] = Field(alias="compensation", nullable=True)
    hold_standard: pa.typing.Series[str] = Field(alias="hold_standard", nullable=True)
    wrap_up: pa.typing.Series[str] = Field(alias="wrap_up", nullable=True)
    beyond_scope_support: pa.typing.Series[str] = Field(alias="beyond_scope_support", nullable=True)
    self_service: pa.typing.Series[str] = Field(alias="self_service", nullable=True)
    case_ownership: pa.typing.Series[str] = Field(alias="case_ownership", nullable=True)
    contact_confirm: pa.typing.Series[str] = Field(alias="contact_confirm", nullable=True)
    retention: pa.typing.Series[str] = Field(alias="retention", nullable=True)
    downsell: pa.typing.Series[str] = Field(alias="downsell", nullable=True)
    mnp: pa.typing.Series[str] = Field(alias="mnp", nullable=True)
    upselling: pa.typing.Series[str] = Field(alias="upselling", nullable=True)
    overall_sentiment: pa.typing.Series[str] = Field(alias="overall_sentiment", nullable=True)
    initial_sentiment: pa.typing.Series[str] = Field(alias="initial_sentiment", nullable=True)
    final_sentiment: pa.typing.Series[str] = Field(alias="final_sentiment", nullable=True)
    summary_story: pa.typing.Series[str] = Field(alias="summary_story", nullable=True)
    call_type: pa.typing.Series[str] = Field(alias="call_type", nullable=True)

    class Config:
        coerce = True
        strict = False
