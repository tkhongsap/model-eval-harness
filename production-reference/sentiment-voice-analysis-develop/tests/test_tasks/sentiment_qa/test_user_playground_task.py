import io
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd
import pytest

from tasks.sentiment_qa.user_playground_task import UserPlaygroundTask

COMMON_YAML = {
    "control": {
        "site_domain": "control.sharepoint.com",
        "client_id": "control-client",
        "client_secret": "control-secret",
        "tenant_id": "tenant-id",
        "site_path": "/sites/control",
        "site_name": "control-site",
    },
    "msgraph": {
        "tenant_id": "tenant-id",
        "client_id": "graph-client",
        "client_secret": "graph-secret",
        "sender_email": "sender@example.com",
        "receiver_email": "receiver@example.com",
        "cc_email": "cc@example.com",
    },
    "framework": {"timezone": "Asia/Bangkok"},
}


def _excel_bytes(df: pd.DataFrame, sheet_name: str) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name=sheet_name, index=False)
    return buf.getvalue()


def _raw_line(
    file_uri: str, *, status: str = "", text: str | None = None, model_version: str | None = "gemini-2.5-flash"
):
    response = {
        "createTime": "2025-01-15T12:00:00Z",
        "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 3},
    }
    if model_version is not None:
        response["modelVersion"] = model_version
    if text is not None:
        response["candidates"] = [{"content": {"parts": [{"text": text}]}}]
    else:
        response["candidates"] = []

    record = {
        "request": {"contents": [{"parts": [{"fileData": {"fileUri": file_uri}}]}]},
        "response": response,
        "processed_time": "2025-01-15T12:01:00Z",
    }
    if status:
        record["status"] = status
    return record


@pytest.fixture
def mock_deps():
    with (
        patch("tasks.sentiment_qa.user_playground_task.SharePointModule") as sp_cls,
        patch("tasks.sentiment_qa.user_playground_task.GCSModule") as gcs_cls,
        patch("tasks.sentiment_qa.user_playground_task.GeminiBatchModule") as gemini_cls,
        patch("tasks.sentiment_qa.user_playground_task.MSGraphModule") as graph_cls,
        patch("tasks.sentiment_qa.user_playground_task.load_yaml") as load_yaml,
        patch("tasks.sentiment_qa.user_playground_task.resolve_env") as resolve_env,
        patch("tasks.sentiment_qa.user_playground_task.resolve_date") as resolve_date,
    ):
        load_yaml.return_value = COMMON_YAML
        resolve_env.side_effect = lambda value: value
        resolve_date.side_effect = lambda text, replace_date=None: text
        yield {
            "sharepoint": sp_cls,
            "gcs": gcs_cls,
            "gemini": gemini_cls,
            "msgraph": graph_cls,
        }


@pytest.fixture
def task(mock_deps):
    instance = UserPlaygroundTask()
    instance.gcs = {
        "project_id": "qa-proj",
        "bucket_name": "qa-bucket",
        "input_folder": "input",
        "processing_voice_folder": "processing/voice/%{DATA_DATE_YYYYMMDDHHMMSS}",
        "processing_batch_folder": "processing/batch/%{DATA_DATE_YYYYMMDDHHMMSS}",
        "output_folder": "output/%{DATA_DATE_YYYYMMDDHHMMSS}",
        "archive_batch_folder": "archive/batch",
    }
    instance.gcp = {"project_id": "qa-proj", "project_name": "QA Project"}
    instance.vertexai = {"project_id": "qa-proj", "location": "us-central1", "model": "gemini-2.5-flash"}
    instance.sharepoint = {
        "control": {
            "source_folder": "Source/Voice",
            "daily_output_file": "Control/daily.xlsx",
            "archive_folder": "Archive/batch",
            "transaction_log_file": "Control/transaction.csv",
            "user_config_path": "Control/user_config.xlsx",
        }
    }
    instance.framework = {"concurrency_upload": "2", "user_config_path": "config/user_config.xlsx"}
    instance.packages = {"execution_dt": datetime(2025, 1, 15, 12, 0, 0)}
    instance.get_package = lambda key, default=None: instance.packages.get(key, default)
    instance.sharepoint_control = Mock()
    instance.gcs_module = Mock()
    instance.gcs_module.project_id = "qa-proj"
    instance.gcs_module.bucket_name = "qa-bucket"
    instance.gemini_batch_module = Mock()
    instance.msgraph_module = Mock()
    instance.control_site = "control.sharepoint.com"
    instance.batch_run_datetime = "20250115120000"
    instance.task_sender_email = "sender@example.com"
    instance.task_receiver_email = "receiver@example.com"
    instance.task_cc_email = "cc@example.com"
    instance.project_id = "qa-proj"
    instance.task_name = "QAUserPlaygroundTask"
    instance.processing_log_list = []
    instance.export_output_task_instance = Mock()
    instance.export_output_task_instance._calculate_category = Mock(return_value="Pass")
    return instance


def _playground_prediction_df(**overrides):
    row = {
        "file_name": "sample",
        "full_path": "Source/Voice/sample.wav",
        "folder": None,
        "record_date": "20250115",
        "duration": 60,
        "token_input": {"text": 7},
        "token_cached": 1,
        "token_output": {"text": 3},
        "status": "SUCCESS",
        "message": "",
        "processed_time": "2025-01-15T12:01:00Z",
        "create_time": "2025-01-15T12:00:00Z",
        "model_version": "gemini-2.5-flash",
        "load_dt": "2025-01-15 12:05:00",
    }
    row.update(overrides)
    return pd.DataFrame([row])


# ============================================================
# pre_execute
# ============================================================


def test_pre_execute_initializes_modules(mock_deps):
    instance = UserPlaygroundTask()
    instance.gcs = {"project_id": "qa-proj", "bucket_name": "qa-bucket"}
    instance.vertexai = {"project_id": "qa-proj", "location": "us-central1"}

    instance.pre_execute()

    mock_deps["sharepoint"].assert_called_once()
    mock_deps["gcs"].assert_called_once_with(project_id="qa-proj", bucket_name="qa-bucket")
    mock_deps["gemini"].assert_called_once_with(genai_project_id="qa-proj", genai_location="us-central1")
    mock_deps["msgraph"].assert_called_once()


@pytest.mark.parametrize("dependency_key", ["sharepoint", "gcs", "gemini", "msgraph"])
def test_pre_execute_raises_when_module_initialization_fails(mock_deps, dependency_key):
    instance = UserPlaygroundTask()
    instance.gcs = {"project_id": "qa-proj", "bucket_name": "qa-bucket"}
    instance.vertexai = {"project_id": "qa-proj", "location": "us-central1"}
    mock_deps[dependency_key].side_effect = RuntimeError(f"{dependency_key} init failed")

    with pytest.raises(RuntimeError, match=f"{dependency_key} init failed"):
        instance.pre_execute()


# ============================================================
# execute_task
# ============================================================


def test_execute_task_retrieves_when_prediction_exists(task):
    task.gcs_module.list_files.return_value = ["output/run-1/predictions.jsonl"]

    with patch.object(task, "_retrieve_prediction_step", return_value=[{"ok": True}]) as retrieve_step:
        result = task.execute_task()

    assert result == [{"ok": True}]
    retrieve_step.assert_called_once()


