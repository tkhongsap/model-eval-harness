from zoneinfo import ZoneInfo
from datetime import datetime
from typing import TypedDict, Any, Optional
from dataclasses import dataclass
from src.utils.common import (
    parse_datetime,
)

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

@dataclass
class TransactionLogSchema:
    data_date: Optional[str]
    start_time: Optional[datetime]
    end_time: Optional[datetime]
    total_time_mins: Optional[str]
    type: Optional[str]
    gcp_project_id: Optional[str]
    gcp_project_name: Optional[str]
    user_id: Optional[str]
    source: Optional[str]
    storage_path: Optional[str]
    folder: Optional[str]
    filename: Optional[str]
    file_metadata_min: Optional[str]
    status_pass_failed_retry: Optional[str]
    error_log_if: Optional[str]
    latency_ms: Optional[float]
    token_usage_input: Optional[int]
    token_usage_output: Optional[int]
    total_cost_usd: Optional[float]

    @classmethod
    def from_dict(cls, transaction_log: TransactionPayload) -> "TransactionLogSchema":
        try:
            timezone = ZoneInfo("Asia/Bangkok")
        except Exception:
            # Fallback to UTC if timezone data is missing
            from datetime import timezone as dt_timezone
            timezone = dt_timezone.utc

        start_time = parse_datetime(val=transaction_log.get("start_time"), timezone=timezone)
        end_time = parse_datetime(val=transaction_log.get("end_time"), timezone=timezone)

        process_duration = abs((end_time - start_time).total_seconds()) if start_time and end_time else None
        total_time_mins = f"{int(process_duration // 60)}:{int(process_duration % 60):02d}" if process_duration is not None else None
        latency_ms = (process_duration * 1000) if process_duration is not None else None

        audio_duration = transaction_log.get("file_metadata_sec")
        file_metadata_min = f"{audio_duration // 60}.{(audio_duration % 60):02d}" if audio_duration is not None else None

        return cls(
            data_date=transaction_log.get("data_date"),
            start_time=start_time,
            end_time=end_time,
            total_time_mins=total_time_mins,
            type=transaction_log.get("type"),
            gcp_project_id=transaction_log.get("gcp_project_id"),
            gcp_project_name=transaction_log.get("gcp_project_name"),
            user_id=transaction_log.get("user_id"),
            source=transaction_log.get("source"),
            storage_path=transaction_log.get("storage_path"),
            folder=transaction_log.get("folder"),
            filename=transaction_log.get("filename"),
            file_metadata_min=file_metadata_min,
            status_pass_failed_retry=transaction_log.get("status_pass_failed_retry"),
            error_log_if=transaction_log.get("error_log_if"),
            latency_ms=latency_ms,
            token_usage_input=transaction_log.get("token_usage_input"),
            token_usage_output=transaction_log.get("token_usage_output"),
            total_cost_usd=transaction_log.get("total_cost_usd"),
        )