"""Build and export per-page audit logs for the tax-invoice OCR run.

One transaction row is produced **per page (per JSONL line / Gemini prediction)** — the
OCR output explodes each page's single prediction into many line-item rows that all repeat
the same ``USAGE_METADATA`` / ``START_TIME`` / ``END_TIME``, so they are deduped back down to
one row per prediction before logging (summing usage across a page's line items would
double-count). Real token usage and USD cost are attached per page; the model version is
joined from the run's pre-processing log. The class:

* writes the transaction and performance logs to the control SharePoint site as append-only
  monthly CSVs, and
* emits an AI-operation summary via the logger (console in dev, JSON -> Cloud Logging in prod).
"""

from __future__ import annotations

import io
import json
import os
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from src.modules.audit_log.log_time_stamper import LogTimeStamper
from src.modules.audit_log.performance_log import PerformanceLogSchema, PerformancePayload
from src.modules.audit_log.transaction_log import TransactionLogSchema, TransactionPayload
from src.modules.google.gemini_batch import GeminiBatchModule
from src.modules.microsoft.sharepoint import SharePointModule
from src.utils.common import logging_ai_operation
from src.utils.logger import Logger
from src.utils.pandas_utils import ensure_df_schema, replace_nan_with_default
from src.utils.token_utils import gemini_cost
from tasks.ocr_tax_invoice_pipeline.helper.constant import OCROutputStatus
from tasks.ocr_tax_invoice_pipeline.helper.log_retention import (
    DEFAULT_RETENTION_DAYS,
    month_file_pattern,
    prune_by_timestamp,
    resolve_retention_days,
    retention_cutoff,
    sweep_month_files,
)

logger = Logger(__name__)

_CSV_ENCODING = "utf-8-sig"  # BOM so Thai characters open correctly in Excel
# Both audit logs date a row by `load_dt` (naive local; parsed as UTC, which errs toward keeping a
# row up to the tz offset longer — never toward pruning it early).
_RETENTION_COLUMN = "load_dt"
_COST_API_TYPE = "batch"
_PROJECT_TYPE = "batch"
# Terminal audit-action labels stamped onto each log row (matching the telesale/qa pipelines)
# so the LogInterface `status`/`action` columns advance past the intermediate PROCESSING state.
_TRANSACTION_LOG_ACTION = "Create Transaction Log"
_PERFORMANCE_LOG_ACTION = "Create Performance Log"
_REQUIRED_FIELDS = ("FILE_PATH", "PAGE_NO", "START_TIME", "END_TIME", "STATUS", "MESSAGE", "USAGE_METADATA", "DATADATE")
# Columns that identify one prediction (page / JSONL line). All are constant within a page's
# line items; including the usage/timestamps keeps distinct predictions apart when the
# file/page join misses (null FILE_PATH / PAGE_NO).
_PAGE_KEYS = ["FILE_PATH", "PAGE_NO", "START_TIME", "END_TIME", "_usage_key"]
_TRANSACTION_SORT_KEYS = ["updated_dt", "load_dt", "data_date", "start_time", "end_time"]
_PERFORMANCE_SORT_KEYS = ["load_dt", "data_date"]
_AI_OPERATION_COLUMNS = [
    "process_date",
    "environment",
    "project_id",
    "project_type",
    "total_transaction",
    "total_success_transaction",
    "total_failed_transaction",
    "average_response_time_sec",
    "total_runtime_sec",
]


