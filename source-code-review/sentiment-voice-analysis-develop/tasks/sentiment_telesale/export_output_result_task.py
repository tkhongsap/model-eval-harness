# Library imports
import asyncio
import csv
import io
import json
import os
from datetime import datetime
from pathlib import Path

import pandas as pd

from src.core.task_interface import TaskInterface

# Source code imports
from src.core.task_registry import task_registry
from src.modules.audit_log.log_time_stamper import LogTimeStamper
from src.modules.audit_log.performance_log import (
    PerformanceLogSchema,
    PerformancePayload,
)
from src.modules.audit_log.transaction_log import (
    TransactionLogSchema,
    TransactionPayload,
)
from src.modules.google.gcs import GCSModule
from src.modules.google.gemini_batch import GeminiBatchModule
from src.modules.microsoft.sharepoint import SharePointModule
from src.utils.common import (
    force_none_value_dict,
    get_value_by_path,
    logging_ai_operation,
    resolve_date,
    resolve_env,
    safe_cast_value,
)
from src.utils.date_utils import (
    get_current_datetime,
)
from src.utils.file_utils import (
    load_yaml,
)
from src.utils.logger import Logger
from src.utils.pandas_utils import (
    ensure_df_schema,
    replace_nan_with_default,
)
from src.utils.token_utils import (
    gemini_cost,
)
from tasks.sentiment_telesale.schemas.metadata import Metadata

logger = Logger(__name__)


