"""Pre-processing-log row construction (pure; no I/O)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from src.modules.microsoft.sharepoint import SharePointModule
from src.utils.common import resolve_env
from src.utils.logger import Logger
from tasks.ocr_tax_invoice_pipeline.helper.constant import JobStatus, QualityStatus
from tasks.ocr_tax_invoice_pipeline.helper.messages import unsupported_file_reason
from tasks.ocr_tax_invoice_pipeline.helper.task_context import OCRTaskContext
from tasks.ocr_tax_invoice_pipeline.schema.contracts import BatchSubmission

logger = Logger(__name__)


@dataclass(frozen=True)
class PreLogContext:
    """Resolved, per-run constants needed to stamp a pre-processing-log row."""

    job_id: str
    pipeline_name: str
    domain: str
    gcp_project_id: str
    gcs_project_id: str
    vertexai_project_id: str
    batch_inference_location: str
    batch_inference_model_name: str
    timezone: ZoneInfo

    @classmethod
    def from_task_context(cls, ctx: OCRTaskContext) -> PreLogContext:
        """Build from an :class:`OCRTaskContext`, env-resolving the project/model fields."""
        return cls(
            job_id=ctx.job_id,
            pipeline_name=ctx.pipeline_name,
            domain=ctx.domain,
            gcp_project_id=resolve_env(ctx.gcp.get("project_id", "")),
            gcs_project_id=resolve_env(ctx.gcs.get("project_id", "")),
            vertexai_project_id=resolve_env(ctx.vertexai.get("project_id", "")),
            batch_inference_location=resolve_env(ctx.vertexai.get("location", "")),
            batch_inference_model_name=resolve_env(ctx.vertexai.get("model", "")),
            timezone=ctx.timezone,
        )


class PreLogRowBuilder:
    """Builds one ``PreProcessingLogSchema`` row per source file from this run's outcomes."""

    def __init__(self, context: PreLogContext, sharepoint_module: SharePointModule) -> None:
        """Initialise with the resolved per-run :class:`PreLogContext`."""
        self._ctx = context
        self._sharepoint_module = sharepoint_module

    def build(
        self,
        uploaded: list[dict],
        failed_uploads: list[dict],
        unsupported: list[dict],
        manifest_rows: list[dict],
        submissions: list[BatchSubmission],
        datadate: str,
    ) -> list[dict]:
        """Build all pre-processing-log rows for one run.

        Prepends one terminal REJECTED row per unsupported-extension file, followed by
        the failed-upload rows and the per-landed-file row groups.
        """
        rows = [self._unsupported_row(item, datadate) for item in unsupported]
        rows += [
            self._row(
                item["sp_path"],
                JobStatus.FAILED,
                datadate,
                message=f"Upload to GCS landing failed: {item.get('error', '')}",
            )
            for item in failed_uploads
        ]
        for item in uploaded:
            rows.extend(self._rows_for_file(item, manifest_rows, submissions, datadate))
        return rows

    def _unsupported_row(self, item: dict, datadate: str) -> dict:
        """Build the terminal REJECTED row for a file skipped for an unsupported extension.

        ``gcs_landing_path`` stays empty — the file was never uploaded to landing.
        """
        ext = Path(item["name"]).suffix.lower()
        return self._row(item["sp_path"], JobStatus.REJECTED, datadate, message=unsupported_file_reason(ext))

    def _rows_for_file(
        self, item: dict, manifest_rows: list[dict], submissions: list[BatchSubmission], datadate: str
    ) -> list[dict]:
        """Build the INITIAL row plus the terminal-for-now row for one landed file."""
        sp_path, landing_path = item["sp_path"], item["gcs_path"]
        pages = [r for r in manifest_rows if r["parent_path"] == landing_path]
        valid = [p for p in pages if p["quality_status"] == QualityStatus.ACCEPTED.value]
        rejected = [p for p in pages if p["quality_status"] == QualityStatus.REJECTED.value]

        rows = [self._row(sp_path, JobStatus.INITIAL, datadate, landing_path=landing_path, message=None)]
        if not valid:
            reasons = sorted({p["message"] for p in pages if p.get("message")})
            msg = "; ".join(reasons) if reasons else f"All {len(pages)} page(s) failed the image quality check"
            rows.append(self._row(sp_path, JobStatus.REJECTED, datadate, landing_path=landing_path, message=msg))
            return rows

        sub = next((s for s in submissions if landing_path in s.parent_paths), None)
        if sub and sub.error:
            rows.append(
                self._row(
                    sp_path,
                    JobStatus.FAILED,
                    datadate,
                    landing_path=landing_path,
                    payload_uri=sub.payload_uri,
                    message=f"Batch submit failed: {sub.error}",
                )
            )
            return rows
        rows.append(self._submitted_row(sp_path, landing_path, datadate, sub, valid, pages, rejected))
        return rows

    def _submitted_row(
        self,
        sp_path: str,
        landing_path: str,
        datadate: str,
        sub: BatchSubmission | None,
        valid: list[dict],
        pages: list[dict],
        rejected: list[dict],
    ) -> dict:
        """Build the PARTIAL (some pages rejected) or PENDING (all accepted) submit row."""
        job_name = sub.job.name if sub and sub.job else ""
        job_display_name = sub.job.display_name if sub and sub.job else ""
        payload_uri = sub.payload_uri if sub else ""
        output_uri = sub.output_uri if sub else ""
        common = {
            "landing_path": landing_path,
            "payload_uri": payload_uri,
            "job_name": job_name,
            "job_display_name": job_display_name,
            "output_uri": output_uri,
        }
        if rejected:
            msg = (
                f"{len(valid)} of {len(pages)} page(s) submitted to {job_name}; {len(rejected)} page(s) rejected by IQS"
            )
            return self._row(sp_path, JobStatus.PARTIAL, datadate, message=msg, **common)
        return self._row(sp_path, JobStatus.PENDING, datadate, message=None, **common)

    def _row(
        self,
        sp_path: str,
        status: JobStatus,
        datadate: str,
        message: str | None = "",
        job_name: str = "",
        job_display_name: str = "",
        output_uri: str = "",
        landing_path: str = "",
        payload_uri: str = "",
    ) -> dict:
        """Build a single ``PreProcessingLogSchema`` dict row (load_dt == update_dt == now)."""
        now = datetime.now(self._ctx.timezone).isoformat()
        return {
            "job_id": self._ctx.job_id,
            "pipeline_name": self._ctx.pipeline_name,
            "domain_name": self._ctx.domain,
            "sharepoint_input_path": sp_path,
            "sharepoint_web_url": self._sharepoint_module.get_web_url(sp_path),
            "gcp_project_id": self._ctx.gcp_project_id,
            "gcs_project_id": self._ctx.gcs_project_id,
            "gcs_landing_path": landing_path,
            "gcs_payload_path": payload_uri,
            "vertexai_project_id": self._ctx.vertexai_project_id,
            "batch_inference_location": self._ctx.batch_inference_location,
            "batch_inference_model_name": self._ctx.batch_inference_model_name,
            "batch_inference_job_name": job_name,
            "batch_inference_display_name": job_display_name,
            "batch_inference_output_path": output_uri,
            "status": status.value,
            "load_dt": now,
            "update_dt": now,
            "datadate": datadate,
            "message": message,
        }
