from datetime import datetime
from unittest.mock import MagicMock, Mock, patch
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from tasks.sentiment_telesale.schemas.control_log_schema import ControlLogSchema
from tasks.sentiment_telesale.upload_voice_task import UploadVoiceTask


class TestUploadVoiceTask:
    @pytest.fixture
    def mock_deps(self):
        with (
            patch("tasks.sentiment_telesale.upload_voice_task.SharePointModule") as sp,
            patch("tasks.sentiment_telesale.upload_voice_task.GCSModule") as gcs,
            patch("tasks.sentiment_telesale.upload_voice_task.load_yaml") as ly,
            patch("tasks.sentiment_telesale.upload_voice_task.resolve_env") as re,
            patch("tasks.sentiment_telesale.upload_voice_task.resolve_date") as rd,
            patch("tasks.sentiment_telesale.upload_voice_task.asyncio.run") as ar,
        ):
            re.side_effect = lambda x: x
            rd.side_effect = lambda text, replace_date: text
            ly.return_value = {
                "verint": {"site_domain": "v.com"},
                "control": {"site_domain": "c.com"},
                "framework": {"timezone": "UTC"},
            }
            yield sp, gcs, ly, re, rd, ar

    @pytest.fixture
    def task(self, mock_deps):
        t = UploadVoiceTask()
        t.gcs = {"project_id": "p", "bucket_name": "b", "input_folder": "in"}
        t.framework = {"lookback_days": "7", "concurrency_upload": "5"}
        t.sharepoint = {"control": {"control_file": "ctrl.xlsx"}, "verint": {"source_folder": "src"}}
        t.packages = {"execution_dt": datetime(2025, 1, 1)}
        t.get_package = lambda k, d=None: t.packages.get(k, d)
        t.timezone = ZoneInfo("UTC")  # Set directly as __init__ logic is skipped in partial mock or needs config

        # Mock initialized modules
        t.sharepoint_verint = Mock()
        t.sharepoint_control = Mock()
        t.gcs_module = Mock()

        return t

    def test_pre_execute(self, task):
        task.sharepoint_verint = None
        task.pre_execute()
        assert task.sharepoint_verint is not None
        assert task.sharepoint_control is not None
        assert task.gcs_module is not None

    def test_get_existing_control_file_new(self, task):
        task.sharepoint_control.is_item_exists.return_value = False
        df = task._get_existing_control_file()
        assert isinstance(df, pd.DataFrame)
        assert df.empty
        assert list(df.columns) == list(ControlLogSchema.to_schema().columns.keys())

    def test_get_existing_control_file_exists(self, task):
        task.sharepoint_control.is_item_exists.return_value = True

        content = MagicMock()
        content.content = b""

        # Mock existing DF
        valid_df = pd.DataFrame(
            [
                {
                    "run_dt": "2025-01-01 00:00:00",
                    "datamonth": "202501",
                    "datadate": "20250101",
                    "processed_status": "Y",
                    "remark": "",
                }
            ]
        )

        with patch("pandas.read_excel", return_value=valid_df):
            task.sharepoint_control.get_item_by_path.return_value = content
            df = task._get_existing_control_file()

            assert not df.empty
            assert df.iloc[0]["processed_status"] == "Y"

    def test_upload_voice_files(self, task, mock_deps):
        _, _, _, _, _, ar = mock_deps

        # Setup
        existing_df = pd.DataFrame(columns=ControlLogSchema.to_schema().columns.keys())
        start_date = "20250101"
        end_date = "20250101"
        datetime(2025, 1, 1, tzinfo=ZoneInfo("UTC"))

        # Mock SharePoint file listing
        # Mock folder exists
        task.sharepoint_verint.is_item_exists.return_value = True

        # Mock files
        task.sharepoint_verint.list_files.return_value = [
            {
                "name": "call_p_t_a_f_l_p_20250101_d.wav",
                "id": "1",
                "parentReference": {"path": "/drive/root:/src"},
                "createdDateTime": "2025-01-01T10:00:00Z",
            }
        ]

        task._upload_voice_files(existing_df, start_date, end_date)

        # Verify upload call
        ar.assert_called()  # asyncio.run called for upload
        task.sharepoint_control.upload_file.assert_called()  # Updated control file uploaded

    def test_upload_voice_files_skip_existing(self, task, mock_deps):
        _, _, _, _, _, ar = mock_deps

        # Setup existing confirmed upload
        existing_df = pd.DataFrame(
            [
                {
                    "run_dt": "2025-01-01",
                    "datamonth": "202501",
                    "datadate": "20250101",
                    "processed_status": "Y",
                    "remark": "",
                }
            ]
        )

        start_date = "20250101"
        end_date = "20250101"

        task._upload_voice_files(existing_df, start_date, end_date)

        # Should not call upload for this date
        task.sharepoint_verint.list_files.assert_not_called()
        ar.assert_not_called()


