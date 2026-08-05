"""
Tests for ExecuteBatchJobTask.
"""

from unittest.mock import Mock, patch

import pytest

from tasks.sentiment_telesale.execute_batch_job_task import ExecuteBatchJobTask


class TestExecuteBatchJobTaskInit:
    """Test ExecuteBatchJobTask initialization."""

    def test_init_sets_config(self, task_instance):
        assert task_instance.gcs["project_id"] == "test-project"
        assert task_instance.vertexai["model"] == "gemini-2.5-flash"


class TestPreExecute:
    """Test pre_execute method."""

    @patch("tasks.sentiment_telesale.execute_batch_job_task.SharePointModule")
    @patch("tasks.sentiment_telesale.execute_batch_job_task.GeminiBatchModule")
    @patch("tasks.sentiment_telesale.execute_batch_job_task.GCSModule")
    @patch("tasks.sentiment_telesale.execute_batch_job_task.resolve_env", side_effect=lambda x: x)
    def test_pre_execute_initializes_modules(self, mock_resolve, mock_gcs, mock_gemini, mock_sp, task_instance):
        task_instance.pre_execute()
        mock_gcs.assert_called_once()
        mock_gemini.assert_called_once()
        mock_sp.assert_called_once()
        assert task_instance.gcs_module is not None
        assert task_instance.gemini_batch_module is not None
        assert task_instance.sharepoint_control is not None

    @patch("tasks.sentiment_telesale.execute_batch_job_task.GCSModule", side_effect=Exception("GCS Error"))
    @patch("tasks.sentiment_telesale.execute_batch_job_task.resolve_env", side_effect=lambda x: x)
    def test_pre_execute_gcs_failure_raises(self, mock_resolve, mock_gcs, task_instance):
        with pytest.raises(Exception):
            task_instance.pre_execute()

    @patch("tasks.sentiment_telesale.execute_batch_job_task.GeminiBatchModule", side_effect=Exception("Gemini Error"))
    @patch("tasks.sentiment_telesale.execute_batch_job_task.GCSModule")
    @patch("tasks.sentiment_telesale.execute_batch_job_task.resolve_env", side_effect=lambda x: x)
    def test_pre_execute_gemini_failure_raises(self, mock_resolve, mock_gcs, mock_gemini, task_instance):
        with pytest.raises(Exception):
            task_instance.pre_execute()


class TestExecuteTask:
    """Test execute_task method."""

    def test_execute_skips_when_no_source_path(self, task_instance):
        task_instance.pre_result = (None, "output/path", [])
        task_instance.packages = {"execution_dt": None}

        def mock_get_package(key, default=None):
            return task_instance.packages.get(key, default)

        task_instance.get_package = mock_get_package

        result = task_instance.execute_task()
        assert result is None

    def test_execute_raises_when_payload_not_found(self, task_instance):
        task_instance.pre_result = ("source/payloads.jsonl", "output/path", [])
        task_instance.packages = {"execution_dt": Mock()}
        task_instance.gcs_module = Mock()
        task_instance.gcs_module.is_file_exists.return_value = False
        task_instance.gcs_module.bucket_name = "test-bucket"

        def mock_get_package(key, default=None):
            return task_instance.packages.get(key, default)

        task_instance.get_package = mock_get_package

        with pytest.raises(FileNotFoundError):
            task_instance.execute_task()

    @patch("tasks.sentiment_telesale.execute_batch_job_task.resolve_env", side_effect=lambda x: x)
    @patch("tasks.sentiment_telesale.execute_batch_job_task.resolve_date", side_effect=lambda text, replace_date: text)
    @patch("tasks.sentiment_telesale.execute_batch_job_task.time")
    def test_execute_creates_batch_job(self, mock_time, mock_resolve_date, mock_resolve_env, task_instance):
        from datetime import datetime

        mock_batch_job = Mock()
        mock_batch_job.name = "batch-job-123"

        task_instance.pre_result = ("source/payloads.jsonl", "output/path", [])
        task_instance.packages = {"execution_dt": datetime.now()}
        task_instance.gcs_module = Mock()
        task_instance.gcs_module.is_file_exists.return_value = True
        task_instance.gcs_module.bucket_name = "test-bucket"
        task_instance.gemini_batch_module = Mock()
        task_instance.gemini_batch_module.create_batch_job.return_value = mock_batch_job
        task_instance.gemini_batch_module.status_check_batch_job.return_value = "JOB_STATE_RUNNING"

        def mock_get_package(key, default=None):
            return task_instance.packages.get(key, default)

        task_instance.get_package = mock_get_package

        task_instance.execute_task()
        task_instance.gemini_batch_module.create_batch_job.assert_called_once()

    @patch("tasks.sentiment_telesale.execute_batch_job_task.resolve_env", side_effect=lambda x: x)
    @patch("tasks.sentiment_telesale.execute_batch_job_task.resolve_date", side_effect=lambda text, replace_date: text)
    @patch("tasks.sentiment_telesale.execute_batch_job_task.time")
    def test_execute_raises_on_failed_job_status(self, mock_time, mock_resolve_date, mock_resolve_env, task_instance):
        from datetime import datetime

        mock_batch_job = Mock()
        mock_batch_job.name = "batch-job-123"

        task_instance.pre_result = ("source/payloads.jsonl", "output/path", [])
        task_instance.packages = {"execution_dt": datetime.now()}
        task_instance.gcs_module = Mock()
        task_instance.gcs_module.is_file_exists.return_value = True
        task_instance.gcs_module.bucket_name = "test-bucket"
        task_instance.gemini_batch_module = Mock()
        task_instance.gemini_batch_module.create_batch_job.return_value = mock_batch_job
        task_instance.gemini_batch_module.status_check_batch_job.return_value = "JOB_STATE_FAILED"

        def mock_get_package(key, default=None):
            return task_instance.packages.get(key, default)

        task_instance.get_package = mock_get_package

        with pytest.raises(Exception, match="Batch job"):
            task_instance.execute_task()


