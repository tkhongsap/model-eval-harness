"""Pandera schema for the intermediate extraction report.

This is the typed frame produced by :class:`ExtractionReportBuilder` (OCR output
aggregated per document + Master-Buyer enrichment) and consumed by
:class:`ReconciliationBuilder`. The Master-Buyer verdict is already folded in:
``DOC_STATUS`` is ``Completed`` only when OCR succeeded *and* buyer name+address match,
and ``REMARK`` carries the OCR message plus any company-code / tax-id / name / address
mismatch reasons. Amounts/dates stay typed here (not stringified yet) because the Z45
reconciliation matches VAT amounts and payment month on real ``Decimal``/``date`` values;
the string-cast happens only at the final report stage.
"""

from __future__ import annotations

import pandas as pd
import pandera.pandas as pa
from pandera import Field
from pandera.engines import pandas_engine

# Money columns carry exact ``Decimal`` values (THB satang precision), matching the
# upstream :class:`OCROutputSchema` so the reconciled report stays exact.
_MONEY_KW = {"precision": 18, "scale": 2}


class ExtractionProcessing(pa.DataFrameModel):
    """One row per resolved document: extracted fields + the folded Master-Buyer verdict."""

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
    # Nullable ``Int64`` (not plain ``int``) mirrors the upstream OCROutputSchema: a manifest
    # no-match can leave ``DATADATE`` NULL, which a NumPy ``int`` column cannot hold.
    DATADATE: pa.typing.Series[pd.Int64Dtype] = Field(nullable=True)
    ISSUE_FLAG: pa.typing.Series[pd.BooleanDtype] = Field(nullable=True)

    class Config:
        coerce = True
        strict = True
