# Library imports
import asyncio
import io
import json
import os
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.core.task_interface import TaskInterface

# Source code imports
from src.core.task_registry import task_registry
from src.modules.audit_log.batch_processing_log import BatchProcessingLogSchema, BatchProcessingPayload
from src.modules.google.gcs import GCSModule
from src.modules.microsoft.sharepoint import SharePointModule
from src.utils.common import (
    get_value_by_path,
    resolve_date,
    resolve_env,
    safe_list_get,
)
from src.utils.date_utils import (
    add_date,
    is_format_datetime,
    list_date,
)
from src.utils.file_utils import (
    load_yaml,
    read_file,
)
from src.utils.logger import Logger
from src.utils.pydantic_utils import pydantic_resolve_refs, sanitize_json_schema_enums
from tasks.sentiment_telesale.output_validation.validate_mapping import ValidationMapping
from tasks.sentiment_telesale.schemas.agent_master_schema import AgentMasterSchema
from tasks.sentiment_telesale.schemas.check_list_schema import (
    CheckListCategorySchema,
    CheckListSchemaSubcategoriesSchema,
    CheckListTagsSchema,
)

logger = Logger(__name__)


@task_registry.register("TelesalePrepPayloadTask")
class PrepPayloadTask(TaskInterface):
    """
    Task to prepare the payload for Gemini Batch API for sentiment analysis.
    Hints:
        - Merges system and user prompts for Gemini input.
        - Prepares payloads based on voice files in GCS for specified date range.
        - Path files must consist of YYYYMM/YYYYMMDD format for lookback processing.
    """

    COMMON_CONFIG_PATH = "config/common.yml"
    DEFAULT_PAYLOAD_FILE = "payloads.jsonl"
    DEFAULT_MASTER_SHEET_NAME = "list"
    check_list_mapping = {
        "Categories": CheckListCategorySchema,
        "Subcategories": CheckListSchemaSubcategoriesSchema,
        "Tags": CheckListTagsSchema,
    }

    def __init__(self, **kwargs):
        """
        Initialize the PrepPayloadTask with configuration parameters.
        """
        super().__init__(**kwargs)

        # Load parameters from configuration
        self.gcs = self.get_config("gcs", {})
        self.vertexai = self.get_config("vertexai", {})
        self.sharepoint = self.get_config("sharepoint", {})
        self.framework = self.get_config("framework", {})

        common_config = load_yaml(self.COMMON_CONFIG_PATH)
        self.verint_access = common_config.get("verint", {})
        self.control_access = common_config.get("control", {})

        # Caches for dynamic validation model construction
        self._cache_literal_checklist = {}
        self._cache_mapping_dict = {}

        # Backup for rollback or reference if needed
        self._backup_prompt_dict = {}

        # Meaning: _IN. -> INBOUND, _OUT. -> OUTBOUND
        # Filter from file name
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

    def pre_execute(self):
        """
        Pre-execution setup: Initialize modules and connections.
        """
        logger.debug("Initializing modules")

        # Initialize SharePoint Control module
        try:
            control_site = resolve_env(self.control_access.get("site_domain"))
            self.sharepoint_control = SharePointModule(
                client_id=resolve_env(self.control_access.get("client_id")),
                client_secret=resolve_env(self.control_access.get("client_secret")),
                tenant_id=resolve_env(self.control_access.get("tenant_id")),
                site_domain=control_site,
                site_path=resolve_env(self.control_access.get("site_path")),
            )
            logger.debug(f"SharePoint Control module initialized: {control_site}")
        except Exception as e:
            logger.error(f"Failed to initialize SharePoint Control module: {e}", exc_info=True)
            raise

        try:
            verint_site = resolve_env(self.verint_access.get("site_domain"))
            self.sharepoint_verint = SharePointModule(
                client_id=resolve_env(self.verint_access.get("client_id")),
                client_secret=resolve_env(self.verint_access.get("client_secret")),
                tenant_id=resolve_env(self.verint_access.get("tenant_id")),
                site_domain=verint_site,
                site_path=resolve_env(self.verint_access.get("site_path")),
            )
            logger.debug(f"SharePoint Verint module initialized: {verint_site}")
        except Exception as e:
            logger.error(f"Failed to initialize SharePoint Verint module: {e}", exc_info=True)
            raise

        # Initialize GCS module
        try:
            project_id = resolve_env(self.gcs.get("project_id"))
            bucket_name = resolve_env(self.gcs.get("bucket_name"))
            self.gcs_module = GCSModule(
                project_id=project_id,
                bucket_name=bucket_name,
            )
            logger.debug(f"GCS module initialized: {project_id}/{bucket_name}")
        except Exception as e:
            logger.error(f"Failed to initialize GCS module: {e}", exc_info=True)
            raise

    def execute_task(self) -> Any:
        """
        Main execution logic for uploading voice data.
        Returns:
            Any: Payload path and output path tuple
        """
        execution_dt = self.get_package("execution_dt", None)
        rerun_date = self.get_package("rerun_data_dt", None)

        datadate = rerun_date or execution_dt.strftime("%Y-%m-%d")

        try:
            end_date = add_date(datadate, -1).strftime("%Y%m%d")
            start_date = add_date(datadate, -int(resolve_env(self.framework["lookback_days"]))).strftime("%Y%m%d")
            logger.info(f"Preparing payload for date range: {start_date} to {end_date}")
        except Exception as e:
            logger.error(f"Failed to calculate date range: {e}", exc_info=True)
            raise Exception(f"Cannot determine processing date range: {e}") from e

        # Prepare payload
        try:
            payload_path, output_path, processing_log_list = self._prepare_payload(start_date, end_date, execution_dt)
            logger.info(f"Payload prepared - Source: {payload_path}, Output: {output_path}")
        except Exception as e:
            logger.error(f"Failed to prepare payload: {e}", exc_info=True)
            raise Exception(f"Cannot prepare batch payload: {e}") from e

        return payload_path, output_path, processing_log_list

    def _prepare_payload(self, start_date: str, end_date: str, execution_dt: datetime) -> tuple:
        """
        Prepare the batch payload for Gemini Batch API.
        Parameters:
            start_date (str): Start date in 'YYYYMMDD'
            end_date (str): End date in 'YYYYMMDD'
            execution_dt (datetime): Execution datetime
        Returns:
            tuple: (payload_path (str), output_batch_path (str), processing_log_list (list))
        """
        try:
            processing_dates = list_date(
                start_date=start_date, end_date=end_date, input_date_format="%Y%m%d", output_date_format="%Y-%m-%d"
            )
            logger.debug(f"Processing {len(processing_dates)} dates: {processing_dates}")
        except Exception as e:
            logger.error(f"Failed to generate processing dates: {e}", exc_info=True)
            raise Exception(f"Cannot generate date list: {e}") from e

        gemini_generation_config = get_value_by_path(self.vertexai, "generation_config", {})
        logger.debug(f"Generation config: {gemini_generation_config}")

        payloads = []
        mapping_file_type = {
            ".txt": "text/plain",
            ".wav": "audio/wav",
            ".jsonl": "application/jsonl",
        }

        try:
            processing_batch_path = resolve_date(
                text=resolve_env(self.gcs.get("processing_batch_folder")), replace_date=execution_dt
            )
            output_batch_path = resolve_date(text=resolve_env(self.gcs.get("output_folder")), replace_date=execution_dt)
            payload_path = processing_batch_path + "/" + self.DEFAULT_PAYLOAD_FILE
            logger.debug(f"Batch paths - Processing: {processing_batch_path}, Output: {output_batch_path}")
        except Exception as e:
            logger.error(f"Failed to resolve batch paths: {e}", exc_info=True)
            raise Exception(f"Cannot resolve GCS paths: {e}") from e

        # For collecting log processing
        processing_log_list = []

        for date in processing_dates:
            logger.debug(f"Processing date: {date}")

            try:
                input_voice_path = resolve_date(text=self.gcs.get("input_folder"), replace_date=date)
                processing_voice_path = resolve_date(text=self.gcs.get("processing_voice_folder"), replace_date=date)
            except Exception as e:
                logger.error(f"Failed to resolve paths for date {date}: {e}")
                continue

            # Check if input folder exists
            try:
                if not self.gcs_module.is_dir_exists(input_voice_path):
                    logger.warning(f"GCS input folder does not exist: {input_voice_path} for date {date}")
                    continue
            except Exception as e:
                logger.error(f"Error checking directory existence for {input_voice_path}: {e}")
                continue

            # List input files
            try:
                input_gcs_files = self.gcs_module.list_files(prefix=input_voice_path)
                if len(input_gcs_files) == 0:
                    logger.debug(f"No files in {input_voice_path} for {date}")
                    continue
                logger.debug(f"Found {len(input_gcs_files)} files for {date}")
            except Exception as e:
                logger.error(f"Failed to list files in {input_voice_path}: {e}", exc_info=True)
                continue

            # Copy files to processing folder
            try:
                copy_result = asyncio.run(
                    self.gcs_module.copy_files_batch(
                        source_files=input_gcs_files,
                        destination_prefix=processing_voice_path,
                        max_concurrent_copies=int(resolve_env(self.framework["concurrency_upload"])),
                    )
                )

                if copy_result["failed"] > 0:
                    logger.warning(
                        f"Date {date}: {copy_result['success']} files copied, {copy_result['failed']} failed - {copy_result.get('errors', [])}"
                    )
                else:
                    logger.debug(f"Copied {copy_result['success']} files for {date}")
            except Exception as e:
                logger.error(f"Failed to copy files for {date}: {e}", exc_info=True)
                continue

            # List processing files
            try:
                processing_gcs_files = self.gcs_module.list_files(prefix=processing_voice_path)
                if len(processing_gcs_files) == 0:
                    logger.warning(f"No files in processing folder for {date}")
                    continue
            except Exception as e:
                logger.error(f"Failed to list processing files for {date}: {e}", exc_info=True)

            # Load prompt mappings
            try:
                prompt_mappings = self._mapping_prompt_template(processing_gcs_files)
            except Exception as e:
                logger.error(f"Failed to load prompt mappings: {e}", exc_info=True)
                raise Exception(f"Cannot load prompt mappings: {e}") from e

            # Zip the backup prompt and upload to GCS for reference or rollback if needed
            try:
                with io.BytesIO() as zip_buffer:
                    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                        for filename, content in self._backup_prompt_dict.items():
                            zf.writestr(filename, content)
                    zip_binary = zip_buffer.getvalue()
                backup_file = resolve_date(text=self.gcs.get("backup_prompt_file"), replace_date=execution_dt)
                self.gcs_module.update_content_to_gcs(
                    content=zip_binary, mime_type="application/zip", destination_path=backup_file
                )
            except Exception as e:
                logger.warning(f"Failed to create/upload backup prompt zip: {e}", exc_info=True)

            # Rebuild validate_mapping with dynamic Literal types based on active tags from Excel
            for skill_code, tag_codes in self._cache_literal_checklist.items():
                campaign = self._cache_mapping_dict.get(str(skill_code))
                if campaign and campaign in self.validate_mapping and tag_codes:
                    _, builder_fn = self.validate_mapping[campaign]
                    self.validate_mapping[campaign] = (builder_fn(tag_codes), builder_fn)
                    logger.debug(f"Rebuilt validation model for '{campaign}' with {len(tag_codes)} active tags")

            files_skipped = 0
            files_added = 0
            for processing_file in processing_gcs_files:
                try:
                    file_uri = f"gs://{self.gcs_module.bucket_name}/{processing_file}"
                    action = "PREP_PAYLOAD_BATCH_PROCESSING"
                    processing_log = BatchProcessingLogSchema.from_dict(
                        BatchProcessingPayload(
                            data_date=date,
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
                                        {"text": prompt_mappings[processing_file][0]},
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
                except KeyError:
                    logger.warning(f"No prompt mapping found for file: {processing_file}")
                    processing_log.stamp_error(
                        action=action, error_message=f"No prompt mapping found for file: {processing_file}"
                    )
                    processing_log_list.append(processing_log)
                    files_skipped += 1
                except Exception as e:
                    logger.error(f"Failed to create payload for {os.path.basename(processing_file)}: {e}")
                    processing_log.stamp_error(action=action, error_message=f"Failed to create payload: {e}")
                    processing_log_list.append(processing_log)
                    files_skipped += 1
            if files_added > 0:
                logger.info(
                    f"Date {date}: Created {files_added} payloads"
                    + (f", skipped {files_skipped}" if files_skipped > 0 else "")
                )
            elif files_skipped > 0:
                logger.warning(f"Date {date}: All {files_skipped} files skipped")

        # Upload merged payload
        if len(payloads) == 0:
            logger.warning("No payloads created")
            return None, None, None

        try:
            merged_payload = ("\n".join(json.dumps(line, ensure_ascii=False) for line in payloads)).encode("utf-8")

            self.gcs_module.update_content_to_gcs(
                content=merged_payload, mime_type=mapping_file_type[".jsonl"], destination_path=payload_path
            )

            logger.info(f"Uploaded {len(payloads)} payloads ({len(merged_payload) / 1024:.2f} KB) to {payload_path}")
        except Exception as e:
            logger.error(f"Failed to upload payload to {payload_path}: {e}", exc_info=True)
            raise Exception(f"Cannot upload payload to GCS: {e}") from e

        return payload_path, output_batch_path, processing_log_list

    def _build_checklist_text(
        self,
        category_df: pd.DataFrame,
        subcategory_df: pd.DataFrame,
        tags_df: pd.DataFrame,
    ) -> str:
        """
        Convert checklist dataframes to structured text for LLM consumption.
        Produces hierarchical text: main categories → sub-categories (with sections) → tags.
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

    def _mapping_prompt_template(self, file_list: list) -> dict:
        """
        Map prompt templates to files based on agent groups.
        Critical method that handles prompt template mapping and loading.

        Parameters:
            file_list (list): List of file paths to process

        Returns:
            dict: Dictionary mapping file names to prompt templates

        Raises:
            Exception: If critical operations fail (agent mapping load, prompt load, etc.)
        """
        logger.debug(f"Mapping prompt templates for {len(file_list)} files")

        # Step 1: Load prompts — system prompt (local), common prompt + check-list Excel (SharePoint)
        try:
            system_prompt_file = self.framework.get("system_prompt_file")
            logger.debug(f"Loading system prompt file: {system_prompt_file}")
            system_prompt = read_file(system_prompt_file)
            # Backup raw content for potential rollback or reference
            self._backup_prompt_dict[os.path.basename(system_prompt_file)] = system_prompt.encode("utf-8")
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
            # Backup raw content for potential rollback or reference
            self._backup_prompt_dict[os.path.basename(common_prompt_file)] = common_prompt.encode("utf-8")
            if not common_prompt.strip():
                logger.error(f"Common prompt file is empty: {common_prompt_file}")
                raise ValueError("Common prompt file is empty or invalid")

            check_list_prompt_file = resolve_env(get_value_by_path(self.sharepoint, "control.check_list_prompt_file"))
            if not self.sharepoint_control.is_item_exists(check_list_prompt_file):
                logger.error(f"Check list prompt file not found in SharePoint: {check_list_prompt_file}")
                raise FileNotFoundError(f"Check list prompt file not found: {check_list_prompt_file}")

            excel_sheet_cache = {}
            check_list_content = self.sharepoint_control.get_item_by_path(check_list_prompt_file).content
            # Backup raw content for potential rollback or reference
            self._backup_prompt_dict[os.path.basename(check_list_prompt_file)] = check_list_content
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

        # Step 2: Extract agent IDs and record dates from file names
        try:
            logger.debug("Extracting agent metadata from file names")
            file_path_splits = [(f, os.path.basename(f).split("_")) for f in file_list]
            file_metadata_list = [
                {
                    "file_path": f[0],
                    "agent_id": safe_list_get(f[1], 3, None),
                    "record_date": safe_list_get(f[1], -3, None),
                }
                for f in file_path_splits
            ]
            file_metadata_df = pd.DataFrame(file_metadata_list).astype({"agent_id": str, "record_date": str})

            not_found_agent_id = file_metadata_df[file_metadata_df["agent_id"].isnull()]["file_path"].tolist()
            if not_found_agent_id:
                logger.warning(
                    f"Cannot extract agent_id from {len(not_found_agent_id)} file(s) - missing agent_id at position 4"
                )
                for file in not_found_agent_id[:3]:  # Show only first 3
                    logger.warning(f"  - {os.path.basename(file)}")
                if len(not_found_agent_id) > 3:
                    logger.warning(f"  ... and {len(not_found_agent_id) - 3} more")

            not_found_record_date = file_metadata_df[file_metadata_df["record_date"].isnull()]["file_path"].tolist()
            if not_found_record_date:
                logger.warning(
                    f"Cannot extract record_date from {len(not_found_record_date)} file(s) - missing record_date at position -3"
                )
                for file in not_found_record_date[:3]:  # Show only first 3
                    logger.warning(f"  - {os.path.basename(file)}")
                if len(not_found_record_date) > 3:
                    logger.warning(f"  ... and {len(not_found_record_date) - 3} more")

            file_metadata_df = file_metadata_df[
                file_metadata_df["agent_id"].notnull() & file_metadata_df["record_date"].notnull()
            ]

            if file_metadata_df.empty:
                logger.warning("No valid metadata extracted from file names - all files will be skipped")
                return {}

            _valid_date_formats = ["%Y%m%d", "%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"]
            _valid_date_mask = file_metadata_df["record_date"].apply(
                lambda d: any(is_format_datetime(d, fmt) for fmt in _valid_date_formats)
            )
            invalid_date_files = file_metadata_df[~_valid_date_mask]["file_path"].tolist()
            if invalid_date_files:
                logger.warning(f"Cannot parse record_date from {len(invalid_date_files)} file(s) - invalid date format")
                for file in invalid_date_files[:3]:
                    logger.warning(f"  - {os.path.basename(file)}")
                if len(invalid_date_files) > 3:
                    logger.warning(f"  ... and {len(invalid_date_files) - 3} more")
            file_metadata_df = file_metadata_df[_valid_date_mask]

            master_folder = resolve_env(get_value_by_path(self.sharepoint, "verint.master_folder"))
            master_pattern = resolve_env(get_value_by_path(self.sharepoint, "verint.master_pattern"))
            # Pre-build lookup per unique record_date to avoid N+1 SharePoint calls inside apply()
            unique_record_dates = file_metadata_df["record_date"].unique()
            date_master_file_map = {
                d: safe_list_get(
                    sorted(
                        self.sharepoint_verint.list_files_pattern(
                            resolve_date(text=master_folder, replace_date=d), master_pattern
                        ),
                        reverse=True,
                    ),
                    0,
                    None,
                )
                for d in unique_record_dates
            }
            file_metadata_df["master_file"] = file_metadata_df["record_date"].map(date_master_file_map)

            if file_metadata_df.empty:
                logger.warning("No valid metadata extracted from file names - all files will be skipped")
                return {}

            logger.info(f"Extracted metadata from {len(file_metadata_df)}/{len(file_list)} files")
        except Exception as e:
            logger.error(f"Failed to process file metadata: {e}", exc_info=True)
            raise Exception(f"Critical error processing file metadata: {e}") from e

        # Step 3: Load agent mapping from SharePoint
        try:
            agent_master_df = pd.DataFrame()
            master_files_to_load = file_metadata_df[["master_file", "record_date"]].drop_duplicates()
            logger.info(f"Loading {len(master_files_to_load)} agent mapping file(s) from SharePoint")

            for idx, row in enumerate(master_files_to_load.itertuples(), 1):
                logger.debug(f"Loading master file {idx}/{len(master_files_to_load)}: {row.master_file}")

                if not self.sharepoint_verint.is_item_exists(row.master_file):
                    logger.warning(f"Agent mapping file not found: {row.master_file} - skipping this date's files")
                    continue

                try:
                    res = self.sharepoint_verint.get_item_by_path(row.master_file)
                except Exception as fetch_err:
                    logger.warning(f"Failed to fetch agent mapping file {row.master_file}: {fetch_err} - skipping")
                    continue
                try:
                    with io.BytesIO(res.content) as buffer:
                        loaded_agent_master_df = pd.read_excel(
                            buffer, sheet_name=self.DEFAULT_MASTER_SHEET_NAME, engine="openpyxl"
                        )
                        loaded_agent_master_df = AgentMasterSchema.validate(loaded_agent_master_df)
                        initial_count = len(loaded_agent_master_df)
                        loaded_agent_master_df = loaded_agent_master_df.dropna(
                            subset=["emp_id", "commission_skill_code"]
                        )
                        dropped_count = initial_count - len(loaded_agent_master_df)

                        if loaded_agent_master_df.empty:
                            logger.warning(f"Agent mapping file has no valid records: {row.master_file} - skipping")
                            continue

                    if dropped_count > 0:
                        logger.warning(
                            f"Dropped {dropped_count}/{initial_count} rows with null values from {os.path.basename(row.master_file)}"
                        )

                    loaded_agent_master_df["updatedate"] = loaded_agent_master_df["updatedate"].dt.strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                    loaded_agent_master_df["record_date"] = row.record_date
                    loaded_agent_master_df["ranking"] = (
                        loaded_agent_master_df.sort_values(by=["updatedate"], ascending=False)
                        .groupby("emp_id")
                        .cumcount()
                        + 1
                    )
                    loaded_agent_master_df = loaded_agent_master_df[loaded_agent_master_df["ranking"] == 1].astype(
                        {"emp_id": str, "commission_skill_code": str, "record_date": str}
                    )
                    agent_master_df = pd.concat([agent_master_df, loaded_agent_master_df], ignore_index=True)
                    logger.debug(
                        f"Loaded {len(loaded_agent_master_df)} unique agents from {os.path.basename(row.master_file)}"
                    )
                except Exception as parse_err:
                    logger.warning(f"Failed to parse agent mapping file {row.master_file}: {parse_err} - skipping")
                    continue

            if agent_master_df.empty:
                logger.warning("No agent mappings loaded from any file - all files will be skipped")
                return {}

            logger.info(f"Loaded {len(agent_master_df)} total agent mappings")
        except Exception as e:
            logger.error(f"Failed to process agent mapping file: {e}", exc_info=True)
            logger.warning("Continuing with empty agent mapping")
            agent_master_df = pd.DataFrame()

        # Step 4: Merge file metadata with agent mapping
        try:
            logger.debug("Merging file metadata with agent mapping")
            file_agent_df = file_metadata_df.merge(
                agent_master_df, how="left", left_on=["agent_id", "record_date"], right_on=["emp_id", "record_date"]
            )
            unmatched_agents = file_agent_df[file_agent_df["commission_skill_code"].isnull()][
                ["file_path", "agent_id", "record_date"]
            ].to_dict(orient="records")
            if len(unmatched_agents) > 0:
                logger.warning(f"{len(unmatched_agents)} agent(s) not found in mapping")
                for agent in unmatched_agents[:3]:  # Show only first 3
                    logger.warning(f"  - Agent ID: {agent['agent_id']}, Date: {agent['record_date']}")
                if len(unmatched_agents) > 3:
                    logger.warning(f"  ... and {len(unmatched_agents) - 3} more")

            unique_skill_codes = (
                file_agent_df[["commission_skill_code"]].dropna().drop_duplicates().to_dict(orient="records")
            )

            if not unique_skill_codes:
                logger.warning("No valid agent groups found after mapping - all files will be skipped")
                return {}

            logger.info(f"Identified {len(unique_skill_codes)} unique agent group(s)")
        except Exception as e:
            logger.error(f"Failed to merge dataframes: {e}", exc_info=True)
            raise Exception(f"Critical error during dataframe merge operations: {e}") from e

        # Step 5: Final merge — attach prompt template per file
        try:
            logger.debug("Creating final file-to-prompt mapping")
            file_prompt_df = file_agent_df.merge(
                skill_prompt_df, how="left", left_on="commission_skill_code", right_on="commission_skill_code"
            )
            file_prompt_df = file_prompt_df[["file_path", "commission_skill_code", "prompt_template"]]

            files_without_prompt_df = file_prompt_df[file_prompt_df["prompt_template"].isnull()]
            if not files_without_prompt_df.empty:
                logger.warning(f"{len(files_without_prompt_df)} file(s) have no prompt template assigned")
                for _, row in files_without_prompt_df.head(3).iterrows():  # Show only first 3
                    logger.warning(f"  - {os.path.basename(row['file_path'])}")
                if len(files_without_prompt_df) > 3:
                    logger.warning(f"  ... and {len(files_without_prompt_df) - 3} more")

            prompt_records = file_prompt_df.replace({np.nan: None}).to_dict(orient="records")
            file_prompt_map = {
                item["file_path"]: (item["prompt_template"], item["commission_skill_code"]) for item in prompt_records
            }

            if not file_prompt_map:
                logger.warning("No files successfully mapped to prompts - all files will be skipped")
                return {}

            valid_mappings = sum(1 for v in file_prompt_map.values() if v[0] is not None)
            logger.info(f"Successfully mapped {valid_mappings}/{len(file_prompt_map)} files to prompts")

            return file_prompt_map

        except Exception as e:
            logger.error(f"Failed to create final prompt mapping: {e}", exc_info=True)
            raise Exception(f"Critical error creating final prompt mapping: {e}") from e

    def post_execute(self, result=None):
        """
        Post-execution cleanup: Remove empty directory markers in GCS.
        Parameters:
            result (Any): Result from execute_task
        Returns:
            Any: The same result passed in
        """
        logger.debug("Post-Execution: Cleanup phase")
        try:
            logger.debug("Cleaning up empty directory markers in GCS...")
            self.gcs_module.cleanup_empty_dir_markers(prefix="")
            logger.info("Post-execution cleanup completed successfully")
        except Exception as e:
            logger.warning(f"Post-execution cleanup failed: {e}", exc_info=True)
            logger.warning("Skipping cleanup due to error")

        return result
