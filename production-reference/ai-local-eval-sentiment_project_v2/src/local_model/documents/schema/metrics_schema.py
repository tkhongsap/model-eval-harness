"""Pandera schema for the internal documents pipeline's Matrix sheet."""

import pandera.pandas as pa
from pandera import Field


class MetricsSchema(pa.DataFrameModel):
    """Per-page monitoring block for the internal (direct endpoint) run.

    One row per *submitted page*, so a page that failed every attempt keeps its place with
    ``Status`` = Failed; ``File name`` rides alongside for grouping. ``No`` does **not**
    agree with the output sheet's ``No`` (one row per printed line item there) -- join on
    ``Page Name``. Token columns follow the OpenAI prompt/completion shape and are nullable
    ``Int64``: blank means "not reported", never 0, and ``Total Tokens`` is never recomputed.
    ``Attempts`` counts calls made (retries included); blank means the endpoint was never
    called at all.
    """

    no: pa.typing.Series[int] = Field(alias="No", nullable=True)
    page_name: pa.typing.Series[str] = Field(alias="Page Name", nullable=True)
    file_name: pa.typing.Series[str] = Field(alias="File name", nullable=True)
    status: pa.typing.Series[str] = Field(alias="Status", nullable=True)
    error_type: pa.typing.Series[str] = Field(alias="Error Type", nullable=True)
    attempts: pa.typing.Series[int] = Field(alias="Attempts", nullable=True)
    prompt_tokens: pa.typing.Series[int] = Field(alias="Prompt Tokens", nullable=True)
    completion_tokens: pa.typing.Series[int] = Field(
        alias="Completion Tokens", nullable=True
    )
    total_tokens: pa.typing.Series[int] = Field(alias="Total Tokens", nullable=True)

    class Config:
        coerce = True
        strict = False
