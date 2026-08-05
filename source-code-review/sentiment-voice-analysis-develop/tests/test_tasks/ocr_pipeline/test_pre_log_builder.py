"""Tests for PreLogRowBuilder — pre-processing-log row construction.

Focus: the GCS-file traceability columns (``gcs_landing_path`` / ``gcs_payload_path``), the
landing-path join with the page manifest, the FAILED/REJECTED/PARTIAL/PENDING status table,
and the terminal REJECTED row for unsupported-extension files. The builder is pure — no task
instance, no I/O.
"""

from unittest.mock import Mock
from zoneinfo import ZoneInfo

from tasks.ocr_tax_invoice_pipeline.helper.constant import JobStatus, QualityStatus
from tasks.ocr_tax_invoice_pipeline.module.pre_log_builder import PreLogContext, PreLogRowBuilder
from tasks.ocr_tax_invoice_pipeline.schema.contracts import BatchSubmission

LANDING_URI = "gs://nprd-bucket/ocr_landing/202606/JOB/invoice.pdf"
SP_PATH = "/nprd_tax_invoice/input/invoice.pdf"
PAYLOAD_URI = "gs://nprd-bucket/ocr_processing/payloads/202606/JOB/pl_0.jsonl"
OUTPUT_URI = "gs://nprd-bucket/ocr_processing/output/202606/JOB/pl_0"
DATADATE = "20260605"

CTX = PreLogContext(
    job_id="JOB",
    pipeline_name="tax_invoice_extraction",
    domain="treasury",
    gcp_project_id="gcp-proj",
    gcs_project_id="gcs-proj",
    vertexai_project_id="vx-proj",
    batch_inference_location="us-central1",
    batch_inference_model_name="gemini",
    timezone=ZoneInfo("UTC"),
)


def _builder():
    sp = Mock()
    sp.get_web_url.return_value = "http://sp/invoice.pdf"
    return PreLogRowBuilder(CTX, sp)


def _item():
    return {"name": "invoice.pdf", "sp_path": SP_PATH, "gcs_path": LANDING_URI}


def _manifest(quality_statuses):
    """One manifest row per page (joined on landing path) plus an unrelated other-file page."""
    rows = [
        {"parent_path": LANDING_URI, "page_no": i + 1, "quality_status": q.value}
        for i, q in enumerate(quality_statuses)
    ]
    rows.append(
        {
            "parent_path": "gs://nprd-bucket/ocr_landing/202606/JOB/other.pdf",
            "page_no": 1,
            "quality_status": QualityStatus.ACCEPTED.value,
        }
    )
    return rows


def _submission(error=None):
    job = None
    if not error:
        job = Mock()
        job.name = "projects/p/locations/l/batchPredictionJobs/123"
        job.display_name = "ocr-batch-123"
    return BatchSubmission(
        payload_name="pl_0.jsonl",
        payload_uri=PAYLOAD_URI,
        output_uri=OUTPUT_URI,
        parent_paths=frozenset({LANDING_URI}),
        job=job,
        error=error,
    )


class TestBuildRowsForFile:
    def test_partial_row_carries_landing_and_payload_file_uris(self):
        rows = _builder().build(
            [_item()],
            [],
            [],
            _manifest([QualityStatus.ACCEPTED, QualityStatus.REJECTED]),
            [_submission()],
            DATADATE,
        )

        initial, submit = rows[0], rows[-1]
        assert initial["status"] == JobStatus.INITIAL.value
        assert initial["gcs_landing_path"] == LANDING_URI
        assert initial["gcs_payload_path"] == ""  # not yet submitted

        assert submit["status"] == JobStatus.PARTIAL.value
        assert submit["gcs_landing_path"] == LANDING_URI
        assert submit["gcs_payload_path"] == PAYLOAD_URI  # real JSONL file, not a folder
        assert submit["sharepoint_input_path"] == SP_PATH
        assert submit["batch_inference_job_name"].endswith("123")

    def test_all_pages_rejected_yields_rejected_row_with_no_payload(self):
        rows = _builder().build([_item()], [], [], _manifest([QualityStatus.REJECTED]), [_submission()], DATADATE)

        rejected = rows[-1]
        assert rejected["status"] == JobStatus.REJECTED.value
        assert rejected["gcs_landing_path"] == LANDING_URI
        assert rejected["gcs_payload_path"] == ""

    def test_all_pages_accepted_yields_pending(self):
        # The join must key on the landing path, not sp_path, or no pages would match.
        rows = _builder().build([_item()], [], [], _manifest([QualityStatus.ACCEPTED]), [_submission()], DATADATE)

        assert rows[-1]["status"] == JobStatus.PENDING.value

    def test_batch_submit_failure_yields_failed_with_payload(self):
        rows = _builder().build(
            [_item()], [], [], _manifest([QualityStatus.ACCEPTED]), [_submission(error="boom")], DATADATE
        )

        failed = rows[-1]
        assert failed["status"] == JobStatus.FAILED.value
        assert failed["gcs_payload_path"] == PAYLOAD_URI
        assert "boom" in failed["message"]


class TestBuildFailedUploads:
    def test_failed_upload_row_has_empty_landing_and_payload(self):
        failed = [{"name": "bad.pdf", "sp_path": "/in/bad.pdf", "error": "boom"}]

        rows = _builder().build([], failed, [], [], [], DATADATE)

        assert len(rows) == 1
        assert rows[0]["status"] == JobStatus.FAILED.value
        assert rows[0]["gcs_landing_path"] == ""
        assert rows[0]["gcs_payload_path"] == ""
        assert rows[0]["sharepoint_input_path"] == "/in/bad.pdf"


class TestBuildUnsupported:
    def test_unsupported_file_yields_terminal_rejected_row(self):
        unsupported = [{"name": "photo.webp", "sp_path": "/in/photo.webp"}]

        rows = _builder().build([], [], unsupported, [], [], DATADATE)

        assert len(rows) == 1
        row = rows[0]
        assert row["status"] == JobStatus.REJECTED.value
        assert row["message"] == "Unsupported file type: .webp"
        assert row["gcs_landing_path"] == ""
        assert row["gcs_payload_path"] == ""
        assert row["sharepoint_web_url"] == "http://sp/invoice.pdf"
        assert not any(r["status"] == JobStatus.INITIAL.value for r in rows)

    def test_unsupported_file_without_extension_uses_no_extension_message(self):
        unsupported = [{"name": "README", "sp_path": "/in/README"}]

        rows = _builder().build([], [], unsupported, [], [], DATADATE)

        assert rows[0]["message"] == "Unsupported file type: file has no extension"
