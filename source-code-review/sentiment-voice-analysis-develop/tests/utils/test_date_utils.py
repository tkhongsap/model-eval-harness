"""
Tests for date utility functions.
"""

from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from src.utils.date_utils import (
    add_date,
    add_months,
    compute_date_range,
    convert_datetime_format,
    format_date_string,
    get_current_datetime,
    has_data_date_placeholder,
    is_format_datetime,
    list_date,
    parse_datetime,
    resolve_data_date_window,
)


class TestAddMonths:
    """Test suite for add_months function."""

    def test_add_positive_months(self):
        """Test adding positive months."""
        date = datetime(2026, 1, 15)
        result = add_months(date, 3)
        assert result.year == 2026
        assert result.month == 4
        assert result.day == 15

    def test_add_negative_months(self):
        """Test subtracting months."""
        date = datetime(2026, 5, 15)
        result = add_months(date, -3)
        assert result.year == 2026
        assert result.month == 2
        assert result.day == 15

    def test_add_months_across_year(self):
        """Test adding months across year boundary."""
        date = datetime(2026, 11, 15)
        result = add_months(date, 3)
        assert result.year == 2027
        assert result.month == 2
        assert result.day == 15

    def test_add_months_negative_across_year(self):
        """Test subtracting months across year boundary."""
        date = datetime(2026, 2, 15)
        result = add_months(date, -3)
        assert result.year == 2025
        assert result.month == 11
        assert result.day == 15

    def test_add_months_day_overflow(self):
        """Test adding months when day would overflow."""
        date = datetime(2026, 1, 31)
        result = add_months(date, 1)
        # January 31 + 1 month = February 28 (not 31)
        assert result.year == 2026
        assert result.month == 2
        assert result.day == 28

    def test_add_months_leap_year(self):
        """Test adding months in leap year."""
        date = datetime(2024, 1, 31)
        result = add_months(date, 1)
        # January 31, 2024 + 1 month = February 29, 2024 (leap year)
        assert result.year == 2024
        assert result.month == 2
        assert result.day == 29

    def test_add_zero_months(self):
        """Test adding zero months returns same date."""
        date = datetime(2026, 5, 15)
        result = add_months(date, 0)
        assert result == date

    def test_add_twelve_months(self):
        """Test adding 12 months."""
        date = datetime(2026, 1, 15)
        result = add_months(date, 12)
        assert result.year == 2027
        assert result.month == 1
        assert result.day == 15


class TestAddDate:
    """Test suite for add_date function."""

    def test_add_positive_days(self):
        """Test adding positive days."""
        date = datetime(2026, 1, 15)
        result = add_date(date, 5)
        assert result == datetime(2026, 1, 20)

    def test_add_negative_days(self):
        """Test subtracting days."""
        date = datetime(2026, 1, 15)
        result = add_date(date, -5)
        assert result == datetime(2026, 1, 10)

    def test_add_days_across_month(self):
        """Test adding days across month boundary."""
        date = datetime(2026, 1, 30)
        result = add_date(date, 5)
        assert result == datetime(2026, 2, 4)

    def test_add_days_across_year(self):
        """Test adding days across year boundary."""
        date = datetime(2026, 12, 30)
        result = add_date(date, 5)
        assert result == datetime(2027, 1, 4)

    def test_add_zero_days(self):
        """Test adding zero days returns same date."""
        date = datetime(2026, 5, 15)
        result = add_date(date, 0)
        assert result == date

    def test_add_large_number_days(self):
        """Test adding large number of days."""
        date = datetime(2026, 1, 1)
        result = add_date(date, 365)
        assert result == datetime(2027, 1, 1)