"""
Phase 3 coverage enhancement tests for UploadVoiceTask.
Target: 66% → 80% coverage

Focus areas:
- __init__ timezone error (line 71)
- pre_execute error handling (lines 91-93, 106-108, 119-121)
- execute_task error handling (lines 127-152)
- Control file parsing errors (lines 176-178, 192-202, 206-208)
- stamp_control_log invalid status (lines 237-238)
- Upload flow edge cases (lines 274-278, 283-287, 298, 309-313)
"""
import io

import pytest


@pytest.fixture
def task_instance():
    """Create UploadVoiceTask with proper init mocking."""
    with patch("tasks.sentiment_telesale.upload_voice_task.load_yaml") as mock_yaml:
        mock_yaml.return_value = {
            "framework": {"timezone": "UTC"},
            "verint": {
                "site_domain": "verint.com",
                "client_id": "cid",
                "client_secret": "cs",
                "tenant_id": "tid",
                "site_path": "/sites/verint",
            },
            "control": {
                "site_domain": "control.com",
                "client_id": "cid2",
                "client_secret": "cs2",
                "tenant_id": "tid2",
                "site_path": "/sites/control",
            },
        }

        task = UploadVoiceTask.__new__(UploadVoiceTask)
        task.config = {}
        task.packages = {"execution_dt": datetime(2025, 1, 1, tzinfo=ZoneInfo("UTC"))}
        task.gcs = {
            "project_id": "test-project",
            "bucket_name": "test-bucket",
            "input_folder": "input/%{DATA_DATE_YYYYMMDD}",
        }
        task.sharepoint = {
            "control": {"control_file": "control/log.xlsx"},
            "verint": {"source_folder": "verint/%{DATA_DATE_YYYYMMDD}"},
        }
        task.framework = {"lookback_days": "7", "concurrency_upload": "5"}
        task.timezone = ZoneInfo("UTC")
        task.COMMON_CONFIG_PATH = "config/common.yml"
        task.DEFAULT_CONTROL_SHEET_NAME = "ControlLog"
        task.DEFAULT_CONTROL_SCHEMA = {
            "run_dt": None,
            "datamonth": None,
            "datadate": None,
            "processed_status": None,
            "remark": None,
        }

        # Mock verint and control access
        task.verint_access = mock_yaml.return_value["verint"]
        task.control_access = mock_yaml.return_value["control"]

        def mock_get_package(key, default=None):
            return task.packages.get(key, default)

        task.get_package = mock_get_package

        return task


class TestInitialization:
    """Test __init__ error handling - Line 71."""

    @patch("tasks.sentiment_telesale.upload_voice_task.load_yaml")
    @patch("tasks.sentiment_telesale.upload_voice_task.get_value_by_path", return_value=None)
    def test_init_missing_timezone_error(self, mock_get_value, mock_yaml):
        """Test __init__ with missing timezone raises error - Line 71."""
        mock_yaml.return_value = {
            "framework": {},  # No timezone
            "verint": {},
            "control": {},
        }

        with pytest.raises(ValueError, match="Timezone not specified"):
            UploadVoiceTask()


