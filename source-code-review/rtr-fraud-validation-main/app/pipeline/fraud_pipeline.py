"""FraudValidationPipeline — top-level async orchestrator.

Replaces the 700-line ``async def main()`` in ``app/main.py``.

Each private method is a named pipeline stage so they can be understood,
tested, and modified independently.  All external I/O is injected via the
constructor (dependency injection pattern).
"""
from __future__ import annotations

import asyncio
import base64
from datetime import datetime
import io
import math

import chardet
from openai.types import Batch
import pandas as pd
import polars as pl
import requests

from app.core.models import PipelineConfig, ShopRecord, ShopResult
from app.processors.email_composer import EmailComposer
from app.processors.report_builder import ReportBuilder
from app.processors.shop_processor import ShopProcessor
from app.services.email_service import EmailService
from app.services.gcs_service import GCSService
from app.services.s3_service import S3Service
from app.services.secret_service import SecretService
from app.services.sharepoint_service import SharePointService
from app.share_log import get_logger
from app.utils.common import get_value_by_path, load_yaml_string, read_file, resolve_env

logger = get_logger(__name__)

# Number of appended schema columns (must match config/app/rtr_main.yaml)
_APPENDED_SCHEMA_LEN = 22


class FraudValidationPipeline:
    """Async fraud-validation pipeline, wired together via DI."""

    def __init__(
        self,
        config: PipelineConfig,
        fraud_sp: SharePointService,
        control_sp: SharePointService,
        s3: S3Service,
        gcs: GCSService,
        shop_processor: ShopProcessor,
        report_builder: ReportBuilder,
        email_composer: EmailComposer,
        email_service: EmailService,
    ) -> None:
        self._cfg = config
        self._fraud_sp = fraud_sp
        self._control_sp = control_sp
        self._s3 = s3
        self._gcs = gcs
        self._shop_proc = shop_processor
        self._report = report_builder
        self._composer = email_composer
        self._email = email_service

        # Runtime state
        self._input_gcs_path: str = ""
        self._lookup_gcs_path: str = ""
        self._user_file_name: str = ""
        self._user_gcs_path: str = ""
        self._logs: list[dict] = []

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Execute the complete pipeline end-to-end."""
        try:
            _df, join_df, user_excel_bytes = await self._run_pipeline()
            await self._notify(join_df, user_excel_bytes)
        except Exception as exc:
            logger.error({"status": "Error", "message": str(exc)})
            await self._notify_error(exc)
            raise
        finally:
            self._cleanup()

    # ------------------------------------------------------------------
    # Pipeline stages
    # ------------------------------------------------------------------

    async def _run_pipeline(self) -> tuple[pl.DataFrame, pl.DataFrame, bytes]:
        """Stages 1-4: ingest → process → build report → publish."""
        resolve_config = load_yaml_string(resolve_env(read_file("config/app/rtr_main.yaml")))
        output_append_headers: list[str] = get_value_by_path(
            resolve_config, "Input.s3.appended_schema"
        )
        initial_headers = get_value_by_path(resolve_config, "Input.s3.initial_schema")
        assert len(output_append_headers) == _APPENDED_SCHEMA_LEN, (
            f"appended_schema has {len(output_append_headers)} columns, expected {_APPENDED_SCHEMA_LEN}"
        )

        # Stage 1: Ingest
        df, prompt = await self._ingest(resolve_config, output_append_headers)

        # Stage 2: Process shops
        df = await self._process_shops(df, output_append_headers, prompt)

        # Stage 3: Build reports
        join_df = self._build_join_df(initial_headers)
        join_df_csv = join_df.write_csv().encode("utf-8-sig")
        system_log_path = (
            get_value_by_path(resolve_config, "Output.sharepoint.output_mapping_file_path")
            .replace("yyyymmdd", self._cfg.today.strftime("%Y%m%d"))
            .replace("yyyymm", self._cfg.today.strftime("%Y%m"))
        )
        full_system_log_path = f"{self._cfg.fraud_site_base_root}/{system_log_path}"
        self._fraud_sp.upload_file(full_system_log_path, join_df_csv)
        logger.info("System log uploaded to SharePoint")

        user_excel_bytes = self._build_user_excel(join_df, resolve_config)

        # Stage 4: Publish
        await self._publish(df, user_excel_bytes, resolve_config)

        return df, join_df, user_excel_bytes

    # --- Stage 1: Ingest ---

    async def _ingest(
        self, resolve_config: dict, output_append_headers: list[str]
    ) -> tuple[pl.DataFrame, str]:
        """Download input & lookup from SharePoint to GCS, read input CSV."""
        today = self._cfg.today
        input_file_path = (
            get_value_by_path(resolve_config, "Input.sharepoint.input_file_path")
            .replace("yyyymmdd", today.strftime("%Y%m%d"))
            .replace("yyyymm", today.strftime("%Y%m"))
        )
        input_enc_file_path = (
            get_value_by_path(resolve_config, "Input.sharepoint.input_enc_file_path")
            .replace("yyyymmdd", today.strftime("%Y%m%d"))
            .replace("yyyymm", today.strftime("%Y%m"))
        )
        s3_input_file_path = (
            get_value_by_path(resolve_config, "Input.s3.input_file_path")
            .replace("yyyymmdd", today.strftime("%Y%m%d"))
        )

        # put enc from s3 to sharepoint
        content_byte = self._s3.read_bytes(s3_input_file_path)
        full_input_path = f"{self._cfg.fraud_site_base_root}/{input_enc_file_path}"
        self._fraud_sp.upload_file(full_input_path, content_byte)

        # decrypt file from sharepoint and put at same place
        csv_buffer = self._fraud_sp.decrypt(
            initial_headers=get_value_by_path(resolve_config, "Input.s3.initial_schema"),
            file_folder_path=f"{self._cfg.input_folder}/{'/'.join(input_enc_file_path.split('/')[-3:-1])}", 
            file_name=input_enc_file_path.split("/")[-1],
            rsa_private_key=self._cfg.rsa_private_key
        )
        self._fraud_sp.upload_file(f"{self._cfg.fraud_site_base_root}/{input_file_path}", csv_buffer.getvalue())
        
        lookup_file_path = (
            get_value_by_path(resolve_config, "Input.sharepoint.lookup_file_path")
            .replace("yyyymmdd", today.strftime("%Y%m%d"))
            .replace("yyyymm", today.strftime("%Y%m"))
        )
        input_folder_path = f"{self._cfg.input_folder}/{'/'.join(input_file_path.split('/')[-3:-1])}"
        lookup_folder_path = f"{self._cfg.input_folder}/{'/'.join(lookup_file_path.split('/')[-2:-1])}"
        input_file_name = input_file_path.split("/")[-1]
        lookup_file_name = lookup_file_path.split("/")[-1]
    
        # Download from SharePoint → upload to GCS (idempotent)
        input_bytes = self._fraud_sp.download_with_backup(
            input_folder_path, input_file_name,
            f"{self._cfg.backup_folder}/{today.strftime('%Y%m')}"
        )
        lookup_bytes = self._fraud_sp.download_with_backup(
            lookup_folder_path, lookup_file_name,
            f"{self._cfg.backup_folder}/{today.strftime('%Y%m')}"
        )

        self._input_gcs_path = f"{self._cfg.input_folder}/{input_file_name}"
        self._lookup_gcs_path = f"{self._cfg.input_folder}/{lookup_file_name}"

        self._gcs.copy_from_sharepoint_if_missing(self._input_gcs_path, input_bytes)
        self._gcs.copy_from_sharepoint_if_missing(self._lookup_gcs_path, lookup_bytes)

        # Read + decode input CSV
        content_bytes = self._gcs.read_bytes(self._input_gcs_path)
        result = chardet.detect(content_bytes)
        encoding = result.get("encoding") or "utf-8-sig"
        content = content_bytes.decode(encoding)

        df = pl.read_csv(io.StringIO(content), ignore_errors=True)
        df = df.rename({c: c.lstrip("\ufeff").strip() for c in df.columns})
        df = df.with_row_index("original_row_id")

        # Ensure all output columns exist
        for col in output_append_headers:
            if col not in df.columns:
                df = df.with_columns(pl.lit("").alias(col))
        if "status" not in df.columns:
            df = df.with_columns(pl.lit("").alias("status"))

        for col in ["xd_rtr_code", "xd_rtr_name", "xt_rtr_code", "xt_rtr_name",
                    "photo_1_path", "photo_2_path", "photo_3_path"]:
            if col in df.columns:
                df = df.with_columns(pl.col(col).fill_null("").cast(pl.Utf8))

        logger.info(f"Input file loaded: {df.height} rows")

        # Download prompt from Control SharePoint
        prompt_path = f"{self._cfg.control_site_base_root}/{self._cfg.control_site_prompts_root}/prompt.txt"
        prompt_url = self._control_sp.get_download_url(prompt_path)
        if not prompt_url:
            raise RuntimeError(f"Prompt file not found: {prompt_path}")
        prompt_resp = requests.get(prompt_url)
        prompt_resp.raise_for_status()
        prompt_text = prompt_resp.text

        return df, prompt_text

    # --- Stage 2: Process shops ---

    async def _process_shops(
        self,
        df: pl.DataFrame,
        output_append_headers: list[str],
        prompt: str,
    ) -> pl.DataFrame:
        """Run AI processing on all unprocessed rows, batching and checkpointing."""
        df_to_process = (
            df
            .filter(pl.col("status").is_null() | ~pl.col("status").is_in(["success", "fail"]))
        )

        total_rows = df_to_process.height
        if total_rows == 0:
            logger.info("No rows to process.")
            return df

        logger.info(f"Processing {total_rows} rows in batches of {self._cfg.batch_size}")

        all_records = [
            ShopRecord.from_row(row) for row in df_to_process.iter_rows(named=True)
        ]

        sem = asyncio.Semaphore(self._cfg.batch_size)

        async def process_one(record: ShopRecord) -> ShopResult:
            async with sem:
                return await self._shop_proc.process(record, prompt)

        # Process in explicit batches to checkpoint to GCS after each batch
        for i in range(0, total_rows, self._cfg.batch_size):
            batch = all_records[i: i + self._cfg.batch_size]
            logger.info(f"Batch {i // self._cfg.batch_size + 1}: submitting {len(batch)} shops")
            results: list[ShopResult] = list(
                await asyncio.gather(*[process_one(r) for r in batch])
            )

            # Update DataFrame with results
            for result in results:
                output_list = result.to_output_list()
                row_id = result.original_row_id
                updates = [
                    pl.when(pl.col("original_row_id") == row_id)
                    .then(pl.lit(self._report.protect_excel_value(str(output_list[i]))))
                    .otherwise(pl.col(col_name))
                    .alias(col_name)
                    for i, col_name in enumerate(output_append_headers)
                ] + [
                    pl.when(pl.col("original_row_id") == row_id)
                    .then(pl.lit(result.status))
                    .otherwise(pl.col("status"))
                    .alias("status")
                ]
                df = df.with_columns(updates)

                log = result.to_log_dict()
                log["process_row_id"] = result.rtr_code
                self._logs.append(log)

                if result.status in ("success", "fail"):
                    logger.info(log)
                else:
                    logger.warning(log)

            # Checkpoint to GCS
            self._gcs.invalidate_cache(self._input_gcs_path)
            csv_text = df.drop("original_row_id").write_csv()
            self._gcs.write_text(self._input_gcs_path, csv_text)
            logger.info(f"Batch {i // self._cfg.batch_size + 1} checkpointed to GCS")

        return df

    # --- Stage 3: Build reports ---

    def _build_join_df(
        self, initial_headers:list
    ) -> pl.DataFrame:
        """Join processed data with lookup file to produce the user-facing DataFrame."""
        today = self._cfg.today

        # Read lookup from GCS
        lookup_bytes = self._gcs.read_bytes(self._lookup_gcs_path)
        lookup_file_name = self._lookup_gcs_path.split("/")[-1]

        if lookup_file_name.endswith((".xlsx", ".xls")):
            sheets = pd.read_excel(io.BytesIO(lookup_bytes), sheet_name=None, header=0, engine="openpyxl")
            combined_pd = pd.concat(
                [v for k, v in sheets.items() if "Detail".lower() in k.lower()],
                ignore_index=True,
            )
            for col in combined_pd.columns:
                if combined_pd[col].dtype == object:
                    combined_pd[col] = combined_pd[col].astype(str)
            combined_df = pl.from_pandas(combined_pd)
        else:
            combined_df = pl.read_csv(io.BytesIO(lookup_bytes), ignore_errors=True)

        combined_df = combined_df.rename(
            {c: c.lower().replace(" ", "_") for c in combined_df.columns}
        )
        partner_status_col = next(
            (c for c in combined_df.columns if "partner_status" in c), None
        )
        if partner_status_col:
            combined_df = combined_df.filter(pl.col(partner_status_col) == "Active")

        partner_codes: set[str] = set(
            combined_df.get_column("partner_code").cast(pl.Utf8).to_list()
        ) if "partner_code" in combined_df.columns else set()

        # Row from original_df read from GCS (post-processing)
        self._gcs.invalidate_cache(self._input_gcs_path)
        content = self._gcs.read_text(self._input_gcs_path)
        original_df = pl.read_csv(io.StringIO(content), ignore_errors=True)

        xd_invalid = pl.col("xd_rtr_code").is_null() | (pl.col("xd_rtr_code").cast(pl.Utf8).str.len_chars() == 0)
        xt_invalid = pl.col("xt_rtr_code").is_null() | (pl.col("xt_rtr_code").cast(pl.Utf8).str.len_chars() == 0)
        # either_invalid = xd_invalid | xt_invalid

        suspicious_expr = (
            pl.when(
                pl.col("xd_rtr_code").cast(pl.Utf8).is_in(partner_codes)
                | pl.col("xt_rtr_code").cast(pl.Utf8).is_in(partner_codes)
            )
            .then(pl.lit("Yes"))
            .otherwise(pl.lit(""))
            .alias("Suspicious")
        )

        if today.strftime("%Y%m%d")[-2:] == "01":
            logger.info("Day 01: using GA from original file, no_visit=0")
            join_df = original_df.select(
                [
                    pl.col("Run_Date"),
                    pl.col("xd_rtr_code"), 
                    pl.col("xd_rtr_name"),
                    pl.col("xt_rtr_code"), 
                    pl.col("xt_rtr_name"),
                    pl.col("Complaint_Status"),
                    pl.coalesce([pl.col("xd_zone_name"), pl.col("xt_zone_name")]).alias("zone_name"),
                    pl.coalesce([pl.col("xd_cluster_name"), pl.col("xt_cluster_name")]).alias("cluster_name"),
                    pl.when(xd_invalid).then(pl.lit("")).otherwise(pl.col("xd_pbh_sale_name")).alias("xd_pbh_sale_name"),
                    pl.when(xd_invalid).then(pl.lit("")).otherwise(pl.col("xd_rsr_sale_name")).alias("xd_rsr_sale_name"),
                    pl.when(xt_invalid).then(pl.lit("")).otherwise(pl.col("xt_pbh_sale_name")).alias("xt_pbh_sale_name"),
                    pl.when(xt_invalid).then(pl.lit("")).otherwise(pl.col("xt_rsr_sale_name")).alias("xt_rsr_sale_name"),
                    pl.when(xd_invalid).then(pl.lit("")).otherwise(pl.col("xd_ga")).alias("xd_ga"),
                    pl.when(xt_invalid).then(pl.lit("")).otherwise(pl.col("xt_ga")).alias("xt_ga"),
                    pl.col("last_photo_update"), # convert date
                    pl.col("no_visit_last_month"),
                    pl.lit(0).alias("no_visit"),
                    pl.col("last_visit_date"), # convert date
                    pl.when(xd_invalid).then(pl.lit("")).otherwise(pl.col("xd_appointment_date")).alias("xd_appointment_date"), # convert date
                    pl.when(xt_invalid).then(pl.lit("")).otherwise(pl.col("xt_appointment_date")).alias("xt_appointment_date"), # convert date
                    pl.col("Photo1_Flag300"),
                    suspicious_expr,
                    pl.coalesce([pl.col("xd_last_verified_by"), pl.col("xt_last_verified_by")]).alias("verified_by_pbh"), 
                    pl.coalesce([pl.col("xd_last_verified_date"), pl.col("xt_last_verified_date")]).alias("date_of_verified_by_pbh"), # convert date
                ]
            )
        else:
            logger.info("Non-01 day: using GA from yyyymm01 S3 file")
            join_df = self._join_with_month_start_file(original_df, suspicious_expr, xd_invalid, xt_invalid, initial_headers)

        def is_blank_filter(col_name):
            return (
                pl.col(col_name).is_null() 
                | (pl.col(col_name).cast(pl.Utf8).str.strip_chars().str.len_chars() == 0)
                | (pl.col(col_name).cast(pl.Utf8).str.replace_all(r"\s+", "") == "")
            )

        join_df = join_df.filter(
            ~(
                is_blank_filter("xd_rtr_code")
                & (
                    pl.col("xt_rtr_code").cast(pl.Utf8).str.starts_with("12")
                    | pl.col("xt_rtr_code").cast(pl.Utf8).str.starts_with("13")
                )
                & (
                    pl.col("xt_ga").is_null()
                    | (pl.col("xt_ga").cast(pl.Utf8).str.replace_all(r"\s+", "") == "")
                    | (pl.col("xt_ga").cast(pl.Utf8).str.strip_chars() == "0")
                )
            )
        )

        return join_df

    def _join_with_month_start_file(
        self,
        original_df: pl.DataFrame,
        suspicious_expr: pl.Expr,
        xd_invalid: pl.Expr,
        xt_invalid: pl.Expr,
        initial_headers:list,
    ) -> pl.DataFrame:
        """Load the yyyymm01 S3 file and join for GA / no_visit_last_month values."""
        today = self._cfg.today
        resolve_config = load_yaml_string(resolve_env(read_file("config/app/rtr_main.yaml")))
        s3_path = (
            get_value_by_path(resolve_config, "Input.s3.input_file_path")
            .replace("yyyymmdd", today.strftime("%Y%m01"))
        )

        try:
            # temporary use sharepoint file (manual)
            # input_file_path = (
            #     get_value_by_path(resolve_config, "Input.sharepoint.input_file_path")
            #     .replace("yyyymmdd", today.strftime("%Y%m01"))
            #     .replace("yyyymm", today.strftime("%Y%m"))
            # )
            # input_folder_path = f"{self._cfg.input_folder}/{'/'.join(input_file_path.split('/')[-3:-1])}"
            # input_file_name = input_file_path.split("/")[-1]
            # content_bytes = self._fraud_sp.download_with_backup(
            #     input_folder_path, input_file_name,
            #     f"{self._cfg.backup_folder}/{today.strftime('%Y%m')}"
            # )
            # df_01 = pl.read_csv(
            #     io.StringIO(content_bytes.decode("utf-8")),
            #     separator=",",
            #     has_header=True,
            #     new_columns=initial_headers,
            #     ignore_errors=True,
            #     truncate_ragged_lines=True,
            #     infer_schema_length=1000,
            # )
            
            # # use S3 file
            key = self._s3.normalise_key(f"s3://{self._cfg.s3_bucket}/{s3_path}")
            content_bytes = self._s3.read_bytes_encrypt(key, self._cfg.rsa_private_key)
            
            df_01 = pl.read_csv(
                io.StringIO(content_bytes.decode("utf-8")),
                separator="|",
                has_header=True,
                new_columns=initial_headers,
                ignore_errors=True,
                truncate_ragged_lines=True,
                infer_schema_length=1000,
            )
        except Exception as exc:
            logger.warning(f"Could not load yyyymm01 S3 file: {exc}; falling back to original")
            df_01 = original_df

        df_01_cols = df_01.columns

        # Cast join keys to Utf8 in df_01 to avoid schema mismatch
        cast_cols = [c for c in ["xd_rtr_code", "xt_rtr_code"] if c in df_01_cols]
        if cast_cols:
            df_01 = df_01.with_columns([pl.col(c).cast(pl.Utf8) for c in cast_cols])

        # Cast join keys in original_df
        original_cast_cols = [c for c in ["xd_rtr_code", "xt_rtr_code"] if c in original_df.columns]
        if original_cast_cols:
            original_df = original_df.with_columns([pl.col(c).cast(pl.Utf8) for c in original_cast_cols])
        joined = original_df

        xd_sel = [pl.col("xd_rtr_code")]
        xd_sel.append(pl.col("xd_ga").alias("xd_ga_01") if "xd_ga" in df_01_cols else pl.lit("").alias("xd_ga_01"))
        xd_sel.append(pl.col("no_visit").alias("no_visit_last_month_xd_01") if "no_visit" in df_01_cols else pl.lit("").alias("no_visit_last_month_xd_01"))
        df_01_xd = df_01.select(xd_sel).unique("xd_rtr_code") if "xd_rtr_code" in df_01_cols else None

        xt_sel = [pl.col("xt_rtr_code")]
        xt_sel.append(pl.col("xt_ga").alias("xt_ga_01") if "xt_ga" in df_01_cols else pl.lit("").alias("xt_ga_01"))
        xt_sel.append(pl.col("no_visit").alias("no_visit_last_month_xt_01") if "no_visit" in df_01_cols else pl.lit("").alias("no_visit_last_month_xt_01"))
        df_01_xt = df_01.select(xt_sel).unique("xt_rtr_code") if "xt_rtr_code" in df_01_cols else None

        if df_01_xd is not None:
            joined = joined.join(df_01_xd, on="xd_rtr_code", how="left")
        else:
            joined = joined.with_columns([pl.lit("").alias("xd_ga_01"), pl.lit("").alias("no_visit_last_month_xd_01")])
        if df_01_xt is not None:
            joined = joined.join(df_01_xt, on="xt_rtr_code", how="left")
        else:
            joined = joined.with_columns([pl.lit("").alias("xt_ga_01"), pl.lit("").alias("no_visit_last_month_xt_01")])

        return joined.select(
            [                
                pl.col("Run_Date"),
                pl.col("xd_rtr_code"), 
                pl.col("xd_rtr_name"),
                pl.col("xt_rtr_code"), 
                pl.col("xt_rtr_name"),
                pl.col("Complaint_Status"),
                pl.coalesce([pl.col("xd_zone_name"), pl.col("xt_zone_name")]).alias("zone_name"),
                pl.coalesce([pl.col("xd_cluster_name"), pl.col("xt_cluster_name")]).alias("cluster_name"),
                pl.when(xd_invalid).then(pl.lit("")).otherwise(pl.col("xd_pbh_sale_name")).alias("xd_pbh_sale_name"),
                pl.when(xd_invalid).then(pl.lit("")).otherwise(pl.col("xd_rsr_sale_name")).alias("xd_rsr_sale_name"),
                pl.when(xt_invalid).then(pl.lit("")).otherwise(pl.col("xt_pbh_sale_name")).alias("xt_pbh_sale_name"),
                pl.when(xt_invalid).then(pl.lit("")).otherwise(pl.col("xt_rsr_sale_name")).alias("xt_rsr_sale_name"),
                pl.when(xd_invalid).then(pl.lit("")).otherwise(pl.col("xd_ga_01").fill_null("")).alias("xd_ga"),
                pl.when(xt_invalid).then(pl.lit("")).otherwise(pl.col("xt_ga_01").fill_null("")).alias("xt_ga"),
                pl.col("last_photo_update"),
                # pl.coalesce([pl.col("no_visit_last_month_xd_01"), pl.col("no_visit_last_month_xt_01")]).fill_null("").alias("no_visit_last_month"),
                pl.col("no_visit_last_month"),
                pl.col("no_visit"),
                pl.col("last_visit_date"),
                pl.when(xd_invalid).then(pl.lit("")).otherwise(pl.col("xd_appointment_date")).alias("xd_appointment_date"),
                pl.when(xt_invalid).then(pl.lit("")).otherwise(pl.col("xt_appointment_date")).alias("xt_appointment_date"),
                pl.col("Photo1_Flag300"),
                suspicious_expr,
                pl.coalesce([pl.col("xd_last_verified_by"), pl.col("xt_last_verified_by")]).alias("verified_by_pbh"),
                pl.coalesce([pl.col("xd_last_verified_date"), pl.col("xt_last_verified_date")]).alias("date_of_verified_by_pbh"), 
            ]
        )

    def is_blank(self, col) -> pl.Expr:
        expr = col if isinstance(col, pl.Expr) else pl.col(col)
        return (
            expr.is_null() |
            (expr.cast(pl.Utf8).str.strip_chars() == "")
        )

    def _build_user_excel(
        self, join_df: pl.DataFrame, resolve_config: dict
    ) -> bytes:
        """Generate the user-facing Excel workbook."""
        schema1: list[str] = get_value_by_path(resolve_config, "Output.sharepoint.output_user_schema_sheet1")
        schema2: list[str] = get_value_by_path(resolve_config, "Output.sharepoint.output_user_schema_sheet2")
        join_df = join_df.select([                
            pl.col("Run_Date"),
            pl.col("xd_rtr_code"), 
            pl.col("xd_rtr_name"),
            pl.col("xt_rtr_code"), 
            pl.col("xt_rtr_name"),
            pl.col("Complaint_Status"),
            pl.col("zone_name"),
            pl.col("cluster_name"),
            pl.col("xd_pbh_sale_name"),
            pl.col("xd_rsr_sale_name"),
            pl.col("xt_pbh_sale_name"),
            pl.col("xt_rsr_sale_name"),
            pl.col("xd_ga"),
            pl.col("xt_ga"),
            pl.col("last_photo_update"),
            pl.col("no_visit_last_month"),
            pl.col("no_visit"),
            pl.col("last_visit_date"),
            pl.col("xd_appointment_date"),
            pl.col("xt_appointment_date"),
            pl.col("Photo1_Flag300"),
            pl.col("Suspicious"),
            pl.col("verified_by_pbh"),
            pl.col("date_of_verified_by_pbh"), 
        ]).with_columns([
            pl.when(self.is_blank("xd_rtr_code") & self.is_blank(pl.col("xd_ga"))) # both blank
            .then(pl.lit(""))
            .otherwise(
                pl.when(~self.is_blank("xd_rtr_code") & self.is_blank(pl.col("xd_ga"))) # xd_rtr_code not blank but xd_ga blank
                .then(pl.lit("0"))
                .otherwise(pl.col("xd_ga"))
                )
            .alias("xd_ga"),

            pl.when(self.is_blank("xt_rtr_code") & self.is_blank(pl.col("xt_ga"))) # both blank
            .then(pl.lit(""))
            .otherwise(
                pl.when(~self.is_blank("xt_rtr_code") & self.is_blank(pl.col("xt_ga"))) # xt_rtr_code not blank but xt_ga blank
                .then(pl.lit("0"))
                .otherwise(pl.col("xt_ga"))
                )
            .alias("xt_ga"),

            pl.col("no_visit").cast(pl.Utf8).fill_null("0").alias("no_visit"),
            pl.col("no_visit_last_month").cast(pl.Utf8).fill_null("0").alias("no_visit_last_month")
        ])

        incompliant_statuses = ["inComplaint", "inComplaint-No Photo", "incompliant-Less than 3 Photos"]
        incompliant_df = join_df.filter(pl.col("Complaint_Status").is_in(incompliant_statuses)).to_pandas()
        suspicious_df = (
            join_df.filter(
                (pl.col("Suspicious") == "Yes") & (pl.col("Complaint_Status") != "")
            )
            .select(
                ["xd_rtr_code", "xd_rtr_name", "xt_rtr_code", "xt_rtr_name",
                 "Complaint_Status", "zone_name", "cluster_name",
                 "xd_pbh_sale_name", "xd_rsr_sale_name", "xt_pbh_sale_name", "xt_rsr_sale_name",
                 "Suspicious", "verified_by_pbh", "date_of_verified_by_pbh"]
            )
            .to_pandas()
        )

        builder = ReportBuilder(schema1, schema2)
        buf = builder.build_user_excel(incompliant_df, suspicious_df)
        return buf.getvalue()

    # --- Stage 4: Publish ---

    async def _publish(
        self,
        df: pl.DataFrame,
        user_excel_bytes: bytes,
        resolve_config: dict,
    ) -> None:
        """Upload all output files to SharePoint and update transaction/perf logs."""
        today = self._cfg.today

        # 1. Input CSV → SharePoint fraud site
        input_csv = df.drop("original_row_id").write_csv().encode("utf-8-sig")
        input_sp_path = (
            get_value_by_path(resolve_config, "Input.sharepoint.input_file_path")
            .replace("yyyymmdd", today.strftime("%Y%m%d"))
            .replace("yyyymm", today.strftime("%Y%m"))
        )
        full_input_path = f"{self._cfg.fraud_site_base_root}/{input_sp_path}"
        self._fraud_sp.upload_file(full_input_path, input_csv)
        logger.info("Input CSV uploaded to SharePoint")

        # 2. System log (same CSV) → SharePoint fraud site
        system_log_path = (
            get_value_by_path(resolve_config, "Output.sharepoint.output_system_log_file_path")
            .replace("yyyymmdd", today.strftime("%Y%m%d"))
            .replace("yyyymm", today.strftime("%Y%m"))
        )
        full_system_log_path = f"{self._cfg.fraud_site_base_root}/{system_log_path}"
        self._fraud_sp.upload_file(full_system_log_path, input_csv)
        logger.info("System log uploaded to SharePoint")

        # 3. User Excel → SharePoint fraud site + GCS
        self._user_file_name = f"Retailers with non-compliant photos report_{today.strftime('%d%b%y').upper()}.xlsx"
        self._user_gcs_path = f"output/{self._user_file_name}"
        self._gcs.write_bytes(self._user_gcs_path, user_excel_bytes)

        user_sp_path = (
            get_value_by_path(resolve_config, "Output.sharepoint.output_user_file_path")
            .replace("yyyymmdd", today.strftime("%Y%m%d"))
            .replace("yyyymm", today.strftime("%Y%m"))
            .replace("DDMMMYY", today.strftime("%d%b%y").upper())
        )
        full_user_path = f"{self._cfg.fraud_site_base_root}/{user_sp_path}"
        self._fraud_sp.upload_file(full_user_path, user_excel_bytes)
        logger.info("User Excel uploaded to SharePoint")

        # 4. Transaction log → SharePoint control site
        await self._upload_transaction_log(resolve_config)

        # 5. Performance log → SharePoint control site
        await self._upload_performance_log()

    async def _upload_transaction_log(self, resolve_config: dict) -> None:
        today = self._cfg.today
        log_columns: list[str] = get_value_by_path(resolve_config, "Output.sharepoint.log_schema")
        builder = ReportBuilder([], [])

        rows = [builder.build_transaction_row(log, today.strftime("%Y%m%d"), self._cfg.project_id, self._cfg.project_name) for log in self._logs]
        new_df = pd.DataFrame(rows)
        if not new_df.empty:
            new_df = new_df[[c for c in log_columns if c in new_df.columns]]

        tx_path = f"{self._cfg.control_site_base_root}/{self._cfg.control_site_transaction_log_path}/{today.strftime('%Y')}/transaction_log_{today.strftime('%Y%m')}.csv"

        existing_content = None
        try:
            dl_url = self._control_sp.get_download_url(tx_path)
            if dl_url:
                existing_content = requests.get(dl_url).content
        except Exception:
            pass

        if existing_content:
            existing_df = pd.read_csv(io.BytesIO(existing_content))
            final_df = pd.concat([existing_df, new_df], ignore_index=True)
        else:
            final_df = new_df

        self._normalise_transaction_df(final_df)
        csv_buf = io.BytesIO()
        final_df.to_csv(csv_buf, index=False, encoding="utf-8-sig")
        csv_buf.seek(0)
        self._control_sp.upload_file(tx_path, csv_buf.getvalue())
        logger.info("Transaction log uploaded to SharePoint")

        # log to splunk
        total = len(final_df)
        completed = int((final_df["status_pass_failed_retry"] == "success").sum())
        failed = int((final_df["status_pass_failed_retry"] == "fail").sum())
        valid_starts = pd.to_datetime(final_df["start_time"].dropna())
        valid_ends = pd.to_datetime(final_df["end_time"].dropna())
        seconds = 0.0
        if not valid_starts.empty and not valid_ends.empty:
            delta = (valid_ends.max() - valid_starts.min()).total_seconds()
            if not (math.isnan(delta) or math.isinf(delta)):
                seconds = delta
        minutes = f"{int(seconds // 60)}.{int(seconds % 60):02d}"
        logger.info(
            {
                "process_date": datetime.now().strftime("%Y-%m-%d"),
                "environment":"NonProduction" if "nprd" in self._cfg.project_id else "Production",
                "project_id": self._cfg.project_id,
                "project_type":"Batch",
                "total_transaction": total,
                "total_success_transaction": completed,
                "total_failed_transaction": failed,
                "average_response_time_sec": round(seconds / total, 2) if total > 0 else 0,
                "total_runtime_sec": seconds
            }
        )

    @staticmethod
    def _normalise_transaction_df(df: pd.DataFrame) -> None:
        """Coerce timestamp columns to Bangkok timezone strings in-place."""
        for col in ("start_time", "end_time"):
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors="coerce", utc=True, format="mixed")
                df[col] = df[col].dt.tz_convert("Asia/Bangkok")
                df[col] = df[col].dt.strftime("%Y-%m-%d %H:%M:%S.%f%z")
                df[col] = df[col].str.replace(r"(\d{2})(\d{2})$", r"\1:\2", regex=True)
        if "data_date" in df.columns:
            df["data_date"] = df["data_date"].astype(str)
        if "data_date" in df.columns:
            df.sort_values("data_date", inplace=True)

    async def _upload_performance_log(self) -> None:
        """Rebuild performance logs from all transaction log files."""
        tx_base = f"{self._cfg.control_site_base_root}/{self._cfg.control_site_transaction_log_path}"
        perf_base = f"{self._cfg.control_site_base_root}/{self._cfg.control_site_performance_log_path}"

        year_folders = self._control_sp.list_folder_names(tx_base)
        for folder in year_folders:
            files = self._control_sp.list_files(f"{tx_base}/{folder}")
            for file_meta in files:
                try:
                    content = self._control_sp.download_file_by_id(file_meta["id"])
                    yyyymm = file_meta["name"].split("_")[-1].replace(".csv", "")
                    tx_df = pd.read_csv(io.BytesIO(content), encoding="utf-8-sig", low_memory=False)  # fix DtypeWarning
                    performance_rows = []

                    for date in tx_df["data_date"].unique():
                        date_df = tx_df[
                            (tx_df["data_date"] == date)
                            & (
                                ~(
                                    (tx_df["error_log_if"].str.contains("image links from S3", na=False))
                                    | (tx_df["error_log_if"].str.contains("Photos", na=False))
                                    | (tx_df["error_log_if"].str.contains("S3", na=False))
                                )
                            )
                        ]

                        # Skip if date_df is empty after filtering
                        if date_df.empty:
                            logger.warning(f"No valid rows for date {date} in {file_meta['name']}, skipping.")
                            continue

                        total = len(date_df)
                        completed = int((date_df["status_pass_failed_retry"] == "success").sum())
                        failed = int((date_df["status_pass_failed_retry"] == "fail").sum())
                        valid_starts = pd.to_datetime(date_df["start_time"].dropna())
                        valid_ends = pd.to_datetime(date_df["end_time"].dropna())
                        seconds = 0.0
                        if not valid_starts.empty and not valid_ends.empty:
                            delta = (valid_ends.max() - valid_starts.min()).total_seconds()
                            if not (math.isnan(delta) or math.isinf(delta)):
                                seconds = delta
                        minutes = f"{int(seconds // 60)}.{int(seconds % 60):02d}"
                        performance_rows.append(
                            {
                                "date": date,
                                "run_date": date,
                                "gcp_project_id": date_df["gcp_project_id"].iloc[0] if "gcp_project_id" in date_df.columns else "",
                                "gcp_project_name": date_df["gcp_project_name"].iloc[0] if "gcp_project_name" in date_df.columns else "",
                                "total_transaction": total,
                                "total_completed": completed,
                                "total_failed": failed,
                                "success_rate": round((completed / total) * 100, 2) if total > 0 else 0,
                                "total_runtime": minutes,
                            }
                        )


                    if not performance_rows:
                        logger.warning(f"No performance rows generated for {file_meta['name']}, skipping upload.")
                        

                    perf_df = pd.DataFrame(performance_rows)
                    perf_path = f"{perf_base}/{yyyymm[:4]}/performance_log_{yyyymm}.csv"
                    buf = io.BytesIO()
                    perf_df.to_csv(buf, index=False, encoding="utf-8-sig")
                    buf.seek(0)
                    self._control_sp.upload_file(perf_path, buf.getvalue())
                    logger.info(f"Performance log uploaded: {perf_path}")
                except Exception as exc:
                    logger.warning(f"Performance log update failed for {file_meta.get('name', '?')}: {exc}")

    # --- Stage 5: Notify ---

    async def _notify(self, join_df: pl.DataFrame, user_excel_bytes: bytes) -> None:
        user_b64 = base64.b64encode(user_excel_bytes).decode("utf-8")
        attachments = {self._user_file_name: user_b64}
        subject, html, inline = self._composer.compose(self._cfg.today, join_df)
        recipients = self._cfg.recipient_emails
        await self._email.send(
            bcc_recipients=recipients,
            subject=subject,
            body_html=html,
            attachments=attachments,
            inline_images=inline,
        )
        logger.info("Email notification sent")

    async def _notify_error(self, exc: Exception) -> None:
        """Send a failure alert to the team."""
        try:
            today = self._cfg.today
            await self._email.send(
                to_recipients=self._cfg.team_emails,
                cc_recipients=self._cfg.cc_emails,  # team email resolved in main.py
                subject=f"Fail Fraud Validation Run on {today.strftime('%Y-%m-%d')}",
                body_html=(
                    f"An error occurred during RTR-Fraud-validation. "
                    f"Error: {exc}"
                ),
            )
        except Exception as e:
            logger.error(f"Failed to send error notification email: {e}")

    # --- Cleanup ---

    def _cleanup(self) -> None:
        """Delete temporary GCS files."""
        for path in [self._input_gcs_path, self._lookup_gcs_path, self._user_gcs_path]:
            if path:
                self._gcs.delete(path)
        logger.info("GCS temp files cleaned up")
