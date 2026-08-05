from datetime import datetime
from unittest.mock import Mock, patch

import pytest

from tasks.sentiment_qa.get_batch_result_task import GetBatchResultTask

EXECUTION_DT = datetime(2025, 1, 3, 9, 15, 0)
COMMON_CONFIG = {
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


@pytest.fixture(autouse=True)
def patch_common():
    with (
        patch("tasks.sentiment_qa.get_batch_result_task.load_yaml", return_value=COMMON_CONFIG),
        patch(
            "tasks.sentiment_qa.get_batch_result_task.resolve_env",
            side_effect=lambda value: value,
        ),
        patch(
            "tasks.sentiment_qa.get_batch_result_task.resolve_date",
            side_effect=_fake_resolve_date,
        ),
    ):
        yield


@pytest.fixture
def task():
    return GetBatchResultTask(
        task_param={
            "gcs": {
                "project_id": "qa-project",
                "bucket_name": "qa-bucket",
                "output_folder": "output/%{DATA_DATE_YYYYMMDD}",
            },
            "sharepoint": {},
            "framework": {"lookback_days": "0"},
        },
        packages={"execution_dt": EXECUTION_DT},
    )


@pytest.fixture
def fixed_now():
    return datetime(2025, 1, 3, 10, 0, 0)


class TestInitAndPreExecute:
    def test_init_reads_common_config(self, task):
        assert task.project_id == "qa-project"
        assert task.msgraph_receiver_email == "receiver@example.com"
        assert task.verint_access["site_domain"] == "verint.example.com"

    def test_pre_execute_initializes_modules(self, task):
        with (
            patch("tasks.sentiment_qa.get_batch_result_task.SharePointModule") as sharepoint_cls,
            patch("tasks.sentiment_qa.get_batch_result_task.GCSModule") as gcs_cls,
            patch("tasks.sentiment_qa.get_batch_result_task.MSGraphModule") as msgraph_cls,
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
                "tasks.sentiment_qa.get_batch_result_task.SharePointModule",
                side_effect=Exception("verint boom"),
            ),
            patch("tasks.sentiment_qa.get_batch_result_task.GCSModule"),
            patch("tasks.sentiment_qa.get_batch_result_task.MSGraphModule"),
            pytest.raises(Exception, match="verint boom"),
        ):
            task.pre_execute()

    def test_pre_execute_raises_for_control_error(self, task):
        with (
            patch(
                "tasks.sentiment_qa.get_batch_result_task.SharePointModule",
                side_effect=[Mock(), Exception("control boom")],
            ),
            patch("tasks.sentiment_qa.get_batch_result_task.GCSModule"),
            patch("tasks.sentiment_qa.get_batch_result_task.MSGraphModule"),
            pytest.raises(Exception, match="control boom"),
        ):
            task.pre_execute()

    def test_pre_execute_raises_for_gcs_error(self, task):
        with (
            patch("tasks.sentiment_qa.get_batch_result_task.SharePointModule"),
            patch(
                "tasks.sentiment_qa.get_batch_result_task.GCSModule",
                side_effect=Exception("gcs boom"),
            ),
            patch("tasks.sentiment_qa.get_batch_result_task.MSGraphModule"),
            pytest.raises(Exception, match="gcs boom"),
        ):
            task.pre_execute()

    def test_pre_execute_raises_for_msgraph_error(self, task):
        with (
            patch("tasks.sentiment_qa.get_batch_result_task.SharePointModule"),
            patch("tasks.sentiment_qa.get_batch_result_task.GCSModule"),
            patch(
                "tasks.sentiment_qa.get_batch_result_task.MSGraphModule",
                side_effect=Exception("msgraph boom"),
            ),
            pytest.raises(Exception, match="msgraph boom"),
        ):
            task.pre_execute()


