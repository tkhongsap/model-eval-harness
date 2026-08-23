"""Pandera schema for the internal ASR + LLM pipeline's Matrix sheet."""

import pandera.pandas as pa
from pandera import Field


class MetricsSchema(pa.DataFrameModel):
    """Per-file monitoring block for the internal ASR + LLM run.

    One row per *source* file, so a failed file keeps its place with ``Status`` = Failed;
    ``Failed Stage`` names which of the three calls broke (``asr``/``label``/``analysis``)
    and ``Error Type`` says how. Token columns are split per stage because the stages report
    in different units and scales -- Whisper reports duration, hence float ``Audio Seconds``.
    ``Analysis Reasoning Tokens`` is **never summed into any total** (endpoints disagree on
    whether it is inside ``completion_tokens``). ``Total Tokens`` sums the two chat calls'
    *reported* totals verbatim, never recomputed from components. Token columns are nullable
    ``Int64``: blank means "not reported" (e.g. a resumed file's ``ASR Attempts``), never 0.
    """

    no: pa.typing.Series[int] = Field(alias="No", nullable=True)
    voice_file_name: pa.typing.Series[str] = Field(alias="Voice File Name", nullable=True)
    status: pa.typing.Series[str] = Field(alias="Status", nullable=True)
    failed_stage: pa.typing.Series[str] = Field(alias="Failed Stage", nullable=True)
    error_type: pa.typing.Series[str] = Field(alias="Error Type", nullable=True)
    coerced_fields: pa.typing.Series[str] = Field(alias="Coerced Fields", nullable=True)
    asr_attempts: pa.typing.Series[int] = Field(alias="ASR Attempts", nullable=True)
    label_attempts: pa.typing.Series[int] = Field(alias="Label Attempts", nullable=True)
    analysis_attempts: pa.typing.Series[int] = Field(alias="Analysis Attempts", nullable=True)
    audio_seconds: pa.typing.Series[float] = Field(alias="Audio Seconds", nullable=True)
    asr_segments: pa.typing.Series[int] = Field(alias="ASR Segments", nullable=True)
    label_prompt_tokens: pa.typing.Series[int] = Field(
        alias="Label Prompt Tokens", nullable=True
    )
    label_completion_tokens: pa.typing.Series[int] = Field(
        alias="Label Completion Tokens", nullable=True
    )
    analysis_prompt_tokens: pa.typing.Series[int] = Field(
        alias="Analysis Prompt Tokens", nullable=True
    )
    analysis_completion_tokens: pa.typing.Series[int] = Field(
        alias="Analysis Completion Tokens", nullable=True
    )
    analysis_reasoning_tokens: pa.typing.Series[int] = Field(
        alias="Analysis Reasoning Tokens", nullable=True
    )
    analysis_cached_tokens: pa.typing.Series[int] = Field(
        alias="Analysis Cached Tokens", nullable=True
    )
    total_tokens: pa.typing.Series[int] = Field(alias="Total Tokens", nullable=True)

    class Config:
        coerce = True
        strict = False
