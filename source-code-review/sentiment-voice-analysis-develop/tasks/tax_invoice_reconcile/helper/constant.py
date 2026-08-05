"""Shared enums and constants for the tax-invoice reconcile package."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ExtractionStatus(Enum):
    """Per-document extraction verdict shown in the ``DOC_STATUS`` column."""

    COMPLETED = "Completed"
    REQUIRES_REVIEW = "RequiresReview"


class MappingZ45Status(Enum):
    """Per-line Z45 reconciliation verdict shown in ``Mapping Tax Invoice Status``."""

    COMPLETED = "Completed"
    INCOMPLETED = "Incompleted"


# ---------------------------------------------------------------------------
# Fact-check field mapping and constants. See DEVELOPER_GUIDE.md § 7.1.
# ---------------------------------------------------------------------------

# Comparison types — drive per-field normalization in the evaluator before the exact-match test.
COMPARE_TEXT = "text"  # trim + collapse whitespace + casefold
COMPARE_TAXID = "taxid"  # digits only, left-padded to 13
COMPARE_AMOUNT = "amount"  # strip thousands separators, compare as 2dp Decimal
COMPARE_DATE = "date"  # parse to an ISO date (GT is dd/mm/yyyy; extraction is a date object)
COMPARE_BOOL = "bool"  # Yes/No/True/False -> canonical true/false

# Sentinel used when both sides are blank/null — GT "N/A" and an empty extraction value agree.
NA_SENTINEL = "N/A"

# Aggregate row label + the AI-Operation log wiring.
OVERALL_LABEL = "overall"
FACT_CHECK_LOG_TYPE = "fact_check"
FACT_CHECK_LOG_MESSAGE = "AI-Operation Fact Check log"


@dataclass(frozen=True)
class FactCheckField:
    """One scored field: its display label, GT column, extraction column(s), and compare type.

    Attributes:
        label: Human-facing field name emitted as the log ``label``.
        gt_field: Canonical ground-truth column name (from ``GroundTruthSchema``).
        extraction_columns: One or more ``ExtractionProcessing`` columns; a match against any
            counts as correct (Thai/English name & address fields carry both).
        compare: One of the ``COMPARE_*`` constants.
    """

    label: str
    gt_field: str
    extraction_columns: tuple[str, ...]
    compare: str


# Ordered list of the 23 scored fields. See DEVELOPER_GUIDE.md § 7.1.
FIELD_MAPPING: tuple[FactCheckField, ...] = (
    FactCheckField("Document Name", "document_name", ("DOC_NAME",), COMPARE_TEXT),
    FactCheckField("Buyer Name", "buyer_name", ("BUYER_NAME_TH", "BUYER_NAME_ENG"), COMPARE_TEXT),
    FactCheckField("Buyer Address", "buyer_address", ("BUYER_ADDRESS_TH", "BUYER_ADDRESS_ENG"), COMPARE_TEXT),
    FactCheckField("Buyer Tax ID", "buyer_tax_id", ("BUYER_TAX_ID",), COMPARE_TAXID),
    FactCheckField("Buyer Branch Code", "buyer_branch_code", ("BUYER_BRANCH_CODE",), COMPARE_TEXT),
    FactCheckField("Buyer Branch Name", "buyer_branch_name", ("BUYER_BRANCH_NAME",), COMPARE_TEXT),
    FactCheckField("Vendor Name", "vendor_name", ("VENDOR_NAME_TH", "VENDOR_NAME_ENG"), COMPARE_TEXT),
    FactCheckField("Vendor Address", "vendor_address", ("VENDOR_ADDRESS_TH", "VENDOR_ADDRESS_ENG"), COMPARE_TEXT),
    FactCheckField("Vendor Tax ID", "vendor_tax_id", ("VENDOR_TAX_ID",), COMPARE_TAXID),
    FactCheckField("Vendor Branch Code", "vendor_branch_code", ("VENDOR_BRANCH_CODE",), COMPARE_TEXT),
    FactCheckField("Vendor Branch Name", "vendor_branch_name", ("VENDOR_BRANCH_NAME",), COMPARE_TEXT),
    FactCheckField("Tax Invoice Number", "tax_invoice_number", ("TAX_INVOICE_NUMBER",), COMPARE_TEXT),
    FactCheckField("Tax Invoice Date", "tax_invoice_date", ("TAX_INVOICE_DATE",), COMPARE_DATE),
    FactCheckField("Total Amount", "total_amount", ("TOTAL_AMOUNT",), COMPARE_AMOUNT),
    FactCheckField("VAT", "vat", ("VAT_AMOUNT",), COMPARE_AMOUNT),
    FactCheckField("Net Amount", "net_amount", ("NET_AMOUNT",), COMPARE_AMOUNT),
    FactCheckField("Copy", "copy", ("COPY",), COMPARE_BOOL),
    FactCheckField("Receiver's Signature", "receiver_signature", ("RECEIVER_SIGNATURE",), COMPARE_BOOL),
    FactCheckField("Withholding Tax", "withholding_tax", ("WITHHOLDING_TAX",), COMPARE_AMOUNT),
    FactCheckField("Invoice Number", "invoice_number", ("INVOICE_NUMBER",), COMPARE_TEXT),
    FactCheckField("Invoice Amount", "invoice_amount", ("INVOICE_AMOUNT",), COMPARE_AMOUNT),
    FactCheckField("Vat Invoice", "vat_invoice", ("VAT_INVOICE",), COMPARE_AMOUNT),
    FactCheckField("Stamp", "stamp", ("STAMP",), COMPARE_BOOL),
)