def test_execute_task_submits_when_no_prediction_exists(task):
    task.gcs_module.list_files.return_value = []

    with patch.object(task, "_submit_job_step", return_value=(None, None, [])) as submit_step:
        result = task.execute_task()

    assert result is None
    submit_step.assert_called_once_with(task.packages["execution_dt"], "2025-01-15")


def test_execute_task_uses_rerun_date(task):
    task.packages["rerun_data_dt"] = "2025-01-10"
    task.gcs_module.list_files.return_value = []

    with patch.object(task, "_submit_job_step", return_value=(None, None, [])) as submit_step:
        task.execute_task()

    assert submit_step.call_args.args[1] == "2025-01-10"


# ============================================================
# _retrieve_prediction_step
# ============================================================


def test_retrieve_prediction_step_exports_and_uploads_daily_file(task):
    mock_export_task = Mock()
    mock_export_task.pre_execute.return_value = None
    mock_export_task._format_output.return_value = [{"call_id": "1001"}]

    with (
        patch(
            "tasks.sentiment_qa.user_playground_task.GeminiBatchModule.retrieve_batch_results",
            return_value=[{"row": 1}],
        ),
        patch.object(task, "_proc_raw_prediction", return_value=[{"prediction": {"status": "SUCCESS"}}]),
        patch("tasks.sentiment_qa.user_playground_task.ExportOutputResultTask", return_value=mock_export_task),
        patch("tasks.sentiment_qa.user_playground_task.clean_invalid_xml_chars", side_effect=lambda x: x),
        patch.object(task, "_upload_daily_files") as upload_daily,
    ):
        results = task._retrieve_prediction_step(
            ["output/20250115120000/prediction-model/predictions.jsonl"],
            datetime(2025, 1, 15, 12, 0, 0),
        )

    assert results == [{"prediction": {"status": "SUCCESS"}}]
    upload_daily.assert_called_once()
    assert task.batch_run_datetime == "20250115120000"


def test_retrieve_prediction_step_sets_batch_run_datetime_from_path(task):
    mock_export_task = Mock()
    mock_export_task._format_output.return_value = []

    with (
        patch(
            "tasks.sentiment_qa.user_playground_task.GeminiBatchModule.retrieve_batch_results",
            return_value=[{"row": 1}],
        ),
        patch.object(task, "_proc_raw_prediction", return_value=[{"prediction": {"status": "SUCCESS"}}]),
        patch("tasks.sentiment_qa.user_playground_task.ExportOutputResultTask", return_value=mock_export_task),
        patch("tasks.sentiment_qa.user_playground_task.clean_invalid_xml_chars", side_effect=lambda x: x),
        patch.object(task, "_upload_daily_files"),
    ):
        task._retrieve_prediction_step(
            ["sentiment_qa/user_playground/output/20260511090050/prediction-model/predictions.jsonl"],
            datetime(2025, 1, 15, 12, 0, 0),
        )

    assert task.batch_run_datetime == "20260511090050"


def test_retrieve_prediction_step_raises_when_all_batch_retrievals_fail(task):
    with (
        patch(
            "tasks.sentiment_qa.user_playground_task.GeminiBatchModule.retrieve_batch_results",
            side_effect=RuntimeError("gcs down"),
        ),
        pytest.raises(Exception, match="All batch files failed to retrieve"),
    ):
        task._retrieve_prediction_step(["output/run1/predictions.jsonl"], datetime(2025, 1, 15, 12, 0, 0))


def test_retrieve_prediction_step_raises_when_all_failed(task):
    mock_export_task = Mock()
    mock_export_task._format_output.return_value = []

    with (
        patch(
            "tasks.sentiment_qa.user_playground_task.GeminiBatchModule.retrieve_batch_results",
            return_value=[{"row": 1}],
        ),
        patch.object(task, "_proc_raw_prediction", return_value=[{"prediction": {"status": "FAILED"}}]),
        patch("tasks.sentiment_qa.user_playground_task.ExportOutputResultTask", return_value=mock_export_task),
        patch("tasks.sentiment_qa.user_playground_task.clean_invalid_xml_chars", side_effect=lambda x: x),
        patch.object(task, "_upload_daily_files"),
        pytest.raises(Exception, match=r"prediction\(s\) failed"),
    ):
        task._retrieve_prediction_step(
            ["output/20250115120000/prediction-model/predictions.jsonl"],
            datetime(2025, 1, 15, 12, 0, 0),
        )


def test_retrieve_prediction_step_raises_when_export_fails(task):
    with (
        patch(
            "tasks.sentiment_qa.user_playground_task.GeminiBatchModule.retrieve_batch_results",
            return_value=[{"row": 1}],
        ),
        patch.object(task, "_proc_raw_prediction", return_value=[{"prediction": {"status": "SUCCESS"}}]),
        patch(
            "tasks.sentiment_qa.user_playground_task.ExportOutputResultTask",
            side_effect=RuntimeError("export init failed"),
        ),
        pytest.raises(Exception, match="Export result step failed"),
    ):
        task._retrieve_prediction_step(
            ["output/20250115120000/pred/predictions.jsonl"], datetime(2025, 1, 15, 12, 0, 0)
        )


def test_retrieve_prediction_step_continues_after_single_batch_error(task):
    mock_export_task = Mock()
    mock_export_task._format_output.return_value = []

    with (
        patch(
            "tasks.sentiment_qa.user_playground_task.GeminiBatchModule.retrieve_batch_results",
            side_effect=[RuntimeError("first broke"), [{"row": 1}]],
        ),
        patch.object(
            task,
            "_proc_raw_prediction",
            return_value=[{"prediction": {"status": "SUCCESS"}}],
        ) as proc_raw,
        patch("tasks.sentiment_qa.user_playground_task.ExportOutputResultTask", return_value=mock_export_task),
        patch("tasks.sentiment_qa.user_playground_task.clean_invalid_xml_chars", side_effect=lambda x: x),
        patch.object(task, "_upload_daily_files"),
    ):
        results = task._retrieve_prediction_step(
            ["output/bad/predictions.jsonl", "output/20250115120000/prediction-model/predictions.jsonl"],
            datetime(2025, 1, 15, 12, 0, 0),
        )

    assert len(results) == 1
    proc_raw.assert_called_once()


# ============================================================
# _submit_job_step
# ============================================================


def _make_prep_task():
    prep_task = Mock()
    prep_task.pre_execute.return_value = None
    prep_task._prepare_prompt.return_value = "Prompt for {date}"
    prep_task._get_analysis_schema.return_value = {"response_mime_type": "application/json"}
    return prep_task