class TestParseDatetime:
    """Test suite for parse_datetime function."""

    def test_parse_iso_string(self):
        """Test parsing ISO format datetime string."""
        tz = ZoneInfo("Asia/Bangkok")
        val = "2026-01-26T10:30:00+07:00"
        result = parse_datetime(val, tz)
        assert result is not None
        assert isinstance(result, datetime)

    def test_parse_datetime_object(self):
        """Test parsing datetime object."""
        tz = ZoneInfo("Asia/Bangkok")
        val = datetime(2026, 1, 26, 10, 30, 0)
        result = parse_datetime(val, tz)
        assert result is not None
        assert isinstance(result, datetime)

    def test_parse_none_returns_none(self):
        """Test that None input returns None."""
        tz = ZoneInfo("Asia/Bangkok")
        result = parse_datetime(None, tz)
        assert result is None

    def test_parse_empty_string_returns_none(self):
        """Test that empty string returns None."""
        tz = ZoneInfo("Asia/Bangkok")
        result = parse_datetime("", tz)
        assert result is None

    def test_parse_invalid_string_returns_none(self):
        """Test that invalid datetime string returns None."""
        tz = ZoneInfo("Asia/Bangkok")
        result = parse_datetime("not-a-datetime", tz)
        assert result is None

    def test_parse_datetime_converts_timezone(self):
        """Test that datetime is converted to specified timezone."""
        tz = ZoneInfo("Asia/Bangkok")
        val = datetime(2026, 1, 26, 10, 30, 0, tzinfo=ZoneInfo("UTC"))
        result = parse_datetime(val, tz)
        assert result.tzinfo == tz


class TestListDate:
    """Test suite for list_date function."""

    def test_list_date_single_day(self):
        """Test listing dates for same start and end."""
        result = list_date("2026-01-26", "2026-01-26")
        assert result == ["2026-01-26"]

    def test_list_date_multiple_days(self):
        """Test listing dates for multiple days."""
        result = list_date("2026-01-26", "2026-01-28")
        assert result == ["2026-01-26", "2026-01-27", "2026-01-28"]

    def test_list_date_across_month(self):
        """Test listing dates across month boundary."""
        result = list_date("2026-01-30", "2026-02-02")
        assert len(result) == 4
        assert result[0] == "2026-01-30"
        assert result[-1] == "2026-02-02"

    def test_list_date_custom_input_format(self):
        """Test listing dates with custom input format."""
        result = list_date("20260126", "20260128", input_date_format="%Y%m%d")
        assert len(result) == 3

    def test_list_date_custom_output_format(self):
        """Test listing dates with custom output format."""
        result = list_date("2026-01-26", "2026-01-28", output_date_format="%Y%m%d")
        assert result == ["20260126", "20260127", "20260128"]

    def test_list_date_custom_formats(self):
        """Test listing dates with custom input and output formats."""
        result = list_date("26/01/2026", "28/01/2026", input_date_format="%d/%m/%Y", output_date_format="%Y-%m-%d")
        assert result == ["2026-01-26", "2026-01-27", "2026-01-28"]


class TestIsFormatDatetime:
    """Test suite for is_format_datetime function."""

    def test_valid_format_yyyymmdd(self):
        """Test valid YYYYMMDD format."""
        assert is_format_datetime("20260126", "%Y%m%d") is True

    def test_valid_format_with_hyphens(self):
        """Test valid YYYY-MM-DD format."""
        assert is_format_datetime("2026-01-26", "%Y-%m-%d") is True

    def test_valid_format_with_time(self):
        """Test valid datetime with time format."""
        assert is_format_datetime("2026-01-26 10:30:45", "%Y-%m-%d %H:%M:%S") is True

    def test_invalid_format(self):
        """Test invalid datetime format."""
        assert is_format_datetime("not-a-date", "%Y-%m-%d") is False

    def test_wrong_format(self):
        """Test datetime with wrong format."""
        assert is_format_datetime("2026-01-26", "%Y%m%d") is False

    def test_partial_match(self):
        """Test datetime with partial format match."""
        assert is_format_datetime("2026-01", "%Y-%m-%d") is False

    def test_extra_characters(self):
        """Test datetime with extra characters."""
        assert is_format_datetime("2026-01-26-extra", "%Y-%m-%d") is False

    def test_empty_string(self):
        """Test empty string."""
        assert is_format_datetime("", "%Y-%m-%d") is False


