# Library imports
import io
import json
import os
from datetime import datetime
from pathlib import Path

import pandas as pd

from src.core.task_interface import TaskInterface

# Source code imports
from src.core.task_registry import task_registry
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
from src.utils.common import force_none_value_dict, get_value_by_path, resolve_date, resolve_env, safe_cast_value
from src.utils.file_utils import (
    load_yaml,
)
from src.utils.logger import Logger
from src.utils.pandas_utils import ensure_df_schema, replace_nan_with_default
from src.utils.token_utils import (
    gemini_cost,
)
from tasks.sentiment_telesale.schemas.metadata import Metadata

logger = Logger(__name__)


@task_registry.register("TelesaleEvaluationOutputTask")
class EvaluationOutputTask(TaskInterface):
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
            output_file = resolve_date(
                resolve_env(self.sharepoint.get("control").get("evaluation_file")), execution_dt.strftime("%Y%m%d")
            )
            suffix = f"_{execution_dt.strftime('%Y-%m-%d_%H-%M-%S')}"
            file_ext = os.path.splitext(output_file)[1]
            output_file = output_file.replace(file_ext, f"{suffix}{file_ext}")
            logger.debug(f"Output file: {output_file}")
        except Exception as e:
            logger.error(f"Failed to resolve output file path: {e}", exc_info=True)
            raise Exception(f"Cannot determine output file path: {e}") from e

        json_str = json.dumps(batch_results, indent=4, ensure_ascii=False)
        json_b = io.BytesIO(json_str.encode("utf-8"))
        self.sharepoint_control.upload_file(
            upload_path=output_file.replace(".xlsx", ".json"),
            content=json_b.read(),
        )

        result = self._format_output(batch_results, execution_dt)
        result_df = pd.DataFrame(result)

        # Excel file path already has .xlsx extension from config
        try:
            # Export to Excel format
            excel_buffer = io.BytesIO()
            with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
                result_df.to_excel(writer, sheet_name="Evaluation", index=False)

            excel_buffer.seek(0)

            self.sharepoint_control.upload_file(
                upload_path=output_file,
                content=excel_buffer.read(),
            )
            logger.info(f"Uploaded Excel result file to SharePoint: {output_file}")
        except Exception as upload_err:
            logger.error(f"Failed to upload Excel file: {upload_err}", exc_info=True)
            raise Exception(f"Failed to upload Excel file: {upload_err}") from upload_err

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
                    gcs_path = "/".join(gcs_full_path.split("/")[-2:])
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
            tmp_output_file = resolve_env(self.sharepoint.get("control").get("evaluation_file"))
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
            logger.debug("Starting file archival process")
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

    def _format_output(self, raw_result: list[dict], execution_dt) -> list[dict]:
        """Format output as flattened structure for Excel export, following export_output_result_task logic."""
        logger.debug(f"Starting output formatting for {len(raw_result)} records")

        # Validate input
        if not raw_result:
            logger.warning("No raw results provided for formatting")
            return []

        result = []
        updated_dt = execution_dt.strftime("%Y-%m-%d %H:%M:%S")
        logger.debug(f"Using updated_dt: {updated_dt}")

        try:
            # Extract unique models
            unique_models = []
            for rec in raw_result:
                model = get_value_by_path(rec, "prediction.model_version", None)
                if model and model not in unique_models:
                    unique_models.append(model)

            logger.debug(f"Loading cost configuration for {len(unique_models)} models: {unique_models}")

            # Load pricing using gemini_cost utility
            model_pricing = gemini_cost(api_type=self.DEFAULT_COST_TYPE, model_list=unique_models)

            # Prepare usage data for cost calculation
            usage_detail = {}
            for idx, rec in enumerate(raw_result):
                record_key = f"record_{idx}"
                usage_detail[record_key] = {
                    "model": get_value_by_path(rec, "prediction.model_version", ""),
                    "token_input": get_value_by_path(rec, "prediction.token_input", {}),
                    "token_cached": get_value_by_path(rec, "prediction.token_cached", 0),
                    "token_output": get_value_by_path(rec, "prediction.token_output", {}),
                }

            # Calculate costs
            logger.debug(f"Calculating costs for {len(usage_detail)} records")
            cost_results = GeminiBatchModule.cal_gemini_cost(usage_detail, model_pricing)
            logger.debug(f"Cost calculation completed for {len(cost_results)} records")

        except Exception as cost_err:
            logger.error(f"Failed to calculate costs for Excel output: {cost_err}", exc_info=True)
            logger.warning("Continuing with cost_usd set to None")
            cost_results = {}

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
                    r_operations_and_professionalism = get_value_by_path(
                        rec, "prediction.raw_prediction.operations_and_professionalism", {}
                    )
                    operations_and_professionalism = get_value_by_path(
                        rec, "scoring_result.scoring_detail.operations_and_professionalism", {}
                    )
                    r_sales_effectiveness = get_value_by_path(rec, "prediction.raw_prediction.sales_effectiveness", {})
                    sales_effectiveness = get_value_by_path(
                        rec, "scoring_result.scoring_detail.sales_effectiveness", {}
                    )
                    r_customer_experience = get_value_by_path(rec, "prediction.raw_prediction.customer_experience", {})
                    customer_experience = get_value_by_path(
                        rec, "scoring_result.scoring_detail.customer_experience", {}
                    )
                    r_compliance = get_value_by_path(rec, "prediction.raw_prediction.compliance", {})
                    compliance = get_value_by_path(rec, "scoring_result.scoring_detail.compliance", {})
                    check_list = get_value_by_path(rec, "prediction.raw_prediction.check_list", {})
                    logger.debug(f"Record [{idx}]: Extracted scoring details successfully")
                except Exception as extract_err:
                    logger.error(
                        f"Record [{idx}] ({filename}): Failed to extract scoring details: {extract_err}", exc_info=True
                    )
                    raise

                # Determine status
                try:
                    prediction_status = get_value_by_path(rec, "prediction.status")
                    scoring_status = get_value_by_path(rec, "scoring_result.scoring_status")

                    if prediction_status != "SUCCESS":
                        status = prediction_status
                        message = get_value_by_path(rec, "prediction.message")
                    elif scoring_status != "SUCCESS":
                        status = scoring_status
                        message = get_value_by_path(rec, "scoring_result.scoring_message")
                    else:
                        status = "SUCCESS"
                        message = None

                    logger.debug(f"Record [{idx}]: Status={status}, Message={message}")
                except Exception as status_err:
                    logger.error(
                        f"Record [{idx}] ({filename}): Failed to determine status: {status_err}", exc_info=True
                    )
                    raise

                # Calculate operations_and_professionalism scores
                try:
                    op_sub_scores = {}
                    for key, value in operations_and_professionalism.get("detail", {}).items():
                        score_val = value.get("score", None)
                        if score_val is None:
                            logger.warning(
                                f"Record [{idx}] ({filename}): operations_and_professionalism.detail.{key}.score is None"
                            )
                        op_sub_scores[key] = safe_cast_value(score_val, int)

                    op_score_val = operations_and_professionalism.get("score", None)
                    if op_score_val is None:
                        logger.warning(f"Record [{idx}] ({filename}): operations_and_professionalism.score is None")
                    op_total_score = safe_cast_value(op_score_val, int)

                    op_max_val = operations_and_professionalism.get("max_score", None)
                    if op_max_val is None:
                        logger.warning(f"Record [{idx}] ({filename}): operations_and_professionalism.max_score is None")
                    op_total_max_score = safe_cast_value(op_max_val, int)

                    op_max_not_none_val = operations_and_professionalism.get("max_score_not_none", None)
                    if op_max_not_none_val is None:
                        logger.warning(
                            f"Record [{idx}] ({filename}): operations_and_professionalism.max_score_not_none is None"
                        )
                    op_total_max_score_not_none = safe_cast_value(op_max_not_none_val, int)

                    op_score_not_none_val = operations_and_professionalism.get("score_not_none", None)
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
                            logger.warning(
                                f"Record [{idx}] ({filename}): sales_effectiveness.detail.{key}.score is None"
                            )
                        se_sub_scores[key] = safe_cast_value(score_val, int)

                    se_score_val = sales_effectiveness.get("score", None)
                    if se_score_val is None:
                        logger.warning(f"Record [{idx}] ({filename}): sales_effectiveness.score is None")
                    se_total_score = safe_cast_value(se_score_val, int)

                    se_max_val = sales_effectiveness.get("max_score", None)
                    if se_max_val is None:
                        logger.warning(f"Record [{idx}] ({filename}): sales_effectiveness.max_score is None")
                    se_total_max_score = safe_cast_value(se_max_val, int)

                    se_max_not_none_val = sales_effectiveness.get("max_score_not_none", None)
                    if se_max_not_none_val is None:
                        logger.warning(f"Record [{idx}] ({filename}): sales_effectiveness.max_score_not_none is None")
                    se_total_max_score_not_none = safe_cast_value(se_max_not_none_val, int)

                    se_score_not_none_val = sales_effectiveness.get("score_not_none", None)
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
                            logger.warning(
                                f"Record [{idx}] ({filename}): customer_experience.detail.{key}.score is None"
                            )
                        cx_sub_scores[key] = safe_cast_value(score_val, int)

                    cx_score_val = customer_experience.get("score", None)
                    if cx_score_val is None:
                        logger.warning(f"Record [{idx}] ({filename}): customer_experience.score is None")
                    cx_total_score = safe_cast_value(cx_score_val, int)

                    cx_max_val = customer_experience.get("max_score", None)
                    if cx_max_val is None:
                        logger.warning(f"Record [{idx}] ({filename}): customer_experience.max_score is None")
                    cx_total_max_score = safe_cast_value(cx_max_val, int)

                    cx_max_not_none_val = customer_experience.get("max_score_not_none", None)
                    if cx_max_not_none_val is None:
                        logger.warning(f"Record [{idx}] ({filename}): customer_experience.max_score_not_none is None")
                    cx_total_max_score_not_none = safe_cast_value(cx_max_not_none_val, int)

                    cx_score_not_none_val = customer_experience.get("score_not_none", None)
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
                            logger.warning(f"Record [{idx}] ({filename}): compliance.detail.{key}.score is None")
                        compliance_sub_scores[key] = safe_cast_value(score_val, int)

                    compliance_score_val = compliance.get("score", None)
                    if compliance_score_val is None:
                        logger.warning(f"Record [{idx}] ({filename}): compliance.score is None")
                    compliance_total_score = safe_cast_value(compliance_score_val, int)

                    compliance_max_val = compliance.get("max_score", None)
                    if compliance_max_val is None:
                        logger.warning(f"Record [{idx}] ({filename}): compliance.max_score is None")
                    compliance_total_max_score = safe_cast_value(compliance_max_val, int)

                    compliance_max_not_none_val = compliance.get("max_score_not_none", None)
                    if compliance_max_not_none_val is None:
                        logger.warning(f"Record [{idx}] ({filename}): compliance.max_score_not_none is None")
                    compliance_total_max_score_not_none = safe_cast_value(compliance_max_not_none_val, int)

                    compliance_score_not_none_val = compliance.get("score_not_none", None)
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
                        check_list_results = check_list.get("operation_check_list", None)
                        check_list_support = check_list.get("support_detail", None)
                    else:
                        check_list_results = None
                        check_list_support = None
                    logger.debug(f"Record [{idx}]: Processed checklist for campaign={campaign_name}")
                except Exception as checklist_err:
                    logger.error(
                        f"Record [{idx}] ({filename}): Failed to process checklist: {checklist_err}", exc_info=True
                    )
                    raise

                # Calculate total scores
                try:
                    total_max_score = get_value_by_path(rec, "scoring_result.max_score", None)
                    total_max_score_no_null = get_value_by_path(rec, "scoring_result.max_score_not_none", None)
                    total_score_no_null = get_value_by_path(rec, "scoring_result.total_score", None)
                    total_weight_score = (
                        round(((total_score_no_null / total_max_score_no_null) * total_max_score), 2)
                        if total_max_score_no_null and total_max_score_no_null != 0 and total_score_no_null is not None
                        else None
                    )
                    logger.debug(f"Record [{idx}]: Calculated total scores - weight={total_weight_score}")
                except Exception as total_err:
                    logger.error(
                        f"Record [{idx}] ({filename}): Failed to calculate total scores: {total_err}", exc_info=True
                    )
                    raise

                # Build support detail strings
                try:
                    details_operations_and_professionalism = "\n".join(
                        [
                            f"call_opening_support: {get_value_by_path(rec, 'prediction.raw_prediction.operations_and_professionalism.call_opening.support_detail', '')}".strip(),
                            f"customer_identity_verification_support: {get_value_by_path(rec, 'prediction.raw_prediction.operations_and_professionalism.customer_identity_verification.support_detail', '')}".strip(),
                            f"language_and_tone_support: {get_value_by_path(rec, 'prediction.raw_prediction.operations_and_professionalism.language_and_tone.support_detail', '')}".strip(),
                            f"active_listening_support: {get_value_by_path(rec, 'prediction.raw_prediction.operations_and_professionalism.active_listening.support_detail', '')}".strip(),
                            f"call_closing_support: {get_value_by_path(rec, 'prediction.raw_prediction.operations_and_professionalism.call_closing.support_detail', '')}".strip(),
                        ]
                    )
                    details_sales_effectiveness = "\n".join(
                        [
                            f"customer_needs_analysis_support: {get_value_by_path(rec, 'prediction.raw_prediction.sales_effectiveness.customer_needs_analysis.support_detail', '')}".strip(),
                            f"offer_presentation_quality_support: {get_value_by_path(rec, 'prediction.raw_prediction.sales_effectiveness.offer_presentation_quality.support_detail', '')}".strip(),
                            f"effective_objection_handling_support: {get_value_by_path(rec, 'prediction.raw_prediction.sales_effectiveness.effective_objection_handling.support_detail', '')}".strip(),
                            f"sales_closing_attempt_support: {get_value_by_path(rec, 'prediction.raw_prediction.sales_effectiveness.sales_closing_attempt.support_detail', '')}".strip(),
                            f"cross_sell_upsell_support: {get_value_by_path(rec, 'prediction.raw_prediction.sales_effectiveness.cross_sell_upsell.support_detail', '')}".strip(),
                        ]
                    )
                    details_customer_experience = "\n".join(
                        [
                            f"positive_customer_experience_support: {get_value_by_path(rec, 'prediction.raw_prediction.customer_experience.positive_customer_experience.support_detail', '')}".strip(),
                            f"clarity_of_communication_support: {get_value_by_path(rec, 'prediction.raw_prediction.customer_experience.clarity_of_communication.support_detail', '')}".strip(),
                            f"building_trust_support: {get_value_by_path(rec, 'prediction.raw_prediction.customer_experience.building_trust.support_detail', '')}".strip(),
                        ]
                    )
                    details_compliance = f"compliance_support: {get_value_by_path(rec, 'prediction.raw_prediction.compliance.compliance.support_detail', '')}".strip()
                    logger.debug(f"Record [{idx}]: Built support detail strings")
                except Exception as detail_err:
                    logger.error(
                        f"Record [{idx}] ({filename}): Failed to build support details: {detail_err}", exc_info=True
                    )
                    raise

                # Flatten the structure for Excel columns
                try:
                    duration_val = get_value_by_path(rec, "file_metadata.duration", None)
                    if duration_val is None:
                        logger.warning(f"Record [{idx}] ({filename}): file_metadata.duration is None")

                    provider_val = get_value_by_path(rec, "file_metadata.provider", None)
                    true_dtac = "Dtac" if provider_val in ["D", "d"] else "True"

                    # Get cost from pre-calculated results
                    record_key = f"record_{idx - 1}"  # idx is 1-based, enumerate started at 1
                    cost_data = cost_results.get(record_key, {"cost_input": 0.0, "cost_output": 0.0})
                    total_cost_usd = cost_data["cost_input"] + cost_data["cost_output"]
                    logger.debug(
                        f"Record [{idx}] ({filename}): Cost - Input: ${cost_data['cost_input']:.6f}, Output: ${cost_data['cost_output']:.6f}, Total: ${total_cost_usd:.6f}"
                    )
                    call_status = get_value_by_path(rec, "prediction.raw_prediction.call_status", None)

                    op_dict = {
                        "OP_Call_Opening": op_sub_scores.get("call_opening"),
                        "OP_Call_Opening_Proper_Identification": get_value_by_path(
                            r_operations_and_professionalism, "call_opening.proper_identification", None
                        ),
                        "OP_Call_Opening_Call_Origin_Disclosure": get_value_by_path(
                            r_operations_and_professionalism, "call_opening.call_origin_disclosure", None
                        ),
                        "OP_Call_Opening_Consent_Before_Engagement": get_value_by_path(
                            r_operations_and_professionalism, "call_opening.call_consent_before_engagement", None
                        ),
                        "OP_Customer_Identity_Verification": op_sub_scores.get("customer_identity_verification"),
                        "OP_Customer_Identity_Verification_Customer_Verification": get_value_by_path(
                            r_operations_and_professionalism,
                            "customer_identity_verification.customer_verification",
                            None,
                        ),
                        "OP_Customer_Identity_Verification_Invalid_Verification": get_value_by_path(
                            r_operations_and_professionalism,
                            "customer_identity_verification.invalid_verification",
                            None,
                        ),
                        "OP_Customer_Identity_Verification_Missing_Verification": get_value_by_path(
                            r_operations_and_professionalism,
                            "customer_identity_verification.missing_verification",
                            None,
                        ),
                        "OP_Language_and_Tone": op_sub_scores.get("language_and_tone"),
                        "OP_Language_and_Tone_Behavioral_Violation": get_value_by_path(
                            r_operations_and_professionalism, "language_and_tone.behavioral_violation", None
                        ),
                        "OP_Language_and_Tone_Clarity": get_value_by_path(
                            r_operations_and_professionalism, "language_and_tone.clarity", None
                        ),
                        "OP_Language_and_Tone_Delivery_Pace": get_value_by_path(
                            r_operations_and_professionalism, "language_and_tone.delivery_pace", None
                        ),
                        "OP_Active_Listening": op_sub_scores.get("active_listening"),
                        "OP_Active_Listening_No_Interruption": get_value_by_path(
                            r_operations_and_professionalism, "active_listening.no_interruption", None
                        ),
                        "OP_Active_Listening_Correct_Understanding": get_value_by_path(
                            r_operations_and_professionalism, "active_listening.correct_understanding", None
                        ),
                        "OP_Active_Listening_Acknowledgement_Paraphrasing": get_value_by_path(
                            r_operations_and_professionalism, "active_listening.acknowledgement_paraphrasing", None
                        ),
                        "OP_Call_Closing": op_sub_scores.get("call_closing"),
                        "OP_Call_Closing_Confirm_Resolution": get_value_by_path(
                            r_operations_and_professionalism, "call_closing.confirm_resolution", None
                        ),
                        "OP_Call_Closing_Courteous_Ending": get_value_by_path(
                            r_operations_and_professionalism, "call_closing.courteous_ending", None
                        ),
                        "OP_Call_Closing_Smooth_Closing": get_value_by_path(
                            r_operations_and_professionalism, "call_closing.smooth_closing", None
                        ),
                        "OP_Total_Score": op_total_score,
                        "OP_Total_Weight_Score": op_total_weight,
                        "OP_Total_Max_Score": op_total_max_score,
                        "OP_Total_Max_Score_Not_None": op_total_max_score_not_none,
                        "OP_Details": details_operations_and_professionalism,
                    }
                    se_dict = {
                        "SE_Customer_Needs_Analysis": se_sub_scores.get("customer_needs_analysis"),
                        "SE_Customer_Needs_Analysis_Usage_Based_Analysis": get_value_by_path(
                            r_sales_effectiveness, "customer_needs_analysis.usage_based_analysis", None
                        ),
                        "SE_Customer_Needs_Analysis_Benefit_Highlight": get_value_by_path(
                            r_sales_effectiveness, "customer_needs_analysis.benefit_highlight", None
                        ),
                        "SE_Offer_Presentation_Quality": se_sub_scores.get("offer_presentation_quality"),
                        "SE_Offer_Presentation_Quality_Clarity_of_Explanation": get_value_by_path(
                            r_sales_effectiveness, "offer_presentation_quality.clarity_of_explanation", None
                        ),
                        "SE_Offer_Presentation_Quality_Customer_Benefit_Highlight": get_value_by_path(
                            r_sales_effectiveness, "offer_presentation_quality.customer_benefit_highlight", None
                        ),
                        "SE_Effective_Objection_Handling": se_sub_scores.get("effective_objection_handling"),
                        "SE_Effective_Objection_Handling_Failure_to_Listen": get_value_by_path(
                            r_sales_effectiveness, "effective_objection_handling.failure_to_listen", None
                        ),
                        "SE_Effective_Objection_Handling_Confrontational_Tone": get_value_by_path(
                            r_sales_effectiveness, "effective_objection_handling.confrontational_tone", None
                        ),
                        "SE_Sales_Closing_Attempt": se_sub_scores.get("sales_closing_attempt"),
                        "SE_Sales_Closing_Attempt_Value_Based_Closing": get_value_by_path(
                            r_sales_effectiveness, "sales_closing_attempt.value_based_closing", None
                        ),
                        "SE_Sales_Closing_Attempt_Unclear_Separation": get_value_by_path(
                            r_sales_effectiveness, "sales_closing_attempt.unclear_separation", None
                        ),
                        "SE_Sales_Closing_Attempt_Inadequate_Addon_Disclosure": get_value_by_path(
                            r_sales_effectiveness, "sales_closing_attempt.inadequate_addon_disclosure", None
                        ),
                        "SE_Cross_Sell_Upsell": se_sub_scores.get("cross_sell_upsell"),
                        "SE_Cross_Sell_Upsell_Missed_Crosssell_Upsell": get_value_by_path(
                            r_sales_effectiveness, "cross_sell_upsell.missed_crosssell_upsell", None
                        ),
                        "SE_Cross_Sell_Upsell_Unclear_Addon_Separation_Crosssell": get_value_by_path(
                            r_sales_effectiveness, "cross_sell_upsell.unclear_addon_separation_crosssell", None
                        ),
                        "SE_Cross_Sell_Upsell_Inadequate_Addon_Disclosure_Crosssell": get_value_by_path(
                            r_sales_effectiveness, "cross_sell_upsell.inadequate_addon_disclosure_crosssell", None
                        ),
                        "SE_Total_Score": se_total_score,
                        "SE_Total_Weight_Score": se_total_weight,
                        "SE_Total_Max_Score": se_total_max_score,
                        "SE_Total_Max_Score_Not_None": se_total_max_score_not_none,
                        "SE_Details": details_sales_effectiveness,
                    }
                    cx_dict = {
                        "CX_Positive_Customer_Experience": cx_sub_scores.get("positive_customer_experience"),
                        "CX_Positive_Customer_Experience_Failure_to_Demonstrate_Empathy": get_value_by_path(
                            r_customer_experience, "positive_customer_experience.failure_to_demonstrate_empathy", None
                        ),
                        "CX_Positive_Customer_Experience_Deflecting_Responsibility": get_value_by_path(
                            r_customer_experience, "positive_customer_experience.deflecting_responsibility", None
                        ),
                        "CX_Positive_Customer_Experience_Escalates_Customer_Emotion": get_value_by_path(
                            r_customer_experience, "positive_customer_experience.escalates_customer_emotion", None
                        ),
                        "CX_Clarity_of_Communication": cx_sub_scores.get("clarity_of_communication"),
                        "CX_Clarity_of_Communication_Overly_Technical_Language": get_value_by_path(
                            r_customer_experience, "clarity_of_communication.overly_technical_language", None
                        ),
                        "CX_Clarity_of_Communication_Fails_to_Clarify_Limitations": get_value_by_path(
                            r_customer_experience, "clarity_of_communication.fails_to_clarify_limitations", None
                        ),
                        "CX_Clarity_of_Communication_No_Adjustment_for_Complexity": get_value_by_path(
                            r_customer_experience, "clarity_of_communication.no_adjustment_for_complexity", None
                        ),
                        "CX_Building_Trust": cx_sub_scores.get("building_trust"),
                        "CX_Building_Trust_Provides_Unclear_Information": get_value_by_path(
                            r_customer_experience, "building_trust.provides_unclear_information", None
                        ),
                        "CX_Building_Trust_Provides_Misleading_Information": get_value_by_path(
                            r_customer_experience, "building_trust.provides_misleading_information", None
                        ),
                        "CX_Building_Trust_Fails_to_Connect_Value": get_value_by_path(
                            r_customer_experience, "building_trust.fails_to_connect_value", None
                        ),
                        "CX_Total_Score": cx_total_score,
                        "CX_Total_Weight_Score": cx_total_weight,
                        "CX_Total_Max_Score": cx_total_max_score,
                        "CX_Total_Max_Score_Not_None": cx_total_max_score_not_none,
                        "CX_Details": details_customer_experience,
                    }
                    com_dict = {
                        "Compliance_Score": compliance_sub_scores.get("compliance"),
                        "Compliance_Score_Data_Privacy_Compliance": get_value_by_path(
                            r_compliance, "compliance.data_privacy_compliance", None
                        ),
                        "Compliance_Score_Sales_Integrity_Compliance": get_value_by_path(
                            r_compliance, "compliance.sales_integrity_compliance", None
                        ),
                        "Compliance_Score_Professional_Conduct_Compliance": get_value_by_path(
                            r_compliance, "compliance.professional_conduct_compliance", None
                        ),
                        "Compliance_Total_Score": compliance_total_score,
                        "Compliance_Total_Weight_Score": compliance_total_weight,
                        "Compliance_Total_Max_Score": compliance_total_max_score,
                        "Compliance_Total_Max_Score_Not_None": compliance_total_max_score_not_none,
                        "Compliance_Details": details_compliance,
                    }
                    chk_dict = {
                        "Check_List": check_list_results,
                        "Check_List_Support_Detail": check_list_support,
                    }
                    sum_dict = {
                        "Total_Max_Score": total_max_score,
                        "Total_Max_Score_Not_None": total_max_score_no_null,
                        "Total_Score_Not_None": total_score_no_null,
                        "Total_Weight_Score": total_weight_score,
                        "Agent_Strength": get_value_by_path(rec, "prediction.raw_prediction.agent_strength", None),
                        "Agent_Weakness": get_value_by_path(rec, "prediction.raw_prediction.agent_weakness", None),
                        "Upsell_Main_Offers_Count": get_value_by_path(
                            rec, "prediction.raw_prediction.sales_performance.main_package_offered", None
                        ),
                        "Upsell_Main_Accepted_Count": get_value_by_path(
                            rec, "prediction.raw_prediction.sales_performance.main_package_accepted", None
                        ),
                        "Upsell_Add_on_Offers_Count": get_value_by_path(
                            rec, "prediction.raw_prediction.sales_performance.upsell_add_on_package_offered", None
                        ),
                        "Upsell_Add_on_Accepted_Count": get_value_by_path(
                            rec, "prediction.raw_prediction.sales_performance.upsell_add_on_package_accepted", None
                        ),
                        "Cross_Sell_Offers_Count": get_value_by_path(
                            rec, "prediction.raw_prediction.sales_performance.crosssell_add_on_product_offered", None
                        ),
                        "Cross_Sell_Accepted_Count": get_value_by_path(
                            rec, "prediction.raw_prediction.sales_performance.crosssell_add_on_product_accepted", None
                        ),
                        "Main_And_Add_On_Product_List": get_value_by_path(
                            rec,
                            "prediction.raw_prediction.sales_performance.main_and_upsell_add_on_product_offered_list",
                            None,
                        ),
                        "Cross_Sell_Offers_Product_List": get_value_by_path(
                            rec,
                            "prediction.raw_prediction.sales_performance.crosssell_add_on_product_offered_list",
                            None,
                        ),
                        "Rejection_Reason": get_value_by_path(
                            rec, "prediction.raw_prediction.customer_insight.rejection_reason", None
                        ),
                        "Network_Issue": get_value_by_path(
                            rec, "prediction.raw_prediction.customer_insight.network_issue", None
                        ),
                        "Churn_Risk_Indicator": get_value_by_path(
                            rec, "prediction.raw_prediction.customer_insight.churn_risk_indicator", None
                        ),
                        "Customer_Sentiment_Emotional": get_value_by_path(
                            rec, "prediction.raw_prediction.customer_insight.customer_sentiment_emotional", None
                        ),
                    }
                    if call_status == "Abandoned":
                        op_dict = force_none_value_dict(op_dict)
                        se_dict = force_none_value_dict(se_dict)
                        cx_dict = force_none_value_dict(cx_dict)
                        com_dict = force_none_value_dict(com_dict)
                        chk_dict = force_none_value_dict(chk_dict)
                        sum_dict = force_none_value_dict(sum_dict)

                    flattened_record = {
                        "filename": filename,
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
                        "call_duration_sec": safe_cast_value(duration_val, int),
                        "agent_id": safe_cast_value(get_value_by_path(rec, "file_metadata.agent_id", None), str),
                        "full_name": f"{get_value_by_path(rec, 'file_metadata.first_name', '')} {get_value_by_path(rec, 'file_metadata.last_name', '')}".strip(),
                        "true_dtac": true_dtac,
                        "cost_usd": round(total_cost_usd, 6) if total_cost_usd > 0 else None,
                        # Operations & Professionalism - Individual Scores
                        **op_dict,
                        # Sales Effectiveness - Individual Scores
                        **se_dict,
                        # Customer Experience - Individual Scores
                        **cx_dict,
                        # Compliance - Individual Scores
                        **com_dict,
                        # Check List
                        **chk_dict,
                        # Overall Summary
                        **sum_dict,
                        # Status and Metadata
                        "Call_Status": call_status,
                        "Status": status,
                        "Error_Message": message if message else None,
                        "Updated_DT": updated_dt,
                    }

                    result.append(flattened_record)
                    success_count += 1
                    logger.debug(f"Record [{idx}] ({filename}): Successfully formatted")
                except Exception as flatten_err:
                    logger.error(f"Record [{idx}] ({filename}): Failed to flatten record: {flatten_err}", exc_info=True)
                    raise

            except Exception as record_err:
                result.append(
                    {
                        "filename": filename if "filename" in locals() else f"unknown_{idx}",
                        "call_id": None,
                        "phone_number": None,
                        "call_month": None,
                        "call_date": None,
                        "call_duration_sec": None,
                        "agent_id": None,
                        "full_name": None,
                        "true_dtac": None,
                        "cost_usd": None,
                        "OP_Call_Opening": None,
                        "OP_Call_Opening_Proper_Identification": None,
                        "OP_Call_Opening_Call_Origin_Disclosure": None,
                        "OP_Call_Opening_Consent_Before_Engagement": None,
                        "OP_Customer_Identity_Verification": None,
                        "OP_Customer_Identity_Verification_Customer_Verification": None,
                        "OP_Customer_Identity_Verification_Invalid_Verification": None,
                        "OP_Customer_Identity_Verification_Missing_Verification": None,
                        "OP_Language_and_Tone": None,
                        "OP_Language_and_Tone_Behavioral_Violation": None,
                        "OP_Language_and_Tone_Clarity": None,
                        "OP_Language_and_Tone_Delivery_Pace": None,
                        "OP_Active_Listening": None,
                        "OP_Active_Listening_No_Interruption": None,
                        "OP_Active_Listening_Correct_Understanding": None,
                        "OP_Active_Listening_Acknowledgement_Paraphrasing": None,
                        "OP_Call_Closing": None,
                        "OP_Call_Closing_Confirm_Resolution": None,
                        "OP_Call_Closing_Courteous_Ending": None,
                        "OP_Call_Closing_Smooth_Closing": None,
                        "OP_Total_Score": None,
                        "OP_Total_Weight_Score": None,
                        "OP_Total_Max_Score": None,
                        "OP_Total_Max_Score_Not_None": None,
                        "OP_Details": None,
                        "SE_Customer_Needs_Analysis": None,
                        "SE_Customer_Needs_Analysis_Usage_Based_Analysis": None,
                        "SE_Customer_Needs_Analysis_Benefit_Highlight": None,
                        "SE_Offer_Presentation_Quality": None,
                        "SE_Offer_Presentation_Quality_Clarity_of_Explanation": None,
                        "SE_Offer_Presentation_Quality_Customer_Benefit_Highlight": None,
                        "SE_Effective_Objection_Handling": None,
                        "SE_Effective_Objection_Handling_Failure_to_Listen": None,
                        "SE_Effective_Objection_Handling_Confrontational_Tone": None,
                        "SE_Sales_Closing_Attempt": None,
                        "SE_Sales_Closing_Attempt_Value_Based_Closing": None,
                        "SE_Sales_Closing_Attempt_Unclear_Separation": None,
                        "SE_Sales_Closing_Attempt_Inadequate_Addon_Disclosure": None,
                        "SE_Cross_Sell_Upsell": None,
                        "SE_Cross_Sell_Upsell_Missed_Crosssell_Upsell": None,
                        "SE_Cross_Sell_Upsell_Unclear_Addon_Separation_Crosssell": None,
                        "SE_Cross_Sell_Upsell_Inadequate_Addon_Disclosure_Crosssell": None,
                        "SE_Total_Score": None,
                        "SE_Total_Weight_Score": None,
                        "SE_Total_Max_Score": None,
                        "SE_Total_Max_Score_Not_None": None,
                        "SE_Details": None,
                        "CX_Positive_Customer_Experience": None,
                        "CX_Clarity_of_Communication": None,
                        "CX_Building_Trust": None,
                        "CX_Total_Score": None,
                        "CX_Total_Weight_Score": None,
                        "CX_Total_Max_Score": None,
                        "CX_Total_Max_Score_Not_None": None,
                        "CX_Details": None,
                        "Compliance_Score": None,
                        "Compliance_Score_Data_Privacy_Compliance": None,
                        "Compliance_Score_Sales_Integrity_Compliance": None,
                        "Compliance_Score_Professional_Conduct_Compliance": None,
                        "Compliance_Total_Score": None,
                        "Compliance_Total_Weight_Score": None,
                        "Compliance_Total_Max_Score": None,
                        "Compliance_Total_Max_Score_Not_None": None,
                        "Compliance_Details": None,
                        "Check_List": None,
                        "Check_List_Support_Detail": None,
                        "Total_Max_Score": None,
                        "Total_Max_Score_Not_None": None,
                        "Total_Score_Not_None": None,
                        "Total_Weight_Score": None,
                        "Agent_Strength": None,
                        "Agent_Weakness": None,
                        "Upsell_Main_Offers_Count": None,
                        "Upsell_Main_Accepted_Count": None,
                        "Upsell_Add_on_Offers_Count": None,
                        "Upsell_Add_on_Accepted_Count": None,
                        "Cross_Sell_Offers_Count": None,
                        "Cross_Sell_Accepted_Count": None,
                        "Main_And_Add_On_Product_List": None,
                        "Cross_Sell_Offers_Product_List": None,
                        "Rejection_Reason": None,
                        "Network_Issue": None,
                        "Churn_Risk_Indicator": None,
                        "Customer_Sentiment_Emotional": None,
                        "Status": "FAILED",
                        "Error_Message": str(record_err),
                        "Updated_DT": updated_dt,
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

                    # Get latest record for each filename by sorting and deduplicating
                    batch_processing_log_df = batch_processing_log_df.sort_values(
                        by=["filename", "updated_dt"], ascending=[True, False]
                    ).drop_duplicates(subset=["filename"], keep="first")
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
                            result.append(
                                {
                                    "filename": failed_file.filename,
                                    "call_id": None,
                                    "phone_number": None,
                                    "call_month": None,
                                    "call_date": None,
                                    "call_duration_sec": None,
                                    "agent_id": None,
                                    "full_name": None,
                                    "true_dtac": None,
                                    "cost_usd": None,
                                    "OP_Call_Opening": None,
                                    "OP_Customer_Identity_Verification": None,
                                    "OP_Language_and_Tone": None,
                                    "OP_Active_Listening": None,
                                    "OP_Call_Closing": None,
                                    "OP_Total_Score": None,
                                    "OP_Total_Weight_Score": None,
                                    "OP_Total_Max_Score": None,
                                    "OP_Total_Max_Score_Not_None": None,
                                    "OP_Details": None,
                                    "SE_Customer_Needs_Analysis": None,
                                    "SE_Offer_Presentation_Quality": None,
                                    "SE_Effective_Objection_Handling": None,
                                    "SE_Sales_Closing_Attempt": None,
                                    "SE_Cross_Sell_Upsell": None,
                                    "SE_Total_Score": None,
                                    "SE_Total_Weight_Score": None,
                                    "SE_Total_Max_Score": None,
                                    "SE_Total_Max_Score_Not_None": None,
                                    "SE_Details": None,
                                    "CX_Positive_Customer_Experience": None,
                                    "CX_Clarity_of_Communication": None,
                                    "CX_Building_Trust": None,
                                    "CX_Total_Score": None,
                                    "CX_Total_Weight_Score": None,
                                    "CX_Total_Max_Score": None,
                                    "CX_Total_Max_Score_Not_None": None,
                                    "CX_Details": None,
                                    "Compliance_Score": None,
                                    "Compliance_Total_Score": None,
                                    "Compliance_Total_Weight_Score": None,
                                    "Compliance_Total_Max_Score": None,
                                    "Compliance_Total_Max_Score_Not_None": None,
                                    "Compliance_Details": None,
                                    "Check_List": None,
                                    "Check_List_Support_Detail": None,
                                    "Total_Max_Score": None,
                                    "Total_Max_Score_Not_None": None,
                                    "Total_Score_Not_None": None,
                                    "Total_Weight_Score": None,
                                    "Agent_Strength": None,
                                    "Agent_Weakness": None,
                                    "Status": "FAILED",
                                    "Error_Message": failed_file.error_message,
                                    "Updated_DT": updated_dt,
                                }
                            )
                            appended_count += 1
                            failed_count += 1
                        except Exception as append_err:
                            result.append(
                                {
                                    "filename": None,
                                    "call_id": None,
                                    "phone_number": None,
                                    "call_month": None,
                                    "call_date": None,
                                    "call_duration_sec": None,
                                    "agent_id": None,
                                    "full_name": None,
                                    "true_dtac": None,
                                    "cost_usd": None,
                                    "OP_Call_Opening": None,
                                    "OP_Customer_Identity_Verification": None,
                                    "OP_Language_and_Tone": None,
                                    "OP_Active_Listening": None,
                                    "OP_Call_Closing": None,
                                    "OP_Total_Score": None,
                                    "OP_Total_Weight_Score": None,
                                    "OP_Total_Max_Score": None,
                                    "OP_Total_Max_Score_Not_None": None,
                                    "OP_Details": None,
                                    "SE_Customer_Needs_Analysis": None,
                                    "SE_Offer_Presentation_Quality": None,
                                    "SE_Effective_Objection_Handling": None,
                                    "SE_Sales_Closing_Attempt": None,
                                    "SE_Cross_Sell_Upsell": None,
                                    "SE_Total_Score": None,
                                    "SE_Total_Weight_Score": None,
                                    "SE_Total_Max_Score": None,
                                    "SE_Total_Max_Score_Not_None": None,
                                    "SE_Details": None,
                                    "CX_Positive_Customer_Experience": None,
                                    "CX_Clarity_of_Communication": None,
                                    "CX_Building_Trust": None,
                                    "CX_Total_Score": None,
                                    "CX_Total_Weight_Score": None,
                                    "CX_Total_Max_Score": None,
                                    "CX_Total_Max_Score_Not_None": None,
                                    "CX_Details": None,
                                    "Compliance_Score": None,
                                    "Compliance_Total_Score": None,
                                    "Compliance_Total_Weight_Score": None,
                                    "Compliance_Total_Max_Score": None,
                                    "Compliance_Total_Max_Score_Not_None": None,
                                    "Compliance_Details": None,
                                    "Check_List": None,
                                    "Check_List_Support_Detail": None,
                                    "Total_Max_Score": None,
                                    "Total_Max_Score_Not_None": None,
                                    "Total_Score_Not_None": None,
                                    "Total_Weight_Score": None,
                                    "Agent_Strength": None,
                                    "Agent_Weakness": None,
                                    "Status": "FAILED",
                                    "Error_Message": f"Unknown error during log append {append_err}",
                                    "Updated_DT": updated_dt,
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

            for success_record in success_records.itertuples():
                # Archive from processing folder
                try:
                    logger.debug(f"Archiving voice file: {success_record.file_name}")
                    logger.debug(f"Files in processing folder: {files_in_processing}")
                    if success_record.file_name in files_in_processing:
                        self.gcs_module.move_file(
                            source_path=f"{processing_voice_folder}/{success_record.file_name}",
                            destination_path=f"{archive_voice_folder}/{success_record.file_name}",
                        )
                        archived_success_count += 1
                        logger.debug(f"Archived voice file: {success_record.file_name}")
                    else:
                        logger.warning(f"Voice file not found in processing folder: {success_record.file_name}")
                        archived_skipped_count += 1
                except Exception as archive_err:
                    logger.error(
                        f"Failed to archive voice file '{success_record.file_name}': {archive_err}", exc_info=True
                    )
                    archived_skipped_count += 1

                # Delete from input folder
                try:
                    if success_record.file_name in files_in_input:
                        self.gcs_module.delete_file(file_path=f"{input_voice_folder}/{success_record.file_name}")
                        deleted_success_count += 1
                        logger.debug(f"Deleted voice file from input folder: {success_record.file_name}")
                    else:
                        logger.debug(f"Voice file not found in input folder: {success_record.file_name}")
                        deleted_skipped_count += 1
                except Exception as delete_err:
                    logger.error(
                        f"Failed to delete voice file '{success_record.file_name}' from input folder: {delete_err}",
                        exc_info=True,
                    )
                    deleted_skipped_count += 1

            logger.info(
                f"Voice file archival complete. Archived: {archived_success_count}, Skipped: {archived_skipped_count}"
            )
            logger.info(
                f"Voice file deletion from input complete. Deleted: {deleted_success_count}, Skipped: {deleted_skipped_count}"
            )

        # Archive batch files
        logger.debug("Archiving processed batch files")
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

        # Validate input DataFrame
        if df is None or df.empty:
            logger.warning("No records to log. Skipping log insertion.")
            return

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
                self._performance_log(transaction_log_df=transaction_log_df, execution_dt=execution_dt)
                logger.debug("Performance log creation completed successfully")
            except Exception as perf_err:
                logger.error(f"Non-critical error creating performance log: {perf_err}", exc_info=True)
                logger.warning("Skipping performance log creation due to error, continuing execution")
        else:
            logger.warning("Transaction log DataFrame is None or empty. Skipping performance log creation.")

    def _prep_gemini_cost(self, prediction_df: pd.DataFrame) -> list[dict]:
        """
        Prepare Gemini cost configuration using gemini_cost utility.

        Parameters:
            prediction_df (pd.DataFrame): DataFrame containing prediction results with model_version column.

        Returns:
            list[dict]: List of pricing configurations for all models in the DataFrame.
        """
        logger.debug("Preparing Gemini cost configuration")

        try:
            model_prediction = prediction_df["model_version"].unique().tolist()
            logger.info(f"Processing pricing for {len(model_prediction)} unique models: {model_prediction}")
        except Exception as e:
            logger.error(f"Failed to extract model versions: {e}", exc_info=True)
            raise Exception(f"Cannot extract model versions: {e}") from e

        return gemini_cost(api_type=self.DEFAULT_COST_TYPE, model_list=model_prediction)

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
        Returns:
            pd.DataFrame: The updated DataFrame after logging transactions.
        Raises:
            ValueError: If required columns are missing in the prediction DataFrame.
            Exception: For any other unexpected errors during transaction logging.
        """
        logger.debug(f"Processing {len(prediction_df)} prediction records for transaction logging")
        logger.debug(f"DataFrame columns: {list(prediction_df.columns)}")

        model_pricing = self._prep_gemini_cost(prediction_df)

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

        # Prepare usage data for cal_gemini_cost
        usage_detail = {}
        for idx, row in prediction_df.iterrows():
            record_key = f"record_{idx}"
            usage_detail[record_key] = {
                "model": row.get("model_version", ""),
                "token_input": row.get("token_input", {}),
                "token_cached": row.get("token_cached", 0),
                "token_output": row.get("token_output", {}),
            }

        # Calculate costs using GeminiBatchModule.cal_gemini_cost
        logger.debug(f"Calculating costs for {len(usage_detail)} records using GeminiBatchModule.cal_gemini_cost")
        try:
            cost_results = GeminiBatchModule.cal_gemini_cost(usage_detail, model_pricing)
            logger.debug(f"Cost calculation completed for {len(cost_results)} records")
        except Exception as cost_err:
            logger.error(f"Failed to calculate costs: {cost_err}", exc_info=True)
            raise Exception(f"Cost calculation failed: {cost_err}") from cost_err

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

                # Get pre-calculated cost from cal_gemini_cost results
                record_key = f"record_{idx - 1}"  # idx is 1-based, DataFrame index is 0-based
                cost_data = cost_results.get(record_key, {"cost_input": 0.0, "cost_output": 0.0})
                total_cost_usd = cost_data["cost_input"] + cost_data["cost_output"]

                logger.debug(
                    f"Record {idx} ({record['file_name']}): Cost from cal_gemini_cost - Input: ${cost_data['cost_input']:.6f}, Output: ${cost_data['cost_output']:.6f}, Total: ${total_cost_usd:.6f}"
                )

                # Calculate token usage for logging
                try:
                    token_input = record.get("token_input")
                    token_output = record.get("token_output")

                    if not isinstance(token_input, dict):
                        logger.warning(
                            f"Record {idx} ({record['file_name']}): token_input is not a dict (type: {type(token_input)}), setting to 0"
                        )
                        token_usage_input = 0
                    else:
                        # Sum all input tokens
                        token_usage_input = sum(int(v) for v in token_input.values() if str(v).isdigit())

                        # Subtract cached tokens (already deducted in cal_gemini_cost, this is for display only)
                        token_cached = record.get("token_cached", 0)
                        if token_cached and str(token_cached).isdigit():
                            token_usage_input_billable = token_usage_input - int(token_cached)
                        else:
                            token_usage_input_billable = token_usage_input

                        logger.debug(
                            f"Record {idx}: Input tokens={token_usage_input} (billable after cache: {token_usage_input_billable}, cached={token_cached})"
                        )

                    if not isinstance(token_output, dict):
                        logger.warning(
                            f"Record {idx} ({record['file_name']}): token_output is not a dict (type: {type(token_output)}), setting to 0"
                        )
                        token_usage_output = 0
                    else:
                        # Sum all output tokens
                        token_usage_output = sum(int(v) for v in token_output.values() if str(v).isdigit())
                        logger.debug(f"Record {idx}: Output tokens={token_usage_output}")

                except (ValueError, TypeError, AttributeError) as token_err:
                    logger.error(
                        f"Record {idx} ({record['file_name']}): Error calculating token usage: {token_err}",
                        exc_info=True,
                    )
                    logger.warning(f"Record {idx}: Setting token usage to 0 due to error")
                    token_usage_input = 0
                    token_usage_output = 0

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

                    # Ensure token values are integers
                    token_input_val = (
                        token_usage_input
                        if isinstance(token_usage_input, int)
                        else safe_cast_value(token_usage_input if token_usage_input is not None else 0, int, 0)
                    )
                    token_output_val = (
                        token_usage_output
                        if isinstance(token_usage_output, int)
                        else safe_cast_value(token_usage_output if token_usage_output is not None else 0, int, 0)
                    )

                    # Ensure cost is float
                    cost_val = (
                        total_cost_usd
                        if isinstance(total_cost_usd, float)
                        else safe_cast_value(total_cost_usd if total_cost_usd is not None else 0.0, float, 0.0)
                    )

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
                            token_usage_input=token_input_val,
                            token_usage_output=token_output_val,
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

        # Upload single log file keyed by pipeline execution date
        try:
            transaction_path = resolve_date(
                text=resolve_env(get_value_by_path(self.sharepoint, "control.transaction_log_file")),
                replace_date=execution_dt,
            )
            logger.debug(f"Transaction log path: {transaction_path}")

            if self.sharepoint_control.is_item_exists(item_path=transaction_path):
                logger.debug("Existing transaction log found, merging with new data")
                try:
                    existing_log = self.sharepoint_control.get_item_by_path(transaction_path)
                    with io.BytesIO(existing_log.content) as existing_buffer:
                        existing_df = pd.read_csv(existing_buffer)
                    logger.debug(f"Existing log has {len(existing_df)} rows")
                    combined_df = pd.concat([existing_df, new_transaction_df], ignore_index=True)
                    logger.debug(f"Combined DataFrame has {len(combined_df)} rows before deduplication")
                except Exception as merge_err:
                    logger.error(f"Error merging with existing transaction log: {merge_err}", exc_info=True)
                    raise Exception(f"Cannot merge transaction log: {merge_err}") from merge_err
            else:
                logger.debug("No existing transaction log found, creating new log")
                combined_df = new_transaction_df

            # Apply retention policy: keep only records newer than 3 months
            three_months_ago = pd.Timestamp(execution_dt).replace(tzinfo=None) - pd.DateOffset(months=3)
            combined_df["data_date"] = combined_df["data_date"].astype(str)
            combined_df["updated_dt"] = pd.to_datetime(combined_df["updated_dt"], errors="coerce")
            combined_df = combined_df[combined_df.updated_dt >= three_months_ago]
            combined_df = combined_df.fillna("").sort_values(
                by=["updated_dt", "load_dt", "data_date", "start_time", "end_time"],
                ascending=[False, False, False, False, False],
            )
            combined_df = ensure_df_schema(combined_df, list(new_transaction_df.columns))
            combined_df = replace_nan_with_default(combined_df, default_value="")

            csv_buffer = io.BytesIO()
            combined_df.to_csv(csv_buffer, index=False, encoding="utf-8-sig")
            csv_buffer.seek(0)
            self.sharepoint_control.upload_file(upload_path=transaction_path, content=csv_buffer.read())
            logger.info(f"Successfully uploaded transaction log to {transaction_path}")
        except Exception as upload_err:
            logger.error(f"Failed to upload transaction log: {upload_err}", exc_info=True)
            raise

        return new_transaction_df

    def _performance_log(self, transaction_log_df: pd.DataFrame, execution_dt: datetime) -> pd.DataFrame | None:
        """
        Create performance log entries based on transaction log DataFrame.
        Parameters:
            transaction_log_df (pd.DataFrame): The DataFrame containing transaction log records.
        Returns:
            pd.DataFrame | None: The updated DataFrame after logging performance metrics, or None if no valid records.
        Raises:
            ValueError: If required columns are missing in the transaction log DataFrame.
        """
        logger.info(f"Processing {len(transaction_log_df)} transaction log records for performance logging")
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
        logger.info("Performance log processing in ERROR-TOLERANT MODE - will skip failed records")
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
            return None

        # Insert performance logs
        try:
            logger.debug("Calling audit_log_module.log_performance...")
            df = pd.DataFrame(log.to_dict() for log in log_payload)
            df = ensure_df_schema(df, Metadata.PERFORMANCE_LOG_SCHEMA)
            logger.info(f"Performance log DataFrame created with {len(df)} rows and {len(df.columns)} columns")
        except Exception as log_err:
            logger.error(f"Failed to create performance log DataFrame: {log_err}", exc_info=True)
            raise Exception(f"Cannot create performance log: {log_err}") from log_err

        # Upload single log file keyed by pipeline execution date
        try:
            performance_path = resolve_date(
                text=resolve_env(get_value_by_path(self.sharepoint, "control.performance_log_file")),
                replace_date=execution_dt,
            )
            logger.debug(f"Performance log path: {performance_path}")

            if self.sharepoint_control.is_item_exists(item_path=performance_path):
                logger.info("Existing performance log found, merging with new data")
                try:
                    existing_log = self.sharepoint_control.get_item_by_path(performance_path)
                    with io.BytesIO(existing_log.content) as existing_buffer:
                        existing_df = pd.read_csv(existing_buffer)
                    logger.debug(f"Existing log has {len(existing_df)} rows")
                    combined_df = pd.concat([existing_df, df], ignore_index=True)
                    logger.debug(f"Combined DataFrame has {len(combined_df)} rows before deduplication")
                except Exception as merge_err:
                    logger.error(f"Error merging with existing performance log: {merge_err}", exc_info=True)
                    raise Exception(f"Cannot merge performance log: {merge_err}") from merge_err
            else:
                logger.info("No existing performance log found, creating new log")
                combined_df = df

            # Apply retention policy: keep only records newer than 3 months
            three_months_ago = pd.Timestamp(execution_dt).replace(tzinfo=None) - pd.DateOffset(months=3)
            combined_df["data_date"] = combined_df["data_date"].astype(str)
            combined_df["updated_dt"] = pd.to_datetime(combined_df["updated_dt"], errors="coerce")
            combined_df = combined_df[combined_df.updated_dt >= three_months_ago]
            combined_df = combined_df.fillna("").sort_values(
                by=["updated_dt", "load_dt", "data_date"], ascending=[False, False, False]
            )
            combined_df = ensure_df_schema(combined_df, list(df.columns))
            combined_df = replace_nan_with_default(combined_df, default_value="")

            csv_buffer = io.BytesIO()
            combined_df.to_csv(csv_buffer, index=False, encoding="utf-8-sig")
            csv_buffer.seek(0)
            self.sharepoint_control.upload_file(upload_path=performance_path, content=csv_buffer.read())
            logger.info(
                f"Successfully uploaded performance log to {performance_path} with {len(combined_df)} total records"
            )
        except Exception as upload_err:
            logger.error(f"Failed to upload performance log: {upload_err}", exc_info=True)
            raise

        return df