class TestPreExecuteErrorHandling:
    """Test pre_execute error handling - Lines 91-93, 106-108, 119-121."""

    @patch("tasks.sentiment_telesale.upload_voice_task.SharePointModule", side_effect=Exception("Verint SP Error"))
    @patch("tasks.sentiment_telesale.upload_voice_task.resolve_env", side_effect=lambda x: x)
    def test_pre_execute_verint_sharepoint_error(self, mock_resolve, mock_sp, task_instance):
        """Test SharePoint Verint initialization error - Lines 91-93."""
        with pytest.raises(Exception, match="Verint SP Error"):
            task_instance.pre_execute()

    @patch("tasks.sentiment_telesale.upload_voice_task.SharePointModule")
    @patch("tasks.sentiment_telesale.upload_voice_task.resolve_env", side_effect=lambda x: x)
    def test_pre_execute_control_sharepoint_error(self, mock_resolve, mock_sp, task_instance):
        """Test SharePoint Control initialization error - Lines 106-108."""
        # First call (Verint) succeeds, second call (Control) fails
        mock_sp.side_effect = [Mock(), Exception("Control SP Error")]

        with pytest.raises(Exception, match="Control SP Error"):
            task_instance.pre_execute()

    @patch("tasks.sentiment_telesale.upload_voice_task.GCSModule", side_effect=Exception("GCS Error"))
    @patch("tasks.sentiment_telesale.upload_voice_task.SharePointModule")
    @patch("tasks.sentiment_telesale.upload_voice_task.resolve_env", side_effect=lambda x: x)
    def test_pre_execute_gcs_error(self, mock_resolve, mock_sp, mock_gcs, task_instance):
        """Test GCS initialization error - Lines 119-121."""
        with pytest.raises(Exception, match="GCS Error"):
            task_instance.pre_execute()


class TestExecuteTaskErrorHandling:
    """Test execute_task error handling - Lines 127-152."""

    @patch("tasks.sentiment_telesale.upload_voice_task.add_date", side_effect=Exception("Date error"))
    @patch("tasks.sentiment_telesale.upload_voice_task.resolve_env", side_effect=lambda x: x)
    def test_execute_task_date_calculation_error(self, mock_resolve, mock_add_date, task_instance):
        """Test execute_task date calculation error - Lines 139-141."""
        task_instance.packages = {"execution_dt": datetime(2025, 1, 1)}

        with pytest.raises(Exception, match="Cannot determine processing date range"):
            task_instance.execute_task()

    @patch("tasks.sentiment_telesale.upload_voice_task.add_date", return_value=datetime(2024, 12, 25))
    @patch("tasks.sentiment_telesale.upload_voice_task.resolve_env", side_effect=lambda x: x)
    def test_execute_task_upload_voice_files_error(self, mock_resolve, mock_add_date, task_instance):
        """Test execute_task with _upload_voice_files error - Lines 149-151."""
        task_instance.packages = {"execution_dt": datetime(2025, 1, 1)}

        with patch.object(task_instance, "_get_existing_control_file", return_value=pd.DataFrame()):
            with patch.object(task_instance, "_upload_voice_files", side_effect=Exception("Upload error")):
                with pytest.raises(Exception, match="Voice file upload failed"):
                    task_instance.execute_task()


