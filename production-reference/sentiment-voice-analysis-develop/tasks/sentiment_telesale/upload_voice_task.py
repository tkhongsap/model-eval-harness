# Library imports
import asyncio
import io
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from src.core.task_interface import TaskInterface

# Source code imports
from src.core.task_registry import task_registry
from src.modules.google.gcs import GCSModule
from src.modules.microsoft.sharepoint import SharePointModule
from src.utils.common import (
    get_value_by_path,
    resolve_date,
    resolve_env,
    safe_list_get,
)
from src.utils.date_utils import add_date, get_current_datetime, is_format_datetime, list_date
from src.utils.file_utils import (
    load_yaml,
)
from src.utils.logger import Logger
from src.utils.pandas_utils import (
    df_to_excel_bytes,
    replace_nan_with_default,
)
from tasks.sentiment_telesale.schemas.agent_master_schema import AgentMasterSchema
from tasks.sentiment_telesale.schemas.control_log_schema import ControlLogSchema

logger = Logger(__name__)


@task_registry.register("TelesaleUploadVoiceTask")
class UploadVoiceTask(TaskInterface):
    """
    Task to upload voice files from SharePoint to GCS for sentiment analysis.
    Manages control logs to track processed files and dates.
    Hints:
        - Path files must consist of YYYYMM/YYYYMMDD format for lookback processing.
        - Control log is maintained in SharePoint to avoid re-processing.
    """

    COMMON_CONFIG_PATH = "config/common.yml"
    DEFAULT_CONTROL_SHEET_NAME = "ControlLog"
    DEFAULT_MASTER_SHEET_NAME = "list"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # Load parameters from configuration
        self.gcs = self.get_config("gcs", {})
        self.sharepoint = self.get_config("sharepoint", {})
        self.framework = self.get_config("framework", {})

        common_config = load_yaml(self.COMMON_CONFIG_PATH)
        timezone = get_value_by_path(common_config, "framework.timezone")
        self.verint_access = common_config.get("verint", {})
        self.control_access = common_config.get("control", {})

        if not timezone:
            raise ValueError(f"Timezone not specified in common configuration file {self.COMMON_CONFIG_PATH}")
        self.timezone = ZoneInfo(timezone)

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
            logger.debug(f"SharePoint Control: {control_site}")
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
            logger.debug(f"GCS: {project_id}/{bucket_name}")
        except Exception as e:
            logger.error(f"Failed to initialize GCS module: {e}", exc_info=True)
            raise

    def execute_task(self) -> Any:
        """
        Main execution logic for uploading voice data.
        """
        execution_dt = self.get_package("execution_dt", None)
        rerun_date = self.get_package("rerun_data_dt", None)

        datadate = rerun_date or execution_dt.strftime("%Y-%m-%d")

        try:
            end_date = add_date(datadate, -1).strftime("%Y%m%d")
            start_date = add_date(datadate, -int(resolve_env(self.framework["lookback_days"]))).strftime("%Y%m%d")
            logger.info(f"Uploading voice files for date range: {start_date} to {end_date}")
        except Exception as e:
            logger.error(f"Failed to calculate date range: {e}", exc_info=True)
            raise Exception(f"Cannot determine processing date range: {e}") from e

        # Get existing control file
        existing_df = self._get_existing_control_file()
        logger.debug(f"Loaded {len(existing_df)} control records")

        # Upload voice files from SharePoint to GCS
        try:
            self._upload_voice_files(existing_df, start_date, end_date)
        except Exception as e:
            logger.error(f"Failed to upload voice files: {e}", exc_info=True)
            raise Exception(f"Voice file upload failed: {e}") from e

    def _get_existing_control_file(self) -> pd.DataFrame:
        """
        Load and parse the existing control file from SharePoint.
        Parameters:
            None
        Returns:
            pd.DataFrame: The existing control file as a DataFrame.
        """
        # Load control file
        control_path = resolve_env(get_value_by_path(self.sharepoint, "control.control_file"))

        try:

            def _empty_control_df() -> pd.DataFrame:
                return pd.DataFrame([], columns=list(ControlLogSchema.to_schema().columns.keys()))

            if not self.sharepoint_control.is_item_exists(control_path):
                logger.debug(f"Control file not found, will create new: {control_path}")
                existing_df = _empty_control_df()
            else:
                control_content = self.sharepoint_control.get_item_by_path(control_path)

                try:
                    with io.BytesIO(control_content.content) as existing_file_buffer:
                        existing_df = pd.read_excel(existing_file_buffer, sheet_name=self.DEFAULT_CONTROL_SHEET_NAME)
                    logger.debug(f"Loaded control file with {len(existing_df)} records")
                except Exception as e:
                    logger.warning(f"Failed to parse control file, creating new: {e}")
                    existing_df = _empty_control_df()

                # Validate schema and parse dates
                try:
                    existing_df = ControlLogSchema.validate(existing_df)

                    if not existing_df.empty:
                        # Parse dates with error handling
                        try:
                            existing_df["run_dt"] = pd.to_datetime(
                                existing_df["run_dt"], format="mixed", errors="coerce"
                            ).dt.strftime("%Y-%m-%d %H:%M:%S")
                            existing_df["datadate"] = pd.to_datetime(
                                existing_df["datadate"], format="%Y%m%d", errors="coerce"
                            ).dt.strftime("%Y%m%d")
                            existing_df["datamonth"] = pd.to_datetime(
                                existing_df["datamonth"], format="%Y%m", errors="coerce"
                            ).dt.strftime("%Y%m")
                            existing_df["processed_status"] = existing_df["processed_status"].astype(str)
                            existing_df["remark"] = existing_df["remark"].astype(str)
                        except Exception as e:
                            logger.warning(f"Error parsing control file columns: {e}")
                            existing_df["run_dt"] = None
                            existing_df["datadate"] = None
                            existing_df["datamonth"] = None
                            existing_df["processed_status"] = "N"
                            existing_df["remark"] = ""

                except Exception as e:
                    logger.warning(f"Error ensuring schema, using empty control file: {e}")
                    existing_df = _empty_control_df()

            return existing_df

        except Exception as e:
            logger.error(f"Critical error loading control file: {e}", exc_info=True)
            raise Exception(f"Critical error loading control file: {e}") from e

    def _upload_voice_files(self, existing_df: pd.DataFrame, start_date: str, end_date: str) -> str:
        """
        Upload voice files from SharePoint to GCS for the specified date range.
        Parameters:
            existing_df (pd.DataFrame): Existing DataFrame containing control log records
            start_date (str): Start date in 'YYYYMMDD'
            end_date (str): End date in 'YYYYMMDD'
        Returns:
            str: Uploaded payload path in GCS
        """

        def stamp_control_log(
            source_df: pd.DataFrame, run_dt: datetime, datamonth: str, datadate: str, status: str, remark: str = ""
        ) -> pd.DataFrame:
            """
            Stamp a new record into the control log DataFrame.
            Parameters:
                source_df (pd.DataFrame): The existing control log DataFrame.
                run_dt (datetime): The datetime of the current run.
                datamonth (str): The data month in 'YYYYMM' format.
                datadate (str): The data date in 'YYYYMMDD' format.
                status (str): The processed status ('Y' or 'N').
                remark (str): Any remarks for this record.
            Returns:
                pd.DataFrame: Updated control log DataFrame with the new record appended.
            """

            if status.upper() not in ["Y", "N"]:
                logger.warning(f"Invalid status '{status}' provided. Defaulting to 'N'")
                status = "N"
            else:
                status = status.upper()

            # Handle naive datetimes and compare ZoneInfo objects directly
            if run_dt.tzinfo is None or run_dt.tzinfo != self.timezone:
                run_dt = run_dt.replace(tzinfo=self.timezone)

            record = {
                "run_dt": run_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "datamonth": pd.to_datetime(datamonth, format="%Y%m").strftime("%Y%m"),
                "datadate": pd.to_datetime(datadate, format="%Y%m%d").strftime("%Y%m%d"),
                "processed_status": status,
                "remark": remark,
            }
            return pd.concat([source_df, pd.DataFrame([record])], ignore_index=True)

        execution_dt = self.get_package("execution_dt", get_current_datetime())
        unique_df = existing_df.drop_duplicates(subset=["run_dt", "datadate", "datamonth"], keep="last")
        control_df = unique_df[
            (unique_df["processed_status"] == "Y")
            & (unique_df["datadate"] >= start_date)
            & (unique_df["datadate"] <= end_date)
        ]

        processed_dates = control_df["datadate"].tolist()
        logger.debug(f"Already processed dates: {processed_dates}")

        master_folder = resolve_env(get_value_by_path(self.sharepoint, "verint.master_folder"))
        master_pattern = resolve_env(get_value_by_path(self.sharepoint, "verint.master_pattern"))

        processing_dates = list_date(start_date, end_date, "%Y%m%d", "%Y%m%d")
        master_file_cache = {}  # Cache master file lookup per month (YYYYMM) to avoid N+1 SharePoint calls
        for datadate in processing_dates:
            if datadate in processed_dates:
                logger.debug(f"Skipping processed date: {datadate}")
                continue

            conditions = []
            try:
                logger.debug(f"Getting master agent list for {datadate}")
                cache_key = datadate[:6]  # YYYYMM — master files are monthly
                if cache_key not in master_file_cache:
                    master_file_cache[cache_key] = safe_list_get(
                        sorted(
                            self.sharepoint_verint.list_files_pattern(
                                resolve_date(text=master_folder, replace_date=datadate), master_pattern
                            ),
                            reverse=True,
                        ),
                        0,
                        None,
                    )
                master_path = master_file_cache[cache_key]
                if master_path:
                    res = self.sharepoint_verint.get_item_by_path(master_path)
                    agent_list_df = AgentMasterSchema.validate(
                        pd.read_excel(
                            io.BytesIO(res.content), sheet_name=self.DEFAULT_MASTER_SHEET_NAME, engine="openpyxl"
                        )
                    )
                    upload_cond = self.framework.get("upload_cond", [])
                    for cond in upload_cond:
                        skill_code = cond.split("/")[0]
                        cond_type = cond.split("/")[1:]
                        filtered_df = agent_list_df[(agent_list_df["commission_skill_code"] == skill_code)]
                        agent_id_list = filtered_df["emp_id"].tolist()
                        conditions.append((agent_id_list, cond_type))
                    logger.debug(f"Loaded master agent list with {len(agent_list_df)} records")
                logger.debug(f"Total commission skill code to process: {len(conditions)} for {datadate}")
            except Exception as e:
                logger.warning(f"Failed to load master agent list for {datadate}: {e}", exc_info=True)
                logger.warning("Continuing without conditions filtering")

            source_path = resolve_date(
                text=resolve_env(get_value_by_path(self.sharepoint, "verint.source_folder", "")), replace_date=datadate
            )
            if not self.sharepoint_verint.is_item_exists(source_path):
                logger.debug(f"Source folder not found for {datadate}")
                existing_df = stamp_control_log(
                    source_df=existing_df,
                    run_dt=execution_dt,
                    datamonth=datadate[:6],
                    datadate=datadate,
                    status="N",
                    remark="Source folder does not exist",
                )
                continue
            voice_files = self.sharepoint_verint.list_files(folder_path=source_path)
            if len(voice_files) == 0:
                logger.debug(f"No voice files for {datadate}")
                existing_df = stamp_control_log(
                    source_df=existing_df,
                    run_dt=execution_dt,
                    datamonth=datadate[:6],
                    datadate=datadate,
                    status="N",
                    remark="No voice files found",
                )
                continue
            logger.info(f"Processing {datadate}: {len(voice_files)} voice files")

            filtered_voice_files = []
            for file in voice_files:
                file_name = file.get("name", None)
                file_id = file.get("id", None)
                file_path = file.get("parentReference", {}).get("path", None)
                file_created_datetime = file.get("createdDateTime", None)
                file_extension = Path(file_name).suffix.lower() if file_name else None

                # Filter by file extension
                if file_extension and file_extension not in [".wav"]:
                    continue

                # Filter by conditions (if any conditions specified)
                if conditions:
                    should_skip = True  # Default: skip unless a condition matches
                    for cond in conditions:
                        try:
                            agent_id_list, cond_type = cond
                            agent_id = safe_list_get(file_name.split("_"), 3, None)

                            # Skip if agent_id couldn't be extracted
                            if agent_id is None:
                                continue  # Try next condition

                            # File matches if: agent in list AND any condition type found in filename
                            if agent_id in agent_id_list and any(t in file_name for t in cond_type):
                                should_skip = False  # File matches this condition
                                break  # No need to check other conditions
                        except Exception as e:
                            logger.warning(f"Error processing conditions for file {file_name}: {e}", exc_info=True)
                            continue  # Try next condition

                    if should_skip:
                        logger.debug(f"Skipping file {file_name} - no matching condition (agent_id or type)")
                        continue  # Skip to next file
                # If no conditions specified, include all files (no filtering)

                filtered_voice_files.append(
                    {
                        "file_name": file_name,
                        "file_extension": file_extension,
                        "file_id": file_id,
                        "file_path": file_path,
                        "file_created_datetime": file_created_datetime,
                    }
                )

            stream_list = []
            if len(filtered_voice_files) == 0:
                logger.debug(f"No valid voice files for {datadate}")
                existing_df = stamp_control_log(
                    source_df=existing_df,
                    run_dt=execution_dt,
                    datamonth=datadate[:6],
                    datadate=datadate,
                    status="N",
                    remark="No valid voice files to process",
                )
                continue

            for item in filtered_voice_files:
                try:
                    record_date = safe_list_get(item["file_name"].split("_"), -3, None)
                    if record_date is None or is_format_datetime(record_date, "%Y%m%d") is False:
                        record_date = datadate
                except Exception as e:
                    logger.warning(f"Failed to extract record date from file name, using datadate: {e}")
                    record_date = datadate

                input_voice_path = resolve_date(text=self.gcs.get("input_folder"), replace_date=record_date)

                stream_list.append(
                    {
                        "download": item["file_path"].replace("/drive/root:", "") + "/" + item["file_name"],
                        "upload": input_voice_path + "/" + item["file_name"],
                        "mime_type": "audio/wav",
                    }
                )

            asyncio.run(
                self.gcs_module.upload_sharepoint_to_gcs(
                    sharepoint_object=self.sharepoint_verint,
                    stream_list=stream_list,
                    max_concurrent_uploads=int(resolve_env(self.framework["concurrency_upload"])),
                )
            )
            existing_df = stamp_control_log(
                source_df=existing_df,
                run_dt=execution_dt,
                datamonth=datadate[:6],
                datadate=datadate,
                status="Y",
                remark="",
            )

        existing_df = existing_df[list(ControlLogSchema.to_schema().columns.keys())].sort_values(
            by=["run_dt", "datadate"], ascending=[False, False]
        )
        existing_df = replace_nan_with_default(existing_df)
        control_bytes = df_to_excel_bytes(existing_df, sheet_name=self.DEFAULT_CONTROL_SHEET_NAME, freeze_panes="A2")
        control_path = resolve_env(get_value_by_path(self.sharepoint, "control.control_file"))
        self.sharepoint_control.upload_file(upload_path=control_path, content=control_bytes)
        logger.info(f"Control log updated with {len(existing_df)} records")