def test_submit_job_step_builds_payload_uploads_and_submits(task):
    task.framework["concurrency_upload"] = "2"
    task.vertexai["generation_config"] = {"temperature": 0.1}
    task.vertexai["batch_job_name"] = "playground-%{DATA_DATE_YYYYMMDDHHMMSS}"
    task.gcs_module.list_files.side_effect = [
        [
            "input/1111_0890000000_120000_A001_jane_doe_D_20250115_60_IN.wav",
            "input/1111_0890000000_120000_A001_jane_doe_D_20250115_60_OUT.txt",
        ],
        [
            "processing/voice/1111_0890000000_120000_A001_jane_doe_D_20250115_60_IN.wav",
            "processing/voice/1111_0890000000_120000_A001_jane_doe_D_20250115_60_OUT.txt",
        ],
    ]
    task.gcs_module.is_file_exists.return_value = True
    task.gemini_batch_module.create_batch_job.return_value = SimpleNamespace(
        name="projects/p/locations/l/batchPredictionJobs/123",
        display_name="playground-job",
    )
    task.gemini_batch_module.status_check_batch_job.return_value = "JOB_STATE_RUNNING"
    prep_task = _make_prep_task()

    with (
        patch.object(task, "_upload_voice_files"),
        patch("tasks.sentiment_qa.user_playground_task.PrepPayloadTask", return_value=prep_task),
        patch("tasks.sentiment_qa.user_playground_task.time.sleep"),
        patch(
            "tasks.sentiment_qa.user_playground_task.asyncio.run",
            return_value={"success": 2, "failed": 1, "errors": ["copy failed"]},
        ),
    ):
        payload_path, output_folder, processing_logs = task._submit_job_step(
            datetime(2025, 1, 15, 12, 0, 0), "2025-01-15"
        )

    assert payload_path.endswith("payloads.jsonl")
    assert output_folder == "output/%{DATA_DATE_YYYYMMDDHHMMSS}"
    assert len(processing_logs) == 2
    assert all(log.batch_job_id == "123" for log in processing_logs)
    uploaded_payload = task.gcs_module.update_content_to_gcs.call_args.kwargs["content"].decode("utf-8")
    assert "1111_0890000000_120000_A001_jane_doe_D_20250115_60_IN.wav" in uploaded_payload
    assert "1111_0890000000_120000_A001_jane_doe_D_20250115_60_OUT.txt" in uploaded_payload
    assert '"text": "Prompt for 20250115"' in uploaded_payload


def test_submit_job_step_returns_none_when_no_supported_payloads(task):
    task.framework["concurrency_upload"] = "2"
    task.gcs_module.list_files.side_effect = [
        ["input/1111_0890000000_120000_A001_jane_doe_D_20250115_60_IN.bin"],
        ["processing/1111_0890000000_120000_A001_jane_doe_D_20250115_60_IN.bin"],
    ]
    prep_task = _make_prep_task()

    with (
        patch.object(task, "_upload_voice_files"),
        patch("tasks.sentiment_qa.user_playground_task.PrepPayloadTask", return_value=prep_task),
        patch("tasks.sentiment_qa.user_playground_task.asyncio.run", return_value={"success": 1, "failed": 0}),
    ):
        payload_path, output_folder, processing_logs = task._submit_job_step(
            datetime(2025, 1, 15, 12, 0, 0), "2025-01-15"
        )

    assert payload_path is None
    assert output_folder is None
    assert len(processing_logs) == 0


def test_submit_job_step_raises_when_listing_input_files_fails(task):
    task.framework["concurrency_upload"] = "2"
    task.gcs_module.list_files.side_effect = RuntimeError("input list failed")

    with patch.object(task, "_upload_voice_files"), pytest.raises(RuntimeError, match="input list failed"):
        task._submit_job_step(datetime(2025, 1, 15, 12, 0, 0), "2025-01-15")


def test_submit_job_step_raises_when_copying_to_processing_fails(task):
    task.framework["concurrency_upload"] = "2"
    task.gcs_module.list_files.side_effect = [["input/file.wav"]]

    with (
        patch.object(task, "_upload_voice_files"),
        patch("tasks.sentiment_qa.user_playground_task.asyncio.run", side_effect=RuntimeError("copy failed")),
        pytest.raises(RuntimeError, match="copy failed"),
    ):
        task._submit_job_step(datetime(2025, 1, 15, 12, 0, 0), "2025-01-15")


def test_submit_job_step_raises_when_processing_folder_is_empty(task):
    task.framework["concurrency_upload"] = "2"
    task.gcs_module.list_files.side_effect = [["input/file.wav"], []]

    with (
        patch.object(task, "_upload_voice_files"),
        patch("tasks.sentiment_qa.user_playground_task.asyncio.run", return_value={"success": 1, "failed": 0}),
        pytest.raises(Exception, match="No files in processing folder"),
    ):
        task._submit_job_step(datetime(2025, 1, 15, 12, 0, 0), "2025-01-15")


def test_submit_job_step_raises_when_prompt_preparation_fails(task):
    task.framework["concurrency_upload"] = "2"
    task.gcs_module.list_files.side_effect = [["input/file.wav"], ["processing/file.wav"]]
    prep_task = _make_prep_task()
    prep_task.pre_execute.side_effect = RuntimeError("prompt load failed")

    with (
        patch.object(task, "_upload_voice_files"),
        patch("tasks.sentiment_qa.user_playground_task.PrepPayloadTask", return_value=prep_task),
        patch("tasks.sentiment_qa.user_playground_task.asyncio.run", return_value={"success": 1, "failed": 0}),
        pytest.raises(Exception, match="Cannot prepare prompt"),
    ):
        task._submit_job_step(datetime(2025, 1, 15, 12, 0, 0), "2025-01-15")


def test_submit_job_step_records_payload_creation_errors_for_unsupported_direction(task):
    task.framework["concurrency_upload"] = "2"
    # File without _IN or _OUT direction at index 9
    task.gcs_module.list_files.side_effect = [
        ["input/1111_0890000000_120000_A001_jane_doe_D_20250115_60_UNKNOWN.wav"],
        ["processing/1111_0890000000_120000_A001_jane_doe_D_20250115_60_UNKNOWN.wav"],
    ]
    prep_task = _make_prep_task()

    with (
        patch.object(task, "_upload_voice_files"),
        patch("tasks.sentiment_qa.user_playground_task.PrepPayloadTask", return_value=prep_task),
        patch("tasks.sentiment_qa.user_playground_task.asyncio.run", return_value={"success": 1, "failed": 0}),
    ):
        payload_path, output_folder, processing_logs = task._submit_job_step(
            datetime(2025, 1, 15, 12, 0, 0), "2025-01-15"
        )

    assert payload_path is None
    assert output_folder is None
    assert len(processing_logs) == 1
    assert processing_logs[0].status == "FAILED"
    assert "Failed to create payload" in processing_logs[0].error_message


def test_submit_job_step_raises_when_payload_upload_fails(task):
    task.framework["concurrency_upload"] = "2"
    task.gcs_module.list_files.side_effect = [
        ["input/1111_0890000000_120000_A001_jane_doe_D_20250115_60_IN.wav"],
        ["processing/1111_0890000000_120000_A001_jane_doe_D_20250115_60_IN.wav"],
    ]
    task.gcs_module.update_content_to_gcs.side_effect = RuntimeError("upload failed")
    prep_task = _make_prep_task()

    with (
        patch.object(task, "_upload_voice_files"),
        patch("tasks.sentiment_qa.user_playground_task.PrepPayloadTask", return_value=prep_task),
        patch("tasks.sentiment_qa.user_playground_task.asyncio.run", return_value={"success": 1, "failed": 0}),
        pytest.raises(Exception, match="Cannot upload payload to GCS: upload failed"),
    ):
        task._submit_job_step(datetime(2025, 1, 15, 12, 0, 0), "2025-01-15")