class TestAddDateWithString:
    """Additional tests for add_date function with string input."""

    def test_add_date_with_iso_string(self):
        """Test add_date with ISO format string input."""
        result = add_date("2026-01-15T10:00:00", 5)
        expected = datetime(2026, 1, 20, 10, 0, 0)
        assert result == expected

    def test_add_date_with_date_string(self):
        """Test add_date with date string input."""
        result = add_date("2026-01-15", 10)
        expected = datetime(2026, 1, 25)
        assert result == expected


class TestConvertDatetimeFormat:
    """Test suite for convert_datetime_format function."""

    def test_convert_valid_format(self):
        """Test converting datetime string from one format to another."""
        result = convert_datetime_format("2026-01-26", "%Y-%m-%d", "%d/%m/%Y")
        assert result == "26/01/2026"

    def test_convert_with_time(self):
        """Test converting datetime string with time."""
        result = convert_datetime_format("2026-01-26 14:30:45", "%Y-%m-%d %H:%M:%S", "%d-%m-%Y %H:%M")
        assert result == "26-01-2026 14:30"

    def test_convert_to_different_format(self):
        """Test converting to completely different format."""
        result = convert_datetime_format("26/01/2026", "%d/%m/%Y", "%Y%m%d")
        assert result == "20260126"

    def test_convert_invalid_format_returns_none(self):
        """Test that invalid format returns None."""
        result = convert_datetime_format("invalid-date", "%Y-%m-%d", "%d/%m/%Y")
        assert result is None

    def test_convert_mismatched_format_returns_none(self):
        """Test that mismatched format returns None."""
        result = convert_datetime_format("2026-01-26", "%d/%m/%Y", "%Y%m%d")
        assert result is None


class TestParseDatetimeAdditional:
    def test_returns_none_for_unsupported_type(self):
        assert parse_datetime(123, ZoneInfo("UTC")) is None


class TestGetCurrentDatetime:
    @patch("src.utils.date_utils.load_yaml", return_value={"framework": {"timezone": "Asia/Bangkok"}})
    def test_uses_timezone_from_config(self, mock_load_yaml):
        result = get_current_datetime()

        assert result.tzinfo == ZoneInfo("Asia/Bangkok")
        mock_load_yaml.assert_called_once_with("config/common.yml")

    @patch("src.utils.date_utils.load_yaml", return_value={"framework": {}})
    def test_falls_back_to_utc_when_timezone_missing(self, mock_load_yaml):
        result = get_current_datetime()

        assert result.tzinfo == ZoneInfo("UTC")
        mock_load_yaml.assert_called_once_with("config/common.yml")

    @patch("src.utils.date_utils.load_yaml", return_value={"framework": {"timezone": "Asia/Bangkok"}})
    def test_prefers_explicit_timezone_argument(self, mock_load_yaml):
        result = get_current_datetime(ZoneInfo("UTC"))

        assert result.tzinfo == ZoneInfo("UTC")
        mock_load_yaml.assert_called_once_with("config/common.yml")


class TestComputeDateRange:
    def test_accepts_string_end_date(self):
        assert compute_date_range("2026-01-10", 3) == ("2026-01-08", "2026-01-10")

    def test_accepts_datetime_end_date(self):
        end_date = datetime(2026, 1, 10)
        assert compute_date_range(end_date, 1) == ("2026-01-10", "2026-01-10")


class TestFormatDateString:
    def test_formats_compact_date_string(self):
        assert format_date_string("20260421") == "21 Apr 2026"

    def test_returns_original_value_for_invalid_input(self):
        assert format_date_string("not-a-date") == "not-a-date"


