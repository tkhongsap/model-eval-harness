# Library imports
import asyncio
import contextlib
import io
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from src.core.task_interface import TaskInterface

# Source code imports
from src.core.task_registry import task_registry
from src.modules.audit_log.batch_processing_log import BatchProcessingLogSchema, BatchProcessingPayload
from src.modules.audit_log.transaction_log import (
    TransactionLogSchema,
    TransactionPayload,
)
from src.modules.google.gcs import GCSModule
from src.modules.google.gemini_batch import GeminiBatchModule
from src.modules.microsoft.sharepoint import SharePointModule
from src.utils.common import (
    get_value_by_path,
    recursive_dict_value_by_key,
    resolve_date,
    resolve_env,
    safe_cast_value,
    safe_list_get,
    safe_list_get_slicing,
)
from src.utils.date_utils import (
    get_current_datetime,
    parse_datetime,
)
from src.utils.file_utils import (
    load_yaml,
    read_file,
)
from src.utils.logger import Logger
from src.utils.pandas_utils import (
    df_to_excel_bytes,
    ensure_df_schema,
    replace_nan_with_default,
    trim_all_columns,
)
from src.utils.pydantic_utils import pydantic_resolve_refs, sanitize_json_schema_enums
from src.utils.token_utils import gemini_cost
from tasks.sentiment_telesale.output_validation.validate_mapping import ValidationMapping
from tasks.sentiment_telesale.schemas.check_list_schema import (
    CheckListCategorySchema,
    CheckListSchemaSubcategoriesSchema,
    CheckListTagsSchema,
)
from tasks.sentiment_telesale.schemas.filename_list_schema import FilenameListSchema
from tasks.sentiment_telesale.schemas.ground_truth_schema import GroundTruthSchema
from tasks.sentiment_telesale.schemas.metadata import Metadata

logger = Logger(__name__)


