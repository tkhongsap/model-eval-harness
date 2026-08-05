"""
Tests for LogInterface dataclass.
"""

from src.modules.audit_log.batch_processing_log import BatchProcessingLogSchema
from src.modules.audit_log.log_interface import LogInterface


class TestLogInterfaceInit:
    """Test LogInterface initialization and default values."""

    def test_init_with_required_fields(self):
        """Test creating LogInterface with only required fields."""
        log = LogInterface(log_id="test-123", log_type="TRANSACTION")
        assert log.log_id == "test-123"
        assert log.log_type == "TRANSACTION"
        assert log.action == "INITIATED"
        assert log.status == "INITIATED"
        assert log.error_message is None
        assert log.updated_dt is None

    def test_init_created_dt_auto_set(self):
        """Test that created_dt is automatically set on creation."""
        log = LogInterface(log_id="test-123", log_type="TRANSACTION")
        assert log.created_dt is not None
        assert isinstance(log.created_dt, str)

    def test_init_with_custom_values(self):
        """Test creating LogInterface with custom field values."""
        log = LogInterface(
            log_id="custom-id",
            log_type="PERFORMANCE",
            action="CUSTOM_ACTION",
            status="IN_PROGRESS",
            error_message="some error",
        )
        assert log.log_id == "custom-id"
        assert log.log_type == "PERFORMANCE"
        assert log.action == "CUSTOM_ACTION"
        assert log.status == "IN_PROGRESS"
        assert log.error_message == "some error"


class TestStampUpdate:
    """Test stamp_update method."""

    def test_stamp_update_sets_action_and_status(self):
        """Test that stamp_update correctly updates action and status."""
        log = LogInterface(log_id="test-123", log_type="TRANSACTION")
        log.stamp_update(action="PROCESSING", status="IN_PROGRESS")
        assert log.action == "PROCESSING"
        assert log.status == "IN_PROGRESS"
        assert log.updated_dt is not None

    def test_stamp_update_sets_updated_dt(self):
        """Test that stamp_update sets the updated_dt timestamp."""
        log = LogInterface(log_id="test-123", log_type="TRANSACTION")
        assert log.updated_dt is None
        log.stamp_update(action="CHECK", status="PENDING")
        assert log.updated_dt is not None

    def test_stamp_update_multiple_times(self):
        """Test calling stamp_update multiple times updates correctly."""
        log = LogInterface(log_id="test-123", log_type="TRANSACTION")
        log.stamp_update(action="FIRST", status="PENDING")

        log.stamp_update(action="SECOND", status="COMPLETE")
        assert log.action == "SECOND"
        assert log.status == "COMPLETE"


class TestStampCompletion:
    """Test stamp_completion method."""

    def test_stamp_completion_default_success(self):
        """Test stamp_completion sets SUCCESS status by default."""
        log = LogInterface(log_id="test-123", log_type="TRANSACTION")
        log.stamp_completion(action="UPLOAD")
        assert log.action == "UPLOAD"
        assert log.status == "SUCCESS"
        assert log.error_message is None
        assert log.updated_dt is not None

    def test_stamp_completion_custom_status(self):
        """Test stamp_completion with custom status."""
        log = LogInterface(log_id="test-123", log_type="TRANSACTION")
        log.stamp_completion(action="DOWNLOAD", status="DONE")
        assert log.status == "DONE"

    def test_stamp_completion_clears_error_message(self):
        """Test stamp_completion clears any previous error message."""
        log = LogInterface(log_id="test-123", log_type="TRANSACTION")
        log.error_message = "previous error"
        log.stamp_completion(action="RETRY")
        assert log.error_message is None


class TestStampError:
    """Test stamp_error method."""

    def test_stamp_error_default_failed(self):
        """Test stamp_error sets FAILED status by default."""
        log = LogInterface(log_id="test-123", log_type="TRANSACTION")
        log.stamp_error(action="UPLOAD", error_message="Upload failed")
        assert log.action == "UPLOAD"
        assert log.status == "FAILED"
        assert log.error_message == "Upload failed"
        assert log.updated_dt is not None

    def test_stamp_error_custom_status(self):
        """Test stamp_error with custom status."""
        log = LogInterface(log_id="test-123", log_type="TRANSACTION")
        log.stamp_error(action="CHECK", error_message="Timeout", status="TIMEOUT")
        assert log.status == "TIMEOUT"
        assert log.error_message == "Timeout"

    def test_stamp_error_overwrites_previous_state(self):
        """Test stamp_error overwrites previous completion state."""
        log = LogInterface(log_id="test-123", log_type="TRANSACTION")
        log.stamp_completion(action="UPLOAD")
        assert log.status == "SUCCESS"

        log.stamp_error(action="POST_CHECK", error_message="Validation failed")
        assert log.status == "FAILED"
        assert log.error_message == "Validation failed"


class TestGetDurationSeconds:
    """Test get_duration_seconds method."""

    def test_get_duration_when_not_updated(self):
        """Test get_duration_seconds returns None when updated_dt is not set."""
        log = LogInterface(log_id="test-123", log_type="TRANSACTION")
        assert log.get_duration_seconds() is None

    def test_get_duration_with_valid_timestamps(self):
        """Test get_duration_seconds calculates correct duration."""
        log = LogInterface(log_id="test-123", log_type="TRANSACTION")
        log.created_dt = "2026-01-26 10:00:00"
        log.updated_dt = "2026-01-26 10:00:30"
        duration = log.get_duration_seconds()
        assert duration == 30.0

    def test_get_duration_with_minutes(self):
        """Test get_duration_seconds with multi-minute duration."""
        log = LogInterface(log_id="test-123", log_type="TRANSACTION")
        log.created_dt = "2026-01-26 10:00:00"
        log.updated_dt = "2026-01-26 10:05:30"

        duration = log.get_duration_seconds()
        assert duration == 330.0

    def test_get_duration_with_invalid_format(self):
        """Test get_duration_seconds returns None for invalid date format."""
        log = LogInterface(log_id="test-123", log_type="TRANSACTION")
        log.created_dt = "invalid-date"
        log.updated_dt = "also-invalid"
        assert log.get_duration_seconds() is None