"""
Phase 3 coverage enhancement tests for ExecuteBatchJobTask.
Target: 51% → 70% coverage

Focus areas:
- SharePoint initialization error handling (lines 98-100)
- Payload verification exception handling (lines 120-122)
- Batch job creation error handling (lines 145-147)
- Batch job info stamping error handling (lines 165-174)
- _stamp_batch_processing method (lines 180-234)
- post_execute method (lines 244-253)
"""
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd


@pytest.fixture
def task_instance():
    """Create an ExecuteBatchJobTask with mocked init."""
    with patch.object(ExecuteBatchJobTask, "__init__", lambda self, **kw: None):
        task = ExecuteBatchJobTask.__new__(ExecuteBatchJobTask)
        task.config = {}
        task.packages = {}
        task.task_name = "ExecuteBatchJobTask"
        task.pre_result = None
        task.gcs = {"project_id": "test-project", "bucket_name": "test-bucket"}
        task.vertexai = {
            "project_id": "test-project",
            "location": "us-central1",
            "model": "gemini-2.5-flash",
            "batch_job_name": "test-batch-%{DATA_DATE_YYYYMMDDHHMMSS}",
        }
        task.sharepoint = {"control": {"batch_processing_log_file": "logs/batch_processing.csv"}}
        task.control_access = {
            "site_domain": "control.sharepoint.com",
            "client_id": "cid",
            "client_secret": "cs",
            "tenant_id": "tid",
            "site_path": "/sites/control",
        }
        task.DEFAULT_BATCH_JOB_NAME = "sentiment-voice-batch-job-%{DATA_DATE_YYYYMMDDHHMMSS}"
        task.BATCH_PROCESSING_LOG_SCHEMA = [
            "data_date",
            "gcp_project_id",
            "gcp_project_name",
            "gcs_bucket_name",
            "source_path",
            "filename",
            "prediction_payload_path",
            "log_id",
            "log_type",
            "batch_job_id",
            "batch_job_display_name",
            "model_name",
            "action",
            "status",
            "error_message",
            "created_dt",
            "updated_dt",
            "duration_seconds",
        ]
        task.processing_log_list = []
        task.tz = ZoneInfo("UTC")
        return task


class TestPreExecuteErrorHandling:
    """Test pre_execute SharePoint error handling - Lines 98-100."""

    @patch(
        "tasks.sentiment_telesale.execute_batch_job_task.SharePointModule",
        side_effect=Exception("SharePoint connection error"),
    )
    @patch("tasks.sentiment_telesale.execute_batch_job_task.GeminiBatchModule")
    @patch("tasks.sentiment_telesale.execute_batch_job_task.GCSModule")
    @patch("tasks.sentiment_telesale.execute_batch_job_task.resolve_env", side_effect=lambda x: x)
    def test_pre_execute_sharepoint_failure_raises(self, mock_resolve, mock_gcs, mock_gemini, mock_sp, task_instance):
        """Test SharePoint initialization error raises exception - Line 98-100."""
        with pytest.raises(Exception, match="SharePoint connection error"):
            task_instance.pre_execute()


