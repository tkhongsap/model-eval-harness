"""Schema + export headers for the enriched SAP ZAPRPT45 (Z45) output report.

The enriched Z45 is a faithful re-export of the source ZAPRPT45 file with one extra
column (``Mapping Tax Invoice Status``). Its column headers must stay **exactly** as the
source SAP export — including the two truncated, identical ``Tax Cleari`` headers (one
holds the clearing document, the other the clearing date).

Pandera collapses two model fields that share an ``alias`` (the schema would expose 37
columns for 38 fields), so it cannot validate the duplicate-header frame. ``Z45Output``
therefore documents the typed contract / canonical field order, while
``Z45_OUTPUT_HEADERS`` is the literal export header row (duplicates preserved) that the
reconciliation builder applies positionally after validating the unique field-name frame.
"""

from __future__ import annotations

import pandera.pandas as pa
from pandera import Field


class Z45Output(pa.DataFrameModel):
    """Canonical field order + source-header aliases for the enriched Z45 report."""

    company: pa.typing.Series[str] = Field(nullable=True, alias="Company")
    ref_doc_inv: pa.typing.Series[str] = Field(nullable=True, alias="Ref. Doc  (Inv.)")
    doc_type: pa.typing.Series[str] = Field(nullable=True, alias="Doc Type (")
    invoice_document: pa.typing.Series[str] = Field(nullable=True, alias="Invoice Do")
    vendor_code: pa.typing.Series[str] = Field(nullable=True, alias="Vendor cod")
    vendor_name: pa.typing.Series[str] = Field(nullable=True, alias="Vendor Name")
    vat_amount: pa.typing.Series[str] = Field(nullable=True, alias="Vat Amount")
    tax_base_amount: pa.typing.Series[str] = Field(nullable=True, alias="Tax Base A")
    payment_document: pa.typing.Series[str] = Field(nullable=True, alias="Payment Do")
    payment_method: pa.typing.Series[str] = Field(nullable=True, alias="Payment Me")
    short_text: pa.typing.Series[str] = Field(nullable=True, alias="Short text")
    encashment: pa.typing.Series[str] = Field(nullable=True, alias="Encachment")
    payment_date: pa.typing.Series[str] = Field(nullable=True, alias="Payment Da")
    cheque_no: pa.typing.Series[str] = Field(nullable=True, alias="Cheque No.")
    payee_name: pa.typing.Series[str] = Field(nullable=True, alias="Payeename")
    doc_header_text: pa.typing.Series[str] = Field(nullable=True, alias="Doc.Header")
    document_currency: pa.typing.Series[str] = Field(nullable=True, alias="Document C")
    net_paid: pa.typing.Series[str] = Field(nullable=True, alias="Net paid")
    cost_center: pa.typing.Series[str] = Field(nullable=True, alias="Cost Cente")
    tax_code: pa.typing.Series[str] = Field(nullable=True, alias="Tax Code")
    tax_clearing_doc: pa.typing.Series[str] = Field(nullable=True, alias="Tax Cleari")
    tax_clearing_date: pa.typing.Series[str] = Field(nullable=True, alias="Tax Cleari")
    tax_id: pa.typing.Series[str] = Field(nullable=True, alias="Tax ID")
    branch_code: pa.typing.Series[str] = Field(nullable=True, alias="Branch")
    email_requester: pa.typing.Series[str] = Field(nullable=True, alias="Email requester")
    tax_invoice_number: pa.typing.Series[str] = Field(nullable=True, alias="Tax Invoice Number")
    check_duplicate: pa.typing.Series[str] = Field(nullable=True, alias="CHECK DUPLICATE")
    send_date: pa.typing.Series[str] = Field(nullable=True, alias="SEND DATE(Vlookup)")
    aging: pa.typing.Series[str] = Field(nullable=True, alias="Aging")
    pending_for_release: pa.typing.Series[str] = Field(nullable=True, alias="Pending for Release")
    vat_status: pa.typing.Series[str] = Field(nullable=True, alias="VAT_Status(Vlookup)")
    clearing_doc: pa.typing.Series[str] = Field(nullable=True, alias="Clearing Doc")
    process_date: pa.typing.Series[str] = Field(nullable=True, alias="วันที่ทำ")
    remark: pa.typing.Series[str] = Field(nullable=True, alias="Remark")
    user: pa.typing.Series[str] = Field(nullable=True, alias="user")
    user_outward: pa.typing.Series[str] = Field(nullable=True, alias="User Outward")
    department: pa.typing.Series[str] = Field(nullable=True, alias="Department")
    mapping_tax_invoice_status: pa.typing.Series[str] = Field(nullable=True, alias="Mapping Tax Invoice Status")

    class Config:
        coerce = True
        strict = True


# Literal export header row, in canonical field order, with the source's duplicate
# ``Tax Cleari`` headers preserved. Kept in lock-step with the field order above.
Z45_OUTPUT_HEADERS: list[str] = [
    "Company",
    "Ref. Doc  (Inv.)",
    "Doc Type (",
    "Invoice Do",
    "Vendor cod",
    "Vendor Name",
    "Vat Amount",
    "Tax Base A",
    "Payment Do",
    "Payment Me",
    "Short text",
    "Encachment",
    "Payment Da",
    "Cheque No.",
    "Payeename",
    "Doc.Header",
    "Document C",
    "Net paid",
    "Cost Cente",
    "Tax Code",
    "Tax Cleari",
    "Tax Cleari",
    "Tax ID",
    "Branch",
    "Email requester",
    "Tax Invoice Number",
    "CHECK DUPLICATE",
    "SEND DATE(Vlookup)",
    "Aging",
    "Pending for Release",
    "VAT_Status(Vlookup)",
    "Clearing Doc",
    "วันที่ทำ",
    "Remark",
    "user",
    "User Outward",
    "Department",
    "Mapping Tax Invoice Status",
]
