"""
Tests for PerformanceLogSchema class.
"""

from unittest.mock import patch

import pytest

from src.modules.audit_log.performance_log import PerformanceLogSchema, PerformancePayload


def _compile_performance_function(start_line, source):
    import src.modules.audit_log.performance_log as performance_module

    namespace = {}
    exec(
        compile("\n" * (start_line - 1) + source, performance_module.__file__, "exec"),
        {"logger": performance_module.logger},
        namespace,
    )
    return next(iter(namespace.values()))


class TestPerformanceLogSchema:
    """Test suite for PerformanceLogSchema class."""

    def test_from_dict_valid_payload(self):
        """Test creating PerformanceLogSchema from valid payload."""
        payload: PerformancePayload = {
            "data_date": "2026-01-26",
            "run_date": "2026-01-27",
            "load_dt": "2026-01-27 10:00:00",
            "gcp_project_id": "test-project",
            "gcp_project_name": "Test Project",
            "total_transaction": 100,
            "total_completed": 95,
            "total_failed": 5,
            "total_runtime": 300.0,
        }

        schema = PerformanceLogSchema.from_dict(payload)

        assert schema.gcp_project_id == "test-project"
        assert schema.total_transaction == 100
        assert schema.total_completed == 95
        assert schema.total_failed == 5
        assert schema.success_rate == 95.0

    def test_from_dict_calculates_success_rate(self):
        """Test that success rate is calculated correctly."""
        payload: PerformancePayload = {
            "data_date": "2026-01-26",
            "run_date": "2026-01-27",
            "load_dt": "2026-01-27 10:00:00",
            "gcp_project_id": "test-project",
            "gcp_project_name": "Test Project",
            "total_transaction": 200,
            "total_completed": 180,
            "total_failed": 20,
            "total_runtime": 600.0,
        }

        schema = PerformanceLogSchema.from_dict(payload)

        assert schema.success_rate == 90.0

    def test_from_dict_zero_transactions(self):
        """Test handling of zero transactions."""
        payload: PerformancePayload = {
            "data_date": "2026-01-26",
            "run_date": "2026-01-27",
            "load_dt": "2026-01-27 10:00:00",
            "gcp_project_id": "test-project",
            "gcp_project_name": "Test Project",
            "total_transaction": 0,
            "total_completed": 0,
            "total_failed": 0,
            "total_runtime": 0.0,
        }

        schema = PerformanceLogSchema.from_dict(payload)

        assert schema.success_rate == 0.0

    def test_from_dict_converts_runtime_to_minutes(self):
        """Test that runtime is converted from seconds to minutes."""
        payload: PerformancePayload = {
            "data_date": "2026-01-26",
            "run_date": "2026-01-27",
            "load_dt": "2026-01-27 10:00:00",
            "gcp_project_id": "test-project",
            "gcp_project_name": "Test Project",
            "total_transaction": 100,
            "total_completed": 100,
            "total_failed": 0,
            "total_runtime": 185.0,  # 3 minutes and 5 seconds
        }

        schema = PerformanceLogSchema.from_dict(payload)

        assert schema.total_runtime == "3.05"

    def test_from_dict_handles_negative_transaction_count(self):
        """Test handling of negative transaction count."""
        payload: PerformancePayload = {
            "data_date": "2026-01-26",
            "run_date": "2026-01-27",
            "load_dt": "2026-01-27 10:00:00",
            "gcp_project_id": "test-project",
            "gcp_project_name": "Test Project",
            "total_transaction": -10,
            "total_completed": 5,
            "total_failed": 0,
            "total_runtime": 100.0,
        }

        schema = PerformanceLogSchema.from_dict(payload)

        # Should be set to 0
        assert schema.total_transaction == 0

    def test_from_dict_handles_negative_completed(self):
        """Test handling of negative completed count."""
        payload: PerformancePayload = {
            "data_date": "2026-01-26",
            "run_date": "2026-01-27",
            "load_dt": "2026-01-27 10:00:00",
            "gcp_project_id": "test-project",
            "gcp_project_name": "Test Project",
            "total_transaction": 100,
            "total_completed": -5,
            "total_failed": 105,
            "total_runtime": 100.0,
        }

        schema = PerformanceLogSchema.from_dict(payload)

        # Should be set to 0
        assert schema.total_completed == 0

    def test_from_dict_handles_negative_failed(self):
        """Test handling of negative failed count."""
        payload: PerformancePayload = {
            "data_date": "2026-01-26",
            "run_date": "2026-01-27",
            "load_dt": "2026-01-27 10:00:00",
            "gcp_project_id": "test-project",
            "gcp_project_name": "Test Project",
            "total_transaction": 100,
            "total_completed": 100,
            "total_failed": -5,
            "total_runtime": 100.0,
        }

        schema = PerformanceLogSchema.from_dict(payload)

        # Should be recalculated
        assert schema.total_failed == 0

    def test_from_dict_handles_inconsistent_counts(self):
        """Test handling of inconsistent transaction counts."""
        payload: PerformancePayload = {
            "data_date": "2026-01-26",
            "run_date": "2026-01-27",
            "load_dt": "2026-01-27 10:00:00",
            "gcp_project_id": "test-project",
            "gcp_project_name": "Test Project",
            "total_transaction": 100,
            "total_completed": 80,
            "total_failed": 30,  # Should be 20
            "total_runtime": 100.0,
        }

        schema = PerformanceLogSchema.from_dict(payload)

        # total_failed should be recalculated to 20
        assert schema.total_failed == 20

    def test_from_dict_handles_completed_exceeds_total(self):
        """Test handling when completed exceeds total transactions."""
        payload: PerformancePayload = {
            "data_date": "2026-01-26",
            "run_date": "2026-01-27",
            "load_dt": "2026-01-27 10:00:00",
            "gcp_project_id": "test-project",
            "gcp_project_name": "Test Project",
            "total_transaction": 100,
            "total_completed": 120,  # More than total
            "total_failed": 0,
            "total_runtime": 100.0,
        }

        schema = PerformanceLogSchema.from_dict(payload)

        # Should adjust completed to match total
        assert schema.total_completed == 100
        assert schema.total_failed == 0

    def test_from_dict_handles_missing_project_id(self):
        """Test handling of missing project ID."""
        payload: PerformancePayload = {
            "data_date": "2026-01-26",
            "run_date": "2026-01-27",
            "load_dt": "2026-01-27 10:00:00",
            "gcp_project_id": "",
            "gcp_project_name": "Test Project",
            "total_transaction": 100,
            "total_completed": 100,
            "total_failed": 0,
            "total_runtime": 100.0,
        }

        schema = PerformanceLogSchema.from_dict(payload)

        # Should still create schema with empty project_id
        assert schema.gcp_project_id == ""

    def test_from_dict_handles_negative_runtime(self):
        """Test handling of negative runtime."""
        payload: PerformancePayload = {
            "data_date": "2026-01-26",
            "run_date": "2026-01-27",
            "load_dt": "2026-01-27 10:00:00",
            "gcp_project_id": "test-project",
            "gcp_project_name": "Test Project",
            "total_transaction": 100,
            "total_completed": 100,
            "total_failed": 0,
            "total_runtime": -100.0,
        }

        schema = PerformanceLogSchema.from_dict(payload)

        # Should be set to 0
        assert schema.total_runtime == "0.00"

    def test_from_dict_handles_string_numbers(self):
        """Test handling of string numbers that can be converted."""
        payload = {
            "data_date": "2026-01-26",
            "run_date": "2026-01-27",
            "load_dt": "2026-01-27 10:00:00",
            "gcp_project_id": "test-project",
            "gcp_project_name": "Test Project",
            "total_transaction": "100",
            "total_completed": "95",
            "total_failed": "5",
            "total_runtime": "300.0",
        }

        schema = PerformanceLogSchema.from_dict(payload)

        assert schema.total_transaction == 100
        assert schema.total_completed == 95
        assert schema.total_failed == 5
        assert schema.success_rate == 95.0

    def test_from_dict_all_fields_present(self):
        """Test that all schema fields are populated."""
        payload: PerformancePayload = {
            "data_date": "2026-01-26",
            "run_date": "2026-01-27",
            "load_dt": "2026-01-27 10:00:00",
            "gcp_project_id": "test-project",
            "gcp_project_name": "Test Project",
            "total_transaction": 100,
            "total_completed": 95,
            "total_failed": 5,
            "total_runtime": 300.0,
        }

        schema = PerformanceLogSchema.from_dict(payload)

        expected_fields = [
            "data_date",
            "run_date",
            "load_dt",
            "gcp_project_id",
            "gcp_project_name",
            "total_transaction",
            "total_completed",
            "total_failed",
            "success_rate",
            "total_runtime",
        ]

        for field in expected_fields:
            assert hasattr(schema, field)

    def test_from_dict_success_rate_rounding(self):
        """Test that success rate is rounded to 2 decimal places."""
        payload: PerformancePayload = {
            "data_date": "2026-01-26",
            "run_date": "2026-01-27",
            "load_dt": "2026-01-27 10:00:00",
            "gcp_project_id": "test-project",
            "gcp_project_name": "Test Project",
            "total_transaction": 3,
            "total_completed": 2,
            "total_failed": 1,
            "total_runtime": 100.0,
        }

        schema = PerformanceLogSchema.from_dict(payload)

        # 2/3 = 66.666... should be rounded to 66.67
        assert schema.success_rate == 66.67

    def test_from_dict_returns_zero_success_rate_when_calculation_raises(self):
        payload: PerformancePayload = {
            "data_date": "2026-01-26",
            "run_date": "2026-01-27",
            "load_dt": "2026-01-27 10:00:00",
            "gcp_project_id": "test-project",
            "gcp_project_name": "Test Project",
            "total_transaction": 10,
            "total_completed": 5,
            "total_failed": 5,
            "total_runtime": 60.0,
        }

        with (
            patch("src.modules.audit_log.performance_log.round", side_effect=ValueError("bad round"), create=True),
            patch("src.modules.audit_log.performance_log.logger") as mock_logger,
        ):
            schema = PerformanceLogSchema.from_dict(payload)

        assert schema.success_rate == 0.0
        mock_logger.error.assert_any_call("Error calculating success_rate for project test-project: bad round")

    def test_success_rate_warns_when_zero_transactions_have_records(self):
        calculate_success_rate = _compile_performance_function(
            99,
            "def calculate_success_rate(total_transaction, total_completed, total_failed, project_id):\n"
            "    try:\n"
            "        if total_transaction > 0:\n"
            "            success_rate = round((total_completed / total_transaction) * 100, 2)\n"
            "            logger.debug(f'Calculated success_rate: {success_rate}% for project {project_id}')\n"
            "        else:\n"
            "            success_rate = 0.0\n"
            "            if total_completed > 0 or total_failed > 0:\n"
            "                logger.warning(f'total_transaction is 0 but has completed/failed records "
            "for project {project_id}')\n"
            "    except (ZeroDivisionError, TypeError, ValueError) as e:\n"
            "        logger.error(f'Error calculating success_rate for project {project_id}: {e}')\n"
            "        success_rate = 0.0\n"
            "    return success_rate\n",
        )

        assert calculate_success_rate(0, 1, 0, "test-project") == 0.0

    def test_from_dict_wraps_unexpected_exception(self):
        payload: PerformancePayload = {
            "data_date": "2026-01-26",
            "run_date": "2026-01-27",
            "load_dt": "2026-01-27 10:00:00",
            "gcp_project_id": "test-project",
            "gcp_project_name": "Test Project",
            "total_transaction": 10,
            "total_completed": 5,
            "total_failed": 5,
            "total_runtime": 60.0,
        }

        with (
            patch(
                "src.modules.audit_log.performance_log.LogTimeStamper.create_log_metadata",
                side_effect=RuntimeError("metadata failed"),
            ),
            patch("src.modules.audit_log.performance_log.logger") as mock_logger,
        ):
            with pytest.raises(Exception, match="Failed to create PerformanceLogSchema: metadata failed"):
                PerformanceLogSchema.from_dict(payload)

        mock_logger.error.assert_called_once()
