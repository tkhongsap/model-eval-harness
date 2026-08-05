from datetime import datetime
from zoneinfo import ZoneInfo

from src.utils.common import (
    get_value_by_path,
)
from src.utils.date_utils import (
    is_format_datetime,
)
from src.utils.file_utils import load_yaml
from src.utils.logger import Logger

logger = Logger(__name__)


class LogTimeStamper:
    """Utility class for timestamp stamping in logs."""

    # Load timezone configuration at class level
    COMMON_CONFIG = "config/common.yml"
    _config_tz = get_value_by_path(load_yaml("config/common.yml"), "framework.timezone", None)
    if _config_tz is None:
        logger.warning("Timezone config not found, defaulting to 'Asia/Bangkok'")
        _config_tz = "Asia/Bangkok"

    CONFIG_TIMEZONE = _config_tz
    DEFAULT_TIMEZONE = ZoneInfo(_config_tz)

    # Set date formats as class variables
    DATE_FORMAT = "%Y%m%d"
    MONTH_FORMAT = "%Y%m"
    DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"

    @classmethod
    def get_current_datetime(cls, timezone: ZoneInfo | None = None) -> datetime:
        """Get the current datetime in the specified timezone or default timezone."""
        tz = timezone if timezone else cls.DEFAULT_TIMEZONE
        return datetime.now(tz)

    @classmethod
    def format_datetime(cls, dt: datetime, fmt: str | None = None) -> str:
        """Format a datetime object into a string based on the provided format."""
        fmt = fmt if fmt else cls.DATETIME_FORMAT
        return dt.strftime(fmt)

    @classmethod
    def stamp_current_datetime(cls, timezone: ZoneInfo | None = None) -> str:
        """Get the current datetime as a formatted string."""
        current_dt = cls.get_current_datetime(timezone)
        return cls.format_datetime(current_dt)

    @classmethod
    def stamp_int_date(cls, dt: datetime | str) -> str | None:
        """Convert a datetime object to an integer date string (YYYYMMDD)."""
        if dt is None or (isinstance(dt, str) and not dt.strip()):
            return None
        try:
            if is_format_datetime(dt, cls.DATE_FORMAT):
                return dt
            if isinstance(dt, str):
                dt = datetime.fromisoformat(dt)
                return dt.strftime(cls.DATE_FORMAT)
            return dt.strftime(cls.DATE_FORMAT)
        except Exception as e:
            logger.warning(f"Error stamping int date from {dt}: {e}")
            return None

    @classmethod
    def stamp_int_month(cls, dt: datetime) -> str:
        """Convert a datetime object to an integer month string (YYYYMM)."""
        return dt.strftime(cls.MONTH_FORMAT)

    @classmethod
    def create_log_metadata(cls, additional_fields: dict | None = None) -> dict:
        now = cls.get_current_datetime()
        metadata = {
            "timestamp": cls.format_datetime(now),
            "timestamp_unix": int(now.timestamp()),
            "timezone": cls.DEFAULT_TIMEZONE,
        }

        if additional_fields:
            metadata.update(additional_fields)
        return metadata
