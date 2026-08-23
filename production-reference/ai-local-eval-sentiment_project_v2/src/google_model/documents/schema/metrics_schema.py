"""Pandera schema for the Matrix sheet's per-page monitoring block."""

from __future__ import annotations

import pandera.pandas as pa
from pandera import Field


class MetricsSchema(pa.DataFrameModel):
    """Schema for the per-page monitoring block, keyed by split-page name.

    One row per *submitted page*, the unit usage is reported against; ``File name`` rides
    alongside for grouping, resolvable from ``split_doc``'s mapping even for a failed page.
    ``No`` does **not** agree with the output sheet's ``No`` (one row per printed line item
    there) -- join the two on ``Page Name``. Token columns are nullable on purpose -- blank
    means "not reported", never 0 -- and ``Series[int]`` + ``nullable=True`` + ``coerce=True``
    resolves to pandas' ``Int64``, which preserves ``pd.NA``. The prompt side is split per
    modality (billed at different rates); this pipeline fills TEXT and DOCUMENT or IMAGE. On
    fully-reported rows the six prompt columns sum to ``Prompt Tokens``, prompt + candidates +
    thoughts equals ``Total Tokens``, and ``Cached Tokens`` is a *subset* of ``Prompt Tokens``
    -- reported values, never recomputed.
    """

    no: pa.typing.Series[int] = Field(alias="No", nullable=True)
    page_name: pa.typing.Series[str] = Field(alias="Page Name", nullable=True)
    file_name: pa.typing.Series[str] = Field(alias="File name", nullable=True)
    status: pa.typing.Series[str] = Field(alias="Status", nullable=True)
    error_type: pa.typing.Series[str] = Field(alias="Error Type", nullable=True)
    prompt_tokens: pa.typing.Series[int] = Field(alias="Prompt Tokens", nullable=True)
    prompt_text_tokens: pa.typing.Series[int] = Field(alias="Prompt Text Tokens", nullable=True)
    prompt_audio_tokens: pa.typing.Series[int] = Field(alias="Prompt Audio Tokens", nullable=True)
    prompt_image_tokens: pa.typing.Series[int] = Field(alias="Prompt Image Tokens", nullable=True)
    prompt_video_tokens: pa.typing.Series[int] = Field(alias="Prompt Video Tokens", nullable=True)
    prompt_document_tokens: pa.typing.Series[int] = Field(
        alias="Prompt Document Tokens", nullable=True
    )
    prompt_other_tokens: pa.typing.Series[int] = Field(alias="Prompt Other Tokens", nullable=True)
    cached_tokens: pa.typing.Series[int] = Field(alias="Cached Tokens", nullable=True)
    candidates_tokens: pa.typing.Series[int] = Field(alias="Candidates Tokens", nullable=True)
    thoughts_tokens: pa.typing.Series[int] = Field(alias="Thoughts Tokens", nullable=True)
    total_tokens: pa.typing.Series[int] = Field(alias="Total Tokens", nullable=True)
    prompt_modalities: pa.typing.Series[str] = Field(alias="Prompt Modalities", nullable=True)
    traffic_type: pa.typing.Series[str] = Field(alias="Traffic Type", nullable=True)

    class Config:
        coerce = True
        strict = False