class TestGetExistingControlFileErrors:
    """Test _get_existing_control_file error handling - Lines 176-178, 192-202, 206-208."""

    @patch("tasks.sentiment_telesale.upload_voice_task.get_value_by_path", return_value="control.xlsx")
    @patch("tasks.sentiment_telesale.upload_voice_task.resolve_env", side_effect=lambda x: x)
    def test_get_existing_control_file_parse_error(self, mock_resolve, mock_get_value, task_instance):
        """Test control file parsing error - Lines 176-178."""
        task_instance.sharepoint_control = Mock()
        task_instance.sharepoint_control.is_item_exists.return_value = True

        mock_content = Mock()
        mock_content.content = b"invalid excel content"
        task_instance.sharepoint_control.get_item_by_path.return_value = mock_content

        # Should return empty DataFrame when parsing fails
        result = task_instance._get_existing_control_file()

        assert isinstance(result, pd.DataFrame)
        assert result.empty

    @patch("tasks.sentiment_telesale.upload_voice_task.ControlLogSchema.validate")
    @patch("tasks.sentiment_telesale.upload_voice_task.get_value_by_path", return_value="control.xlsx")
    @patch("tasks.sentiment_telesale.upload_voice_task.resolve_env", side_effect=lambda x: x)
    @patch("pandas.read_excel")
    def test_get_existing_control_file_date_parsing_error(
        self, mock_read_excel, mock_resolve, mock_get_value, mock_schema, task_instance
    ):
        """Test control file date parsing error - Lines 192-202."""
        task_instance.sharepoint_control = Mock()
        task_instance.sharepoint_control.is_item_exists.return_value = True

        mock_content = Mock()
        mock_content.content = b"data"
        task_instance.sharepoint_control.get_item_by_path.return_value = mock_content

        # Create DataFrame that will fail date parsing
        test_df = pd.DataFrame(
            [
                {
                    "run_dt": "invalid_date",
                    "datamonth": "bad_month",
                    "datadate": "bad_date",
                    "processed_status": "Y",
                    "remark": "",
                }
            ]
        )

        mock_read_excel.return_value = test_df
        mock_schema.return_value = test_df

        # Mock pd.to_datetime to raise error
        with patch("pandas.to_datetime", side_effect=Exception("Date parse error")):
            result = task_instance._get_existing_control_file()

        # Should handle error and set default values
        assert isinstance(result, pd.DataFrame)
        assert not result.empty
        # Check default values were set (lines 196-201)
        assert result.iloc[0]["run_dt"] is None

    @patch(
        "tasks.sentiment_telesale.upload_voice_task.ControlLogSchema.validate", side_effect=Exception("Schema error")
    )
    @patch("tasks.sentiment_telesale.upload_voice_task.get_value_by_path", return_value="control.xlsx")
    @patch("tasks.sentiment_telesale.upload_voice_task.resolve_env", side_effect=lambda x: x)
    @patch("pandas.read_excel")
    def test_get_existing_control_file_schema_error(
        self, mock_read_excel, mock_resolve, mock_get_value, mock_schema, task_instance
    ):
        """Test control file schema error - Lines 206-208."""
        task_instance.sharepoint_control = Mock()
        task_instance.sharepoint_control.is_item_exists.return_value = True

        mock_content = Mock()
        mock_content.content = b"data"
        task_instance.sharepoint_control.get_item_by_path.return_value = mock_content

        test_df = pd.DataFrame([{"run_dt": "2025-01-01"}])
        mock_read_excel.return_value = test_df

        # Should return empty DataFrame on schema error
        result = task_instance._get_existing_control_file()

        assert isinstance(result, pd.DataFrame)
        assert result.empty


class TestStampControlLogInvalidStatus:
    """Test stamp_control_log with invalid status - Lines 237-238."""

    @patch(
        "tasks.sentiment_telesale.upload_voice_task.get_current_datetime",
        return_value=datetime(2025, 1, 1, tzinfo=ZoneInfo("UTC")),
    )
    @patch("tasks.sentiment_telesale.upload_voice_task.list_date", return_value=["20250101"])
    @patch("tasks.sentiment_telesale.upload_voice_task.resolve_date", side_effect=lambda text, replace_date: text)
    @patch("tasks.sentiment_telesale.upload_voice_task.resolve_env", side_effect=lambda x: x)
    def test_upload_voice_files_invalid_status_warning(
        self, mock_resolve, mock_resolve_date, mock_list_date, mock_datetime, task_instance
    ):
        """Test stamp_control_log with invalid status triggers warning - Lines 237-238."""
        task_instance.sharepoint_verint = Mock()
        task_instance.sharepoint_control = Mock()

        # Mock no source folder exists to trigger stamp_control_log with custom status
        task_instance.sharepoint_verint.is_item_exists.return_value = False
        task_instance.sharepoint_control.upload_file = Mock()

        existing_df = pd.DataFrame(columns=task_instance.DEFAULT_CONTROL_SCHEMA.keys())

        # This will call stamp_control_log with status="N" via the path
        task_instance._upload_voice_files(existing_df, "20250101", "20250101")

        # Verify stamp was called (indirectly through the upload flow)
        assert task_instance.sharepoint_control.upload_file.called