@task_registry.register("TelesaleFactCheckTask")
class FactCheckTask(TaskInterface):
    """
    Combined fact-check batch task: upload voice files, prepare Gemini batch payloads,
    and submit the batch job in a single execution.

    Unlike the normal telesale pipeline, commission_skill_code is read directly from the
    filename list Excel file — no agent master (emp_id) lookup is required.

    Each run produces output under an execution-datetime-stamped GCS prefix so that
    multiple executions never overwrite each other.
    """

    COMMON_CONFIG_PATH = "config/common.yml"
    DEFAULT_FILENAME_LIST_SHEET_NAME = "FilenameList"
    DEFAULT_PAYLOAD_FILE = "payloads.jsonl"
    DEFAULT_JSONL_PREDICTION_FILE = "predictions.jsonl"
    DEFAULT_MODEL_VERSION = "gemini-2.5-flash"
    DEFAULT_RECORD_DATE = "99991231"
    DEFAULT_COST_TYPE = "batch"
    check_list_mapping = {
        "Categories": CheckListCategorySchema,
        "Subcategories": CheckListSchemaSubcategoriesSchema,
        "Tags": CheckListTagsSchema,
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.gcs = self.get_config("gcs", {})
        self.gcp = self.get_config("gcp", {})
        self.vertexai = self.get_config("vertexai", {})
        self.sharepoint = self.get_config("sharepoint", {})
        self.framework = self.get_config("framework", {})

        common_config = load_yaml(self.COMMON_CONFIG_PATH)
        self.control_access = common_config.get("control", {})

        # Caches for dynamic validation model construction
        self._cache_literal_checklist = {}
        self._cache_mapping_dict = {}

        # Per-metric thresholds from config (fallback to class-level STATUS_THRESHOLDS)
        self.metric_thresholds = self.get_config("metric_thresholds", {})

        # _IN. -> INBOUND, _OUT. -> OUTBOUND — filter by call type from filename
        self.filter_call = {
            "13_True_Promo_End": ["_OUT."],
            "01_True_CVG_DIGITAL": ["_OUT."],
            "03_True_CVG_Post": ["_OUT."],
            "08_True_P2P_M1": ["_OUT."],
            "09_True_P2P_M2": ["_OUT."],
            "10_True_UTOL": ["_OUT."],
            "Postpaid Upsell": ["_OUT."],
        }

        # Copy validate_mapping to avoid mutating the shared class-level dict
        self.validate_mapping = dict(ValidationMapping.validate_mapping)

        # Expose GT_FIELD_MAPPING on instance for test accessibility
        self.GT_FIELD_MAPPING = Metadata.GT_FIELD_MAPPING

    def pre_execute(self):
        """Initialize SharePoint Control, GCS, and Gemini Batch modules."""
        logger.debug("Initializing modules")

        # SharePoint Control
        try:
            self.control_site = resolve_env(self.control_access.get("site_domain"))
            self.sharepoint_control = SharePointModule(
                client_id=resolve_env(self.control_access.get("client_id")),
                client_secret=resolve_env(self.control_access.get("client_secret")),
                tenant_id=resolve_env(self.control_access.get("tenant_id")),
                site_domain=self.control_site,
                site_path=resolve_env(self.control_access.get("site_path")),
            )
            logger.debug(f"SharePoint Control: {self.control_site}")
        except Exception as e:
            logger.error(f"Failed to initialize SharePoint Control: {e}", exc_info=True)
            raise

        # GCS
        try:
            project_id = resolve_env(self.gcs.get("project_id"))
            bucket_name = resolve_env(self.gcs.get("bucket_name"))
            self.gcs_module = GCSModule(project_id=project_id, bucket_name=bucket_name)
            logger.debug(f"GCS: {project_id}/{bucket_name}")
        except Exception as e:
            logger.error(f"Failed to initialize GCS: {e}", exc_info=True)
            raise

        # Gemini Batch
        try:
            project_id = resolve_env(self.vertexai.get("project_id"))
            location = resolve_env(self.vertexai.get("location"))
            self.gemini_batch_module = GeminiBatchModule(
                genai_project_id=project_id,
                genai_location=location,
            )
            logger.debug(f"Gemini Batch: {project_id}/{location}")
        except Exception as e:
            logger.error(f"Failed to initialize Gemini Batch: {e}", exc_info=True)
            raise

    def execute_task(self) -> Any:
        """
        Main execution: check for existing predictions and retrieve, or submit a new batch job.
        Returns:
            list: Processed batch results, or None if a new batch job was submitted.
        """
        execution_dt = self.get_package("execution_dt", None)
        rerun_date = self.get_package("rerun_data_dt", None)
        datadate = rerun_date if rerun_date else execution_dt.strftime("%Y-%m-%d")

        output_path = "/".join(resolve_env(self.gcs.get("output_folder")).split("/")[:-1])
        existing_files = self.gcs_module.list_files(prefix=output_path)
        prediction_output = [f for f in existing_files if self.DEFAULT_JSONL_PREDICTION_FILE in f]

        if prediction_output:
            logger.info(f"Found {len(prediction_output)} existing prediction file(s) — retrieving results.")
            return self._retrieve_prediction_step(prediction_output, execution_dt)
        logger.info("No prediction output found. Submitting new batch job.")
        self._submit_job_step(execution_dt, datadate)
        return None

    def _retrieve_prediction_step(self, prediction_output: list, execution_dt: datetime) -> dict:
        """
        Retrieve and process batch prediction files from GCS.
        Parameters:
            prediction_output (list): List of GCS paths to predictions.jsonl files.
            execution_dt (datetime): Pipeline execution datetime for consistent load_dt stamping.
        Returns:
            list: Processed batch results.
        """
        all_results: list[dict] = []
        for idx, path in enumerate(prediction_output, 1):
            logger.debug(f"Processing batch [{idx}/{len(prediction_output)}]: {path}")
            try:
                raw_jsonl = GeminiBatchModule.retrieve_batch_results(gcs_module=self.gcs_module, batch_output_path=path)
                logger.debug(f"Retrieved {len(raw_jsonl)} prediction records from batch")
                batch_results = self._proc_raw_prediction(batch=path, raw_jsonl=raw_jsonl, execution_dt=execution_dt)
                all_results.extend(batch_results)
            except Exception as retrieve_err:
                logger.error(f"Failed to retrieve batch results from {path}: {retrieve_err}", exc_info=True)
                continue

        if not all_results:
            raise Exception("All batch files failed to retrieve — no prediction results available.")

        all_failed = all(r.get("prediction", {}).get("status") == "FAILED" for r in all_results)
        if all_failed:
            logger.error(f"All {len(all_results)} prediction(s) have FAILED status.")
            raise Exception(f"All {len(all_results)} prediction(s) failed — check batch output for errors.")

        failed_count = sum(1 for r in all_results if r.get("prediction", {}).get("status") == "FAILED")
        logger.info(
            f"Total predictions processed: {len(all_results)} ({failed_count} failed, {len(all_results) - failed_count} succeeded)"
        )

        try:
            self._evaluate(all_results)
        except Exception as eval_err:
            logger.error(f"Evaluation step failed (non-fatal): {eval_err}", exc_info=True)
            raise Exception(f"Evaluation step failed: {eval_err}") from eval_err

        return all_results

    def _submit_job_step(self, execution_dt: datetime, datadate: str) -> tuple:
        """
        Orchestrate upload → payload preparation → batch job submission.
        Parameters:
            execution_dt (datetime): The execution datetime for the current run.
            datadate (str): The date string for the current run.
        Returns:
            tuple: (payload_path, output_folder, processing_log_list)
        """
        # Resolve per-run paths using execution datetime for isolation
        input_folder = resolve_env(self.gcs.get("input_folder"))
        processing_voice_folder = resolve_date(
            text=resolve_env(self.gcs.get("processing_voice_folder")), replace_date=execution_dt
        )
        processing_batch_folder = resolve_date(
            text=resolve_env(self.gcs.get("processing_batch_folder")), replace_date=execution_dt
        )
        output_folder = resolve_date(text=resolve_env(self.gcs.get("output_folder")), replace_date=execution_dt)
        payload_path = processing_batch_folder + "/" + self.DEFAULT_PAYLOAD_FILE

        logger.debug(
            f"Run paths — input: {input_folder}, "
            f"processing_voice: {processing_voice_folder}, "
            f"batch: {processing_batch_folder}, "
            f"output: {output_folder}"
        )

        # --- Phase 1: Load filename list ---
        filename_list_df = self._get_filename_list()
        logger.debug(f"Loaded filename list: {len(filename_list_df)} records")

        # --- Phase 2: Upload voice files from SharePoint → GCS input folder ---
        self._upload_voice_files(filename_list_df, input_folder)

        # --- Phase 3: List files from GCS input folder, filter by filename list ---
        try:
            filenames_to_process = set(filename_list_df["filename"].values)
            all_input_files = self.gcs_module.list_files(prefix=input_folder)
            input_files = [f for f in all_input_files if os.path.basename(f) in filenames_to_process]
            if not input_files:
                raise Exception(f"No matching files found in GCS input folder: {input_folder}")
            logger.info(f"Found {len(input_files)} matching file(s) in GCS input folder")
        except Exception as e:
            logger.error(f"Failed to list GCS input files: {e}", exc_info=True)
            raise

        # --- Phase 4: Copy to per-run processing folder ---
        try:
            copy_result = asyncio.run(
                self.gcs_module.copy_files_batch(
                    source_files=input_files,
                    destination_prefix=processing_voice_folder,
                    max_concurrent_copies=int(resolve_env(self.framework["concurrency_upload"])),
                )
            )
            if copy_result["failed"] > 0:
                logger.warning(
                    f"Copy: {copy_result['success']} succeeded, "
                    f"{copy_result['failed']} failed — {copy_result.get('errors', [])}"
                )
            logger.debug(f"Copied {copy_result['success']} file(s) to {processing_voice_folder}")
        except Exception as e:
            logger.error(f"Failed to copy files to processing folder: {e}", exc_info=True)
            raise

        # --- Phase 5: List files in processing folder ---
        try:
            processing_files = self.gcs_module.list_files(prefix=processing_voice_folder)
            if not processing_files:
                raise Exception(f"No files in processing folder: {processing_voice_folder}")
            logger.debug(f"Processing {len(processing_files)} file(s)")
        except Exception as e:
            logger.error(f"Failed to list processing files: {e}", exc_info=True)
            raise

        # --- Phase 6: Map prompts using commission_skill_code from filename list ---
        try:
            prompt_mappings = self._mapping_prompt_by_filename_list(processing_files, filename_list_df)
        except Exception as e:
            logger.error(f"Failed to load prompt mappings: {e}", exc_info=True)
            raise Exception(f"Cannot load prompt mappings: {e}") from e

        # --- Phase 7: Build JSONL payloads ---
        gemini_generation_config = get_value_by_path(self.vertexai, "generation_config", {})
        mapping_file_type = {
            ".txt": "text/plain",
            ".wav": "audio/wav",
            ".jsonl": "application/jsonl",
        }

        # Rebuild validate_mapping with dynamic Literal types based on active tags from Excel
        for skill_code, tag_codes in self._cache_literal_checklist.items():
            campaign = self._cache_mapping_dict.get(str(skill_code))
            if campaign and campaign in self.validate_mapping and tag_codes:
                _, builder_fn = self.validate_mapping[campaign]
                self.validate_mapping[campaign] = (builder_fn(tag_codes), builder_fn)
                logger.debug(f"Rebuilt validation model for '{campaign}' with {len(tag_codes)} active tags")

        payloads = []
        processing_log_list = []
        files_added = 0
        files_skipped = 0

        for processing_file in processing_files:
            try:
                file_uri = f"gs://{self.gcs_module.bucket_name}/{processing_file}"
                action = "PREP_PAYLOAD_BATCH_PROCESSING"
                processing_log = BatchProcessingLogSchema.from_dict(
                    BatchProcessingPayload(
                        data_date=datadate,
                        gcp_project_id=self.gcs_module.project_id,
                        gcp_project_name=self.gcs_module.project_id,
                        gcs_bucket_name=self.gcs_module.bucket_name,
                        source_path=file_uri,
                        filename=os.path.basename(processing_file),
                        prediction_payload_path=None,
                    )
                )

                prompt_text = prompt_mappings[processing_file][0]
                if not prompt_text:
                    processing_log.stamp_error(
                        action=action,
                        error_message="No prompt found for file. Maybe agent's commission skill code is missing or invalid.",
                    )
                    processing_log_list.append(processing_log)
                    files_skipped += 1
                    continue

                matched_skill_code = prompt_mappings[processing_file][1]
                if not matched_skill_code:
                    processing_log.stamp_error(
                        action=action,
                        error_message="No validation mapping found for file. Maybe agent's commission skill code is missing or invalid.",
                    )
                    processing_log_list.append(processing_log)
                    files_skipped += 1
                    continue

                if not all(
                    filter_str in os.path.basename(processing_file)
                    for filter_str in self.filter_call.get(self._cache_mapping_dict[matched_skill_code], [])
                ):
                    processing_log.stamp_error(
                        action=action, error_message="File didn't match with call type (INBOUND or/and OUTBOUND)."
                    )
                    processing_log_list.append(processing_log)
                    files_skipped += 1
                    continue

                file_extension = Path(processing_file).suffix.lower()
                mime_type = mapping_file_type.get(file_extension)

                temp_generation_config = gemini_generation_config.copy()
                response_schema = self.validate_mapping[self._cache_mapping_dict[matched_skill_code]][
                    0
                ].model_json_schema()
                response_schema = pydantic_resolve_refs(response_schema)
                response_schema = sanitize_json_schema_enums(response_schema)
                if response_schema:
                    temp_generation_config.update({"responseSchema": response_schema})
                else:
                    logger.warning(f"Response schema is empty for {os.path.basename(processing_file)}")

                if not mime_type:
                    logger.debug(f"Skipping unsupported file type {file_extension}: {processing_file}")
                    files_skipped += 1
                    continue

                payload = {
                    "request": {
                        "contents": [
                            {
                                "role": "user",
                                "parts": [
                                    {"text": prompt_text},
                                    {"fileData": {"fileUri": file_uri, "mimeType": mime_type}},
                                ],
                            }
                        ]
                    }
                }
                payload["request"].update({"generationConfig": temp_generation_config})
                payloads.append(payload)
                processing_log.stamp_payload_path(prediction_payload_path=payload_path)
                processing_log.stamp_completion(action=action)
                processing_log_list.append(processing_log)
                files_added += 1

            except Exception as e:
                logger.error(f"Failed to create payload for {os.path.basename(processing_file)}: {e}")
                processing_log.stamp_error(action=action, error_message=f"Failed to create payload: {e}")
                processing_log_list.append(processing_log)
                files_skipped += 1

        logger.info(f"Payloads: {files_added} created, {files_skipped} skipped")
        self.processing_log_list = processing_log_list

        if not payloads:
            logger.warning("No payloads created — skipping batch job submission")
            return None, None, processing_log_list

        # --- Phase 8: Upload merged JSONL to GCS ---
        try:
            merged_payload = ("\n".join(json.dumps(line, ensure_ascii=False) for line in payloads)).encode("utf-8")
            self.gcs_module.update_content_to_gcs(
                content=merged_payload, mime_type=mapping_file_type[".jsonl"], destination_path=payload_path
            )
            logger.info(f"Uploaded {len(payloads)} payloads ({len(merged_payload) / 1024:.2f} KB) to {payload_path}")
        except Exception as e:
            logger.error(f"Failed to upload payload: {e}", exc_info=True)
            raise Exception(f"Cannot upload payload to GCS: {e}") from e

        # --- Phase 9: Submit batch job ---
        try:
            if not self.gcs_module.is_file_exists(payload_path):
                raise FileNotFoundError(f"Payload file not found: {payload_path}")

            full_source_path = f"gs://{self.gcs_module.bucket_name}/{payload_path}"
            full_output_path = f"gs://{self.gcs_module.bucket_name}/{output_folder}"
            model_name = resolve_env(self.vertexai.get("model"))
            display_name = resolve_date(
                text=self.vertexai.get("batch_job_name", "sentiment-fc-telesale-batch-job-%{DATA_DATE_YYYYMMDDHHMMSS}"),
                replace_date=execution_dt,
            )

            logger.info(f"Creating batch job: {display_name}")
            logger.debug(f"Model: {model_name}, Source: {full_source_path}, Output: {full_output_path}")

            batch_job = self.gemini_batch_module.create_batch_job(
                model_nm=model_name,
                src_uri=full_source_path,
                config={"dest": full_output_path, "display_name": display_name},
            )
            logger.info(f"Batch job created: {batch_job.name}")
        except Exception as e:
            logger.error(f"Failed to create batch job: {e}", exc_info=True)
            raise Exception(f"Batch job creation failed: {e}") from e

        # Initial status check
        try:
            time.sleep(5)
            job_status = self.gemini_batch_module.status_check_batch_job(job_name=batch_job.name)
            if job_status in ["JOB_STATE_FAILED", "JOB_STATE_CANCELLED", "JOB_STATE_EXPIRED"]:
                raise Exception(f"Batch job failed immediately with status: {job_status}")
            logger.info(f"Batch job submitted, current status: {job_status}")
        except Exception as e:
            logger.error(f"Batch job status check failed: {e}", exc_info=True)
            raise

        # Stamp batch job info to processing logs
        try:
            for log in processing_log_list:
                log.batch_job_id = str(batch_job.name.split("/")[-1])
                log.batch_job_display_name = batch_job.display_name
                log.model_name = model_name
        except Exception as e:
            logger.error(f"Failed to stamp batch job info to logs: {e}", exc_info=True)
            for log in processing_log_list:
                log.batch_job_id = None
                log.batch_job_display_name = display_name
                log.model_name = model_name

        self.processing_log_list = processing_log_list
        return payload_path, output_folder, processing_log_list

    def _get_filename_list(self) -> pd.DataFrame:
        """Load filename list Excel from SharePoint Control."""
        filename_list_path = resolve_env(get_value_by_path(self.sharepoint, "control.filename_list_file"))
        try:
            if not self.sharepoint_control.is_item_exists(filename_list_path):
                raise Exception(f"Filename list file not found: {filename_list_path}")
            content = self.sharepoint_control.get_item_by_path(filename_list_path)
            with io.BytesIO(content.content) as buf:
                list_df = pd.read_excel(buf, sheet_name=self.DEFAULT_FILENAME_LIST_SHEET_NAME)
            list_df = list_df.reindex(columns=list(FilenameListSchema.to_schema().columns.keys()))
            list_df = FilenameListSchema.validate(list_df)
            if list_df.empty:
                raise Exception(f"Filename list is empty: {filename_list_path}")
            return list_df
        except Exception as e:
            logger.error(f"Critical error loading filename list: {e}", exc_info=True)
            raise Exception(f"Critical error loading filename list: {e}") from e

    def _upload_voice_files(self, filename_list_df: pd.DataFrame, input_folder: str) -> None:
        """Download matching voice files from SharePoint and upload to GCS input folder."""
        source_path = resolve_env(get_value_by_path(self.sharepoint, "control.source_folder"))
        logger.debug(f"SharePoint source: {source_path}, GCS target: {input_folder}")

        voice_files = self.sharepoint_control.list_files(folder_path=source_path)
        logger.debug(f"Found {len(voice_files)} file(s) in SharePoint source folder")

        filenames_in_list = set(filename_list_df["filename"].values)
        filtered = [
            {"file_name": f.get("name"), "file_path": f.get("parentReference", {}).get("path", "")}
            for f in voice_files
            if f.get("name") in filenames_in_list
        ]
        logger.debug(f"Matched {len(filtered)} file(s) from filename list")

        if not filtered:
            raise Exception("No matching voice files found to upload")

        stream_list = [
            {
                "download": item["file_path"].replace("/drive/root:", "") + "/" + item["file_name"],
                "upload": input_folder + "/" + item["file_name"],
                "mime_type": "audio/wav",
            }
            for item in filtered
        ]

        concurrency = int(resolve_env(self.framework["concurrency_upload"]))
        asyncio.run(
            self.gcs_module.upload_sharepoint_to_gcs(
                sharepoint_object=self.sharepoint_control, stream_list=stream_list, max_concurrent_uploads=concurrency
            )
        )
        logger.info(f"Uploaded {len(stream_list)} file(s) to {input_folder}")

    def _build_checklist_text(
        self,
        category_df: pd.DataFrame,
        subcategory_df: pd.DataFrame,
        tags_df: pd.DataFrame,
    ) -> str:
        """
        Convert checklist dataframes to structured text for LLM consumption.
        Produces hierarchical text: main categories → sub-categories (with sections) → tags.
        Parameters:
            category_df (pd.DataFrame): DataFrame of main categories with columns [main_category_no, main_category_name, commission_skill_code, commission_skill, section, content, order].
            subcategory_df (pd.DataFrame): DataFrame of sub-categories with columns [sub_category_no, sub_category_name, section, content, order].
            tags_df (pd.DataFrame): DataFrame of tags with columns [item_no, tag_code, rule_and_logic, positive_example_th, negative_example_th, is_active].
        Returns:
            str: Formatted checklist text.
        """
        lines = []

        for main_no, cat_grp in category_df.groupby("main_category_no", sort=True):
            first = cat_grp.iloc[0]
            lines.append(
                f"### Main Category {main_no}: {first['main_category_name']} - {first['commission_skill']} Campaign"
            )
            lines.append("")
            for _, row in cat_grp.sort_values("order").iterrows():
                if pd.notna(row.get("section")) and pd.notna(row.get("content")):
                    lines.append(f"**{row['section']}:**")
                    lines.append(str(row["content"]))
                    lines.append("")

            for sub_no, sub_grp in subcategory_df.groupby("sub_category_no", sort=True):
                sub_first = sub_grp.iloc[0]
                lines.append(f"#### Sub-Category {sub_no}: {sub_first['sub_category_name']}")
                lines.append("")
                for _, sub_row in sub_grp.sort_values("order").iterrows():
                    if pd.notna(sub_row.get("section")) and pd.notna(sub_row.get("content")):
                        lines.append(f"**{sub_row['section']}:**")
                        lines.append(str(sub_row["content"]))
                        lines.append("")

                tag_sub = tags_df[
                    tags_df["item_no"].astype(str).str.startswith(f"{sub_no}.") & (tags_df["is_active"] == True)  # noqa: E712
                ].sort_values("item_no")

                for _, tag in tag_sub.iterrows():
                    lines.append(f"##### {tag['item_no']}. {tag['tag_code']}")
                    lines.append("")
                    if pd.notna(tag.get("rule_and_logic")):
                        lines.append(str(tag["rule_and_logic"]))
                        lines.append("")
                    if pd.notna(tag.get("positive_example_th")):
                        lines.append("**positive_example_th:**")
                        lines.append(str(tag["positive_example_th"]))
                        lines.append("")
                    if pd.notna(tag.get("negative_example_th")):
                        lines.append("**negative_example_th:**")
                        lines.append(str(tag["negative_example_th"]))
                        lines.append("")

        return "\n".join(lines)

    def _mapping_prompt_by_filename_list(self, file_list: list, filename_list_df: pd.DataFrame) -> dict:
        """
        Map prompt templates to GCS file paths using commission_skill_code from
        the filename list. Simplified version — no agent master (emp_id) lookup needed.

        Parameters:
            file_list (list): List of GCS file paths in the processing folder.
            filename_list_df (pd.DataFrame): DataFrame with columns [filename, commission_skill_code, commission_skill].

        Returns:
            dict: {gcs_file_path: (prompt_text, commission_skill_code)}
        """
        logger.debug(f"Mapping prompts for {len(file_list)} files")

        # Step 1: Load prompts — system prompt (local), common prompt + check-list Excel (SharePoint)
        try:
            system_prompt_file = self.framework.get("system_prompt_file")
            logger.debug(f"Loading system prompt file: {system_prompt_file}")
            system_prompt = read_file(system_prompt_file)
            if not system_prompt.strip():
                logger.error("System prompt file is empty")
                raise ValueError("System prompt file is empty or invalid")
        except Exception as e:
            logger.error(f"Failed to load system prompt from {system_prompt_file}: {e}", exc_info=True)
            raise Exception(f"Critical error loading system prompt: {e}") from e

        try:
            common_prompt_file = resolve_env(get_value_by_path(self.sharepoint, "control.common_prompt_file"))
            if not self.sharepoint_control.is_item_exists(common_prompt_file):
                logger.error(f"Common prompt file not found in SharePoint: {common_prompt_file}")
                raise FileNotFoundError(f"Common prompt file not found: {common_prompt_file}")
            common_prompt = self.sharepoint_control.get_item_by_path(common_prompt_file).content.decode("utf-8")
            if not common_prompt.strip():
                logger.error(f"Common prompt file is empty: {common_prompt_file}")
                raise ValueError("Common prompt file is empty or invalid")

            check_list_prompt_file = resolve_env(get_value_by_path(self.sharepoint, "control.check_list_prompt_file"))
            if not self.sharepoint_control.is_item_exists(check_list_prompt_file):
                logger.error(f"Check list prompt file not found in SharePoint: {check_list_prompt_file}")
                raise FileNotFoundError(f"Check list prompt file not found: {check_list_prompt_file}")

            excel_sheet_cache = {}
            check_list_content = self.sharepoint_control.get_item_by_path(check_list_prompt_file).content
            for key, value in self.check_list_mapping.items():
                df = pd.read_excel(io.BytesIO(check_list_content), sheet_name=key, dtype=str, engine="openpyxl")
                schema_columns = list(value.to_schema().columns.keys())
                df = df.reindex(columns=schema_columns)
                df = value.validate(df)
                excel_sheet_cache[key] = df

            category_df = excel_sheet_cache["Categories"].sort_values(
                by=["commission_skill_code", "main_category_no", "order"]
            )
            subcategory_df = excel_sheet_cache["Subcategories"].sort_values(
                by=["commission_skill_code", "sub_category_no", "order"]
            )
            tags_df = excel_sheet_cache["Tags"].sort_values(by=["commission_skill_code", "item_no"])

            skill_codes = category_df["commission_skill_code"].unique()
            if len(skill_codes) == 0:
                logger.error("No commission skill codes found in check list prompts")
                raise ValueError("Check list prompt data is empty or invalid - no commission skill codes found")

            skill_prompt_list = []
            for skill in skill_codes:
                skill_category_df = category_df[category_df["commission_skill_code"] == skill].sort_values(
                    by=["main_category_no", "order"]
                )
                skill_subcategory_df = subcategory_df[subcategory_df["commission_skill_code"] == skill].sort_values(
                    by=["sub_category_no", "order"]
                )
                skill_tags_df = tags_df[
                    (tags_df["commission_skill_code"] == skill) & (tags_df["is_active"] == True)  # noqa: E712
                ].sort_values(by=["item_no"])

                # Cache the tag codes for replacing in pydantic validation later in the flow
                self._cache_literal_checklist[skill] = skill_tags_df["tag_code"].tolist()
                # Also cache the mapping of commission_skill_code to commission_skill for later use in validation mapping
                self._cache_mapping_dict[str(skill)] = (
                    skill_category_df["commission_skill"].iloc[0] if not skill_category_df.empty else None
                )

                checklist_md = self._build_checklist_text(skill_category_df, skill_subcategory_df, skill_tags_df)
                full_prompt = system_prompt.replace("${KNOWLEDGE_BASE}", common_prompt).replace(
                    "${CAMPAIGN_CHECKLIST}", checklist_md
                )
                skill_prompt_list.append({"commission_skill_code": str(skill), "prompt_template": full_prompt})

            skill_prompt_df = pd.DataFrame(skill_prompt_list)

        except Exception as e:
            logger.error(f"Failed to load user prompts from SharePoint: {e}", exc_info=True)
            raise Exception(f"Critical error loading user prompts: {e}") from e

        # Step 2: Lookup commission_skill_code directly from filename list (no emp_id master needed)
        try:
            filename_lookup = filename_list_df.set_index("filename")[["commission_skill_code"]].to_dict(orient="index")
            file_meta_list = []
            for f in file_list:
                basename = os.path.basename(f)
                entry = filename_lookup.get(basename, {})
                skill_code = entry.get("commission_skill_code")
                if skill_code is None:
                    logger.warning(f"No commission_skill_code for '{basename}' in filename list")
                file_meta_list.append(
                    {"file_path": f, "commission_skill_code": str(skill_code) if skill_code is not None else None}
                )

            file_meta_df = pd.DataFrame(file_meta_list)
            if file_meta_df.empty:
                logger.warning("No file metadata extracted")
                return {}

            merged_df = file_meta_df.merge(skill_prompt_df, how="left", on="commission_skill_code")

            missing_df = merged_df[merged_df["prompt_template"].isnull()]
            if not missing_df.empty:
                logger.warning(
                    f"{len(missing_df)} file(s) have no prompt template — commission_skill_code may be missing or unmapped"
                )
                for _, row in missing_df.head(3).iterrows():
                    logger.warning(
                        f"  - {os.path.basename(row['file_path'])} (commission_skill_code={row['commission_skill_code']})"
                    )
                if len(missing_df) > 3:
                    logger.warning(f"  ... and {len(missing_df) - 3} more")

            final_df = merged_df.dropna().drop_duplicates()

            if final_df.empty:
                logger.warning("No files could be mapped to a prompt template")
                return {}

            prompt_dict = {
                row["file_path"]: (row["prompt_template"], row["commission_skill_code"])
                for row in final_df.replace({np.nan: None}).to_dict(orient="records")
            }
            logger.debug(f"Prompt mapping complete: {len(prompt_dict)}/{len(file_list)} file(s) mapped")
            return prompt_dict

        except Exception as e:
            logger.error(f"Failed to create final prompt mapping: {e}", exc_info=True)
            raise Exception(f"Critical error creating final prompt mapping: {e}") from e

    def _proc_raw_prediction(self, batch: str, raw_jsonl: list[dict], execution_dt: datetime) -> list[dict]:
        """
        Process raw prediction records into structured format and append to batch_results.
        Parameters:
            batch (str): GCS path of the batch file (for logging).
            raw_jsonl (list[dict]): List of raw prediction records from Gemini batch output.
            execution_dt (datetime): Pipeline execution datetime for consistent load_dt stamping.
        Returns:
            list[dict]: List of processed prediction records.
        """

        def add_additional_info(line: dict) -> dict:
            model_version = get_value_by_path(line, "response.modelVersion", None)
            if model_version is None:
                logger.warning("Model version missing, using default")
                model_version = self.DEFAULT_MODEL_VERSION
            return {
                "create_time": get_value_by_path(line, "response.createTime", None),
                "processed_time": get_value_by_path(line, "processed_time", None),
                "model_version": model_version,
            }

        load_dt_str = execution_dt.strftime("%Y-%m-%d %H:%M:%S")
        processed_count = 0
        skipped_count = 0
        batch_results = []

        for line_idx, line in enumerate(raw_jsonl):
            try:
                voice_processed_path = safe_list_get(
                    recursive_dict_value_by_key(data=line, target_key="fileUri"), 0, None
                )
                if voice_processed_path is None:
                    logger.warning(f"No 'fileUri' in line {line_idx + 1}, skipping")
                    skipped_count += 1
                    continue

                file_name = os.path.splitext(os.path.basename(voice_processed_path))[0]
                logger.debug(f"Processing prediction for: {file_name}")

                try:
                    file_name_components = file_name.split("_")
                    try:
                        record_date = safe_list_get(file_name_components, -3, None)
                        if not record_date or not re.match(r"^\d{8}$", record_date or ""):
                            regex_match = re.findall(r"(?<=\/)\d{8}(?=\/)", voice_processed_path)
                            if regex_match:
                                record_date = regex_match[-1]
                                logger.warning(f"Record date extracted from path: {record_date}")
                            else:
                                record_date = self.DEFAULT_RECORD_DATE
                                logger.warning(f"Record date not found, using default: {record_date}")
                    except Exception as rec_err:
                        logger.error(f"Error extracting record date for '{file_name}': {rec_err}")
                        record_date = self.DEFAULT_RECORD_DATE

                    voice_info = {
                        "file_uri": voice_processed_path,
                        "file_name": file_name,
                        "file_ext": os.path.splitext(os.path.basename(voice_processed_path))[1],
                        "call_id": safe_list_get(file_name_components, 0, None),
                        "phone_number": safe_list_get(file_name_components, 1, None),
                        "call_time": safe_list_get(file_name_components, 2, None),
                        "agent_id": safe_list_get(file_name_components, 3, None),
                        "first_name": " ".join(
                            safe_list_get_slicing(list_name=file_name_components, start=4, end=-5, default_value="")
                        ).capitalize()
                        or None,
                        "last_name": safe_list_get(file_name_components, -5, "").capitalize() or None,
                        "provider": safe_list_get(file_name_components, -4, None),
                        "record_date": record_date,
                        "duration": safe_list_get(file_name_components, -2, None),
                    }
                except Exception as parse_err:
                    logger.error(f"Error parsing file name '{file_name}': {parse_err}")
                    voice_info = {
                        "file_uri": voice_processed_path,
                        "file_name": file_name,
                        "file_ext": None,
                        "call_id": None,
                        "phone_number": None,
                        "call_time": None,
                        "agent_id": None,
                        "first_name": None,
                        "last_name": None,
                        "provider": None,
                        "record_date": self.DEFAULT_RECORD_DATE,
                        "duration": None,
                    }

                # Error status in batch result
                if line.get("status", "") != "":
                    logger.warning(f"Batch result error status: {line['status']}")
                    payload = self._prepare_prediction_payload(voice_info, prediction=line["status"], err_flag=True)
                    payload["prediction"].update(add_additional_info(line))
                    payload["load_dt"] = load_dt_str
                    try:
                        usage_summary = GeminiBatchModule.sum_tokens_usage_for_billing(
                            get_value_by_path(line, "response.usageMetadata", {})
                        )
                        payload["prediction"].update(usage_summary)
                    except Exception:
                        pass
                    batch_results.append(payload)
                    processed_count += 1
                    continue

                # Missing prediction text
                prediction_str = get_value_by_path(line, "response.candidates.0.content.parts.0.text", None)
                if prediction_str is None:
                    warning_msg = f"No prediction found for file {file_name}"
                    logger.warning(warning_msg)
                    payload = self._prepare_prediction_payload(voice_info, prediction=warning_msg, err_flag=True)
                    payload["prediction"].update(add_additional_info(line))
                    payload["load_dt"] = load_dt_str
                    with contextlib.suppress(Exception):
                        payload["prediction"].update(
                            GeminiBatchModule.sum_tokens_usage_for_billing(
                                get_value_by_path(line, "response.usageMetadata", {})
                            )
                        )
                    batch_results.append(payload)
                    processed_count += 1
                    continue

                # Successful prediction
                payload = self._prepare_prediction_payload(voice_info, prediction=prediction_str)
                payload["prediction"].update(add_additional_info(line))
                payload["load_dt"] = load_dt_str
                try:
                    usage_summary = GeminiBatchModule.sum_tokens_usage_for_billing(
                        get_value_by_path(line, "response.usageMetadata", {})
                    )
                    payload["prediction"].update(usage_summary)
                    logger.debug(f"Usage: {usage_summary}")
                except Exception as usage_err:
                    logger.warning(f"Could not extract usage metadata for {file_name}: {usage_err}")
                    payload["prediction"].update(
                        {"token_input": {"text": 0, "audio": 0}, "token_output": {"text": 0}, "token_cached": 0}
                    )

                batch_results.append(payload)
                processed_count += 1

            except Exception as line_err:
                logger.error(f"Error processing line {line_idx + 1} in batch {batch}: {line_err}", exc_info=True)
                skipped_count += 1

        logger.info(f"Batch processing complete: {processed_count} processed, {skipped_count} skipped")
        return batch_results

    def _prepare_prediction_payload(self, voice_info: dict, prediction: str, err_flag: bool = False) -> dict:
        """
        Build a structured prediction payload.
        Parameters:
            voice_info (dict): File metadata extracted from the voice file path.
            prediction (str): Raw prediction JSON string or error message.
            err_flag (bool): True if this is an error record.
        Returns:
            dict: Structured payload with file_metadata and prediction fields.
        """
        logger.debug(f"Preparing payload for: {voice_info.get('file_name')}")
        fw_schema = {
            "file_metadata": voice_info,
            "prediction": {
                "raw_prediction": None,
                "status": None,
                "message": None,
            },
        }

        if err_flag:
            fw_schema["prediction"]["status"] = "FAILED"
            fw_schema["prediction"]["message"] = prediction
            logger.warning(f"Marking record as FAILED: {voice_info.get('file_name')}")
            return fw_schema

        try:
            logger.debug(f"Parsing prediction JSON for: {voice_info.get('file_name')}")
            parsed_data = json.loads(prediction)
            campaign_name = parsed_data.get("campaign_name")
            if not campaign_name:
                logger.error(f"No validation model found for prediction in file: {voice_info.get('file_name')}")
                raise ValueError("No suitable validation model found for prediction.")
            validate_match = self.validate_mapping[campaign_name][0]
            logger.debug(f"Using validation model: {campaign_name} for file: {voice_info.get('file_name')}")
            validation_class = validate_match.model_validate_json(prediction)
            fw_schema["prediction"]["raw_prediction"] = validation_class.model_dump()
            fw_schema["prediction"]["status"] = "SUCCESS"
            fw_schema["prediction"]["message"] = None
            logger.debug(f"Successfully parsed prediction for: {voice_info.get('file_name')}")
        except Exception as e:
            logger.error(f"Failed to parse prediction for '{voice_info.get('file_name')}': {e}", exc_info=True)
            fw_schema["prediction"]["status"] = "FAILED"
            fw_schema["prediction"]["message"] = f"Failed to parse prediction JSON: {str(e)}"

        return fw_schema

    def _evaluate(self, batch_results: list) -> None:
        """
        Compare batch predictions against ground truth Excel and upload an evaluation
        metrics report (one row per GT field) to SharePoint.

        GT=N/A and Pred=None are treated as equivalent (both agree "not applicable") → TP.
        Only rows where a matching prediction exists are included in the confusion matrix.
        """
        logger.info("Starting evaluation step")

        # 1. Build prediction lookup: file_name → raw_prediction dict (SUCCESS only)
        pred_lookup: dict = {}
        for record in batch_results:
            pred = record.get("prediction", {})
            if pred.get("status") == "SUCCESS":
                file_name = record.get("file_metadata", {}).get("file_name")
                raw = pred.get("raw_prediction")
                if file_name and raw:
                    pred_lookup[file_name] = raw
        logger.debug(f"Prediction lookup built: {len(pred_lookup)} SUCCESS record(s)")

        # 2. Build prediction dataset
        pred_ds = []
        for key, value in pred_lookup.items():
            tmp_map = {"filename": key}
            for col_name, (_sub_cat, sub_cat_item) in Metadata.GT_FIELD_MAPPING.items():
                val = get_value_by_path(value, sub_cat_item)
                if val is True:
                    tmp_map[col_name] = "True"
                elif val is False:
                    tmp_map[col_name] = "False"
                else:
                    tmp_map[col_name] = "N/A"
            pred_ds.append(tmp_map)
        pred_df = pd.DataFrame(pred_ds).sort_values(by="filename")

        # 3. Load GT Excel from SharePoint (dedicated ground_truth_file)
        gt_path = resolve_env(get_value_by_path(self.sharepoint, "control.ground_truth_file"))
        gt_content = self.sharepoint_control.get_item_by_path(gt_path)
        with io.BytesIO(gt_content.content) as buf:
            gt_df = pd.read_excel(buf, sheet_name=Metadata.DEFAULT_GT_SHEET_NAME)
        gt_df = GroundTruthSchema.validate(gt_df)
        # Strip extension from filename for matching against filename (no ext)
        gt_df["filename"] = gt_df["filename"].apply(lambda x: os.path.splitext(str(x))[0] if pd.notna(x) else None)
        gt_df = trim_all_columns(gt_df)
        ex_cols = [col for col in gt_df.columns if col not in ["filename"]]
        gt_df[ex_cols] = gt_df[ex_cols].map(
            lambda x: "True" if str(x).strip() in ("T", "t") else ("False" if str(x).strip() in ("F", "f") else "N/A")
        )
        gt_df = gt_df.sort_values(by="filename")

        # 4. Align GT and pred on filename (inner join — only matched rows are evaluated)
        merged = gt_df.merge(pred_df, on="filename", how="inner", suffixes=("_gt", "_pred"))
        total_gt = len(gt_df)
        total_pred = len(pred_df)
        logger.info(f"GT: {total_gt} rows, Pred: {total_pred} rows, Matched: {len(merged)} rows")

        # 5. Run-level metadata — sourced from batch result fields
        model_versions = [
            r["prediction"]["model_version"] for r in batch_results if r.get("prediction", {}).get("model_version")
        ]
        processed_times = [
            r["prediction"]["processed_time"] for r in batch_results if r.get("prediction", {}).get("processed_time")
        ]
        load_dts = [r["load_dt"] for r in batch_results if r.get("load_dt")]

        tz = ZoneInfo(get_value_by_path(load_yaml(self.COMMON_CONFIG_PATH), "framework.timezone", "Asia/Bangkok"))

        model_version = (
            max(set(model_versions), key=model_versions.count) if model_versions else self.DEFAULT_MODEL_VERSION
        )
        gcp_project_id = resolve_env(self.gcs.get("project_id", ""))

        # created_datetime = load_dt (report creation time, already in config timezone)
        execution_dt = self.get_package("execution_dt", get_current_datetime())
        raw_load = max(set(load_dts), key=load_dts.count) if load_dts else None
        if raw_load:
            try:
                report_dt = datetime.strptime(raw_load, "%Y-%m-%d %H:%M:%S").replace(tzinfo=tz)
                created_datetime = report_dt.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                created_datetime = execution_dt.strftime("%Y-%m-%d %H:%M:%S")
        else:
            created_datetime = execution_dt.strftime("%Y-%m-%d %H:%M:%S")

        # processed_datetime = processed_time (Gemini prediction time → config timezone)
        raw_pt = max(set(processed_times), key=processed_times.count) if processed_times else None
        pt_parsed = parse_datetime(raw_pt, tz) if raw_pt else None
        processed_datetime = pt_parsed.strftime("%Y-%m-%d %H:%M:%S") if pt_parsed else None

        common_meta = {
            "created_datetime": created_datetime,
            "processed_datetime": processed_datetime,
            "gcp_project_id": gcp_project_id,
            "gcp_project_name": gcp_project_id,
            "model_version": model_version,
            "ground_truth_count": total_gt,
            "prediction_count": total_pred,
        }

        # 6. Compute per-criterion TP/FP/FN/TN grouped by dimension (one-vs-rest for class "True")
        dimension_criteria: dict = {}
        for col_name, (dimension, _) in Metadata.GT_FIELD_MAPPING.items():
            dimension_criteria.setdefault(dimension, []).append(col_name)

        output_rows = []
        custom_dim_sort = []
        custom_label_sort = []
        for dimension, col_names in dimension_criteria.items():
            dim_rows = []
            for col_name in col_names:
                gt_col = f"{col_name}_gt"
                pred_col = f"{col_name}_pred"
                if gt_col not in merged.columns or pred_col not in merged.columns:
                    logger.warning(f"Column '{col_name}' missing after merge — skipping")
                    continue

                gt_s = merged[gt_col].astype(str).str.strip()
                pr_s = merged[pred_col].astype(str).str.strip()
                exact = gt_s == pr_s

                # Simple correct / incorrect confusion matrix:
                # TP: GT == Pred  (exact match across True, False, N/A)
                # FP: GT != Pred  (any mismatch)
                # FN: 0 (no missed positives by definition)
                # TN: 0 (no true negatives by definition)
                TP = int(exact.sum())  # noqa: N806
                FP = int((~exact).sum())  # noqa: N806
                FN = 0  # noqa: N806
                TN = 0  # noqa: N806

                metrics = self._compute_eval_metrics(TP, FP, FN, TN)
                label_name = re.sub(r"^(op_|se_|cx_|cp_)", "", col_name).replace(dimension, "").strip("_")
                dim_rows.append({**common_meta, "dimension": dimension, "label": label_name, **metrics})
                custom_dim_sort.append(dimension)
                custom_label_sort.append(label_name)

            # Append labels in GT_FIELD_MAPPING order, weighted_avg last
            if dim_rows:
                output_rows.extend(dim_rows)

                total_w = sum(r["weight"] for r in dim_rows)
                if total_w > 0:
                    acc = round(sum(r["accuracy"] * r["weight"] for r in dim_rows) / total_w, 2)
                    prec = round(sum(r["precision"] * r["weight"] for r in dim_rows) / total_w, 2)
                    rec = round(sum(r["recall"] * r["weight"] for r in dim_rows) / total_w, 2)
                    f1 = round(sum(r["f1_score"] * r["weight"] for r in dim_rows) / total_w, 2)
                    wavg = {
                        "accuracy": acc,
                        "accuracy_status": self._eval_status(acc, "accuracy"),
                        "precision": prec,
                        "precision_status": self._eval_status(prec, "precision"),
                        "recall": rec,
                        "recall_status": self._eval_status(rec, "recall"),
                        "f1_score": f1,
                        "f1_score_status": self._eval_status(f1, "f1_score"),
                        "TP": sum(r["TP"] for r in dim_rows),
                        "FP": sum(r["FP"] for r in dim_rows),
                        "FN": sum(r["FN"] for r in dim_rows),
                        "TN": sum(r["TN"] for r in dim_rows),
                        "weight": total_w,
                    }
                else:
                    wavg = dict.fromkeys(
                        ["accuracy", "precision", "recall", "f1_score", "TP", "FP", "FN", "TN", "weight"], 0
                    )
                    for s in ["accuracy_status", "precision_status", "recall_status", "f1_score_status"]:
                        wavg[s] = "unacceptable"
                output_rows.append({**common_meta, "dimension": dimension, "label": "weighted_avg", **wavg})
                custom_label_sort.append("weighted_avg")

        # 7. Build output DataFrame with fixed schema
        out_df = pd.DataFrame(output_rows)
        out_df = ensure_df_schema(out_df, Metadata.EVALUATION_OUTPUT_SCHEMA)

        # 8. Merge with existing report, sort desc, upload to SharePoint
        eval_path = resolve_date(
            text=resolve_env(get_value_by_path(self.sharepoint, "control.evaluation_folder")), replace_date=execution_dt
        )
        existing_df = None
        try:
            if self.sharepoint_control.is_item_exists(eval_path):
                existing_content = self.sharepoint_control.get_item_by_path(eval_path)
                with io.BytesIO(existing_content.content) as buf:
                    existing_df = pd.read_excel(buf, sheet_name=Metadata.DEFAULT_GT_SHEET_NAME)
                logger.info(f"Loaded existing evaluation: {len(existing_df)} rows")
        except Exception as load_err:
            logger.warning(f"Could not load existing evaluation: {load_err}")

        final_df = (
            pd.concat([existing_df, out_df], ignore_index=True)
            if existing_df is not None and not existing_df.empty
            else out_df
        )
        sort_rank = {}
        for i, row in enumerate(output_rows):
            key = (row["dimension"], row["label"])
            if key not in sort_rank:
                sort_rank[key] = i
        final_df["_sort_key"] = final_df.apply(
            lambda r: sort_rank.get((r["dimension"], r["label"]), len(output_rows)), axis=1
        )
        final_df = final_df.sort_values(by=["created_datetime", "_sort_key"], ascending=[False, True]).reset_index(
            drop=True
        )
        del final_df["_sort_key"]
        final_df = ensure_df_schema(final_df, Metadata.EVALUATION_OUTPUT_SCHEMA)
        final_df_byte = df_to_excel_bytes(final_df, sheet_name=Metadata.DEFAULT_GT_SHEET_NAME)
        self.sharepoint_control.upload_file(upload_path=eval_path, content=final_df_byte)
        logger.info(f"Evaluation uploaded: {eval_path} ({len(output_rows)} new rows, {len(final_df)} total)")

    def _compute_eval_metrics(self, TP: int, FP: int, FN: int, TN: int) -> dict:  # noqa: N803
        total = TP + FP + FN + TN
        accuracy = (TP + TN) / total * 100 if total > 0 else 0.0
        precision = TP / (TP + FP) * 100 if (TP + FP) > 0 else 0.0
        recall = TP / (TP + FN) * 100 if (TP + FN) > 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
        return {
            "accuracy": round(accuracy, 2),
            "accuracy_status": self._eval_status(accuracy, "accuracy"),
            "precision": round(precision, 2),
            "precision_status": self._eval_status(precision, "precision"),
            "recall": round(recall, 2),
            "recall_status": self._eval_status(recall, "recall"),
            "f1_score": round(f1, 2),
            "f1_score_status": self._eval_status(f1, "f1_score"),
            "TP": TP,
            "FP": FP,
            "FN": FN,
            "TN": TN,
            "weight": TP + FN,
        }

    def _eval_status(self, value: float, metric: str) -> str:
        """
        Return status label for a metric value using per-metric config thresholds.
        Parameters:
            value (float): Metric value (e.g. accuracy percentage).
            metric (str): Metric name (e.g. "accuracy", "precision", "recall", "f1_score").
        Returns:
            str: Status label ("excellent", "good", "acceptable", "unacceptable").
        """
        cfg = self.metric_thresholds.get(metric, {})
        for k, v in cfg.items():
            if not isinstance(v, (int)):
                logger.error(f"Invalid threshold value for metric '{metric}', status '{k}': {v} (must be integer)")
                raise ValueError(f"Invalid threshold value for metric '{metric}', status '{k}': {v}")
            if k not in ["excellent", "good", "acceptable"]:
                logger.error(
                    f"Invalid status key in thresholds for metric '{metric}': {k} (must be 'excellent', 'good', or 'acceptable')"
                )
                raise ValueError(f"Invalid status key in thresholds for metric '{metric}': {k}")
        if cfg:
            excellent = float(cfg.get("excellent"))
            good = float(cfg.get("good"))
            acceptable = float(cfg.get("acceptable"))
            if value >= excellent:
                return "excellent"
            if value >= good:
                return "good"
            if value >= acceptable:
                return "acceptable"
            return "unacceptable"
        logger.error(f"No thresholds configured for metric '{metric}'")
        raise ValueError(f"No thresholds configured for metric '{metric}'")

    def post_execute(self, result: Any) -> Any:
        """
        Archive batch output files, clean up GCS working directories, and insert
        transaction log. Only runs when predictions have been retrieved (result is not None).
        """
        if not result:
            logger.info("No batch results (batch job submitted, not retrieved) — skipping archive and transaction log")
            return result

        execution_dt = self.get_package("execution_dt", None)

        # Build DataFrame from batch results
        after_upload_list = []
        for record in result:
            try:
                file_name = get_value_by_path(record, "file_metadata.file_name", None)
                file_ext = get_value_by_path(record, "file_metadata.file_ext", "") or ""
                full_path = (
                    resolve_env(get_value_by_path(self.sharepoint, "control.source_folder"))
                    + "/"
                    + str(file_name or "")
                    + file_ext
                )

                after_upload_list.append(
                    {
                        "file_name": file_name,
                        "full_path": full_path,
                        "folder": None,
                        "record_date": get_value_by_path(record, "file_metadata.record_date", self.DEFAULT_RECORD_DATE),
                        "duration": get_value_by_path(record, "file_metadata.duration", None),
                        "token_input": get_value_by_path(record, "prediction.token_input", None),
                        "token_cached": get_value_by_path(record, "prediction.token_cached", None),
                        "token_output": get_value_by_path(record, "prediction.token_output", None),
                        "status": get_value_by_path(record, "prediction.status", None),
                        "message": get_value_by_path(record, "prediction.message", None),
                        "processed_time": get_value_by_path(record, "prediction.processed_time", None),
                        "create_time": get_value_by_path(record, "prediction.create_time", None),
                        "model_version": get_value_by_path(record, "prediction.model_version", None),
                        "load_dt": record.get("load_dt", None),
                    }
                )
            except Exception as e:
                logger.error(f"Error building upload dict for record: {e}", exc_info=True)
                continue

        df = pd.DataFrame(after_upload_list)
        logger.debug(f"Built DataFrame with {len(df)} records for archive and logging")

        try:
            self._archive_and_cleanup(execution_dt)
        except Exception as archive_err:
            logger.error(f"Archive and cleanup failed: {archive_err}", exc_info=True)
            raise Exception(f"Archive and cleanup process failed: {archive_err}") from archive_err

        if df.empty:
            logger.warning("DataFrame is empty — all records failed to build. Skipping transaction log.")
        else:
            try:
                self._insert_log_record(df)
            except Exception as log_err:
                logger.error(f"Failed to insert transaction log records: {log_err}", exc_info=True)
                raise Exception(f"Critical error: Transaction log insertion failed: {log_err}") from log_err

        return result

    def _archive_and_cleanup(self, execution_dt: datetime) -> None:  # noqa: ARG002
        """
        Archive prediction JSONL files from the GCS output folder to the archive folder,
        then delete working directories (input, processing, output).
        """
        output_parent = "/".join(resolve_env(self.gcs.get("output_folder")).split("/")[:-1])
        archive_folder = resolve_env(self.gcs.get("archive_batch_folder"))
        logger.info(f"Archiving batch files from {output_parent} to {archive_folder}")

        # --- Archive prediction output files ---
        archived_count = 0
        failed_archive_count = 0

        try:
            output_files = self.gcs_module.list_files(prefix=output_parent)
            logger.debug(f"Found {len(output_files)} file(s) in output folder to archive")
        except Exception as list_err:
            logger.error(f"Failed to list output files for archiving: {list_err}", exc_info=True)
            output_files = []

        for idx, file_path in enumerate(output_files, 1):
            try:
                # Strip output_parent prefix, then drop the execution-timestamp folder (index 0)
                # e.g. "output/20260304150358/prediction-model-.../predictions.jsonl"
                #   → "prediction-model-.../predictions.jsonl"
                relative_path = file_path[len(output_parent) :].lstrip("/")
                batch_path = "/".join(relative_path.split("/")[1:])
                destination = f"{archive_folder}/{batch_path}"
                logger.debug(f"Archiving [{idx}/{len(output_files)}]: {file_path} -> {destination}")
                self.gcs_module.move_file(source_path=file_path, destination_path=destination)
                archived_count += 1
            except Exception as archive_err:
                logger.error(f"Failed to archive file '{file_path}': {archive_err}", exc_info=True)
                failed_archive_count += 1
                continue

        logger.info(f"Batch archive complete. Archived: {archived_count}, Failed: {failed_archive_count}")

        # --- Cleanup working directories ---
        logger.debug("Cleaning up working GCS directories")

        dirs_to_delete = []
        try:
            dirs_to_delete.append(resolve_env(self.gcs.get("input_folder")))
        except Exception as e:
            logger.warning(f"Could not resolve input_folder for cleanup: {e}")

        try:
            # Parent processing/ folder — strip "voice/%{DATA_DATE_YYYYMMDDHHMMSS}" (last 2 components)
            # e.g. "sentiment_telesale/fact_check/processing/voice/%{...}" → "sentiment_telesale/fact_check/processing"
            # This covers both processing/voice/ and processing/batch/ in one delete
            processing_voice = resolve_env(self.gcs.get("processing_voice_folder"))
            dirs_to_delete.append("/".join(processing_voice.split("/")[:-2]))
        except Exception as e:
            logger.warning(f"Could not resolve processing_voice_folder for cleanup: {e}")

        try:
            dirs_to_delete.append(output_parent)
        except Exception as e:
            logger.warning(f"Could not resolve output_folder parent for cleanup: {e}")

        deleted_count = 0
        failed_delete_count = 0
        for dir_path in dirs_to_delete:
            try:
                if not self.gcs_module.is_dir_exists(dir_path=dir_path):
                    logger.debug(f"Directory does not exist, skipping: {dir_path}")
                    deleted_count += 1
                    continue
                logger.debug(f"Deleting directory: {dir_path}")
                self.gcs_module.delete_dir(dir_path=dir_path)
                deleted_count += 1
                logger.debug(f"Deleted directory: {dir_path}")
            except Exception as delete_err:
                logger.error(f"Failed to delete directory '{dir_path}': {delete_err}", exc_info=True)
                failed_delete_count += 1

        logger.info(f"Directory cleanup complete. Deleted: {deleted_count}, Failed: {failed_delete_count}")

    def _insert_log_record(self, df: pd.DataFrame) -> None:
        """Insert transaction log records for all records in a single execution-dated file."""
        logger.info("Starting transaction log record insertion")
        default_type = "AI Fact-Checker"
        default_user_id = "daisyrpa"
        default_source = "SharePoint"

        execution_dt = self.get_package("execution_dt", None)

        try:
            logger.debug("Creating transaction log records")
            self._transaction_log(
                default_type=default_type,
                default_user_id=default_user_id,
                default_source=default_source,
                prediction_df=df,
                execution_dt=execution_dt,
            )
            logger.debug("Transaction log created successfully")
        except Exception as trans_err:
            logger.error(f"Critical error creating transaction log: {trans_err}", exc_info=True)
            raise Exception(f"Transaction log creation failed: {trans_err}") from trans_err

    def _transaction_log(
        self,
        default_type: str,
        default_user_id: str,
        default_source: str,
        prediction_df: pd.DataFrame,
        execution_dt: datetime,
    ) -> pd.DataFrame:
        """
        Create and upload transaction log entries for fact-check batch results.
        Mirrors export_output_result_task._transaction_log with type='AI Fact-Checker'
        and GCS URI as storage_path.
        The log file path is keyed by execution_dt (pipeline run date); data_date
        (voice call date) remains a field value inside the CSV rows.
        """
        logger.debug(f"Processing {len(prediction_df)} records for transaction logging")

        model_pricing = gemini_cost(
            api_type=self.DEFAULT_COST_TYPE, model_list=prediction_df["model_version"].unique().tolist()
        )

        required_columns = [
            "file_name",
            "full_path",
            "folder",
            "record_date",
            "duration",
            "token_input",
            "token_cached",
            "token_output",
            "status",
            "message",
            "processed_time",
            "create_time",
            "model_version",
            "load_dt",
        ]
        missing_columns = [col for col in required_columns if col not in prediction_df.columns]
        if missing_columns:
            logger.error(f"Missing required columns: {missing_columns}")
            raise ValueError(f"Cannot create transaction log: missing columns {missing_columns}")

        log_payload = []
        usage_df = prediction_df[required_columns].copy()
        usage_dict = usage_df.to_dict(orient="records")

        for idx, record in enumerate(usage_dict, 1):
            try:
                if not record.get("file_name"):
                    raise ValueError(f"Record {idx}: Missing required field 'file_name'")

                control_site_name = resolve_env(get_value_by_path(self.control_access, "site_name", ""))
                if not record.get("full_path"):
                    logger.warning(f"Record {idx} ({record['file_name']}): Missing full_path, URL will be incomplete")
                    storage_path = f"https://{self.control_site}/sites/{control_site_name}/"
                else:
                    storage_path = f"https://{self.control_site}/sites/{control_site_name}/{record['full_path']}"

                usage_detail = {
                    record["file_name"]: {
                        "model": record.get("model_version"),
                        "token_input": record.get("token_input"),
                        "token_cached": record.get("token_cached"),
                        "token_output": record.get("token_output"),
                    }
                }

                token_usage_input = 0
                token_usage_output = 0
                for usage in usage_detail.values():
                    for _, token_count in usage.get("token_input", {}).items():
                        if str(token_count).isdigit():
                            token_usage_input += int(token_count)
                    for _, token_count in usage.get("token_output", {}).items():
                        if str(token_count).isdigit():
                            token_usage_output += int(token_count)

                cost_detail = GeminiBatchModule.cal_gemini_cost(
                    usage_detail=usage_detail,
                    cost_config=model_pricing,
                )
                cost_input = cost_detail[record["file_name"]].get("cost_input", 0.0) or 0.0
                cost_output = cost_detail[record["file_name"]].get("cost_output", 0.0) or 0.0
                total_cost_usd = cost_input + cost_output

                duration_val = record.get("duration")
                if duration_val is None:
                    file_metadata_sec = 0
                elif isinstance(duration_val, int):
                    file_metadata_sec = duration_val
                else:
                    file_metadata_sec = safe_cast_value(duration_val, int, 0)

                raw_start = str(record.get("processed_time", ""))
                raw_end = str(record.get("create_time", ""))
                if raw_start and raw_end:
                    try:
                        if datetime.fromisoformat(raw_start) > datetime.fromisoformat(raw_end):
                            raw_start, raw_end = raw_end, raw_start
                    except Exception:
                        pass

                transaction_log = TransactionLogSchema.from_dict(
                    TransactionPayload(
                        data_date=str(record.get("record_date", "")),
                        start_time=raw_start,
                        end_time=raw_end,
                        type=default_type,
                        gcp_project_id=resolve_env(self.gcp.get("project_id", None)),
                        gcp_project_name=resolve_env(self.gcp.get("project_name", None)),
                        user_id=default_user_id,
                        source=default_source,
                        storage_path=storage_path,
                        folder=str(record.get("folder", "")),
                        filename=str(record.get("file_name", "")),
                        file_metadata_sec=file_metadata_sec,
                        status_pass_failed_retry="Pass" if record.get("status") == "SUCCESS" else "Failed",
                        error_log_if=str(record.get("message", "")),
                        token_usage_input=token_usage_input,
                        token_usage_output=token_usage_output,
                        total_cost_usd=safe_cast_value(total_cost_usd, float, 0.0),
                        load_dt=str(record.get("load_dt", "")),
                    )
                )
                if record.get("status") != "SUCCESS":
                    transaction_log.stamp_error(
                        action="Create Transaction Log", error_message=record.get("message", "")
                    )
                else:
                    transaction_log.stamp_completion(action="Create Transaction Log")
                log_payload.append(transaction_log)

            except Exception as record_err:
                logger.error(f"CRITICAL: Record {idx}: {record_err}", exc_info=True)
                raise Exception(f"Transaction log creation failed at record {idx}: {record_err}") from record_err

        logger.info(f"Created {len(log_payload)} transaction payload record(s)")

        if not log_payload:
            raise Exception("No transaction records were created from prediction data")

        # Build DataFrame
        new_transaction_df = pd.DataFrame([log.to_dict() for log in log_payload])
        new_transaction_df = ensure_df_schema(new_transaction_df, Metadata.TRANSACTION_LOG_SCHEMA)
        logger.info(f"Transaction log DataFrame: {len(new_transaction_df)} rows")

        # Upload all records to a single log file keyed by execution_dt (pipeline run date),
        # not by data_date (voice call date). data_date remains a field value inside the CSV.
        try:
            transaction_path = resolve_date(
                text=resolve_env(get_value_by_path(self.sharepoint, "control.transaction_log_file")),
                replace_date=execution_dt,
            )
            logger.info(f"Transaction log path (execution_dt): {transaction_path}")

            if self.sharepoint_control.is_item_exists(item_path=transaction_path):
                logger.info("Existing log found, merging")
                existing_log = self.sharepoint_control.get_item_by_path(transaction_path)
                with io.BytesIO(existing_log.content) as buf:
                    existing_df = pd.read_csv(buf)
                logger.debug(f"Existing log has {len(existing_df)} rows")
                combined_df = pd.concat([existing_df, new_transaction_df], ignore_index=True)
                logger.debug(f"Combined DataFrame has {len(combined_df)} rows")
            else:
                logger.info("No existing log found, creating new")
                combined_df = new_transaction_df

            combined_df["data_date"] = combined_df["data_date"].astype(str)
            combined_df = combined_df.fillna("").sort_values(
                by=["updated_dt", "load_dt", "data_date", "start_time", "end_time"],
                ascending=[False, False, False, False, False],
            )
            combined_df = ensure_df_schema(combined_df, list(new_transaction_df.columns))
            combined_df = replace_nan_with_default(combined_df, default_value="")

            csv_buffer = io.BytesIO()
            combined_df.to_csv(csv_buffer, index=False, encoding="utf-8-sig")
            csv_buffer.seek(0)

            self.sharepoint_control.upload_file(
                upload_path=transaction_path,
                content=csv_buffer.read(),
            )
            logger.info(f"Successfully uploaded transaction log to {transaction_path}")
        except Exception as upload_err:
            logger.error(f"Failed to upload transaction log: {upload_err}", exc_info=True)
            raise Exception(f"Critical error: Cannot upload transaction log: {upload_err}") from upload_err

        return new_transaction_df
