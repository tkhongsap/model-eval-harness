"""Pandera schema for the sentiment_retention ground-truth sheet (human evaluation per call).

Gates the ``Voice_retention - Groundtruth`` tab of the benchmark workbook before a Vertex
batch job is paid for: ``strict=False`` admits extra columns but not missing ones, so a
renamed tab or a dropped column fails at the pre-flight rather than at the final write.

Same columns as
:class:`~src.google_model.sentiment_retention.schema.output_schema.OutputSchema`, including
the sheet's ``call_sumary`` typo -- see that module for why the sheet's spelling wins.
"""

import pandera.pandas as pa
from pandera import Field


class InputGTSchema(pa.DataFrameModel):
    """Schema for the input ground truth sheet, keyed by voice file name."""

    no: pa.typing.Series[int] = Field(alias="No", nullable=True)
    voice_file_name: pa.typing.Series[str] = Field(alias="Voice File Name", nullable=True)
    call_sumary_call_result: pa.typing.Series[str] = Field(
        alias="call_sumary_call_result", nullable=True
    )
    call_sumary_product: pa.typing.Series[str] = Field(alias="call_sumary_product", nullable=True)
    reason_main: pa.typing.Series[str] = Field(alias="reason_main", nullable=True)
    reason_secondary: pa.typing.Series[str] = Field(alias="reason_secondary", nullable=True)
    reason_third: pa.typing.Series[str] = Field(alias="reason_third", nullable=True)

    class Config:
        coerce = True
        strict = False
