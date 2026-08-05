"""Tests for ResultFinalizer — the join that shapes predictions to OCROutputSchema.

Regression focus: ``DATADATE`` (carried from the pre-processing log) must survive the
schema-narrowing step at the end of ``run`` and land as a nullable ``Int64``. The reconcile
builder partitions/joins on this column, so dropping it breaks the downstream pipeline.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from tasks.ocr_tax_invoice_pipeline.helper.constant import OCROutputStatus, QualityStatus
from tasks.ocr_tax_invoice_pipeline.module.result_finalizer import ResultFinalizer

_JOB = "batchjob-1"
_LANDING = "gs://landing/doc.pdf"
_CHILD = "gs://processing/doc_p1.pdf"
_SP_PATH = "/Shared/Documents/doc.pdf"
_DATADATE = "20260610"


def _result_df() -> pd.DataFrame:
    """One predicted line item carrying every non-join OCROutputSchema field (lowercased)."""
    return pd.DataFrame(
        [
            {
                "batch_inference_job_name": _JOB,
                "source_file_uri": _CHILD,
                "doc_name": "DOC1",
                "doc_type": "tax_invoice",
                "tax_invoice_number": "INV-001",
                "tax_invoice_date": "2026-06-10",
                "customer_name_th": "Buyer Co",
                "customer_address_th": "Bangkok",
                "customer_name_eng": "Buyer Co",
                "customer_address_eng": "Bangkok",
                "customer_tax_id": "0105551234567",
                "customer_branch_code": "00000",
                "customer_branch_name": "HQ",
                "vendor_name_th": "Seller Co",
                "vendor_name_eng": "Seller Co",
                "vendor_address_th": "Nonthaburi",
                "vendor_address_eng": "Nonthaburi",
                "vendor_tax_id": "0107000000000",
                "vendor_branch_code": "00000",
                "vendor_branch_name": "HQ",
                "before_vat_amount": "100.00",
                "vat_amount": "7.00",
                "after_vat_amount": "107.00",
                "withholding_tax_amount": "0.00",
                "net_amount": "107.00",
                "item_no": 1,
                "invoice_number": "INV-001",
                "description": "item",
                "quantity": 1.0,
                "unit_price": 100.0,
                "invoice_amount_before_vat": "100.00",
                "invoice_vat_amount": "7.00",
                "invoice_amount_after_vat": "107.00",
                "copy": False,
                "payee_signature_flag": False,
                "payee_signature_name": None,
                "authorized_receiver_signature_flag": False,
                "authorized_receiver_signature_name": None,
                "authorized_signatory_signature_flag": False,
                "authorized_signatory_signature_name": None,
                "stamp": False,
                "start_time": datetime(2026, 6, 10, 1, 0, 0),
                "end_time": datetime(2026, 6, 10, 1, 0, 5),
                "status": OCROutputStatus.SUCCESS.value,
                "message": None,
                "usage_metadata": {"total_token_count": 10},
            }
        ]
    )


def _pre_processing_log() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "job_id": "J1",
                "sharepoint_input_path": _SP_PATH,
                "gcs_landing_path": _LANDING,
                "batch_inference_job_name": _JOB,
                "datadate": _DATADATE,
            }
        ]
    )


def _page_manifest_log() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "job_id": "J1",
                "parent_path": _LANDING,
                "child_path": _CHILD,
                "page_no": 1,
                "iqs_score": 0.9,
                "quality_status": QualityStatus.ACCEPTED.value,
                "message": None,
            }
        ]
    )


def test_run_carries_datadate_as_int64_through_schema_narrow():
    # Arrange
    finalizer = ResultFinalizer()

    # Act
    out = finalizer.run(_result_df(), _pre_processing_log(), _page_manifest_log())

    # Assert — the column survives the OCROutputSchema narrow and is coerced to nullable Int64.
    assert "DATADATE" in out.columns
    assert str(out["DATADATE"].dtype) == "Int64"
    assert out["DATADATE"].iloc[0] == 20260610
    # Sanity: the prediction joined to its source file/page (so DATADATE is a real value, not null).
    assert out["FILE_PATH"].iloc[0] == _SP_PATH
    assert out["PAGE_NO"].iloc[0] == 1


def test_run_rejected_page_is_failed_with_datadate():
    # Arrange — same job, but the manifest page is IQS-rejected (not ACCEPTED).
    manifest = _page_manifest_log()
    manifest.loc[0, "quality_status"] = QualityStatus.REJECTED.value
    manifest.loc[0, "message"] = "below IQS threshold"

    # Act
    out = ResultFinalizer().run(_result_df(), _pre_processing_log(), manifest)

    # Assert — a FAILED row for the rejected page also carries DATADATE.
    failed = out[out["STATUS"] == OCROutputStatus.FAILED.value]
    assert not failed.empty
    assert failed["DATADATE"].iloc[0] == 20260610