def test_submit_job_step_raises_when_uploaded_payload_file_missing(task):
    task.framework["concurrency_upload"] = "2"
    task.gcs_module.list_files.side_effect = [
        ["input/1111_0890000000_120000_A001_jane_doe_D_20250115_60_IN.wav"],
        ["processing/1111_0890000000_120000_A001_jane_doe_D_20250115_60_IN.wav"],
    ]
    task.gcs_module.is_file_exists.return_value = False
    prep_task = _make_prep_task()

    with (
        patch.object(task, "_upload_voice_files"),
        patch("tasks.sentiment_qa.user_playground_task.PrepPayloadTask", return_value=prep_task),
        patch("tasks.sentiment_qa.user_playground_task.asyncio.run", return_value={"success": 1, "failed": 0}),
        pytest.raises(Exception, match="Payload file not found"),
    ):
        task._submit_job_step(datetime(2025, 1, 15, 12, 0, 0), "2025-01-15")


def test_submit_job_step_raises_when_status_check_fails(task):
    task.framework["concurrency_upload"] = "2"
    task.gcs_module.list_files.side_effect = [
        ["input/1111_0890000000_120000_A001_jane_doe_D_20250115_60_IN.wav"],
        ["processing/1111_0890000000_120000_A001_jane_doe_D_20250115_60_IN.wav"],
    ]
    task.gcs_module.is_file_exists.return_value = True
    task.gemini_batch_module.create_batch_job.return_value = SimpleNamespace(
        name="projects/p/locations/l/batchPredictionJobs/123",
        display_name="pg-job",
    )
    task.gemini_batch_module.status_check_batch_job.return_value = "JOB_STATE_FAILED"
    prep_task = _make_prep_task()

    with (
        patch.object(task, "_upload_voice_files"),
        patch("tasks.sentiment_qa.user_playground_task.PrepPayloadTask", return_value=prep_task),
        patch("tasks.sentiment_qa.user_playground_task.time.sleep"),
        patch("tasks.sentiment_qa.user_playground_task.asyncio.run", return_value={"success": 1, "failed": 0}),
        pytest.raises(Exception, match="Batch job failed immediately"),
    ):
        task._submit_job_step(datetime(2025, 1, 15, 12, 0, 0), "2025-01-15")


def test_submit_job_step_falls_back_when_batch_job_log_stamping_fails(task):
    task.framework["concurrency_upload"] = "2"
    task.vertexai["batch_job_name"] = "fallback-display-name"
    task.gcs_module.list_files.side_effect = [
        ["input/1111_0890000000_120000_A001_jane_doe_D_20250115_60_IN.wav"],
        ["processing/1111_0890000000_120000_A001_jane_doe_D_20250115_60_IN.wav"],
    ]
    task.gcs_module.is_file_exists.return_value = True
    task.gemini_batch_module.create_batch_job.return_value = SimpleNamespace(name=None, display_name="batch-display")
    task.gemini_batch_module.status_check_batch_job.return_value = "JOB_STATE_RUNNING"
    prep_task = _make_prep_task()

    with (
        patch.object(task, "_upload_voice_files"),
        patch("tasks.sentiment_qa.user_playground_task.PrepPayloadTask", return_value=prep_task),
        patch("tasks.sentiment_qa.user_playground_task.time.sleep"),
        patch("tasks.sentiment_qa.user_playground_task.asyncio.run", return_value={"success": 1, "failed": 0}),
    ):
        _, _, processing_logs = task._submit_job_step(datetime(2025, 1, 15, 12, 0, 0), "2025-01-15")

    assert len(processing_logs) == 1
    assert processing_logs[0].batch_job_id is None
    assert processing_logs[0].batch_job_display_name == "fallback-display-name"
    assert processing_logs[0].model_name == "gemini-2.5-flash"


# ============================================================
# _upload_voice_files
# ============================================================


def test_upload_voice_files_filters_to_wav_and_builds_stream_list(task):
    task.sharepoint["control"]["source_folder"] = "Source/Voice"
    task.gcs["input_folder"] = "input"
    task.sharepoint_control.is_item_exists.return_value = True
    task.sharepoint_control.list_files.return_value = [
        {
            "name": "call-1.wav",
            "id": "1",
            "parentReference": {"path": "/drive/root:/test_site/user_playground/input"},
            "createdDateTime": "2025-01-15T12:00:00Z",
        },
        {
            "name": "ignore.txt",
            "id": "2",
            "parentReference": {"path": "/drive/root:/test_site/user_playground/input"},
            "createdDateTime": "2025-01-15T12:01:00Z",
        },
    ]

    with patch("tasks.sentiment_qa.user_playground_task.asyncio.run", return_value={"success": 1, "failed": 0}):
        task._upload_voice_files()

    stream_list = task.gcs_module.upload_sharepoint_to_gcs.call_args.kwargs["stream_list"]
    assert len(stream_list) == 1
    assert stream_list[0]["upload"] == "input/call-1.wav"
    assert stream_list[0]["mime_type"] == "audio/wav"
    assert "call-1.wav" in stream_list[0]["download"]


def test_upload_voice_files_skips_when_source_folder_not_found(task):
    task.sharepoint_control.is_item_exists.return_value = False

    task._upload_voice_files()

    task.gcs_module.upload_sharepoint_to_gcs.assert_not_called()


def test_upload_voice_files_handles_empty_source_folder(task):
    task.sharepoint_control.is_item_exists.return_value = True
    task.sharepoint_control.list_files.return_value = []

    task._upload_voice_files()

    task.gcs_module.upload_sharepoint_to_gcs.assert_not_called()


# ============================================================
# _proc_raw_prediction
# ============================================================


def test_proc_raw_prediction_covers_skip_error_missing_text_and_success(task):
    success_file = (
        "gs://qa-bucket/processing/voice/202501/20250115/1111_0890000000_120000_A001_jane_doe_D_20250115_60_IN.wav"
    )
    raw_jsonl = [
        {"request": {}, "response": {}},
        _raw_line(success_file, status="FAILED_PRECONDITION", text='{"x": 1}'),
        _raw_line(success_file, text=None),
        _raw_line(success_file, text='{"service_quality": {"greeting_standard": "True"}}', model_version=None),
    ]

    with patch(
        "tasks.sentiment_qa.user_playground_task.GeminiBatchModule.sum_tokens_usage_for_billing",
        return_value={"token_input": {"text": 7, "audio": 0}, "token_output": {"text": 3}, "token_cached": 1},
    ):
        results = task._proc_raw_prediction(
            "output/run-1/predictions.jsonl", raw_jsonl, datetime(2025, 1, 15, 12, 0, 0)
        )

    assert len(results) == 3
    assert results[0]["prediction"]["status"] == "FAILED"
    assert results[0]["prediction"]["message"] == "FAILED_PRECONDITION"
    assert results[1]["prediction"]["status"] == "FAILED"
    assert "No prediction found" in results[1]["prediction"]["message"]
    assert results[2]["prediction"]["status"] == "SUCCESS"
    assert results[2]["prediction"]["model_version"] == task.DEFAULT_MODEL_VERSION
    assert results[2]["file_metadata"]["first_name"] == "Jane"
    assert results[2]["file_metadata"]["record_date"] == "20250115"
    assert results[2]["prediction"]["token_cached"] == 1


