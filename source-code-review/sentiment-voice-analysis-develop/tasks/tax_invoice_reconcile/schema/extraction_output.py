"""Pandera schema for the exported extraction report.

This is the business-facing frame written to ``control_site.extraction_result_path`` as a
dated CSV. It is a projection of :class:`ExtractionProcessing` (the builder→reconcile
contract) with the internal ``DATADATE`` run marker dropped; ``DOC_STATUS``/``REMARK``
already carry the folded Master-Buyer verdict from the builder. Use
:func:`to_extraction_output` to build and validate it.
"""

from __future__ import annotations

import pandas as pd
import pandera.pandas as pa
from pandera import Field
from pandera.engines import pandas_engine

# Money columns carry exact ``Decimal`` values (THB satang precision), matching the
# upstream :class:`OCROutputSchema` so the reconciled report stays exact.
_MONEY_KW = {"precision": 18, "scale": 2}


class ExtractionOutput(pa.DataFrameModel):
    """One row per resolved document: extracted fields + buyer lookup, for export."""

    DOC_NAME: pa.typing.Series[str] = Field(nullable=True)
    BUYER_NAME_TH: pa.typing.Series[str] = Field(nullable=True)
    BUYER_ADDRESS_TH: pa.typing.Series[str] = Field(nullable=True)
    BUYER_NAME_ENG: pa.typing.Series[str] = Field(nullable=True)
    BUYER_ADDRESS_ENG: pa.typing.Series[str] = Field(nullable=True)
    BUYER_COMPANY_CODE: pa.typing.Series[str] = Field(nullable=True)
    BUYER_TAX_ID: pa.typing.Series[str] = Field(nullable=True)
    BUYER_BRANCH_CODE: pa.typing.Series[str] = Field(nullable=True)
    BUYER_BRANCH_NAME: pa.typing.Series[str] = Field(nullable=True)
    VENDOR_NAME_TH: pa.typing.Series[str] = Field(nullable=True)
    VENDOR_ADDRESS_TH: pa.typing.Series[str] = Field(nullable=True)
    VENDOR_NAME_ENG: pa.typing.Series[str] = Field(nullable=True)
    VENDOR_ADDRESS_ENG: pa.typing.Series[str] = Field(nullable=True)
    VENDOR_TAX_ID: pa.typing.Series[str] = Field(nullable=True)
    VENDOR_BRANCH_CODE: pa.typing.Series[str] = Field(nullable=True)
    VENDOR_BRANCH_NAME: pa.typing.Series[str] = Field(nullable=True)
    TAX_INVOICE_NUMBER: pa.typing.Series[str] = Field(nullable=True)
    TAX_INVOICE_DATE: pa.typing.Series[object] = Field(nullable=True)
    TOTAL_AMOUNT: pa.typing.Series[pandas_engine.Decimal] = Field(nullable=True, dtype_kwargs=_MONEY_KW)
    VAT_AMOUNT: pa.typing.Series[pandas_engine.Decimal] = Field(nullable=True, dtype_kwargs=_MONEY_KW)
    NET_AMOUNT: pa.typing.Series[pandas_engine.Decimal] = Field(nullable=True, dtype_kwargs=_MONEY_KW)
    # ``COPY``/``STAMP`` mirror the upstream OCROutputSchema's nullable ``BooleanDtype``: FAILED /
    # IQS-rejected pages null every document flag, and a plain NumPy ``bool`` cannot hold ``<NA>``.
    COPY: pa.typing.Series[pd.BooleanDtype] = Field(nullable=True)
    RECEIVER_SIGNATURE: pa.typing.Series[bool] = Field(nullable=True)
    WITHHOLDING_TAX: pa.typing.Series[pandas_engine.Decimal] = Field(nullable=True, dtype_kwargs=_MONEY_KW)
    INVOICE_NUMBER: pa.typing.Series[str] = Field(nullable=True)
    INVOICE_AMOUNT: pa.typing.Series[pandas_engine.Decimal] = Field(nullable=True, dtype_kwargs=_MONEY_KW)
    VAT_INVOICE: pa.typing.Series[pandas_engine.Decimal] = Field(nullable=True, dtype_kwargs=_MONEY_KW)
    STAMP: pa.typing.Series[pd.BooleanDtype] = Field(nullable=True)
    FILE_NAME: pa.typing.Series[str] = Field(nullable=True)
    FILE_PATH: pa.typing.Series[str] = Field(nullable=True)
    IQS_SCORE: pa.typing.Series[float] = Field(nullable=True)
    BUYER_NAME_LOOKUP_TH: pa.typing.Series[str] = Field(nullable=True)
    BUYER_ADDRESS_LOOKUP_TH: pa.typing.Series[str] = Field(nullable=True)
    BUYER_NAME_LOOKUP_ENG: pa.typing.Series[str] = Field(nullable=True)
    BUYER_ADDRESS_LOOKUP_ENG: pa.typing.Series[str] = Field(nullable=True)
    # Per-field + overall confidence scores from the BU validation model. Binary fields score
    # 0/1; amount fields use exponential decay (0..1); ``DOC_CONF_SCORE`` is the 0..100 average.
    DOC_NAME_CONF_SCORE: pa.typing.Series[float] = Field(nullable=True)
    BUYER_TAX_ID_CONF_SCORE: pa.typing.Series[float] = Field(nullable=True)
    VENDOR_TAX_ID_CONF_SCORE: pa.typing.Series[float] = Field(nullable=True)
    TAX_INVOICE_NUMBER_CONF_SCORE: pa.typing.Series[float] = Field(nullable=True)
    TAX_INVOICE_DATE_CONF_SCORE: pa.typing.Series[float] = Field(nullable=True)
    TOTAL_AMOUNT_CONF_SCORE: pa.typing.Series[float] = Field(nullable=True)
    VAT_AMOUNT_CONF_SCORE: pa.typing.Series[float] = Field(nullable=True)
    NET_AMOUNT_CONF_SCORE: pa.typing.Series[float] = Field(nullable=True)
    DOC_CONF_SCORE: pa.typing.Series[float] = Field(nullable=True)
    DOC_STATUS: pa.typing.Series[str] = Field(nullable=True)
    REMARK: pa.typing.Series[str] = Field(nullable=True)

    class Config:
        coerce = True
        strict = True


def to_extraction_output(processing_df: pd.DataFrame) -> pd.DataFrame:
    """Project an ``ExtractionProcessing`` frame onto the export schema, validated.

    Selects the :class:`ExtractionOutput` columns, dropping the internal ``DATADATE`` run
    marker. ``DOC_STATUS``/``REMARK`` already carry the folded Master-Buyer verdict.

    Args:
        processing_df: The builder's ``ExtractionProcessing`` frame.

    Returns:
        The frame validated against :class:`ExtractionOutput`.
    """
    cols = list(ExtractionOutput.__annotations__.keys())
    validated = ExtractionOutput.validate(processing_df[cols])
    return validated.sort_values(["FILE_NAME", "TAX_INVOICE_DATE", "TAX_INVOICE_NUMBER", "INVOICE_NUMBER"])
