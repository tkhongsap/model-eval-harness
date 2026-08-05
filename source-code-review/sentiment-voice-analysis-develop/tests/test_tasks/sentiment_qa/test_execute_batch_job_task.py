import io
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd
import pytest

from tasks.sentiment_qa.execute_batch_job_task import ExecuteBatchJobTask

EXECUTION_DT = datetime(2025, 1, 3, 12, 30, 45)
COMMON_CONFIG = {
    "control": {
        "site_domain": "control.example.com",
        "client_id": "control-client",
        "client_secret": "control-secret",
        "tenant_id": "tenant-id",
        "site_path": "/sites/control",
    },
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
        date_ts = replace_date.strftime("%Y%m%d%H%M%S")
    else:
        normalized = str(replace_date).replace("-", "")
        date_ymd = normalized[:8]
        date_ts = normalized if len(normalized) >= 14 else f"{date_ymd}000000"
    return text.replace("%{DATA_DATE_YYYYMMDDHHMMSS}", date_ts).replace("%{DATA_DATE_YYYYMMDD}", date_ymd)


@pytest.fixture(autouse=True)
def patch_common():
    with (
        patch("tasks.sentiment_qa.execute_batch_job_task.load_yaml", return_value=COMMON_CONFIG),
        patch(
            "tasks.sentiment_qa.execute_batch_job_task.resolve_env",
            side_effect=lambda value: value,
        ),
        patch(
            "tasks.sentiment_qa.execute_batch_job_task.resolve_date",
            side_effect=_fake_resolve_date,
        ),
        patch("tasks.sentiment_qa.execute_batch_job_task.time.sleep"),
    ):
        yield


@pytest.fixture
def task():
    return ExecuteBatchJobTask(
        task_param={
            "gcs": {"project_id": "qa-project", "bucket_name": "qa-bucket"},
            "vertexai": {
                "project_id": "vertex-project",
                "location": "asia-southeast1",
                "model": "gemini-2.5-flash",
                "batch_job_name": "qa-batch-%{DATA_DATE_YYYYMMDDHHMMSS}",
            },
            "sharepoint": {"control": {"batch_processing_log_file": "logs/batch_processing.csv"}},
        },
        packages={"execution_dt": EXECUTION_DT},
    )


@pytest.fixture
def fixed_now():
    return datetime(2025, 1, 3, 16, 0, 0)


def _make_log_dict(filename="new.jsonl", updated_dt=None, data_date="20250103"):
    updated_dt = updated_dt or datetime.now().isoformat()
    return {
        "data_date": data_date,
        "gcp_project_id": "qa-project",
        "gcp_project_name": "qa-project",
        "gcs_bucket_name": "qa-bucket",
        "source_path": f"gs://qa-bucket/{filename}",
        "filename": filename,
        "prediction_payload_path": "processing/payloads.jsonl",
        "log_id": "LOG-1",
        "log_type": "Batch Processing Log",
        "batch_job_id": "123",
        "batch_job_display_name": "qa-batch",
        "model_name": "gemini-2.5-flash",
        "action": "BATCH_PROCESSING_LOG_WRITTEN",
        "status": "SUCCESS",
        "error_message": None,
        "created_dt": updated_dt,
        "updated_dt": updated_dt,
        "duration_seconds": 0,
    }


class TestInitAndPreExecute:
    def test_init_reads_common_config(self, task):
        assert task.project_id == "qa-project"
        assert task.msgraph_sender_email == "sender@example.com"
        assert task.control_access["site_domain"] == "control.example.com"

    def test_pre_execute_initializes_modules(self, task):
        with (
            patch("tasks.sentiment_qa.execute_batch_job_task.GCSModule") as gcs_cls,
            patch("tasks.sentiment_qa.execute_batch_job_task.GeminiBatchModule") as gemini_cls,
            patch("tasks.sentiment_qa.execute_batch_job_task.SharePointModule") as sharepoint_cls,
            patch("tasks.sentiment_qa.execute_batch_job_task.MSGraphModule") as msgraph_cls,
        ):
            task.pre_execute()

        gcs_cls.assert_called_once_with(project_id="qa-project", bucket_name="qa-bucket")
        gemini_cls.assert_called_once_with(genai_project_id="vertex-project", genai_location="asia-southeast1")
        sharepoint_cls.assert_called_once_with(
            client_id="control-client",
            client_secret="control-secret",
            tenant_id="tenant-id",
            site_domain="control.example.com",
            site_path="/sites/control",
        )
        msgraph_cls.assert_called_once_with(
            tenant_id="graph-tenant",
            client_id="graph-client",
            client_secret="graph-secret",
        )

    def test_pre_execute_raises_for_gcs_error(self, task):
        with (
            patch(
                "tasks.sentiment_qa.execute_batch_job_task.GCSModule",
                side_effect=Exception("gcs boom"),
            ),
            patch("tasks.sentiment_qa.execute_batch_job_task.GeminiBatchModule"),
            patch("tasks.sentiment_qa.execute_batch_job_task.SharePointModule"),
            patch("tasks.sentiment_qa.execute_batch_job_task.MSGraphModule"),
            pytest.raises(Exception, match="gcs boom"),
        ):
            task.pre_execute()

    def test_pre_execute_raises_for_gemini_error(self, task):
        with (
            patch("tasks.sentiment_qa.execute_batch_job_task.GCSModule"),
            patch(
                "tasks.sentiment_qa.execute_batch_job_task.GeminiBatchModule",
                side_effect=Exception("gemini boom"),
            ),
            patch("tasks.sentiment_qa.execute_batch_job_task.SharePointModule"),
            patch("tasks.sentiment_qa.execute_batch_job_task.MSGraphModule"),
            pytest.raises(Exception, match="gemini boom"),
        ):
            task.pre_execute()

    def test_pre_execute_raises_for_sharepoint_error(self, task):
        with (
            patch("tasks.sentiment_qa.execute_batch_job_task.GCSModule"),
            patch("tasks.sentiment_qa.execute_batch_job_task.GeminiBatchModule"),
            patch(
                "tasks.sentiment_qa.execute_batch_job_task.SharePointModule",
                side_effect=Exception("sharepoint boom"),
            ),
            patch("tasks.sentiment_qa.execute_batch_job_task.MSGraphModule"),
        ):
            with pytest.raises(Exception, match="sharepoint boom"):
                task.pre_execute()

    def test_pre_execute_raises_for_msgraph_error(self, task):
        with (
            patch("tasks.sentiment_qa.execute_batch_job_task.GCSModule"),
            patch("tasks.sentiment_qa.execute_batch_job_task.GeminiBatchModule"),
            patch("tasks.sentiment_qa.execute_batch_job_task.SharePointModule"),
            patch(
                "tasks.sentiment_qa.execute_batch_job_task.MSGraphModule",
                side_effect=Exception("msgraph boom"),
            ),
            pytest.raises(Exception, match="msgraph boom"),
        ):
            task.pre_execute()


class TestExecuteTask:
    def test_execute_skips_when_source_path_missing(self, task):
        task.pre_result = (None, "output/batch", [])

        assert task.execute_task() is None

    def test_execute_raises_for_missing_payload_file(self, task):
        task.pre_result = ("processing/payloads.jsonl", "output/batch", [])
        task.gcs_module = Mock(bucket_name="qa-bucket")
        task.gcs_module.is_file_exists.return_value = False

        with pytest.raises(FileNotFoundError, match="Payload file not found"):
            task.execute_task()

    def test_execute_wraps_generic_payload_verification_error(self, task):
        task.pre_result = ("processing/payloads.jsonl", "output/batch", [])
        task.gcs_module = Mock(bucket_name="qa-bucket")
        task.gcs_module.is_file_exists.side_effect = RuntimeError("gcs timeout")

        with pytest.raises(Exception, match="Cannot verify payload file: gcs timeout"):
            task.execute_task()

    def test_execute_creates_batch_job_and_stamps_logs(self, task, fixed_now):
        processing_log = SimpleNamespace(batch_job_id=None, batch_job_display_name=None, model_name=None)
        task.pre_result = ("processing/payloads.jsonl", "output/batch", [processing_log])
        task.gcs_module = Mock(bucket_name="qa-bucket")
        task.gcs_module.is_file_exists.return_value = True
        task.gemini_batch_module = Mock()
        task.gemini_batch_module.create_batch_job.return_value = SimpleNamespace(
            name="projects/x/locations/y/batchPredictionJobs/12345",
            display_name="qa-batch-20250103123045",
        )
        task.gemini_batch_module.status_check_batch_job.return_value = "JOB_STATE_RUNNING"

        with patch(
            "tasks.sentiment_qa.execute_batch_job_task.get_current_datetime",
            return_value=fixed_now,
        ):
            task.execute_task()

        task.gemini_batch_module.create_batch_job.assert_called_once_with(
            model_nm="gemini-2.5-flash",
            src_uri="gs://qa-bucket/processing/payloads.jsonl",
            config={
                "dest": "gs://qa-bucket/output/batch",
                "display_name": "qa-batch-20250103160000",
            },
        )
        assert processing_log.batch_job_id == "12345"
        assert processing_log.batch_job_display_name == "qa-batch-20250103123045"
        assert processing_log.model_name == "gemini-2.5-flash"

    def test_execute_wraps_batch_creation_error(self, task, fixed_now):
        task.pre_result = ("processing/payloads.jsonl", "output/batch", [])
        task.gcs_module = Mock(bucket_name="qa-bucket")
        task.gcs_module.is_file_exists.return_value = True
        task.gemini_batch_module = Mock()
        task.gemini_batch_module.create_batch_job.side_effect = RuntimeError("create failed")

        with (
            patch(
                "tasks.sentiment_qa.execute_batch_job_task.get_current_datetime",
                return_value=fixed_now,
            ),
            pytest.raises(Exception, match="Batch job creation failed: create failed"),
        ):
            task.execute_task()

    def test_execute_wraps_failed_job_status(self, task, fixed_now):
        task.pre_result = ("processing/payloads.jsonl", "output/batch", [])
        task.gcs_module = Mock(bucket_name="qa-bucket")
        task.gcs_module.is_file_exists.return_value = True
        task.gemini_batch_module = Mock()
        task.gemini_batch_module.create_batch_job.return_value = SimpleNamespace(
            name="projects/x/locations/y/batchPredictionJobs/12345",
            display_name="qa-batch-20250103123045",
        )
        task.gemini_batch_module.status_check_batch_job.return_value = "JOB_STATE_FAILED"

        with (
            patch(
                "tasks.sentiment_qa.execute_batch_job_task.get_current_datetime",
                return_value=fixed_now,
            ),
            pytest.raises(
                Exception,
                match="Batch job status check failed: Batch job failed with status: JOB_STATE_FAILED",
            ),
        ):
            task.execute_task()

    def test_execute_falls_back_when_log_stamping_fails(self, task, fixed_now):
        class FragileLog:
            def __init__(self):
                self._batch_job_id = None
                self.batch_job_display_name = None
                self.model_name = None

            @property
            def batch_job_id(self):
                return self._batch_job_id

            @batch_job_id.setter
            def batch_job_id(self, value):
                if value is not None:
                    raise ValueError("cannot set non-null job id")
                self._batch_job_id = value

        fragile_log = FragileLog()
        task.pre_result = ("processing/payloads.jsonl", "output/batch", [fragile_log])
        task.gcs_module = Mock(bucket_name="qa-bucket")
        task.gcs_module.is_file_exists.return_value = True
        task.gemini_batch_module = Mock()
        task.gemini_batch_module.create_batch_job.return_value = SimpleNamespace(
            name="projects/x/locations/y/batchPredictionJobs/12345",
            display_name="qa-batch-20250103123045",
        )
        task.gemini_batch_module.status_check_batch_job.return_value = "JOB_STATE_RUNNING"

        with patch(
            "tasks.sentiment_qa.execute_batch_job_task.get_current_datetime",
            return_value=fixed_now,
        ):
            task.execute_task()

        assert fragile_log.batch_job_id is None
        assert fragile_log.batch_job_display_name == "qa-batch-20250103160000"
        assert fragile_log.model_name == "gemini-2.5-flash"


class TestStampBatchProcessing:
    def test_stamp_batch_processing_returns_when_no_logs(self, task):
        task.processing_log_list = []

        task._stamp_batch_processing()

    def test_stamp_batch_processing_wraps_dataframe_creation_error(self, task):
        bad_log = Mock()
        bad_log.to_dict.side_effect = RuntimeError("serialize failed")
        task.processing_log_list = [bad_log]

        with pytest.raises(Exception, match="Cannot create execution log: serialize failed"):
            task._stamp_batch_processing()

    def test_stamp_batch_processing_merges_existing_and_filters_old_rows(self, task):
        recent_dt = datetime.now() - timedelta(days=5)
        old_dt = datetime.now() - timedelta(days=120)
        new_log = Mock()
        new_log.to_dict.return_value = _make_log_dict(filename="new.jsonl", updated_dt=recent_dt.isoformat())
        task.processing_log_list = [new_log]

        existing_df = pd.DataFrame(
            [
                _make_log_dict(filename="old.jsonl", updated_dt=old_dt.isoformat(), data_date="20240901"),
                _make_log_dict(
                    filename="recent-existing.jsonl",
                    updated_dt=recent_dt.isoformat(),
                    data_date="20250102",
                ),
            ]
        )
        existing_item = SimpleNamespace(content=existing_df.to_csv(index=False).encode("utf-8"))
        task.sharepoint_control = Mock()
        task.sharepoint_control.is_item_exists.return_value = True
        task.sharepoint_control.get_item_by_path.return_value = existing_item

        task._stamp_batch_processing()

        upload_kwargs = task.sharepoint_control.upload_file.call_args.kwargs
        uploaded_df = pd.read_csv(io.BytesIO(upload_kwargs["content"]))
        assert upload_kwargs["upload_path"] == "logs/batch_processing.csv"
        assert set(uploaded_df["filename"]) == {"new.jsonl", "recent-existing.jsonl"}
        assert "old.jsonl" not in set(uploaded_df["filename"])

    def test_stamp_batch_processing_wraps_merge_error(self, task):
        new_log = Mock()
        new_log.to_dict.return_value = _make_log_dict()
        task.processing_log_list = [new_log]
        task.sharepoint_control = Mock()
        task.sharepoint_control.is_item_exists.return_value = True
        task.sharepoint_control.get_item_by_path.side_effect = RuntimeError("read failed")

        with pytest.raises(Exception, match="Cannot merge existing execution log: read failed"):
            task._stamp_batch_processing()

    def test_stamp_batch_processing_wraps_upload_error(self, task):
        new_log = Mock()
        new_log.to_dict.return_value = _make_log_dict()
        task.processing_log_list = [new_log]
        task.sharepoint_control = Mock()
        task.sharepoint_control.is_item_exists.return_value = False
        task.sharepoint_control.upload_file.side_effect = RuntimeError("upload failed")

        with pytest.raises(Exception, match="Cannot upload execution log to SharePoint: upload failed"):
            task._stamp_batch_processing()


class TestPostExecuteAndOnError:
    def test_post_execute_returns_result(self, task):
        with patch.object(task, "_stamp_batch_processing") as stamp:
            result = task.post_execute({"ok": True})

        stamp.assert_called_once_with()
        assert result == {"ok": True}

    def test_post_execute_wraps_stamp_error(self, task):
        with (
            patch.object(task, "_stamp_batch_processing", side_effect=RuntimeError("boom")),
            pytest.raises(Exception, match="Post-execution failed during log upload: boom"),
        ):
            task.post_execute({"ok": True})

    def test_on_error_sends_email(self, task, fixed_now):
        task.msgraph_module = Mock()

        with patch(
            "tasks.sentiment_qa.execute_batch_job_task.get_current_datetime",
            return_value=fixed_now,
        ):
            task.on_error(RuntimeError("batch failed"))

        kwargs = task.msgraph_module.send_email.call_args.kwargs
        assert kwargs["subject"] == "[AI Failed] [AI-QA]"
        assert "batch failed" in kwargs["body"]
        assert kwargs["sender_email"] == "sender@example.com"

    def test_on_error_swallows_email_failures(self, task, fixed_now):
        task.msgraph_module = Mock()
        task.msgraph_module.send_email.side_effect = RuntimeError("email boom")

        with patch(
            "tasks.sentiment_qa.execute_batch_job_task.get_current_datetime",
            return_value=fixed_now,
        ):
            task.on_error(RuntimeError("batch failed"))
