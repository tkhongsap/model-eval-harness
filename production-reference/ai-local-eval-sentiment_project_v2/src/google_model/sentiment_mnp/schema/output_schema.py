"""Pandera schema for the sentiment_mnp model-result sheet (one row per scored call).

Columns and their order are the ``Voice_mnp - Groundtruth`` tab's, because
:func:`~src.google_model.sentiment_mnp.google_exact_match.evaluate` joins the two sheets
with pandas' ``suffixes=("_gt", "_pred")`` and reads each scored column as
``f"{column}_gt"`` / ``f"{column}_pred"`` -- a column spelled differently on the two
sheets survives the merge unsuffixed and raises ``KeyError``.

``call_sumary_call_result`` reproduces the ground-truth sheet's **typo** (one ``m`` in
"sumary"). The sheet's spelling wins, exactly as ``company_verification`` /
``self_service`` do in the sibling QA schema: the workbook is a shared control file, and
the join is by literal column name.
"""

import pandera.pandas as pa
from pandera import Field


class OutputSchema(pa.DataFrameModel):
    """Schema for the output sheet, keyed by voice file name."""

    no: pa.typing.Series[int] = Field(alias="No", nullable=True)
    voice_file_name: pa.typing.Series[str] = Field(alias="Voice File Name", nullable=True)
    call_sumary_call_result: pa.typing.Series[str] = Field(
        alias="call_sumary_call_result", nullable=True
    )
    reason_main: pa.typing.Series[str] = Field(alias="reason_main", nullable=True)
    reason_secondary: pa.typing.Series[str] = Field(alias="reason_secondary", nullable=True)
    reason_third: pa.typing.Series[str] = Field(alias="reason_third", nullable=True)

    class Config:
        coerce = True
        strict = False
