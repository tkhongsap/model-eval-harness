# Library imports
import json
import os
import re
from datetime import datetime
from typing import Any

from src.core.task_interface import TaskInterface

# Source code imports
from src.core.task_registry import task_registry
from src.modules.google.gcs import GCSModule
from src.modules.google.gemini_batch import GeminiBatchModule
from src.modules.microsoft.sharepoint import SharePointModule
from src.utils.common import (
    get_value_by_path,
    recursive_dict_value_by_key,
    resolve_date,
    resolve_env,
    safe_list_get,
    safe_list_get_slicing,
)
from src.utils.date_utils import (
    add_date,
    get_current_datetime,
    list_date,
)
from src.utils.file_utils import (
    load_yaml,
)
from src.utils.logger import Logger
from tasks.sentiment_telesale.output_validation.validate_mapping import ValidationMapping

logger = Logger(__name__)


@task_registry.register("TelesaleGetBatchResultTask")
class GetBatchResultTask(TaskInterface):
    COMMON_CONFIG_PATH = "config/common.yml"
    DEFAULT_MODEL_VERSION = "gemini-2.5-flash"
    DEFAULT_RECORD_DATE = "99991231"
    DEFAULT_JSONL_PREDICTION_FILE = "predictions.jsonl"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # Load parameters from configuration
        self.gcs = self.get_config("gcs", {})
        self.sharepoint = self.get_config("sharepoint", {})
        self.framework = self.get_config("framework", {})

        common_config = load_yaml(self.COMMON_CONFIG_PATH)
        self.verint_access = common_config.get("verint", {})
        self.control_access = common_config.get("control", {})

        # Copy validate_mapping to avoid mutating the shared class-level dict
        self.validate_mapping = dict(ValidationMapping.validate_mapping)

    def pre_execute(self):
        """
        Pre-execution setup: Initialize modules and connections.
        """
        logger.debug("Initializing modules")

        # Initialize SharePoint Verint module
        try:
            verint_site = resolve_env(self.verint_access.get("site_domain"))
            self.sharepoint_verint = SharePointModule(
                client_id=resolve_env(self.verint_access.get("client_id")),
                client_secret=resolve_env(self.verint_access.get("client_secret")),
                tenant_id=resolve_env(self.verint_access.get("tenant_id")),
                site_domain=verint_site,
                site_path=resolve_env(self.verint_access.get("site_path")),
            )
            logger.debug(f"SharePoint Verint: {verint_site}")
        except Exception as e:
            logger.error(f"Failed to initialize SharePoint Verint module: {e}", exc_info=True)
            raise

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
        execution_dt = self.get_package("execution_dt", None)
        rerun_date = self.get_package("rerun_data_dt", None)

        datadate = rerun_date or execution_dt.strftime("%Y-%m-%d")

        try:
            # Calculate start and end dates (8 days including today)
            end_date = datetime.strptime(datadate, "%Y-%m-%d").strftime("%Y%m%d")
            start_date = add_date(datadate, -int(resolve_env(self.framework["lookback_days"]))).strftime("%Y%m%d")
            logger.debug(f"Processing date range: {start_date} to {end_date}")
        except Exception as e:
            logger.error(f"Failed to calculate date range: {e}", exc_info=True)
            raise Exception(f"Cannot determine processing date range: {e}") from e

        # Generate list of dates to process
        try:
            list_dates = list_date(
                start_date=start_date,
                end_date=end_date,
                input_date_format="%Y%m%d",
                output_date_format="%Y%m%d",
            )
            logger.debug(f"Generated {len(list_dates)} dates to process: {list_dates}")
        except Exception as e:
            logger.error(f"Failed to generate date list: {e}", exc_info=True)
            raise Exception(f"Critical error: Cannot generate date list: {e}") from e

        # Discover batch files
        logger.debug("Discovering batch output files")
        list_batchs = []
        for date_str in list_dates:
            try:
                output_folder = resolve_date(
                    text=self.gcs["output_folder"],
                    replace_date=date_str,
                )
                logger.debug(f"Checking output folder for {date_str}: {output_folder}")

                files = self.gcs_module.list_files(prefix=output_folder)
                filter_output_files = [f for f in files if f.endswith(self.DEFAULT_JSONL_PREDICTION_FILE)]

                if filter_output_files:
                    logger.debug(f"Found {len(filter_output_files)} batch files for date {date_str}")
                    list_batchs.extend(filter_output_files)
                else:
                    logger.debug(f"No batch files found for date {date_str}")

            except Exception as e:
                logger.error(f"Error listing files for date {date_str}: {e}", exc_info=True)
                logger.warning(f"Skipping date {date_str} due to listing error")
                continue

        logger.info(f"Total batch files discovered: {len(list_batchs)}")

        if len(list_batchs) == 0:
            logger.warning("No batch files found to process. Task completed.")
            logger.info("Retrieve Batch Task completed (no batches to process)")
            return {"list_batchs": [], "batch_results": [], "failed_batches": []}

        # Process batch files
        logger.debug("Processing batch prediction files")

        self.failed_batches: list[str] = []
        self.batch_results: list[dict] = []

        for idx, batch in enumerate(list_batchs, 1):
            logger.debug(f"Processing batch [{idx}/{len(list_batchs)}]: {batch}")

            try:
                # Retrieve batch results from GCS
                try:
                    raw_jsonl = GeminiBatchModule.retrieve_batch_results(
                        gcs_module=self.gcs_module, batch_output_path=batch
                    )
                    logger.debug(f"Retrieved {len(raw_jsonl)} prediction records from batch")
                except Exception as retrieve_err:
                    logger.error(f"Failed to retrieve batch results: {retrieve_err}", exc_info=True)
                    self.failed_batches.append(batch)
                    continue

                self._proc_raw_prediction(batch=batch, raw_jsonl=raw_jsonl, execution_dt=execution_dt)
            except Exception as e:
                logger.error(f"Failed to process batch {batch}: {e}", exc_info=True)
                self.failed_batches.append(batch)
                continue
        logger.info(f"Total predictions processed: {len(self.batch_results)}")
        logger.info(f"Failed batches: {len(self.failed_batches)}")

        if self.failed_batches:
            logger.warning(f"Some batches failed to process: {self.failed_batches}")

        # list_batchs: All batches found, batch_results: All processed results, failed_batches: Batches that failed
        return {"list_batchs": list_batchs, "batch_results": self.batch_results, "failed_batches": self.failed_batches}

    def _proc_raw_prediction(self, batch: str, raw_jsonl: list[dict], execution_dt=None) -> dict:
        """
        Process raw prediction record into structured format.
        Parameters:
            batch (str): The batch file path.
            raw_jsonl (list[dict]): List of raw prediction records.
        Returns:
            dict: Processed batch results.
        """

        def add_additional_info(line: dict) -> dict:
            """
            Prepare batch date info and model version for adding to payload.
            Parameters:
                line (dict): The raw prediction record.
            Returns:
                dict: Dictionary containing create_time, processed_time, and model_version.
            """
            model_version = get_value_by_path(line, "response.modelVersion", None)
            if model_version is None:
                logger.warning("Model version missing in error record, using default")
                model_version = self.DEFAULT_MODEL_VERSION
            return {
                "create_time": get_value_by_path(line, "response.createTime", None),
                "processed_time": get_value_by_path(line, "processed_time", None),
                "model_version": model_version,
            }

        # Process each prediction in the batch
        processed_count = 0
        skipped_count = 0
        load_dt_str = (
            execution_dt.strftime("%Y-%m-%d %H:%M:%S")
            if execution_dt
            else get_current_datetime().strftime("%Y-%m-%d %H:%M:%S")
        )

        for line_idx, line in enumerate(raw_jsonl):
            try:
                # Extract voice file URI
                voice_processed_path = safe_list_get(
                    recursive_dict_value_by_key(data=line, target_key="fileUri"), 0, None
                )

                if voice_processed_path is None:
                    logger.warning(f"No 'fileUri' found in line {line_idx + 1}, skipping")
                    skipped_count += 1
                    continue

                file_name = os.path.splitext(os.path.basename(voice_processed_path))[0]
                logger.debug(f"Processing prediction for file: {file_name}")

                # Parse file name components (non-critical errors - use None for missing parts)
                try:
                    file_name_components = file_name.split("_")

                    # Extract record_date with fallback strategy
                    try:
                        record_date = safe_list_get(file_name_components, -3, None)
                        if not record_date or not re.match(r"^\d{8}$", record_date or ""):
                            # Fallback: Extract from path using regex
                            regex_match = re.findall(r"(?<=\/)\d{8}(?=\/)", voice_processed_path)
                            if regex_match:
                                record_date = regex_match[-1]  # Get last date from path
                                logger.warning(f"Record date missing in file name, extracted from path: {record_date}")
                            else:
                                record_date = self.DEFAULT_RECORD_DATE
                                logger.warning(
                                    f"Record date not found in file name or path, using default: {record_date}"
                                )
                    except Exception as rec_err:
                        logger.error(f"Error extracting record date for file '{file_name}': {rec_err}")
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
                    logger.warning("Using default None values for voice_info")
                    voice_info = {
                        "file_name": file_name,
                        "file_ext": None,
                        "call_id": None,
                        "phone_number": None,
                        "call_time": None,
                        "agent_id": None,
                        "first_name": None,
                        "last_name": None,
                        "provider": None,
                        "record_date": record_date,
                        "duration": None,
                    }

                # Check for error status in batch result
                if line.get("status", "") != "":
                    logger.warning(f"Batch result has error status: {line['status']}")
                    payload = self._prepare_prediction_payload(voice_info, prediction=line["status"], err_flag=True)

                    # Add timestamps if available
                    logger.debug("Adding batch createTime, processed_time and model_version to payload")
                    batch_date_info = add_additional_info(line)
                    payload["prediction"].update(batch_date_info)
                    payload["load_dt"] = load_dt_str
                    logger.debug(f"Added timestamps to payload: {batch_date_info}")

                    # Extract and add usage metadata
                    try:
                        usage_summary = GeminiBatchModule.sum_tokens_usage_for_billing(
                            get_value_by_path(line, "response.usageMetadata", {})
                        )
                        payload["prediction"].update(usage_summary)
                        logger.debug(f"Added usage metadata for error record: {usage_summary}")
                    except Exception as usage_err:
                        logger.debug(f"Could not extract usage metadata for error record: {usage_err}")

                    self.batch_results.append(payload)
                    processed_count += 1
                    continue

                # Extract prediction text
                prediction_str = get_value_by_path(line, "response.candidates.0.content.parts.0.text", None)

                if prediction_str is None:
                    warning_msg = f"No prediction found for file {file_name}"
                    logger.warning(warning_msg)
                    payload = self._prepare_prediction_payload(voice_info, prediction=warning_msg, err_flag=True)

                    # Add timestamps if available
                    logger.debug("Adding batch createTime, processed_time and model_version to payload")
                    batch_date_info = add_additional_info(line)
                    payload["prediction"].update(batch_date_info)
                    payload["load_dt"] = load_dt_str
                    logger.debug(f"Added timestamps to payload: {batch_date_info}")

                    # Extract and add usage metadata
                    try:
                        usage_summary = GeminiBatchModule.sum_tokens_usage_for_billing(
                            get_value_by_path(line, "response.usageMetadata", {})
                        )
                        payload["prediction"].update(usage_summary)
                        logger.debug(f"Added usage metadata for missing prediction: {usage_summary}")
                    except Exception as usage_err:
                        logger.debug(f"Could not extract usage metadata for missing prediction: {usage_err}")

                    self.batch_results.append(payload)
                    processed_count += 1
                    continue

                # Prepare successful prediction payload
                payload = self._prepare_prediction_payload(voice_info, prediction=prediction_str)

                # Add timestamps if available
                logger.debug("Adding batch createTime, processed_time and model_version to payload")
                batch_date_info = add_additional_info(line)
                payload["prediction"].update(batch_date_info)
                payload["load_dt"] = load_dt_str
                logger.debug(f"Added timestamps to payload: {batch_date_info}")

                # Extract and add usage metadata
                try:
                    usage_summary = GeminiBatchModule.sum_tokens_usage_for_billing(
                        get_value_by_path(line, "response.usageMetadata", {})
                    )
                    payload["prediction"].update(usage_summary)
                    logger.debug(
                        f"Added usage metadata: tokens_input={usage_summary.get('token_input')}, tokens_output={usage_summary.get('token_output')}, tokens_cached={usage_summary.get('token_cached')}"
                    )
                except Exception as usage_err:
                    logger.warning(f"Could not extract usage metadata for {file_name}: {usage_err}")
                    # Add default empty usage if extraction fails
                    payload["prediction"].update(
                        {"token_input": {"text": 0, "audio": 0}, "token_output": {"text": 0}, "token_cached": 0}
                    )

                self.batch_results.append(payload)
                processed_count += 1

            except Exception as line_err:
                logger.error(f"Error processing line {line_idx + 1} in batch {batch}: {line_err}", exc_info=True)
                skipped_count += 1
                continue

        logger.info(f"Batch processing complete: {processed_count} processed, {skipped_count} skipped")

    def _prepare_prediction_payload(self, voice_info: dict[str, Any], prediction: str, err_flag: bool = False) -> dict:
        """
        Prepare the prediction payload structure.
        Parameters:
            voice_info (dict): Metadata about the voice file.
            prediction (str): The raw prediction JSON string or error message.
            err_flag (bool): Flag indicating if this is an error case.
        Returns:
            dict: Structured payload with prediction and metadata.
        """
        logger.debug(f"Preparing prediction payload for file: {voice_info.get('file_name')}")

        # Create schema from centralized definition
        fw_schema = {
            "file_metadata": voice_info,
            "prediction": {
                "raw_prediction": None,
                "status": None,
                "message": None,
            },
        }

        # Handle error cases
        if err_flag:
            fw_schema["prediction"]["status"] = "FAILED"
            fw_schema["prediction"]["message"] = prediction

            logger.warning(f"Marking record as FAILED: {voice_info.get('file_name')}")
            return fw_schema

        # Parse prediction JSON
        try:
            logger.debug(f"Parsing prediction JSON for: {voice_info.get('file_name')}")
            parsed_data = json.loads(prediction)
            campaign_name = parsed_data.get("campaign_name")
            if not campaign_name:
                logger.error(f"No validation model found for prediction in file: {voice_info.get('file_name')}")
                raise ValueError("No suitable validation model found for prediction.")
            validation_class, _ = self.validate_mapping[campaign_name]
            logger.info(f"Using validation model: {campaign_name} for file: {voice_info.get('file_name')}")
            parsed_output = validation_class.model_validate_json(prediction)
            parsed_output_dict = parsed_output.model_dump()
            fw_schema["prediction"]["raw_prediction"] = parsed_output_dict

            fw_schema["prediction"]["status"] = "SUCCESS"
            fw_schema["prediction"]["message"] = None
            logger.info(f"Successfully parsed prediction for: {voice_info.get('file_name')}")

        except Exception as e:
            logger.error(f"Failed to parse prediction JSON for '{voice_info.get('file_name')}': {e}", exc_info=True)
            fw_schema["prediction"]["status"] = "FAILED"
            fw_schema["prediction"]["message"] = f"Failed to parse prediction JSON: {str(e)}"

        return fw_schema
