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
from src.modules.microsoft.msgraph import MSGraphModule
from src.modules.microsoft.sharepoint import SharePointModule
from src.utils.common import (
    get_value_by_path,
    resolve_date,
    resolve_env,
    safe_list_get,
)
from src.utils.date_utils import add_date, format_date_string, get_current_datetime, is_format_datetime, list_date
from src.utils.file_utils import (
    load_yaml,
)
from src.utils.logger import Logger
from src.utils.pandas_utils import (
    df_to_excel_bytes,
    ensure_df_schema,
    replace_nan_with_default,
)

logger = Logger(__name__)


@task_registry.register("QAUploadVoiceTask")
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
    DEFAULT_CONTROL_SCHEMA = {
        "run_dt": None,
        "datamonth": None,
        "datadate": None,
        "processed_status": None,
        "input_folder": None,
        "remark": None,
    }
    DEFAULT_MASTER_SHEET_NAME = "list"
    MASTER_SCHEMA = ["emp_id", "commission_skill_code", "updatedate"]
    MASTER_DTYPE = {"emp_id": str, "commission_skill_code": str, "commission_skill": str}
    MASTER_PARSE_DATES = ["updatedate"]

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
        self.sandbox_access = common_config.get("sandbox", {})
        self.msgraph_access = common_config.get("msgraph", {})

        self.project_id = resolve_env(self.gcs.get("project_id"))

        self.msgraph_sender_email = resolve_env(self.msgraph_access.get("sender_email"))
        self.msgraph_receiver_email = resolve_env(self.msgraph_access.get("receiver_email"))
        self.msgraph_cc_email = resolve_env(self.msgraph_access.get("cc_email"))

        self.task_sender_email = resolve_env(self.framework.get("sender_email"))
        self.task_receiver_email = resolve_env(self.framework.get("receiver_email"))
        self.task_cc_email = resolve_env(self.framework.get("cc_email"))

        input_folder_list_inbound = resolve_env(
            get_value_by_path(self.sharepoint, "verint.input_folder_list_inbound", "")
        )
        input_folder_list_outbound = resolve_env(
            get_value_by_path(self.sharepoint, "verint.input_folder_list_outbound", "")
        )

        self.input_folder_list_inbound = [folder.strip() for folder in input_folder_list_inbound.split(",")]
        self.input_folder_list_outbound = [folder.strip() for folder in input_folder_list_outbound.split(",")]
        self.combined_folder_list = list(
            dict.fromkeys(self.input_folder_list_inbound + self.input_folder_list_outbound)
        )

        if not timezone:
            raise ValueError(f"Timezone not specified in common configuration file {self.COMMON_CONFIG_PATH}")
        self.timezone = ZoneInfo(timezone)

    def pre_execute(self):
        """
        Pre-execution setup: Initialize modules and connections.
        """
        logger.info("Initializing modules")

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
        """
        execution_dt = self.get_package("execution_dt", None)

        self.rerun_start_date = self.get_package("start_data_dt", None)
        self.rerun_end_date = self.get_package("end_data_dt", None)

        try:
            batch_results, self.result_df = self.pre_result
        except:  # noqa: E722 -- narrowing to Exception would stop catching BaseException, a semantic change
            logger.warning("pre_result from ExportOutputResultTask is None. Setting result_df as None...")
            self.result_df = None

        try:
            if self.rerun_start_date and self.rerun_end_date:
                end_date = add_date(self.rerun_end_date, 0).strftime("%Y%m%d")
                start_date = add_date(self.rerun_start_date, 0).strftime("%Y%m%d")
            else:
                datadate = execution_dt.strftime("%Y-%m-%d")
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
            if not self.sharepoint_control.is_item_exists(control_path):
                logger.info(f"Control file not found, will create new: {control_path}")
                existing_df = pd.DataFrame([], columns=self.DEFAULT_CONTROL_SCHEMA.keys())
            else:
                control_content = self.sharepoint_control.get_item_by_path(control_path)

                try:
                    with io.BytesIO(control_content.content) as existing_file_buffer:
                        existing_df = pd.read_excel(existing_file_buffer, sheet_name=self.DEFAULT_CONTROL_SHEET_NAME)
                    logger.debug(f"Loaded control file with {len(existing_df)} records")
                except Exception as e:
                    logger.warning(f"Failed to parse control file, creating new: {e}")
                    existing_df = pd.DataFrame([], columns=self.DEFAULT_CONTROL_SCHEMA.keys())

                # Ensure schema and parse dates
                try:
                    existing_df = ensure_df_schema(existing_df, list(self.DEFAULT_CONTROL_SCHEMA.keys()))

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
                            existing_df["input_folder"] = existing_df["input_folder"].astype(str)
                            existing_df["remark"] = existing_df["remark"].astype(str)
                        except Exception as e:
                            logger.warning(f"Error parsing control file columns: {e}")
                            existing_df["run_dt"] = None
                            existing_df["datadate"] = None
                            existing_df["datamonth"] = None
                            existing_df["processed_status"] = "N"
                            existing_df["input_folder"] = ""
                            existing_df["remark"] = ""

                except Exception as e:
                    logger.warning(f"Error ensuring schema, using empty control file: {e}")
                    existing_df = pd.DataFrame([], columns=self.DEFAULT_CONTROL_SCHEMA.keys())

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
            source_df: pd.DataFrame,
            run_dt: datetime,
            datamonth: str,
            datadate: str,
            status: str,
            input_folder: str,
            remark: str = "",
        ) -> pd.DataFrame:
            """
            Stamp a new record into the control log DataFrame.
            Parameters:
                source_df (pd.DataFrame): The existing control log DataFrame.
                run_dt (datetime): The datetime of the current run.
                datamonth (str): The data month in 'YYYYMM' format.
                datadate (str): The data date in 'YYYYMMDD' format.
                status (str): The processed status ('Y' or 'N').
                input_folder (str): The input folder for this record.
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
                "input_folder": input_folder,
                "remark": remark,
            }
            return pd.concat([source_df, pd.DataFrame([record])], ignore_index=True)

        execution_date = self.get_package("execution_dt", None).strftime("%Y-%m-%d")
        unique_df = existing_df.drop_duplicates(subset=["run_dt", "datadate", "datamonth", "input_folder"], keep="last")
        control_df = unique_df[
            (unique_df["processed_status"] == "Y")
            & (unique_df["datadate"] >= start_date)
            & (unique_df["datadate"] <= end_date)
        ]

        processed_dates = control_df.groupby("datadate")["input_folder"].apply(list).to_dict()
        logger.debug(f"Already processed dates: {processed_dates}")

        processing_dates = list_date(start_date, end_date, "%Y%m%d", "%Y%m%d")
        missing_audio_date = {datadate: [] for datadate in processing_dates}
        all_summaries = []

        for datadate in processing_dates:
            for input_folder in self.combined_folder_list:
                is_input_inbound = input_folder in self.input_folder_list_inbound
                is_input_outbound = input_folder in self.input_folder_list_outbound
                filtered_voice_files = []
                service_directions = []
                if is_input_inbound:
                    service_directions.append("IN")
                if is_input_outbound:
                    service_directions.append("OUT")
                if (
                    self.rerun_start_date is None
                    and self.rerun_end_date is None
                    and datadate in processed_dates
                    and input_folder in processed_dates[datadate]
                ):
                    logger.debug(f"Skipping processed date: {datadate} for input folder: {input_folder}")
                    continue
                source_path = get_value_by_path(self.sharepoint, "verint.source_folder", "")
                source_path = source_path.replace("${QA_VERINT_PRODUCTS}", input_folder)

                source_path = resolve_date(text=resolve_env(source_path), replace_date=datadate)

                if not self.sharepoint_verint.is_item_exists(source_path):
                    logger.debug(f"Source folder not found for {datadate}")  # debug
                    existing_df = stamp_control_log(
                        source_df=existing_df,
                        run_dt=get_current_datetime(),
                        datamonth=datadate[:6],
                        datadate=datadate,
                        status="N",
                        input_folder=input_folder,
                        remark="Source folder does not exist",
                    )
                    missing_audio_date[datadate].append(input_folder)
                    for service_direction in service_directions:
                        all_summaries.append(
                            {
                                "Service": input_folder,
                                "call_direction": service_direction,
                                "Date": datadate,
                                "Total": 0,
                                "Success": 0,
                                "Failed": 0,
                            }
                        )
                    continue

                voice_files = self.sharepoint_verint.list_files(folder_path=source_path)
                if len(voice_files) == 0:
                    logger.debug(f"No voice files for {datadate}")
                    existing_df = stamp_control_log(
                        source_df=existing_df,
                        run_dt=get_current_datetime(),
                        datamonth=datadate[:6],
                        datadate=datadate,
                        status="N",
                        input_folder=input_folder,
                        remark="No voice files found",
                    )
                    missing_audio_date[datadate].append(input_folder)
                    for service_direction in service_directions:
                        all_summaries.append(
                            {
                                "Service": input_folder,
                                "call_direction": service_direction,
                                "Date": datadate,
                                "Total": 0,
                                "Success": 0,
                                "Failed": 0,
                            }
                        )
                    continue
                logger.info(f"Processing {source_path}: {len(voice_files)} voice files")

                before_filter_count = len(filtered_voice_files)

                for file in voice_files:
                    file_name = file.get("name", None)
                    file_id = file.get("id", None)
                    file_path = file.get("parentReference", {}).get("path", None)
                    file_created_datetime = file.get("createdDateTime", None)
                    file_extension = Path(file_name).suffix.lower() if file_name else None

                    # Filter by file extension
                    if file_extension and file_extension not in [".wav"]:
                        continue

                    file_name_without_ext = file_name[:-4]
                    call_direction = safe_list_get(file_name_without_ext.split("_"), 9, None)
                    if call_direction == "IN" and not is_input_inbound:
                        continue
                    if call_direction == "OUT" and not is_input_outbound:
                        continue

                    filtered_voice_files.append(
                        {
                            "file_name": file_name,
                            "file_extension": file_extension,
                            "file_id": file_id,
                            "file_path": file_path,
                            "file_created_datetime": file_created_datetime,
                            "call_direction": call_direction,
                        }
                    )

                if len(filtered_voice_files) > before_filter_count:
                    logger.debug(f"No valid voice files after filtering for {input_folder}/{datadate}")
                    existing_df = stamp_control_log(
                        source_df=existing_df,
                        run_dt=get_current_datetime(),
                        datamonth=datadate[:6],
                        datadate=datadate,
                        status="Y",
                        input_folder=input_folder,
                        remark="",
                    )
                else:
                    existing_df = stamp_control_log(
                        source_df=existing_df,
                        run_dt=get_current_datetime(),
                        datamonth=datadate[:6],
                        datadate=datadate,
                        status="N",
                        input_folder=input_folder,
                        remark="No valid file in folder (not .wav, outbound file)",
                    )

                if len(filtered_voice_files) == 0:
                    logger.debug(f"No valid voice files for {datadate}")
                    for service_direction in service_directions:
                        all_summaries.append(
                            {
                                "Service": input_folder,
                                "call_direction": service_direction,
                                "Date": datadate,
                                "Total": 0,
                                "Success": 0,
                                "Failed": 0,
                            }
                        )
                    continue

                for direction in service_directions:
                    stream_list = []
                    direction_filtered_voice_files = [
                        file for file in filtered_voice_files if file.get("call_direction", None) == direction
                    ]
                    for item in direction_filtered_voice_files:
                        try:
                            record_date = safe_list_get(item["file_name"].split("_"), -3, None)
                            if record_date is None or is_format_datetime(record_date, "%Y%m%d") is False:
                                record_date = datadate
                        except Exception as e:
                            logger.warning(f"Failed to extract record date from file name, using datadate: {e}")
                            record_date = datadate

                        input_voice_path = resolve_date(text=self.gcs.get("input_folder"), replace_date=record_date)
                        product_name = safe_list_get(
                            item["file_path"].replace("/drive/root:", "").split("/"), 3, "unknown_product"
                        )  # ['', 'test_sentiment_batch_callcenterqa', 'Input', 'inbound_voice_2', '202603', '20260304']

                        stream_list.append(
                            {
                                "download": item["file_path"].replace("/drive/root:", "") + "/" + item["file_name"],
                                "upload": input_voice_path + "/" + product_name + "/" + item["file_name"],
                                "mime_type": "audio/wav",
                            }
                        )

                    upload_summary = asyncio.run(
                        self.gcs_module.upload_sharepoint_to_gcs(
                            sharepoint_object=self.sharepoint_verint,
                            stream_list=stream_list,
                            max_concurrent_uploads=int(resolve_env(self.framework["concurrency_upload"])),
                        )
                    )

                    all_summaries.append(
                        {
                            "Service": input_folder,
                            "call_direction": direction,
                            "Date": datadate,
                            "Total": upload_summary["total"],
                            "Success": upload_summary["success"],
                            "Failed": upload_summary["failed"],
                        }
                    )

        existing_df = existing_df[list(self.DEFAULT_CONTROL_SCHEMA.keys())].sort_values(
            by=["run_dt", "datadate"], ascending=[False, False]
        )
        existing_df = replace_nan_with_default(existing_df)
        control_bytes = df_to_excel_bytes(existing_df, sheet_name=self.DEFAULT_CONTROL_SHEET_NAME, freeze_panes="A2")
        control_path = resolve_env(get_value_by_path(self.sharepoint, "control.control_file"))
        self.sharepoint_control.upload_file(upload_path=control_path, content=control_bytes)
        logger.info(f"Control log updated with {len(existing_df)} records")

        current_dt = get_current_datetime().replace(microsecond=0)
        formatted_current_dt = current_dt.isoformat(sep=" ")

        if self.result_df is not None and not self.result_df.empty:
            ai_output_summary_df = (
                self.result_df.groupby(["department", "call_direction", "call_date", "status"])
                .size()
                .unstack(fill_value=0)
            )
            ai_output_summary_df = ai_output_summary_df.reindex(columns=["SUCCESS", "FAILED"], fill_value=0)
            ai_output_summary_df["Total"] = ai_output_summary_df["SUCCESS"] + ai_output_summary_df["FAILED"]

            ai_output_summary = (
                ai_output_summary_df.reset_index()
                .rename(
                    columns={"department": "Service", "call_date": "Date", "SUCCESS": "Success", "FAILED": "Failed"}
                )
                .to_dict(orient="records")
            )
        else:
            ai_output_summary = []
            date_t2 = add_date(end_date, -1).strftime("%Y%m%d")
            for service in self.combined_folder_list:
                ai_output_summary.append(
                    {
                        "Service": service,
                        "call_direction": "IN",
                        "Date": date_t2,
                        "Total": None,
                        "Success": None,
                        "Failed": None,
                    }
                )
                ai_output_summary.append(
                    {
                        "Service": service,
                        "call_direction": "OUT",
                        "Date": date_t2,
                        "Total": None,
                        "Success": None,
                        "Failed": None,
                    }
                )

        summary_table = self._build_summary_table(all_summaries)
        ai_output_table = self._build_summary_table(ai_output_summary)

        table_style = (
            """
        <style>
            .summary-table { border-collapse: collapse; font-family: Arial; font-size: 11px; border: 1px solid #444; }
            .summary-table th { background-color: #0078d4; color: white; border: 1px solid #ffffff; padding: 4px 8px;"""
            """ text-align: center; }
            .summary-table td { border: 1px solid #ccc; padding: 4px 6px; text-align: center; }
            .summary-table .gray-row td { background-color: #f3f3f3; }
            .service-col { text-align: left !important; font-weight: bold; min-width: 120px; }
            .total-row { font-weight: bold; background-color: #e8e8e8; }
        </style>
        """
        )

        subject = f"[AI Report] [AI-QA] on {execution_date}"
        body = (
            f"""
        Dear All,<br>
        {table_style}
        <br>
        Sentiment Analysis QA - Call center daily status report <br>
        Report Date: {execution_date} <br>
        <br>
        -- AI Input: Voice Data Processing --<br>
        {summary_table}
        <br>
        -- AI Output: Results Processing --<br>
        {ai_output_table}
        <span style="font-size: 11px; color: #666;">*Any data that failed in AI Output will be automatically re-run"""
            f""" in the next batch</span><br>
        <br>
        -- Timestamp: {formatted_current_dt} <br>
        <br>
        Best Regards,<br>
        [This is automatic message generated by AI - Do Not REPLY]
        """
        )

        self.msgraph_module.send_email(
            subject=subject,
            body=body,
            sender_email=self.task_sender_email,
            receiver_email=self.task_receiver_email,
            cc_email=self.task_cc_email,
        )

    def _build_summary_table(self, all_summaries):
        df = pd.DataFrame(all_summaries)

        # 1. Pivot using BOTH 'Service' and 'Call direction' as row indices
        pivot_df = df.pivot(index=["Service", "call_direction"], columns="Date", values=["Total", "Success", "Failed"])

        # 2. Reindex to ensure all input folders are present.
        if hasattr(self, "combined_folder_list") and self.combined_folder_list:
            pivot_df = pivot_df.reindex(self.combined_folder_list, level="Service")

        # -----------------------
        directions = ["IN", "OUT"]
        services = (
            self.combined_folder_list
            if hasattr(self, "combined_folder_list") and self.combined_folder_list
            else df["Service"].unique()
        )
        full_multi_index = pd.MultiIndex.from_product([services, directions], names=["Service", "call_direction"])
        pivot_df = pivot_df.reindex(full_multi_index)
        # -----------------------
        # 3. Reorder levels so Date is the primary header
        pivot_df = pivot_df.reorder_levels([1, 0], axis=1)

        # 4. Formatter for Body Rows: Handle NaN as '-' and Floats as Ints
        def format_val(val):
            if pd.isna(val):
                return "-"
            try:
                return str(int(float(val)))
            except Exception:
                return str(val)

        # 5. Apply formatting to the display dataframe (turns values into strings)
        display_df = pivot_df.map(format_val)

        # 6. Start building HTML string
        html = '<table class="summary-table"><thead>'

        # Header Row 1: Dates (Formatted)
        dates = sorted(df["Date"].unique())
        html += '<tr><th rowspan="2">No</th><th rowspan="2">Service</th><th rowspan="2">Call Direction</th>'
        for d in dates:
            formatted_date = format_date_string(d)  # Uses your YYYYMMDD -> DD Mon YYYY func
            html += f'<th colspan="3">{formatted_date}</th>'
        html += "</tr>"

        # Header Row 2: Sub-headers
        html += "<tr>"
        for _d in dates:
            html += "<th>Total</th><th>Success</th><th>Failed</th>"
        html += "</tr></thead><tbody>"

        # 7. Body Rows with Row-spanning for Service names
        service_counts = display_df.index.get_level_values("Service").value_counts(sort=False)
        seen_services = set()

        is_gray = True
        previous_service = None

        service_numbers = {service: idx for idx, service in enumerate(services, 1)}

        for service, direction in display_df.index:
            if previous_service is not None and service != previous_service:
                is_gray = not is_gray
            previous_service = service

            row_class = ' class="gray-row"' if is_gray else ""
            html += f"<tr{row_class}>"

            # Row number and Service columns with rowspan logic
            if service not in seen_services:
                span = service_counts[service]
                service_no = service_numbers.get(service, len(seen_services) + 1)
                html += f'<td rowspan="{span}">{service_no}</td>'
                html += f'<td class="service-col" rowspan="{span}">{service}</td>'
                seen_services.add(service)

            # New Call Direction column
            html += f'<td class="direction-col">{direction}</td>'

            # Metric columns for each date
            for d in dates:
                try:
                    raw_t = pivot_df.loc[(service, direction), (d, "Total")]
                    raw_s = pivot_df.loc[(service, direction), (d, "Success")]
                    raw_f = pivot_df.loc[(service, direction), (d, "Failed")]
                except KeyError:
                    raw_t, raw_s, raw_f = pd.NA, pd.NA, pd.NA

                if (
                    not pd.isna(raw_t)
                    and not pd.isna(raw_s)
                    and not pd.isna(raw_f)
                    and int(float(raw_t)) == 0
                    and int(float(raw_s)) == 0
                    and int(float(raw_f)) == 0
                ):
                    text_style = ' style="color: red;"'
                else:
                    text_style = ""

                # Retrieve pre-formatted string values
                try:
                    t = display_df.loc[(service, direction), (d, "Total")]
                    s = display_df.loc[(service, direction), (d, "Success")]
                    f = display_df.loc[(service, direction), (d, "Failed")]
                except KeyError:
                    t, s, f = "-", "-", "-"

                html += f"<td{text_style}>{t}</td><td{text_style}>{s}</td><td{text_style}>{f}</td>"

            html += "</tr>"

        # Footer Row (Totals)
        html += '<tr class="total-row"><td></td><td>Total</td><td></td>'
        for d in dates:
            col_data = df[df["Date"] == d]

            if col_data.empty or col_data["Total"].isna().all():
                t_sum_disp, s_sum_disp, f_sum_disp = "-", "-", "-"
                f_val_for_color = 0
            else:
                t_sum = int(col_data["Total"].fillna(0).sum())
                s_sum = int(col_data["Success"].fillna(0).sum())
                f_sum = int(col_data["Failed"].fillna(0).sum())

                t_sum_disp, s_sum_disp, f_sum_disp = t_sum, s_sum, f_sum
                f_val_for_color = f_sum

            color_style = ' style="color: red;"' if f_val_for_color > 0 else ""

            html += f"<td>{t_sum_disp}</td>"
            html += f"<td>{s_sum_disp}</td>"
            html += f"<td {color_style}>{f_sum_disp}</td>"

        html += "</tr></tbody></table>"
        return html

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