class TestResolveDataDateWindow:
    """Test suite for resolve_data_date_window — CLI date-flag resolution."""

    _DEFAULT = datetime(2026, 6, 13, 9, 30, 0)

    def test_no_flags_returns_default_unchanged(self):
        """With no flags, the default datetime is returned verbatim (today's behavior)."""
        result = resolve_data_date_window(None, None, None, self._DEFAULT)
        assert result == [self._DEFAULT]

    def test_rerun_wins_and_parses_to_midnight(self):
        """A rerun date is returned as a single naive-midnight datetime."""
        result = resolve_data_date_window("2026-06-10", None, None, self._DEFAULT)
        assert result == [datetime(2026, 6, 10)]

    def test_range_is_inclusive_and_ascending(self):
        """A start/end range yields every day inclusive, ascending."""
        result = resolve_data_date_window(None, "2026-06-10", "2026-06-13", self._DEFAULT)
        assert result == [
            datetime(2026, 6, 10),
            datetime(2026, 6, 11),
            datetime(2026, 6, 12),
            datetime(2026, 6, 13),
        ]

    def test_single_day_range_start_equals_end(self):
        """A range whose start equals its end yields exactly one day."""
        result = resolve_data_date_window(None, "2026-06-10", "2026-06-10", self._DEFAULT)
        assert result == [datetime(2026, 6, 10)]

    def test_range_across_month_boundary(self):
        """A range spanning a month boundary lists every day across it."""
        result = resolve_data_date_window(None, "2026-05-30", "2026-06-02", self._DEFAULT)
        assert result == [
            datetime(2026, 5, 30),
            datetime(2026, 5, 31),
            datetime(2026, 6, 1),
            datetime(2026, 6, 2),
        ]

    def test_rerun_with_start_raises(self):
        """rerun combined with a range bound is rejected."""
        with pytest.raises(ValueError, match="cannot be combined"):
            resolve_data_date_window("2026-06-10", "2026-06-11", None, self._DEFAULT)

    def test_rerun_with_end_raises(self):
        """rerun combined with the other range bound is rejected."""
        with pytest.raises(ValueError, match="cannot be combined"):
            resolve_data_date_window("2026-06-10", None, "2026-06-11", self._DEFAULT)

    def test_lone_start_raises(self):
        """A start bound without an end bound is rejected."""
        with pytest.raises(ValueError, match="must be provided together"):
            resolve_data_date_window(None, "2026-06-10", None, self._DEFAULT)

    def test_lone_end_raises(self):
        """An end bound without a start bound is rejected."""
        with pytest.raises(ValueError, match="must be provided together"):
            resolve_data_date_window(None, None, "2026-06-10", self._DEFAULT)

    def test_start_after_end_raises(self):
        """A start later than the end is rejected."""
        with pytest.raises(ValueError, match="must not be after"):
            resolve_data_date_window(None, "2026-06-13", "2026-06-10", self._DEFAULT)

    def test_bad_format_slash_separator_raises(self):
        """A non-ISO separator is rejected as a bad format."""
        with pytest.raises(ValueError, match="YYYY-MM-DD"):
            resolve_data_date_window("2026/06/13", None, None, self._DEFAULT)

    def test_bad_format_day_first_raises(self):
        """A day-first value is rejected as a bad format."""
        with pytest.raises(ValueError, match="YYYY-MM-DD"):
            resolve_data_date_window(None, "13-06-2026", "2026-06-14", self._DEFAULT)


class TestHasDataDatePlaceholder:
    """Test suite for has_data_date_placeholder — must mirror resolve_date's regex."""

    def test_plain_format_placeholder_is_true(self):
        """A placeholder with a format part is detected."""
        assert has_data_date_placeholder(".../%{DATA_DATE_YYYYMMDD}") is True

    def test_offset_placeholder_is_true(self):
        """A placeholder with an offset and format part is detected."""
        assert has_data_date_placeholder("gs://b/%{DATA_DATE-7D_YYYYMMDD}/x") is True

    def test_bare_placeholder_is_false(self):
        """A bare %{DATA_DATE} (no mandatory format part) is NOT a resolvable placeholder."""
        assert has_data_date_placeholder("path/%{DATA_DATE}") is False

    def test_env_style_token_is_false(self):
        """An env-var-style ${DATA_DATE} token is not a date placeholder."""
        assert has_data_date_placeholder("path/${DATA_DATE}") is False

    def test_no_placeholder_is_false(self):
        """A plain path with no placeholder returns False."""
        assert has_data_date_placeholder("root/input") is False

    def test_none_is_false(self):
        """None is treated as empty and returns False."""
        assert has_data_date_placeholder(None) is False
