from dataclasses import dataclass, field, fields
from datetime import datetime
from typing import Any, ClassVar

from src.modules.audit_log.log_time_stamper import LogTimeStamper
from src.utils.logger import Logger

logger = Logger(__name__)


@dataclass
class LogInterface:
    """
    Base interface for audit logs with timestamping and status management.
    """

    # ClassVar so dataclass doesn't treat these as fields. Subclasses extend
    # _IDENTITY_FIELDS to cluster their own *_id fields next to log_id/log_type;
    # _AUDIT_FIELDS is fixed and trailing. column_names() composes:
    # identity + domain + audit. duration_seconds is NOT included on purpose.
    _IDENTITY_FIELDS: ClassVar[tuple[str, ...]] = ("log_id", "log_type")
    _AUDIT_FIELDS: ClassVar[tuple[str, ...]] = (
        "action",
        "status",
        "created_dt",
        "updated_dt",
        "error_message",
    )

    # Primary stamps
    log_id: str
    log_type: str

    # time stamp fields
    created_dt: str = field(default_factory=LogTimeStamper.stamp_current_datetime)
    updated_dt: str | None = None

    # action fields
    action: str = "INITIATED"
    status: str = "INITIATED"

    # error message field
    error_message: str | None = None

    def stamp_update(self, action: str, status: str) -> None:
        """Update the updated_dt timestamp and status."""
        self.updated_dt = LogTimeStamper.stamp_current_datetime()
        self.action = action
        self.status = status
        logger.debug(f"Log updated: action={self.action}, status={self.status}, updated_dt={self.updated_dt}")

    def stamp_completion(self, action: str, status: str = "SUCCESS") -> None:
        """Stamp log with completion timestamp and final status."""
        self.updated_dt = LogTimeStamper.stamp_current_datetime()
        self.action = action
        self.status = status
        self.error_message = None
        logger.debug(f"Log completed: status={self.status}, updated_dt={self.updated_dt}")

    def stamp_error(self, action: str, error_message: str, status: str = "FAILED") -> None:
        self.updated_dt = LogTimeStamper.stamp_current_datetime()
        self.action = action
        self.status = status
        self.error_message = error_message
        logger.debug(
            f"Log error: status={self.status}, error_message={self.error_message}, updated_dt={self.updated_dt}"
        )

    def get_duration_seconds(self) -> float | None:
        """Calculate duration between creation and completion."""
        if not self.updated_dt:
            return None
        try:
            created = datetime.strptime(self.created_dt, LogTimeStamper.DATETIME_FORMAT)
            updated = datetime.strptime(self.updated_dt, LogTimeStamper.DATETIME_FORMAT)
            return (updated - created).total_seconds()
        except Exception:
            return None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary, including all fields for DataFrame compatibility."""
        result = dict(self.__dict__)
        # Add calculated duration
        duration = self.get_duration_seconds()
        if duration is not None:
            result["duration_seconds"] = duration
        return result

    @classmethod
    def column_names(cls) -> list[str]:
        """Return CSV column names in identity → domain → audit order.

        Identity and audit groups are class-level tuples (``_IDENTITY_FIELDS``,
        ``_AUDIT_FIELDS``) so subclasses can extend identity (e.g. ``job_id``)
        without touching the audit tail. Domain = every dataclass field not
        tagged identity or audit, in dataclass declaration order
        (parent then subclass).

        ``duration_seconds`` is intentionally NOT included — it's a trivial
        derivation from ``created_dt`` / ``updated_dt`` that any reader can
        compute on demand, and ``get_duration_seconds()`` is still available
        on the instance. ``to_dict()`` continues to emit ``duration_seconds``
        so the existing telesale/qa CSV pipelines (which list it in their own
        hardcoded schemas) still receive computed values.
        """
        all_names = [f.name for f in fields(cls)]
        identity_set = set(cls._IDENTITY_FIELDS)
        audit_set = set(cls._AUDIT_FIELDS)
        identity = [n for n in cls._IDENTITY_FIELDS if n in all_names]
        domain = [n for n in all_names if n not in identity_set and n not in audit_set]
        audit = [n for n in cls._AUDIT_FIELDS if n in all_names]
        return identity + domain + audit
