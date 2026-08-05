"""File-level, page-level, and row-level status enums for the OCR pipeline."""

from enum import Enum


class JobStatus(Enum):
    """Processing status for a source file in the pre-processing log."""

    INITIAL = "INITIAL"
    PENDING = "PENDING"  # all IQS-valid pages submitted to batch
    PARTIAL = "PARTIAL"  # some pages submitted, some IQS-rejected
    # Rejected in pre-processing: all pages failed IQS, or unsupported file type — nothing
    # submitted; the message column says which.
    REJECTED = "REJECTED"
    FAILED = "FAILED"  # technical error (upload, PDF parse, batch submit)
    SUCCESS = "SUCCESS"  # post-processing complete
    SUCCESS_WITH_FAILURE = "SUCCESS_WITH_FAILURE"  # post-proc complete, some pages/lines failed validation


class QualityStatus(Enum):
    """Per-page IQS gate result."""

    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


class OCROutputStatus(Enum):
    """OCR extraction outcome for a line item (each consuming domain validates downstream)."""

    SUCCESS = "SUCCESS"  # line item extracted successfully; the domain applies its own validation
    FAILED = "FAILED"  # prediction/parse failure, IQS-rejected page, or dead batch job
    SUSPICIOUS = "SUSPICIOUS"  # document carries a prompt-injection / jailbreak attempt (DOC_TYPE=Suspicious)
    UNSUPPORTED = "UNSUPPORTED"  # document type not supported (DOC_TYPE=Other)
    BLANK = "BLANK"  # no text extracted at all, or no line items extracted (e.g. header-only invoice)


# Lifecycle order of JobStatus values (low -> high); tiebreaker for latest_status_per_file.
# See DEVELOPER_GUIDE.md § 5 ("STATUS_RANK — and why it exists") for the full rationale.
STATUS_RANK: dict[str, int] = {
    JobStatus.INITIAL.value: 0,
    JobStatus.PENDING.value: 1,
    JobStatus.PARTIAL.value: 1,
    JobStatus.REJECTED.value: 2,
    JobStatus.FAILED.value: 2,
    JobStatus.SUCCESS.value: 3,
    JobStatus.SUCCESS_WITH_FAILURE.value: 3,
}
