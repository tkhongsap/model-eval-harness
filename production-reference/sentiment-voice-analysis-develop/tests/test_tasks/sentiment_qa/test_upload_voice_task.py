import io
from datetime import datetime
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from tasks.sentiment_qa.upload_voice_task import UploadVoiceTask

EXECUTION_DT = datetime(2025, 1, 3, 8, 0, 0, tzinfo=ZoneInfo("UTC"))
COMMON_CONFIG = {
    "framework": {"timezone": "UTC"},
    "verint": {
        "site_domain": "verint.example.com",
        "client_id": "verint-client",
        "client_secret": "verint-secret",
        "tenant_id": "tenant-id",
        "site_path": "/sites/verint",
    },
    "control": {
        "site_domain": "control.example.com",
        "client_id": "control-client",
        "client_secret": "control-secret",
        "tenant_id": "tenant-id",
        "site_path": "/sites/control",
    },
    "sandbox": {},
    "msgraph": {
        "tenant_id": "graph-tenant",
        "client_id": "graph-client",
        "client_secret": "graph-secret",
        "sender_email": "sender@example.com",
        "receiver_email": "receiver@example.com",
        "cc_email": "cc@example.com",
    },
}


def _fake_resolve_date(text, replace_date):
    if hasattr(replace_date, "strftime"):
        date_ymd = replace_date.strftime("%Y%m%d")
    else:
        date_ymd = str(replace_date).replace("-", "")[:8]
    return text.replace("%{DATA_DATE_YYYYMMDD}", date_ymd)


def _excel_bytes(df, sheet_name="ControlLog"):
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name=sheet_name)
    buffer.seek(0)
    return buffer.read()


@pytest.fixture(autouse=True)
def patch_common():
    with (
        patch("tasks.sentiment_qa.upload_voice_task.load_yaml", return_value=COMMON_CONFIG),
        patch(
            "tasks.sentiment_qa.upload_voice_task.resolve_env",
            side_effect=lambda value: value,
        ),
        patch(
            "tasks.sentiment_qa.upload_voice_task.resolve_date",
            side_effect=_fake_resolve_date,
        ),
        patch("tasks.sentiment_qa.upload_voice_task.asyncio.run") as async_run,
    ):
        async_run.return_value = {"total": 1, "success": 1, "failed": 0}
        yield async_run


@pytest.fixture
def task():
    qa_task = UploadVoiceTask(
        task_param={
            "gcs": {
                "project_id": "qa-project",
                "bucket_name": "qa-bucket",
                "input_folder": "input/%{DATA_DATE_YYYYMMDD}",
            },
            "sharepoint": {
                "control": {"control_file": "control/log.xlsx"},
                "verint": {
                    "input_folder_list_inbound": "Service",
                    "input_folder_list_outbound": "FIBER",
                    "source_folder": "/drive/root:/qa/Input/${QA_VERINT_PRODUCTS}/%{DATA_DATE_YYYYMMDD}",
                },
            },
            "framework": {
                "lookback_days": "1",
                "concurrency_upload": "5",
                "sender_email": "task-sender@example.com",
                "receiver_email": "task-receiver@example.com",
                "cc_email": "task-cc@example.com",
            },
        },
        packages={"execution_dt": EXECUTION_DT},
    )
    qa_task.sharepoint_verint = Mock()
    qa_task.sharepoint_control = Mock()
    qa_task.gcs_module = Mock(project_id="qa-project", bucket_name="qa-bucket")
    qa_task.msgraph_module = Mock()
    qa_task.result_df = pd.DataFrame()
    return qa_task


@pytest.fixture
def fixed_now():
    return datetime(2025, 1, 3, 10, 0, 0, tzinfo=ZoneInfo("UTC"))