class ExportLogging:
    """Produce and persist the OCR run's transaction, performance, and AI-operation logs."""

    DEFAULT_TYPE = "AI Classification"
    DEFAULT_USER_ID = "daisyrpa"
    DEFAULT_SOURCE = "SharePoint"

    def __init__(
        self,
        execution_dt: datetime,
        ocr_df: pd.DataFrame,
        pre_log_df: pd.DataFrame,
        cfg: dict,
        sharepoint: SharePointModule,
    ) -> None:
        """Validate the OCR frame and capture the connection + resolved log paths.

        Args:
            execution_dt: Pipeline execution datetime (drives ``load_dt`` / ``run_date``).
            ocr_df: OCR output frame (one row per line item) carrying ``_REQUIRED_FIELDS``.
            pre_log_df: Per-file pre-processing-log rows (supplies the model version).
            cfg: ``project_id`` / ``project_name``, the resolved ``transaction_log_path`` /
                ``performance_log_path``, and ``retention_days`` (negative disables retention).
            sharepoint: Control-site SharePoint connection for the monthly log CSVs.
        """
        if not isinstance(ocr_df, pd.DataFrame):
            logger.error("Input OCR data is not a DataFrame.")
            raise ValueError("ocr_df must be a pandas DataFrame.")
        self._validate_required_fields(ocr_df)
        self.execution_dt = execution_dt
        self.ocr_df = ocr_df
        self.pre_log_df = pre_log_df if isinstance(pre_log_df, pd.DataFrame) else pd.DataFrame()
        self.cfg = cfg or {}
        self.sharepoint = sharepoint
        self.transaction_log_path: str = self.cfg.get("transaction_log_path", "")
        self.performance_log_path: str = self.cfg.get("performance_log_path", "")
        self._load_dt: str = execution_dt.strftime("%Y-%m-%d %H:%M:%S")
        self._run_date: str = execution_dt.strftime("%Y-%m-%d")
        self._cutoff = retention_cutoff(
            resolve_retention_days(self.cfg.get("retention_days", DEFAULT_RETENTION_DAYS)),
            execution_dt.tzinfo if isinstance(execution_dt.tzinfo, ZoneInfo) else None,
        )

    @staticmethod
    def _validate_required_fields(df: pd.DataFrame) -> None:
        """Raise if the OCR frame is missing any column the logs need."""
        missing = [field for field in _REQUIRED_FIELDS if field not in df.columns]
        if missing:
            logger.error(f"Missing required fields for export logging: {missing}")
            raise ValueError(f"Missing required fields for export logging: {missing}")

    def export_logs(self, enable_oper_log: bool = True, p_type: str | None = None) -> None:
        """Build the logs and persist them; raise on failure so the caller can decide.

        Args:
            enable_oper_log: Whether to emit the AI-operation summary (best-effort).
            p_type: The type of the log entry.

        Raises:
            Exception: If any part of the log export fails (transaction or performance).
        """
        try:
            cost_config = self._cost_config()
            page_df = self._dedup_pages(self.ocr_df)
            transaction_df = self._build_transaction_log(page_df, cost_config, p_type)
            performance_df = self._build_performance_log(transaction_df)
            if enable_oper_log:
                self._ai_operation_logging(transaction_df)
            self._append_and_upload(
                transaction_df, self.transaction_log_path, _TRANSACTION_SORT_KEYS, "transaction log"
            )
            self._append_and_upload(
                performance_df, self.performance_log_path, _PERFORMANCE_SORT_KEYS, "performance log"
            )
            logger.info("Transaction and performance logs exported successfully")
        except Exception as exc:
            logger.error(f"Failed to export logs: {exc}", exc_info=True)
            raise

    # --- Build -----------------------------------------------------------------

    def _dedup_pages(self, ocr_df: pd.DataFrame) -> pd.DataFrame:
        """Collapse per-line-item rows to one row per prediction (page / JSONL line)."""
        df = ocr_df.copy()
        df["_usage_key"] = df["USAGE_METADATA"].map(self._usage_json)
        failed = OCROutputStatus.FAILED.value
        page_failed = df.groupby(_PAGE_KEYS, dropna=False, sort=False)["STATUS"].transform(lambda s: s.eq(failed).any())
        df["PAGE_STATUS"] = page_failed.map({True: failed, False: OCROutputStatus.SUCCESS.value})
        return df.drop_duplicates(subset=_PAGE_KEYS)

    def _build_transaction_log(
        self, page_df: pd.DataFrame, cost_config: list[dict], p_type: str | None = None
    ) -> pd.DataFrame:
        """One ``TransactionLogSchema`` row per page, with real token usage + USD cost."""
        if page_df.empty:
            return pd.DataFrame()
        model_map = self._pre_log_map("batch_inference_model_name")
        storage_map = self._pre_log_map("sharepoint_web_url")
        records = page_df.to_dict("records")
        usage_detail = {
            idx: {"model": model_map.get(row.get("FILE_PATH"), ""), **self._page_usage(row.get("USAGE_METADATA"))}
            for idx, row in enumerate(records)
        }
        cost_map = self._compute_costs(usage_detail, cost_config)
        rows = [
            self._transaction_row(row, usage_detail[idx], cost_map.get(idx, {}), storage_map, p_type)
            for idx, row in enumerate(records)
        ]
        return pd.DataFrame(rows)

    def _transaction_row(
        self, row: dict, usage: dict, cost: dict, storage_map: dict[str, str], p_type: str | None = None
    ) -> dict:
        """Build one transaction-log dict for a single page, stamped with its terminal audit state."""
        file_path = row.get("FILE_PATH") or ""
        is_failed = row.get("PAGE_STATUS") == OCROutputStatus.FAILED.value
        message = str(row.get("MESSAGE") or "")
        total_cost = round((cost.get("cost_input", 0.0) or 0.0) + (cost.get("cost_output", 0.0) or 0.0), 6)
        payload = TransactionPayload(
            data_date=str(row.get("DATADATE", "")),
            start_time=self._to_str(row.get("START_TIME")),
            end_time=self._to_str(row.get("END_TIME")),
            type=p_type or self.DEFAULT_TYPE,
            gcp_project_id=self.cfg.get("project_id"),
            gcp_project_name=self.cfg.get("project_name"),
            user_id=self.DEFAULT_USER_ID,
            source=self.DEFAULT_SOURCE,
            storage_path=storage_map.get(file_path, ""),
            folder=file_path,
            filename=file_path.split("/")[-1] if file_path else "",
            file_metadata_sec=0,
            status_pass_failed_retry="Failed" if is_failed else "Pass",
            error_log_if=message,
            token_usage_input=int(sum(usage["token_input"].values())),
            token_usage_output=int(sum(usage["token_output"].values())),
            total_cost_usd=total_cost,
            load_dt=self._load_dt,
        )
        schema = TransactionLogSchema.from_dict(payload)
        if is_failed:
            schema.stamp_error(action=_TRANSACTION_LOG_ACTION, error_message=message)
        else:
            schema.stamp_completion(action=_TRANSACTION_LOG_ACTION)
        return schema.to_dict()

    def _build_performance_log(self, transaction_df: pd.DataFrame) -> pd.DataFrame:
        """Aggregate transactions to one performance row per ``data_date`` + project."""
        if transaction_df.empty:
            return pd.DataFrame()
        grouped = transaction_df.groupby(
            ["data_date", "gcp_project_id", "gcp_project_name"], as_index=False, dropna=False
        ).agg(
            total_transaction=("status_pass_failed_retry", "count"),
            total_completed=("status_pass_failed_retry", lambda s: int((s == "Pass").sum())),
            total_failed=("status_pass_failed_retry", lambda s: int((s == "Failed").sum())),
            total_runtime_ms=("latency_ms", "sum"),
            load_dt=("load_dt", "max"),
        )
        rows = [self._performance_row(rec, self._run_date) for rec in grouped.itertuples(index=False)]
        return pd.DataFrame(rows)

    def _performance_row(self, rec: Any, run_date: str) -> dict:
        """Build one performance-log dict (``total_runtime`` in seconds)."""
        payload = PerformancePayload(
            data_date=str(rec.data_date),
            run_date=run_date,
            load_dt=str(rec.load_dt),
            gcp_project_id=str(rec.gcp_project_id),
            gcp_project_name=str(rec.gcp_project_name),
            total_transaction=int(rec.total_transaction),
            total_completed=int(rec.total_completed),
            total_failed=int(rec.total_failed),
            total_runtime=str(round((rec.total_runtime_ms or 0.0) / 1000, 2)),
        )
        schema = PerformanceLogSchema.from_dict(payload)
        schema.stamp_completion(action=_PERFORMANCE_LOG_ACTION)
        return schema.to_dict()

    def _cost_config(self) -> list[dict]:
        """Load batch pricing for the run's model versions (empty on any miss -> cost 0)."""
        df = self.pre_log_df
        if df.empty or "batch_inference_model_name" not in df.columns:
            return []
        models = [m for m in df["batch_inference_model_name"].dropna().unique().tolist() if m]
        if not models:
            return []
        try:
            return gemini_cost(_COST_API_TYPE, models)
        except Exception as exc:
            logger.warning(f"Could not load Gemini pricing; cost will be 0: {exc}")
            return []

    def _pre_log_map(self, value_col: str) -> dict[str, str]:
        """Map each source file path to *value_col* from the pre-proc log.

        Defensive: the pre-processing-log frame may be empty or (for logs written before a
        column existed) missing ``value_col`` / ``sharepoint_input_path`` — in any of those
        cases return ``{}`` rather than aborting the whole audit-log export.
        """
        df = self.pre_log_df
        if df.empty or "sharepoint_input_path" not in df.columns or value_col not in df.columns:
            return {}
        pairs = df.dropna(subset=["sharepoint_input_path"])
        return dict(zip(pairs["sharepoint_input_path"], pairs[value_col].fillna(""), strict=False))

    # --- AI-operation logging --------------------------------------------------

    def _ai_operation_logging(self, transaction_df: pd.DataFrame) -> None:
        """Emit one AI-operation summary per process-date + project (best-effort)."""
        logger.info("Stamping AI-Operation logs")
        try:
            if transaction_df.empty:
                logger.info("No transactions to summarize for AI-Operation logs")
                return
            log_df = self._ai_operation_frame(transaction_df)
            for log in log_df.to_dict(orient="records"):
                logging_ai_operation(log_instance=logger, log_obj=log, log_type="batch", message="AI-Operation-Log")
            logger.info("AI-Operation log stamping completed successfully")
        except Exception as log_err:
            logger.error(f"Failed to stamp AI-Operation logs: {log_err}", exc_info=True)

    def _ai_operation_frame(self, transaction_df: pd.DataFrame) -> pd.DataFrame:
        """Shape the transaction frame into the AI-operation summary columns."""
        required = ["start_time", "end_time", "gcp_project_id", "status_pass_failed_retry", "latency_ms"]
        df = ensure_df_schema(transaction_df, required).copy()
        tz = LogTimeStamper.CONFIG_TIMEZONE
        df["start_time"] = pd.to_datetime(df["start_time"], errors="coerce", utc=True).dt.tz_convert(tz)
        df["end_time"] = pd.to_datetime(df["end_time"], errors="coerce", utc=True).dt.tz_convert(tz)
        df["latency_ms"] = pd.to_numeric(df["latency_ms"], errors="coerce")
        df["process_date"] = df["start_time"].dt.date
        agg = df.groupby(["process_date", "gcp_project_id"], as_index=False, dropna=False).agg(
            total_transaction=("status_pass_failed_retry", "count"),
            total_success_transaction=("status_pass_failed_retry", lambda s: int((s == "Pass").sum())),
            total_failed_transaction=("status_pass_failed_retry", lambda s: int((s == "Failed").sum())),
            average_response_time_sec=(
                "latency_ms",
                lambda s: round(s.mean() / 1000, 2) if pd.notna(s.mean()) else 0.0,
            ),
            min_start_time=("start_time", "min"),
            max_end_time=("end_time", "max"),
        )
        agg["total_runtime_sec"] = (
            (
                pd.to_datetime(agg["max_end_time"], errors="coerce")
                - pd.to_datetime(agg["min_start_time"], errors="coerce")
            )
            .dt.total_seconds()
            .round(2)
        )
        agg = agg.drop(columns=["min_start_time", "max_end_time"]).rename(columns={"gcp_project_id": "project_id"})
        agg["environment"] = self._environment_label()
        agg["project_type"] = _PROJECT_TYPE
        return agg[_AI_OPERATION_COLUMNS]

    # --- SharePoint write ------------------------------------------------------

    def _append_and_upload(self, df: pd.DataFrame, path: str, sort_keys: list[str], label: str) -> None:
        """Append *df* to the existing monthly CSV at *path* (fresh file if unreadable),
        stringify ``data_date``, sort newest-first, align columns, blank out NaN, upload, then
        sweep expired month-files."""
        if df.empty:
            logger.info(f"No {label} rows to export; skipping upload")
            return
        if not path:
            logger.warning(f"No {label} path configured; skipping upload")
            return
        combined = df
        try:
            if self.sharepoint.is_item_exists(item_path=path):
                content = self.sharepoint.get_item_by_path(item_path=path).content
                combined = pd.concat([pd.read_csv(io.BytesIO(content)), df], ignore_index=True)
        except Exception as exc:
            logger.warning(f"Could not read existing {label} at {path}; writing fresh: {exc}")
            combined = df
        if "data_date" in combined.columns:
            combined["data_date"] = combined["data_date"].astype(str)
        sort_cols = [key for key in sort_keys if key in combined.columns]
        if sort_cols:
            combined = combined.sort_values(by=sort_cols, ascending=False)
        # Retention, always on. Row-pruning this file only bites when the window is shorter than a
        # month (each month-file holds one month); the month-file sweep below is what bounds storage.
        combined = prune_by_timestamp(combined, self._cutoff, _RETENTION_COLUMN, label=label)
        combined = replace_nan_with_default(ensure_df_schema(combined, list(df.columns)), default_value="")
        csv_bytes = combined.to_csv(index=False, encoding=_CSV_ENCODING).encode(_CSV_ENCODING)
        self.sharepoint.upload_file(path, csv_bytes)
        logger.info(f"{label} ({len(df)} new / {len(combined)} total rows) saved to SharePoint: {path}")
        prefix = path.rsplit("/", 1)[-1].rsplit("_", 1)[0]  # transaction_log_202607.csv -> transaction_log
        sweep_month_files(self.sharepoint, path, month_file_pattern(prefix), self._cutoff, label)

    # --- Small helpers ---------------------------------------------------------

    @staticmethod
    def _usage_json(usage_metadata: Any) -> str:
        """Stable JSON string for a usage dict (group key); ``""`` for non-dicts."""
        return json.dumps(usage_metadata, sort_keys=True, default=str) if isinstance(usage_metadata, dict) else ""

    @staticmethod
    def _page_usage(usage_metadata: Any) -> dict:
        """Summarize one page's token usage; zeros for rejected pages (no metadata)."""
        summary = (
            GeminiBatchModule.sum_tokens_usage_for_billing(usage_metadata) if isinstance(usage_metadata, dict) else {}
        )
        return {
            "token_input": summary.get("token_input", {}) or {},
            "token_cached": summary.get("token_cached", 0) or 0,
            "token_output": summary.get("token_output", {}) or {},
        }

    def _compute_costs(self, usage_detail: dict, cost_config: list[dict]) -> dict:
        """Cost per page keyed by record index; ``{}`` (cost 0) on any failure."""
        if not usage_detail or not cost_config:
            return {}
        try:
            return GeminiBatchModule.cal_gemini_cost(usage_detail=usage_detail, cost_config=cost_config)
        except Exception as exc:
            logger.warning(f"Cost computation failed; defaulting cost to 0: {exc}")
            return {}

    @staticmethod
    def _environment_label() -> str:
        """Map the ``ENVIRONMENT`` env var to the AI-operation environment label."""
        env = os.environ.get("ENVIRONMENT", "").lower()
        if env == "prod":
            return "production"
        if env == "nprd":
            return "non-production"
        return env or "unknown"

    @staticmethod
    def _to_str(value: Any) -> str:
        """Render a timestamp as an ISO string; ``""`` for null/NaT."""
        if isinstance(value, str):
            return value
        if value is None or pd.isna(value):
            return ""
        return value.isoformat() if hasattr(value, "isoformat") else str(value)