class TestExecuteTask:
    def test_execute_returns_empty_result_when_no_batches_found(self, task):
        task.gcs_module = Mock()
        task.gcs_module.list_files.return_value = []

        result = task.execute_task()

        assert result == {"list_batchs": [], "batch_results": [], "failed_batches": []}

    def test_execute_uses_rerun_date_and_processes_batches(self, task, fixed_now):
        task.framework["lookback_days"] = "0"
        task.packages["rerun_data_dt"] = "2025-01-02"
        task.gcs_module = Mock()
        task.gcs_module.list_files.return_value = [
            "output/20250102/predictions.jsonl",
            "output/20250102/ignore.txt",
        ]
        raw_jsonl = [
            {
                "fileUri": "gs://qa-bucket/processing/PROMO_END/call123_0812345678_090000_AGENT1_alice_smith_provider_20250102_60_IN.wav",
                "response": {
                    "modelVersion": "gemini-2.5-flash",
                    "createTime": "2025-01-02T08:00:00Z",
                    "usageMetadata": {"promptTokenCount": 10},
                    "candidates": [{"content": {"parts": [{"text": '{"sentiment": "positive"}'}]}}],
                },
                "processed_time": "2025-01-02T08:05:00Z",
            }
        ]

        with (
            patch(
                "tasks.sentiment_qa.get_batch_result_task.GeminiBatchModule.retrieve_batch_results",
                return_value=raw_jsonl,
            ),
            patch(
                "tasks.sentiment_qa.get_batch_result_task.GeminiBatchModule.sum_tokens_usage_for_billing",
                return_value={
                    "token_input": {"text": 10, "audio": 0},
                    "token_output": {"text": 4},
                    "token_cached": 0,
                },
            ),
            patch(
                "tasks.sentiment_qa.get_batch_result_task.get_current_datetime",
                return_value=fixed_now,
            ),
        ):
            result = task.execute_task()

        assert result["failed_batches"] == []
        assert result["list_batchs"] == ["output/20250102/predictions.jsonl"]
        payload = result["batch_results"][0]
        assert payload["file_metadata"]["record_date"] == "20250102"
        assert payload["prediction"]["status"] == "SUCCESS"
        assert payload["prediction"]["raw_prediction"] == {"sentiment": "positive"}
        assert payload["prediction"]["token_input"] == {"text": 10, "audio": 0}
        assert payload["load_dt"] == "2025-01-03 10:00:00"

    def test_execute_wraps_date_range_errors(self, task):
        with (
            patch(
                "tasks.sentiment_qa.get_batch_result_task.add_date",
                side_effect=ValueError("bad date"),
            ),
            pytest.raises(Exception, match="Cannot determine processing date range: bad date"),
        ):
            task.execute_task()

    def test_execute_wraps_date_list_errors(self, task):
        with (
            patch(
                "tasks.sentiment_qa.get_batch_result_task.list_date",
                side_effect=ValueError("bad list"),
            ),
            pytest.raises(Exception, match="Critical error: Cannot generate date list: bad list"),
        ):
            task.execute_task()

    def test_execute_continues_after_listing_error(self, task):
        task.framework["lookback_days"] = "1"
        task.gcs_module = Mock()
        task.gcs_module.list_files.side_effect = [
            RuntimeError("list boom"),
            ["output/20250103/predictions.jsonl"],
        ]

        with patch(
            "tasks.sentiment_qa.get_batch_result_task.GeminiBatchModule.retrieve_batch_results",
            return_value=[],
        ):
            result = task.execute_task()

        assert result["list_batchs"] == ["output/20250103/predictions.jsonl"]
        assert result["failed_batches"] == []

    def test_execute_marks_retrieval_failures(self, task):
        task.gcs_module = Mock()
        task.gcs_module.list_files.return_value = ["output/20250103/predictions.jsonl"]

        with patch(
            "tasks.sentiment_qa.get_batch_result_task.GeminiBatchModule.retrieve_batch_results",
            side_effect=RuntimeError("retrieve boom"),
        ):
            result = task.execute_task()

        assert result["failed_batches"] == ["output/20250103/predictions.jsonl"]
        assert result["batch_results"] == []

    def test_execute_marks_processing_failures(self, task):
        task.gcs_module = Mock()
        task.gcs_module.list_files.return_value = ["output/20250103/predictions.jsonl"]

        with (
            patch(
                "tasks.sentiment_qa.get_batch_result_task.GeminiBatchModule.retrieve_batch_results",
                return_value=[{"fileUri": "gs://qa-bucket/test.wav"}],
            ),
            patch.object(task, "_proc_raw_prediction", side_effect=RuntimeError("process boom")),
        ):
            result = task.execute_task()

        assert result["failed_batches"] == ["output/20250103/predictions.jsonl"]


class TestPreparePredictionPayload:
    def test_prepare_prediction_payload_success(self, task):
        payload = task._prepare_prediction_payload({"file_name": "sample"}, '{"sentiment": "positive"}')

        assert payload["prediction"]["status"] == "SUCCESS"
        assert payload["prediction"]["raw_prediction"] == {"sentiment": "positive"}

    def test_prepare_prediction_payload_error_flag(self, task):
        payload = task._prepare_prediction_payload({"file_name": "sample"}, "permission denied", err_flag=True)

        assert payload["prediction"]["status"] == "FAILED"
        assert payload["prediction"]["message"] == "permission denied"

    def test_prepare_prediction_payload_invalid_json(self, task):
        payload = task._prepare_prediction_payload({"file_name": "sample"}, "not-json")

        assert payload["prediction"]["status"] == "FAILED"
        assert "Failed to parse prediction JSON" in payload["prediction"]["message"]