class TestInitAndPreExecute:
    def test_init_sets_timezone_and_input_folder_list(self, task):
        assert task.project_id == "qa-project"
        assert task.combined_folder_list == ["Service", "FIBER"]
        assert task.timezone == ZoneInfo("UTC")

    def test_init_raises_when_timezone_is_missing(self):
        with (
            patch(
                "tasks.sentiment_qa.upload_voice_task.load_yaml",
                return_value={
                    "framework": {},
                    "verint": {},
                    "control": {},
                    "sandbox": {},
                    "msgraph": {},
                },
            ),
            patch(
                "tasks.sentiment_qa.upload_voice_task.resolve_env",
                side_effect=lambda value: value,
            ),
            pytest.raises(ValueError, match="Timezone not specified"),
        ):
            UploadVoiceTask(task_param={"gcs": {}, "sharepoint": {}, "framework": {}})

    def test_pre_execute_initializes_modules(self, task):
        with (
            patch("tasks.sentiment_qa.upload_voice_task.SharePointModule") as sharepoint_cls,
            patch("tasks.sentiment_qa.upload_voice_task.GCSModule") as gcs_cls,
            patch("tasks.sentiment_qa.upload_voice_task.MSGraphModule") as msgraph_cls,
        ):
            task.pre_execute()

        assert sharepoint_cls.call_count == 2
        gcs_cls.assert_called_once_with(project_id="qa-project", bucket_name="qa-bucket")
        msgraph_cls.assert_called_once_with(
            tenant_id="graph-tenant",
            client_id="graph-client",
            client_secret="graph-secret",
        )

    def test_pre_execute_raises_for_verint_error(self, task):
        with (
            patch(
                "tasks.sentiment_qa.upload_voice_task.SharePointModule",
                side_effect=Exception("verint boom"),
            ),
            patch("tasks.sentiment_qa.upload_voice_task.GCSModule"),
            patch("tasks.sentiment_qa.upload_voice_task.MSGraphModule"),
            pytest.raises(Exception, match="verint boom"),
        ):
            task.pre_execute()

    def test_pre_execute_raises_for_control_error(self, task):
        with (
            patch(
                "tasks.sentiment_qa.upload_voice_task.SharePointModule",
                side_effect=[Mock(), Exception("control boom")],
            ),
            patch("tasks.sentiment_qa.upload_voice_task.GCSModule"),
            patch("tasks.sentiment_qa.upload_voice_task.MSGraphModule"),
            pytest.raises(Exception, match="control boom"),
        ):
            task.pre_execute()

    def test_pre_execute_raises_for_gcs_error(self, task):
        with (
            patch("tasks.sentiment_qa.upload_voice_task.SharePointModule"),
            patch(
                "tasks.sentiment_qa.upload_voice_task.GCSModule",
                side_effect=Exception("gcs boom"),
            ),
            patch("tasks.sentiment_qa.upload_voice_task.MSGraphModule"),
            pytest.raises(Exception, match="gcs boom"),
        ):
            task.pre_execute()

    def test_pre_execute_raises_for_msgraph_error(self, task):
        with (
            patch("tasks.sentiment_qa.upload_voice_task.SharePointModule"),
            patch("tasks.sentiment_qa.upload_voice_task.GCSModule"),
            patch(
                "tasks.sentiment_qa.upload_voice_task.MSGraphModule",
                side_effect=Exception("msgraph boom"),
            ),
            pytest.raises(Exception, match="msgraph boom"),
        ):
            task.pre_execute()