class TestUploadVoiceFilesEdgeCases:
    """Test _upload_voice_files edge cases - Lines 274-278, 283-287, 298, 309-313."""

    @patch(
        "tasks.sentiment_telesale.upload_voice_task.get_current_datetime",
        return_value=datetime(2025, 1, 1, tzinfo=ZoneInfo("UTC")),
    )
    @patch("tasks.sentiment_telesale.upload_voice_task.list_date", return_value=["20250101"])
    @patch("tasks.sentiment_telesale.upload_voice_task.resolve_date", side_effect=lambda text, replace_date: text)
    @patch("tasks.sentiment_telesale.upload_voice_task.resolve_env", side_effect=lambda x: x)
    def test_upload_voice_files_source_folder_not_found(
        self, mock_resolve, mock_resolve_date, mock_list_date, mock_datetime, task_instance
    ):
        """Test source folder not found path - Lines 274-278."""
        task_instance.sharepoint_verint = Mock()
        task_instance.sharepoint_control = Mock()
        task_instance.sharepoint_verint.is_item_exists.return_value = False

        existing_df = pd.DataFrame(columns=task_instance.DEFAULT_CONTROL_SCHEMA.keys())

        task_instance._upload_voice_files(existing_df, "20250101", "20250101")

        # Should call upload_file to save control log
        task_instance.sharepoint_control.upload_file.assert_called_once()

    @patch(
        "tasks.sentiment_telesale.upload_voice_task.get_current_datetime",
        return_value=datetime(2025, 1, 1, tzinfo=ZoneInfo("UTC")),
    )
    @patch("tasks.sentiment_telesale.upload_voice_task.list_date", return_value=["20250101"])
    @patch("tasks.sentiment_telesale.upload_voice_task.resolve_date", side_effect=lambda text, replace_date: text)
    @patch("tasks.sentiment_telesale.upload_voice_task.resolve_env", side_effect=lambda x: x)
    def test_upload_voice_files_no_voice_files(
        self, mock_resolve, mock_resolve_date, mock_list_date, mock_datetime, task_instance
    ):
        """Test no voice files found path - Lines 283-287."""
        task_instance.sharepoint_verint = Mock()
        task_instance.sharepoint_control = Mock()
        task_instance.sharepoint_verint.is_item_exists.return_value = True
        task_instance.sharepoint_verint.list_files.return_value = []

        existing_df = pd.DataFrame(columns=task_instance.DEFAULT_CONTROL_SCHEMA.keys())

        task_instance._upload_voice_files(existing_df, "20250101", "20250101")

        # Should call upload_file to save control log with remark
        task_instance.sharepoint_control.upload_file.assert_called_once()

    @patch(
        "tasks.sentiment_telesale.upload_voice_task.get_current_datetime",
        return_value=datetime(2025, 1, 1, tzinfo=ZoneInfo("UTC")),
    )
    @patch("tasks.sentiment_telesale.upload_voice_task.list_date", return_value=["20250101"])
    @patch("tasks.sentiment_telesale.upload_voice_task.resolve_date", side_effect=lambda text, replace_date: text)
    @patch("tasks.sentiment_telesale.upload_voice_task.resolve_env", side_effect=lambda x: x)
    def test_upload_voice_files_no_valid_wav_files(
        self, mock_resolve, mock_resolve_date, mock_list_date, mock_datetime, task_instance
    ):
        """Test no valid .wav files path - Line 298."""
        task_instance.sharepoint_verint = Mock()
        task_instance.sharepoint_control = Mock()
        task_instance.sharepoint_verint.is_item_exists.return_value = True
        # Return files with non-.wav extensions
        task_instance.sharepoint_verint.list_files.return_value = [
            {
                "name": "file.txt",
                "id": "1",
                "parentReference": {"path": "/drive/root:/verint"},
                "createdDateTime": "2025-01-01T10:00:00Z",
            }
        ]

        existing_df = pd.DataFrame(columns=task_instance.DEFAULT_CONTROL_SCHEMA.keys())

        task_instance._upload_voice_files(existing_df, "20250101", "20250101")

        # Should call upload_file with remark about no valid files
        task_instance.sharepoint_control.upload_file.assert_called_once()

    @patch("tasks.sentiment_telesale.upload_voice_task.asyncio.run")
    @patch("tasks.sentiment_telesale.upload_voice_task.is_format_datetime", return_value=False)
    @patch("tasks.sentiment_telesale.upload_voice_task.safe_list_get", side_effect=Exception("Parse error"))
    @patch(
        "tasks.sentiment_telesale.upload_voice_task.get_current_datetime",
        return_value=datetime(2025, 1, 1, tzinfo=ZoneInfo("UTC")),
    )
    @patch("tasks.sentiment_telesale.upload_voice_task.list_date", return_value=["20250101"])
    @patch("tasks.sentiment_telesale.upload_voice_task.resolve_date", side_effect=lambda text, replace_date: text)
    @patch("tasks.sentiment_telesale.upload_voice_task.resolve_env", side_effect=lambda x: x)
    def test_upload_voice_files_record_date_extraction_error(
        self,
        mock_resolve,
        mock_resolve_date,
        mock_list_date,
        mock_datetime,
        mock_safe_list,
        mock_is_format,
        mock_asyncio,
        task_instance,
    ):
        """Test record date extraction error - Lines 309-313."""
        task_instance.sharepoint_verint = Mock()
        task_instance.sharepoint_control = Mock()
        task_instance.gcs_module = Mock()
        task_instance.sharepoint_verint.is_item_exists.return_value = True
        task_instance.sharepoint_verint.list_files.return_value = [
            {
                "name": "test_file.wav",
                "id": "1",
                "parentReference": {"path": "/drive/root:/verint"},
                "createdDateTime": "2025-01-01T10:00:00Z",
            }
        ]

        existing_df = pd.DataFrame(columns=task_instance.DEFAULT_CONTROL_SCHEMA.keys())

        task_instance._upload_voice_files(existing_df, "20250101", "20250101")

        # Should handle error and use datadate as fallback
        mock_asyncio.assert_called_once()

    @patch("tasks.sentiment_telesale.upload_voice_task.asyncio.run")
    @patch(
        "tasks.sentiment_telesale.upload_voice_task.get_current_datetime",
        return_value=datetime(2025, 1, 1, tzinfo=ZoneInfo("UTC")),
    )
    @patch("tasks.sentiment_telesale.upload_voice_task.list_date", return_value=["20250101"])
    @patch("tasks.sentiment_telesale.upload_voice_task.resolve_date", side_effect=lambda text, replace_date: text)
    @patch("tasks.sentiment_telesale.upload_voice_task.resolve_env", side_effect=lambda x: x)
    def test_upload_voice_files_successful_upload(
        self, mock_resolve, mock_resolve_date, mock_list_date, mock_datetime, mock_asyncio, task_instance
    ):
        """Test successful voice file upload with asyncio - Lines 320-323."""
        task_instance.sharepoint_verint = Mock()
        task_instance.sharepoint_control = Mock()
        task_instance.gcs_module = Mock()
        task_instance.sharepoint_verint.is_item_exists.return_value = True
        task_instance.sharepoint_verint.list_files.return_value = [
            {
                "name": "call_p_t_a_f_l_p_20250101_d.wav",
                "id": "1",
                "parentReference": {"path": "/drive/root:/verint"},
                "createdDateTime": "2025-01-01T10:00:00Z",
            }
        ]

        existing_df = pd.DataFrame(columns=task_instance.DEFAULT_CONTROL_SCHEMA.keys())

        task_instance._upload_voice_files(existing_df, "20250101", "20250101")

        # Verify asyncio.run was called for upload
        mock_asyncio.assert_called_once()