class TestToDict:
    """Test to_dict method."""

    def test_to_dict_contains_all_fields(self):
        """Test to_dict includes all dataclass fields."""
        log = LogInterface(log_id="test-123", log_type="TRANSACTION")
        result = log.to_dict()

        assert isinstance(result, dict)
        assert result["log_id"] == "test-123"
        assert result["log_type"] == "TRANSACTION"
        assert result["action"] == "INITIATED"
        assert result["status"] == "INITIATED"
        assert "created_dt" in result

    def test_to_dict_includes_duration(self):
        """Test to_dict includes calculated duration_seconds when available."""
        log = LogInterface(log_id="test-123", log_type="TRANSACTION")
        log.created_dt = "2026-01-26 10:00:00"
        log.updated_dt = "2026-01-26 10:01:00"

        result = log.to_dict()
        assert "duration_seconds" in result
        assert result["duration_seconds"] == 60.0

    def test_to_dict_no_duration_when_not_updated(self):
        """Test to_dict does not include duration_seconds when not yet updated."""
        log = LogInterface(log_id="test-123", log_type="TRANSACTION")
        result = log.to_dict()
        assert "duration_seconds" not in result

    def test_to_dict_after_error(self):
        """Test to_dict correctly captures error state."""
        log = LogInterface(log_id="test-123", log_type="TRANSACTION")
        log.stamp_error(action="UPLOAD", error_message="Network error")

        result = log.to_dict()
        assert result["status"] == "FAILED"
        assert result["error_message"] == "Network error"
        assert result["action"] == "UPLOAD"


class TestLogInterfaceWorkflow:
    """Test complete workflow scenarios."""

    def test_full_success_workflow(self):
        """Test a complete success workflow: init -> update -> complete."""
        log = LogInterface(log_id="wf-001", log_type="BATCH")
        assert log.status == "INITIATED"

        log.stamp_update(action="PROCESSING", status="IN_PROGRESS")
        assert log.status == "IN_PROGRESS"

        log.stamp_completion(action="DONE")
        assert log.status == "SUCCESS"
        assert log.error_message is None

    def test_error_then_retry_workflow(self):
        """Test error then successful retry workflow."""
        log = LogInterface(log_id="wf-002", log_type="BATCH")

        log.stamp_error(action="UPLOAD", error_message="Timeout")
        assert log.status == "FAILED"
        assert log.error_message == "Timeout"

        log.stamp_completion(action="RETRY_UPLOAD")
        assert log.status == "SUCCESS"
        assert log.error_message is None

    def test_to_dict_captures_final_state(self):
        """Test to_dict accurately captures the final state after multiple ops."""
        log = LogInterface(log_id="wf-003", log_type="PERFORMANCE")
        log.stamp_update(action="STEP_1", status="IN_PROGRESS")
        log.stamp_completion(action="STEP_2")

        result = log.to_dict()
        assert result["status"] == "SUCCESS"
        assert result["action"] == "STEP_2"
        assert result["error_message"] is None


class TestColumnNames:
    """Tests for LogInterface.column_names() — identity → domain → audit order.

    duration_seconds is intentionally absent from column_names() — it's a
    derived value that to_dict() still emits but the CSV header source does not.
    """

    def test_base_log_interface_column_names(self):
        assert LogInterface.column_names() == [
            "log_id",
            "log_type",
            "action",
            "status",
            "created_dt",
            "updated_dt",
            "error_message",
        ]

    def test_batch_processing_log_schema_column_names_identity_prefix(self):
        cols = BatchProcessingLogSchema.column_names()
        # Identity prefix: parent identity + subclass identity, in that order.
        assert cols[:6] == [
            "log_id",
            "log_type",
            "job_id",
            "batch_job_id",
            "batch_job_display_name",
            "model_name",
        ]
        # Audit tail (last 5 columns — duration_seconds no longer appended).
        assert cols[-5:] == [
            "action",
            "status",
            "created_dt",
            "updated_dt",
            "error_message",
        ]
        # Domain fields fall in between.
        assert "data_date" in cols[6:-5]
        assert "prediction_payload_path" in cols[6:-5]
        assert "duration_seconds" not in cols

    def test_column_names_have_no_duplicates(self):
        for schema in (LogInterface, BatchProcessingLogSchema):
            cols = schema.column_names()
            assert len(cols) == len(set(cols)), f"{schema.__name__} has duplicate columns: {cols}"

    def test_to_dict_keeps_duration_seconds_despite_being_absent_from_column_names(self):
        """to_dict() must still emit duration_seconds — telesale/qa schemas
        list it and would otherwise see a NaN column. column_names() doesn't
        include it; ensure_df_schema drops the extra column for tax_invoice.
        """
        log = BatchProcessingLogSchema(log_id="x", log_type="y")
        log.stamp_completion(action="done")  # populates updated_dt → duration available

        d = log.to_dict()

        assert "duration_seconds" in d
        assert "duration_seconds" not in BatchProcessingLogSchema.column_names()
        non_duration_keys = set(d.keys()) - {"duration_seconds"}
        assert non_duration_keys.issubset(set(BatchProcessingLogSchema.column_names()))