class TestGetExistingControlFile:
    def test_get_existing_control_file_returns_empty_when_missing(self, task):
        task.sharepoint_control.is_item_exists.return_value = False

        result = task._get_existing_control_file()

        assert list(result.columns) == list(task.DEFAULT_CONTROL_SCHEMA.keys())
        assert result.empty

    def test_get_existing_control_file_parses_existing_excel(self, task):
        existing_df = pd.DataFrame(
            [
                {
                    "run_dt": "2025-01-03 08:00:00",
                    "datamonth": "202501",
                    "datadate": "20250102",
                    "processed_status": "Y",
                    "input_folder": "Service",
                    "remark": "done",
                }
            ]
        )
        task.sharepoint_control.is_item_exists.return_value = True
        task.sharepoint_control.get_item_by_path.return_value = Mock(content=_excel_bytes(existing_df))

        result = task._get_existing_control_file()

        assert result.iloc[0]["processed_status"] == "Y"
        assert result.iloc[0]["input_folder"] == "Service"

    def test_get_existing_control_file_returns_empty_when_excel_is_invalid(self, task):
        task.sharepoint_control.is_item_exists.return_value = True
        task.sharepoint_control.get_item_by_path.return_value = Mock(content=b"bad-excel")

        result = task._get_existing_control_file()

        assert result.empty

    def test_get_existing_control_file_defaults_values_when_date_parsing_fails(self, task):
        task.sharepoint_control.is_item_exists.return_value = True
        task.sharepoint_control.get_item_by_path.return_value = Mock(content=b"placeholder")

        bad_df = pd.DataFrame(
            [
                {
                    "run_dt": "bad",
                    "datamonth": "bad",
                    "datadate": "bad",
                    "processed_status": "Y",
                    "input_folder": "Service",
                    "remark": "bad",
                }
            ]
        )

        with (
            patch("tasks.sentiment_qa.upload_voice_task.pd.read_excel", return_value=bad_df),
            patch(
                "tasks.sentiment_qa.upload_voice_task.pd.to_datetime",
                side_effect=ValueError("bad date"),
            ),
        ):
            result = task._get_existing_control_file()

        assert result.iloc[0]["run_dt"] is None
        assert result.iloc[0]["processed_status"] == "N"
        assert result.iloc[0]["input_folder"] == ""

    def test_get_existing_control_file_returns_empty_when_schema_validation_fails(self, task):
        task.sharepoint_control.is_item_exists.return_value = True
        task.sharepoint_control.get_item_by_path.return_value = Mock(content=b"placeholder")

        with (
            patch(
                "tasks.sentiment_qa.upload_voice_task.pd.read_excel",
                return_value=pd.DataFrame([{"run_dt": "2025-01-03 08:00:00"}]),
            ),
            patch(
                "tasks.sentiment_qa.upload_voice_task.ensure_df_schema",
                side_effect=ValueError("schema boom"),
            ),
        ):
            result = task._get_existing_control_file()

        assert result.empty
        assert list(result.columns) == list(task.DEFAULT_CONTROL_SCHEMA.keys())

    def test_get_existing_control_file_wraps_critical_errors(self, task):
        task.sharepoint_control.is_item_exists.side_effect = RuntimeError("load boom")

        with pytest.raises(Exception, match="Critical error loading control file: load boom"):
            task._get_existing_control_file()


class TestExecuteTask:
    def test_execute_task_handles_missing_pre_result(self, task):
        task.pre_result = None

        with (
            patch.object(task, "_get_existing_control_file", return_value=pd.DataFrame()) as get_control,
            patch.object(task, "_upload_voice_files") as upload_voice_files,
        ):
            task.execute_task()

        get_control.assert_called_once_with()
        upload_voice_files.assert_called_once()
        assert task.result_df is None

    def test_execute_task_wraps_upload_errors(self, task):
        task.pre_result = None

        with (
            patch.object(task, "_get_existing_control_file", return_value=pd.DataFrame()),
            patch.object(task, "_upload_voice_files", side_effect=RuntimeError("upload boom")),
            pytest.raises(Exception, match="Voice file upload failed: upload boom"),
        ):
            task.execute_task()

    def test_execute_task_uses_rerun_date(self, task):
        task.pre_result = ([], pd.DataFrame())
        task.packages["rerun_data_dt"] = "2025-01-03"

        with (
            patch(
                "tasks.sentiment_qa.upload_voice_task.add_date",
                side_effect=[datetime(2025, 1, 2), datetime(2025, 1, 2)],
            ),
            patch.object(task, "_get_existing_control_file", return_value=pd.DataFrame()) as get_control,
            patch.object(task, "_upload_voice_files") as upload_voice_files,
        ):
            task.execute_task()

        get_control.assert_called_once_with()
        upload_args = upload_voice_files.call_args.args
        assert upload_args[1:] == ("20250102", "20250102")
        assert upload_args[0].empty

    def test_execute_task_wraps_date_errors(self, task):
        task.pre_result = None

        with (
            patch(
                "tasks.sentiment_qa.upload_voice_task.add_date",
                side_effect=ValueError("bad date"),
            ),
            pytest.raises(Exception, match="Cannot determine processing date range: bad date"),
        ):
            task.execute_task()