class TestProcessRawPrediction:
    def test_proc_raw_prediction_skips_lines_without_file_uri(self, task):
        task.batch_results = []

        task._proc_raw_prediction("batch-1", [{"response": {"candidates": []}}])

        assert task.batch_results == []

    def test_proc_raw_prediction_handles_error_status_and_default_model_version(self, task, fixed_now):
        task.batch_results = []
        raw_jsonl = [
            {
                "fileUri": "gs://qa-bucket/processing/PROMO_END/call123_0812345678_090000_AGENT1_alice_smith_provider_20250103_60_IN.wav",
                "status": "PERMISSION_DENIED",
                "response": {"createTime": "2025-01-03T08:00:00Z", "usageMetadata": {}},
                "processed_time": "2025-01-03T08:05:00Z",
            }
        ]

        with (
            patch(
                "tasks.sentiment_qa.get_batch_result_task.GeminiBatchModule.sum_tokens_usage_for_billing",
                side_effect=RuntimeError("usage boom"),
            ),
            patch(
                "tasks.sentiment_qa.get_batch_result_task.get_current_datetime",
                return_value=fixed_now,
            ),
        ):
            task._proc_raw_prediction("batch-1", raw_jsonl)

        payload = task.batch_results[0]
        assert payload["prediction"]["status"] == "FAILED"
        assert payload["prediction"]["message"] == "PERMISSION_DENIED"
        assert payload["prediction"]["model_version"] == task.DEFAULT_MODEL_VERSION
        assert payload["load_dt"] == "2025-01-03 10:00:00"

    def test_proc_raw_prediction_handles_missing_prediction_text(self, task, fixed_now):
        task.batch_results = []
        raw_jsonl = [
            {
                "fileUri": "gs://qa-bucket/processing/PROMO_END/call123_0812345678_090000_AGENT1_alice_smith_provider_20250103_60_IN.wav",
                "response": {
                    "modelVersion": "gemini-2.5-flash",
                    "createTime": "2025-01-03T08:00:00Z",
                    "usageMetadata": {},
                    "candidates": [{"content": {"parts": [{}]}}],
                },
                "processed_time": "2025-01-03T08:05:00Z",
            }
        ]

        with (
            patch(
                "tasks.sentiment_qa.get_batch_result_task.GeminiBatchModule.sum_tokens_usage_for_billing",
                return_value={
                    "token_input": {"text": 2, "audio": 0},
                    "token_output": {"text": 0},
                    "token_cached": 0,
                },
            ),
            patch(
                "tasks.sentiment_qa.get_batch_result_task.get_current_datetime",
                return_value=fixed_now,
            ),
        ):
            task._proc_raw_prediction("batch-1", raw_jsonl)

        payload = task.batch_results[0]
        assert payload["prediction"]["status"] == "FAILED"
        assert "No prediction found" in payload["prediction"]["message"]
        assert payload["prediction"]["token_output"] == {"text": 0}

    def test_proc_raw_prediction_defaults_usage_when_summary_fails(self, task, fixed_now):
        task.batch_results = []
        raw_jsonl = [
            {
                "fileUri": "gs://qa-bucket/processing/PROMO_END/short_name.wav",
                "response": {
                    "modelVersion": "gemini-2.5-flash",
                    "createTime": "2025-01-03T08:00:00Z",
                    "usageMetadata": {},
                    "candidates": [{"content": {"parts": [{"text": '{"sentiment": "neutral"}'}]}}],
                },
                "processed_time": "2025-01-03T08:05:00Z",
            }
        ]

        with (
            patch(
                "tasks.sentiment_qa.get_batch_result_task.GeminiBatchModule.sum_tokens_usage_for_billing",
                side_effect=RuntimeError("usage boom"),
            ),
            patch(
                "tasks.sentiment_qa.get_batch_result_task.get_current_datetime",
                return_value=fixed_now,
            ),
        ):
            task._proc_raw_prediction("batch-1", raw_jsonl)

        payload = task.batch_results[0]
        assert payload["file_metadata"]["record_date"] == "99991231"
        assert payload["prediction"]["status"] == "SUCCESS"
        assert payload["prediction"]["token_input"] == {"text": 0, "audio": 0}
        assert payload["prediction"]["token_cached"] == 0

    def test_proc_raw_prediction_extracts_record_date_from_path(self, task, fixed_now):
        task.batch_results = []
        raw_jsonl = [
            {
                "fileUri": (
                    "gs://qa-bucket/processing/PROMO_END/20250104/"
                    "call123_0812345678_090000_AGENT1_alice_smith_provider_bad_60_IN.wav"
                ),
                "response": {
                    "modelVersion": "gemini-2.5-flash",
                    "createTime": "2025-01-03T08:00:00Z",
                    "usageMetadata": {},
                    "candidates": [{"content": {"parts": [{"text": '{"sentiment": "neutral"}'}]}}],
                },
                "processed_time": "2025-01-03T08:05:00Z",
            }
        ]

        with (
            patch(
                "tasks.sentiment_qa.get_batch_result_task.GeminiBatchModule.sum_tokens_usage_for_billing",
                return_value={
                    "token_input": {"text": 2, "audio": 0},
                    "token_output": {"text": 1},
                    "token_cached": 0,
                },
            ),
            patch(
                "tasks.sentiment_qa.get_batch_result_task.get_current_datetime",
                return_value=fixed_now,
            ),
        ):
            task._proc_raw_prediction("batch-1", raw_jsonl)

        assert task.batch_results[0]["file_metadata"]["record_date"] == "20250104"

    def test_proc_raw_prediction_defaults_record_date_when_regex_lookup_fails(self, task, fixed_now):
        task.batch_results = []
        raw_jsonl = [
            {
                "fileUri": (
                    "gs://qa-bucket/processing/PROMO_END/"
                    "call123_0812345678_090000_AGENT1_alice_smith_provider_bad_60_IN.wav"
                ),
                "response": {
                    "modelVersion": "gemini-2.5-flash",
                    "createTime": "2025-01-03T08:00:00Z",
                    "usageMetadata": {},
                    "candidates": [{"content": {"parts": [{"text": '{"sentiment": "neutral"}'}]}}],
                },
                "processed_time": "2025-01-03T08:05:00Z",
            }
        ]

        with (
            patch(
                "tasks.sentiment_qa.get_batch_result_task.re.findall",
                side_effect=RuntimeError("regex boom"),
            ),
            patch(
                "tasks.sentiment_qa.get_batch_result_task.GeminiBatchModule.sum_tokens_usage_for_billing",
                return_value={
                    "token_input": {"text": 2, "audio": 0},
                    "token_output": {"text": 1},
                    "token_cached": 0,
                },
            ),
            patch(
                "tasks.sentiment_qa.get_batch_result_task.get_current_datetime",
                return_value=fixed_now,
            ),
        ):
            task._proc_raw_prediction("batch-1", raw_jsonl)

        assert task.batch_results[0]["file_metadata"]["record_date"] == task.DEFAULT_RECORD_DATE

    def test_proc_raw_prediction_uses_default_voice_info_on_parse_errors(self, task, fixed_now):
        task.batch_results = []
        raw_jsonl = [
            {
                "fileUri": (
                    "gs://qa-bucket/processing/PROMO_END/"
                    "call123_0812345678_090000_AGENT1_alice_smith_provider_20250104_60_IN.wav"
                ),
                "response": {
                    "modelVersion": "gemini-2.5-flash",
                    "createTime": "2025-01-03T08:00:00Z",
                    "usageMetadata": {},
                    "candidates": [{"content": {"parts": [{"text": '{"sentiment": "positive"}'}]}}],
                },
                "processed_time": "2025-01-03T08:05:00Z",
            }
        ]

        with (
            patch(
                "tasks.sentiment_qa.get_batch_result_task.safe_list_get_slicing",
                side_effect=RuntimeError("parse boom"),
            ),
            patch(
                "tasks.sentiment_qa.get_batch_result_task.GeminiBatchModule.sum_tokens_usage_for_billing",
                return_value={
                    "token_input": {"text": 2, "audio": 0},
                    "token_output": {"text": 1},
                    "token_cached": 0,
                },
            ),
            patch(
                "tasks.sentiment_qa.get_batch_result_task.get_current_datetime",
                return_value=fixed_now,
            ),
        ):
            task._proc_raw_prediction("batch-1", raw_jsonl)

        payload = task.batch_results[0]
        assert payload["file_metadata"]["file_name"].startswith("call123")
        assert payload["file_metadata"]["agent_id"] is None
        assert payload["file_metadata"]["record_date"] == "20250104"

    def test_proc_raw_prediction_adds_usage_metadata_for_error_status(self, task, fixed_now):
        task.batch_results = []
        raw_jsonl = [
            {
                "fileUri": (
                    "gs://qa-bucket/processing/PROMO_END/"
                    "call123_0812345678_090000_AGENT1_alice_smith_provider_20250103_60_IN.wav"
                ),
                "status": "PERMISSION_DENIED",
                "response": {
                    "createTime": "2025-01-03T08:00:00Z",
                    "usageMetadata": {"promptTokenCount": 9},
                },
                "processed_time": "2025-01-03T08:05:00Z",
            }
        ]

        with (
            patch(
                "tasks.sentiment_qa.get_batch_result_task.GeminiBatchModule.sum_tokens_usage_for_billing",
                return_value={
                    "token_input": {"text": 9, "audio": 0},
                    "token_output": {"text": 0},
                    "token_cached": 1,
                },
            ),
            patch(
                "tasks.sentiment_qa.get_batch_result_task.get_current_datetime",
                return_value=fixed_now,
            ),
        ):
            task._proc_raw_prediction("batch-1", raw_jsonl)

        payload = task.batch_results[0]
        assert payload["prediction"]["token_input"] == {"text": 9, "audio": 0}
        assert payload["prediction"]["token_cached"] == 1

    def test_proc_raw_prediction_skips_usage_metadata_failures_for_missing_prediction(self, task, fixed_now):
        task.batch_results = []
        raw_jsonl = [
            {
                "fileUri": (
                    "gs://qa-bucket/processing/PROMO_END/"
                    "call123_0812345678_090000_AGENT1_alice_smith_provider_20250103_60_IN.wav"
                ),
                "response": {
                    "modelVersion": "gemini-2.5-flash",
                    "createTime": "2025-01-03T08:00:00Z",
                    "usageMetadata": {"promptTokenCount": 9},
                    "candidates": [{"content": {"parts": [{}]}}],
                },
                "processed_time": "2025-01-03T08:05:00Z",
            }
        ]

        with (
            patch(
                "tasks.sentiment_qa.get_batch_result_task.GeminiBatchModule.sum_tokens_usage_for_billing",
                side_effect=RuntimeError("usage boom"),
            ),
            patch(
                "tasks.sentiment_qa.get_batch_result_task.get_current_datetime",
                return_value=fixed_now,
            ),
        ):
            task._proc_raw_prediction("batch-1", raw_jsonl)

        payload = task.batch_results[0]
        assert payload["prediction"]["status"] == "FAILED"
        assert "token_input" not in payload["prediction"]

    def test_proc_raw_prediction_skips_lines_that_raise_processing_errors(self, task):
        task.batch_results = []
        raw_jsonl = [
            {
                "fileUri": (
                    "gs://qa-bucket/processing/PROMO_END/"
                    "call123_0812345678_090000_AGENT1_alice_smith_provider_20250103_60_IN.wav"
                ),
                "response": {
                    "modelVersion": "gemini-2.5-flash",
                    "createTime": "2025-01-03T08:00:00Z",
                    "usageMetadata": {},
                    "candidates": [{"content": {"parts": [{"text": '{"sentiment": "neutral"}'}]}}],
                },
                "processed_time": "2025-01-03T08:05:00Z",
            }
        ]

        with patch.object(task, "_prepare_prediction_payload", side_effect=RuntimeError("payload boom")):
            task._proc_raw_prediction("batch-9", raw_jsonl)

        assert task.batch_results == []


class TestOnError:
    def test_on_error_sends_email(self, task, fixed_now):
        task.msgraph_module = Mock()

        with patch(
            "tasks.sentiment_qa.get_batch_result_task.get_current_datetime",
            return_value=fixed_now,
        ):
            task.on_error(RuntimeError("boom"))

        kwargs = task.msgraph_module.send_email.call_args.kwargs
        assert kwargs["subject"] == "[AI Failed] [AI-QA]"
        assert "boom" in kwargs["body"]
        assert kwargs["receiver_email"] == "receiver@example.com"

    def test_on_error_swallows_email_failure(self, task, fixed_now):
        task.msgraph_module = Mock()
        task.msgraph_module.send_email.side_effect = RuntimeError("email boom")

        with patch(
            "tasks.sentiment_qa.get_batch_result_task.get_current_datetime",
            return_value=fixed_now,
        ):
            task.on_error(RuntimeError("boom"))