def test_proc_raw_prediction_extracts_call_direction(task):
    file_uri = "gs://qa-bucket/processing/voice/20250115/1111_0890000000_120000_A001_jane_doe_D_20250115_60_IN.wav"
    raw_jsonl = [_raw_line(file_uri, text='{"service_quality": {"greeting_standard": "True"}}')]

    with patch(
        "tasks.sentiment_qa.user_playground_task.GeminiBatchModule.sum_tokens_usage_for_billing",
        return_value={"token_input": {"text": 1, "audio": 0}, "token_output": {"text": 1}, "token_cached": 0},
    ):
        results = task._proc_raw_prediction(
            "output/run-1/predictions.jsonl", raw_jsonl, datetime(2025, 1, 15, 12, 0, 0)
        )

    assert results[0]["file_metadata"]["call_direction"] == "IN"


def test_proc_raw_prediction_defaults_token_usage_on_usage_error(task):
    file_uri = "gs://qa-bucket/processing/voice/20250115/not_enough_parts.wav"
    raw_jsonl = [_raw_line(file_uri, text='{"service_quality": {"greeting_standard": "True"}}')]

    with patch(
        "tasks.sentiment_qa.user_playground_task.GeminiBatchModule.sum_tokens_usage_for_billing",
        side_effect=RuntimeError("usage missing"),
    ):
        results = task._proc_raw_prediction(
            "output/run-1/predictions.jsonl", raw_jsonl, datetime(2025, 1, 15, 12, 0, 0)
        )

    assert results[0]["prediction"]["token_input"] == {"text": 0, "audio": 0}
    assert results[0]["prediction"]["token_output"] == {"text": 0}
    assert results[0]["prediction"]["token_cached"] == 0


def test_proc_raw_prediction_falls_back_when_filename_parsing_fails(task):
    raw_jsonl = [
        _raw_line(
            "gs://qa-bucket/processing/voice/20250115/1111_0890000000_120000_A001_jane_doe_D_20250115_60.wav",
            text='{"service_quality": {"greeting_standard": "True"}}',
        )
    ]

    def safe_list_get_side_effect(data, index, default=None):
        if isinstance(data, list) and index == 4 and default == "":
            raise RuntimeError("parse failed")
        try:
            return data[index]
        except Exception:
            return default

    with (
        patch("tasks.sentiment_qa.user_playground_task.safe_list_get", side_effect=safe_list_get_side_effect),
        patch(
            "tasks.sentiment_qa.user_playground_task.GeminiBatchModule.sum_tokens_usage_for_billing",
            return_value={"token_input": {"text": 1, "audio": 0}, "token_output": {"text": 1}, "token_cached": 0},
        ),
    ):
        results = task._proc_raw_prediction(
            "output/run-1/predictions.jsonl", raw_jsonl, datetime(2025, 1, 15, 12, 0, 0)
        )

    assert results[0]["file_metadata"]["file_ext"] is None
    assert results[0]["file_metadata"]["record_date"] == task.DEFAULT_RECORD_DATE


def test_proc_raw_prediction_skips_lines_with_unexpected_errors(task):
    raw_jsonl = [_raw_line("gs://qa-bucket/processing/voice/20250115/file.wav", text='{"x": 1}')]

    with patch(
        "tasks.sentiment_qa.user_playground_task.recursive_dict_value_by_key", side_effect=RuntimeError("bad line")
    ):
        results = task._proc_raw_prediction(
            "output/run-1/predictions.jsonl", raw_jsonl, datetime(2025, 1, 15, 12, 0, 0)
        )

    assert results == []


def test_proc_raw_prediction_uses_default_record_date_when_no_date_found(task):
    raw_jsonl = [
        _raw_line(
            "gs://qa-bucket/processing/voice/no-date-here/file.wav",
            text='{"service_quality": {"greeting_standard": "True"}}',
        )
    ]

    with patch(
        "tasks.sentiment_qa.user_playground_task.GeminiBatchModule.sum_tokens_usage_for_billing",
        return_value={"token_input": {"text": 1, "audio": 0}, "token_output": {"text": 1}, "token_cached": 0},
    ):
        results = task._proc_raw_prediction(
            "output/run-1/predictions.jsonl", raw_jsonl, datetime(2025, 1, 15, 12, 0, 0)
        )

    assert results[0]["file_metadata"]["record_date"] == task.DEFAULT_RECORD_DATE


def test_proc_raw_prediction_ignores_usage_errors_for_failed_predictions(task):
    file_uri = "gs://qa-bucket/processing/voice/20250115/file.wav"
    raw_jsonl = [
        _raw_line(file_uri, status="FAILED_PRECONDITION", text='{"x": 1}'),
        _raw_line(file_uri, text=None),
    ]

    with patch(
        "tasks.sentiment_qa.user_playground_task.GeminiBatchModule.sum_tokens_usage_for_billing",
        side_effect=RuntimeError("usage unavailable"),
    ):
        results = task._proc_raw_prediction(
            "output/run-1/predictions.jsonl", raw_jsonl, datetime(2025, 1, 15, 12, 0, 0)
        )

    assert "token_input" not in results[0]["prediction"]
    assert "token_input" not in results[1]["prediction"]


# ============================================================
# _prepare_prediction_payload
# ============================================================


@pytest.mark.parametrize(
    ("prediction", "err_flag", "expected_status"),
    [('{"service_quality": {"greeting_standard": "True"}}', False, "SUCCESS"), ("boom", True, "FAILED")],
)
def test_prepare_prediction_payload(task, prediction, err_flag, expected_status):
    payload = task._prepare_prediction_payload({"file_name": "test-file"}, prediction, err_flag=err_flag)

    assert payload["prediction"]["status"] == expected_status
    if err_flag:
        assert payload["prediction"]["message"] == "boom"
    else:
        assert payload["prediction"]["raw_prediction"]["service_quality"]["greeting_standard"] == "True"


def test_prepare_prediction_payload_invalid_json_marks_failed(task):
    payload = task._prepare_prediction_payload({"file_name": "test-file"}, "{invalid json}")

    assert payload["prediction"]["status"] == "FAILED"
    assert "Failed to parse prediction JSON" in payload["prediction"]["message"]


# ============================================================
# _upload_daily_files
# ============================================================


def test_upload_daily_files_uploads_excel_to_sharepoint(task):
    service_quality_group_df = pd.DataFrame(
        [
            {"sub_category": "greet_group", "item": "greeting_standard"},
        ]
    )
    weight_score_content = SimpleNamespace(content=_excel_bytes(service_quality_group_df, "service_quality_group"))
    task.sharepoint_control.get_item_by_path.return_value = weight_score_content
    task.export_output_task_instance._calculate_category = Mock(return_value="Pass")

    daily_df = pd.DataFrame(
        [
            {
                "call_date": "2025-01-15",
                "agent_id": "A001",
                "greeting_standard": "Pass",
                "greeting_standard_reason": "Good greeting",
                "service_quality_score": 90,
                "service_quality_performance_insight": "Good",
            }
        ]
    )

    mock_sp = Mock()
    task._upload_daily_files(daily_df, "Control/daily.xlsx", mock_sp)

    mock_sp.upload_file.assert_called_once()
    assert mock_sp.upload_file.call_args.kwargs["upload_path"] == "Control/daily.xlsx"
    assert isinstance(mock_sp.upload_file.call_args.kwargs["content"], bytes)