class TestUploadVoiceFiles:
    def test_upload_voice_files_skips_already_processed_entries(self, task, patch_common, fixed_now):
        task.rerun_start_date = None
        task.rerun_end_date = None
        task.combined_folder_list = ["Service"]
        task.input_folder_list_inbound = ["Service"]
        task.input_folder_list_outbound = []
        task.result_df = pd.DataFrame()
        hardcoded_dates = ["20260601", "20260602", "20260603", "20260604", "20260605", "20260606", "20260607"]
        existing_df = pd.DataFrame(
            [
                {
                    "run_dt": f"2026-06-0{i + 1} 08:00:00",
                    "datamonth": "202606",
                    "datadate": date,
                    "processed_status": "Y",
                    "input_folder": "Service",
                    "remark": "done",
                }
                for i, date in enumerate(hardcoded_dates)
            ]
        )

        with (
            patch(
                "tasks.sentiment_qa.upload_voice_task.get_current_datetime",
                return_value=fixed_now,
            ),
            patch(
                "tasks.sentiment_qa.upload_voice_task.add_date",
                return_value=datetime(2025, 1, 1),
            ),
            patch.object(task, "_build_summary_table", return_value="<table></table>"),
        ):
            task._upload_voice_files(existing_df, "20260601", "20260607")

        task.sharepoint_verint.list_files.assert_not_called()
        patch_common.assert_not_called()
        task.msgraph_module.send_email.assert_called_once()

    def test_upload_voice_files_stamps_missing_source_folder(self, task, fixed_now):
        task.rerun_start_date = None
        task.rerun_end_date = None
        task.combined_folder_list = ["Service"]
        task.result_df = pd.DataFrame()
        task.sharepoint_verint.is_item_exists.return_value = False
        existing_df = pd.DataFrame(columns=task.DEFAULT_CONTROL_SCHEMA.keys())

        with (
            patch(
                "tasks.sentiment_qa.upload_voice_task.get_current_datetime",
                return_value=fixed_now,
            ),
            patch(
                "tasks.sentiment_qa.upload_voice_task.add_date",
                return_value=datetime(2025, 1, 1),
            ),
            patch(
                "tasks.sentiment_qa.upload_voice_task.df_to_excel_bytes",
                return_value=b"fake",
            ) as mock_excel,
        ):
            task._upload_voice_files(existing_df, "20250102", "20250102")

        captured_df = mock_excel.call_args.args[0]
        assert captured_df.iloc[0]["processed_status"] == "N"
        assert captured_df.iloc[0]["remark"] == "Source folder does not exist"

    def test_upload_voice_files_stamps_no_voice_files(self, task, fixed_now):
        task.rerun_start_date = None
        task.rerun_end_date = None
        task.combined_folder_list = ["Service"]
        task.result_df = pd.DataFrame()
        task.sharepoint_verint.is_item_exists.return_value = True
        task.sharepoint_verint.list_files.return_value = []
        existing_df = pd.DataFrame(columns=task.DEFAULT_CONTROL_SCHEMA.keys())

        with (
            patch(
                "tasks.sentiment_qa.upload_voice_task.get_current_datetime",
                return_value=fixed_now,
            ),
            patch(
                "tasks.sentiment_qa.upload_voice_task.add_date",
                return_value=datetime(2025, 1, 1),
            ),
            patch(
                "tasks.sentiment_qa.upload_voice_task.df_to_excel_bytes",
                return_value=b"fake",
            ) as mock_excel,
        ):
            task._upload_voice_files(existing_df, "20250102", "20250102")

        captured_df = mock_excel.call_args.args[0]
        assert captured_df.iloc[0]["remark"] == "No voice files found"

    def test_upload_voice_files_filters_invalid_and_outbound_files(self, task, patch_common, fixed_now):
        task.rerun_start_date = None
        task.rerun_end_date = None
        task.combined_folder_list = ["Service"]
        task.result_df = pd.DataFrame()
        task.sharepoint_verint.is_item_exists.return_value = True
        task.sharepoint_verint.list_files.return_value = [
            {
                "name": "skip.txt",
                "id": "1",
                "parentReference": {"path": "/drive/root:/qa/Input/Service/20250102"},
                "createdDateTime": "2025-01-02T00:00:00Z",
            },
            {
                "name": "call123_0812345678_090000_AGENT1_alice_smith_provider_20250102_60_OUT.wav",
                "id": "2",
                "parentReference": {"path": "/drive/root:/qa/Input/Service/20250102"},
                "createdDateTime": "2025-01-02T00:00:00Z",
            },
        ]
        existing_df = pd.DataFrame(columns=task.DEFAULT_CONTROL_SCHEMA.keys())

        with (
            patch(
                "tasks.sentiment_qa.upload_voice_task.get_current_datetime",
                return_value=fixed_now,
            ),
            patch(
                "tasks.sentiment_qa.upload_voice_task.add_date",
                return_value=datetime(2025, 1, 1),
            ),
            patch(
                "tasks.sentiment_qa.upload_voice_task.df_to_excel_bytes",
                return_value=b"fake",
            ) as mock_excel,
        ):
            task._upload_voice_files(existing_df, "20250102", "20250102")

        patch_common.assert_not_called()
        captured_df = mock_excel.call_args.args[0]
        assert captured_df.iloc[0]["remark"] == "No valid file in folder (not .wav, outbound file)"

    def test_upload_voice_files_uploads_valid_files_and_builds_ai_output_summary(self, task, patch_common, fixed_now):
        task.rerun_start_date = None
        task.rerun_end_date = None
        task.input_folder_list = ["Service"]
        task.result_df = pd.DataFrame(
            [
                {"department": "Service", "call_direction": "IN", "call_date": "20250101", "status": "SUCCESS"},
                {"department": "Service", "call_direction": "IN", "call_date": "20250101", "status": "FAILED"},
            ]
        )
        task.sharepoint_verint.is_item_exists.return_value = True
        file_name = "call123_0812345678_090000_AGENT1_alice_smith_provider_20250102_60_IN.wav"
        task.sharepoint_verint.list_files.return_value = [
            {
                "name": file_name,
                "id": "1",
                "parentReference": {"path": "/drive/root:/qa/Input/Service/20250102"},
                "createdDateTime": "2025-01-02T00:00:00Z",
            }
        ]
        existing_df = pd.DataFrame(columns=task.DEFAULT_CONTROL_SCHEMA.keys())

        with patch(
            "tasks.sentiment_qa.upload_voice_task.get_current_datetime",
            return_value=fixed_now,
        ):
            task._upload_voice_files(existing_df, "20250102", "20250102")

        stream_list = task.gcs_module.upload_sharepoint_to_gcs.call_args.kwargs["stream_list"]
        assert stream_list[0]["upload"] == f"input/20250102/Service/{file_name}"
        email_body = task.msgraph_module.send_email.call_args.kwargs["body"]
        assert "AI Output" in email_body
        assert "Service" in email_body
        assert "01 Jan 2025" in email_body

    def test_upload_voice_files_falls_back_to_datadate_when_record_date_errors(self, task, fixed_now):
        from tasks.sentiment_qa import upload_voice_task as upload_voice_task_module

        task.rerun_start_date = None
        task.rerun_end_date = None
        task.combined_folder_list = ["Service"]
        task.result_df = pd.DataFrame()
        task.sharepoint_verint.is_item_exists.return_value = True
        file_name = "call123_0812345678_090000_AGENT1_alice_smith_provider_20250102_60_IN.wav"
        task.sharepoint_verint.list_files.return_value = [
            {
                "name": file_name,
                "id": "1",
                "parentReference": {"path": "/drive/root:/qa/Input/Service/20250102"},
                "createdDateTime": "2025-01-02T00:00:00Z",
            }
        ]
        existing_df = pd.DataFrame(columns=task.DEFAULT_CONTROL_SCHEMA.keys())
        original_safe_list_get = upload_voice_task_module.safe_list_get

        def flaky_safe_list_get(values, index, default=None):
            if index == -3:
                raise RuntimeError("record date boom")
            return original_safe_list_get(values, index, default)

        with (
            patch(
                "tasks.sentiment_qa.upload_voice_task.safe_list_get",
                side_effect=flaky_safe_list_get,
            ),
            patch(
                "tasks.sentiment_qa.upload_voice_task.get_current_datetime",
                return_value=fixed_now,
            ),
        ):
            task._upload_voice_files(existing_df, "20250102", "20250102")

        stream_list = task.gcs_module.upload_sharepoint_to_gcs.call_args.kwargs["stream_list"]
        assert stream_list[0]["upload"] == f"input/20250102/Service/{file_name}"

    def test_upload_voice_files_uses_datadate_for_invalid_record_date(self, task):
        task.rerun_start_date = None
        task.rerun_end_date = None
        task.combined_folder_list = ["Service"]
        task.result_df = pd.DataFrame()
        task.sharepoint_verint.is_item_exists.return_value = True
        file_name = "call123_0812345678_090000_AGENT1_alice_smith_provider_invalid_60_IN.wav"
        task.sharepoint_verint.list_files.return_value = [
            {
                "name": file_name,
                "id": "1",
                "parentReference": {"path": "/drive/root:/qa/Input/Service/20250102"},
                "createdDateTime": "2025-01-02T00:00:00Z",
            }
        ]
        existing_df = pd.DataFrame(columns=task.DEFAULT_CONTROL_SCHEMA.keys())

        with patch(
            "tasks.sentiment_qa.upload_voice_task.get_current_datetime",
            return_value=datetime(2025, 1, 3, 10, 0, 0),
        ):
            task._upload_voice_files(existing_df, "20250102", "20250102")

        stream_list = task.gcs_module.upload_sharepoint_to_gcs.call_args.kwargs["stream_list"]
        assert stream_list[0]["upload"] == f"input/20250102/Service/{file_name}"