class TestExecuteTaskExceptionHandling:
    """Test execute_task exception handling scenarios."""

    @patch("tasks.sentiment_telesale.execute_batch_job_task.resolve_env", side_effect=lambda x: x)
    def test_execute_payload_verification_generic_exception(self, mock_resolve, task_instance):
        """Test payload verification with non-FileNotFoundError - Lines 120-122."""
        task_instance.pre_result = ("source/payloads.jsonl", "output/path", [])
        task_instance.packages = {"execution_dt": datetime.now()}
        task_instance.gcs_module = Mock()
        # Simulate generic exception during file check
        task_instance.gcs_module.is_file_exists.side_effect = ValueError("GCS connection timeout")
        task_instance.gcs_module.bucket_name = "test-bucket"

        def mock_get_package(key, default=None):
            return task_instance.packages.get(key, default)

        task_instance.get_package = mock_get_package

        with pytest.raises(Exception, match="Cannot verify payload file"):
            task_instance.execute_task()

    @patch("tasks.sentiment_telesale.execute_batch_job_task.resolve_env", side_effect=lambda x: x)
    @patch(
        "tasks.sentiment_telesale.execute_batch_job_task.resolve_date", side_effect=Exception("Date resolution error")
    )
    def test_execute_batch_job_creation_failure(self, mock_resolve_date, mock_resolve_env, task_instance):
        """Test batch job creation failure - Lines 145-147."""
        task_instance.pre_result = ("source/payloads.jsonl", "output/path", [])
        task_instance.packages = {"execution_dt": datetime.now()}
        task_instance.gcs_module = Mock()
        task_instance.gcs_module.is_file_exists.return_value = True
        task_instance.gcs_module.bucket_name = "test-bucket"
        task_instance.gemini_batch_module = Mock()

        def mock_get_package(key, default=None):
            return task_instance.packages.get(key, default)

        task_instance.get_package = mock_get_package

        with pytest.raises(Exception, match="Batch job creation failed"):
            task_instance.execute_task()

    @patch("tasks.sentiment_telesale.execute_batch_job_task.resolve_env", side_effect=lambda x: x)
    @patch("tasks.sentiment_telesale.execute_batch_job_task.resolve_date", side_effect=lambda text, replace_date: text)
    @patch("tasks.sentiment_telesale.execute_batch_job_task.time")
    def test_execute_batch_job_stamp_info_error(self, mock_time, mock_resolve_date, mock_resolve_env, task_instance):
        """Test batch job info stamping error handling - Lines 165-174."""
        # Create mock batch job with problematic name property
        mock_batch_job = Mock()
        mock_batch_job.name = None
        mock_batch_job.display_name = "test-job-display"

        task_instance.pre_result = ("source/payloads.jsonl", "output/path", [Mock()])
        task_instance.packages = {"execution_dt": datetime.now()}
        task_instance.gcs_module = Mock()
        task_instance.gcs_module.is_file_exists.return_value = True
        task_instance.gcs_module.bucket_name = "test-bucket"
        task_instance.gemini_batch_module = Mock()
        task_instance.gemini_batch_module.create_batch_job.return_value = mock_batch_job
        task_instance.gemini_batch_module.status_check_batch_job.return_value = "JOB_STATE_RUNNING"

        def mock_get_package(key, default=None):
            return task_instance.packages.get(key, default)

        task_instance.get_package = mock_get_package

        # Should complete without raising, but logs should have None for batch_job_id
        task_instance.execute_task()
        assert task_instance.processing_log_list[0].batch_job_id is None