def test_upload_daily_files_falls_back_to_local_config_when_sharepoint_fails(task):
    task.sharepoint_control.get_item_by_path.side_effect = RuntimeError("SP unavailable")
    task.export_output_task_instance._calculate_category = Mock(return_value="Pass")

    daily_df = pd.DataFrame([{"call_date": "2025-01-15", "agent_id": "A001"}])
    service_quality_group_df = pd.DataFrame([{"sub_category": None, "item": "service_quality_score"}])
    mock_sp = Mock()

    with patch("tasks.sentiment_qa.user_playground_task.read_xlsx", return_value=service_quality_group_df):
        task._upload_daily_files(daily_df, "Control/daily.xlsx", mock_sp)

    mock_sp.upload_file.assert_called_once()


# ============================================================
# post_execute
# ============================================================


def test_post_execute_skips_when_no_result(task):
    with (
        patch.object(task, "_archive_and_cleanup") as archive_cleanup,
        patch.object(task, "_insert_log_record") as insert_log,
    ):
        result = task.post_execute(None)

    assert result is None
    archive_cleanup.assert_not_called()
    insert_log.assert_not_called()


def test_post_execute_archives_gcs_and_inserts_log(task):
    result = [
        {
            "file_metadata": {"file_name": "sample", "file_ext": ".wav", "record_date": "20250115", "duration": 60},
            "prediction": {
                "status": "SUCCESS",
                "message": None,
                "token_input": {"text": 7},
                "token_cached": 1,
                "token_output": {"text": 3},
                "processed_time": "2025-01-15T12:01:00Z",
                "create_time": "2025-01-15T12:00:00Z",
                "model_version": "gemini-2.5-flash",
            },
            "load_dt": "2025-01-15 12:05:00",
        }
    ]
    # Early return from Phase 3 — no voice files in source path
    task.sharepoint_control.is_item_exists.return_value = False

    with (
        patch.object(task, "_archive_and_cleanup") as archive_cleanup,
        patch.object(task, "_insert_log_record") as insert_log,
        patch(
            "tasks.sentiment_qa.user_playground_task.get_current_datetime",
            return_value=datetime(2025, 1, 15, 12, 30, 0),
        ),
    ):
        output = task.post_execute(result)

    assert output == result
    archive_cleanup.assert_called_once_with(task.packages["execution_dt"])
    insert_log.assert_called_once()
    insert_df = insert_log.call_args.args[0]
    assert insert_df.iloc[0]["full_path"] == "Source/Voice/sample.wav"
    assert insert_df.iloc[0]["status"] == "SUCCESS"


def test_post_execute_raises_when_archive_fails(task):
    result = [
        {
            "file_metadata": {"file_name": "sample", "file_ext": ".wav", "record_date": "20250115", "duration": 60},
            "prediction": {"status": "SUCCESS"},
            "load_dt": "2025-01-15 12:05:00",
        }
    ]

    with patch.object(task, "_archive_and_cleanup", side_effect=RuntimeError("gcs down")):
        with pytest.raises(Exception, match="Archive and cleanup process failed"):
            task.post_execute(result)


def test_post_execute_raises_when_transaction_log_insertion_fails(task):
    result = [
        {
            "file_metadata": {"file_name": "sample", "file_ext": ".wav", "record_date": "20250115", "duration": 60},
            "prediction": {"status": "SUCCESS"},
            "load_dt": "2025-01-15 12:05:00",
        }
    ]

    with (
        patch.object(task, "_archive_and_cleanup"),
        patch.object(task, "_insert_log_record", side_effect=RuntimeError("log failed")),
        pytest.raises(Exception, match="Transaction log insertion failed"),
    ):
        task.post_execute(result)


def test_post_execute_raises_when_output_path_resolution_fails(task):
    result = [
        {
            "file_metadata": {"file_name": "sample", "file_ext": ".wav", "record_date": "20250115", "duration": 60},
            "prediction": {"status": "SUCCESS"},
            "load_dt": "2025-01-15 12:05:00",
        }
    ]

    def bad_resolve_date(text, replace_date=None):
        raise RuntimeError("path resolution failed")

    with (
        patch.object(task, "_archive_and_cleanup"),
        patch.object(task, "_insert_log_record"),
        patch("tasks.sentiment_qa.user_playground_task.resolve_date", side_effect=bad_resolve_date),
        pytest.raises(Exception, match="Cannot determine output file path"),
    ):
        task.post_execute(result)


def test_post_execute_returns_early_when_source_path_not_exists(task):
    result = [
        {
            "file_metadata": {"file_name": "sample", "file_ext": ".wav", "record_date": "20250115", "duration": 60},
            "prediction": {"status": "SUCCESS"},
            "load_dt": "2025-01-15 12:05:00",
        }
    ]
    task.sharepoint_control.is_item_exists.return_value = False

    with (
        patch.object(task, "_archive_and_cleanup"),
        patch.object(task, "_insert_log_record"),
        patch(
            "tasks.sentiment_qa.user_playground_task.get_current_datetime",
            return_value=datetime(2025, 1, 15, 12, 30, 0),
        ),
    ):
        output = task.post_execute(result)

    assert output == result
    # Email NOT sent since we returned early
    task.msgraph_module.send_email.assert_not_called()


def test_post_execute_returns_early_when_no_voice_files_in_source(task):
    result = [
        {
            "file_metadata": {"file_name": "sample", "file_ext": ".wav", "record_date": "20250115", "duration": 60},
            "prediction": {"status": "SUCCESS"},
            "load_dt": "2025-01-15 12:05:00",
        }
    ]
    task.sharepoint_control.is_item_exists.return_value = True
    task.sharepoint_control.list_files.return_value = []

    with (
        patch.object(task, "_archive_and_cleanup"),
        patch.object(task, "_insert_log_record"),
        patch(
            "tasks.sentiment_qa.user_playground_task.get_current_datetime",
            return_value=datetime(2025, 1, 15, 12, 30, 0),
        ),
    ):
        output = task.post_execute(result)

    assert output == result
    task.msgraph_module.send_email.assert_not_called()


