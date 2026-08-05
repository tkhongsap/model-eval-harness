"""Pandera contract for the finalized OCR output frame (``OCRResult.final_df``)."""

from datetime import datetime
from typing import Any

import pandas as pd
import pandera.pandas as pa
from pandera import Field
from pandera.engines import pandas_engine

# Money columns carry exact ``Decimal`` values (THB satang precision). ``coerce``
# lands the JSON-dumped decimal strings on a fixed-scale Decimal dtype so summing
# them downstream stays exact instead of accumulating float error.
_MONEY_KW = {"precision": 18, "scale": 2}


class OCROutputSchema(pa.DataFrameModel):
    """Finalized post-processing output — one row per line item, ready for hand-off.

    Every data field is nullable: FAILED rows null all document/line fields, and the
    LEFT JOIN onto the page manifest can leave the file/page columns null on no-match.
    Only ``STATUS`` is always populated. ``coerce`` lands JSON-dumped values (e.g. the
    ISO-string ``TAX_INVOICE_DATE``) on their declared dtypes.

    ``TAX_INVOICE_DATE`` is declared as an object Series rather than pandera's ``date``
    Series type, because that type rejects an all-null column (which a zero-success run
    produces every time). Real values are coerced to ``datetime.date`` objects in
    ``ResultFinalizer._coerce_invoice_date`` (module/result_finalizer.py), not in a
    ``PostProcessingTask`` — that class no longer exists in this package.
    """

    DOC_NAME: pa.typing.Series[str] = Field(nullable=True)
    DOC_TYPE: pa.typing.Series[str] = Field(nullable=True)
    TAX_INVOICE_NUMBER: pa.typing.Series[str] = Field(nullable=True)
    # Object dtype: see class docstring for why, and where it is coerced to real dates.
    TAX_INVOICE_DATE: pa.typing.Series[object] = Field(nullable=True)
    CUSTOMER_NAME_TH: pa.typing.Series[str] = Field(nullable=True)
    CUSTOMER_ADDRESS_TH: pa.typing.Series[str] = Field(nullable=True)
    CUSTOMER_NAME_ENG: pa.typing.Series[str] = Field(nullable=True)
    CUSTOMER_ADDRESS_ENG: pa.typing.Series[str] = Field(nullable=True)
    CUSTOMER_TAX_ID: pa.typing.Series[str] = Field(nullable=True)
    CUSTOMER_BRANCH_CODE: pa.typing.Series[str] = Field(nullable=True)
    CUSTOMER_BRANCH_NAME: pa.typing.Series[str] = Field(nullable=True)
    VENDOR_NAME_TH: pa.typing.Series[str] = Field(nullable=True)
    VENDOR_NAME_ENG: pa.typing.Series[str] = Field(nullable=True)
    VENDOR_ADDRESS_TH: pa.typing.Series[str] = Field(nullable=True)
    VENDOR_ADDRESS_ENG: pa.typing.Series[str] = Field(nullable=True)
    VENDOR_TAX_ID: pa.typing.Series[str] = Field(nullable=True)
    VENDOR_BRANCH_CODE: pa.typing.Series[str] = Field(nullable=True)
    VENDOR_BRANCH_NAME: pa.typing.Series[str] = Field(nullable=True)
    BEFORE_VAT_AMOUNT: pa.typing.Series[pandas_engine.Decimal] = Field(nullable=True, dtype_kwargs=_MONEY_KW)
    VAT_AMOUNT: pa.typing.Series[pandas_engine.Decimal] = Field(nullable=True, dtype_kwargs=_MONEY_KW)
    AFTER_VAT_AMOUNT: pa.typing.Series[pandas_engine.Decimal] = Field(nullable=True, dtype_kwargs=_MONEY_KW)
    WITHHOLDING_TAX_AMOUNT: pa.typing.Series[pandas_engine.Decimal] = Field(nullable=True, dtype_kwargs=_MONEY_KW)
    NET_AMOUNT: pa.typing.Series[pandas_engine.Decimal] = Field(nullable=True, dtype_kwargs=_MONEY_KW)
    ITEM_NO: pa.typing.Series[pd.Int64Dtype] = Field(nullable=True)
    INVOICE_NUMBER: pa.typing.Series[str] = Field(nullable=True)
    DESCRIPTION: pa.typing.Series[str] = Field(nullable=True)
    QUANTITY: pa.typing.Series[float] = Field(nullable=True)
    UNIT_PRICE: pa.typing.Series[float] = Field(nullable=True)
    INVOICE_AMOUNT_BEFORE_VAT: pa.typing.Series[pandas_engine.Decimal] = Field(nullable=True, dtype_kwargs=_MONEY_KW)
    INVOICE_VAT_AMOUNT: pa.typing.Series[pandas_engine.Decimal] = Field(nullable=True, dtype_kwargs=_MONEY_KW)
    INVOICE_AMOUNT_AFTER_VAT: pa.typing.Series[pandas_engine.Decimal] = Field(nullable=True, dtype_kwargs=_MONEY_KW)
    COPY: pa.typing.Series[pd.BooleanDtype] = Field(nullable=True)
    PAYEE_SIGNATURE_FLAG: pa.typing.Series[pd.BooleanDtype] = Field(nullable=True)
    PAYEE_SIGNATURE_NAME: pa.typing.Series[str] = Field(nullable=True)
    AUTHORIZED_RECEIVER_SIGNATURE_FLAG: pa.typing.Series[pd.BooleanDtype] = Field(nullable=True)
    AUTHORIZED_RECEIVER_SIGNATURE_NAME: pa.typing.Series[str] = Field(nullable=True)
    AUTHORIZED_SIGNATORY_SIGNATURE_FLAG: pa.typing.Series[pd.BooleanDtype] = Field(nullable=True)
    AUTHORIZED_SIGNATORY_SIGNATURE_NAME: pa.typing.Series[str] = Field(nullable=True)
    STAMP: pa.typing.Series[pd.BooleanDtype] = Field(nullable=True)
    FILE_PATH: pa.typing.Series[str] = Field(nullable=True)
    FILE_NAME: pa.typing.Series[str] = Field(nullable=True)
    PAGE_NO: pa.typing.Series[pd.Int64Dtype] = Field(nullable=True)
    IQS_SCORE: pa.typing.Series[float] = Field(nullable=True)
    START_TIME: pa.typing.Series[datetime] = Field(nullable=True)
    END_TIME: pa.typing.Series[datetime] = Field(nullable=True)
    STATUS: pa.typing.Series[str] = Field(nullable=False)
    MESSAGE: pa.typing.Series[str] = Field(nullable=True)
    USAGE_METADATA: pa.typing.Series[dict[str, Any]] = Field(nullable=True)
    # Data-date (YYYYMMDD) the file was ingested under, carried from the pre-processing log via
    # the finalizer join. Nullable ``Int64`` (not a non-null ``int``) mirrors ``PAGE_NO``: both
    # come in through the page-manifest LEFT JOIN and tolerate a no-match without crashing.
    DATADATE: pa.typing.Series[pd.Int64Dtype] = Field(nullable=True)

    class Config:
        coerce = True
        strict = False
