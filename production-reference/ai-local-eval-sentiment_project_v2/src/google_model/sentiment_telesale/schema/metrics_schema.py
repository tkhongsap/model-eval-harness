"""Pandera schema for the Matrix sheet's per-file monitoring block."""

import pandera.pandas as pa
from pandera import Field


class MetricsSchema(pa.DataFrameModel):
    """Schema for the per-file monitoring block, keyed by voice file name.

    One row per *submitted* file, not per prediction row, so a file Vertex never answered still
    shows. Token columns are nullable on purpose -- blank means "not reported", never 0 -- and
    ``Series[int]`` + ``nullable=True`` + ``coerce=True`` resolves to pandas' ``Int64``, which
    preserves ``pd.NA``. The prompt side is split per modality (billed at different rates);
    this pipeline fills TEXT and AUDIO. On fully-reported rows the six prompt columns sum to
    ``Prompt Tokens``, prompt + candidates + thoughts equals ``Total Tokens``, and ``Cached
    Tokens`` is a *subset* of ``Prompt Tokens`` -- reported values, never recomputed.

    ``Coerced Fields`` is telesale-only: a comma-separated list of the field paths the softened
    validators had to rewrite, empty when the model answered within the prompt's own rules.
    Production *raises* on those answers; this pipeline repairs them instead, because a raise
    would cost all 39 scored flags of a call out of only 26. The column exists so a repaired row
    is never mistaken for a compliant one -- see
    :func:`~src.google_model.sentiment_telesale.schema.model_response.collect_coercions`.
    """

    no: pa.typing.Series[int] = Field(alias="No", nullable=True)
    voice_file_name: pa.typing.Series[str] = Field(alias="Voice File Name", nullable=True)
    status: pa.typing.Series[str] = Field(alias="Status", nullable=True)
    error_type: pa.typing.Series[str] = Field(alias="Error Type", nullable=True)
    coerced_fields: pa.typing.Series[str] = Field(alias="Coerced Fields", nullable=True)
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