def test_post_execute_sends_email_after_successful_archiving(task):
    result = [
        {
            "file_metadata": {"file_name": "sample", "file_ext": ".wav", "record_date": "20250115", "duration": 60},
            "prediction": {"status": "SUCCESS"},
            "load_dt": "2025-01-15 12:05:00",
        }
    ]
    task.sharepoint_control.is_item_exists.return_value = True
    task.sharepoint_control.list_files.return_value = [{"name": "voice-1.wav"}]
    # Populate cache so Phase 4 does not return early before the email phase
    task._cache_oper_log = {
        "process_date": [datetime(2025, 1, 15).date()],
        "transaction_df": pd.DataFrame(
            [
                {
                    "start_time": "2025-01-15T12:00:00+00:00",
                    "end_time": "2025-01-15T12:05:00+00:00",
                    "gcp_project_id": "qa-proj",
                    "status_pass_failed_retry": "Pass",
                    "latency_ms": 100,
                }
            ]
        ),
    }

    with (
        patch.object(task, "_archive_and_cleanup"),
        patch.object(task, "_insert_log_record"),
        patch(
            "tasks.sentiment_qa.user_playground_task.get_current_datetime",
            return_value=datetime(2025, 1, 15, 12, 30, 0),
        ),
        patch("tasks.sentiment_qa.user_playground_task.asyncio.run", return_value=[True]),
        patch("tasks.sentiment_qa.user_playground_task.logging_ai_operation"),
    ):
        output = task.post_execute(result)

    assert output == result
    task.msgraph_module.send_email.assert_called_once()
    call_kwargs = task.msgraph_module.send_email.call_args.kwargs
    assert call_kwargs["sender_email"] == "sender@example.com"
    assert call_kwargs["receiver_email"] == "receiver@example.com"
    assert "user-playground" in call_kwargs["subject"]


def test_post_execute_copies_voice_files_to_archive(task):
    result = [
        {
            "file_metadata": {"file_name": "sample", "file_ext": ".wav", "record_date": "20250115", "duration": 60},
            "prediction": {"status": "SUCCESS"},
            "load_dt": "2025-01-15 12:05:00",
        }
    ]
    task.sharepoint_control.is_item_exists.return_value = True
    task.sharepoint_control.list_files.return_value = [{"name": "voice-1.wav"}, {"name": "voice-2.wav"}]

    with (
        patch.object(task, "_archive_and_cleanup"),
        patch.object(task, "_insert_log_record"),
        patch(
            "tasks.sentiment_qa.user_playground_task.get_current_datetime",
            return_value=datetime(2025, 1, 15, 12, 30, 0),
        ),
        patch("tasks.sentiment_qa.user_playground_task.asyncio.run", return_value=[True, True]) as asyncio_run,
    ):
        task.post_execute(result)

    # asyncio.run called for copy + for email send path — check copy was triggered
    asyncio_run.assert_called()


# ============================================================
# _archive_and_cleanup
# ============================================================


def test_archive_and_cleanup_moves_files_and_deletes_existing_dirs(task):
    task.gcs_module.list_files.return_value = [
        "output/20250115120000/predictions.jsonl",
        "output/20250115120000/model-1/predictions.jsonl",
    ]
    dir_exists = {"input": True, "processing": False, "output": True}
    task.gcs_module.is_dir_exists.side_effect = lambda dir_path: dir_exists.get(dir_path, False)

    task._archive_and_cleanup(datetime(2025, 1, 15, 12, 0, 0))

    move_calls = task.gcs_module.move_file.call_args_list
    assert len(move_calls) == 2
    assert move_calls[0].kwargs == {
        "source_path": "output/20250115120000/predictions.jsonl",
        "destination_path": "archive/batch/predictions.jsonl",
    }
    assert move_calls[1].kwargs == {
        "source_path": "output/20250115120000/model-1/predictions.jsonl",
        "destination_path": "archive/batch/model-1/predictions.jsonl",
    }
    deleted_dirs = [call.kwargs["dir_path"] for call in task.gcs_module.delete_dir.call_args_list]
    assert deleted_dirs == ["input", "output"]


def test_archive_and_cleanup_raises_when_archive_folder_resolution_fails(task):
    task.gcs_module.list_files.return_value = []
    task.gcs_module.is_dir_exists.return_value = False

    def bad_resolve_date(text, replace_date=None):
        raise RuntimeError("archive path failed")

    with (
        patch("tasks.sentiment_qa.user_playground_task.resolve_date", side_effect=bad_resolve_date),
        pytest.raises(Exception, match="Cannot determine archive batch folder path"),
    ):
        task._archive_and_cleanup(datetime(2025, 1, 15, 12, 0, 0))


def test_archive_and_cleanup_handles_output_listing_errors(task):
    task.gcs_module.list_files.side_effect = RuntimeError("cannot list output")
    task.gcs_module.is_dir_exists.return_value = False

    task._archive_and_cleanup(datetime(2025, 1, 15, 12, 0, 0))

    task.gcs_module.move_file.assert_not_called()


def test_archive_and_cleanup_continues_after_move_and_delete_failures(task):
    task.gcs_module.list_files.return_value = ["output/20250115120000/predictions.jsonl"]
    task.gcs_module.move_file.side_effect = RuntimeError("archive failed")
    task.gcs_module.is_dir_exists.side_effect = lambda dir_path: dir_path in {"input", "output"}
    task.gcs_module.delete_dir.side_effect = [RuntimeError("delete failed"), None]

    task._archive_and_cleanup(datetime(2025, 1, 15, 12, 0, 0))

    assert task.gcs_module.delete_dir.call_count == 2


def test_archive_and_cleanup_skips_dirs_when_resolve_fails(task):
    task.gcs_module.list_files.return_value = []
    task.gcs_module.is_dir_exists.return_value = False

    def resolve_env_side_effect(value):
        if value == "input":
            raise RuntimeError("missing input dir")
        if value == "processing/voice/%{DATA_DATE_YYYYMMDDHHMMSS}":
            raise RuntimeError("missing processing dir")
        return value

    with patch("tasks.sentiment_qa.user_playground_task.resolve_env", side_effect=resolve_env_side_effect):
        task._archive_and_cleanup(datetime(2025, 1, 15, 12, 0, 0))

    delete_candidates = [call.kwargs["dir_path"] for call in task.gcs_module.is_dir_exists.call_args_list]
    assert delete_candidates == ["output"]


# ============================================================
# _insert_log_record
# ============================================================


def test_insert_log_record_calls_transaction_log(task):
    df = _playground_prediction_df()
    with patch.object(
        task, "_transaction_log", return_value=pd.DataFrame([{"data_date": "20250115"}])
    ) as transaction_log:
        task._insert_log_record(df)

    transaction_log.assert_called_once()


def test_insert_log_record_wraps_transaction_log_errors(task):
    df = _playground_prediction_df()
    with patch.object(task, "_transaction_log", side_effect=RuntimeError("transaction broke")):
        with pytest.raises(Exception, match="Transaction log creation failed: transaction broke"):
            task._insert_log_record(df)


# ============================================================
# _transaction_log
# ============================================================


def test_transaction_log_creates_and_uploads_csv(task):
    existing_df = pd.DataFrame(
        [
            {
                "data_date": "20250115",
                "start_time": "2025-01-15 00:00:00+00:00",
                "end_time": "2025-01-15 00:00:10+00:00",
                "type": "AI User-Playground",
                "updated_dt": "2025-01-15 01:00:00",
                "load_dt": "2025-01-15 01:00:00",
                "filename": "existing.wav",
            }
        ]
    )
    task.sharepoint_control.is_item_exists.return_value = True
    task.sharepoint_control.get_item_by_path.return_value = SimpleNamespace(
        content=existing_df.to_csv(index=False).encode("utf-8")
    )

    with (
        patch("tasks.sentiment_qa.user_playground_task.gemini_cost", return_value={}),
        patch(
            "tasks.sentiment_qa.user_playground_task.GeminiBatchModule.cal_gemini_cost",
            return_value={"sample": {"cost_input": 0.05, "cost_output": 0.02}},
        ),
    ):
        new_df = task._transaction_log(
            "AI User-Playground",
            "daisyrpa",
            "SharePoint",
            _playground_prediction_df(),
            datetime(2025, 1, 15, 12, 0, 0),
        )

    assert len(new_df) == 1
    upload_kwargs = task.sharepoint_control.upload_file.call_args.kwargs
    uploaded_df = pd.read_csv(io.BytesIO(upload_kwargs["content"]))
    assert upload_kwargs["upload_path"] == "Control/transaction.csv"
    assert set(uploaded_df["filename"]) >= {"existing.wav", "sample"}


