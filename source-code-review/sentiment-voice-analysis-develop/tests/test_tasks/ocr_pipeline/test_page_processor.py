"""Tests for PageProcessor — landing read, per-file document processing, chunk upload."""

from __future__ import annotations

from unittest.mock import Mock

from tasks.ocr_tax_invoice_pipeline.module.page_processor import PageProcessor
from tasks.ocr_tax_invoice_pipeline.schema.contracts import ChunkEntry


def _processor(landing_gcs=None, processing_gcs=None, doc_processor=None):
    landing_gcs = landing_gcs or Mock()
    processing_gcs = processing_gcs or Mock(bucket_name="proc-bucket")
    doc_processor = doc_processor or Mock()
    return (
        PageProcessor(
            landing_gcs=landing_gcs,
            processing_gcs=processing_gcs,
            processing_prefix="ocr_processing/JOB",
            doc_processor=doc_processor,
            job_id="JOB",
            pipeline_name="ocr_tax_invoice_pipeline",
        ),
        landing_gcs,
        processing_gcs,
        doc_processor,
    )


class TestRun:
    def test_run_uploads_accepted_chunks_and_returns_manifest(self):
        # Arrange
        landing_gcs = Mock()
        landing_gcs.download_file_from_gcs.return_value = b"file-bytes"
        processing_gcs = Mock(bucket_name="proc-bucket")
        doc_processor = Mock()
        doc_processor.process.return_value = (
            [{"page_no": 1, "child_path": "ocr_processing/JOB/a_p001.pdf"}],
            [
                {
                    "content": b"page-bytes",
                    "mime_type": "application/pdf",
                    "destination_path": "ocr_processing/JOB/a_p001.pdf",
                }
            ],
        )
        processor, *_ = _processor(landing_gcs, processing_gcs, doc_processor)
        uploaded = [{"name": "a.pdf", "sp_path": "/sp/a.pdf", "gcs_path": "gs://landing/a.pdf"}]

        # Act
        manifest_rows, chunk_entries = processor.run(uploaded)

        # Assert
        assert len(manifest_rows) == 1
        assert manifest_rows[0]["parent_path"] == "gs://landing/a.pdf"
        assert manifest_rows[0]["child_path"] == "gs://proc-bucket/ocr_processing/JOB/a_p001.pdf"
        processing_gcs.update_content_to_gcs.assert_called_once_with(
            b"page-bytes", "application/pdf", "ocr_processing/JOB/a_p001.pdf"
        )
        assert chunk_entries == [
            ChunkEntry(
                parent_landing_path="gs://landing/a.pdf",
                gcs_uri="gs://proc-bucket/ocr_processing/JOB/a_p001.pdf",
            )
        ]

    def test_run_processes_every_uploaded_file(self):
        # Arrange
        landing_gcs = Mock()
        landing_gcs.download_file_from_gcs.return_value = b"file-bytes"
        doc_processor = Mock()
        doc_processor.process.side_effect = [
            ([{"page_no": 1, "child_path": ""}], []),
            ([{"page_no": 1, "child_path": ""}], []),
        ]
        processor, *_ = _processor(landing_gcs=landing_gcs, doc_processor=doc_processor)
        uploaded = [
            {"name": "a.pdf", "sp_path": "/sp/a.pdf", "gcs_path": "gs://landing/a.pdf"},
            {"name": "b.pdf", "sp_path": "/sp/b.pdf", "gcs_path": "gs://landing/b.pdf"},
        ]

        # Act
        manifest_rows, chunk_entries = processor.run(uploaded)

        # Assert
        assert doc_processor.process.call_count == 2
        assert len(manifest_rows) == 2
        assert manifest_rows[0]["parent_path"] == "gs://landing/a.pdf"
        assert manifest_rows[1]["parent_path"] == "gs://landing/b.pdf"
        assert chunk_entries == []

    def test_run_with_empty_uploaded_list_returns_empty(self):
        # Arrange
        processor, *_ = _processor()

        # Act
        manifest_rows, chunk_entries = processor.run([])

        # Assert
        assert manifest_rows == []
        assert chunk_entries == []


class TestProcessFileErrorPath:
    def test_landing_read_failure_yields_no_rows_and_skips_doc_processor(self, caplog):
        # Arrange
        landing_gcs = Mock()
        landing_gcs.download_file_from_gcs.side_effect = Exception("gcs unavailable")
        doc_processor = Mock()
        processor, *_ = _processor(landing_gcs=landing_gcs, doc_processor=doc_processor)
        uploaded = [{"name": "broken.pdf", "sp_path": "/sp/broken.pdf", "gcs_path": "gs://landing/broken.pdf"}]

        # Act
        with caplog.at_level("WARNING"):
            manifest_rows, chunk_entries = processor.run(uploaded)

        # Assert
        assert manifest_rows == []
        assert chunk_entries == []
        doc_processor.process.assert_not_called()
        assert any("Failed to read landing copy" in rec.message for rec in caplog.records)

    def test_one_bad_file_does_not_abort_remaining_files(self):
        # Arrange
        landing_gcs = Mock()
        landing_gcs.download_file_from_gcs.side_effect = [Exception("boom"), b"good-bytes"]
        doc_processor = Mock()
        doc_processor.process.return_value = ([{"page_no": 1, "child_path": ""}], [])
        processor, *_ = _processor(landing_gcs=landing_gcs, doc_processor=doc_processor)
        uploaded = [
            {"name": "broken.pdf", "sp_path": "/sp/broken.pdf", "gcs_path": "gs://landing/broken.pdf"},
            {"name": "good.pdf", "sp_path": "/sp/good.pdf", "gcs_path": "gs://landing/good.pdf"},
        ]

        # Act
        manifest_rows, _ = processor.run(uploaded)

        # Assert
        assert len(manifest_rows) == 1
        assert manifest_rows[0]["parent_path"] == "gs://landing/good.pdf"


class TestEnrichChildPath:
    def test_run_rewrites_non_empty_child_path_and_leaves_empty_untouched(self):
        # Arrange
        landing_gcs = Mock()
        landing_gcs.download_file_from_gcs.return_value = b"file-bytes"
        processing_gcs = Mock(bucket_name="proc-bucket")
        doc_processor = Mock()
        doc_processor.process.return_value = (
            [
                {"page_no": 1, "child_path": "ocr_processing/JOB/a_p001.pdf"},
                {"page_no": 2, "child_path": ""},
            ],
            [],
        )
        processor, *_ = _processor(landing_gcs, processing_gcs, doc_processor)
        uploaded = [{"name": "a.pdf", "sp_path": "/sp/a.pdf", "gcs_path": "gs://landing/a.pdf"}]

        # Act
        manifest_rows, _ = processor.run(uploaded)

        # Assert
        assert manifest_rows[0]["child_path"] == "gs://proc-bucket/ocr_processing/JOB/a_p001.pdf"
        assert manifest_rows[1]["child_path"] == ""
