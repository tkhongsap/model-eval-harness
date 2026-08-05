"""ReportBuilder — constructs Excel workbooks and CSV log files.

Extracts ``process_excel()`` and the transaction/performance log loops
from ``main.py`` so they can be tested independently.
"""
from __future__ import annotations

import io
import re
from datetime import datetime

import pandas as pd

from app.share_log import get_logger

logger = get_logger(__name__)


class ReportBuilder:
    """Builds Excel and CSV reports for the pipeline output."""

    def __init__(
        self,
        output_schema_sheet1: list[str],
        output_schema_sheet2: list[str],
    ) -> None:
        self._schema1 = output_schema_sheet1
        self._schema2 = output_schema_sheet2

    # ------------------------------------------------------------------
    # Excel user report
    # ------------------------------------------------------------------

    def build_user_excel(
        self,
        incompliant_df: pd.DataFrame,
        suspicious_df: pd.DataFrame | None = None,
    ) -> io.BytesIO:
        """Return a BytesIO containing the formatted two-sheet Excel workbook."""
        buffer = io.BytesIO()
        sheets: list[tuple[str, pd.DataFrame, list[str]]] = [
            ("Incompliant Photo Retailer", incompliant_df, self._schema1),
        ]
        if suspicious_df is not None:
            sheets.append(("Suspicious Retailer (Active)", suspicious_df, self._schema2))

        with pd.ExcelWriter(buffer, engine="xlsxwriter") as writer:  # Remove datetime_format here
            for sheet_name, sheet_df, schema in sheets:
                sheet_df = sheet_df.copy()
                sheet_df.columns = schema

                date_columns = ["Last Photo Update", "Last visit update", "Created Code (D) Date", "Created Code (T) Date", "Date of Verified by PBH"]

                for col in date_columns:
                    if col in sheet_df.columns:
                        # Try dayfirst=True since your source data is dd/mm/yyyy
                        sheet_df[col] = pd.to_datetime(sheet_df[col], dayfirst=True, errors="coerce")
                        # Strip timezone if present
                        if hasattr(sheet_df[col].dt, 'tz') and sheet_df[col].dt.tz is not None:
                            sheet_df[col] = sheet_df[col].dt.tz_localize(None)

                sheet_df.to_excel(
                    writer,
                    sheet_name=sheet_name,
                    index=False,
                    startrow=1,
                    header=False,
                )

                workbook = writer.book
                worksheet = writer.sheets[sheet_name]

                header_fmt = workbook.add_format({
                    "bold": True,
                    "text_wrap": False,
                    "align": "center",
                    "valign": "vcenter",
                    "fg_color": "#2C3E50",
                    "font_color": "#FFFFFF",
                    "border": 1,
                })
                left_fmt = workbook.add_format({"align": "left", "valign": "vcenter"})
                date_fmt = workbook.add_format({
                    "num_format": "dd/mm/yyyy hh:mm:ss",
                    "align": "left",
                    "valign": "vcenter",
                })

                worksheet.write_row("A1", sheet_df.columns, header_fmt)

                for col_idx, col_name in enumerate(sheet_df.columns):
                    max_len = (
                        max(
                            sheet_df[col_name].astype(str).map(len).max(),
                            len(col_name),
                        )
                        + 1
                    )
                    fmt = date_fmt if col_name in date_columns else left_fmt
                    worksheet.set_column(col_idx, col_idx, max_len, fmt)
                
                date_col_indices = {col_name: idx for idx, col_name in enumerate(sheet_df.columns) if col_name in date_columns}

                # More efficient: iterate column by column instead of row by row
                for col_name, col_idx in date_col_indices.items():
                    for row_idx, val in enumerate(sheet_df[col_name]):
                        if pd.isna(val):
                            worksheet.write_blank(row_idx + 1, col_idx, None, date_fmt)
                        else:
                            worksheet.write_datetime(row_idx + 1, col_idx, val.to_pydatetime(), date_fmt)

        buffer.seek(0)
        return buffer

    # ------------------------------------------------------------------
    # Excel value protection
    # ------------------------------------------------------------------

    @staticmethod
    def protect_excel_value(value: object) -> object:
        """Prevent Excel from misinterpreting date strings and fractions.

        Matches the original ``protect_from_excel_date_parsing()`` in ``main.py``.
        """
        if isinstance(value, str):
            if re.match(r"^\d{4}-\d{2}-\d{2}$", value):
                return f"'{value}"
            if re.match(r"^\d{1,2}/\d{1,2}$", value):
                return f"'{value}"
        return value

    # ------------------------------------------------------------------
    # CSV transaction / performance logs
    # ------------------------------------------------------------------

    def build_transaction_row(
        self,
        log: dict,
        data_date: str,
        project_id: str,
        project_name: str,
    ) -> dict:
        """Convert a ``ShopResult.to_log_dict()`` entry to a transaction log row."""
        meta: dict = log.get("meta_data", {})
        text_in = meta.get("text_input_tokens", 0)
        image_in = meta.get("image_input_tokens", 0)
        text_cache = meta.get("text_cache_tokens", 0)
        image_cache = meta.get("image_cache_tokens", 0)
        output_tok = meta.get("output_tokens", 0)

        # Costs per million tokens (matching original main.py constants)
        cost = (
            (text_in * 0.30 + image_in * 0.30 + text_cache * 0.03 + image_cache * 0.03) / 1_000_000
            + output_tok * 2.50 / 1_000_000
        )

        return {
            "data_date": data_date,
            "start_time": log.get("start_time", ""),
            "end_time": log.get("end_time", ""),
            "total_time_mins": round(log.get("process_time", 0) / 60, 4),
            "type": "fraud_validation",
            "gcp_project_id": project_id,
            "gcp_project_name": project_name,
            "user_id": "",
            "source": "S3",
            "storage_path": "",
            "folder": log.get("image_parts", [""])[0] if log.get("image_parts") else "",
            "filename": str(log.get("image_parts", [])),
            "file_metadata_min": "",
            "status_pass_failed_retry": log.get("status", ""),
            "error_log_if": log.get("message", "") if log.get("status") != "success" else "",
            "latency_ms": round(log.get("process_time", 0) * 1000, 2),
            "token_usage_input": text_in + image_in,
            "token_usage_output": output_tok,
            "total_cost_usd": round(cost, 6),
        }

    def build_performance_row(
        self,
        transaction_df: pd.DataFrame,
        data_date: str,
        project_id: str,
        project_name: str,
    ) -> dict:
        """Aggregate transaction rows into a single performance summary row."""
        return {
            "data_date": data_date,
            "run_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "gcp_project_id": project_id,
            "gcp_project_name": project_name,
            "total_shops": len(transaction_df),
            "success_count": int((transaction_df["status_pass_failed_retry"] == "success").sum()),
            "fail_count": int((transaction_df["status_pass_failed_retry"] == "fail").sum()),
            "error_count": int((transaction_df["status_pass_failed_retry"] == "error").sum()),
            "total_token_input": int(transaction_df["token_usage_input"].sum()),
            "total_token_output": int(transaction_df["token_usage_output"].sum()),
            "total_cost_usd": round(float(transaction_df["total_cost_usd"].sum()), 6),
            "avg_latency_ms": round(float(transaction_df["latency_ms"].mean()), 2),
        }
