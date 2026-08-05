import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import TypedDict

from src.modules.audit_log.log_interface import LogInterface
from src.modules.audit_log.log_time_stamper import LogTimeStamper
from src.utils.common import (
    safe_cast_value,
)
from src.utils.date_utils import (
    parse_datetime,
)
from src.utils.logger import Logger

logger = Logger(__name__)


class TransactionPayload(TypedDict, total=True):
    data_date: str
    start_time: str
    end_time: str
    type: str
    gcp_project_id: str
    gcp_project_name: str
    user_id: str
    source: str
    storage_path: str
    folder: str
    filename: str
    file_metadata_sec: int
    status_pass_failed_retry: str
    error_log_if: str
    token_usage_input: int
    token_usage_output: int
    total_cost_usd: float
    load_dt: str


@dataclass
class TransactionLogSchema(LogInterface):
    data_date: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    total_time_mins: str | None = None
    type: str | None = None
    gcp_project_id: str | None = None
    gcp_project_name: str | None = None
    user_id: str | None = None
    source: str | None = None
    storage_path: str | None = None
    folder: str | None = None
    filename: str | None = None
    file_metadata_min: str | None = None
    status_pass_failed_retry: str | None = None
    error_log_if: str | None = None
    latency_ms: float | None = None
    token_usage_input: int | None = None
    token_usage_output: int | None = None
    total_cost_usd: float | None = None
    load_dt: str | None = None

    @classmethod
    def from_dict(cls, payload: TransactionPayload) -> "TransactionLogSchema":
        try:
            # Generate unique log ID
            log_id = f"TXN-{uuid.uuid4().hex[:12]}"
            log_type = "Transaction Log"

            # Create metadata
            metadata = LogTimeStamper.create_log_metadata()
            filename = payload.get("filename", "unknown")
            start_time = payload.get("start_time")
            try:
                start_time = None if not start_time else parse_datetime(val=start_time, timezone=metadata["timezone"])
            except Exception as e:
                logger.error(f"File '{filename}': Error parsing start_time: {e}", exc_info=True)
                start_time = None

            try:
                end_time = payload.get("end_time")
                end_time = None if not end_time else parse_datetime(val=end_time, timezone=metadata["timezone"])
            except Exception as e:
                logger.error(f"File '{filename}': Error parsing end_time: {e}", exc_info=True)
                end_time = None

            try:
                # Calculate process duration with validation
                process_duration = None
                total_time_mins = None
                latency_ms = None
                if start_time and end_time:
                    process_duration = abs((end_time - start_time).total_seconds())

                    if process_duration < 0:
                        process_duration = abs(process_duration)

                    total_time_mins = f"{int(process_duration // 60)}.{int(process_duration % 60):02d}"
                    latency_ms = process_duration * 1000

            except Exception as duration_err:
                logger.error(f"File '{filename}': Error calculating process duration: {duration_err}", exc_info=True)
                process_duration = None
                total_time_mins = None
                latency_ms = None

            # Parse audio duration with error handling
            file_metadata_min = None
            try:
                audio_duration = safe_cast_value(payload.get("file_metadata_sec"), int, None)

                if audio_duration is not None:
                    if audio_duration < 0:
                        logger.warning(
                            f"File '{filename}': Negative audio duration ({audio_duration}s), setting to None"
                        )
                        audio_duration = None
                    else:
                        file_metadata_min = f"{audio_duration // 60}:{(audio_duration % 60):02d}"
                        logger.debug(f"File '{filename}': Audio duration: {file_metadata_min} min")
                else:
                    logger.debug(f"File '{filename}': No audio duration provided")

            except Exception as audio_err:
                logger.error(f"File '{filename}': Error parsing audio duration: {audio_err}", exc_info=True)
                file_metadata_min = None

            # Validate and cast token usage values
            try:
                token_usage_input = safe_cast_value(payload.get("token_usage_input"), int, 0)
                if token_usage_input < 0:
                    logger.warning(f"File '{filename}': Negative token_usage_input ({token_usage_input}), setting to 0")
                    token_usage_input = 0
            except Exception as token_in_err:
                logger.error(f"File '{filename}': Error parsing token_usage_input: {token_in_err}")
                token_usage_input = 0

            try:
                token_usage_output = safe_cast_value(payload.get("token_usage_output"), int, 0)
                if token_usage_output < 0:
                    logger.warning(
                        f"File '{filename}': Negative token_usage_output ({token_usage_output}), setting to 0"
                    )
                    token_usage_output = 0
            except Exception as token_out_err:
                logger.error(f"File '{filename}': Error parsing token_usage_output: {token_out_err}")
                token_usage_output = 0

            # Validate and cast cost
            try:
                total_cost_usd = safe_cast_value(payload.get("total_cost_usd"), float, 0.0)
                if total_cost_usd < 0:
                    logger.warning(f"File '{filename}': Negative total_cost_usd ({total_cost_usd}), setting to 0.0")
                    total_cost_usd = 0.0
            except Exception as cost_err:
                logger.error(f"File '{filename}': Error parsing total_cost_usd: {cost_err}")
                total_cost_usd = 0.0

            # Validate status
            status = payload.get("status_pass_failed_retry")
            if status not in ["Pass", "Failed", "Retry", None]:
                logger.warning(f"File '{filename}': Unexpected status value '{status}'")

            log_schema = cls(
                # Default identification fields (Future changes)
                log_id=log_id,
                log_type=log_type,
                # Transaction log fields (current version)
                data_date=LogTimeStamper.stamp_int_date(payload.get("data_date")),
                start_time=start_time,
                end_time=end_time,
                total_time_mins=total_time_mins,
                type=payload.get("type"),
                gcp_project_id=payload.get("gcp_project_id"),
                gcp_project_name=payload.get("gcp_project_name"),
                user_id=payload.get("user_id"),
                source=payload.get("source"),
                storage_path=payload.get("storage_path"),
                folder=payload.get("folder"),
                filename=filename,
                file_metadata_min=file_metadata_min,
                status_pass_failed_retry=status,
                error_log_if=payload.get("error_log_if"),
                latency_ms=latency_ms,
                token_usage_input=token_usage_input,
                token_usage_output=token_usage_output,
                total_cost_usd=total_cost_usd,
                load_dt=payload.get("load_dt"),
                # Default LogInterface fields (Future changes)
                action="TRANSACTION_LOG_WRITTEN",
                status="PROCESSING",
                error_message=None,
                created_dt=metadata["timestamp"],
                updated_dt=metadata["timestamp"],  # Same as created_dt initially (standard pattern)
            )

            logger.info(
                f"Successfully created TransactionLogSchema for '{filename}': status={status}, "
                f"tokens_in={token_usage_input}, tokens_out={token_usage_output}, cost=${total_cost_usd:.6f}"
            )
            return log_schema
        except Exception as e:
            logger.error(f"Unexpected error creating TransactionLogSchema: {e}", exc_info=True)
            raise Exception(f"Failed to create TransactionLogSchema: {e}") from e