class TestBuildSummaryTable:
    def test_build_summary_table_formats_totals_and_zero_rows(self, task):
        task.input_folder_list = ["Service", "FIBER"]
        html = task._build_summary_table(
            [
                {
                    "Service": "Service",
                    "call_direction": "IN",
                    "Date": "20250102",
                    "Total": 1,
                    "Success": 1,
                    "Failed": 0,
                },
                {"Service": "FIBER", "call_direction": "IN", "Date": "20250102", "Total": 0, "Success": 0, "Failed": 0},
            ]
        )

        assert 'class="total-row"' in html
        assert "02 Jan 2025" in html
        assert 'style="color: red;"' in html

    def test_build_summary_table_falls_back_to_string_for_uncoercible_values(self, task):
        class FlakyNumeric:
            def __init__(self, label):
                self.label = label
                self._first_float = True

            def __float__(self):
                if self._first_float:
                    self._first_float = False
                    raise ValueError("bad float")
                return 0.0

            def __int__(self):
                return 0

            def __radd__(self, other):
                return other

            def __add__(self, other):
                return other

            def __str__(self):
                return self.label

        task.input_folder_list = ["Service"]
        html = task._build_summary_table(
            [
                {
                    "Service": "Service",
                    "call_direction": "IN",
                    "Date": "20250102",
                    "Total": FlakyNumeric("n/a"),
                    "Success": FlakyNumeric("ok"),
                    "Failed": FlakyNumeric("bad"),
                }
            ]
        )

        assert "n/a" in html
        assert "ok" in html
        assert "bad" in html


class TestOnError:
    def test_on_error_sends_email(self, task, fixed_now):
        with patch(
            "tasks.sentiment_qa.upload_voice_task.get_current_datetime",
            return_value=fixed_now,
        ):
            task.on_error(RuntimeError("upload boom"))

        kwargs = task.msgraph_module.send_email.call_args.kwargs
        assert kwargs["subject"] == "[AI Failed] [AI-QA]"
        assert "upload boom" in kwargs["body"]
        assert kwargs["receiver_email"] == "receiver@example.com"

    def test_on_error_swallows_email_failures(self, task, fixed_now):
        task.msgraph_module.send_email.side_effect = RuntimeError("email boom")

        with patch(
            "tasks.sentiment_qa.upload_voice_task.get_current_datetime",
            return_value=fixed_now,
        ):
            task.on_error(RuntimeError("upload boom"))
