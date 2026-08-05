import uuid
from dataclasses import dataclass
from typing import TypedDict

from src.modules.audit_log.log_interface import LogInterface
from src.modules.audit_log.log_time_stamper import LogTimeStamper
from src.utils.common import (
    safe_cast_value,
)
from src.utils.logger import Logger

logger = Logger(__name__)


class PerformancePayload(TypedDict, total=True):
    data_date: str
    run_date: str
    load_dt: str
    gcp_project_id: str
    gcp_project_name: str
    total_transaction: int
    total_completed: int
    total_failed: int
    total_runtime: str


@dataclass
class PerformanceLogSchema(LogInterface):
    # Default identification fields
    log_id: str = None
    log_type: str = None

    # Performance log fields (current version)
    data_date: str | None = None
    run_date: str | None = None
    load_dt: str | None = None
    gcp_project_id: str | None = None
    gcp_project_name: str | None = None
    total_transaction: int | None = None
    total_completed: int | None = None
    total_failed: int | None = None
    success_rate: float | None = None
    total_runtime: str | None = None
    average_response_time_ms: str | None = None

    @classmethod
    def from_dict(cls, payload: PerformancePayload) -> "PerformanceLogSchema":
        try:
            # Generate unique log ID
            log_id = f"PERF-{uuid.uuid4().hex[:12]}"
            log_type = "Performance Log"

            # Create metadata
            metadata = LogTimeStamper.create_log_metadata(
                additional_fields={"project": payload.get("gcp_project_id"), "run_date": payload.get("run_date")}
            )

            logger.debug(f"Creating PerformanceLogSchema {log_id} from payload: {payload}")

            # Extract and validate project ID
            project_id = payload.get("gcp_project_id")
            if not project_id:
                logger.warning("gcp_project_id is missing or empty in performance log payload")

            # Safely extract numeric values with validation
            total_transaction = safe_cast_value(payload.get("total_transaction", 0), int, 0)
            if total_transaction < 0:
                logger.warning(
                    f"Invalid total_transaction ({total_transaction}) for project {project_id}, setting to 0"
                )
                total_transaction = 0

            total_completed = safe_cast_value(payload.get("total_completed", 0), int, 0)
            if total_completed < 0:
                logger.warning(f"Invalid total_completed ({total_completed}) for project {project_id}, setting to 0")
                total_completed = 0

            total_failed = safe_cast_value(payload.get("total_failed", 0), int, 0)
            if total_failed < 0:
                logger.warning(f"Invalid total_failed ({total_failed}) for project {project_id}, setting to 0")
                total_failed = 0

            # Validate data consistency
            expected_total_failed = total_transaction - total_completed
            if expected_total_failed < 0:
                logger.warning(
                    f"Data inconsistency for project {project_id}: "
                    f"total_completed ({total_completed}) > total_transaction ({total_transaction}). "
                    f"Setting total_completed to total_transaction."
                )
                total_completed = total_transaction
                expected_total_failed = 0

            if total_failed != expected_total_failed:
                logger.warning(
                    f"Data inconsistency in performance log for project {project_id}: "
                    f"total_transaction ({total_transaction}) != total_completed ({total_completed}) + "
                    f"total_failed ({total_failed}). "
                    f"Overriding total_failed with calculated value ({expected_total_failed})."
                )
                total_failed = expected_total_failed

            # Calculate success rate with error handling
            try:
                if total_transaction > 0:
                    success_rate = round((total_completed / total_transaction) * 100, 2)
                    logger.debug(f"Calculated success_rate: {success_rate}% for project {project_id}")
                else:
                    success_rate = 0.0
                    if total_completed > 0 or total_failed > 0:
                        logger.warning(
                            f"total_transaction is 0 but has completed/failed records for project {project_id}"
                        )
            except (ZeroDivisionError, TypeError, ValueError) as e:
                logger.error(f"Error calculating success_rate for project {project_id}: {e}")
                success_rate = 0.0

            # Parse runtime with error handling
            total_runtime_sec = safe_cast_value(payload.get("total_runtime", 0.00), float, 0.0)
            if total_runtime_sec < 0:
                logger.warning(f"Invalid total_runtime ({total_runtime_sec}) for project {project_id}, setting to 0")
                total_runtime_sec = 0.0

            total_runtime_min = f"{int(total_runtime_sec // 60)}.{int(total_runtime_sec % 60):02d}"
            logger.debug(f"Converted runtime: {total_runtime_sec}s -> {total_runtime_min} min")

            log_schema = cls(
                # Default identification fields (Future changes)
                log_id=log_id,
                log_type=log_type,
                # Performance log fields (current version)
                data_date=LogTimeStamper.stamp_int_date(payload.get("data_date")),
                run_date=LogTimeStamper.stamp_int_date(payload.get("run_date")),
                load_dt=payload.get("load_dt"),
                gcp_project_id=project_id,
                gcp_project_name=payload.get("gcp_project_name"),
                total_transaction=total_transaction,
                total_completed=total_completed,
                total_failed=total_failed,
                success_rate=success_rate,
                total_runtime=total_runtime_min,
                # Default LogInterface fields (Future changes)
                action="PERFORMANCE_LOG_WRITTEN",
                status="COMPLETED",
                error_message=None,
                created_dt=metadata["timestamp"],
                updated_dt=metadata["timestamp"],  # Same as created_dt initially (standard pattern)
                average_response_time_ms=payload.get("average_response_time_ms"),
            )

            logger.info(
                f"Successfully created PerformanceLogSchema {log_id} for project {project_id}: "
                f"{total_transaction} total, {total_completed} completed, {success_rate}% success"
            )
            return log_schema

        except Exception as e:
            logger.error(f"Unexpected error creating PerformanceLogSchema: {e}", exc_info=True)
            raise Exception(f"Failed to create PerformanceLogSchema: {e}") from e