@task_registry.register("TelesaleExportOutputResultTask")
class ExportOutputResultTask(TaskInterface):
    """
    Task to export telesale sentiment analysis results to SharePoint and archive processed files.
    """

    COMMON_CONFIG_PATH = "config/common.yml"
    DEFAULT_COST_TYPE = "batch"
    DEFAULT_RECORD_DATE = "99991231"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # Load parameters from configuration
        self.gcs = self.get_config("gcs", {})
        self.gcp = self.get_config("gcp", {})
        self.sharepoint = self.get_config("sharepoint", {})
        self.framework = self.get_config("framework", {})

        self._cache_oper_log = {
            "transaction_df": pd.DataFrame(columns=Metadata.TRANSACTION_LOG_SCHEMA),
            "process_date": [],
        }

        try:
            common_config = load_yaml(self.COMMON_CONFIG_PATH)
            self.verint_access = common_config.get("verint", {})
            self.control_access = common_config.get("control", {})
            self.gemini_cost_path = self.control_access.get("gemini_cost_path", None)
        except Exception as e:
            logger.error(f"Failed to load common configuration from {self.COMMON_CONFIG_PATH}: {e}", exc_info=True)
            raise

    def pre_execute(self):
        """
        Pre-execution setup: Initialize modules and connections.
        """
        logger.debug("Initializing modules")

        # Initialize SharePoint Verint module
        try:
            self.verint_site = resolve_env(self.verint_access.get("site_domain"))
            self.sharepoint_verint = SharePointModule(
                client_id=resolve_env(self.verint_access.get("client_id")),
                client_secret=resolve_env(self.verint_access.get("client_secret")),
                tenant_id=resolve_env(self.verint_access.get("tenant_id")),
                site_domain=self.verint_site,
                site_path=resolve_env(self.verint_access.get("site_path")),
            )
            logger.debug(f"SharePoint Verint: {self.verint_site}")
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

    def execute_task(self):
        execution_dt = self.get_package("execution_dt", None)
        batch_results = self.pre_result.get("batch_results", [])
        list_batchs = self.pre_result.get("list_batchs", [])
        failed_batches = self.pre_result.get("failed_batches", [])

        if not batch_results:
            logger.warning("No batch results to process")
            return None

        try:
            output_file = resolve_date(resolve_env(self.sharepoint.get("verint").get("output_file")), execution_dt)
            suffix = f"_{get_current_datetime().strftime('%Y-%m-%d_%H-%M-%S')}"
            output_file_ext = os.path.splitext(output_file)[1]
            output_file = output_file.replace(output_file_ext, f"{suffix}{output_file_ext}")
            logger.debug(f"Output file: {output_file}")
        except Exception as e:
            logger.error(f"Failed to resolve output file path: {e}", exc_info=True)
            raise Exception(f"Cannot determine output file path: {e}") from e

        result = self._format_output(batch_results)
        result_df = pd.DataFrame(result)
        result_csv = result_df.to_csv(
            sep="|",
            index=False,
            header=True,
            encoding="utf-8",
            quoting=csv.QUOTE_NONNUMERIC,
            lineterminator="\n",
            doublequote=True,
        )
        try:
            if os.environ.get("IS_MONITORING_ENABLED", "false").lower() == "true":
                self._export_raw_result(execution_dt=execution_dt, raw_result=batch_results, filename=output_file)
        except Exception as e:
            logger.warning(f"Failed to export raw result for monitoring: {e}", exc_info=True)
            logger.warning("Continuing without exporting raw result for monitoring")

        try:
            self.sharepoint_verint.upload_file(
                upload_path=output_file,
                content=result_csv.encode("utf-8"),
            )
            logger.info(f"Uploaded result file to SharePoint: {output_file}")
        except Exception as upload_err:
            logger.error(f"Failed to upload file for date {execution_dt}: {upload_err}", exc_info=True)
            raise Exception(f"File upload failed for date {execution_dt}: {upload_err}") from upload_err

        logger.debug("Starting for achieving prediction results")
        sep_date = {}
        after_upload_list = []

        for record in batch_results:
            try:
                key = get_value_by_path(record, "file_metadata.record_date", self.DEFAULT_RECORD_DATE)
                if key not in sep_date:
                    sep_date[key] = []
                    sep_date[key].append(record)
                else:
                    sep_date[key].append(record)

                # Mock full path from GCS storage path
                gcs_full_path = get_value_by_path(record, "file_metadata.file_uri", None)
                if gcs_full_path:
                    gcs_path = "/".join(gcs_full_path.split("/")[-3:])
                    folder = os.path.dirname(gcs_path)
                else:
                    gcs_path = ""
                    folder = ""
                full_path = resolve_env("${TELESALE_VERINT_ROOT}/${TELESALE_VERINT_INPUT}") + "/" + gcs_path

                # Prepare for archive and logging
                after_upload_dict = {
                    "file_name": get_value_by_path(record, "file_metadata.file_name", None)
                    + get_value_by_path(record, "file_metadata.file_ext", None),
                    "full_path": full_path,
                    "folder": folder,
                    "record_date": key,
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
                after_upload_list.append(after_upload_dict)
            except Exception as record_err:
                logger.error(f"Error processing record: {record_err}", exc_info=True)
                logger.warning("Skipping problematic record and continuing")
                continue

        logger.debug(f"Prepared {len(after_upload_list)} records for upload across {len(sep_date)} dates")

        try:
            tmp_output_file = resolve_env(self.sharepoint.get("verint").get("output_file"))
            logger.debug(f"Output file template: {tmp_output_file}")
        except Exception as e:
            logger.error(f"Failed to resolve output file path: {e}", exc_info=True)
            raise Exception(f"Cannot determine output file path: {e}") from e

        try:
            df = pd.DataFrame(after_upload_list)
            logger.debug(f"Created DataFrame with {len(df)} records for archival and logging")
            logger.debug(f"DataFrame columns: {list(df.columns)}")
        except Exception as e:
            logger.error(f"Failed to create DataFrame from upload list: {e}", exc_info=True)
            raise Exception(f"Cannot create DataFrame for archival: {e}") from e

        # Archive files
        try:
            logger.info("Starting file archival process")
            self._archive_files(execution_dt, df, list_batchs, failed_batches)
            logger.info("File archival completed successfully")
        except Exception as archive_err:
            logger.error(f"File archival failed: {archive_err}", exc_info=True)
            raise Exception(f"File archival process failed: {archive_err}") from archive_err

        # Insert transaction log records
        try:
            self._insert_log_record(df)
        except Exception as log_err:
            logger.error(f"Failed to insert transaction log records: {log_err}", exc_info=True)
            raise Exception(f"Critical error: Transaction log insertion failed: {log_err}") from log_err

        return batch_results

    def _export_raw_result(self, execution_dt: str, raw_result: list[dict], filename: str) -> None:
        """
        Export raw result to SharePoint for monitoring and troubleshooting purposes.
        The raw result will be exported as a JSON file with the same filename as the processed file, stored in the "raw_results" folder in SharePoint.
        Parameters:
            - execution_dt: The execution date and time for the current batch.
            - raw_result: The original raw result data to be exported.
            - filename: The name of the processed file, used to generate the JSON filename.
        Return:
            - None
        """
        try:
            if "/" in filename:
                filename = os.path.basename(filename)

            raw_prediction_folder = resolve_date(
                text=resolve_env(get_value_by_path(self.sharepoint, "control.raw_prediction_folder")),
                replace_date=execution_dt,
            )
            filename = os.path.splitext(filename)[0]
            export_path = f"{raw_prediction_folder}/{filename}.json"
            logger.debug(f"Exporting raw result for monitoring: {export_path}")
            with io.BytesIO() as json_b:
                json_str = json.dumps(raw_result, indent=4, ensure_ascii=False)
                json_b.write(json_str.encode("utf-8"))
                json_b.seek(0)
                self.sharepoint_control.upload_file(
                    upload_path=export_path,
                    content=json_b.read(),
                )
            logger.info(f"Successfully exported raw result to SharePoint: {export_path}")
        except Exception as e:
            logger.warning(f"Failed to export raw result to JSON: {e}", exc_info=True)
            logger.warning("Skipping exporting raw result for monitoring and continuing")

    def _format_output(self, raw_result: list[dict]) -> list[dict]:
        """
        Format the raw result data for further processing.
        Parameters:
            - raw_result: The original raw result data to be formatted.
        Return:
            - A list of formatted result records.
        """
        logger.debug(f"Starting output formatting for {len(raw_result)} records")

        # Validate input
        if not raw_result:
            logger.warning("No raw results provided for formatting")
            return []

        result = []
        updated_dt = get_current_datetime().strftime("%Y-%m-%d %H:%M:%S")
        logger.debug(f"Using updated_dt: {updated_dt}")

        success_count = 0
        failed_count = 0
        process_files = []
        for idx, rec in enumerate(raw_result, 1):
            try:
                filename = get_value_by_path(rec, "file_metadata.file_name", f"unknown_{idx}") + get_value_by_path(
                    rec, "file_metadata.file_ext", ""
                )
                process_files.append(filename)
                logger.debug(f"Processing record [{idx}/{len(raw_result)}]: {filename}")

                # Extract scoring details
                try:
                    operations_and_professionalism = get_value_by_path(
                        rec, "scoring_result.scoring_detail.operations_and_professionalism", {}
                    )
                    sales_effectiveness = get_value_by_path(
                        rec, "scoring_result.scoring_detail.sales_effectiveness", {}
                    )
                    customer_experience = get_value_by_path(
                        rec, "scoring_result.scoring_detail.customer_experience", {}
                    )
                    compliance = get_value_by_path(rec, "scoring_result.scoring_detail.compliance", {})
                    check_list = get_value_by_path(rec, "prediction.raw_prediction.check_list", {})
                    sales_performance = get_value_by_path(rec, "prediction.raw_prediction.sales_performance", {})
                    customer_insight = get_value_by_path(rec, "prediction.raw_prediction.customer_insight", {})
                    logger.debug(f"Record [{idx}]: Extracted scoring details successfully")
                except Exception as extract_err:
                    logger.error(
                        f"Record [{idx}] ({filename}): Failed to extract scoring details: {extract_err}", exc_info=True
                    )
                    raise

                # Build support details
                try:
                    details_operations_and_professionalism = "\n".join(
                        [
                            f"call_opening_support: {get_value_by_path(rec, 'prediction.raw_prediction.operations_and_professionalism.call_opening.support_detail')}".strip(),
                            f"customer_identity_verification_support: {get_value_by_path(rec, 'prediction.raw_prediction.operations_and_professionalism.customer_identity_verification.support_detail')}".strip(),
                            f"language_and_tone_support: {get_value_by_path(rec, 'prediction.raw_prediction.operations_and_professionalism.language_and_tone.support_detail')}".strip(),
                            f"active_listening_support: {get_value_by_path(rec, 'prediction.raw_prediction.operations_and_professionalism.active_listening.support_detail')}".strip(),
                            f"call_closing_support: {get_value_by_path(rec, 'prediction.raw_prediction.operations_and_professionalism.call_closing.support_detail')}".strip(),
                        ]
                    )
                    details_sales_effectiveness = "\n".join(
                        [
                            f"customer_needs_analysis_support: {get_value_by_path(rec, 'prediction.raw_prediction.sales_effectiveness.customer_needs_analysis.support_detail')}".strip(),
                            f"offer_presentation_quality_support: {get_value_by_path(rec, 'prediction.raw_prediction.sales_effectiveness.offer_presentation_quality.support_detail')}".strip(),
                            f"effective_objection_handling_support: {get_value_by_path(rec, 'prediction.raw_prediction.sales_effectiveness.effective_objection_handling.support_detail')}".strip(),
                            f"sales_closing_attempt_support: {get_value_by_path(rec, 'prediction.raw_prediction.sales_effectiveness.sales_closing_attempt.support_detail')}".strip(),
                            f"cross_sell_upsell_support: {get_value_by_path(rec, 'prediction.raw_prediction.sales_effectiveness.cross_sell_upsell.support_detail')}".strip(),
                        ]
                    )
                    details_customer_experience = "\n".join(
                        [
                            f"positive_customer_experience_support: {get_value_by_path(rec, 'prediction.raw_prediction.customer_experience.positive_customer_experience.support_detail')}".strip(),
                            f"clarity_of_communication_support: {get_value_by_path(rec, 'prediction.raw_prediction.customer_experience.clarity_of_communication.support_detail')}".strip(),
                            f"building_trust_support: {get_value_by_path(rec, 'prediction.raw_prediction.customer_experience.building_trust.support_detail')}".strip(),
                        ]
                    )
                    details_compliance = "\n".join(
                        [
                            f"compliance_support: {get_value_by_path(rec, 'prediction.raw_prediction.compliance.compliance.support_detail')}".strip()
                        ]
                    )
                    logger.debug(f"Record [{idx}]: Built support detail strings")
                except Exception as detail_err:
                    logger.error(
                        f"Record [{idx}] ({filename}): Failed to build support details: {detail_err}", exc_info=True
                    )
                    raise

                # Determine status
                try:
                    prediction_status = get_value_by_path(rec, "prediction.status")
                    scoring_status = get_value_by_path(rec, "scoring_result.scoring_status")

                    if prediction_status != "SUCCESS":
                        status = prediction_status
                        raw_message = get_value_by_path(rec, "prediction.message")
                        # Clean message by replacing newlines for CSV compatibility
                        message = raw_message.replace("\n", " ").replace("\r", " ") if raw_message else None
                    elif scoring_status != "SUCCESS":
                        status = scoring_status
                        raw_message = get_value_by_path(rec, "scoring_result.scoring_message")
                        # Clean message by replacing newlines for CSV compatibility
                        message = raw_message.replace("\n", " ").replace("\r", " ") if raw_message else None
                    else:
                        status = "SUCCESS"
                        message = None

                    logger.debug(f"Record [{idx}]: Status={status}, Message={message}")
                except Exception as status_err:
                    logger.error(
                        f"Record [{idx}] ({filename}): Failed to determine status: {status_err}", exc_info=True
                    )
                    raise

                # Build customer information
                try:
                    duration_val = get_value_by_path(rec, "file_metadata.duration", None)
                    if duration_val is None:
                        logger.warning(f"Record [{idx}] ({filename}): file_metadata.duration is None")

                    model = get_value_by_path(rec, "prediction.model_version", "unknown_model")
                    usage_detail = {
                        idx: {
                            "model": model,
                            "token_input": get_value_by_path(rec, "prediction.token_input", {}),
                            "token_cached": get_value_by_path(rec, "prediction.token_cached", 0),
                            "token_output": get_value_by_path(rec, "prediction.token_output", {}),
                        }
                    }
                    cost_config = gemini_cost(api_type=self.DEFAULT_COST_TYPE, model_list=[model])
                    cost_detail = GeminiBatchModule.cal_gemini_cost(usage_detail=usage_detail, cost_config=cost_config)
                    cost_data = cost_detail.get(idx, {"cost_input": 0.0, "cost_output": 0.0})
                    cost_usd = round(cost_data["cost_input"] + cost_data["cost_output"], 6)

                    customer_information = {
                        "customer_information": {
                            "call_id": safe_cast_value(get_value_by_path(rec, "file_metadata.call_id", None), str),
                            "phone_number": safe_cast_value(
                                get_value_by_path(rec, "file_metadata.phone_number", None), str
                            ),
                            "call_month": safe_cast_value(
                                get_value_by_path(rec, "file_metadata.record_date", self.DEFAULT_RECORD_DATE)[:6], str
                            ),
                            "call_date": safe_cast_value(
                                get_value_by_path(rec, "file_metadata.record_date", self.DEFAULT_RECORD_DATE), str
                            ),
                            "cost_usd": cost_usd,
                            "call_duration_sec": safe_cast_value(duration_val, int),
                            "agent_id": safe_cast_value(get_value_by_path(rec, "file_metadata.agent_id", None), str),
                            "full_name": f"{get_value_by_path(rec, 'file_metadata.first_name', None)} {get_value_by_path(rec, 'file_metadata.last_name', None)}".strip(),
                            "true_dtac": "Dtac"
                            if get_value_by_path(rec, "file_metadata.provider", None) in ["D", "d"]
                            else "True",
                        }
                    }
                    logger.debug(f"Record [{idx}]: Built customer information")
                except Exception as customer_err:
                    logger.error(
                        f"Record [{idx}] ({filename}): Failed to build customer information: {customer_err}",
                        exc_info=True,
                    )
                    raise

                # Calculate operations_and_professionalism scores
                try:
                    op_sub_scores = {}
                    for key, value in operations_and_professionalism.get("detail", {}).items():
                        score_val = value.get("score", None)
                        if score_val is None:
                            logger.debug(
                                f"Record [{idx}] ({filename}): operations_and_professionalism.detail.{key}.score is None"
                            )
                        op_sub_scores[key] = safe_cast_value(score_val, int)

                    op_score_val = operations_and_professionalism.get("score", None)
                    if op_score_val is None:
                        logger.debug(f"Record [{idx}] ({filename}): operations_and_professionalism.score is None")
                    op_total_score = safe_cast_value(op_score_val, int)

                    op_max_val = operations_and_professionalism.get("max_score", None)
                    if op_max_val is None:
                        logger.debug(f"Record [{idx}] ({filename}): operations_and_professionalism.max_score is None")
                    op_total_max_score = safe_cast_value(op_max_val, int)

                    op_max_not_none_val = operations_and_professionalism.get("max_score_not_none", None)
                    if op_max_not_none_val is None:
                        logger.debug(
                            f"Record [{idx}] ({filename}): operations_and_professionalism.max_score_not_none is None"
                        )
                    op_total_max_score_not_none = safe_cast_value(op_max_not_none_val, int)

                    op_score_not_none_val = operations_and_professionalism.get("score_not_none", None)
                    if op_score_not_none_val is None:
                        logger.debug(
                            f"Record [{idx}] ({filename}): operations_and_professionalism.score_not_none is None"
                        )
                    op_score_not_none = safe_cast_value(op_score_not_none_val, int)

                    op_total_weight = (
                        round(((op_score_not_none / op_total_max_score_not_none) * op_total_max_score), 2)
                        if op_total_max_score_not_none
                        and op_total_max_score_not_none != 0
                        and op_score_not_none is not None
                        else None
                    )
                    logger.debug(
                        f"Record [{idx}]: Operations scores calculated - total={op_total_score}, weight={op_total_weight}"
                    )
                except Exception as op_err:
                    logger.error(
                        f"Record [{idx}] ({filename}): Failed to calculate operations_and_professionalism scores: {op_err}",
                        exc_info=True,
                    )
                    raise

                # Calculate sales_effectiveness scores
                try:
                    se_sub_scores = {}
                    for key, value in sales_effectiveness.get("detail", {}).items():
                        score_val = value.get("score", None)
                        if score_val is None:
                            logger.debug(f"Record [{idx}] ({filename}): sales_effectiveness.detail.{key}.score is None")
                        se_sub_scores[key] = safe_cast_value(score_val, int)

                    se_score_val = sales_effectiveness.get("score", None)
                    if se_score_val is None:
                        logger.debug(f"Record [{idx}] ({filename}): sales_effectiveness.score is None")
                    se_total_score = safe_cast_value(se_score_val, int)

                    se_max_val = sales_effectiveness.get("max_score", None)
                    if se_max_val is None:
                        logger.debug(f"Record [{idx}] ({filename}): sales_effectiveness.max_score is None")
                    se_total_max_score = safe_cast_value(se_max_val, int)

                    se_max_not_none_val = sales_effectiveness.get("max_score_not_none", None)
                    if se_max_not_none_val is None:
                        logger.debug(f"Record [{idx}] ({filename}): sales_effectiveness.max_score_not_none is None")
                    se_total_max_score_not_none = safe_cast_value(se_max_not_none_val, int)

                    se_score_not_none_val = sales_effectiveness.get("score_not_none", None)
                    if se_score_not_none_val is None:
                        logger.debug(f"Record [{idx}] ({filename}): sales_effectiveness.score_not_none is None")
                    se_score_not_none = safe_cast_value(se_score_not_none_val, int)

                    se_total_weight = (
                        round(((se_score_not_none / se_total_max_score_not_none) * se_total_max_score), 2)
                        if se_total_max_score_not_none
                        and se_total_max_score_not_none != 0
                        and se_score_not_none is not None
                        else None
                    )
                    logger.debug(
                        f"Record [{idx}]: Sales scores calculated - total={se_total_score}, weight={se_total_weight}"
                    )
                except Exception as se_err:
                    logger.error(
                        f"Record [{idx}] ({filename}): Failed to calculate sales_effectiveness scores: {se_err}",
                        exc_info=True,
                    )
                    raise

                # Calculate customer_experience scores
                try:
                    cx_sub_scores = {}
                    for key, value in customer_experience.get("detail", {}).items():
                        score_val = value.get("score", None)
                        if score_val is None:
                            logger.debug(f"Record [{idx}] ({filename}): customer_experience.detail.{key}.score is None")
                        cx_sub_scores[key] = safe_cast_value(score_val, int)

                    cx_score_val = customer_experience.get("score", None)
                    if cx_score_val is None:
                        logger.debug(f"Record [{idx}] ({filename}): customer_experience.score is None")
                    cx_total_score = safe_cast_value(cx_score_val, int)

                    cx_max_val = customer_experience.get("max_score", None)
                    if cx_max_val is None:
                        logger.debug(f"Record [{idx}] ({filename}): customer_experience.max_score is None")
                    cx_total_max_score = safe_cast_value(cx_max_val, int)

                    cx_max_not_none_val = customer_experience.get("max_score_not_none", None)
                    if cx_max_not_none_val is None:
                        logger.debug(f"Record [{idx}] ({filename}): customer_experience.max_score_not_none is None")
                    cx_total_max_score_not_none = safe_cast_value(cx_max_not_none_val, int)

                    cx_score_not_none_val = customer_experience.get("score_not_none", None)
                    if cx_score_not_none_val is None:
                        logger.debug(f"Record [{idx}] ({filename}): customer_experience.score_not_none is None")
                    cx_score_not_none = safe_cast_value(cx_score_not_none_val, int)

                    cx_total_weight = (
                        round(((cx_score_not_none / cx_total_max_score_not_none) * cx_total_max_score), 2)
                        if cx_total_max_score_not_none
                        and cx_total_max_score_not_none != 0
                        and cx_score_not_none is not None
                        else None
                    )
                    logger.debug(
                        f"Record [{idx}]: Customer experience scores calculated - total={cx_total_score}, weight={cx_total_weight}"
                    )
                except Exception as cx_err:
                    logger.error(
                        f"Record [{idx}] ({filename}): Failed to calculate customer_experience scores: {cx_err}",
                        exc_info=True,
                    )
                    raise

                # Calculate compliance scores
                try:
                    compliance_sub_scores = {}
                    for key, value in compliance.get("detail", {}).items():
                        score_val = value.get("score", None)
                        if score_val is None:
                            logger.debug(f"Record [{idx}] ({filename}): compliance.detail.{key}.score is None")
                        compliance_sub_scores[key] = safe_cast_value(score_val, int)

                    compliance_score_val = compliance.get("score", None)
                    if compliance_score_val is None:
                        logger.debug(f"Record [{idx}] ({filename}): compliance.score is None")
                    compliance_total_score = safe_cast_value(compliance_score_val, int)

                    compliance_max_val = compliance.get("max_score", None)
                    if compliance_max_val is None:
                        logger.debug(f"Record [{idx}] ({filename}): compliance.max_score is None")
                    compliance_total_max_score = safe_cast_value(compliance_max_val, int)

                    compliance_max_not_none_val = compliance.get("max_score_not_none", None)
                    if compliance_max_not_none_val is None:
                        logger.debug(f"Record [{idx}] ({filename}): compliance.max_score_not_none is None")
                    compliance_total_max_score_not_none = safe_cast_value(compliance_max_not_none_val, int)

                    compliance_score_not_none_val = compliance.get("score_not_none", None)
                    if compliance_score_not_none_val is None:
                        logger.debug(f"Record [{idx}] ({filename}): compliance.score_not_none is None")
                    compliance_score_not_none = safe_cast_value(compliance_score_not_none_val, int)

                    compliance_total_weight = (
                        round(
                            (
                                (compliance_score_not_none / compliance_total_max_score_not_none)
                                * compliance_total_max_score
                            ),
                            2,
                        )
                        if compliance_total_max_score_not_none
                        and compliance_total_max_score_not_none != 0
                        and compliance_score_not_none is not None
                        else None
                    )
                    logger.debug(
                        f"Record [{idx}]: Compliance scores calculated - total={compliance_total_score}, weight={compliance_total_weight}"
                    )
                except Exception as compliance_err:
                    logger.error(
                        f"Record [{idx}] ({filename}): Failed to calculate compliance scores: {compliance_err}",
                        exc_info=True,
                    )
                    raise

                # Process checklist based on campaign
                try:
                    campaign_name = get_value_by_path(rec, "prediction.raw_prediction.campaign_name", None)
                    if campaign_name == "13_True_Promo_End":
                        check_list_results = {
                            "operation_check_list": get_value_by_path(check_list, "operation_check_list", None),
                        }
                        details_check_list = (
                            f"operation_check_list_support: {get_value_by_path(check_list, 'support_detail', None)}"
                        )
                    else:
                        check_list_results = {}
                        details_check_list = ""
                    logger.debug(f"Record [{idx}]: Processed checklist for campaign={campaign_name}")
                except Exception as checklist_err:
                    logger.error(
                        f"Record [{idx}] ({filename}): Failed to process checklist: {checklist_err}", exc_info=True
                    )
                    raise

                # Build scoring result
                try:
                    scoring_result = {
                        "operations_and_professionalism": {
                            "metrics": op_sub_scores,
                            "total_score": op_total_score,
                            "total_weight_score": op_total_weight,
                            "details": details_operations_and_professionalism.strip(),
                        },
                        "sales_effectiveness": {
                            "metrics": se_sub_scores,
                            "total_score": se_total_score,
                            "total_weight_score": se_total_weight,
                            "details": details_sales_effectiveness.strip(),
                        },
                        "customer_experience": {
                            "metrics": cx_sub_scores,
                            "total_score": cx_total_score,
                            "total_weight_score": cx_total_weight,
                            "details": details_customer_experience.strip(),
                        },
                        "compliance": {
                            "metrics": compliance_sub_scores,
                            "total_score": compliance_total_score,
                            "total_weight_score": compliance_total_weight,
                            "details": details_compliance.strip(),
                        },
                        "check_list": {"metrics": check_list_results, "details": details_check_list.strip()},
                    }
                    logger.debug(f"Record [{idx}]: Built scoring result structure")
                except Exception as scoring_err:
                    logger.error(
                        f"Record [{idx}] ({filename}): Failed to build scoring result: {scoring_err}", exc_info=True
                    )
                    raise

                # Calculate total scores and build summary
                try:
                    total_max_score = get_value_by_path(rec, "scoring_result.max_score", None)
                    total_max_score_no_null = get_value_by_path(rec, "scoring_result.max_score_not_none", None)
                    total_score_no_null = get_value_by_path(rec, "scoring_result.total_score", None)
                    total_weight_score = (
                        round(((total_score_no_null / total_max_score_no_null) * total_max_score), 2)
                        if total_max_score_no_null and total_max_score_no_null != 0 and total_score_no_null is not None
                        else None
                    )
                    call_status = get_value_by_path(rec, "prediction.raw_prediction.call_status", None)

                    summary_result = {
                        "summary": {
                            "total_max_score": total_max_score,
                            "total_score": total_weight_score,
                            "total_max_score_no_null": total_max_score_no_null,
                            "total_score_no_null": total_score_no_null,
                            "agent_strength": get_value_by_path(rec, "prediction.raw_prediction.agent_strength", None),
                            "agent_weakness": get_value_by_path(rec, "prediction.raw_prediction.agent_weakness", None),
                            "sales_performance": sales_performance,
                            "customer_insight": customer_insight,
                            "call_status": call_status,
                        }
                    }
                    logger.debug(f"Record [{idx}]: Calculated total scores - weight={total_weight_score}")
                except Exception as summary_err:
                    logger.error(f"Record [{idx}] ({filename}): Failed to build summary: {summary_err}", exc_info=True)
                    raise

                # Combine all results
                try:
                    if call_status == "Abandoned":
                        scoring_result = force_none_value_dict(scoring_result)
                        summary_result = force_none_value_dict(
                            summary_result, exclude_keys=["sales_performance", "customer_insight", "call_status"]
                        )

                    full_result = {"qa_call_data": {**customer_information, **scoring_result, **summary_result}}

                    result.append(
                        {
                            "filename": filename,
                            "summary": json.dumps(full_result, ensure_ascii=False),
                            "updated_dt": updated_dt,
                            "status": status,
                            "error_code": message,
                        }
                    )
                    success_count += 1
                    logger.debug(f"Record [{idx}] ({filename}): Successfully formatted")
                except Exception as combine_err:
                    logger.error(
                        f"Record [{idx}] ({filename}): Failed to combine results: {combine_err}", exc_info=True
                    )
                    raise

            except Exception as record_err:
                # Clean error message by replacing newlines with spaces for better CSV compatibility
                error_msg = str(record_err).replace("\n", " ").replace("\r", " ")

                result.append(
                    {
                        "filename": filename,
                        "summary": None,
                        "updated_dt": updated_dt,
                        "status": "FAILED",
                        "error_code": error_msg,
                    }
                )
                failed_count += 1
                logger.error(f"Record [{idx}]: Failed to format output: {record_err}", exc_info=True)
                logger.warning(f"Skipping problematic record [{idx}] and continuing")
                continue

        # Process batch processing log for failed files
        try:
            log_path = resolve_env(get_value_by_path(self.sharepoint, "control.batch_processing_log_file"))
            logger.debug(f"Checking batch processing log at: {log_path}")

            if self.sharepoint_control.is_item_exists(item_path=log_path):
                logger.debug("Found batch processing log, checking for failed files")
                try:
                    batch_processing_log = self.sharepoint_control.get_item_by_path(log_path)
                    with io.BytesIO(batch_processing_log.content) as log_buffer:
                        batch_processing_log_df = pd.read_csv(log_buffer)

                    logger.debug(f"Batch processing log has {len(batch_processing_log_df)} total records")

                    batch_processing_log_df = (
                        batch_processing_log_df.sort_values(
                            by=["filename", "updated_dt"], ascending=[True, False]
                        ).drop_duplicates(subset=["filename"], keep="first")
                    )[["batch_job_id", "batch_job_display_name", "filename", "status", "error_message", "updated_dt"]]
                    batch_job_detail_df = batch_processing_log_df[
                        batch_processing_log_df["filename"].isin(process_files)
                    ][["batch_job_id", "batch_job_display_name"]].drop_duplicates()
                    logger.debug(f"Found {len(batch_job_detail_df)} unique batch jobs for processed files")

                    failed_files_df = batch_processing_log_df.merge(
                        batch_job_detail_df, on=["batch_job_id", "batch_job_display_name"], how="inner"
                    )
                    failed_files_df = failed_files_df[failed_files_df["status"] == "FAILED"]

                    logger.debug(f"Found {len(failed_files_df)} failed files in batch processing log")

                    appended_count = 0
                    for failed_file in failed_files_df.itertuples():
                        try:
                            # Clean error message by replacing newlines for CSV compatibility
                            clean_error = (
                                failed_file.error_message.replace("\n", " ").replace("\r", " ")
                                if failed_file.error_message
                                else None
                            )

                            result.append(
                                {
                                    "filename": failed_file.filename,
                                    "summary": None,
                                    "updated_dt": updated_dt,
                                    "status": "FAILED",
                                    "error_code": clean_error,
                                }
                            )
                            appended_count += 1
                            failed_count += 1
                        except Exception as append_err:
                            # Clean error message
                            clean_append_err = str(append_err).replace("\n", " ").replace("\r", " ")

                            result.append(
                                {
                                    "filename": None,
                                    "summary": None,
                                    "updated_dt": updated_dt,
                                    "status": "FAILED",
                                    "error_code": f"Unknown error during log append {clean_append_err}",
                                }
                            )
                            logger.error(f"Failed to append failed file from log: {append_err}", exc_info=True)
                            failed_count += 1
                            continue

                    logger.debug(f"Appended {appended_count} failed files from batch processing log")

                except Exception as log_err:
                    logger.error(f"Failed to process batch processing log for failed files: {log_err}", exc_info=True)
                    logger.warning("Skipping appending failed files from batch processing log")
            else:
                logger.warning(f"Batch processing log not found at: {log_path}")
        except Exception as log_path_err:
            logger.error(f"Failed to resolve batch processing log path: {log_path_err}", exc_info=True)
            logger.warning("Skipping batch processing log check")

        logger.info(
            f"Output formatting complete: {success_count} successful, {failed_count} failed, total results: {len(result)}"
        )

        if not result:
            logger.error("No records were successfully formatted - all records failed")
            raise Exception("Failed to format any output records")

        if failed_count > 0:
            logger.warning(
                f"{failed_count} record(s) failed to format but continuing with {success_count} successful records"
            )
            logger.warning(f"Success rate: {(success_count / (success_count + failed_count) * 100):.2f}%")
        else:
            logger.info("All records formatted successfully")

        return result

    def _archive_files(
        self, execution_dt: datetime, df: pd.DataFrame, list_batchs: list[str], failed_batches: list[str]
    ):
        logger.debug("Starting file archival process")
        logger.debug(f"Processing {len(df['record_date'].unique())} unique record dates")

        for record_date in df["record_date"].unique():
            # Archive successfully processed voice files
            logger.debug(f"Archiving successfully processed voice files for record date: {record_date}")
            try:
                partition_df = df[df["record_date"] == record_date]
                success_records = partition_df[partition_df["status"] == "SUCCESS"].drop_duplicates(
                    subset=["file_name"]
                )
                logger.debug(f"Found {len(success_records)} successfully processed files to archive")
            except Exception as filter_err:
                logger.error(f"Failed to filter success records: {filter_err}", exc_info=True)
                logger.warning("Skipping voice file archival for this partition")
                continue

            archived_success_count = 0
            archived_skipped_count = 0
            deleted_success_count = 0
            deleted_skipped_count = 0

            # Resolve folder paths once (non-critical errors - continue with empty lists)
            try:
                processing_voice_folder = resolve_date(
                    text=self.gcs["processing_voice_folder"],
                    replace_date=record_date,
                )
                archive_voice_folder = resolve_date(
                    text=self.gcs["archive_voice_folder"],
                    replace_date=record_date,
                )
                input_voice_folder = resolve_date(
                    text=self.gcs["input_folder"],
                    replace_date=record_date,
                )
                logger.debug(f"Processing folder: {processing_voice_folder}")
                logger.debug(f"Archive folder: {archive_voice_folder}")
                logger.debug(f"Input folder: {input_voice_folder}")
            except Exception as path_err:
                logger.error(f"Failed to resolve folder paths: {path_err}", exc_info=True)
                logger.warning("Skipping voice file archival due to path resolution error")
                continue

            # List files in folders (non-critical - continue with empty lists)
            try:
                if self.gcs_module.is_dir_exists(dir_path=processing_voice_folder):
                    files_in_processing = self.gcs_module.list_files(prefix=processing_voice_folder)
                    files_in_processing = [os.path.basename(f) for f in files_in_processing]
                    logger.debug(f"Found {len(files_in_processing)} files in processing folder")
                else:
                    files_in_processing = []
                    logger.warning(f"Processing voice folder does not exist: {processing_voice_folder}")
            except Exception as list_err:
                logger.error(f"Error listing processing folder: {list_err}", exc_info=True)
                files_in_processing = []

            try:
                if self.gcs_module.is_dir_exists(dir_path=input_voice_folder):
                    files_in_input = self.gcs_module.list_files(prefix=input_voice_folder)
                    files_in_input = [os.path.basename(f) for f in files_in_input]
                    logger.debug(f"Found {len(files_in_input)} files in input folder")
                else:
                    files_in_input = []
                    logger.warning(f"Input voice folder does not exist: {input_voice_folder}")
            except Exception as list_err:
                logger.error(f"Error listing input folder: {list_err}", exc_info=True)
                files_in_input = []

            # Batch copy: archive all matching files from processing folder in parallel
            files_in_processing_set = set(files_in_processing)
            files_in_input_set = set(files_in_input)
            to_archive = [
                f"{processing_voice_folder}/{r.file_name}"
                for r in success_records.itertuples()
                if r.file_name in files_in_processing_set
            ]
            archived_skipped_count = len(success_records) - len(to_archive)
            if to_archive:
                try:
                    copy_result = asyncio.run(
                        self.gcs_module.copy_files_batch(
                            source_files=to_archive,
                            destination_prefix=archive_voice_folder,
                        )
                    )
                    archived_success_count = copy_result.get("success", 0)
                    archived_skipped_count += copy_result.get("failed", 0)
                    if copy_result.get("failed", 0) > 0:
                        logger.warning(f"Batch archive copy: {copy_result['failed']} file(s) failed")
                    logger.debug(f"Batch archived {archived_success_count} voice files to {archive_voice_folder}")
                    # Delete successfully copied files from processing folder
                    for src in to_archive:
                        try:
                            self.gcs_module.delete_file(file_path=src)
                        except Exception as del_err:
                            logger.error(f"Failed to delete processing file '{src}': {del_err}", exc_info=True)
                except Exception as archive_err:
                    logger.error(f"Batch archive failed: {archive_err}", exc_info=True)
                    archived_skipped_count += len(to_archive)
            else:
                logger.debug("No processing voice files to archive")

            # Delete from input folder (sequential — no batch delete in GCSModule)
            for r in success_records.itertuples():
                try:
                    if r.file_name in files_in_input_set:
                        self.gcs_module.delete_file(file_path=f"{input_voice_folder}/{r.file_name}")
                        deleted_success_count += 1
                        logger.debug(f"Deleted voice file from input folder: {r.file_name}")
                    else:
                        logger.debug(f"Voice file not found in input folder: {r.file_name}")
                        deleted_skipped_count += 1
                except Exception as delete_err:
                    logger.error(
                        f"Failed to delete voice file '{r.file_name}' from input folder: {delete_err}", exc_info=True
                    )
                    deleted_skipped_count += 1

            logger.info(
                f"Voice file archival complete. Archived: {archived_success_count}, Skipped: {archived_skipped_count}"
            )
            logger.info(
                f"Voice file deletion from input complete. Deleted: {deleted_success_count}, Skipped: {deleted_skipped_count}"
            )

        # Archive batch files
        logger.info("Archiving processed batch files")
        if failed_batches:
            logger.warning(f"Excluding {len(failed_batches)} failed batches from archival")
            for batch in failed_batches:
                logger.warning(f"Failed batch: {batch}")

        # Filter out failed batches from archive list
        archive_batch_list = [batch for batch in list_batchs if batch not in failed_batches]
        logger.debug(f"Archiving {len(archive_batch_list)} processed batch files...")

        archived_batch_count = 0
        failed_archive_count = 0
        output_folders = set()  # Collect unique output folders

        for idx, batch in enumerate(archive_batch_list, 1):
            try:
                try:
                    archive_batch_folder = resolve_date(
                        text=self.gcs["archive_batch_folder"],
                        replace_date=execution_dt.strftime("%Y-%m-%d"),
                    )
                except Exception as path_err:
                    logger.error(f"Failed to resolve archive path for batch {batch}: {path_err}")
                    failed_archive_count += 1
                    continue

                # Extract last two path components for archive structure
                try:
                    parts = Path(batch).parts
                    batch_path = "/".join(parts[-2:])
                    destination = f"{archive_batch_folder}/{batch_path}"
                except Exception as path_parse_err:
                    logger.error(f"Failed to parse batch path '{batch}': {path_parse_err}")
                    failed_archive_count += 1
                    continue

                logger.debug(f"Archiving batch [{idx}/{len(archive_batch_list)}]: {batch} -> {destination}")
                self.gcs_module.move_file(
                    source_path=batch,
                    destination_path=destination,
                )
                archived_batch_count += 1
                logger.debug(f"Archived batch [{idx}/{len(archive_batch_list)}]: {batch_path}")

                # Collect output folder for later cleanup
                try:
                    output_folders.add(os.path.dirname(batch))
                except Exception as folder_err:
                    logger.debug(f"Could not extract folder from batch path: {folder_err}")

            except Exception as batch_archive_err:
                logger.error(f"Failed to archive batch file '{batch}': {batch_archive_err}", exc_info=True)
                failed_archive_count += 1
                continue

        logger.info(f"Batch archival complete. Archived: {archived_batch_count}, Failed: {failed_archive_count}")

        logger.debug("Cleaning up processed output directories")

        deleted_output_count = 0
        failed_delete_count = 0
        logger.debug(f"Checking {len(output_folders)} output folders for cleanup...")

        for output_folder in output_folders:
            try:
                if not self.gcs_module.is_dir_exists(dir_path=output_folder):
                    logger.debug(f"Output folder does not exist, skipping: {output_folder}")
                    deleted_output_count += 1  # Count as success since it's already gone
                    continue

                logger.debug(f"Cleaning up output folder: {output_folder}")
                self.gcs_module.delete_dir(dir_path=output_folder)
                deleted_output_count += 1
                logger.debug(f"Cleaned up output folder: {output_folder}")

            except Exception as delete_folder_err:
                logger.error(f"Failed to cleanup output folder '{output_folder}': {delete_folder_err}", exc_info=True)
                failed_delete_count += 1

        logger.info(
            f"Output directory cleanup complete. Cleaned: {deleted_output_count}, Skipped: {failed_delete_count}"
        )

        # Final summary
        if failed_batches:
            logger.warning(f"Retrieve Batch Task completed with {len(failed_batches)} failed batches")
        else:
            logger.info("Retrieve Batch Task completed successfully")

    def _insert_log_record(self, df: pd.DataFrame):
        logger.debug("Starting log record insertion")
        default_type = "AI Classification"
        default_user_id = "daisyrpa"
        default_source = "SharePoint"

        execution_dt = self.get_package("execution_dt", None)

        # Transaction log is critical - raise error if it fails
        try:
            logger.debug("Creating transaction log records")
            transaction_log_df = self._transaction_log(
                default_type=default_type,
                default_user_id=default_user_id,
                default_source=default_source,
                prediction_df=df,
                execution_dt=execution_dt,
            )
            logger.debug("Transaction log creation completed successfully")
        except Exception as trans_err:
            logger.error(f"Critical error creating transaction log: {trans_err}", exc_info=True)
            raise Exception(f"Transaction log creation failed: {trans_err}") from trans_err

        # Performance log is non-critical - skip if it fails but continue execution
        if transaction_log_df is not None and not transaction_log_df.empty:
            try:
                logger.debug("Creating performance log records")
                self._performance_log(transaction_log_df=transaction_log_df)
                logger.debug("Performance log creation completed successfully")
            except Exception as perf_err:
                logger.error(f"Non-critical error creating performance log: {perf_err}", exc_info=True)
                logger.warning("Skipping performance log creation due to error, continuing execution")
        else:
            logger.warning("Transaction log DataFrame is None or empty. Skipping performance log creation.")

    def _transaction_log(
        self,
        default_type: str,
        default_user_id: str,
        default_source: str,
        prediction_df: pd.DataFrame,
        execution_dt: datetime,
    ) -> pd.DataFrame:
        """
        Create transaction log entries based on prediction DataFrame.
        Parameters:
            default_type (str): The default transaction type.
            default_user_id (str): The default user ID for the transaction.
            default_source (str): The default source of the transaction.
            prediction_df (pd.DataFrame): The DataFrame containing prediction results.
            execution_dt (datetime): The pipeline execution datetime, used to determine the log file path.
        Returns:
            pd.DataFrame: The updated DataFrame after logging transactions.
        Raises:
            ValueError: If required columns are missing in the prediction DataFrame.
            Exception: For any other unexpected errors during transaction logging.
        """
        logger.debug(f"Processing {len(prediction_df)} prediction records for transaction logging")
        logger.debug(f"DataFrame columns: {list(prediction_df.columns)}")

        model_pricing = gemini_cost(
            api_type=self.DEFAULT_COST_TYPE, model_list=prediction_df["model_version"].unique().tolist()
        )

        # Validate required columns exist
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
            logger.error(f"Missing required columns in prediction_df: {missing_columns}")
            raise ValueError(f"Cannot create transaction log: missing columns {missing_columns}")

        logger.debug("Extracting usage data from prediction DataFrame")
        logger.debug("Transaction log processing in STRICT MODE - will fail on any record error")
        log_payload = []
        usage_df = prediction_df[required_columns].copy()
        usage_dict = usage_df.to_dict(orient="records")

        # Pre-resolve Verint site name
        verint_site_name = resolve_env(get_value_by_path(self.verint_access, "site_name", ""))

        for idx, record in enumerate(usage_dict, 1):
            try:
                # Validate critical fields - STRICT MODE
                if not record.get("file_name"):
                    error_msg = f"Record {idx}: Missing required field 'file_name'"
                    logger.error(error_msg)
                    raise ValueError(error_msg)

                if not record.get("full_path"):
                    logger.warning(f"Record {idx} ({record['file_name']}): Missing full_path, URL will be incomplete")
                    source_files_url = f"https://{self.verint_site}/sites/{verint_site_name}/"
                else:
                    source_files_url = f"https://{self.verint_site}/sites/{verint_site_name}/{record['full_path']}"

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
                    for _token_type, token_count in usage.get("token_input", {}).items():
                        if str(token_count).isdigit():
                            token_usage_input += int(token_count)
                    for _token_type, token_count in usage.get("token_output", {}).items():
                        if str(token_count).isdigit():
                            token_usage_output += int(token_count)

                cost_detail = GeminiBatchModule.cal_gemini_cost(
                    usage_detail=usage_detail,
                    cost_config=model_pricing,
                )
                cost_input = cost_detail[record["file_name"]].get("cost_input", 0.0) or 0.0
                cost_output = cost_detail[record["file_name"]].get("cost_output", 0.0) or 0.0
                total_cost_usd = cost_input + cost_output

                # Create transaction payload with safe defaults
                try:
                    # Pre-process None values to avoid safe_cast_value warnings
                    duration_val = record.get("duration")
                    if duration_val is None:
                        file_metadata_sec = 0
                    elif isinstance(duration_val, int):
                        file_metadata_sec = duration_val
                    else:
                        file_metadata_sec = safe_cast_value(duration_val, int, 0)

                    # Ensure cost is float
                    cost_val = safe_cast_value(total_cost_usd, float, 0.0)

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
                            storage_path=source_files_url,
                            folder=str(record.get("folder", "")),
                            filename=str(record.get("file_name", "")),
                            file_metadata_sec=file_metadata_sec,
                            status_pass_failed_retry="Pass" if record.get("status") == "SUCCESS" else "Failed",
                            error_log_if=str(record.get("message", "")),
                            token_usage_input=token_usage_input,
                            token_usage_output=token_usage_output,
                            total_cost_usd=cost_val,
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
                except Exception as payload_err:
                    logger.error(
                        f"CRITICAL: Record {idx} ({record['file_name']}): Failed to create TransactionPayload: {payload_err}",
                        exc_info=True,
                    )
                    raise Exception(f"Transaction log creation failed at record {idx}: {payload_err}") from payload_err

            except Exception as record_err:
                logger.error(f"CRITICAL: Record {idx}: Unexpected error processing record: {record_err}", exc_info=True)
                logger.debug(f"Problematic record data: {record}")
                raise Exception(f"Transaction log creation failed at record {idx}: {record_err}") from record_err

        logger.info(f"All {len(log_payload)} transaction payload records created successfully (STRICT MODE)")

        if not log_payload:
            error_msg = "No transaction records were created from prediction data"
            logger.error(error_msg)
            raise Exception(error_msg)

        # Insert transaction logs
        try:
            logger.debug("Calling audit_log_module.log_transaction...")
            new_transaction_df = pd.DataFrame([log.to_dict() for log in log_payload])
            new_transaction_df = ensure_df_schema(new_transaction_df, Metadata.TRANSACTION_LOG_SCHEMA)
            logger.debug(
                f"Transaction log DataFrame created with {len(new_transaction_df)} rows and {len(new_transaction_df.columns)} columns"
            )
        except Exception as log_err:
            logger.error(f"Failed to create transaction log DataFrame: {log_err}", exc_info=True)
            raise Exception(f"Critical error: Cannot create transaction log: {log_err}") from log_err

        # Upload all records to a single log file keyed by execution_dt (pipeline run date),
        # not by data_date (voice call date). data_date remains a field value inside the CSV.
        try:
            transaction_path = resolve_date(
                text=resolve_env(get_value_by_path(self.sharepoint, "control.transaction_log_file")),
                replace_date=execution_dt,
            )
            logger.debug(f"Transaction log path (execution_dt): {transaction_path}")

            if self.sharepoint_control.is_item_exists(item_path=transaction_path):
                logger.debug("Existing transaction log found, merging with new data")
                try:
                    existing_log = self.sharepoint_control.get_item_by_path(transaction_path)
                    with io.BytesIO(existing_log.content) as existing_buffer:
                        existing_df = pd.read_csv(existing_buffer)
                    logger.debug(f"Existing log has {len(existing_df)} rows")
                    combined_df = pd.concat([existing_df, new_transaction_df], ignore_index=True)
                    logger.debug(f"Combined DataFrame has {len(combined_df)} rows")
                except Exception as merge_err:
                    logger.error(f"Error merging with existing transaction log: {merge_err}", exc_info=True)
                    raise Exception(f"Cannot merge transaction log: {merge_err}") from merge_err
            else:
                logger.debug("No existing transaction log found, creating new log")
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

        self._cache_oper_log["transaction_df"] = combined_df[combined_df["type"] == default_type]
        self._cache_oper_log["process_date"] = (
            new_transaction_df["start_time"].dt.date.unique().tolist()
            if "start_time" in new_transaction_df.columns
            else []
        )

        return new_transaction_df

    def _performance_log(self, transaction_log_df: pd.DataFrame) -> None:
        """
        Create performance log entries based on transaction log DataFrame.
        Parameters:
            transaction_log_df (pd.DataFrame): The DataFrame containing transaction log records.
        Returns:
            None
        Raises:
            ValueError: If required columns are missing in the transaction log DataFrame.
        """
        logger.debug(f"Processing {len(transaction_log_df)} transaction log records for performance logging")
        logger.debug(f"DataFrame columns: {list(transaction_log_df.columns)}")

        # Validate required columns exist
        required_columns = [
            "data_date",
            "start_time",
            "load_dt",
            "gcp_project_id",
            "gcp_project_name",
            "status_pass_failed_retry",
            "latency_ms",
        ]
        missing_columns = [col for col in required_columns if col not in transaction_log_df.columns]
        if missing_columns:
            logger.error(f"Missing required columns in transaction_log_df: {missing_columns}")
            raise ValueError(f"Cannot create performance log: missing columns {missing_columns}")

        logger.debug("Extracting and aggregating data from transaction log DataFrame")
        pre_df = transaction_log_df[required_columns].copy()

        # Convert start_time to datetime, setting problematic values to None
        pre_df["start_time"] = pd.to_datetime(pre_df["start_time"], errors="coerce")
        invalid_count = pre_df["start_time"].isna().sum()
        if invalid_count > 0:
            logger.warning(f"{invalid_count} records have invalid start_time values, setting them to None")

        # Format valid datetime values, keep None for invalid ones (don't filter out)
        pre_df["start_time"] = pre_df["start_time"].apply(lambda x: x.strftime("%Y%m%d") if pd.notna(x) else None)

        performance_df = pre_df.groupby(
            ["data_date", "start_time", "load_dt", "gcp_project_id", "gcp_project_name"],
            as_index=False,
            dropna=False,  # Keep groups with None values
        ).agg(
            total_transactions=("status_pass_failed_retry", "count"),
            total_completed=("status_pass_failed_retry", lambda x: (x == "Pass").sum()),
            total_failed=("status_pass_failed_retry", lambda x: (x == "Failed").sum()),
            total_runtime=("latency_ms", "sum"),
        )

        logger.debug(f"Aggregated performance data: {len(performance_df)} records")
        logger.debug("Performance log processing in ERROR-TOLERANT MODE - will skip failed records")
        performance_dict = performance_df.to_dict(orient="records")
        log_payload = []
        skipped_records = 0

        for idx, record in enumerate(performance_dict, 1):
            try:
                performance_log = PerformanceLogSchema.from_dict(
                    PerformancePayload(
                        data_date=str(record.get("data_date", "")),
                        run_date=str(record.get("start_time", "")),
                        load_dt=str(record.get("load_dt", "")),
                        gcp_project_id=str(record.get("gcp_project_id", "")),
                        gcp_project_name=str(record.get("gcp_project_name", "")),
                        total_transaction=int(record.get("total_transactions", 0)),
                        total_completed=int(record.get("total_completed", 0)),
                        total_failed=int(record.get("total_failed", 0)),
                        total_runtime=str(record.get("total_runtime", "0.0")),
                    )
                )
                performance_log.stamp_completion(action="Create Performance Log")
                log_payload.append(performance_log)
            except Exception as payload_err:
                logger.error(
                    f"Performance record {idx}: Failed to create PerformancePayload: {payload_err}", exc_info=True
                )
                logger.warning(f"Skipping performance record {idx} and continuing...")
                skipped_records += 1
                continue

        logger.info(
            f"Performance payload created: {len(log_payload)} succeeded, {skipped_records} skipped (ERROR-TOLERANT MODE)"
        )

        if skipped_records > 0:
            logger.warning(f"Note: {skipped_records} performance records were skipped due to errors")

        if not log_payload:
            logger.warning(f"All {len(performance_dict)} performance records failed to process - returning None")
            return

        # Insert performance logs
        try:
            logger.debug("Calling audit_log_module.log_performance...")
            df = pd.DataFrame(log.to_dict() for log in log_payload)
            df = ensure_df_schema(df, Metadata.PERFORMANCE_LOG_SCHEMA)
            logger.info(f"Performance log DataFrame created with {len(df)} rows and {len(df.columns)} columns")
        except Exception as log_err:
            logger.error(f"Failed to create performance log DataFrame: {log_err}", exc_info=True)
            raise Exception(f"Cannot create performance log: {log_err}") from log_err

        # Filter out empty data_date values
        try:
            data_dates = [d for d in df["data_date"].unique().tolist() if d and str(d).strip()]
            logger.debug(f"Processing performance logs for {len(data_dates)} unique dates: {data_dates}")
        except Exception as date_err:
            logger.error(f"Failed to extract unique data_dates: {date_err}", exc_info=True)
            raise Exception(f"Cannot process performance logs: {date_err}") from date_err

        # Upload logs to SharePoint by date
        upload_success_count = 0
        upload_failed_count = 0

        for data_date in data_dates:
            try:
                performance_path = resolve_date(
                    text=resolve_env(get_value_by_path(self.sharepoint, "control.performance_log_file")),
                    replace_date=data_date,
                )
                logger.debug(f"Processing performance log for date {data_date}: {performance_path}")

                # Get date partition
                date_partition_df = df[df["data_date"] == data_date]
                logger.debug(f"Date partition has {len(date_partition_df)} records")

                if self.sharepoint_control.is_item_exists(item_path=performance_path):
                    logger.debug(f"Existing performance log found for date {data_date}, merging with new data")
                    try:
                        existing_log = self.sharepoint_control.get_item_by_path(performance_path)
                        with io.BytesIO(existing_log.content) as existing_buffer:
                            existing_df = pd.read_csv(existing_buffer)

                        logger.debug(
                            f"Existing log has {len(existing_df)} rows with {len(existing_df.columns)} columns"
                        )

                        combined_df = pd.concat([existing_df, date_partition_df], ignore_index=True)
                        logger.debug(f"Combined DataFrame has {len(combined_df)} rows before deduplication")
                    except Exception as merge_err:
                        logger.error(f"Error merging with existing performance log: {merge_err}", exc_info=True)
                        raise Exception(
                            f"Cannot merge performance log for date {data_date}: {merge_err}"
                        ) from merge_err
                else:
                    logger.debug(f"No existing performance log found for date {data_date}, creating new log")
                    combined_df = date_partition_df

                # Prepare and upload CSV - use combined_df to preserve all records
                try:
                    combined_df["data_date"] = combined_df["data_date"].astype(str)
                    combined_df = combined_df.fillna("").sort_values(
                        by=["load_dt", "data_date"], ascending=[False, False]
                    )
                    combined_df = ensure_df_schema(combined_df, list(df.columns))
                    combined_df = replace_nan_with_default(combined_df, default_value="")

                    csv_buffer = io.BytesIO()
                    combined_df.to_csv(csv_buffer, index=False, encoding="utf-8-sig")
                    csv_buffer.seek(0)

                    self.sharepoint_control.upload_file(
                        upload_path=performance_path,
                        content=csv_buffer.read(),
                    )
                    logger.info(
                        f"Successfully uploaded performance log to {performance_path} with {len(combined_df)} total records"
                    )
                    upload_success_count += 1
                except Exception as upload_err:
                    logger.error(f"Failed to upload performance log for date {data_date}: {upload_err}", exc_info=True)
                    upload_failed_count += 1

            except Exception as date_err:
                logger.error(f"Failed to process performance log for date {data_date}: {date_err}", exc_info=True)
                upload_failed_count += 1
                continue

        logger.info(f"Performance log upload complete: {upload_success_count} successful, {upload_failed_count} failed")

        if upload_failed_count > 0:
            logger.warning(f"{upload_failed_count} performance log(s) failed to upload to SharePoint")
        else:
            logger.info("All performance logs uploaded/updated successfully")

    def post_execute(self, result=None):  # noqa: ARG002
        """
        Post-execution hook for the task. This method is called after the main execution logic is completed. It is responsible for stamping AI-Operation logs based on the transaction log data.
        """
        logger.info("Stamping AI-Operation logs")
        if not self._cache_oper_log.get("process_date"):
            logger.warning("No process_date found for stamping logs - skipping logging")
            return

        transaction_df = self._cache_oper_log.get("transaction_df")
        if transaction_df is None or transaction_df.empty:
            logger.warning("No transaction log DataFrame found for stamping logs - skipping logging")
            return
        try:
            required_columns = ["start_time", "end_time", "gcp_project_id", "status_pass_failed_retry", "latency_ms"]
            transaction_df = ensure_df_schema(transaction_df, required_columns).copy()
            transaction_df["start_time"] = pd.to_datetime(
                transaction_df["start_time"], errors="coerce", utc=True
            ).dt.tz_convert(LogTimeStamper.CONFIG_TIMEZONE)
            transaction_df["end_time"] = pd.to_datetime(
                transaction_df["end_time"], errors="coerce", utc=True
            ).dt.tz_convert(LogTimeStamper.CONFIG_TIMEZONE)
            transaction_df["latency_ms"] = pd.to_numeric(transaction_df["latency_ms"], errors="coerce")
            transaction_df["process_date"] = transaction_df["start_time"].dt.date
            process_dates = self._cache_oper_log["process_date"]
            for process_date in process_dates:
                filtered_df = transaction_df[(transaction_df["process_date"] == process_date)]

                if filtered_df.empty:
                    logger.warning(
                        f"No transaction records found for process_date {process_date} - skipping log stamping"
                    )
                    continue

                log_df = filtered_df.groupby(["process_date", "gcp_project_id"], as_index=False, dropna=False).agg(
                    total_transaction=("status_pass_failed_retry", "count"),
                    total_success_transaction=("status_pass_failed_retry", lambda x: (x == "Pass").sum()),
                    total_failed_transaction=("status_pass_failed_retry", lambda x: (x == "Failed").sum()),
                    average_response_time_sec=(
                        "latency_ms",
                        lambda x: round(x.mean() / 1000, 2) if pd.notna(x.mean()) else 0.0,
                    ),
                    min_start_time=("start_time", "min"),
                    max_end_time=("end_time", "max"),
                )
                log_df["total_runtime_sec"] = (
                    (
                        pd.to_datetime(log_df["max_end_time"], errors="coerce")
                        - pd.to_datetime(log_df["min_start_time"], errors="coerce")
                    )
                    .dt.total_seconds()
                    .round(2)
                )
                log_df = log_df.drop(columns=["min_start_time", "max_end_time"])
                env = os.environ.get("ENVIRONMENT", "").lower()
                if env == "prod":
                    log_df["environment"] = "production"
                elif env == "nprd":
                    log_df["environment"] = "non-production"
                else:
                    log_df["environment"] = env or "unknown"
                log_df = log_df.rename(columns={"gcp_project_id": "project_id"})
                log_df["project_type"] = "batch"

                log_df = log_df[
                    [
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
                ]
                log_list = log_df.to_dict(orient="records")
                for log in log_list:
                    logging_ai_operation(log_instance=logger, log_obj=log, log_type="batch", message="AI-Operation-Log")
            logger.info("AI-Operation log stamping completed successfully")
        except Exception as log_err:
            logger.error(f"Failed to stamp AI-Operation logs: {log_err}", exc_info=True)
