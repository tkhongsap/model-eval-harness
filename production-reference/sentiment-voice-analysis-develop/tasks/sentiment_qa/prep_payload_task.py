# Library imports
import asyncio
import io
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from src.core.task_interface import TaskInterface

# Source code imports
from src.core.task_registry import task_registry
from src.modules.audit_log.batch_processing_log import BatchProcessingLogSchema, BatchProcessingPayload
from src.modules.google.gcs import GCSModule
from src.modules.microsoft.msgraph import MSGraphModule
from src.modules.microsoft.sharepoint import SharePointModule
from src.utils.common import (
    get_value_by_path,
    resolve_date,
    resolve_env,
    safe_list_get,
)
from src.utils.date_utils import add_date, get_current_datetime, list_date
from src.utils.file_utils import (
    load_yaml,
    read_file,
    read_xlsx,
)
from src.utils.logger import Logger

logger = Logger(__name__)


@task_registry.register("QAPrepPayloadTask")
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
    MASTER_SCHEMA = ["emp_id", "commission_skill_code", "updatedate"]
    MASTER_DTYPE = {"emp_id": str, "commission_skill_code": str, "commission_skill": str}
    MASTER_PARSE_DATES = ["updatedate"]

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
        self.msgraph_access = common_config.get("msgraph", {})

        self.project_id = resolve_env(self.gcs.get("project_id", ""))

        self.msgraph_sender_email = resolve_env(self.msgraph_access.get("sender_email", ""))
        self.msgraph_receiver_email = resolve_env(self.msgraph_access.get("receiver_email", ""))
        self.msgraph_cc_email = resolve_env(self.msgraph_access.get("cc_email", ""))

        input_folder_list_inbound = resolve_env(
            get_value_by_path(self.sharepoint, "verint.input_folder_list_inbound", "")
        )
        input_folder_list_outbound = resolve_env(
            get_value_by_path(self.sharepoint, "verint.input_folder_list_outbound", "")
        )

        self.input_folder_list_inbound = [folder.strip() for folder in input_folder_list_inbound.split(",")]
        self.input_folder_list_outbound = [folder.strip() for folder in input_folder_list_outbound.split(",")]
        self.combined_folder_list = list(set(self.input_folder_list_inbound + self.input_folder_list_outbound))

    def pre_execute(self):
        """
        Pre-execution setup: Initialize modules and connections.
        """
        logger.info("Initializing modules")

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

        # Initialize Microsoft Graph client for email notifications
        try:
            self.msgraph_module = MSGraphModule(
                tenant_id=resolve_env(self.msgraph_access.get("tenant_id")),
                client_id=resolve_env(self.msgraph_access.get("client_id")),
                client_secret=resolve_env(self.msgraph_access.get("client_secret")),
            )
            logger.debug("Microsoft Graph client initialized for email notifications")
        except Exception as e:
            logger.error(f"Failed to initialize Microsoft Graph client: {e}", exc_info=True)
            raise

    def execute_task(self) -> Any:
        """
        Main execution logic for uploading voice data.
        Returns:
            Any: Payload path and output path tuple
        """

        execution_dt = self.get_package("execution_dt", None)

        self.rerun_start_date = self.get_package("start_data_dt", None)
        self.rerun_end_date = self.get_package("end_data_dt", None)

        try:
            if self.rerun_start_date and self.rerun_end_date:
                end_date = add_date(self.rerun_end_date, 0).strftime("%Y%m%d")
                start_date = add_date(self.rerun_start_date, 0).strftime("%Y%m%d")
            else:
                datadate = execution_dt.strftime("%Y-%m-%d")
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

        # Load prompt
        try:
            prompt_inbound = self._prepare_prompt(execution_dt, "user_prompt_inbound")
            prompt_outbound = self._prepare_prompt(execution_dt, "user_prompt_outbound")
        except Exception as e:
            logger.error(f"Failed to load prompt mappings: {e}", exc_info=True)
            raise Exception(f"Cannot load prompt mappings: {e}") from e

        for date in processing_dates:
            logger.debug(f"Processing date: {date}")

            # Load prompt
            prompt_inbound = prompt_inbound.replace("{date}", date)
            prompt_outbound = prompt_outbound.replace("{date}", date)

            for input_folder in self.combined_folder_list:
                try:
                    input_voice_path = (
                        resolve_date(text=self.gcs.get("input_folder"), replace_date=date) + "/" + input_folder
                    )
                    processing_voice_path = (
                        resolve_date(text=self.gcs.get("processing_voice_folder"), replace_date=date)
                        + "/"
                        + input_folder
                    )
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
                    on_input_files = self.gcs_module.list_files(prefix=input_voice_path)
                    if len(on_input_files) == 0:
                        logger.debug(f"No files in {input_voice_path} for {date}")
                        continue
                    logger.debug(f"Found {len(on_input_files)} files for {date}")
                except Exception as e:
                    logger.error(f"Failed to list files in {input_voice_path}: {e}", exc_info=True)
                    continue

                # Copy files to processing folder
                try:
                    copy_result = asyncio.run(
                        self.gcs_module.copy_files_batch(
                            source_files=on_input_files,
                            destination_prefix=processing_voice_path,
                            max_concurrent_copies=int(resolve_env(self.framework["concurrency_upload"])),
                        )
                    )

                    if copy_result["failed"] > 0:
                        logger.warning(
                            f"Date {date}: {copy_result['success']} files copied, {copy_result['failed']} failed - "
                            f"{copy_result.get('errors', [])}"
                        )
                    else:
                        logger.debug(f"Copied {copy_result['success']} files for {date}")
                except Exception as e:
                    logger.error(f"Failed to copy files for {date}: {e}", exc_info=True)
                    continue

                # List processing files
                try:
                    on_prcoess_files = self.gcs_module.list_files(prefix=processing_voice_path)
                    if len(on_prcoess_files) == 0:
                        logger.warning(f"No files in processing folder for {date}")
                        continue
                except Exception as e:
                    logger.error(f"Failed to list processing files for {date}: {e}", exc_info=True)

                files_skipped = 0
                files_added = 0

                output_schema = self._get_analysis_schema()

                for processing_file in on_prcoess_files:
                    try:
                        file_uri = f"gs://{self.gcs_module.bucket_name}/{processing_file}"
                        action = "PREP_PAYLOAD_BATCH_PROCESSING"
                        file_name = os.path.basename(processing_file)
                        file_name_without_ext = file_name.split(".")[0]
                        processing_log = BatchProcessingLogSchema.from_dict(
                            BatchProcessingPayload(
                                data_date=date,
                                gcp_project_id=self.gcs_module.project_id,
                                gcp_project_name=self.gcs_module.project_id,
                                gcs_bucket_name=self.gcs_module.bucket_name,
                                source_path=file_uri,
                                filename=file_name,
                                prediction_payload_path=None,
                            )
                        )
                        call_direction = safe_list_get(file_name_without_ext.split("_"), 9, None)

                        if call_direction == "IN":
                            prompt = prompt_inbound
                        elif call_direction == "OUT":
                            prompt = prompt_outbound
                        else:
                            raise ValueError(
                                f"Invalid call direction '{call_direction}' extracted from filename '{file_name}'. "
                                f"Expected 'IN' or 'OUT'."
                            )

                        file_extension = Path(processing_file).suffix.lower()
                        mime_type = mapping_file_type.get(file_extension)

                        temp_generation_config = gemini_generation_config.copy()

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
                                            {"text": prompt},
                                            {"fileData": {"fileUri": file_uri, "mimeType": mime_type}},
                                        ],
                                    }
                                ],
                                "generationConfig": output_schema,
                            }
                        }
                        payload["request"]["generationConfig"].update(temp_generation_config)
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

    def _prepare_prompt(self, execution_dt: datetime, sheet_name: str) -> str:
        """
        Prepare the prompt by replacing placeholders with actual values.
        Parameters:
            execution_dt (datetime): The execution date to be included in the prompt
            sheet_name (str): The name of the sheet to read from the Excel file
        Returns:
            str: The prepared prompt with placeholders replaced
        """
        try:
            # Step 1: Load system prompt template
            system_prompt = read_file(resolve_env(self.framework.get("system_prompt_file")))

            try:
                # Try downloading existing XLSX
                user_config_path = resolve_env(self.sharepoint.get("control").get("user_config_path"))
                master_file = self.sharepoint_control.get_item_by_path(item_path=user_config_path)
                master_file_bytes = master_file.content
                with io.BytesIO(master_file_bytes) as existing_file:
                    user_config_df = pd.read_excel(existing_file, header=0, sheet_name=sheet_name)
                with io.BytesIO(master_file_bytes) as existing_file:
                    standard_gsd_df = pd.read_excel(existing_file, header=0, sheet_name="standard_gsd")

                try:
                    backup_excel_prompt_path = resolve_date(
                        text=resolve_env(self.gcs.get("backup_excel_prompt_path")), replace_date=execution_dt
                    )

                    asyncio.run(
                        self.gcs_module.upload_sharepoint_to_gcs(
                            sharepoint_object=self.sharepoint_control,
                            stream_list=[
                                {
                                    "download": user_config_path,
                                    "upload": backup_excel_prompt_path,
                                    "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                }
                            ],
                            max_concurrent_uploads=1,
                        )
                    )

                    logger.info("Successfully uploaded the Excel prompt to GCS for backup")
                except Exception as e:
                    logger.error(f"Failed to upload the Excel prompt to GCS: {e}", exc_info=True)
            except Exception as e:
                logger.error(
                    f"Failed to read user config excel file from SharePoint: {e}, falling back to local file",
                    exc_info=True,
                )
                user_config_path = resolve_env(self.framework.get("user_config_path"))
                logger.info(f"Read file from config: {user_config_path}")
                user_config_df = read_xlsx(user_config_path, sheet_name=sheet_name)
                standard_gsd_df = read_xlsx(user_config_path, sheet_name="standard_gsd")

            standard_gsd_df["standard_gsd_name"] = standard_gsd_df["standard_gsd_name"].str.strip()
            standard_gsd_df["definition"] = standard_gsd_df["definition"].str.strip()

            grouped = standard_gsd_df.groupby("standard_gsd_name")["definition"].apply(list)

            standard_gsd_lines = []
            for name, definitions in grouped.items():
                standard_gsd_lines.append(f"    - '{name}'")
                for definition in definitions:
                    sub_lines = definition.split("\n")

                    for sub_line in sub_lines:
                        if sub_line.strip():
                            standard_gsd_lines.append(f"        - {sub_line.strip()}")
            standard_gsd_lines = "\n".join(standard_gsd_lines)

            user_prompt = ""
            for _index, row in user_config_df.iterrows():
                no_cate = row["no_cate"]
                category = row["category"]
                sub_category = row["sub_category"]
                rule_and_logic = row["rule_and_logic"]

                if not pd.isna(no_cate):
                    no_cate = int(no_cate) - 1
                    if no_cate > 0:
                        user_prompt += f"### **Category {no_cate}.: `{category}`** \n\n"

                if sub_category == "issue_type":
                    user_prompt += f"**`{sub_category}`** (**empty string if issue not relate to network)\n"
                    user_prompt += f"{rule_and_logic}\n\n"
                elif sub_category == "standard_gsd_name":
                    user_prompt += f"**`{sub_category}`** \n"
                    user_prompt += f"{rule_and_logic}\n"
                    user_prompt += f"{standard_gsd_lines}\n\n"
                else:
                    user_prompt += f"**`{sub_category}`** \n"
                    user_prompt += f"{rule_and_logic}\n\n"

            return (
                system_prompt.replace("{user_prompt}", user_prompt)
                .replace("company_verification", "customer_verification")
                .replace("`self_service`", "`true_application`")
            )
        except Exception as e:
            logger.error(f"Failed to prepare prompt: {e}", exc_info=True)
            raise Exception(f"Cannot prepare prompt: {e}") from e

    def post_execute(self, result=None):
        """
        Post-execution cleanup: Remove empty directory markers in GCS.
        Parameters:
            result (Any): Result from execute_task
        Returns:
            Any: The same result passed in
        """
        logger.info("Post-Execution: Cleanup phase")
        try:
            logger.debug("Cleaning up empty directory markers in GCS...")
            self.gcs_module.cleanup_empty_dir_markers(prefix="")
            logger.info("Post-execution cleanup completed successfully")
        except Exception as e:
            logger.warning(f"Post-execution cleanup failed: {e}", exc_info=True)
            logger.warning("Skipping cleanup due to error")

        return result

    def _get_analysis_schema(self) -> dict:
        """
        Comprehensive schema for telecom call analysis covering insights,
        quality, sales, sentiment, CX, and network performance.
        """
        return {
            "response_mime_type": "application/json",
            "response_schema": {
                "type": "OBJECT",
                "properties": {
                    "service_number": {"type": "STRING"},
                    "call_type": {
                        "type": "STRING",
                        "description": "1.Complaint, 2.Retention, 3.Service Request, 4.Enquiry, 5.Sale (comma separated)",  # noqa: E501 -- Gemini schema description text, kept verbatim
                        "nullable": False,
                    },
                    "call_type_confident": {"type": "STRING", "nullable": False},
                    "customer_insight": {
                        "type": "OBJECT",
                        "properties": {
                            "summary_story": {
                                "type": "STRING",
                                "description": "บทสรุปการสนทนา สรุปเป็นภาษาไทย",
                                "nullable": False,
                            },
                            "product_category": {"type": "STRING", "nullable": False},
                            "repeat_call": {"type": "STRING", "enum": ["Repeat Call", "New Call"], "nullable": False},
                            "fcr": {"type": "BOOLEAN", "nullable": False},
                            "churn_probability": {"type": "STRING", "nullable": False},
                            "churn_reason": {
                                "type": "STRING",
                                "enum": [
                                    "Price and Package",
                                    "Network and Quality",
                                    "Service and Experience",
                                    "Device Contract and Policy",
                                    "Competitor",
                                    "Other",
                                ],
                                "nullable": False,
                            },
                            "customer_insight_summary": {"type": "STRING", "nullable": False},
                            "standard_gsd_name": {
                                "type": "STRING",
                                "description": "เลือก 1 หัวข้อที่ตรงกับบทสนทนาจากรายการที่กำหนดให้ หากไม่อยู่ในหมวดหมู่ที่มี ให้ระบุชื่อหัวข้อใหม่ภาษาอังกฤษที่เหมาะสมที่สุด",  # noqa: E501 -- Gemini schema description text, kept verbatim
                                "nullable": False,
                            },
                        },
                        "required": [
                            "summary_story",
                            "product_category",
                            "repeat_call",
                            "fcr",
                            "churn_probability",
                            "churn_reason",
                            "customer_insight_summary",
                            "standard_gsd_name",
                        ],
                    },
                    "service_quality": {
                        "type": "OBJECT",
                        "properties": {
                            "greeting_standard": {
                                "type": "OBJECT",
                                "properties": {
                                    "evaluation": {
                                        "type": "STRING",
                                        "enum": ["Meet", "Below", "N/A"],
                                        "nullable": False,
                                    },
                                    "reason": {
                                        "type": "STRING",
                                        "description": "ระบุเหตุผลในการประเมิน evaluation แบบกะทัดรัดสั้นๆ ห้ามสรุปเป็น step และขอเป็นภาษาไทย",  # noqa: E501 -- Gemini schema description text, kept verbatim
                                        "nullable": False,
                                    },
                                },
                                "required": ["evaluation", "reason"],
                            },
                            "manners": {
                                "type": "OBJECT",
                                "properties": {
                                    "evaluation": {"type": "STRING", "enum": ["Meet", "Below"], "nullable": False},
                                    "reason": {
                                        "type": "STRING",
                                        "description": "ระบุเหตุผลในการประเมิน evaluation แบบกะทัดรัดสั้นๆ ห้ามสรุปเป็น step และขอเป็นภาษาไทย",  # noqa: E501 -- Gemini schema description text, kept verbatim
                                        "nullable": False,
                                    },
                                },
                                "required": ["evaluation", "reason"],
                            },
                            "enthusiasm": {
                                "type": "OBJECT",
                                "properties": {
                                    "evaluation": {"type": "STRING", "enum": ["Meet", "Below"], "nullable": False},
                                    "reason": {
                                        "type": "STRING",
                                        "description": "ระบุเหตุผลในการประเมิน evaluation แบบกะทัดรัดสั้นๆ ห้ามสรุปเป็น step และขอเป็นภาษาไทย",  # noqa: E501 -- Gemini schema description text, kept verbatim
                                        "nullable": False,
                                    },
                                },
                                "required": ["evaluation", "reason"],
                            },
                            "communication_skill": {
                                "type": "OBJECT",
                                "properties": {
                                    "evaluation": {"type": "STRING", "enum": ["Meet", "Below"], "nullable": False},
                                    "reason": {
                                        "type": "STRING",
                                        "description": "ระบุเหตุผลในการประเมิน evaluation แบบกะทัดรัดสั้นๆ ห้ามสรุปเป็น step และขอเป็นภาษาไทย",  # noqa: E501 -- Gemini schema description text, kept verbatim
                                        "nullable": False,
                                    },
                                },
                                "required": ["evaluation", "reason"],
                            },
                            "ending_standard": {
                                "type": "OBJECT",
                                "properties": {
                                    "evaluation": {
                                        "type": "STRING",
                                        "enum": ["Meet", "Below", "N/A"],
                                        "nullable": False,
                                    },
                                    "reason": {
                                        "type": "STRING",
                                        "description": "ระบุเหตุผลในการประเมิน evaluation แบบกะทัดรัดสั้นๆ ห้ามสรุปเป็น step และขอเป็นภาษาไทย",  # noqa: E501 -- Gemini schema description text, kept verbatim
                                        "nullable": False,
                                    },
                                },
                                "required": ["evaluation", "reason"],
                            },
                            "data_privacy": {
                                "type": "OBJECT",
                                "properties": {
                                    "evaluation": {
                                        "type": "STRING",
                                        "enum": ["Meet", "Below", "N/A"],
                                        "nullable": False,
                                    },
                                    "reason": {
                                        "type": "STRING",
                                        "description": "ระบุเหตุผลในการประเมิน evaluation แบบกะทัดรัดสั้นๆ ห้ามสรุปเป็น step และขอเป็นภาษาไทย",  # noqa: E501 -- Gemini schema description text, kept verbatim
                                        "nullable": False,
                                    },
                                },
                                "required": ["evaluation", "reason"],
                            },
                            "legal_verification": {
                                "type": "OBJECT",
                                "properties": {
                                    "evaluation": {
                                        "type": "STRING",
                                        "enum": ["Meet", "Below", "N/A"],
                                        "nullable": False,
                                    },
                                    "reason": {
                                        "type": "STRING",
                                        "description": "ระบุเหตุผลในการประเมิน evaluation แบบกะทัดรัดสั้นๆ ห้ามสรุปเป็น step และขอเป็นภาษาไทย",  # noqa: E501 -- Gemini schema description text, kept verbatim
                                        "nullable": False,
                                    },
                                },
                                "required": ["evaluation", "reason"],
                            },
                            "customer_verification": {
                                "type": "OBJECT",
                                "properties": {
                                    "evaluation": {
                                        "type": "STRING",
                                        "enum": ["Meet", "Below", "N/A"],
                                        "nullable": False,
                                    },
                                    "reason": {
                                        "type": "STRING",
                                        "description": "ระบุเหตุผลในการประเมิน evaluation แบบกะทัดรัดสั้นๆ ห้ามสรุปเป็น step และขอเป็นภาษาไทย",  # noqa: E501 -- Gemini schema description text, kept verbatim
                                        "nullable": False,
                                    },
                                },
                                "required": ["evaluation", "reason"],
                            },
                            "sla_notification": {
                                "type": "OBJECT",
                                "properties": {
                                    "evaluation": {
                                        "type": "STRING",
                                        "enum": ["Meet", "Below", "N/A"],
                                        "nullable": False,
                                    },
                                    "reason": {
                                        "type": "STRING",
                                        "description": "ระบุเหตุผลในการประเมิน evaluation แบบกะทัดรัดสั้นๆ ห้ามสรุปเป็น step และขอเป็นภาษาไทย",  # noqa: E501 -- Gemini schema description text, kept verbatim
                                        "nullable": False,
                                    },
                                },
                                "required": ["evaluation", "reason"],
                            },
                            "transfer_standard": {
                                "type": "OBJECT",
                                "properties": {
                                    "evaluation": {
                                        "type": "STRING",
                                        "enum": ["Meet", "Below", "N/A"],
                                        "nullable": False,
                                    },
                                    "reason": {
                                        "type": "STRING",
                                        "description": "ระบุเหตุผลในการประเมิน evaluation แบบกะทัดรัดสั้นๆ ห้ามสรุปเป็น step และขอเป็นภาษาไทย",  # noqa: E501 -- Gemini schema description text, kept verbatim
                                        "nullable": False,
                                    },
                                },
                                "required": ["evaluation", "reason"],
                            },
                            "problem_understanding": {
                                "type": "OBJECT",
                                "properties": {
                                    "evaluation": {"type": "STRING", "enum": ["Meet", "Below"], "nullable": False},
                                    "reason": {
                                        "type": "STRING",
                                        "description": "ระบุเหตุผลในการประเมิน evaluation แบบกะทัดรัดสั้นๆ ห้ามสรุปเป็น step และขอเป็นภาษาไทย",  # noqa: E501 -- Gemini schema description text, kept verbatim
                                        "nullable": False,
                                    },
                                },
                                "required": ["evaluation", "reason"],
                            },
                            "compensation": {
                                "type": "OBJECT",
                                "properties": {
                                    "evaluation": {
                                        "type": "STRING",
                                        "enum": ["Meet", "Below", "N/A"],
                                        "nullable": False,
                                    },
                                    "reason": {
                                        "type": "STRING",
                                        "description": "ระบุเหตุผลในการประเมิน evaluation แบบกะทัดรัดสั้นๆ ห้ามสรุปเป็น step และขอเป็นภาษาไทย",  # noqa: E501 -- Gemini schema description text, kept verbatim
                                        "nullable": False,
                                    },
                                },
                                "required": ["evaluation", "reason"],
                            },
                            "hold_standard": {
                                "type": "OBJECT",
                                "properties": {
                                    "evaluation": {
                                        "type": "STRING",
                                        "enum": ["Meet", "Below", "N/A"],
                                        "nullable": False,
                                    },
                                    "reason": {
                                        "type": "STRING",
                                        "description": "ระบุเหตุผลในการประเมิน evaluation แบบกะทัดรัดสั้นๆ ห้ามสรุปเป็น step และขอเป็นภาษาไทย",  # noqa: E501 -- Gemini schema description text, kept verbatim
                                        "nullable": False,
                                    },
                                },
                                "required": ["evaluation", "reason"],
                            },
                            "wrap_up": {
                                "type": "OBJECT",
                                "properties": {
                                    "evaluation": {
                                        "type": "STRING",
                                        "enum": ["Meet", "Below", "N/A"],
                                        "nullable": False,
                                    },
                                    "reason": {
                                        "type": "STRING",
                                        "description": "ระบุเหตุผลในการประเมิน evaluation แบบกะทัดรัดสั้นๆ ห้ามสรุปเป็น step และขอเป็นภาษาไทย",  # noqa: E501 -- Gemini schema description text, kept verbatim
                                        "nullable": False,
                                    },
                                },
                                "required": ["evaluation", "reason"],
                            },
                            "beyond_scope_support": {
                                "type": "OBJECT",
                                "properties": {
                                    "evaluation": {
                                        "type": "STRING",
                                        "enum": ["Meet", "Below", "N/A"],
                                        "nullable": False,
                                    },
                                    "reason": {
                                        "type": "STRING",
                                        "description": "ระบุเหตุผลในการประเมิน evaluation แบบกะทัดรัดสั้นๆ ห้ามสรุปเป็น step และขอเป็นภาษาไทย",  # noqa: E501 -- Gemini schema description text, kept verbatim
                                        "nullable": False,
                                    },
                                },
                                "required": ["evaluation", "reason"],
                            },
                            "true_application": {
                                "type": "OBJECT",
                                "properties": {
                                    "evaluation": {
                                        "type": "STRING",
                                        "enum": ["Meet", "Below", "N/A"],
                                        "nullable": False,
                                    },
                                    "reason": {
                                        "type": "STRING",
                                        "description": "ระบุเหตุผลในการประเมิน evaluation แบบกะทัดรัดสั้นๆ ห้ามสรุปเป็น step และขอเป็นภาษาไทย",  # noqa: E501 -- Gemini schema description text, kept verbatim
                                        "nullable": False,
                                    },
                                },
                                "required": ["evaluation", "reason"],
                            },
                            "case_ownership": {
                                "type": "OBJECT",
                                "properties": {
                                    "evaluation": {
                                        "type": "STRING",
                                        "enum": ["Meet", "Below", "N/A"],
                                        "nullable": False,
                                    },
                                    "reason": {
                                        "type": "STRING",
                                        "description": "ระบุเหตุผลในการประเมิน evaluation แบบกะทัดรัดสั้นๆ ห้ามสรุปเป็น step และขอเป็นภาษาไทย",  # noqa: E501 -- Gemini schema description text, kept verbatim
                                        "nullable": False,
                                    },
                                },
                                "required": ["evaluation", "reason"],
                            },
                            "contact_confirm": {
                                "type": "OBJECT",
                                "properties": {
                                    "evaluation": {
                                        "type": "STRING",
                                        "enum": ["Meet", "Below", "N/A"],
                                        "nullable": False,
                                    },
                                    "reason": {
                                        "type": "STRING",
                                        "description": "ระบุเหตุผลในการประเมิน evaluation แบบกะทัดรัดสั้นๆ ห้ามสรุปเป็น step และขอเป็นภาษาไทย",  # noqa: E501 -- Gemini schema description text, kept verbatim
                                        "nullable": False,
                                    },
                                },
                                "required": ["evaluation", "reason"],
                            },
                            "omotenashi": {
                                "type": "OBJECT",
                                "properties": {
                                    "evaluation": {
                                        "type": "STRING",
                                        "enum": ["Meet", "Below", "N/A"],
                                        "nullable": False,
                                    },
                                    "reason": {
                                        "type": "STRING",
                                        "description": "ระบุเหตุผลในการประเมิน evaluation แบบกะทัดรัดสั้นๆ ห้ามสรุปเป็น step และขอเป็นภาษาไทย",  # noqa: E501 -- Gemini schema description text, kept verbatim
                                        "nullable": False,
                                    },
                                },
                                "required": ["evaluation", "reason"],
                            },
                            "retention": {
                                "type": "OBJECT",
                                "properties": {
                                    "evaluation": {
                                        "type": "STRING",
                                        "enum": ["Meet", "Below", "N/A"],
                                        "nullable": False,
                                    },
                                    "reason": {
                                        "type": "STRING",
                                        "description": "ระบุเหตุผลในการประเมิน evaluation แบบกะทัดรัดสั้นๆ ห้ามสรุปเป็น step และขอเป็นภาษาไทย",  # noqa: E501 -- Gemini schema description text, kept verbatim
                                        "nullable": False,
                                    },
                                },
                                "required": ["evaluation", "reason"],
                            },
                            "downsell": {
                                "type": "OBJECT",
                                "properties": {
                                    "evaluation": {
                                        "type": "STRING",
                                        "enum": ["Meet", "Below", "N/A"],
                                        "nullable": False,
                                    },
                                    "reason": {
                                        "type": "STRING",
                                        "description": "ระบุเหตุผลในการประเมิน evaluation แบบกะทัดรัดสั้นๆ ห้ามสรุปเป็น step และขอเป็นภาษาไทย",  # noqa: E501 -- Gemini schema description text, kept verbatim
                                        "nullable": False,
                                    },
                                },
                                "required": ["evaluation", "reason"],
                            },
                            "mnp": {
                                "type": "OBJECT",
                                "properties": {
                                    "evaluation": {
                                        "type": "STRING",
                                        "enum": ["Meet", "Below", "N/A"],
                                        "nullable": False,
                                    },
                                    "reason": {
                                        "type": "STRING",
                                        "description": "ระบุเหตุผลในการประเมิน evaluation แบบกะทัดรัดสั้นๆ ห้ามสรุปเป็น step และขอเป็นภาษาไทย",  # noqa: E501 -- Gemini schema description text, kept verbatim
                                        "nullable": False,
                                    },
                                },
                                "required": ["evaluation", "reason"],
                            },
                            "upselling": {
                                "type": "OBJECT",
                                "properties": {
                                    "evaluation": {
                                        "type": "STRING",
                                        "enum": ["Meet", "Below", "N/A"],
                                        "nullable": False,
                                    },
                                    "reason": {
                                        "type": "STRING",
                                        "description": "ระบุเหตุผลในการประเมิน evaluation แบบกะทัดรัดสั้นๆ ห้ามสรุปเป็น step และขอเป็นภาษาไทย",  # noqa: E501 -- Gemini schema description text, kept verbatim
                                        "nullable": False,
                                    },
                                },
                                "required": ["evaluation", "reason"],
                            },
                            "service_quality_performance_insight": {
                                "type": "STRING",
                                "description": "สรุปเป็นภาษาไทย",
                                "nullable": False,
                            },
                        },
                        "required": [
                            "greeting_standard",
                            "manners",
                            "enthusiasm",
                            "communication_skill",
                            "ending_standard",
                            "data_privacy",
                            "legal_verification",
                            "customer_verification",
                            "sla_notification",
                            "transfer_standard",
                            "problem_understanding",
                            "compensation",
                            "hold_standard",
                            "wrap_up",
                            "beyond_scope_support",
                            "true_application",
                            "case_ownership",
                            "contact_confirm",
                            "omotenashi",
                            "retention",
                            "downsell",
                            "mnp",
                            "upselling",
                            "service_quality_performance_insight",
                        ],
                    },
                    "sale_opportunity": {
                        "type": "OBJECT",
                        "properties": {
                            "opportunity_recognition_in_conversation": {"type": "BOOLEAN", "nullable": False},
                            "product_suggested_by_ai": {"type": "STRING", "nullable": False},
                            "agent_offer_product_presentation_&_explanation": {"type": "BOOLEAN", "nullable": False},
                            "product_offer_by_agent": {"type": "STRING", "nullable": False},
                            "sales_outcome_&_customer_decision": {"type": "BOOLEAN", "nullable": False},
                            "sales_opportunities_performance_insight": {"type": "STRING", "nullable": False},
                        },
                        "required": [
                            "opportunity_recognition_in_conversation",
                            "product_suggested_by_ai",
                            "agent_offer_product_presentation_&_explanation",
                            "product_offer_by_agent",
                            "sales_outcome_&_customer_decision",
                            "sales_opportunities_performance_insight",
                        ],
                    },
                    "customer_sentiment": {
                        "type": "OBJECT",
                        "properties": {
                            "overall_sentiment": {"type": "STRING", "nullable": False},
                            "initial_sentiment": {
                                "type": "STRING",
                                "enum": ["Positive", "Neutral", "Negative"],
                                "nullable": False,
                            },
                            "final_sentiment": {
                                "type": "STRING",
                                "enum": ["Positive", "Neutral", "Negative"],
                                "nullable": False,
                            },
                            "primary_sentiment_driver": {
                                "type": "STRING",
                                "enum": [
                                    "Issues Resolution",
                                    "Agent Behavior and Communication",
                                    "Process / Policy",
                                    "Price / Charges / Commercials",
                                    "System / IVR / Channel Experience",
                                    "Product / Service Feature",
                                ],
                                "nullable": False,
                            },
                            "csat": {"type": "STRING", "description": "1 to 5", "nullable": False},
                            "cs_performance_insight": {"type": "STRING", "nullable": False},
                        },
                        "required": [
                            "overall_sentiment",
                            "initial_sentiment",
                            "final_sentiment",
                            "primary_sentiment_driver",
                            "csat",
                            "cs_performance_insight",
                        ],
                    },
                    "customer_experience": {
                        "type": "OBJECT",
                        "properties": {
                            "agent_communication_&_attitude": {
                                "type": "STRING",
                                "description": "0 to 5",
                                "nullable": False,
                            },
                            "agent_communication_&_attitude_reason": {"type": "STRING", "nullable": False},
                            "agent_understanding_&_resolution": {
                                "type": "STRING",
                                "description": "0 to 5",
                                "nullable": False,
                            },
                            "agent_understanding_&_resolution_reason": {"type": "STRING", "nullable": False},
                            "agent_responsiveness": {"type": "STRING", "description": "0 to 5", "nullable": False},
                            "agent_responsiveness_reason": {"type": "STRING", "nullable": False},
                            "system_accessibility": {"type": "BOOLEAN", "nullable": False},
                            "system_accessibility_reason": {"type": "STRING", "nullable": False},
                            "ivr_usability_&_design": {"type": "BOOLEAN", "nullable": False},
                            "ivr_usability_&_design_reason": {"type": "STRING", "nullable": False},
                            "ces": {"type": "STRING", "description": "1 to 5", "nullable": False},
                            "self_service_readiness": {
                                "type": "STRING",
                                "enum": ["High", "Medium", "Low"],
                                "nullable": False,
                            },
                            "cx_performance_insight": {"type": "STRING", "nullable": False},
                        },
                        "required": [
                            "agent_communication_&_attitude",
                            "agent_communication_&_attitude_reason",
                            "agent_understanding_&_resolution",
                            "agent_understanding_&_resolution_reason",
                            "agent_responsiveness",
                            "agent_responsiveness_reason",
                            "system_accessibility",
                            "system_accessibility_reason",
                            "ivr_usability_&_design",
                            "ivr_usability_&_design_reason",
                            "ces",
                            "self_service_readiness",
                            "cx_performance_insight",
                        ],
                    },
                    "network": {
                        "type": "OBJECT",
                        "properties": {
                            "issue_type": {
                                "type": "STRING",
                                "enum": [
                                    "Speed",
                                    "Outage",
                                    "Drop",
                                    "Coverage",
                                    "FUP",
                                    "Installation",
                                    "Support",
                                    "Voice Quality",
                                ],
                                "nullable": True,
                            },
                            "problem_statement": {"type": "ARRAY", "items": {"type": "STRING"}},
                            "area_tag_province": {"type": "STRING", "nullable": True},
                            "area_tag_district": {"type": "STRING", "nullable": True},
                            "area_tag_sub_district": {"type": "STRING", "nullable": True},
                            "area_tag_landmark": {"type": "STRING", "nullable": True},
                        },
                        "required": [
                            "issue_type",
                            "problem_statement",
                            "area_tag_province",
                            "area_tag_district",
                            "area_tag_sub_district",
                            "area_tag_landmark",
                        ],
                    },
                },
                "required": [
                    "service_number",
                    "call_type",
                    "call_type_confident",
                    "customer_insight",
                    "service_quality",
                    "sale_opportunity",
                    "customer_sentiment",
                    "customer_experience",
                    "network",
                ],
            },
        }

    def on_error(self, error: Exception) -> None:
        """
        Hook executed when task execution fails.
        Override this method to implement custom error handling
        (e.g., cleanup, notifications, retry logic).

        Args:
            error (Exception): The exception that occurred during execution

        Returns:
            None
        """
        try:
            current_dt = get_current_datetime().replace(microsecond=0)
            formatted_current_dt = current_dt.isoformat(sep=" ")

            subject = "[AI Failed] [AI-QA]"
            body = f"""
            Dear Operation Team,<br>
            <br>
                Project: {self.project_id} <br>
                An error occurred during execution of task '{self.task_name}' <br>
                Error details: {error} <br>
                <br>
                Please investigate. <br>
            <br>
            -- Timestamp: {formatted_current_dt} <br>
            Best Regards,<br>
            [This is automatic message generated by AI - Do Not REPLY]
            """

            self.msgraph_module.send_email(
                subject=subject,
                body=body,
                sender_email=self.msgraph_sender_email,
                receiver_email=self.msgraph_receiver_email,
                cc_email=self.msgraph_cc_email,
            )
            logger.info(f"Task error notification successfully issued: {self.task_name}")
        except Exception as email_error:
            logger.error(f"Task error notification failed to issue: {email_error}", exc_info=True)
        logger.error(f"Error in task '{self.task_name}': {error}", exc_info=True)
