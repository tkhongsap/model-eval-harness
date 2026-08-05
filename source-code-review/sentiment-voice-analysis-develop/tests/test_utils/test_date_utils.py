from datetime import datetime
from zoneinfo import ZoneInfo

from src.utils.date_utils import (
    add_date,
    add_months,
    convert_datetime_format,
    is_format_datetime,
    list_date,
    parse_datetime,
)


class TestDateUtils:
    def test_add_months(self):
        # Normal addition
        dt = datetime(2025, 1, 31)
        assert add_months(dt, 1) == datetime(2025, 2, 28)  # Handle end of month
        assert add_months(dt, 2) == datetime(2025, 3, 31)

        # Subtraction
        assert add_months(dt, -1) == datetime(2024, 12, 31)

    def test_add_date(self):
        dt = datetime(2025, 1, 1)
        # Add days
        assert add_date(dt, 1) == datetime(2025, 1, 2)
        # Subtract days
        assert add_date(dt, -1) == datetime(2024, 12, 31)
        # String input
        assert add_date("2025-01-01", 1) == datetime(2025, 1, 2)

    def test_parse_datetime(self):
        tz = ZoneInfo("UTC")
        # String input
        dt = parse_datetime("2025-01-01T12:00:00", tz)
        assert dt.year == 2025
        assert dt.tzinfo == tz

        # Datetime input
        dt_input = datetime(2025, 1, 1, 12, 0, 0)
        dt = parse_datetime(dt_input, tz)
        assert dt.tzinfo == tz

        # None input
        assert parse_datetime(None, tz) is None
        # Invalid string
        assert parse_datetime("invalid", tz) is None

    def test_list_date(self):
        dates = list_date("2025-01-01", "2025-01-03")
        assert len(dates) == 3
        assert dates == ["2025-01-01", "2025-01-02", "2025-01-03"]

        # Custom format
        dates = list_date("20250101", "20250102", "%Y%m%d", "%d-%m-%Y")
        assert dates == ["01-01-2025", "02-01-2025"]

    def test_is_format_datetime(self):
        assert is_format_datetime("2025-01-01", "%Y-%m-%d")
        assert not is_format_datetime("20250101", "%Y-%m-%d")

    def test_convert_datetime_format(self):
        assert convert_datetime_format("2025-01-01", "%Y-%m-%d", "%Y%m%d") == "20250101"
        assert convert_datetime_format("invalid", "%Y-%m-%d", "%Y%m%d") is None
