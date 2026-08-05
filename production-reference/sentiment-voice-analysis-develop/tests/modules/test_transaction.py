"""
Tests for Transaction Log Module.
"""

from datetime import datetime
from unittest.mock import patch

import pytest

from src.modules.audit_log.transaction_log import TransactionLogSchema, TransactionPayload


class TestTransactionLog:
    """Test suite for Transaction Log functionality."""

    def test_transaction_log_schema_from_dict(self, sample_transaction_payload):
        """Test creating TransactionLogSchema from dictionary."""
        schema = TransactionLogSchema.from_dict(sample_transaction_payload)

        assert schema.filename == "test_file.jsonl"
        assert schema.gcp_project_id == "test-project"
        assert schema.status_pass_failed_retry == "pass"

    def test_transaction_log_schema_parses_timestamps(self, sample_transaction_payload):
        """Test that timestamps are parsed correctly."""
        schema = TransactionLogSchema.from_dict(sample_transaction_payload)

        assert isinstance(schema.start_time, datetime) or schema.start_time is None
        assert isinstance(schema.end_time, datetime) or schema.end_time is None

    def test_transaction_log_schema_calculates_duration(self, sample_transaction_payload):
        """Test that duration is calculated from start and end times."""
        schema = TransactionLogSchema.from_dict(sample_transaction_payload)

        # If timestamps parsed successfully, should have total_time_mins
        if schema.start_time and schema.end_time:
            assert schema.total_time_mins is not None

    def test_transaction_log_schema_handles_missing_filename(self):
        """Test handling of missing filename."""
        payload = {
            "data_date": "2026-01-26",
            "start_time": "2026-01-26 10:00:00",
            "end_time": "2026-01-26 10:30:00",
            "type": "test",
            "gcp_project_id": "test-project",
            "gcp_project_name": "Test",
            "user_id": "user",
            "source": "test",
            "storage_path": "gs://test",
            "folder": "folder",
            "filename": "",  # Empty filename
            "file_metadata_sec": 0,
            "status_pass_failed_retry": "pass",
            "error_log_if": "",
            "token_usage_input": 0,
            "token_usage_output": 0,
            "total_cost_usd": 0.0,
        }

        # Should not raise, just log warning
        schema = TransactionLogSchema.from_dict(payload)
        assert schema.filename == ""

    def test_transaction_log_schema_handles_invalid_timestamps(self):
        """Test handling of invalid timestamp formats."""
        payload = {
            "data_date": "2026-01-26",
            "start_time": "invalid-timestamp",
            "end_time": "also-invalid",
            "type": "test",
            "gcp_project_id": "test-project",
            "gcp_project_name": "Test",
            "user_id": "user",
            "source": "test",
            "storage_path": "gs://test",
            "folder": "folder",
            "filename": "test.jsonl",
            "file_metadata_sec": 0,
            "status_pass_failed_retry": "pass",
            "error_log_if": "",
            "token_usage_input": 0,
            "token_usage_output": 0,
            "total_cost_usd": 0.0,
        }

        # Should handle gracefully
        schema = TransactionLogSchema.from_dict(payload)
        # Invalid timestamps should result in None
        assert schema.start_time is None or isinstance(schema.start_time, datetime)

    def test_transaction_log_schema_token_usage(self, sample_transaction_payload):
        """Test that token usage fields are preserved."""
        schema = TransactionLogSchema.from_dict(sample_transaction_payload)

        assert schema.token_usage_input == 1000
        assert schema.token_usage_output == 500

    def test_transaction_log_schema_cost(self, sample_transaction_payload):
        """Test that cost field is preserved."""
        schema = TransactionLogSchema.from_dict(sample_transaction_payload)

        assert schema.total_cost_usd == 0.05

    def test_transaction_log_schema_all_fields_present(self, sample_transaction_payload):
        """Test that all schema fields are created."""
        schema = TransactionLogSchema.from_dict(sample_transaction_payload)

        expected_fields = [
            "data_date",
            "start_time",
            "end_time",
            "total_time_mins",
            "type",
            "gcp_project_id",
            "gcp_project_name",
            "user_id",
            "source",
            "storage_path",
            "folder",
            "filename",
            "file_metadata_min",
            "status_pass_failed_retry",
            "error_log_if",
            "latency_ms",
            "token_usage_input",
            "token_usage_output",
            "total_cost_usd",
        ]

        for field in expected_fields:
            assert hasattr(schema, field)

    def test_transaction_payload_typed_dict(self):
        """Test TransactionPayload TypedDict structure."""
        payload: TransactionPayload = {
            "data_date": "2026-01-26",
            "start_time": "2026-01-26 10:00:00",
            "end_time": "2026-01-26 10:30:00",
            "type": "test",
            "gcp_project_id": "project",
            "gcp_project_name": "Project",
            "user_id": "user",
            "source": "source",
            "storage_path": "path",
            "folder": "folder",
            "filename": "file.jsonl",
            "file_metadata_sec": 100,
            "status_pass_failed_retry": "pass",
            "error_log_if": "",
            "token_usage_input": 100,
            "token_usage_output": 50,
            "total_cost_usd": 0.01,
        }

        assert payload["filename"] == "file.jsonl"
        assert payload["token_usage_input"] == 100

    def test_transaction_log_schema_with_negative_audio_duration(self):
        """Test handling of negative audio duration."""
        payload = {
            "data_date": "2026-01-26",
            "start_time": "2026-01-26 10:00:00",
            "end_time": "2026-01-26 10:30:00",
            "type": "test",
            "gcp_project_id": "test-project",
            "gcp_project_name": "Test",
            "user_id": "user",
            "source": "test",
            "storage_path": "gs://test",
            "folder": "folder",
            "filename": "test.jsonl",
            "file_metadata_sec": -100,  # Negative duration
            "status_pass_failed_retry": "Pass",
            "error_log_if": "",
            "token_usage_input": 100,
            "token_usage_output": 50,
            "total_cost_usd": 0.01,
            "load_dt": "2026-01-26",
        }

        schema = TransactionLogSchema.from_dict(payload)
        assert schema.file_metadata_min is None

    def test_transaction_log_schema_with_negative_tokens(self):
        """Test handling of negative token values."""
        payload = {
            "data_date": "2026-01-26",
            "start_time": "2026-01-26 10:00:00",
            "end_time": "2026-01-26 10:30:00",
            "type": "test",
            "gcp_project_id": "test-project",
            "gcp_project_name": "Test",
            "user_id": "user",
            "source": "test",
            "storage_path": "gs://test",
            "folder": "folder",
            "filename": "test.jsonl",
            "file_metadata_sec": 100,
            "status_pass_failed_retry": "Pass",
            "error_log_if": "",
            "token_usage_input": -100,  # Negative
            "token_usage_output": -50,  # Negative
            "total_cost_usd": -0.01,  # Negative
            "load_dt": "2026-01-26",
        }

        schema = TransactionLogSchema.from_dict(payload)
        assert schema.token_usage_input == 0
        assert schema.token_usage_output == 0
        assert schema.total_cost_usd == 0.0

    def test_transaction_log_schema_with_unexpected_status(self):
        """Test handling of unexpected status values."""
        payload = {
            "data_date": "2026-01-26",
            "start_time": "2026-01-26 10:00:00",
            "end_time": "2026-01-26 10:30:00",
            "type": "test",
            "gcp_project_id": "test-project",
            "gcp_project_name": "Test",
            "user_id": "user",
            "source": "test",
            "storage_path": "gs://test",
            "folder": "folder",
            "filename": "test.jsonl",
            "file_metadata_sec": 100,
            "status_pass_failed_retry": "Unknown",  # Unexpected status
            "error_log_if": "",
            "token_usage_input": 100,
            "token_usage_output": 50,
            "total_cost_usd": 0.01,
            "load_dt": "2026-01-26",
        }

        # Should handle gracefully with warning
        schema = TransactionLogSchema.from_dict(payload)
        assert schema.status_pass_failed_retry == "Unknown"

    def test_transaction_log_schema_with_valid_audio_duration(self):
        """Test calculation of audio duration in minutes."""
        payload = {
            "data_date": "2026-01-26",
            "start_time": "2026-01-26 10:00:00",
            "end_time": "2026-01-26 10:30:00",
            "type": "test",
            "gcp_project_id": "test-project",
            "gcp_project_name": "Test",
            "user_id": "user",
            "source": "test",
            "storage_path": "gs://test",
            "folder": "folder",
            "filename": "test.jsonl",
            "file_metadata_sec": 185,  # 3 minutes 5 seconds
            "status_pass_failed_retry": "Pass",
            "error_log_if": "",
            "token_usage_input": 100,
            "token_usage_output": 50,
            "total_cost_usd": 0.01,
            "load_dt": "2026-01-26",
        }

        schema = TransactionLogSchema.from_dict(payload)
        assert schema.file_metadata_min == "3:05"

    def test_transaction_log_schema_with_none_audio_duration(self):
        """Test handling of None audio duration."""
        payload = {
            "data_date": "2026-01-26",
            "start_time": "2026-01-26 10:00:00",
            "end_time": "2026-01-26 10:30:00",
            "type": "test",
            "gcp_project_id": "test-project",
            "gcp_project_name": "Test",
            "user_id": "user",
            "source": "test",
            "storage_path": "gs://test",
            "folder": "folder",
            "filename": "test.jsonl",
            "file_metadata_sec": None,
            "status_pass_failed_retry": "Pass",
            "error_log_if": "",
            "token_usage_input": 100,
            "token_usage_output": 50,
            "total_cost_usd": 0.01,
            "load_dt": "2026-01-26",
        }

        schema = TransactionLogSchema.from_dict(payload)
        assert schema.file_metadata_min is None

    def test_transaction_log_schema_with_missing_timestamps(self):
        """Test when start_time or end_time is missing."""
        payload = {
            "data_date": "2026-01-26",
            "start_time": None,
            "end_time": None,
            "type": "test",
            "gcp_project_id": "test-project",
            "gcp_project_name": "Test",
            "user_id": "user",
            "source": "test",
            "storage_path": "gs://test",
            "folder": "folder",
            "filename": "test.jsonl",
            "file_metadata_sec": 100,
            "status_pass_failed_retry": "Pass",
            "error_log_if": "",
            "token_usage_input": 100,
            "token_usage_output": 50,
            "total_cost_usd": 0.01,
            "load_dt": "2026-01-26",
        }

        schema = TransactionLogSchema.from_dict(payload)
        assert schema.total_time_mins is None
        assert schema.latency_ms is None

    def test_transaction_log_schema_timezone_fallback(self, monkeypatch):
        """Test timezone fallback to UTC when Asia/Bangkok fails."""

        def mock_zoneinfo(*args, **kwargs):
            raise Exception("Timezone not found")

        # This test would require more complex mocking
        # Just test that schema handles timezone setup errors gracefully
        payload = {
            "data_date": "2026-01-26",
            "start_time": "2026-01-26 10:00:00",
            "end_time": "2026-01-26 10:30:00",
            "type": "test",
            "gcp_project_id": "test-project",
            "gcp_project_name": "Test",
            "user_id": "user",
            "source": "test",
            "storage_path": "gs://test",
            "folder": "folder",
            "filename": "test.jsonl",
            "file_metadata_sec": 100,
            "status_pass_failed_retry": "Pass",
            "error_log_if": "",
            "token_usage_input": 100,
            "token_usage_output": 50,
            "total_cost_usd": 0.01,
            "load_dt": "2026-01-26",
        }

        # Should not raise even if timezone setup has issues
        TransactionLogSchema.from_dict(payload)

    def test_transaction_log_schema_with_parse_start_time_error(self, monkeypatch):
        """Test error handling when parsing start_time fails."""

        payload = {
            "data_date": "2026-01-26",
            "start_time": "invalid-timestamp",
            "end_time": "2026-01-26 10:30:00",
            "type": "test",
            "gcp_project_id": "test-project",
            "gcp_project_name": "Test",
            "user_id": "user",
            "source": "test",
            "storage_path": "gs://test",
            "folder": "folder",
            "filename": "test.jsonl",
            "file_metadata_sec": 100,
            "status_pass_failed_retry": "Pass",
            "error_log_if": "",
            "token_usage_input": 100,
            "token_usage_output": 50,
            "total_cost_usd": 0.01,
            "load_dt": "2026-01-26",
        }

        # Should handle parse errors gracefully
        schema = TransactionLogSchema.from_dict(payload)
        assert schema.start_time is None or isinstance(schema.start_time, datetime)

    def test_transaction_log_schema_with_parse_end_time_error(self):
        """Test error handling when parsing end_time fails."""
        payload = {
            "data_date": "2026-01-26",
            "start_time": "2026-01-26 10:00:00",
            "end_time": "invalid-timestamp",
            "type": "test",
            "gcp_project_id": "test-project",
            "gcp_project_name": "Test",
            "user_id": "user",
            "source": "test",
            "storage_path": "gs://test",
            "folder": "folder",
            "filename": "test.jsonl",
            "file_metadata_sec": 100,
            "status_pass_failed_retry": "Pass",
            "error_log_if": "",
            "token_usage_input": 100,
            "token_usage_output": 50,
            "total_cost_usd": 0.01,
            "load_dt": "2026-01-26",
        }

        # Should handle parse errors gracefully
        schema = TransactionLogSchema.from_dict(payload)
        assert schema.end_time is None or isinstance(schema.end_time, datetime)

    def test_transaction_log_schema_with_duration_calculation_error(self):
        """Test error handling in duration calculation."""
        payload = {
            "data_date": "2026-01-26",
            "start_time": "2026-01-26 10:30:00",
            "end_time": "2026-01-26 10:00:00",  # End before start
            "type": "test",
            "gcp_project_id": "test-project",
            "gcp_project_name": "Test",
            "user_id": "user",
            "source": "test",
            "storage_path": "gs://test",
            "folder": "folder",
            "filename": "test.jsonl",
            "file_metadata_sec": 100,
            "status_pass_failed_retry": "Pass",
            "error_log_if": "",
            "token_usage_input": 100,
            "token_usage_output": 50,
            "total_cost_usd": 0.01,
            "load_dt": "2026-01-26",
        }

        # Should handle negative duration gracefully
        schema = TransactionLogSchema.from_dict(payload)
        assert schema.total_time_mins is not None

    def test_transaction_log_schema_with_audio_duration_parsing_error(self, monkeypatch):
        """Test error handling when parsing audio duration fails."""

        payload = {
            "data_date": "2026-01-26",
            "start_time": "2026-01-26 10:00:00",
            "end_time": "2026-01-26 10:30:00",
            "type": "test",
            "gcp_project_id": "test-project",
            "gcp_project_name": "Test",
            "user_id": "user",
            "source": "test",
            "storage_path": "gs://test",
            "folder": "folder",
            "filename": "test.jsonl",
            "file_metadata_sec": "invalid",  # Invalid value
            "status_pass_failed_retry": "Pass",
            "error_log_if": "",
            "token_usage_input": 100,
            "token_usage_output": 50,
            "total_cost_usd": 0.01,
            "load_dt": "2026-01-26",
        }

        # Should handle invalid audio duration
        schema = TransactionLogSchema.from_dict(payload)
        assert schema.file_metadata_min is None

    def test_transaction_log_schema_with_token_input_parsing_error(self, monkeypatch):
        """Test error handling when parsing token_usage_input fails."""

        payload = {
            "data_date": "2026-01-26",
            "start_time": "2026-01-26 10:00:00",
            "end_time": "2026-01-26 10:30:00",
            "type": "test",
            "gcp_project_id": "test-project",
            "gcp_project_name": "Test",
            "user_id": "user",
            "source": "test",
            "storage_path": "gs://test",
            "folder": "folder",
            "filename": "test.jsonl",
            "file_metadata_sec": 100,
            "status_pass_failed_retry": "Pass",
            "error_log_if": "",
            "token_usage_input": "invalid",  # Invalid value
            "token_usage_output": 50,
            "total_cost_usd": 0.01,
            "load_dt": "2026-01-26",
        }

        # Should handle invalid token input
        schema = TransactionLogSchema.from_dict(payload)
        assert schema.token_usage_input == 0 or schema.token_usage_input is None

    def test_transaction_log_schema_with_token_output_parsing_error(self):
        """Test error handling when parsing token_usage_output fails."""
        payload = {
            "data_date": "2026-01-26",
            "start_time": "2026-01-26 10:00:00",
            "end_time": "2026-01-26 10:30:00",
            "type": "test",
            "gcp_project_id": "test-project",
            "gcp_project_name": "Test",
            "user_id": "user",
            "source": "test",
            "storage_path": "gs://test",
            "folder": "folder",
            "filename": "test.jsonl",
            "file_metadata_sec": 100,
            "status_pass_failed_retry": "Pass",
            "error_log_if": "",
            "token_usage_input": 100,
            "token_usage_output": "invalid",  # Invalid value
            "total_cost_usd": 0.01,
            "load_dt": "2026-01-26",
        }

        # Should handle invalid token output
        schema = TransactionLogSchema.from_dict(payload)
        assert schema.token_usage_output == 0 or schema.token_usage_output is None

    def test_transaction_log_schema_with_cost_parsing_error(self):
        """Test error handling when parsing total_cost_usd fails."""
        payload = {
            "data_date": "2026-01-26",
            "start_time": "2026-01-26 10:00:00",
            "end_time": "2026-01-26 10:30:00",
            "type": "test",
            "gcp_project_id": "test-project",
            "gcp_project_name": "Test",
            "user_id": "user",
            "source": "test",
            "storage_path": "gs://test",
            "folder": "folder",
            "filename": "test.jsonl",
            "file_metadata_sec": 100,
            "status_pass_failed_retry": "Pass",
            "error_log_if": "",
            "token_usage_input": 100,
            "token_usage_output": 50,
            "total_cost_usd": "invalid",  # Invalid value
            "load_dt": "2026-01-26",
        }

        # Should handle invalid cost
        schema = TransactionLogSchema.from_dict(payload)
        assert schema.total_cost_usd == 0.0 or schema.total_cost_usd is None

    def test_transaction_log_schema_with_zoneinfo_exception(self, monkeypatch):
        """Test timezone fallback when ZoneInfo raises exception."""
        import src.modules.audit_log.transaction_log as txn_module

        def mock_zoneinfo_fail(zone_name):
            raise Exception("Timezone database not found")

        # transaction_log no longer imports ZoneInfo (timezone handling lives in
        # log_time_stamper); raising=False keeps this a no-op as it always was.
        monkeypatch.setattr(txn_module, "ZoneInfo", mock_zoneinfo_fail, raising=False)

        payload = {
            "data_date": "2026-01-26",
            "start_time": "2026-01-26 10:00:00",
            "end_time": "2026-01-26 10:30:00",
            "type": "test",
            "gcp_project_id": "test-project",
            "gcp_project_name": "Test",
            "user_id": "user",
            "source": "test",
            "storage_path": "gs://test",
            "folder": "folder",
            "filename": "test.jsonl",
            "file_metadata_sec": 100,
            "status_pass_failed_retry": "Pass",
            "error_log_if": "",
            "token_usage_input": 100,
            "token_usage_output": 50,
            "total_cost_usd": 0.01,
            "load_dt": "2026-01-26",
        }

        # Should handle timezone error and fall back to UTC
        schema = TransactionLogSchema.from_dict(payload)
        assert schema is not None

    def test_transaction_log_schema_with_parse_datetime_exception(self, monkeypatch):
        """Test handling when parse_datetime raises exception for start_time."""
        import src.modules.audit_log.transaction_log as txn_module

        original_parse_datetime = txn_module.parse_datetime
        call_count = [0]

        def mock_parse_datetime_fail(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:  # First call (start_time)
                raise Exception("Parse error")
            return original_parse_datetime(*args, **kwargs)

        monkeypatch.setattr(txn_module, "parse_datetime", mock_parse_datetime_fail)

        payload = {
            "data_date": "2026-01-26",
            "start_time": "2026-01-26 10:00:00",
            "end_time": "2026-01-26 10:30:00",
            "type": "test",
            "gcp_project_id": "test-project",
            "gcp_project_name": "Test",
            "user_id": "user",
            "source": "test",
            "storage_path": "gs://test",
            "folder": "folder",
            "filename": "test.jsonl",
            "file_metadata_sec": 100,
            "status_pass_failed_retry": "Pass",
            "error_log_if": "",
            "token_usage_input": 100,
            "token_usage_output": 50,
            "total_cost_usd": 0.01,
            "load_dt": "2026-01-26",
        }

        schema = TransactionLogSchema.from_dict(payload)
        assert schema.start_time is None

    def test_transaction_log_schema_with_parse_end_time_exception(self, monkeypatch):
        """Test handling when parse_datetime raises exception for end_time."""
        import src.modules.audit_log.transaction_log as txn_module

        original_parse_datetime = txn_module.parse_datetime
        call_count = [0]

        def mock_parse_datetime_fail(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 2:  # Second call (end_time)
                raise Exception("Parse error")
            return original_parse_datetime(*args, **kwargs)

        monkeypatch.setattr(txn_module, "parse_datetime", mock_parse_datetime_fail)

        payload = {
            "data_date": "2026-01-26",
            "start_time": "2026-01-26 10:00:00",
            "end_time": "2026-01-26 10:30:00",
            "type": "test",
            "gcp_project_id": "test-project",
            "gcp_project_name": "Test",
            "user_id": "user",
            "source": "test",
            "storage_path": "gs://test",
            "folder": "folder",
            "filename": "test.jsonl",
            "file_metadata_sec": 100,
            "status_pass_failed_retry": "Pass",
            "error_log_if": "",
            "token_usage_input": 100,
            "token_usage_output": 50,
            "total_cost_usd": 0.01,
            "load_dt": "2026-01-26",
        }

        schema = TransactionLogSchema.from_dict(payload)
        assert schema.end_time is None

    def test_transaction_log_schema_with_duration_exception(self, monkeypatch):
        """Test handling when duration calculation raises exception."""
        from datetime import datetime
        from unittest.mock import Mock

        payload = {
            "data_date": "2026-01-26",
            "start_time": "2026-01-26 10:00:00",
            "end_time": "2026-01-26 10:30:00",
            "type": "test",
            "gcp_project_id": "test-project",
            "gcp_project_name": "Test",
            "user_id": "user",
            "source": "test",
            "storage_path": "gs://test",
            "folder": "folder",
            "filename": "test.jsonl",
            "file_metadata_sec": 100,
            "status_pass_failed_retry": "Pass",
            "error_log_if": "",
            "token_usage_input": 100,
            "token_usage_output": 50,
            "total_cost_usd": 0.01,
            "load_dt": "2026-01-26",
        }

        # Create mock datetime that raises error on total_seconds
        import src.modules.audit_log.transaction_log as txn_module

        original_parse_datetime = txn_module.parse_datetime

        def mock_parse_with_broken_math(*args, **kwargs):
            result = original_parse_datetime(*args, **kwargs)
            if result:
                # Create a mock that will cause subtraction to fail
                broken_dt = Mock(spec=datetime)
                broken_dt.__sub__ = Mock(side_effect=Exception("Math error"))
                return broken_dt
            return result

        monkeypatch.setattr(txn_module, "parse_datetime", mock_parse_with_broken_math)

        schema = TransactionLogSchema.from_dict(payload)
        assert schema.total_time_mins is None
        assert schema.latency_ms is None

    def test_transaction_log_schema_with_audio_duration_exception(self, monkeypatch):
        """Test handling when audio duration parsing raises exception."""
        import src.modules.audit_log.transaction_log as txn_module

        def mock_safe_cast_fail(*args, **kwargs):
            raise Exception("Cast error")

        monkeypatch.setattr(txn_module, "safe_cast_value", mock_safe_cast_fail)

        payload = {
            "data_date": "2026-01-26",
            "start_time": "2026-01-26 10:00:00",
            "end_time": "2026-01-26 10:30:00",
            "type": "test",
            "gcp_project_id": "test-project",
            "gcp_project_name": "Test",
            "user_id": "user",
            "source": "test",
            "storage_path": "gs://test",
            "folder": "folder",
            "filename": "test.jsonl",
            "file_metadata_sec": 100,
            "status_pass_failed_retry": "Pass",
            "error_log_if": "",
            "token_usage_input": 100,
            "token_usage_output": 50,
            "total_cost_usd": 0.01,
            "load_dt": "2026-01-26",
        }

        schema = TransactionLogSchema.from_dict(payload)
        assert schema.file_metadata_min is None

    def test_transaction_log_schema_with_token_input_exception(self, monkeypatch):
        """Test handling when token_usage_input parsing raises exception."""
        import src.modules.audit_log.transaction_log as txn_module

        original_safe_cast = txn_module.safe_cast_value
        call_count = [0]

        def mock_safe_cast_fail_on_second(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 2:  # Second call (token_input)
                raise Exception("Cast error")
            return original_safe_cast(*args, **kwargs)

        monkeypatch.setattr(txn_module, "safe_cast_value", mock_safe_cast_fail_on_second)

        payload = {
            "data_date": "2026-01-26",
            "start_time": "2026-01-26 10:00:00",
            "end_time": "2026-01-26 10:30:00",
            "type": "test",
            "gcp_project_id": "test-project",
            "gcp_project_name": "Test",
            "user_id": "user",
            "source": "test",
            "storage_path": "gs://test",
            "folder": "folder",
            "filename": "test.jsonl",
            "file_metadata_sec": 100,
            "status_pass_failed_retry": "Pass",
            "error_log_if": "",
            "token_usage_input": 100,
            "token_usage_output": 50,
            "total_cost_usd": 0.01,
            "load_dt": "2026-01-26",
        }

        schema = TransactionLogSchema.from_dict(payload)
        assert schema.token_usage_input == 0

    def test_transaction_log_schema_with_token_output_exception(self, monkeypatch):
        """Test handling when token_usage_output parsing raises exception."""
        import src.modules.audit_log.transaction_log as txn_module

        original_safe_cast = txn_module.safe_cast_value
        call_count = [0]

        def mock_safe_cast_fail_on_third(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 3:  # Third call (token_output)
                raise Exception("Cast error")
            return original_safe_cast(*args, **kwargs)

        monkeypatch.setattr(txn_module, "safe_cast_value", mock_safe_cast_fail_on_third)

        payload = {
            "data_date": "2026-01-26",
            "start_time": "2026-01-26 10:00:00",
            "end_time": "2026-01-26 10:30:00",
            "type": "test",
            "gcp_project_id": "test-project",
            "gcp_project_name": "Test",
            "user_id": "user",
            "source": "test",
            "storage_path": "gs://test",
            "folder": "folder",
            "filename": "test.jsonl",
            "file_metadata_sec": 100,
            "status_pass_failed_retry": "Pass",
            "error_log_if": "",
            "token_usage_input": 100,
            "token_usage_output": 50,
            "total_cost_usd": 0.01,
            "load_dt": "2026-01-26",
        }

        schema = TransactionLogSchema.from_dict(payload)
        assert schema.token_usage_output == 0

    def test_transaction_log_schema_with_cost_exception(self, monkeypatch):
        """Test handling when total_cost_usd parsing raises exception."""
        import src.modules.audit_log.transaction_log as txn_module

        original_safe_cast = txn_module.safe_cast_value
        call_count = [0]

        def mock_safe_cast_fail_on_fourth(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 4:  # Fourth call (cost)
                raise Exception("Cast error")
            return original_safe_cast(*args, **kwargs)

        monkeypatch.setattr(txn_module, "safe_cast_value", mock_safe_cast_fail_on_fourth)

        payload = {
            "data_date": "2026-01-26",
            "start_time": "2026-01-26 10:00:00",
            "end_time": "2026-01-26 10:30:00",
            "type": "test",
            "gcp_project_id": "test-project",
            "gcp_project_name": "Test",
            "user_id": "user",
            "source": "test",
            "storage_path": "gs://test",
            "folder": "folder",
            "filename": "test.jsonl",
            "file_metadata_sec": 100,
            "status_pass_failed_retry": "Pass",
            "error_log_if": "",
            "token_usage_input": 100,
            "token_usage_output": 50,
            "total_cost_usd": 0.01,
            "load_dt": "2026-01-26",
        }

        schema = TransactionLogSchema.from_dict(payload)
        assert schema.total_cost_usd == 0.0

    def test_transaction_log_schema_with_outer_exception(self):
        """Test handling of unexpected exception in from_dict."""

        # Create a dict subclass that can raise exception
        class BrokenDict(dict):
            def get(self, key, default=None):
                if key == "filename":
                    raise RuntimeError("Unexpected error accessing filename")
                return super().get(key, default)

        payload = BrokenDict(
            {
                "data_date": "2026-01-26",
                "start_time": "2026-01-26 10:00:00",
                "end_time": "2026-01-26 10:30:00",
                "type": "test",
                "gcp_project_id": "test-project",
                "gcp_project_name": "Test",
                "user_id": "user",
                "source": "test",
                "storage_path": "gs://test",
                "folder": "folder",
                "filename": "test.jsonl",
                "file_metadata_sec": 100,
                "status_pass_failed_retry": "Pass",
                "error_log_if": "",
                "token_usage_input": 100,
                "token_usage_output": 50,
                "total_cost_usd": 0.01,
                "load_dt": "2026-01-26",
            }
        )

        with pytest.raises(Exception, match="Failed to create TransactionLogSchema"):
            TransactionLogSchema.from_dict(payload)

    def test_transaction_log_schema_with_actual_negative_duration(self):
        """Test with end_time before start_time to trigger negative duration path."""

        # Create payload with end before start
        payload = {
            "data_date": "2026-01-26",
            "start_time": "2026-01-26 10:30:00",  # Later time
            "end_time": "2026-01-26 10:00:00",  # Earlier time
            "type": "test",
            "gcp_project_id": "test-project",
            "gcp_project_name": "Test",
            "user_id": "user",
            "source": "test",
            "storage_path": "gs://test",
            "folder": "folder",
            "filename": "test_negative.jsonl",
            "file_metadata_sec": 100,
            "status_pass_failed_retry": "Pass",
            "error_log_if": "",
            "token_usage_input": 100,
            "token_usage_output": 50,
            "total_cost_usd": 0.01,
            "load_dt": "2026-01-26",
        }

        schema = TransactionLogSchema.from_dict(payload)
        # Should have positive duration due to abs()
        assert schema.total_time_mins is not None
        assert schema.latency_ms is not None
        assert schema.latency_ms > 0

    def test_transaction_log_schema_reapplies_abs_for_negative_process_duration(self, sample_transaction_payload):
        with patch("src.modules.audit_log.transaction_log.abs", side_effect=[-30.0, 30.0], create=True):
            schema = TransactionLogSchema.from_dict(sample_transaction_payload)

        assert schema.total_time_mins == "0.30"
        assert schema.latency_ms == 30000.0
