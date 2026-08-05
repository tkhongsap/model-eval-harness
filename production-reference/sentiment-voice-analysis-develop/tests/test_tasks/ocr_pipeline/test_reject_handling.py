"""Tests for IQS reject reason messages, Suspicious status detection, and failure rollup."""

from __future__ import annotations

import pandas as pd

from tasks.ocr_tax_invoice_pipeline.helper.constant import OCROutputStatus
from tasks.ocr_tax_invoice_pipeline.helper.messages import iqs_reject_reason, unsupported_file_reason
from tasks.ocr_tax_invoice_pipeline.module.result_retriever import BatchResultRetriever
from tasks.ocr_tax_invoice_pipeline.module.status_finalizer import aggregate_file_messages, build_terminal_log_rows

_IQS_CFG = {"threshold": 0.6, "sub_thresholds": {"vq": 0.3, "sq": None, "ct": None}}


def test_unsupported_file_reason_formats_extension():
    assert unsupported_file_reason(".webp") == "Unsupported file type: .webp"
    assert unsupported_file_reason("") == "Unsupported file type: file has no extension"


def test_iqs_reject_reason_names_blur_when_visual_quality_below_floor():
    msg = iqs_reject_reason({"vq": 0.2, "sq": 0.9, "ct": 0.9, "iqs": 0.5}, _IQS_CFG)
    assert "blurry" in msg and "image quality" in msg.lower()


def test_iqs_reject_reason_names_skew_for_low_structural_quality():
    # Overall below threshold with structural quality the weakest dimension.
    msg = iqs_reject_reason({"vq": 0.9, "sq": 0.1, "ct": 0.9, "iqs": 0.5}, {"threshold": 0.6, "sub_thresholds": {}})
    assert "skewed" in msg


def test_derive_status_flags_suspicious_doc_type():
    assert BatchResultRetriever._derive_status("Suspicious", []) == OCROutputStatus.SUSPICIOUS.value


def test_derive_status_other_is_unsupported():
    assert BatchResultRetriever._derive_status("Other", []) == OCROutputStatus.UNSUPPORTED.value


def test_aggregate_file_messages_joins_distinct_nonsuccess_reasons():
    blur = "Rejected by image quality check: image is blurry or low-resolution"
    final_df = pd.DataFrame(
        {
            "FILE_PATH": ["/a", "/a", "/b"],
            "STATUS": ["SUCCESS", "FAILED", "SUSPICIOUS"],
            "MESSAGE": [None, blur, "Injection (page 1)"],
        }
    )
    assert aggregate_file_messages(final_df) == {"/a": blur, "/b": "Injection (page 1)"}


def test_build_terminal_log_rows_stamps_aggregated_message():
    pre_log = pd.DataFrame(
        [
            {
                "sharepoint_input_path": "/a",
                "status": "PENDING",
                "update_dt": "2026-06-10T10:00:00+00:00",
                "batch_inference_job_name": "job1",
            }
        ]
    )
    rows = build_terminal_log_rows(
        {"/a": "FAILED"}, pre_log, "2026-06-10T11:00:00+00:00", file_messages={"/a": "bad scan"}
    )
    assert rows[0]["status"] == "FAILED"
    assert rows[0]["message"] == "bad scan"
