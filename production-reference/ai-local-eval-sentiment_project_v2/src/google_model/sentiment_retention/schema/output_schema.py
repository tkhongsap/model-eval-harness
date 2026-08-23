"""Pandera schema for the sentiment_retention model-result sheet (one row per file+product).

Columns and their order are the ``Voice_retention - Groundtruth`` tab's, because
:mod:`~src.google_model.sentiment_retention.google_confusion_matrix` joins the two sheets
by literal column name -- a column spelled differently on the two sheets would silently
fall out of the scored set.

``call_sumary_call_result`` and ``call_sumary_product`` reproduce the ground-truth sheet's
**typo** (one ``m`` in "sumary"). The sheet's spelling wins, exactly as
``company_verification`` / ``self_service`` do in the sibling QA schema: the workbook is a
shared control file, and the join is by literal column name.

The sheet's grain is **one row per (file, product)**, matching production's
``fact_checker.py``: ``ModelResponse.product`` is a ``ProductMap`` whose analysed slots
(``Postpaid`` / ``TOL`` / ``TVS`` / ``unknown``) each fan out to their own row in
``build_output_df``, so a multi-product call carries one ``call_sumary_product`` value --
and its own outcome and reasons -- per row. A file therefore legitimately appears more
than once under ``Voice File Name``; the scorer joins on the (file stem, product) pair.
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
    call_sumary_product: pa.typing.Series[str] = Field(alias="call_sumary_product", nullable=True)
    reason_main: pa.typing.Series[str] = Field(alias="reason_main", nullable=True)
    reason_secondary: pa.typing.Series[str] = Field(alias="reason_secondary", nullable=True)
    reason_third: pa.typing.Series[str] = Field(alias="reason_third", nullable=True)

    class Config:
        coerce = True
        strict = False
