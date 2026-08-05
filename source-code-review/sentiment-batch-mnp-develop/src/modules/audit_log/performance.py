import os
from typing import TypedDict, Optional
from dataclasses import dataclass
from src.log import Logger

logger = Logger(__name__, env=os.environ.get("LOG_ENVIRONMENT", "prod"))

class PerformancePayload(TypedDict, total=True):
    data_date: str
    run_date: str
    gcp_project_id: str
    gcp_project_name: str
    total_transaction: int
    total_completed: int
    total_failed: int
    total_runtime: str

@dataclass
class PerformanceLogSchema:
    data_date: Optional[str]
    run_date: Optional[str]
    gcp_project_id: Optional[str]
    gcp_project_name: Optional[str]
    total_transaction: Optional[int]
    total_completed: Optional[int]
    total_failed: Optional[int]
    success_rate: Optional[float]
    total_runtime: Optional[str]

    @classmethod
    def from_dict(cls, performance_log: PerformancePayload) -> "PerformanceLogSchema":
        project_id = performance_log.get("gcp_project_id")
        total_transaction = performance_log.get("total_transaction", 0)
        total_completed = performance_log.get("total_completed", 0)
        total_failed = performance_log.get("total_failed", 0)
        expected_total_failed = total_transaction - total_completed
        if total_failed != expected_total_failed:
            logger.warning(
                f"Data inconsistency in performance log for project {project_id}: "
                f"total_transaction ({total_transaction}) != total_completed ({total_completed}) + total_failed ({total_failed}). "
                f"Overriding total_failed with calculated value ({expected_total_failed})."
            )
            total_failed = expected_total_failed
        success_rate = round((total_completed / total_transaction) * 100, 2) if total_transaction > 0 else 0.0
        return cls(
            data_date=performance_log.get("data_date"),
            run_date=performance_log.get("run_date"),
            gcp_project_id=project_id,
            gcp_project_name=performance_log.get("gcp_project_name"),
            total_transaction=total_transaction,
            total_completed=total_completed,
            total_failed=total_failed,
            success_rate=success_rate,
            total_runtime=performance_log.get("total_runtime"),
        )