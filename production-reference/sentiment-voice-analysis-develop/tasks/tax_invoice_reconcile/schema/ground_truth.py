"""Pandera schema for the tax-invoice fact-check ground-truth workbook.

The human-labelled ``ground_truth.xlsx`` carries one row per source document. Its headers
are free-text (with a curly apostrophe and non-breaking spaces in places), so each column is
aliased to a canonical ``snake_case`` field name via :meth:`GroundTruthSchema.validate` +
positional rename. Every cell is read as text (``dtype=str`` upstream) and compared against the
extraction output by :class:`~tasks.tax_invoice_reconcile.module.fact_check_evaluator.FactCheckEvaluator`;
the columns with no extraction counterpart (invoice/payment document, payment date, vendor code,
send date) are still validated so the workbook shape stays pinned, but they are not scored.
"""

from __future__ import annotations

import pandera.pandas as pa
from pandera import Field

# The join key against the extraction output (matched on the extension-stripped file name).
GROUND_TRUTH_FILE_KEY = "file_name"


class GroundTruthSchema(pa.DataFrameModel):
    """One row per labelled source document; aliases free-text headers to canonical names."""

    file_name: pa.typing.Series[str] = Field(alias="File Name", nullable=True)
    document_name: pa.typing.Series[str] = Field(alias="Document Name", nullable=True)
    buyer_name: pa.typing.Series[str] = Field(alias="Buyer Name", nullable=True)
    buyer_address: pa.typing.Series[str] = Field(alias="Buyer Address", nullable=True)
    buyer_tax_id: pa.typing.Series[str] = Field(alias="Buyer Tax ID", nullable=True)
    buyer_branch_code: pa.typing.Series[str] = Field(alias="Buyer Branch Code", nullable=True)
    buyer_branch_name: pa.typing.Series[str] = Field(alias="Buyer Branch Name", nullable=True)
    vendor_name: pa.typing.Series[str] = Field(alias="Vendor Name", nullable=True)
    vendor_address: pa.typing.Series[str] = Field(alias="Vendor Address", nullable=True)
    vendor_tax_id: pa.typing.Series[str] = Field(alias="Vendor Tax ID", nullable=True)
    vendor_branch_code: pa.typing.Series[str] = Field(alias="Vendor Branch Code", nullable=True)
    vendor_branch_name: pa.typing.Series[str] = Field(alias="Vendor Branch Name", nullable=True)
    tax_invoice_number: pa.typing.Series[str] = Field(alias="Tax Invoice Number", nullable=True)
    tax_invoice_date: pa.typing.Series[str] = Field(alias="Tax Invoice Date", nullable=True)
    total_amount: pa.typing.Series[str] = Field(alias="Total Amount", nullable=True)
    vat: pa.typing.Series[str] = Field(alias="VAT", nullable=True)
    net_amount: pa.typing.Series[str] = Field(alias="Net Amount", nullable=True)
    copy: pa.typing.Series[str] = Field(alias="Copy", nullable=True)
    # NB: the header uses a curly apostrophe (U+2019), not an ASCII "'".
    receiver_signature: pa.typing.Series[str] = Field(alias="Receiver’s Signature", nullable=True)
    withholding_tax: pa.typing.Series[str] = Field(alias="Withholding Tax", nullable=True)
    invoice_number: pa.typing.Series[str] = Field(alias="Invoice Number", nullable=True)
    invoice_amount: pa.typing.Series[str] = Field(alias="Invoice Amount", nullable=True)
    vat_invoice: pa.typing.Series[str] = Field(alias="Vat Invoice", nullable=True)
    stamp: pa.typing.Series[str] = Field(alias="Stamp", nullable=True)

    class Config:
        coerce = True
        # Non-scored trailing columns (Invoice Document / Payment Document / Payment Date /
        # Vendor Code / Send Date) may or may not be present; do not fail on extras.
        strict = False
