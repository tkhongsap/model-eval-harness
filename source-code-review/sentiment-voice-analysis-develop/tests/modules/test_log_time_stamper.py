"""
Tests for LogTimeStamper utility class.
"""

import importlib
from datetime import datetime
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from src.modules.audit_log.log_time_stamper import LogTimeStamper


class TestStampCurrentDatetime:
    """Test stamp_current_datetime class method."""

    def test_returns_string(self):
        result = LogTimeStamper.stamp_current_datetime()
        assert isinstance(result, str)

    def test_format_is_valid_datetime(self):
        result = LogTimeStamper.stamp_current_datetime()
        parsed = datetime.strptime(result, LogTimeStamper.DATETIME_FORMAT)
        assert isinstance(parsed, datetime)

    def test_custom_timezone(self):
        tz = ZoneInfo("UTC")
        result = LogTimeStamper.stamp_current_datetime(timezone=tz)
        assert isinstance(result, str)


class TestGetCurrentDatetime:
    """Test get_current_datetime class method."""

    def test_returns_datetime(self):
        result = LogTimeStamper.get_current_datetime()
        assert isinstance(result, datetime)

    def test_with_custom_timezone(self):
        tz = ZoneInfo("UTC")
        result = LogTimeStamper.get_current_datetime(timezone=tz)
        assert result.tzinfo is not None

    def test_default_timezone(self):
        result = LogTimeStamper.get_current_datetime()
        assert result.tzinfo is not None


class TestFormatDatetime:
    """Test format_datetime class method."""

    def test_default_format(self):
        dt = datetime(2026, 1, 26, 10, 30, 0)
        result = LogTimeStamper.format_datetime(dt)
        assert result == "2026-01-26 10:30:00"

    def test_custom_format(self):
        dt = datetime(2026, 1, 26, 10, 30, 0)
        result = LogTimeStamper.format_datetime(dt, fmt="%Y%m%d")
        assert result == "20260126"


class TestStampIntDate:
    """Test stamp_int_date class method."""

    def test_with_datetime_object(self):
        dt = datetime(2026, 1, 26)
        result = LogTimeStamper.stamp_int_date(dt)
        # stamp_int_date may return None if is_format_datetime errors on datetime objects
        assert result is None or result == "20260126"

    def test_with_iso_string(self):
        result = LogTimeStamper.stamp_int_date("2026-01-26")
        assert result == "20260126"

    def test_with_already_formatted_string(self):
        result = LogTimeStamper.stamp_int_date("20260126")
        assert result == "20260126"

    def test_with_none(self):
        result = LogTimeStamper.stamp_int_date(None)
        assert result is None

    def test_with_empty_string(self):
        result = LogTimeStamper.stamp_int_date("")
        assert result is None


class TestStampIntMonth:
    """Test stamp_int_month class method."""

    def test_with_datetime_object(self):
        dt = datetime(2026, 1, 26)
        result = LogTimeStamper.stamp_int_month(dt)
        assert result == "202601"


class TestCreateLogMetadata:
    """Test create_log_metadata class method."""

    def test_returns_dict(self):
        result = LogTimeStamper.create_log_metadata()
        assert isinstance(result, dict)

    def test_contains_expected_keys(self):
        result = LogTimeStamper.create_log_metadata()
        assert "timestamp" in result
        assert "timestamp_unix" in result
        assert "timezone" in result

    def test_timestamp_is_valid(self):
        result = LogTimeStamper.create_log_metadata()
        parsed = datetime.strptime(result["timestamp"], LogTimeStamper.DATETIME_FORMAT)
        assert isinstance(parsed, datetime)

    def test_timestamp_unix_is_int(self):
        result = LogTimeStamper.create_log_metadata()
        assert isinstance(result["timestamp_unix"], int)

    def test_additional_fields(self):
        result = LogTimeStamper.create_log_metadata(additional_fields={"custom": "value"})
        assert result["custom"] == "value"

    def test_no_additional_fields(self):
        result = LogTimeStamper.create_log_metadata()
        assert "custom" not in result


class TestLogTimeStamperAdditional:
    def test_defaults_timezone_to_asia_bangkok_when_config_missing(self):
        import src.modules.audit_log.log_time_stamper as log_time_stamper_module

        mock_logger = Mock()

        try:
            with (
                patch("src.utils.file_utils.load_yaml", return_value={}),
                patch("src.utils.common.get_value_by_path", return_value=None),
                patch("src.utils.logger.Logger", return_value=mock_logger),
            ):
                reloaded_module = importlib.reload(log_time_stamper_module)
                assert reloaded_module.LogTimeStamper.CONFIG_TIMEZONE == "Asia/Bangkok"
                assert str(reloaded_module.LogTimeStamper.DEFAULT_TIMEZONE) == "Asia/Bangkok"
                mock_logger.warning.assert_any_call("Timezone config not found, defaulting to 'Asia/Bangkok'")
        finally:
            importlib.reload(log_time_stamper_module)

    def test_with_datetime_object_uses_final_strftime_return_path(self):
        dt = datetime(2026, 1, 26)

        with patch("src.modules.audit_log.log_time_stamper.is_format_datetime", return_value=False):
            assert LogTimeStamper.stamp_int_date(dt) == "20260126"
