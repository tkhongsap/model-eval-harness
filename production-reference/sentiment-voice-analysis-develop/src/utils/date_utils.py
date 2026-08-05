import re
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from src.utils.file_utils import (
    load_yaml,
)
from src.utils.logger import Logger

logger = Logger(__name__)


def add_months(sourcedate: datetime, months: int) -> datetime:
    """
    Add or subtract months from a given date.
    Parameters:
        sourcedate (datetime): The original date.
        months (int): The number of months to add (can be negative).
    Returns:
        datetime: The new date after adding/subtracting months.
    """
    month = sourcedate.month - 1 + months
    year = sourcedate.year + month // 12
    month = month % 12 + 1
    day = min(
        sourcedate.day,
        [
            31,
            29 if year % 4 == 0 and year % 100 != 0 or year % 400 == 0 else 28,
            31,
            30,
            31,
            30,
            31,
            31,
            30,
            31,
            30,
            31,
        ][month - 1],
    )
    return sourcedate.replace(year=year, month=month, day=day)


def add_date(sourcedate: datetime | str, days: int) -> datetime:
    """
    Add or subtract days from a given date.
    Parameters:
        sourcedate (datetime | str): The original date.
        days (int): The number of days to add (can be negative).
    Returns:
        datetime: The new date after adding/subtracting days.
    """
    if isinstance(sourcedate, str):
        sourcedate = datetime.fromisoformat(sourcedate)
    return sourcedate + timedelta(days=days)


def parse_datetime(val: Any, timezone: ZoneInfo) -> datetime | None:
    """
    Parse a datetime value and convert it to the specified timezone.
    Parameters:
        val (Any): The datetime value to parse (str or datetime).
        timezone (ZoneInfo): The timezone to convert the datetime to.
    Returns:
        datetime | None: The parsed datetime in the specified timezone, or None if parsing fails.
    """
    if not val:
        return None
    if isinstance(val, str):
        try:
            return datetime.fromisoformat(val).astimezone(timezone)
        except ValueError:
            return None
    if isinstance(val, datetime):
        return val.astimezone(timezone)
    return None


def list_date(
    start_date: str, end_date: str, input_date_format: str = "%Y-%m-%d", output_date_format: str = "%Y-%m-%d"
) -> list[str]:
    """
    Generate a list of date strings between start_date and end_date inclusive.
    Parameters:
        start_date (str): The start date as a string.
        end_date (str): The end date as a string.
        input_date_format (str): The format of the input date strings.
        output_date_format (str): The format of the output date strings.
    Returns:
        list[str]: A list of date strings in the specified format.
    """
    start_dt = datetime.strptime(start_date, input_date_format)
    end_dt = datetime.strptime(end_date, input_date_format)
    delta = end_dt - start_dt
    return [(start_dt + timedelta(days=i)).strftime(output_date_format) for i in range(delta.days + 1)]


def is_format_datetime(val: str, date_format: str) -> bool:
    """
    Check if a string matches a given datetime format.
    Parameters:
        val (str): The datetime string to check.
        date_format (str): The expected datetime format.
    Returns:
        bool: True if the string matches the format, False otherwise.
    """
    try:
        datetime.strptime(val, date_format)
        return True
    except ValueError:
        return False


def convert_datetime_format(val: str, from_format: str, to_format: str) -> str | None:
    """
    Convert a datetime string from one format to another.
    Parameters:
        val (str): The datetime string to convert.
        from_format (str): The current format of the datetime string.
        to_format (str): The desired format of the datetime string.
    Returns:
        str | None: The converted datetime string, or None if conversion fails.
    """
    try:
        dt = datetime.strptime(val, from_format)
        return dt.strftime(to_format)
    except ValueError:
        return None


def get_current_datetime(timezone: ZoneInfo | None = None) -> datetime:
    """
    Get the current datetime in the specified timezone or default timezone from configuration.
    Parameters:
        timezone (ZoneInfo | None): The timezone to use. If None, use default from config or UTC.
    Returns:
        datetime: The current datetime in the specified timezone.
    """
    default_tz_str = load_yaml("config/common.yml").get("framework", {}).get("timezone", None)
    if timezone is not None:
        default_tz = timezone
    elif default_tz_str is None and timezone is None:
        logger.warning("Default timezone not found in configuration. Using UTC.")
        default_tz = ZoneInfo("UTC")
    else:
        default_tz = ZoneInfo(default_tz_str)

    return datetime.now(tz=default_tz)


