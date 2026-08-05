"""Business-facing remark/status text used by the extraction report and reconciliation engine."""

from enum import Enum

# Fallback Remark_AI Extract text when a non-Completed extraction carries no specific reason,
# so every non-Completed AI_Extract_Result row still tells the business user something.
EXTRACTION_REVIEW_REMARK = "Extraction requires review"

# Business-facing remark for a true system/batch failure (OCR STATUS=FAILED: batch job failed,
# batch line error, or no response text). The raw technical cause is kept in the pre-processing
# log; the extraction/output reports show this clean line instead of misleading "… is missing".
EXTRACTION_SYSTEM_FAILURE_REMARK = "Extraction failed due to a system error."


class MappingMasterMessage(Enum):
    """Remark text for a Master Buyer lookup mismatch or miss."""

    COMPANY_CODE_MISMATCH_MESSAGE = "Company code doesn't match Master Buyer"
    BUYER_TAX_ID_NOT_FOUND_MESSAGE = "Buyer Tax ID not found in Master Buyer"
    BUYER_NAME_MISMATCH_MESSAGE = "Buyer Name mismatch with Master Buyer"
    BUYER_ADDRESS_MISMATCH_MESSAGE = "Buyer Address mismatch with Master Buyer"


class RequiredFieldMessage(Enum):
    """Remark text for a required extraction field that came back blank."""

    DOC_NAME_MISSING_MESSAGE = "Document Name is missing"
    BUYER_NAME_MISSING_MESSAGE = "Buyer Name is missing"
    BUYER_ADDRESS_MISSING_MESSAGE = "Buyer Address is missing"
    BUYER_TAX_ID_MISSING_MESSAGE = "Buyer Tax ID is missing"
    BUYER_BRANCH_CODE_MISSING_MESSAGE = "Buyer Branch Code is missing"
    BUYER_BRANCH_NAME_MISSING_MESSAGE = "Buyer Branch Name is missing"
    VENDOR_NAME_MISSING_MESSAGE = "Vendor Name is missing"
    VENDOR_ADDRESS_MISSING_MESSAGE = "Vendor Address is missing"
    VENDOR_TAX_ID_MISSING_MESSAGE = "Vendor Tax ID is missing"
    VENDOR_BRANCH_CODE_MISSING_MESSAGE = "Vendor Branch Code is missing"
    VENDOR_BRANCH_NAME_MISSING_MESSAGE = "Vendor Branch Name is missing"
    TAX_INVOICE_NUMBER_MISSING_MESSAGE = "Tax Invoice Number is missing"
    TAX_INVOICE_DATE_MISSING_MESSAGE = "Tax Invoice Date is missing"
    TOTAL_AMOUNT_MISSING_MESSAGE = "Total Amount is missing"
    VAT_AMOUNT_MISSING_MESSAGE = "VAT Amount is missing"
    NET_AMOUNT_MISSING_MESSAGE = "Net Amount is missing"


class ValidationMessage(Enum):
    """Remark text for an extraction field that failed its format/range validation rule."""

    BUYER_TAX_ID_RULE_MESSAGE = "Buyer Tax ID don't match the required format (13 digits)"
    BUYER_BRANCH_CODE_RULE_MESSAGE = "Buyer Branch Code don't match the required format (5 digits)"
    VENDOR_TAX_ID_RULE_MESSAGE = "Vendor Tax ID don't match the required format (13 digits)"
    VENDOR_BRANCH_CODE_RULE_MESSAGE = "Vendor Branch Code don't match the required format (5 digits)"
    TOTAL_AMOUNT_RULE_MESSAGE = "Total Amount is incorrect"
    TOTAL_AMOUNT_GT_NEGATIVE_RULE_MESSAGE = "Total Amount must be greater than or equal to 0"
    VAT_AMOUNT_RULE_MESSAGE = "VAT Amount is incorrect"
    VAT_AMOUNT_GT_NEGATIVE_RULE_MESSAGE = "VAT Amount must be greater than or equal to 0"
    NET_AMOUNT_RULE_MESSAGE = "Net Amount is incorrect"
    NET_AMOUNT_GT_NEGATIVE_RULE_MESSAGE = "Net Amount must be greater than or equal to 0"


class MappingZ45Message(Enum):
    """Remark text for the Z45 reconciliation outcome: missing field, mismatch, or no match."""

    # Function 3 — extracted value does not match the linked Z45 row ("Not match").
    COMPANY_CODE_MISMATCH_MESSAGE = "Company code does not match Z45 report"
    INVOICE_NUMBER_MISMATCH_MESSAGE = "Invoice Number does not match Z45 report"
    VENDOR_NAME_MISMATCH_MESSAGE = "Vendor Name does not match Z45 report"
    VAT_AMOUNT_MISMATCH_MESSAGE = "VAT amount does not match Z45 report"
    PAYMENT_DATE_MISMATCH_MESSAGE = "Payment date does not match Z45 report"
    # No Z45 candidate matched any key — the row could not be located at all.
    NO_MATCH_MESSAGE = "No matching record in Z45 report"
    # Scenario 0 — intentionally not reconciled against Z45 (blank Mapping_Status by design). The
    # specific extraction issue is already spelled out in the row's Remark_AI Extract column.
    COPY_NOT_RECONCILED_MESSAGE = "Copy document, not reconciled with Z45 report"
    ISSUE_NOT_RECONCILED_MESSAGE = (
        "Extraction has an issue (e.g. system failure, unsupported document, blank line item),"
        " not reconciled with Z45 report"
    )
