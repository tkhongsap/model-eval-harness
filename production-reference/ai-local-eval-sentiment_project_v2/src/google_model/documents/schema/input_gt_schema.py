from __future__ import annotations

import pandas as pd
import pandera.pandas as pa
from pandera import Field
from pandera.engines import pandas_engine

_MONEY_KW = {"precision": 18, "scale": 2}

class InputGTSchema(pa.DataFrameModel):
    """Schema for the input ground truth sheet, keyed by file name."""

    no: pa.typing.Series[int] = Field(alias="No", nullable=True)
    unique_identifier: pa.typing.Series[str] = Field(alias="Unique identifier", nullable=True)
    file_name: pa.typing.Series[str] = Field(alias="File name", nullable=True)
    doc_name: pa.typing.Series[str] = Field(alias="Document name", nullable=True)
    doc_type: pa.typing.Series[str] = Field(alias="Document type", nullable=True)
    buyer_name_th: pa.typing.Series[str] = Field(alias="Buyer name th", nullable=True)
    buyer_address_th: pa.typing.Series[str] = Field(alias="Buyer address th", nullable=True)
    buyer_name_eng: pa.typing.Series[str] = Field(alias="Buyer name eng", nullable=True)
    buyer_address_eng: pa.typing.Series[str] = Field(alias="Buyer address eng", nullable=True)
    buyer_tax_id: pa.typing.Series[str] = Field(alias="Buyer tax id", nullable=True)
    buyer_branch_code: pa.typing.Series[str] = Field(alias="Buyer branch code", nullable=True)
    buyer_branch_name: pa.typing.Series[str] = Field(alias="Buyer branch name", nullable=True)
    vendor_name_th: pa.typing.Series[str] = Field(alias="Vendor name th", nullable=True)
    vendor_address_th: pa.typing.Series[str] = Field(alias="Vendor address th", nullable=True)
    vendor_name_eng: pa.typing.Series[str] = Field(alias="Vendor name eng", nullable=True)
    vendor_address_eng: pa.typing.Series[str] = Field(alias="Vendor address eng", nullable=True)
    vendor_tax_id: pa.typing.Series[str] = Field(alias="Vendor tax id", nullable=True)
    vendor_branch_code: pa.typing.Series[str] = Field(alias="Vendor branch code", nullable=True)
    vendor_branch_name: pa.typing.Series[str] = Field(alias="Vendor branch name", nullable=True)
    tax_invoice_number: pa.typing.Series[str] = Field(alias="Tax invoice number", nullable=True)
    tax_invoice_date: pa.typing.Series[object] = Field(alias="Tax invoice date", nullable=True)
    total_amount: pa.typing.Series[pandas_engine.Decimal] = Field(alias="Total amount", nullable=True, dtype_kwargs=_MONEY_KW)
    vat_amount: pa.typing.Series[pandas_engine.Decimal] = Field(alias="Vat amount", nullable=True, dtype_kwargs=_MONEY_KW)
    net_amount: pa.typing.Series[pandas_engine.Decimal] = Field(alias="Net amount", nullable=True, dtype_kwargs=_MONEY_KW)
    copy: pa.typing.Series[pd.BooleanDtype] = Field(alias="Copy", nullable=True)
    payee_signature_flag: pa.typing.Series[pd.BooleanDtype] = Field(alias="Payee signature flag", nullable=True)
    authorized_receiver_signature_flag: pa.typing.Series[pd.BooleanDtype] = Field(alias="Authorized receiver signature flag", nullable=True)
    authorized_signatory_signature_flag: pa.typing.Series[pd.BooleanDtype] = Field(alias="Authorized signatory signature flag", nullable=True)
    withholding_tax: pa.typing.Series[pandas_engine.Decimal] = Field(alias="Withholding tax", nullable=True, dtype_kwargs=_MONEY_KW)
    invoice_number: pa.typing.Series[str] = Field(alias="Invoice number", nullable=True)
    invoice_amount: pa.typing.Series[pandas_engine.Decimal] = Field(alias="Invoice amount", nullable=True, dtype_kwargs=_MONEY_KW)
    vat_invoice: pa.typing.Series[pandas_engine.Decimal] = Field(alias="Vat invoice", nullable=True, dtype_kwargs=_MONEY_KW)
    stamp: pa.typing.Series[pd.BooleanDtype] = Field(alias="Stamp", nullable=True)

    class Config:
        coerce = True
        strict = False