def compute_date_range(end_date: str | datetime, lookback_days: int, date_format: str = "%Y-%m-%d") -> tuple[str, str]:
    """
    Compute a (start_date, end_date) window of exactly `lookback_days` days
    ending on `end_date` (inclusive on both sides).

    start_date = end_date - (lookback_days - 1)

    Parameters:
        end_date (str | datetime): The last date of the window. If str, must match `date_format`.
        lookback_days (int): Total number of days in the window (>= 1).
        date_format (str): Format for both parsing (when end_date is str) and output. Default is '%Y-%m-%d'.
    Returns:
        tuple[str, str]: (start_date, end_date) both formatted with `date_format`.
    """
    if isinstance(end_date, str):
        end_date = datetime.strptime(end_date, date_format)

    end_dt = add_date(end_date, 0)
    start_dt = add_date(end_date, -(lookback_days - 1))
    return start_dt.strftime(date_format), end_dt.strftime(date_format)


def format_date_string(date_str: str) -> str:
    """Converts 'YYYYMMDD' to 'DD Mon YYYY' (e.g., 20260421 -> 21 Apr 2026)"""
    try:
        date_obj = datetime.strptime(str(date_str), "%Y%m%d")
        # Format it to 'Day ShortMonth Year'
        return date_obj.strftime("%d %b %Y")
    except Exception:
        return date_str


def resolve_data_date_window(
    rerun_data_dt: str | None,
    start_data_dt: str | None,
    end_data_dt: str | None,
    default_dt: datetime,
) -> list[datetime]:
    """Resolve CLI date flags to an inclusive, ascending list of data dates.

    Priority: ``rerun_data_dt`` > (``start_data_dt``, ``end_data_dt``) > ``[default_dt]``.

    Args:
        rerun_data_dt: Single replay date, ``YYYY-MM-DD`` or None.
        start_data_dt: Range start, ``YYYY-MM-DD`` or None.
        end_data_dt: Range end, ``YYYY-MM-DD`` or None.
        default_dt: Fallback when no flag is set; returned unchanged as ``[default_dt]``
            so the no-flag behavior is identical to today (paths resolve to execution_dt).

    Returns:
        Naive midnight datetimes for the flag paths (``resolve_date`` only strftimes the
        date part), or ``[default_dt]`` verbatim when no flag is set.

    Raises:
        ValueError: rerun combined with either range bound; only one range bound given;
            start > end; or any value not parseable as ``%Y-%m-%d``.
    """

    def _parse(value: str, flag: str) -> datetime:
        try:
            return datetime.strptime(value, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError(f"{flag} must be in YYYY-MM-DD format (got: {value!r})") from exc

    if rerun_data_dt:
        if start_data_dt or end_data_dt:
            raise ValueError("--rerun_data_dt cannot be combined with --start_data_dt/--end_data_dt")
        return [_parse(rerun_data_dt, "--rerun_data_dt")]

    if start_data_dt or end_data_dt:
        if not (start_data_dt and end_data_dt):
            raise ValueError("--start_data_dt and --end_data_dt must be provided together")
        start_dt = _parse(start_data_dt, "--start_data_dt")
        end_dt = _parse(end_data_dt, "--end_data_dt")
        if start_dt > end_dt:
            raise ValueError(f"--start_data_dt ({start_data_dt}) must not be after --end_data_dt ({end_data_dt})")
        span = (end_dt - start_dt).days
        return [start_dt + timedelta(days=offset) for offset in range(span + 1)]

    return [default_dt]


def has_data_date_placeholder(text: str) -> bool:
    """Return True when ``text`` contains a ``%{DATA_DATE...}`` placeholder.

    The inline regex mirrors :func:`src.utils.common.resolve_date` exactly — the format
    part is mandatory, so a bare ``%{DATA_DATE}`` returns False. If the two regexes ever
    diverge a config could take the windowed path and then resolve every date to the same
    literal string.

    Args:
        text: The candidate path/template (None is treated as empty).

    Returns:
        True if a resolvable ``%{DATA_DATE...}`` placeholder is present, else False.
    """
    pattern = r"%\{DATA_DATE([_\-][+-]?\d+[DMY])?([_\-].+?)\}"
    return re.search(pattern, text or "") is not None
