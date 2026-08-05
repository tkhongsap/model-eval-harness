import uuid
from dataclasses import dataclass
from typing import ClassVar, TypedDict

from src.modules.audit_log.log_interface import LogInterface
from src.modules.audit_log.log_time_stamper import LogTimeStamper
from src.utils.logger import Logger

logger = Logger(__name__)

DATE_FAILURE_FILENAME_PREFIX = "<date_level_failure>"
"""Sentinel filename prefix for batch_processing_log rows that record a
run-/date-level pre-process failure (no specific source file produced the row).
The post-process (`TaxInvoiceGetBatchResultTask`) branches on this prefix to
emit the row as a date-level audit record with empty `FILE_PATH`."""


class BatchProcessingPayload(TypedDict, total=True):
    job_id: str
    data_date: str
    gcp_project_id: str
    gcp_project_name: str
    gcs_bucket_name: str
    source_path: str
    filename: str
    prediction_payload_path: str | None


@dataclass
class BatchProcessingLogSchema(LogInterface):
    # Cluster these next to log_id/log_type in column_names().
    _IDENTITY_FIELDS: ClassVar[tuple[str, ...]] = LogInterface._IDENTITY_FIELDS + (
        "job_id",
        "batch_job_id",
        "batch_job_display_name",
        "model_name",
    )

    # Default identification fields
    log_id: str = None
    log_type: str = None
    job_id: str | None = None
    batch_job_id: str | None = None
    batch_job_display_name: str | None = None
    model_name: str | None = None

    # Batch processing log fields
    data_date: str | None = None
    gcp_project_id: str | None = None
    gcp_project_name: str | None = None
    gcs_bucket_name: str | None = None
    source_path: str | None = None
    filename: str | None = None
    prediction_payload_path: str | None = None

    @classmethod
    def from_dict(cls, payload: BatchProcessingPayload) -> "BatchProcessingLogSchema":
        try:
            log_id = f"BATCH-{uuid.uuid4().hex[:12]}"
            log_type = "Batch Processing Log"
            metadata = LogTimeStamper.create_log_metadata()
            logger.debug(f"Creating BatchProcessingLogSchema {log_id} from payload: {payload}")
            return cls(
                log_id=log_id,
                log_type=log_type,
                job_id=payload.get("job_id"),
                data_date=LogTimeStamper.stamp_int_date(payload.get("data_date")),
                source_path=payload.get("source_path"),
                filename=payload.get("filename"),
                gcp_project_id=payload.get("gcp_project_id"),
                gcp_project_name=payload.get("gcp_project_name"),
                gcs_bucket_name=payload.get("gcs_bucket_name"),
                prediction_payload_path=payload.get("prediction_payload_path"),
                # Default LogInterface fields (Future changes)
                action="BATCH_PROCESSING_LOG_WRITTEN",
                status="PROCESSING",
                error_message=None,
                created_dt=metadata["timestamp"],
                updated_dt=metadata["timestamp"],
            )
        except Exception as e:
            logger.error(f"Error creating BatchProcessingLogSchema: {e}")
            raise Exception(f"Error creating BatchProcessingLogSchema: {e}") from e

    def stamp_payload_path(self, prediction_payload_path: str) -> None:
        self.prediction_payload_path = prediction_payload_path
        logger.debug(f"Stamped prediction_payload_path: {prediction_payload_path} into BatchProcessingLogSchema")
