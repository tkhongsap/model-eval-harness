"""Pandera schema for the final reconciled Output Report.

Every column is text (``str``): the report mixes ``'Yes'``/``'No'`` flags, free-text
remarks, dates rendered as ``dd/MM/yyyy``, and amounts rendered as plain numeric
strings in the same frame, so a uniform string contract is the only one that can hold
a value like ``'No'`` (a blank Withholding Tax / Vat Invoice) next to a numeric column.
Column order here is the contract used to rename the builder's snake_case output onto
these aliases before validation.
"""

from __future__ import annotations

import pandas as pd
import pandera.pandas as pa
from pandera import Field


class ReportOutput(pa.DataFrameModel):
    """Final Output Report — all columns are nullable strings."""

    file_name: pa.typing.Series[str] = Field(nullable=True, alias="File Name")
    document_name: pa.typing.Series[str] = Field(nullable=True, alias="Document Name")
    buyer_name_th: pa.typing.Series[str] = Field(nullable=True, alias="Buyer Name (TH)")
    buyer_address_th: pa.typing.Series[str] = Field(nullable=True, alias="Buyer Address (TH)")
    buyer_name_eng: pa.typing.Series[str] = Field(nullable=True, alias="Buyer Name (ENG)")
    buyer_address_eng: pa.typing.Series[str] = Field(nullable=True, alias="Buyer Address (ENG)")
    buyer_tax_id: pa.typing.Series[str] = Field(nullable=True, alias="Buyer Tax ID")
    buyer_branch_code: pa.typing.Series[str] = Field(nullable=True, alias="Buyer Branch Code")
    buyer_branch_name: pa.typing.Series[str] = Field(nullable=True, alias="Buyer Branch Name")
    vendor_name_th: pa.typing.Series[str] = Field(nullable=True, alias="Vendor Name (TH)")
    vendor_address_th: pa.typing.Series[str] = Field(nullable=True, alias="Vendor Address (TH)")
    vendor_name_eng: pa.typing.Series[str] = Field(nullable=True, alias="Vendor Name (ENG)")
    vendor_address_eng: pa.typing.Series[str] = Field(nullable=True, alias="Vendor Address (ENG)")
    vendor_tax_id: pa.typing.Series[str] = Field(nullable=True, alias="Vendor Tax ID")
    vendor_branch_code: pa.typing.Series[str] = Field(nullable=True, alias="Vendor Branch Code")
    vendor_branch_name: pa.typing.Series[str] = Field(nullable=True, alias="Vendor Branch Name")
    tax_invoice_number: pa.typing.Series[str] = Field(nullable=True, alias="Tax Invoice Number")
    tax_invoice_date: pa.typing.Series[str] = Field(nullable=True, alias="Tax Invoice Date")
    total_amount: pa.typing.Series[str] = Field(nullable=True, alias="Total Amount")
    vat: pa.typing.Series[str] = Field(nullable=True, alias="VAT")
    net_amount: pa.typing.Series[str] = Field(nullable=True, alias="Net Amount")
    copy: pa.typing.Series[str] = Field(nullable=True, alias="Copy")
    receiver_signature: pa.typing.Series[str] = Field(nullable=True, alias="Receiver's Signature")
    withholding_tax: pa.typing.Series[str] = Field(nullable=True, alias="Withholding Tax")
    invoice_number: pa.typing.Series[str] = Field(nullable=True, alias="Invoice Number")
    invoice_amount: pa.typing.Series[str] = Field(nullable=True, alias="Invoice Amount")
    vat_invoice: pa.typing.Series[str] = Field(nullable=True, alias="Vat Invoice")
    stamp: pa.typing.Series[str] = Field(nullable=True, alias="Stamp")
    ai_extract_result: pa.typing.Series[str] = Field(nullable=True, alias="AI_Extract_Result")
    remark_ai_extract: pa.typing.Series[str] = Field(nullable=True, alias="Remark_AI Extract")
    invoice_document: pa.typing.Series[str] = Field(nullable=True, alias="Invoice Document (VAT Report)")
    payment_document: pa.typing.Series[str] = Field(nullable=True, alias="Payment Document (VAT Report)")
    payment_date: pa.typing.Series[str] = Field(nullable=True, alias="Payment Date (VAT Report)")
    vendor_code: pa.typing.Series[str] = Field(nullable=True, alias="Vendor Code (VAT Report)")
    send_date: pa.typing.Series[str] = Field(nullable=True, alias="Send Date")
    mapping_status: pa.typing.Series[str] = Field(nullable=True, alias="Mapping_Status")
    remark_mapping: pa.typing.Series[str] = Field(nullable=True, alias="Remark_Mapping")

    class Config:
        coerce = True
        strict = True

    @pa.dataframe_parser
    def _blank_na(cls, df: pd.DataFrame) -> pd.DataFrame:  # noqa: N805  (pandera passes the model class)
        """Render every NULL/NaN/NA cell as an empty string before string coercion.

        The frame is all-text by contract, but an all-NULL column can arrive from
        DuckDB typed as numeric (e.g. ``Int32``); filling across all columns (not just
        ``object`` ones) keeps blanks blank instead of leaving ``NA`` or coercing into
        the literal strings ``'None'``/``'nan'``.
        """
        return df.astype(object).where(df.notna(), "")