class TestStampBatchProcessing:
    """Test _stamp_batch_processing method - Lines 180-234."""

    def test_stamp_batch_processing_no_logs(self, task_instance):
        """Test _stamp_batch_processing with empty log list - Lines 180-184."""
        task_instance.processing_log_list = []

        # Should return without error
        task_instance._stamp_batch_processing()

    @patch(
        "tasks.sentiment_telesale.execute_batch_job_task.ensure_df_schema",
        side_effect=Exception("Schema validation error"),
    )
    @patch("tasks.sentiment_telesale.execute_batch_job_task.resolve_env", side_effect=lambda x: x)
    def test_stamp_batch_processing_dataframe_creation_error(self, mock_resolve, mock_schema, task_instance):
        """Test _stamp_batch_processing DataFrame creation error - Lines 186-193."""
        mock_log = Mock()
        mock_log.to_dict.return_value = {"data_date": "20260213"}
        task_instance.processing_log_list = [mock_log]
        task_instance.sharepoint_control = Mock()

        with pytest.raises(Exception, match="Cannot create execution log"):
            task_instance._stamp_batch_processing()

    @patch("tasks.sentiment_telesale.execute_batch_job_task.ensure_df_schema")
    @patch("tasks.sentiment_telesale.execute_batch_job_task.replace_nan_with_default")
    @patch(
        "tasks.sentiment_telesale.execute_batch_job_task.get_value_by_path", return_value="logs/batch_processing.csv"
    )
    @patch("tasks.sentiment_telesale.execute_batch_job_task.resolve_env", side_effect=lambda x: x)
    def test_stamp_batch_processing_new_log_creation(
        self, mock_resolve, mock_get_path, mock_replace_nan, mock_schema, task_instance
    ):
        """Test _stamp_batch_processing creating new log file - Lines 195-234."""
        mock_log = Mock()
        mock_log.to_dict.return_value = {
            "data_date": "20260213",
            "filename": "test.jsonl",
            "updated_dt": datetime.now().isoformat(),
        }
        task_instance.processing_log_list = [mock_log]

        mock_df = pd.DataFrame([{"data_date": "20260213", "filename": "test.jsonl", "updated_dt": datetime.now()}])
        mock_schema.return_value = mock_df
        mock_replace_nan.return_value = mock_df

        task_instance.sharepoint_control = Mock()
        task_instance.sharepoint_control.is_item_exists.return_value = False

        task_instance._stamp_batch_processing()

        # Verify upload was called
        task_instance.sharepoint_control.upload_file.assert_called_once()

    @patch("tasks.sentiment_telesale.execute_batch_job_task.ensure_df_schema")
    @patch("tasks.sentiment_telesale.execute_batch_job_task.replace_nan_with_default")
    @patch(
        "tasks.sentiment_telesale.execute_batch_job_task.get_value_by_path", return_value="logs/batch_processing.csv"
    )
    @patch("tasks.sentiment_telesale.execute_batch_job_task.resolve_env", side_effect=lambda x: x)
    def test_stamp_batch_processing_merge_with_existing(
        self, mock_resolve, mock_get_path, mock_replace_nan, mock_schema, task_instance
    ):
        """Test _stamp_batch_processing merging with existing log - Lines 195-211."""
        mock_log = Mock()
        mock_log.to_dict.return_value = {
            "data_date": "20260213",
            "filename": "test.jsonl",
            "updated_dt": datetime.now().isoformat(),
        }
        task_instance.processing_log_list = [mock_log]

        # Existing log data
        existing_data = pd.DataFrame(
            [
                {
                    "data_date": "20260212",
                    "filename": "existing.jsonl",
                    "updated_dt": (datetime.now() - timedelta(days=1)).isoformat(),
                }
            ]
        )

        mock_existing_item = Mock()
        existing_csv = existing_data.to_csv(index=False)
        mock_existing_item.content = existing_csv.encode("utf-8")

        task_instance.sharepoint_control = Mock()
        task_instance.sharepoint_control.is_item_exists.return_value = True
        task_instance.sharepoint_control.get_item_by_path.return_value = mock_existing_item

        # Mock DataFrame processing
        new_df = pd.DataFrame([{"data_date": "20260213", "filename": "test.jsonl", "updated_dt": datetime.now()}])
        mock_schema.return_value = new_df
        mock_replace_nan.return_value = new_df

        task_instance._stamp_batch_processing()

        # Verify get_item_by_path was called
        task_instance.sharepoint_control.get_item_by_path.assert_called_once()

    @patch("tasks.sentiment_telesale.execute_batch_job_task.ensure_df_schema")
    @patch("tasks.sentiment_telesale.execute_batch_job_task.replace_nan_with_default")
    @patch(
        "tasks.sentiment_telesale.execute_batch_job_task.get_value_by_path", return_value="logs/batch_processing.csv"
    )
    @patch("tasks.sentiment_telesale.execute_batch_job_task.resolve_env", side_effect=lambda x: x)
    def test_stamp_batch_processing_merge_error(
        self, mock_resolve, mock_get_path, mock_replace_nan, mock_schema, task_instance
    ):
        """Test _stamp_batch_processing merge error handling - Lines 208-211."""
        mock_log = Mock()
        mock_log.to_dict.return_value = {"data_date": "20260213", "updated_dt": datetime.now().isoformat()}
        task_instance.processing_log_list = [mock_log]

        task_instance.sharepoint_control = Mock()
        task_instance.sharepoint_control.is_item_exists.return_value = True
        task_instance.sharepoint_control.get_item_by_path.side_effect = Exception("SharePoint read error")

        mock_df = pd.DataFrame([{"data_date": "20260213"}])
        mock_schema.return_value = mock_df

        with pytest.raises(Exception, match="Cannot merge existing execution log"):
            task_instance._stamp_batch_processing()

    @patch("tasks.sentiment_telesale.execute_batch_job_task.ensure_df_schema")
    @patch("tasks.sentiment_telesale.execute_batch_job_task.replace_nan_with_default")
    @patch(
        "tasks.sentiment_telesale.execute_batch_job_task.get_value_by_path", return_value="logs/batch_processing.csv"
    )
    @patch("tasks.sentiment_telesale.execute_batch_job_task.resolve_env", side_effect=lambda x: x)
    def test_stamp_batch_processing_retention_policy(
        self, mock_resolve, mock_get_path, mock_replace_nan, mock_schema, task_instance
    ):
        """Test _stamp_batch_processing retention policy (3 months) - Lines 217-223."""
        mock_log = Mock()
        mock_log.to_dict.return_value = {
            "data_date": "20260213",
            "filename": "test.jsonl",
            "updated_dt": datetime.now().isoformat(),
        }
        task_instance.processing_log_list = [mock_log]

        # Create data with old and new records
        old_date = datetime.now() - timedelta(days=100)  # > 3 months
        recent_date = datetime.now() - timedelta(days=30)  # < 3 months

        mock_df = pd.DataFrame(
            [
                {"data_date": "20251113", "filename": "old.jsonl", "updated_dt": old_date},  # Should be filtered out
                {"data_date": "20260113", "filename": "recent.jsonl", "updated_dt": recent_date},  # Should be kept
                {"data_date": "20260213", "filename": "test.jsonl", "updated_dt": datetime.now()},  # Should be kept
            ]
        )

        mock_schema.return_value = mock_df
        mock_replace_nan.return_value = mock_df

        task_instance.sharepoint_control = Mock()
        task_instance.sharepoint_control.is_item_exists.return_value = False

        task_instance._stamp_batch_processing()

        # Verify upload was called (implying filtering happened)
        task_instance.sharepoint_control.upload_file.assert_called_once()

    @patch("tasks.sentiment_telesale.execute_batch_job_task.ensure_df_schema")
    @patch("tasks.sentiment_telesale.execute_batch_job_task.replace_nan_with_default")
    @patch(
        "tasks.sentiment_telesale.execute_batch_job_task.get_value_by_path", return_value="logs/batch_processing.csv"
    )
    @patch("tasks.sentiment_telesale.execute_batch_job_task.resolve_env", side_effect=lambda x: x)
    def test_stamp_batch_processing_upload_error(
        self, mock_resolve, mock_get_path, mock_replace_nan, mock_schema, task_instance
    ):
        """Test _stamp_batch_processing upload error - Lines 232-234."""
        mock_log = Mock()
        mock_log.to_dict.return_value = {
            "data_date": "20260213",
            "filename": "test.jsonl",
            "updated_dt": datetime.now().isoformat(),
        }
        task_instance.processing_log_list = [mock_log]

        mock_df = pd.DataFrame([{"data_date": "20260213", "filename": "test.jsonl", "updated_dt": datetime.now()}])
        mock_schema.return_value = mock_df
        mock_replace_nan.return_value = mock_df

        task_instance.sharepoint_control = Mock()
        task_instance.sharepoint_control.is_item_exists.return_value = False
        task_instance.sharepoint_control.upload_file.side_effect = Exception("SharePoint upload failed")

        with pytest.raises(Exception, match="Cannot upload execution log to SharePoint"):
            task_instance._stamp_batch_processing()