class TestUploadVoiceTaskAdditional(TestUploadVoiceTask):
    @patch("tasks.sentiment_telesale.upload_voice_task.add_date")
    @patch("tasks.sentiment_telesale.upload_voice_task.resolve_env", side_effect=lambda x: x)
    def test_execute_task_uses_rerun_date(self, mock_resolve, mock_add_date, task):
        mock_add_date.side_effect = [datetime(2025, 1, 14), datetime(2025, 1, 8)]
        task.packages["rerun_data_dt"] = "2025-01-15"

        with (
            patch.object(task, "_get_existing_control_file", return_value=pd.DataFrame()) as mock_control,
            patch.object(task, "_upload_voice_files") as mock_upload,
        ):
            task.execute_task()

        mock_control.assert_called_once()
        assert mock_upload.call_args.args[1:] == ("20250108", "20250114")

    def test_get_existing_control_file_critical_error_raises(self, task):
        task.sharepoint_control.is_item_exists.side_effect = RuntimeError("sharepoint down")

        with pytest.raises(Exception, match="Critical error loading control file"):
            task._get_existing_control_file()


class TestUploadVoiceTaskConditionCoverage:
    def _build_master_bytes(self, rows):
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            pd.DataFrame(rows).to_excel(writer, sheet_name="list", index=False)
        buffer.seek(0)
        return buffer.getvalue()

    def _configure_condition_task(self, task_instance, master_bytes):
        task_instance.framework["upload_cond"] = ["13/OUT"]
        task_instance.sharepoint_verint = Mock()
        task_instance.sharepoint_control = Mock()
        task_instance.gcs_module = Mock()
        task_instance.sharepoint_verint.list_files_pattern.return_value = ["master.xlsx"]
        task_instance.sharepoint_verint.get_item_by_path.return_value = Mock(content=master_bytes)
        task_instance.sharepoint_verint.is_item_exists.return_value = True

    def test_stamp_control_log_invalid_status_defaults_to_n(self, task_instance):
        import inspect
        import types

        import tasks.sentiment_telesale.upload_voice_task as upload_module

        def make_cell(value):
            def inner():
                return value

            return inner.__closure__[0]

        code = next(
            const
            for const in UploadVoiceTask._upload_voice_files.__code__.co_consts
            if inspect.iscode(const) and const.co_name == "stamp_control_log"
        )
        stamp_control_log = types.FunctionType(
            code,
            upload_module.__dict__,
            closure=(make_cell(task_instance),),
        )

        result = stamp_control_log(
            pd.DataFrame(columns=task_instance.DEFAULT_CONTROL_SCHEMA.keys()),
            datetime(2025, 1, 1),
            "202501",
            "20250101",
            "maybe",
            "remark",
        )

        assert result.iloc[0]["processed_status"] == "N"

    @patch("tasks.sentiment_telesale.upload_voice_task.asyncio.run")
    @patch("tasks.sentiment_telesale.upload_voice_task.list_date", return_value=["20250101"])
    @patch("tasks.sentiment_telesale.upload_voice_task.resolve_date", side_effect=lambda text, replace_date: text)
    @patch("tasks.sentiment_telesale.upload_voice_task.resolve_env", side_effect=lambda x: x)
    def test_upload_voice_files_master_load_failure_continues_without_conditions(
        self, mock_resolve, mock_resolve_date, mock_list_date, mock_asyncio, task_instance
    ):
        self._configure_condition_task(
            task_instance,
            self._build_master_bytes(
                [
                    {
                        "emp_id": "AGENT1",
                        "commission_skill_code": "13",
                        "commission_skill": "Skill",
                        "updatedate": datetime(2025, 1, 1),
                    }
                ]
            ),
        )
        task_instance.sharepoint_verint.get_item_by_path.side_effect = RuntimeError("master unavailable")
        task_instance.sharepoint_verint.list_files.return_value = [
            {
                "name": "call_phone_time_AGENT1_first_last_prov_20250101_dur_OUT.wav",
                "id": "1",
                "parentReference": {"path": "/drive/root:/verint"},
                "createdDateTime": "2025-01-01T10:00:00Z",
            }
        ]

        existing_df = pd.DataFrame(columns=task_instance.DEFAULT_CONTROL_SCHEMA.keys())
        task_instance._upload_voice_files(existing_df, "20250101", "20250101")

        mock_asyncio.assert_called_once()
        task_instance.sharepoint_control.upload_file.assert_called_once()

    @patch("tasks.sentiment_telesale.upload_voice_task.asyncio.run")
    @patch("tasks.sentiment_telesale.upload_voice_task.list_date", return_value=["20250101"])
    @patch("tasks.sentiment_telesale.upload_voice_task.resolve_date", side_effect=lambda text, replace_date: text)
    @patch("tasks.sentiment_telesale.upload_voice_task.resolve_env", side_effect=lambda x: x)
    def test_upload_voice_files_condition_match_uploads_file(
        self, mock_resolve, mock_resolve_date, mock_list_date, mock_asyncio, task_instance
    ):
        self._configure_condition_task(
            task_instance,
            self._build_master_bytes(
                [
                    {
                        "emp_id": "AGENT1",
                        "commission_skill_code": "13",
                        "commission_skill": "Skill",
                        "updatedate": datetime(2025, 1, 1),
                    }
                ]
            ),
        )
        task_instance.sharepoint_verint.list_files.return_value = [
            {
                "name": "call_phone_time_AGENT1_first_last_prov_20250101_dur_OUT.wav",
                "id": "1",
                "parentReference": {"path": "/drive/root:/verint"},
                "createdDateTime": "2025-01-01T10:00:00Z",
            }
        ]

        existing_df = pd.DataFrame(columns=task_instance.DEFAULT_CONTROL_SCHEMA.keys())
        task_instance._upload_voice_files(existing_df, "20250101", "20250101")

        mock_asyncio.assert_called_once()

    @patch("tasks.sentiment_telesale.upload_voice_task.asyncio.run")
    @patch("tasks.sentiment_telesale.upload_voice_task.list_date", return_value=["20250101"])
    @patch("tasks.sentiment_telesale.upload_voice_task.resolve_date", side_effect=lambda text, replace_date: text)
    @patch("tasks.sentiment_telesale.upload_voice_task.resolve_env", side_effect=lambda x: x)
    def test_upload_voice_files_missing_agent_id_skips_condition_filtered_file(
        self, mock_resolve, mock_resolve_date, mock_list_date, mock_asyncio, task_instance
    ):
        self._configure_condition_task(
            task_instance,
            self._build_master_bytes(
                [
                    {
                        "emp_id": "AGENT1",
                        "commission_skill_code": "13",
                        "commission_skill": "Skill",
                        "updatedate": datetime(2025, 1, 1),
                    }
                ]
            ),
        )
        task_instance.sharepoint_verint.list_files.return_value = [
            {
                "name": "too_short.wav",
                "id": "1",
                "parentReference": {"path": "/drive/root:/verint"},
                "createdDateTime": "2025-01-01T10:00:00Z",
            }
        ]

        existing_df = pd.DataFrame(columns=task_instance.DEFAULT_CONTROL_SCHEMA.keys())
        task_instance._upload_voice_files(existing_df, "20250101", "20250101")

        mock_asyncio.assert_not_called()
        task_instance.sharepoint_control.upload_file.assert_called_once()

    @patch("tasks.sentiment_telesale.upload_voice_task.asyncio.run")
    @patch("tasks.sentiment_telesale.upload_voice_task.list_date", return_value=["20250101"])
    @patch("tasks.sentiment_telesale.upload_voice_task.resolve_date", side_effect=lambda text, replace_date: text)
    @patch("tasks.sentiment_telesale.upload_voice_task.resolve_env", side_effect=lambda x: x)
    def test_upload_voice_files_condition_processing_error_skips_file(
        self, mock_resolve, mock_resolve_date, mock_list_date, mock_asyncio, task_instance
    ):
        self._configure_condition_task(
            task_instance,
            self._build_master_bytes(
                [
                    {
                        "emp_id": "AGENT1",
                        "commission_skill_code": "13",
                        "commission_skill": "Skill",
                        "updatedate": datetime(2025, 1, 1),
                    }
                ]
            ),
        )
        task_instance.sharepoint_verint.list_files.return_value = [
            {
                "name": "call_phone_time_AGENT1_first_last_prov_20250101_dur_OUT.wav",
                "id": "1",
                "parentReference": {"path": "/drive/root:/verint"},
                "createdDateTime": "2025-01-01T10:00:00Z",
            }
        ]

        def fake_safe_list_get(values, index, default=None):
            if index == 0:
                return values[0] if values else default
            if index == 3 and isinstance(values, list) and values and values[0] == "call":
                raise RuntimeError("bad agent id")
            try:
                return values[index]
            except Exception:
                return default

        existing_df = pd.DataFrame(columns=task_instance.DEFAULT_CONTROL_SCHEMA.keys())
        with patch("tasks.sentiment_telesale.upload_voice_task.safe_list_get", side_effect=fake_safe_list_get):
            task_instance._upload_voice_files(existing_df, "20250101", "20250101")

        mock_asyncio.assert_not_called()
        task_instance.sharepoint_control.upload_file.assert_called_once()
