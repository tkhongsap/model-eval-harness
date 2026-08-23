from __future__ import annotations

import pandas as pd
import pandera.pandas as pa
from pandera import Field
from pandera.engines import pandas_engine

_MONEY_KW = {"precision": 18, "scale": 2}

class ProcessingSchema(pa.DataFrameModel):
    """Intermediate frame between the model response and the output sheet.

    Two naming rules are load-bearing (``build_output_df`` maps by name): every **annotation**
    is the matching ``ReceiptExtraction`` / ``InvoiceLineItem`` field name, so the declared
    columns *are* the subset of the model response the sheet uses (undeclared fields are
    dropped; ``FILE_NAME`` alone rides on the item rather than the page), and every **alias**
    is the matching ``OutputSchema`` *attribute* name, which renames the frame onto the sheet's
    headers without a hand-written map. Breaking either rule silently produces an all-null
    column that still validates.
    """

    FILE_NAME: pa.typing.Series[str] = Field(alias="file_name", nullable=True)
    DOC_NAME: pa.typing.Series[str] = Field(alias="doc_name", nullable=True)
    DOC_TYPE: pa.typing.Series[str] = Field(alias="doc_type", nullable=True)
    CUSTOMER_NAME_TH: pa.typing.Series[str] = Field(alias="buyer_name_th", nullable=True)
    CUSTOMER_ADDRESS_TH: pa.typing.Series[str] = Field(alias="buyer_address_th", nullable=True)
    CUSTOMER_NAME_ENG: pa.typing.Series[str] = Field(alias="buyer_name_eng", nullable=True)
    CUSTOMER_ADDRESS_ENG: pa.typing.Series[str] = Field(alias="buyer_address_eng", nullable=True)
    CUSTOMER_TAX_ID: pa.typing.Series[str] = Field(alias="buyer_tax_id", nullable=True)
    CUSTOMER_BRANCH_CODE: pa.typing.Series[str] = Field(alias="buyer_branch_code", nullable=True)
    CUSTOMER_BRANCH_NAME: pa.typing.Series[str] = Field(alias="buyer_branch_name", nullable=True)
    VENDOR_NAME_TH: pa.typing.Series[str] = Field(alias="vendor_name_th", nullable=True)
    VENDOR_ADDRESS_TH: pa.typing.Series[str] = Field(alias="vendor_address_th", nullable=True)
    VENDOR_NAME_ENG: pa.typing.Series[str] = Field(alias="vendor_name_eng", nullable=True)
    VENDOR_ADDRESS_ENG: pa.typing.Series[str] = Field(alias="vendor_address_eng", nullable=True)
    VENDOR_TAX_ID: pa.typing.Series[str] = Field(alias="vendor_tax_id", nullable=True)
    VENDOR_BRANCH_CODE: pa.typing.Series[str] = Field(alias="vendor_branch_code", nullable=True)
    VENDOR_BRANCH_NAME: pa.typing.Series[str] = Field(alias="vendor_branch_name", nullable=True)
    TAX_INVOICE_NUMBER: pa.typing.Series[str] = Field(alias="tax_invoice_number", nullable=True)
    TAX_INVOICE_DATE: pa.typing.Series[object] = Field(alias="tax_invoice_date", nullable=True)
    BEFORE_VAT_AMOUNT: pa.typing.Series[pandas_engine.Decimal] = Field(alias="total_amount", nullable=True, dtype_kwargs=_MONEY_KW)
    VAT_AMOUNT: pa.typing.Series[pandas_engine.Decimal] = Field(alias="vat_amount", nullable=True, dtype_kwargs=_MONEY_KW)
    NET_AMOUNT: pa.typing.Series[pandas_engine.Decimal] = Field(alias="net_amount", nullable=True, dtype_kwargs=_MONEY_KW)
    COPY: pa.typing.Series[pd.BooleanDtype] = Field(alias="copy", nullable=True)
    PAYEE_SIGNATURE_FLAG: pa.typing.Series[pd.BooleanDtype] = Field(alias="payee_signature_flag", nullable=True)
    AUTHORIZED_RECEIVER_SIGNATURE_FLAG: pa.typing.Series[pd.BooleanDtype] = Field(alias="authorized_receiver_signature_flag", nullable=True)
    AUTHORIZED_SIGNATORY_SIGNATURE_FLAG: pa.typing.Series[pd.BooleanDtype] = Field(alias="authorized_signatory_signature_flag", nullable=True)
    WITHHOLDING_TAX_AMOUNT: pa.typing.Series[pandas_engine.Decimal] = Field(alias="withholding_tax", nullable=True, dtype_kwargs=_MONEY_KW)
    INVOICE_NUMBER: pa.typing.Series[str] = Field(alias="invoice_number", nullable=True)
    INVOICE_AMOUNT_BEFORE_VAT: pa.typing.Series[pandas_engine.Decimal] = Field(alias="invoice_amount", nullable=True, dtype_kwargs=_MONEY_KW)
    INVOICE_VAT_AMOUNT: pa.typing.Series[pandas_engine.Decimal] = Field(alias="vat_invoice", nullable=True, dtype_kwargs=_MONEY_KW)
    STAMP: pa.typing.Series[pd.BooleanDtype] = Field(alias="stamp", nullable=True)

    class Config:
        coerce = True
        strict = False