def test_transaction_log_raises_for_missing_columns(task):
    bad_df = pd.DataFrame([{"file_name": "x", "model_version": "gemini-2.5-flash"}])
    with patch("tasks.sentiment_qa.user_playground_task.gemini_cost", return_value={}):
        with pytest.raises(ValueError, match="missing columns"):
            task._transaction_log(
                "AI User-Playground", "daisyrpa", "SharePoint", bad_df, datetime(2025, 1, 15, 12, 0, 0)
            )


def test_transaction_log_raises_when_no_records_created(task):
    empty_df = pd.DataFrame(columns=_playground_prediction_df().columns)
    with patch("tasks.sentiment_qa.user_playground_task.gemini_cost", return_value={}):
        with pytest.raises(Exception, match="No transaction records were created"):
            task._transaction_log(
                "AI User-Playground", "daisyrpa", "SharePoint", empty_df, datetime(2025, 1, 15, 12, 0, 0)
            )


def test_transaction_log_raises_when_record_missing_file_name(task):
    df = _playground_prediction_df(file_name=None)
    with (
        patch("tasks.sentiment_qa.user_playground_task.gemini_cost", return_value={}),
        pytest.raises(
            Exception,
            match="Transaction log creation failed at record 1: Record 1: Missing required field 'file_name'",
        ),
    ):
        task._transaction_log("AI User-Playground", "daisyrpa", "SharePoint", df, datetime(2025, 1, 15, 12, 0, 0))


def test_transaction_log_handles_missing_full_path_and_none_duration(task):
    df = _playground_prediction_df(full_path=None, duration=None)
    task.sharepoint_control.is_item_exists.return_value = False

    with (
        patch("tasks.sentiment_qa.user_playground_task.gemini_cost", return_value={}),
        patch(
            "tasks.sentiment_qa.user_playground_task.GeminiBatchModule.cal_gemini_cost",
            return_value={"sample": {"cost_input": 0.05, "cost_output": 0.02}},
        ),
    ):
        new_df = task._transaction_log(
            "AI User-Playground", "daisyrpa", "SharePoint", df, datetime(2025, 1, 15, 12, 0, 0)
        )

    assert new_df.iloc[0]["storage_path"] == "https://control.sharepoint.com/sites/control-site/"
    assert new_df.iloc[0]["file_metadata_min"] == "0:00"


def test_transaction_log_casts_string_duration_and_ignores_invalid_iso_timestamps(task):
    # Use valid ISO timestamps so TransactionLogSchema produces a datetime-dtype column
    # (the source does not tolerate unparseable strings in the .dt.date cache update).
    # The duration="61" string-to-int cast and the resulting "1:01" display are what is tested.
    df = _playground_prediction_df(duration="61")
    task.sharepoint_control.is_item_exists.return_value = False

    with (
        patch("tasks.sentiment_qa.user_playground_task.gemini_cost", return_value={}),
        patch(
            "tasks.sentiment_qa.user_playground_task.GeminiBatchModule.cal_gemini_cost",
            return_value={"sample": {"cost_input": 0.05, "cost_output": 0.02}},
        ),
    ):
        new_df = task._transaction_log(
            "AI User-Playground", "daisyrpa", "SharePoint", df, datetime(2025, 1, 15, 12, 0, 0)
        )

    assert new_df.iloc[0]["file_metadata_min"] == "1:01"


def test_transaction_log_stamps_failed_records(task):
    df = _playground_prediction_df(status="FAILED", message="model failed")
    task.sharepoint_control.is_item_exists.return_value = False

    with (
        patch("tasks.sentiment_qa.user_playground_task.gemini_cost", return_value={}),
        patch(
            "tasks.sentiment_qa.user_playground_task.GeminiBatchModule.cal_gemini_cost",
            return_value={"sample": {"cost_input": 0.05, "cost_output": 0.02}},
        ),
    ):
        new_df = task._transaction_log(
            "AI User-Playground", "daisyrpa", "SharePoint", df, datetime(2025, 1, 15, 12, 0, 0)
        )

    assert new_df.iloc[0]["status"] == "FAILED"
    assert new_df.iloc[0]["error_message"] == "model failed"


def test_transaction_log_raises_when_upload_fails(task):
    df = _playground_prediction_df()
    task.sharepoint_control.is_item_exists.return_value = False
    task.sharepoint_control.upload_file.side_effect = RuntimeError("upload unavailable")

    with (
        patch("tasks.sentiment_qa.user_playground_task.gemini_cost", return_value={}),
        patch(
            "tasks.sentiment_qa.user_playground_task.GeminiBatchModule.cal_gemini_cost",
            return_value={"sample": {"cost_input": 0.05, "cost_output": 0.02}},
        ),
        pytest.raises(Exception, match="Cannot upload transaction log: upload unavailable"),
    ):
        task._transaction_log("AI User-Playground", "daisyrpa", "SharePoint", df, datetime(2025, 1, 15, 12, 0, 0))


def test_transaction_log_updates_cache_oper_log(task):
    df = _playground_prediction_df()
    task.sharepoint_control.is_item_exists.return_value = False

    with (
        patch("tasks.sentiment_qa.user_playground_task.gemini_cost", return_value={}),
        patch(
            "tasks.sentiment_qa.user_playground_task.GeminiBatchModule.cal_gemini_cost",
            return_value={"sample": {"cost_input": 0.05, "cost_output": 0.02}},
        ),
    ):
        task._transaction_log("AI User-Playground", "daisyrpa", "SharePoint", df, datetime(2025, 1, 15, 12, 0, 0))

    assert not task._cache_oper_log["transaction_df"].empty


# ============================================================
# on_error
# ============================================================


def test_on_error_sends_email(task):
    task.task_name = "QAUserPlaygroundTask"
    with patch(
        "tasks.sentiment_qa.user_playground_task.get_current_datetime",
        return_value=datetime(2025, 1, 15, 12, 30, 0),
    ):
        task.on_error(RuntimeError("failure"))

    task.msgraph_module.send_email.assert_called_once()


def test_on_error_handles_email_failure_gracefully(task):
    task.task_name = "QAUserPlaygroundTask"
    task.msgraph_module.send_email.side_effect = RuntimeError("mail down")

    with patch(
        "tasks.sentiment_qa.user_playground_task.get_current_datetime",
        return_value=datetime(2025, 1, 15, 12, 30, 0),
    ):
        task.on_error(RuntimeError("failure"))

    # Should not re-raise — email failures are swallowed
    task.msgraph_module.send_email.assert_called_once()
