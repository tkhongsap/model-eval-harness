# Library imports
import io
import time
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from src.core.task_interface import TaskInterface

# Source code imports
from src.core.task_registry import task_registry
from src.modules.google.gcs import GCSModule
from src.modules.google.gemini_batch import GeminiBatchModule
from src.modules.microsoft.sharepoint import SharePointModule
from src.utils.common import (
    get_value_by_path,
    resolve_date,
    resolve_env,
)
from src.utils.file_utils import load_yaml
from src.utils.logger import Logger
from src.utils.pandas_utils import (
    ensure_df_schema,
    replace_nan_with_default,
)
from tasks.sentiment_telesale.schemas.metadata import Metadata

logger = Logger(__name__)


@task_registry.register("TelesaleExecuteBatchJobTask")
class ExecuteBatchJobTask(TaskInterface):
    """
    Task to create and monitor a Gemini batch job for sentiment analysis.
    Utilizes GCS for payload storage and Gemini Batch for processing.
    """

    COMMON_CONFIG_PATH = "config/common.yml"
    DEFAULT_BATCH_JOB_NAME = "sentiment-voice-batch-job-%{DATA_DATE_YYYYMMDDHHMMSS}"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # Load parameters from configuration
        self.gcs = self.get_config("gcs", {})
        self.vertexai = self.get_config("vertexai", {})
        self.sharepoint = self.get_config("sharepoint", {})

        common_config = load_yaml(self.COMMON_CONFIG_PATH)
        self.control_access = common_config.get("control", {})
        timezone = get_value_by_path(common_config, "framework.timezone")
        self.tz = ZoneInfo(timezone) if timezone else ZoneInfo("Asia/Bangkok")

    def pre_execute(self):
        """
        Pre-execution setup for the Gemini batch job task.
        Initializes GCS and Gemini Batch modules.
        """
        logger.debug("Initializing modules")

        # Initialize GCS module
        try:
            project_id = resolve_env(self.gcs.get("project_id"))
            bucket_name = resolve_env(self.gcs.get("bucket_name"))
            self.gcs_module = GCSModule(
                project_id=project_id,
                bucket_name=bucket_name,
            )
            logger.debug(f"GCS: {project_id}/{bucket_name}")
        except Exception as e:
            logger.error(f"Failed to initialize GCS module: {e}", exc_info=True)
            raise

        # Initialize Gemini Batch module
        try:
            project_id = resolve_env(self.vertexai.get("project_id"))
            location = resolve_env(self.vertexai.get("location"))
            self.gemini_batch_module = GeminiBatchModule(
                genai_project_id=project_id,
                genai_location=location,
            )
            logger.debug(f"Gemini Batch: {project_id}, {location}")
        except Exception as e:
            logger.error(f"Failed to initialize Gemini Batch module: {e}", exc_info=True)
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

    def execute_task(self) -> Any:
        """
        Main execution logic for creating and monitoring Gemini batch job.
        """
        execution_dt = self.get_package("execution_dt", None)
        source_path, output_path, self.processing_log_list = self.pre_result

        if not source_path:
            logger.warning("No payload source, skipping batch job")
            return

        # Verify payload file exists
        try:
            if not self.gcs_module.is_file_exists(source_path):
                raise FileNotFoundError(f"Payload file not found: {source_path}")
            logger.debug(f"Payload verified: {source_path}")
        except FileNotFoundError:
            raise
        except Exception as e:
            logger.error(f"Error verifying payload file: {e}", exc_info=True)
            raise Exception(f"Cannot verify payload file: {e}") from e

        # Construct GCS URIs and create batch job
        try:
            bucket_name = self.gcs_module.bucket_name
            full_source_path = f"gs://{bucket_name}/{source_path}"
            full_output_path = f"gs://{bucket_name}/{output_path}"

            model_name = resolve_env(self.vertexai.get("model"))
            display_name = resolve_date(
                text=self.vertexai.get("batch_job_name", self.DEFAULT_BATCH_JOB_NAME), replace_date=execution_dt
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

        # Check job status
        try:
            time.sleep(2)
            job_status = self.gemini_batch_module.status_check_batch_job(job_name=batch_job.name)

            if job_status in ["JOB_STATE_FAILED", "JOB_STATE_CANCELLED", "JOB_STATE_EXPIRED"]:
                raise Exception(f"Batch job failed with status: {job_status}")

            logger.info(f"Batch job submitted successfully, status: {job_status}")
        except Exception as e:
            logger.error(f"Batch job status check failed: {e}", exc_info=True)
            raise Exception(f"Batch job status check failed: {e}") from e

        # Stamp job name and display name to processing logs
        try:
            for log in self.processing_log_list:
                log.batch_job_id = str(batch_job.name.split("/")[-1])
                log.batch_job_display_name = batch_job.display_name
                log.model_name = model_name
        except Exception as e:
            logger.error(f"Failed to stamp batch job info to logs: {e}", exc_info=True)
            for log in self.processing_log_list:
                log.batch_job_id = None
                log.batch_job_display_name = display_name
                log.model_name = model_name
            logger.warning("Proceeding without batch job info in logs")

    def _stamp_batch_processing(self):
        """
        Upload or update batch processing execution logs to SharePoint.
        """
        if not self.processing_log_list or len(self.processing_log_list) == 0:
            logger.warning("No processing logs to upload")
            return

        try:
            batch_processing_df = pd.DataFrame([log.to_dict() for log in self.processing_log_list])
            batch_processing_df = ensure_df_schema(batch_processing_df, Metadata.BATCH_PROCESSING_LOG_SCHEMA)
            logger.debug(
                f"Execution log DataFrame created with {len(batch_processing_df)} rows and {len(batch_processing_df.columns)} columns"
            )
        except Exception as log_err:
            logger.error(f"Failed to create execution log DataFrame: {log_err}", exc_info=True)
            raise Exception(f"Critical error: Cannot create execution log: {log_err}") from log_err

        log_path = resolve_env(get_value_by_path(self.sharepoint, "control.batch_processing_log_file"))
        if self.sharepoint_control.is_item_exists(item_path=log_path):
            logger.debug("Existing execution log found, merging with new data")
            try:
                existing_log = self.sharepoint_control.get_item_by_path(log_path)
                with io.BytesIO(existing_log.content) as existing_buffer:
                    existing_df = pd.read_csv(existing_buffer)

                logger.debug(f"Existing log has {len(existing_df)} rows with {len(existing_df.columns)} columns")

                combined_df = pd.concat([existing_df, batch_processing_df], ignore_index=True)
                logger.debug(f"Combined DataFrame has {len(combined_df)} rows before deduplication")
            except Exception as merge_err:
                logger.error(f"Failed to merge existing execution log: {merge_err}", exc_info=True)
                raise Exception(f"Cannot merge existing execution log: {merge_err}") from merge_err
        else:
            logger.debug("No existing execution log found, creating new log")
            combined_df = batch_processing_df

        # Prepare and upload CSV
        try:
            # for retention policy, keep only records newer than 3 months
            execution_dt = self.get_package("execution_dt", None)
            exec_ts = pd.Timestamp.now(tz=self.tz) if execution_dt is None else pd.Timestamp(execution_dt)
            if exec_ts.tzinfo is not None:
                exec_ts = exec_ts.tz_convert(None)
            three_months_ago = exec_ts - pd.DateOffset(months=3)
            combined_df["data_date"] = combined_df["data_date"].astype(str)
            combined_df["updated_dt"] = pd.to_datetime(combined_df["updated_dt"], errors="coerce")
            # Filter: Keep records newer than 3 months (Retention)
            combined_df = combined_df[combined_df.updated_dt >= three_months_ago]
            combined_df = combined_df.fillna("").sort_values(
                by=["updated_dt", "data_date", "filename"], ascending=[False, False, False]
            )
            combined_df = ensure_df_schema(combined_df, list(batch_processing_df.columns))
            combined_df = replace_nan_with_default(combined_df, default_value="")

            csv_buffer = io.BytesIO()
            combined_df.to_csv(csv_buffer, index=False, encoding="utf-8-sig")
            csv_buffer.seek(0)

            self.sharepoint_control.upload_file(
                upload_path=log_path,
                content=csv_buffer.read(),
            )
            logger.info(f"Successfully uploaded execution log to {log_path}")
        except Exception as upload_err:
            logger.error(f"Failed to upload execution log to SharePoint: {upload_err}", exc_info=True)
            raise Exception(f"Cannot upload execution log to SharePoint: {upload_err}") from upload_err

    def post_execute(self, result=None):
        """
        Post-execution log upload.
        Parameters:
            result (Any): The result from the execute_task method.
        Returns:
            Any: The same result passed in
        """
        logger.info("Post-Execution: Uploading execution logs")

        try:
            logger.debug("Starting execution log upload...")
            self._stamp_batch_processing()
            logger.info("Execution log upload completed successfully")
        except Exception as e:
            logger.error(f"Execution log upload failed: {e}", exc_info=True)
            raise Exception(f"Post-execution failed during log upload: {e}") from e
        return result
