"""
Tests for BatchProcessingLogSchema and BatchProcessingPayload.
"""

from unittest.mock import patch

import pytest

from src.modules.audit_log.batch_processing_log import (
    BatchProcessingLogSchema,
    BatchProcessingPayload,
)


class TestBatchProcessingPayload:
    """Test BatchProcessingPayload TypedDict structure."""

    def test_create_payload(self):
        payload = BatchProcessingPayload(
            data_date="2026-01-26",
            gcp_project_id="test-project",
            gcp_project_name="Test Project",
            gcs_bucket_name="test-bucket",
            source_path="gs://test-bucket/path/file.wav",
            filename="file.wav",
            prediction_payload_path=None,
        )
        assert payload["data_date"] == "2026-01-26"
        assert payload["filename"] == "file.wav"
        assert payload["prediction_payload_path"] is None


class TestBatchProcessingLogSchema:
    """Test BatchProcessingLogSchema creation and methods."""

    def _make_payload(self, **overrides) -> BatchProcessingPayload:
        defaults = {
            "data_date": "2026-01-26",
            "gcp_project_id": "test-project",
            "gcp_project_name": "Test Project",
            "gcs_bucket_name": "test-bucket",
            "source_path": "gs://test-bucket/path/file.wav",
            "filename": "file.wav",
            "prediction_payload_path": None,
        }
        defaults.update(overrides)
        return BatchProcessingPayload(**defaults)

    def test_from_dict_creates_schema(self):
        payload = self._make_payload()
        schema = BatchProcessingLogSchema.from_dict(payload)
        assert schema.data_date == "20260126"
        assert schema.gcp_project_id == "test-project"
        assert schema.filename == "file.wav"
        assert schema.status == "PROCESSING"

    def test_from_dict_sets_log_id(self):
        payload = self._make_payload()
        schema = BatchProcessingLogSchema.from_dict(payload)
        assert schema.log_id is not None
        assert isinstance(schema.log_id, str)

    def test_from_dict_sets_log_type(self):
        payload = self._make_payload()
        schema = BatchProcessingLogSchema.from_dict(payload)
        assert schema.log_type == "Batch Processing Log"

    def test_stamp_payload_path(self):
        payload = self._make_payload()
        schema = BatchProcessingLogSchema.from_dict(payload)
        assert schema.prediction_payload_path is None

        schema.stamp_payload_path(prediction_payload_path="gs://bucket/payloads.jsonl")
        assert schema.prediction_payload_path == "gs://bucket/payloads.jsonl"

    def test_stamp_completion(self):
        payload = self._make_payload()
        schema = BatchProcessingLogSchema.from_dict(payload)
        schema.stamp_completion(action="UPLOAD")
        assert schema.status == "SUCCESS"
        assert schema.action == "UPLOAD"

    def test_stamp_error(self):
        payload = self._make_payload()
        schema = BatchProcessingLogSchema.from_dict(payload)
        schema.stamp_error(action="UPLOAD", error_message="Upload failed")
        assert schema.status == "FAILED"
        assert schema.error_message == "Upload failed"

    def test_to_dict(self):
        payload = self._make_payload()
        schema = BatchProcessingLogSchema.from_dict(payload)
        result = schema.to_dict()
        assert isinstance(result, dict)
        assert result["data_date"] == "20260126"
        assert result["filename"] == "file.wav"
        assert "log_id" in result
        assert "created_dt" in result

    def test_to_dict_with_payload_path(self):
        payload = self._make_payload()
        schema = BatchProcessingLogSchema.from_dict(payload)
        schema.stamp_payload_path(prediction_payload_path="gs://b/p.jsonl")
        result = schema.to_dict()
        assert result["prediction_payload_path"] == "gs://b/p.jsonl"

    def test_multiple_schemas_have_unique_log_ids(self):
        payload = self._make_payload()
        schema1 = BatchProcessingLogSchema.from_dict(payload)
        schema2 = BatchProcessingLogSchema.from_dict(payload)
        assert schema1.log_id != schema2.log_id

    def test_from_dict_wraps_unexpected_exception(self):
        payload = self._make_payload()

        with (
            patch(
                "src.modules.audit_log.batch_processing_log.LogTimeStamper.create_log_metadata",
                side_effect=RuntimeError("metadata failed"),
            ),
            patch("src.modules.audit_log.batch_processing_log.logger") as mock_logger,
        ):
            with pytest.raises(Exception, match="Error creating BatchProcessingLogSchema: metadata failed"):
                BatchProcessingLogSchema.from_dict(payload)

        mock_logger.error.assert_called_once()