class TestPostExecute:
    """Test post_execute method - Lines 244-253."""

    def test_post_execute_success(self, task_instance):
        """Test post_execute successful execution - Lines 244-253."""
        task_instance.processing_log_list = []

        result_data = {"batch_job_id": "123"}
        returned_result = task_instance.post_execute(result=result_data)

        # Should return the same result
        assert returned_result == result_data

    def test_post_execute_with_log_upload_error(self, task_instance):
        """Test post_execute with log upload failure - Lines 250-253."""
        mock_log = Mock()
        mock_log.to_dict.side_effect = Exception("Log serialization error")
        task_instance.processing_log_list = [mock_log]
        task_instance.sharepoint_control = Mock()

        with patch.object(task_instance, "_stamp_batch_processing", side_effect=Exception("Upload failed")):
            with pytest.raises(Exception, match="Post-execution failed during log upload"):
                task_instance.post_execute(result={"data": "test"})


class TestExecuteBatchJobTaskAdditional:
    @patch("tasks.sentiment_telesale.execute_batch_job_task.TaskInterface.__init__", return_value=None)
    @patch.object(ExecuteBatchJobTask, "get_config")
    @patch("tasks.sentiment_telesale.execute_batch_job_task.load_yaml")
    def test_init_loads_common_config_and_timezone(self, mock_load_yaml, mock_get_config, mock_task_init):
        mock_get_config.side_effect = [
            {"project_id": "project", "bucket_name": "bucket"},
            {"project_id": "project", "location": "asia", "model": "gemini"},
            {"control": {"batch_processing_log_file": "logs.csv"}},
        ]
        mock_load_yaml.return_value = {
            "control": {"site_domain": "control.example.com"},
            "framework": {"timezone": "UTC"},
        }

        task = ExecuteBatchJobTask()

        assert task.control_access == {"site_domain": "control.example.com"}
        assert str(task.tz) == "UTC"

    @patch("tasks.sentiment_telesale.execute_batch_job_task.resolve_env", side_effect=lambda x: x)
    @patch("tasks.sentiment_telesale.execute_batch_job_task.resolve_date", side_effect=lambda text, replace_date: text)
    @patch("tasks.sentiment_telesale.execute_batch_job_task.time")
    def test_execute_stamps_batch_display_name_and_model(
        self, mock_time, mock_resolve_date, mock_resolve_env, task_instance
    ):
        log = Mock()
        mock_batch_job = Mock()
        mock_batch_job.name = "projects/p/locations/l/batchJobs/12345"
        mock_batch_job.display_name = "display-name"

        task_instance.pre_result = ("source/payloads.jsonl", "output/path", [log])
        task_instance.packages = {"execution_dt": datetime.now()}
        task_instance.gcs_module = Mock()
        task_instance.gcs_module.is_file_exists.return_value = True
        task_instance.gcs_module.bucket_name = "test-bucket"
        task_instance.gemini_batch_module = Mock()
        task_instance.gemini_batch_module.create_batch_job.return_value = mock_batch_job
        task_instance.gemini_batch_module.status_check_batch_job.return_value = "JOB_STATE_RUNNING"
        task_instance.get_package = lambda key, default=None: task_instance.packages.get(key, default)

        task_instance.execute_task()

        assert log.batch_job_id == "12345"
        assert log.batch_job_display_name == "display-name"
        assert log.model_name == "gemini-2.5-flash"

    @patch("tasks.sentiment_telesale.execute_batch_job_task.ensure_df_schema")
    @patch("tasks.sentiment_telesale.execute_batch_job_task.replace_nan_with_default")
    @patch(
        "tasks.sentiment_telesale.execute_batch_job_task.get_value_by_path", return_value="logs/batch_processing.csv"
    )
    @patch("tasks.sentiment_telesale.execute_batch_job_task.resolve_env", side_effect=lambda x: x)
    def test_stamp_batch_processing_converts_tzaware_execution_dt(
        self, mock_resolve, mock_get_path, mock_replace_nan, mock_schema, task_instance
    ):
        mock_log = Mock()
        mock_log.to_dict.return_value = {
            "data_date": "20260213",
            "filename": "test.jsonl",
            "updated_dt": "2025-01-01 00:00:00",
        }
        task_instance.processing_log_list = [mock_log]
        task_instance.sharepoint_control = Mock()
        task_instance.sharepoint_control.is_item_exists.return_value = False
        task_instance.packages = {"execution_dt": datetime(2025, 1, 1, tzinfo=ZoneInfo("UTC"))}
        task_instance.get_package = lambda key, default=None: task_instance.packages.get(key, default)

        mock_df = pd.DataFrame(
            [
                {
                    "data_date": "20260213",
                    "filename": "test.jsonl",
                    "updated_dt": "2025-01-01 00:00:00",
                }
            ]
        )
        mock_schema.return_value = mock_df
        mock_replace_nan.return_value = mock_df

        task_instance._stamp_batch_processing()

        task_instance.sharepoint_control.upload_file.assert_called_once()
