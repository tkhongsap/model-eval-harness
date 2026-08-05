import io
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd
import pytest

from tasks.sentiment_qa.fact_check_task import FactCheckTask

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


@pytest.fixture
def mock_deps():
    with (
        patch("tasks.sentiment_qa.fact_check_task.SharePointModule") as sp_cls,
        patch("tasks.sentiment_qa.fact_check_task.GCSModule") as gcs_cls,
        patch("tasks.sentiment_qa.fact_check_task.GeminiBatchModule") as gemini_cls,
        patch("tasks.sentiment_qa.fact_check_task.MSGraphModule") as graph_cls,
        patch("tasks.sentiment_qa.fact_check_task.load_yaml") as load_yaml,
        patch("tasks.sentiment_qa.fact_check_task.resolve_env") as resolve_env,
        patch("tasks.sentiment_qa.fact_check_task.resolve_date") as resolve_date,
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
    instance = FactCheckTask()
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
            "ground_truth_file": "Control/GT.xlsx",
            "evaluation_folder": "Control/evaluation.xlsx",
            "transaction_log_file": "Control/transaction.csv",
        }
    }
    instance.framework = {"concurrency_upload": "2"}
    instance.metric_thresholds = {
        "accuracy": {"excellent": 90, "good": 80, "acceptable": 70},
        "precision": {"excellent": 90, "good": 80, "acceptable": 70},
        "recall": {"excellent": 90, "good": 80, "acceptable": 70},
        "f1_score": {"excellent": 90, "good": 80, "acceptable": 70},
    }
    instance.packages = {"execution_dt": datetime(2025, 1, 15, 12, 0, 0)}
    instance.get_package = lambda key, default=None: instance.packages.get(key, default)
    instance.sharepoint_control = Mock()
    instance.gcs_module = Mock()
    instance.gcs_module.project_id = "qa-proj"
    instance.gcs_module.bucket_name = "qa-bucket"
    instance.gemini_batch_module = Mock()
    instance.msgraph_module = Mock()
    instance.control_site = "control.sharepoint.com"
    return instance


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


def test_pre_execute_initializes_modules(mock_deps):
    instance = FactCheckTask()
    instance.gcs = {"project_id": "qa-proj", "bucket_name": "qa-bucket"}
    instance.vertexai = {"project_id": "qa-proj", "location": "us-central1"}

    instance.pre_execute()

    mock_deps["sharepoint"].assert_called_once()
    mock_deps["gcs"].assert_called_once_with(project_id="qa-proj", bucket_name="qa-bucket")
    mock_deps["gemini"].assert_called_once_with(genai_project_id="qa-proj", genai_location="us-central1")
    mock_deps["msgraph"].assert_called_once()


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


def test_proc_raw_prediction_covers_skip_error_missing_text_and_success(task):
    success_file = "gs://qa-bucket/processing/voice/202501/20250115/Complain/1111_0890000000_120000_A001_jane_doe_D_20250115_60.wav"
    raw_jsonl = [
        {"request": {}, "response": {}},
        _raw_line(success_file, status="FAILED_PRECONDITION", text='{"x": 1}'),
        _raw_line(success_file, text=None),
        _raw_line(success_file, text='{"service_quality": {"greeting_standard": "True"}}', model_version=None),
    ]

    with patch(
        "tasks.sentiment_qa.fact_check_task.GeminiBatchModule.sum_tokens_usage_for_billing",
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


def test_compute_eval_metrics_and_eval_status(task):
    metrics = task._compute_eval_metrics(TP=3, FP=1, FN=1, TN=5)

    assert metrics["accuracy"] == 80.0
    assert metrics["precision"] == 75.0
    assert metrics["recall"] == 75.0
    assert metrics["f1_score"] == 75.0
    assert metrics["weight"] == 4
    assert metrics["accuracy_status"] == "good"
    assert metrics["precision_status"] == "acceptable"
    assert task._eval_status(95, "accuracy") == "excellent"
    assert task._eval_status(82, "accuracy") == "good"
    assert task._eval_status(71, "accuracy") == "acceptable"
    assert task._eval_status(10, "accuracy") == "unacceptable"


def test_eval_status_validation_errors(task):
    task.metric_thresholds["accuracy"] = {"excellent": "90", "good": 80, "acceptable": 70}
    with pytest.raises(ValueError, match="Invalid threshold value"):
        task._eval_status(95, "accuracy")

    task.metric_thresholds["accuracy"] = {"excellent": 90, "bad": 80, "acceptable": 70}
    with pytest.raises(ValueError, match="Invalid status key"):
        task._eval_status(95, "accuracy")

    task.metric_thresholds.pop("accuracy")
    with pytest.raises(ValueError, match="No thresholds configured"):
        task._eval_status(95, "accuracy")


def test_evaluate_uploads_metrics_and_merges_existing_report(task):
    gt_df = pd.DataFrame(
        [
            {
                "filename": "1111111111111111111_2222222222_080808_33333333_HELLO_WORLD_T_20260501_531_IN.wav",
                "greeting_standard": "Meet",
                "manners": "Below",
            },
            {
                "filename": "2222222222222222222_3333333333_080808_44444444_HELLO_WORLD_T_20260501_600_IN.wav",
                "greeting_standard": "Meet",
                "manners": "Below",
            },
        ]
    )
    existing_df = pd.DataFrame(
        [
            {
                "created_datetime": "2025-01-14 10:00:00",
                "processed_datetime": "2025-01-14 10:05:00",
                "gcp_project_id": "qa-proj",
                "gcp_project_name": "qa-proj",
                "model_version": "gemini-2.5-flash",
                "ground_truth_count": 1,
                "prediction_count": 1,
                "dimension": "legacy",
                "label": "legacy",
                "accuracy": 100,
                "accuracy_status": "excellent",
                "precision": 100,
                "precision_status": "excellent",
                "recall": 100,
                "recall_status": "excellent",
                "f1_score": 100,
                "f1_score_status": "excellent",
                "TP": 1,
                "FP": 0,
                "FN": 0,
                "TN": 0,
                "weight": 1,
            }
        ]
    )

    gt_item = SimpleNamespace(content=_excel_bytes(gt_df, task.DEFAULT_GT_SHEET_NAME))
    existing_item = SimpleNamespace(content=_excel_bytes(existing_df, task.DEFAULT_GT_SHEET_NAME))

    def get_item_side(item_path):
        if item_path == "Control/GT.xlsx":
            return gt_item
        if item_path == "Control/evaluation.xlsx":
            return existing_item
        raise AssertionError(f"Unexpected path: {item_path}")

    task.sharepoint_control.get_item_by_path.side_effect = get_item_side
    task.sharepoint_control.is_item_exists.side_effect = lambda item_path: item_path == "Control/evaluation.xlsx"

    batch_results = [
        {
            "file_metadata": {
                "file_name": "1111111111111111111_2222222222_080808_33333333_HELLO_WORLD_T_20260501_531_IN.wav"
            },  # matches call_1001 after split
            "prediction": {
                "status": "SUCCESS",
                "model_version": "gemini-2.5-flash",
                "processed_time": "2025-01-15T12:05:00Z",
                "raw_prediction": {
                    "service_quality": {"greeting_standard": {"evaluation": "Meet"}, "manners": {"evaluation": "Below"}}
                },
            },
            "load_dt": "2025-01-15 12:06:00",
        },
        {
            "file_metadata": {
                "file_name": "2222222222222222222_3333333333_080808_44444444_HELLO_WORLD_T_20260501_600_IN.wav"
            },  # matches call_2002 after split
            "prediction": {
                "status": "SUCCESS",
                "model_version": "gemini-2.5-flash",
                "processed_time": "2025-01-15T12:05:00Z",
                "raw_prediction": {
                    "service_quality": {
                        "greeting_standard": {"evaluation": "Below"},
                        "manners": {"evaluation": "Below"},
                    }
                },
            },
            "load_dt": "2025-01-15 12:06:00",
        },
    ]

    with (
        patch("tasks.sentiment_qa.fact_check_task.GroundTruthSchema.validate", side_effect=lambda df: df),
        patch("tasks.sentiment_qa.fact_check_task.parse_datetime", return_value=datetime(2025, 1, 15, 19, 5, 0)),
    ):
        task._evaluate(batch_results)

    upload_kwargs = task.sharepoint_control.upload_file.call_args.kwargs
    uploaded_df = pd.read_excel(io.BytesIO(upload_kwargs["content"]), sheet_name=task.DEFAULT_GT_SHEET_NAME)

    assert upload_kwargs["upload_path"] == "Control/evaluation.xlsx"
    assert "legacy" in uploaded_df["label"].tolist()
    assert "weighted_avg" in uploaded_df["label"].tolist()
    greeting_row = uploaded_df[uploaded_df["label"] == "greeting_standard"].iloc[0]
    manners_row = uploaded_df[uploaded_df["label"] == "manners"].iloc[0]
    assert greeting_row["accuracy"] == 50
    assert manners_row["accuracy"] == 100


def test_post_execute_skips_when_no_result(task):
    with (
        patch.object(task, "_archive_and_cleanup") as archive_cleanup,
        patch.object(task, "_insert_log_record") as insert_log,
    ):
        result = task.post_execute(None)

    assert result is None
    archive_cleanup.assert_not_called()
    insert_log.assert_not_called()


def test_post_execute_archives_and_logs_result_dataframe(task):
    result = [
        {
            "file_metadata": {
                "file_name": "sample",
                "file_ext": ".wav",
                "record_date": "20250115",
                "duration": 60,
            },
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

    with (
        patch.object(task, "_archive_and_cleanup") as archive_cleanup,
        patch.object(task, "_insert_log_record") as insert_log,
    ):
        output = task.post_execute(result)

    assert output == result
    archive_cleanup.assert_called_once_with(task.packages["execution_dt"])
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


def test_retrieve_prediction_step_runs_evaluate(task):
    with (
        patch(
            "tasks.sentiment_qa.fact_check_task.GeminiBatchModule.retrieve_batch_results",
            side_effect=[[{"row": 1}], [{"row": 2}]],
        ),
        patch.object(
            task,
            "_proc_raw_prediction",
            side_effect=[
                [{"prediction": {"status": "SUCCESS"}}],
                [{"prediction": {"status": "FAILED"}}],
            ],
        ) as proc_raw,
        patch.object(task, "_evaluate") as evaluate,
    ):
        results = task._retrieve_prediction_step(
            ["output/run1/predictions.jsonl", "output/run2/predictions.jsonl"],
            datetime(2025, 1, 15, 12, 0, 0),
        )

    assert len(results) == 2
    assert proc_raw.call_count == 2
    evaluate.assert_called_once_with(results)


def test_retrieve_prediction_step_raises_when_all_failed(task):
    with (
        patch(
            "tasks.sentiment_qa.fact_check_task.GeminiBatchModule.retrieve_batch_results",
            return_value=[{"row": 1}],
        ),
        patch.object(task, "_proc_raw_prediction", return_value=[{"prediction": {"status": "FAILED"}}]),
        patch.object(task, "_evaluate"),
        pytest.raises(Exception, match=r"prediction\(s\) failed"),
    ):
        task._retrieve_prediction_step(["output/run1/predictions.jsonl"], datetime(2025, 1, 15, 12, 0, 0))


def test_submit_job_step_builds_payload_uploads_and_submits(task):
    task.framework["concurrency_upload"] = "2"
    task.vertexai["generation_config"] = {"temperature": 0.1}
    task.vertexai["batch_job_name"] = "fact-check-%{DATA_DATE_YYYYMMDDHHMMSS}"
    task.sharepoint["control"]["input_folder_list"] = "Enquiry,Complain"
    task.gcs_module.list_files.side_effect = [
        [
            "input/Enquiry/1111_0890000000_120000_A001_jane_doe_D_20250115_60_IN.wav",
            "input/Enquiry/2222_0890000000_120000_A001_jane_doe_D_20250115_60_IN.txt",
        ],
        [
            "processing/voice/%{DATA_DATE_YYYYMMDDHHMMSS}/1111_0890000000_120000_A001_jane_doe_D_20250115_60_IN.wav",
            "processing/voice/%{DATA_DATE_YYYYMMDDHHMMSS}/2222_0890000000_120000_A001_jane_doe_D_20250115_60_IN.txt",
        ],
    ]
    task.gcs_module.is_file_exists.return_value = True
    task.gemini_batch_module.create_batch_job.return_value = SimpleNamespace(
        name="projects/p/locations/l/batchPredictionJobs/123",
        display_name="fact-check-job",
    )
    task.gemini_batch_module.status_check_batch_job.return_value = "JOB_STATE_RUNNING"

    prep_task = Mock()
    prep_task.pre_execute.return_value = None
    prep_task._prepare_prompt.return_value = "Prompt for {date}"
    prep_task._get_analysis_schema.return_value = {"response_mime_type": "application/json"}

    with (
        patch.object(task, "_upload_voice_files") as upload_voice_files,
        patch("tasks.sentiment_qa.fact_check_task.PrepPayloadTask", return_value=prep_task),
        patch("tasks.sentiment_qa.fact_check_task.time.sleep"),
        patch(
            "tasks.sentiment_qa.fact_check_task.asyncio.run",
            return_value={"success": 2, "failed": 1, "errors": ["copy failed"]},
        ),
    ):
        payload_path, output_folder, processing_logs = task._submit_job_step(
            datetime(2025, 1, 15, 12, 0, 0),
            "2025-01-15",
        )

    upload_voice_files.assert_called_once()
    task.gcs_module.update_content_to_gcs.assert_called_once()
    task.gemini_batch_module.create_batch_job.assert_called_once()
    assert payload_path.endswith("payloads.jsonl")
    assert output_folder == "output/%{DATA_DATE_YYYYMMDDHHMMSS}"
    assert len(processing_logs) == 2
    assert all(log.batch_job_id == "123" for log in processing_logs)
    uploaded_payload = task.gcs_module.update_content_to_gcs.call_args.kwargs["content"].decode("utf-8")
    assert "1111_0890000000_120000_A001_jane_doe_D_20250115_60_IN.wav" in uploaded_payload
    assert "2222_0890000000_120000_A001_jane_doe_D_20250115_60_IN.txt" in uploaded_payload


def test_upload_voice_files_filters_to_wav_and_builds_stream_list(task):
    task.sharepoint["control"]["input_folder_list"] = "Enquiry,Complain"
    task.sharepoint["control"]["source_folder"] = "FactCheck/Voice"
    task.gcs["input_folder"] = "input"

    def folder_exists(path):
        return path == "FactCheck/Voice/Enquiry"

    task.sharepoint_control.is_item_exists.side_effect = folder_exists
    task.sharepoint_control.list_files.return_value = [
        {
            "name": "call-1.wav",
            "id": "1",
            "parentReference": {
                "path": "/drive/root:/test_sentiment_batch_callcenterqa/fact_check/prediction/input/Enquiry"
            },
            "createdDateTime": "2025-01-15T12:00:00Z",
        },
        {
            "name": "ignore.txt",
            "id": "2",
            "parentReference": {
                "path": "/drive/root:/test_sentiment_batch_callcenterqa/fact_check/prediction/input/Enquiry"
            },
            "createdDateTime": "2025-01-15T12:01:00Z",
        },
    ]

    with patch("tasks.sentiment_qa.fact_check_task.asyncio.run", return_value={"success": 1, "failed": 0}):
        task._upload_voice_files()

    stream_list = task.gcs_module.upload_sharepoint_to_gcs.call_args.kwargs["stream_list"]
    assert stream_list == [
        {
            "download": "/test_sentiment_batch_callcenterqa/fact_check/prediction/input/Enquiry/call-1.wav",
            "upload": "input/Enquiry/call-1.wav",
            "mime_type": "audio/wav",
        }
    ]


def test_build_checklist_text_formats_hierarchy(task):
    category_df = pd.DataFrame(
        [
            {
                "main_category_no": 1,
                "main_category_name": "Opening",
                "commission_skill": "Retention",
                "section": "Purpose",
                "content": "Open the call",
                "order": 1,
            }
        ]
    )
    subcategory_df = pd.DataFrame(
        [
            {
                "sub_category_no": "1.1",
                "sub_category_name": "Greeting",
                "section": "Goal",
                "content": "Use polite greeting",
                "order": 1,
            }
        ]
    )
    tags_df = pd.DataFrame(
        [
            {
                "item_no": "1.1.1",
                "tag_code": "GREETING",
                "rule_and_logic": "Say hello",
                "positive_example_th": "Good morning",
                "negative_example_th": "Yo",
                "is_active": True,
            }
        ]
    )

    text = task._build_checklist_text(category_df, subcategory_df, tags_df)

    assert "### Main Category 1: Opening - Retention Campaign" in text
    assert "#### Sub-Category 1.1: Greeting" in text
    assert "##### 1.1.1. GREETING" in text
    assert "**positive_example_th:**" in text
    assert "Good morning" in text


def _fact_check_prediction_df():
    return pd.DataFrame(
        [
            {
                "file_name": "sample",
                "full_path": "FactCheck/Voice/sample.wav",
                "folder": "FactCheck/Voice",
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
        ]
    )


def test_insert_log_record_calls_transaction_log(task):
    df = _fact_check_prediction_df()
    with patch.object(
        task, "_transaction_log", return_value=pd.DataFrame([{"data_date": "20250115"}])
    ) as transaction_log:
        task._insert_log_record(df)

    transaction_log.assert_called_once()


def test_transaction_log_creates_and_uploads_csv(task):
    existing_df = pd.DataFrame(
        [
            {
                "data_date": "20250115",
                "start_time": "2025-01-15 00:00:00+00:00",
                "end_time": "2025-01-15 00:00:10+00:00",
                "type": "AI Fact-Checker",
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
        patch("tasks.sentiment_qa.fact_check_task.gemini_cost", return_value={}),
        patch(
            "tasks.sentiment_qa.fact_check_task.GeminiBatchModule.cal_gemini_cost",
            return_value={"sample": {"cost_input": 0.05, "cost_output": 0.02}},
        ),
    ):
        new_df = task._transaction_log(
            "AI Fact-Checker",
            "daisyrpa",
            "SharePoint",
            _fact_check_prediction_df(),
            datetime(2025, 1, 15, 12, 0, 0),
        )

    assert len(new_df) == 1
    upload_kwargs = task.sharepoint_control.upload_file.call_args.kwargs
    uploaded_df = pd.read_csv(io.BytesIO(upload_kwargs["content"]))
    assert upload_kwargs["upload_path"] == "Control/transaction.csv"
    assert set(uploaded_df["filename"]) >= {"existing.wav", "sample"}


def test_on_error_sends_email(task):
    task.task_name = "QAFactCheckTask"
    with patch(
        "tasks.sentiment_qa.fact_check_task.get_current_datetime",
        return_value=datetime(2025, 1, 15, 12, 30, 0),
    ):
        task.on_error(RuntimeError("failure"))

    task.msgraph_module.send_email.assert_called_once()


def test_retrieve_prediction_step_wraps_evaluate_failure(task):
    with (
        patch(
            "tasks.sentiment_qa.fact_check_task.GeminiBatchModule.retrieve_batch_results",
            return_value=[{"row": 1}],
        ),
        patch.object(task, "_proc_raw_prediction", return_value=[{"prediction": {"status": "SUCCESS"}}]),
        patch.object(task, "_evaluate", side_effect=RuntimeError("eval broke")),
    ):
        with pytest.raises(Exception, match="Evaluation step failed"):
            task._retrieve_prediction_step(["output/run1/predictions.jsonl"], datetime(2025, 1, 15, 12, 0, 0))


def test_submit_job_step_returns_none_when_no_supported_payloads(task):
    task.framework["concurrency_upload"] = "2"
    task.sharepoint["control"]["input_folder_list"] = "Enquiry"
    task.gcs_module.list_files.side_effect = [
        ["input/1111_0890000000_120000_A001_jane_doe_D_20250115_60_IN.bin"],
        ["processing/1111_0890000000_120000_A001_jane_doe_D_20250115_60_IN.bin"],
    ]

    prep_task = Mock()
    prep_task.pre_execute.return_value = None
    prep_task._prepare_prompt.return_value = "Prompt for {date}"
    prep_task._get_analysis_schema.return_value = {"response_mime_type": "application/json"}

    with (
        patch.object(task, "_upload_voice_files"),
        patch("tasks.sentiment_qa.fact_check_task.PrepPayloadTask", return_value=prep_task),
        patch(
            "tasks.sentiment_qa.fact_check_task.asyncio.run",
            return_value={"success": 1, "failed": 0},
        ),
    ):
        payload_path, output_folder, processing_logs = task._submit_job_step(
            datetime(2025, 1, 15, 12, 0, 0),
            "2025-01-15",
        )

    assert payload_path is None
    assert output_folder is None
    assert len(processing_logs) == 0


def test_submit_job_step_raises_when_status_check_fails(task):
    task.framework["concurrency_upload"] = "2"
    task.sharepoint["control"]["input_folder_list"] = "Enquiry"
    task.gcs_module.list_files.side_effect = [
        ["input/1111_0890000000_120000_A001_jane_doe_D_20250115_60_IN.wav"],
        ["processing/1111_0890000000_120000_A001_jane_doe_D_20250115_60_IN.wav"],
    ]
    task.gcs_module.is_file_exists.return_value = True
    task.gemini_batch_module.create_batch_job.return_value = SimpleNamespace(
        name="projects/p/locations/l/batchPredictionJobs/123",
        display_name="fact-check-job",
    )
    task.gemini_batch_module.status_check_batch_job.return_value = "JOB_STATE_FAILED"

    prep_task = Mock()
    prep_task.pre_execute.return_value = None
    prep_task._prepare_prompt.return_value = "Prompt for {date}"
    prep_task._get_analysis_schema.return_value = {"response_mime_type": "application/json"}

    with (
        patch.object(task, "_upload_voice_files"),
        patch("tasks.sentiment_qa.fact_check_task.PrepPayloadTask", return_value=prep_task),
        patch("tasks.sentiment_qa.fact_check_task.time.sleep"),
        patch(
            "tasks.sentiment_qa.fact_check_task.asyncio.run",
            return_value={"success": 1, "failed": 0},
        ),
        pytest.raises(Exception, match="Batch job failed immediately"),
    ):
        task._submit_job_step(datetime(2025, 1, 15, 12, 0, 0), "2025-01-15")


def test_transaction_log_raises_for_missing_columns(task):
    bad_df = pd.DataFrame([{"file_name": "x", "model_version": "gemini-2.5-flash"}])
    with patch("tasks.sentiment_qa.fact_check_task.gemini_cost", return_value={}):
        with pytest.raises(ValueError, match="missing columns"):
            task._transaction_log("AI Fact-Checker", "daisyrpa", "SharePoint", bad_df, datetime(2025, 1, 15, 12, 0, 0))


def test_proc_raw_prediction_extracts_date_from_path_and_defaults_token_usage(task):
    file_uri = "gs://qa-bucket/processing/voice/20250115/not_enough_parts.wav"
    raw_jsonl = [_raw_line(file_uri, text='{"service_quality": {"greeting_standard": "True"}}')]

    with patch(
        "tasks.sentiment_qa.fact_check_task.GeminiBatchModule.sum_tokens_usage_for_billing",
        side_effect=RuntimeError("usage missing"),
    ):
        results = task._proc_raw_prediction(
            "output/run-1/predictions.jsonl", raw_jsonl, datetime(2025, 1, 15, 12, 0, 0)
        )

    assert results[0]["file_metadata"]["record_date"] == "20250115"
    assert results[0]["prediction"]["token_input"] == {"text": 0, "audio": 0}
    assert results[0]["prediction"]["token_output"] == {"text": 0}
    assert results[0]["prediction"]["token_cached"] == 0


def test_on_error_handles_email_failure(task):
    task.task_name = "QAFactCheckTask"
    task.msgraph_module.send_email.side_effect = RuntimeError("mail down")
    with patch(
        "tasks.sentiment_qa.fact_check_task.get_current_datetime",
        return_value=datetime(2025, 1, 15, 12, 30, 0),
    ):
        task.on_error(RuntimeError("failure"))

    task.msgraph_module.send_email.assert_called_once()


def _make_submit_prep_task():
    prep_task = Mock()
    prep_task.pre_execute.return_value = None
    prep_task._prepare_prompt.return_value = "Prompt for {date}"
    prep_task._get_analysis_schema.return_value = {"response_mime_type": "application/json"}
    return prep_task


def _single_prediction_df(**overrides):
    row = _fact_check_prediction_df().iloc[0].to_dict()
    row.update(overrides)
    return pd.DataFrame([row])


@pytest.mark.parametrize("dependency_key", ["sharepoint", "gcs", "gemini", "msgraph"])
def test_pre_execute_raises_when_module_initialization_fails(mock_deps, dependency_key):
    instance = FactCheckTask()
    instance.gcs = {"project_id": "qa-proj", "bucket_name": "qa-bucket"}
    instance.vertexai = {"project_id": "qa-proj", "location": "us-central1"}
    mock_deps[dependency_key].side_effect = RuntimeError(f"{dependency_key} init failed")

    with pytest.raises(RuntimeError, match=f"{dependency_key} init failed"):
        instance.pre_execute()


def test_retrieve_prediction_step_continues_after_batch_retrieval_error(task):
    with (
        patch(
            "tasks.sentiment_qa.fact_check_task.GeminiBatchModule.retrieve_batch_results",
            side_effect=[RuntimeError("first batch broke"), [{"row": 1}]],
        ),
        patch.object(task, "_proc_raw_prediction", return_value=[{"prediction": {"status": "SUCCESS"}}]) as proc_raw,
        patch.object(task, "_evaluate") as evaluate,
    ):
        results = task._retrieve_prediction_step(
            ["output/bad/predictions.jsonl", "output/good/predictions.jsonl"],
            datetime(2025, 1, 15, 12, 0, 0),
        )

    assert results == [{"prediction": {"status": "SUCCESS"}}]
    proc_raw.assert_called_once()
    evaluate.assert_called_once_with(results)


def test_retrieve_prediction_step_raises_when_all_batch_retrievals_fail(task):
    with (
        patch(
            "tasks.sentiment_qa.fact_check_task.GeminiBatchModule.retrieve_batch_results",
            side_effect=RuntimeError("gcs unavailable"),
        ),
        pytest.raises(Exception, match="All batch files failed to retrieve"),
    ):
        task._retrieve_prediction_step(["output/run1/predictions.jsonl"], datetime(2025, 1, 15, 12, 0, 0))


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
        patch(
            "tasks.sentiment_qa.fact_check_task.asyncio.run",
            side_effect=RuntimeError("copy failed"),
        ),
        pytest.raises(RuntimeError, match="copy failed"),
    ):
        task._submit_job_step(datetime(2025, 1, 15, 12, 0, 0), "2025-01-15")


def test_submit_job_step_raises_when_processing_folder_is_empty(task):
    task.framework["concurrency_upload"] = "2"
    task.gcs_module.list_files.side_effect = [["input/file.wav"], []]

    with (
        patch.object(task, "_upload_voice_files"),
        patch(
            "tasks.sentiment_qa.fact_check_task.asyncio.run",
            return_value={"success": 1, "failed": 0},
        ),
        pytest.raises(Exception, match="No files in processing folder"),
    ):
        task._submit_job_step(datetime(2025, 1, 15, 12, 0, 0), "2025-01-15")


def test_submit_job_step_wraps_prompt_mapping_failures(task):
    task.framework["concurrency_upload"] = "2"
    task.gcs_module.list_files.side_effect = [["input/file.wav"], ["processing/file.wav"]]
    prep_task = _make_submit_prep_task()
    prep_task.pre_execute.side_effect = RuntimeError("prompt load failed")

    with (
        patch.object(task, "_upload_voice_files"),
        patch("tasks.sentiment_qa.fact_check_task.PrepPayloadTask", return_value=prep_task),
        patch(
            "tasks.sentiment_qa.fact_check_task.asyncio.run",
            return_value={"success": 1, "failed": 0},
        ),
        pytest.raises(Exception, match="Cannot load prompt mappings: prompt load failed"),
    ):
        task._submit_job_step(datetime(2025, 1, 15, 12, 0, 0), "2025-01-15")


def test_submit_job_step_records_payload_creation_errors(task):
    task.framework["concurrency_upload"] = "2"
    task.gcs_module.list_files.side_effect = [["input/file.wav"], ["processing/file.wav"]]
    prep_task = _make_submit_prep_task()
    prep_task._get_analysis_schema.return_value = None

    with (
        patch.object(task, "_upload_voice_files"),
        patch("tasks.sentiment_qa.fact_check_task.PrepPayloadTask", return_value=prep_task),
        patch(
            "tasks.sentiment_qa.fact_check_task.asyncio.run",
            return_value={"success": 1, "failed": 0},
        ),
    ):
        payload_path, output_folder, processing_logs = task._submit_job_step(
            datetime(2025, 1, 15, 12, 0, 0),
            "2025-01-15",
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
    prep_task = _make_submit_prep_task()

    with (
        patch.object(task, "_upload_voice_files"),
        patch("tasks.sentiment_qa.fact_check_task.PrepPayloadTask", return_value=prep_task),
        patch(
            "tasks.sentiment_qa.fact_check_task.asyncio.run",
            return_value={"success": 1, "failed": 0},
        ),
        pytest.raises(Exception, match="Cannot upload payload to GCS: upload failed"),
    ):
        task._submit_job_step(datetime(2025, 1, 15, 12, 0, 0), "2025-01-15")


def test_submit_job_step_raises_when_uploaded_payload_file_is_missing(task):
    task.framework["concurrency_upload"] = "2"
    task.gcs_module.list_files.side_effect = [
        ["input/1111_0890000000_120000_A001_jane_doe_D_20250115_60_IN.wav"],
        ["processing/1111_0890000000_120000_A001_jane_doe_D_20250115_60_IN.wav"],
    ]
    task.gcs_module.is_file_exists.return_value = False
    prep_task = _make_submit_prep_task()

    with (
        patch.object(task, "_upload_voice_files"),
        patch("tasks.sentiment_qa.fact_check_task.PrepPayloadTask", return_value=prep_task),
        patch(
            "tasks.sentiment_qa.fact_check_task.asyncio.run",
            return_value={"success": 1, "failed": 0},
        ),
        pytest.raises(Exception, match="Payload file not found"),
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
    prep_task = _make_submit_prep_task()

    with (
        patch.object(task, "_upload_voice_files"),
        patch("tasks.sentiment_qa.fact_check_task.PrepPayloadTask", return_value=prep_task),
        patch("tasks.sentiment_qa.fact_check_task.time.sleep"),
        patch(
            "tasks.sentiment_qa.fact_check_task.asyncio.run",
            return_value={"success": 1, "failed": 0},
        ),
    ):
        _, _, processing_logs = task._submit_job_step(datetime(2025, 1, 15, 12, 0, 0), "2025-01-15")

    assert len(processing_logs) == 1
    assert processing_logs[0].batch_job_id is None
    assert processing_logs[0].batch_job_display_name == "fallback-display-name"
    assert processing_logs[0].model_name == "gemini-2.5-flash"


def test_upload_voice_files_handles_empty_source_folder(task):
    task.sharepoint["control"]["input_folder_list"] = "Enquiry"
    task.sharepoint["control"]["source_folder"] = "FactCheck/Voice"
    task.gcs["input_folder"] = "input"
    task.sharepoint_control.is_item_exists.return_value = True
    task.sharepoint_control.list_files.return_value = []

    with patch("tasks.sentiment_qa.fact_check_task.asyncio.run", return_value={"success": 0, "failed": 0}):
        task._upload_voice_files()

    assert task.gcs_module.upload_sharepoint_to_gcs.call_args.kwargs["stream_list"] == []


def test_proc_raw_prediction_uses_default_record_date_when_date_extraction_errors(task):
    raw_jsonl = [
        _raw_line(
            "gs://qa-bucket/processing/voice/20250115/1111_0890000000_120000_A001_jane_doe_D_20250115_60.wav",
            text='{"service_quality": {"greeting_standard": "True"}}',
        )
    ]

    with (
        patch("tasks.sentiment_qa.fact_check_task.re.match", side_effect=RuntimeError("regex blew up")),
        patch(
            "tasks.sentiment_qa.fact_check_task.GeminiBatchModule.sum_tokens_usage_for_billing",
            return_value={"token_input": {"text": 1, "audio": 0}, "token_output": {"text": 1}, "token_cached": 0},
        ),
    ):
        results = task._proc_raw_prediction(
            "output/run-1/predictions.jsonl", raw_jsonl, datetime(2025, 1, 15, 12, 0, 0)
        )

    assert results[0]["file_metadata"]["record_date"] == task.DEFAULT_RECORD_DATE


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
        patch(
            "tasks.sentiment_qa.fact_check_task.safe_list_get",
            side_effect=safe_list_get_side_effect,
        ),
        patch(
            "tasks.sentiment_qa.fact_check_task.GeminiBatchModule.sum_tokens_usage_for_billing",
            return_value={"token_input": {"text": 1, "audio": 0}, "token_output": {"text": 1}, "token_cached": 0},
        ),
    ):
        results = task._proc_raw_prediction(
            "output/run-1/predictions.jsonl", raw_jsonl, datetime(2025, 1, 15, 12, 0, 0)
        )

    assert results[0]["file_metadata"]["file_ext"] is None
    assert results[0]["file_metadata"]["record_date"] == task.DEFAULT_RECORD_DATE


def test_proc_raw_prediction_ignores_usage_errors_for_failed_and_missing_predictions(task):
    file_uri = "gs://qa-bucket/processing/voice/20250115/file.wav"
    raw_jsonl = [
        _raw_line(file_uri, status="FAILED_PRECONDITION", text='{"x": 1}'),
        _raw_line(file_uri, text=None),
    ]

    with patch(
        "tasks.sentiment_qa.fact_check_task.GeminiBatchModule.sum_tokens_usage_for_billing",
        side_effect=RuntimeError("usage unavailable"),
    ):
        results = task._proc_raw_prediction(
            "output/run-1/predictions.jsonl", raw_jsonl, datetime(2025, 1, 15, 12, 0, 0)
        )

    assert "token_input" not in results[0]["prediction"]
    assert "token_input" not in results[1]["prediction"]


def test_proc_raw_prediction_skips_lines_that_raise_unexpected_errors(task):
    raw_jsonl = [_raw_line("gs://qa-bucket/processing/voice/20250115/file.wav", text='{"x": 1}')]

    with patch("tasks.sentiment_qa.fact_check_task.recursive_dict_value_by_key", side_effect=RuntimeError("bad line")):
        results = task._proc_raw_prediction(
            "output/run-1/predictions.jsonl", raw_jsonl, datetime(2025, 1, 15, 12, 0, 0)
        )

    assert results == []


def test_proc_raw_prediction_uses_default_record_date_when_no_date_is_found(task):
    raw_jsonl = [
        _raw_line(
            "gs://qa-bucket/processing/voice/no-date-here/file.wav",
            text='{"service_quality": {"greeting_standard": "True"}}',
        )
    ]

    with patch(
        "tasks.sentiment_qa.fact_check_task.GeminiBatchModule.sum_tokens_usage_for_billing",
        return_value={"token_input": {"text": 1, "audio": 0}, "token_output": {"text": 1}, "token_cached": 0},
    ):
        results = task._proc_raw_prediction(
            "output/run-1/predictions.jsonl", raw_jsonl, datetime(2025, 1, 15, 12, 0, 0)
        )

    assert results[0]["file_metadata"]["record_date"] == task.DEFAULT_RECORD_DATE


def test_evaluate_falls_back_to_execution_dt_when_load_dt_is_invalid_and_weight_is_zero(task):
    gt_df = pd.DataFrame([{"filename": "1001.wav", "greeting_standard": "True"}])
    gt_item = SimpleNamespace(content=_excel_bytes(gt_df, task.DEFAULT_GT_SHEET_NAME))
    task.sharepoint_control.get_item_by_path.return_value = gt_item
    task.sharepoint_control.is_item_exists.return_value = False
    batch_results = [
        {
            "file_metadata": {"file_name": "1001_call"},
            "prediction": {
                "status": "SUCCESS",
                "model_version": "gemini-2.5-flash",
                "processed_time": "2025-01-15T12:05:00Z",
                "raw_prediction": {"service_quality": {"greeting_standard": "False"}},
            },
            "load_dt": "invalid-load-datetime",
        }
    ]

    with (
        patch("tasks.sentiment_qa.fact_check_task.GroundTruthSchema.validate", side_effect=lambda df: df),
        patch(
            "tasks.sentiment_qa.fact_check_task.parse_datetime",
            return_value=datetime(2025, 1, 15, 19, 5, 0),
        ),
    ):
        task._evaluate(batch_results)

    uploaded_df = pd.read_excel(
        io.BytesIO(task.sharepoint_control.upload_file.call_args.kwargs["content"]),
        sheet_name=task.DEFAULT_GT_SHEET_NAME,
    )
    weighted_row = uploaded_df[uploaded_df["label"] == "weighted_avg"].iloc[0]

    assert weighted_row["accuracy"] == 0
    assert weighted_row["accuracy_status"] == "unacceptable"
    assert weighted_row["created_datetime"] == "2025-01-15 12:00:00"


def test_evaluate_uses_execution_dt_when_load_dt_missing_and_existing_report_cannot_be_loaded(task):
    gt_df = pd.DataFrame([{"filename": "1001.wav", "greeting_standard": "True"}])
    gt_item = SimpleNamespace(content=_excel_bytes(gt_df, task.DEFAULT_GT_SHEET_NAME))

    def get_item_side(path):
        if path == "Control/GT.xlsx":
            return gt_item
        if path == "Control/evaluation.xlsx":
            raise RuntimeError("broken workbook")
        raise AssertionError(f"Unexpected path: {path}")

    task.sharepoint_control.get_item_by_path.side_effect = get_item_side
    task.sharepoint_control.is_item_exists.side_effect = lambda path: path == "Control/evaluation.xlsx"
    batch_results = [
        {
            "file_metadata": {"file_name": "1001_call"},
            "prediction": {
                "status": "SUCCESS",
                "model_version": "gemini-2.5-flash",
                "processed_time": "2025-01-15T12:05:00Z",
                "raw_prediction": {"service_quality": {"greeting_standard": "True"}},
            },
            "load_dt": None,
        }
    ]

    with (
        patch("tasks.sentiment_qa.fact_check_task.GroundTruthSchema.validate", side_effect=lambda df: df),
        patch(
            "tasks.sentiment_qa.fact_check_task.parse_datetime",
            return_value=datetime(2025, 1, 15, 19, 5, 0),
        ),
    ):
        task._evaluate(batch_results)

    uploaded_df = pd.read_excel(
        io.BytesIO(task.sharepoint_control.upload_file.call_args.kwargs["content"]),
        sheet_name=task.DEFAULT_GT_SHEET_NAME,
    )

    assert uploaded_df.iloc[0]["created_datetime"] == "2025-01-15 12:00:00"


def test_post_execute_skips_logging_when_all_records_fail_to_build(task):
    result = [{"file_metadata": {"file_name": "sample", "file_ext": ".wav"}, "prediction": {"status": "SUCCESS"}}]

    with (
        patch("tasks.sentiment_qa.fact_check_task.resolve_env", side_effect=RuntimeError("bad source path")),
        patch.object(task, "_archive_and_cleanup") as archive_cleanup,
        patch.object(task, "_insert_log_record") as insert_log,
    ):
        output = task.post_execute(result)

    assert output == result
    archive_cleanup.assert_called_once_with(task.packages["execution_dt"])
    insert_log.assert_not_called()


def test_post_execute_raises_when_transaction_log_insertion_fails(task):
    result = [
        {
            "file_metadata": {"file_name": "sample", "file_ext": ".wav", "record_date": "20250115", "duration": 60},
            "prediction": {"status": "SUCCESS", "model_version": "gemini-2.5-flash"},
            "load_dt": "2025-01-15 12:05:00",
        }
    ]

    with (
        patch.object(task, "_archive_and_cleanup"),
        patch.object(task, "_insert_log_record", side_effect=RuntimeError("log insert failed")),
        pytest.raises(Exception, match="Transaction log insertion failed: log insert failed"),
    ):
        task.post_execute(result)


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


def test_archive_and_cleanup_skips_cleanup_dirs_that_cannot_be_resolved(task):
    def resolve_env_side_effect(value):
        if value == "input":
            raise RuntimeError("missing input dir")
        if value == "processing/voice/%{DATA_DATE_YYYYMMDDHHMMSS}":
            raise RuntimeError("missing processing dir")
        return value

    task.gcs_module.list_files.return_value = []
    task.gcs_module.is_dir_exists.return_value = False

    with patch("tasks.sentiment_qa.fact_check_task.resolve_env", side_effect=resolve_env_side_effect):
        task._archive_and_cleanup(datetime(2025, 1, 15, 12, 0, 0))

    delete_candidates = [call.kwargs["dir_path"] for call in task.gcs_module.is_dir_exists.call_args_list]
    assert delete_candidates == ["output"]


def test_insert_log_record_wraps_transaction_log_errors(task):
    with patch.object(task, "_transaction_log", side_effect=RuntimeError("transaction broke")):
        with pytest.raises(Exception, match="Transaction log creation failed: transaction broke"):
            task._insert_log_record(_fact_check_prediction_df())


def test_transaction_log_raises_when_record_missing_file_name(task):
    df = _single_prediction_df(file_name=None)

    with (
        patch("tasks.sentiment_qa.fact_check_task.gemini_cost", return_value={}),
        pytest.raises(
            Exception, match="Transaction log creation failed at record 1: Record 1: Missing required field 'file_name'"
        ),
    ):
        task._transaction_log("AI Fact-Checker", "daisyrpa", "SharePoint", df, datetime(2025, 1, 15, 12, 0, 0))


def test_transaction_log_handles_missing_full_path_and_none_duration(task):
    df = _single_prediction_df(full_path=None, duration=None)
    task.sharepoint_control.is_item_exists.return_value = False

    with (
        patch("tasks.sentiment_qa.fact_check_task.gemini_cost", return_value={}),
        patch(
            "tasks.sentiment_qa.fact_check_task.GeminiBatchModule.cal_gemini_cost",
            return_value={"sample": {"cost_input": 0.05, "cost_output": 0.02}},
        ),
    ):
        new_df = task._transaction_log("AI Fact-Checker", "daisyrpa", "SharePoint", df, datetime(2025, 1, 15, 12, 0, 0))

    assert new_df.iloc[0]["storage_path"] == "https://control.sharepoint.com/sites/control-site/"
    assert new_df.iloc[0]["file_metadata_min"] == "0:00"


def test_transaction_log_casts_string_duration_and_ignores_invalid_iso_timestamps(task):
    df = _single_prediction_df(duration="61", processed_time="not-a-date", create_time="still-not-a-date")
    task.sharepoint_control.is_item_exists.return_value = False

    with (
        patch("tasks.sentiment_qa.fact_check_task.gemini_cost", return_value={}),
        patch(
            "tasks.sentiment_qa.fact_check_task.GeminiBatchModule.cal_gemini_cost",
            return_value={"sample": {"cost_input": 0.05, "cost_output": 0.02}},
        ),
    ):
        new_df = task._transaction_log("AI Fact-Checker", "daisyrpa", "SharePoint", df, datetime(2025, 1, 15, 12, 0, 0))

    assert new_df.iloc[0]["file_metadata_min"] == "1:01"


def test_transaction_log_stamps_failed_records(task):
    df = _single_prediction_df(status="FAILED", message="model failed")
    task.sharepoint_control.is_item_exists.return_value = False

    with (
        patch("tasks.sentiment_qa.fact_check_task.gemini_cost", return_value={}),
        patch(
            "tasks.sentiment_qa.fact_check_task.GeminiBatchModule.cal_gemini_cost",
            return_value={"sample": {"cost_input": 0.05, "cost_output": 0.02}},
        ),
    ):
        new_df = task._transaction_log("AI Fact-Checker", "daisyrpa", "SharePoint", df, datetime(2025, 1, 15, 12, 0, 0))

    assert new_df.iloc[0]["status"] == "FAILED"
    assert new_df.iloc[0]["error_message"] == "model failed"


def test_transaction_log_raises_when_no_records_are_created(task):
    empty_df = pd.DataFrame(columns=_fact_check_prediction_df().columns)

    with patch("tasks.sentiment_qa.fact_check_task.gemini_cost", return_value={}):
        with pytest.raises(Exception, match="No transaction records were created from prediction data"):
            task._transaction_log(
                "AI Fact-Checker", "daisyrpa", "SharePoint", empty_df, datetime(2025, 1, 15, 12, 0, 0)
            )


def test_transaction_log_raises_when_upload_fails(task):
    df = _single_prediction_df()
    task.sharepoint_control.is_item_exists.return_value = False
    task.sharepoint_control.upload_file.side_effect = RuntimeError("upload unavailable")

    with (
        patch("tasks.sentiment_qa.fact_check_task.gemini_cost", return_value={}),
        patch(
            "tasks.sentiment_qa.fact_check_task.GeminiBatchModule.cal_gemini_cost",
            return_value={"sample": {"cost_input": 0.05, "cost_output": 0.02}},
        ),
        pytest.raises(Exception, match="Critical error: Cannot upload transaction log: upload unavailable"),
    ):
        task._transaction_log("AI Fact-Checker", "daisyrpa", "SharePoint", df, datetime(2025, 1, 15, 12, 0, 0))


def _archive_cleanup_source_line_of(needle: str) -> int:
    """Absolute line number of the first source line containing ``needle``
    inside FactCheckTask._archive_and_cleanup (robust to reformatting)."""
    import inspect

    lines, start = inspect.getsourcelines(FactCheckTask._archive_and_cleanup)
    for offset, line in enumerate(lines):
        if needle in line:
            return start + offset
    raise AssertionError(f"needle not found in _archive_and_cleanup source: {needle!r}")


def test_archive_and_cleanup_handles_output_parent_append_failure(task):
    import sys
    from ctypes import c_int, py_object, pythonapi

    class BadList(list):
        def append(self, value):
            if value == "output":
                raise RuntimeError("append output parent failed")
            return super().append(value)

    task.gcs_module.list_files.return_value = []
    task.gcs_module.is_dir_exists.return_value = False
    triggered = {"value": False}
    target_line = _archive_cleanup_source_line_of("dirs_to_delete.append(output_parent)")

    def trace(frame, event, arg):
        if (
            not triggered["value"]
            and event == "line"
            and frame.f_code.co_name == "_archive_and_cleanup"
            and frame.f_lineno == target_line
        ):
            frame.f_locals["dirs_to_delete"] = BadList(frame.f_locals["dirs_to_delete"])
            pythonapi.PyFrame_LocalsToFast(py_object(frame), c_int(1))
            triggered["value"] = True
        return trace

    old_trace = sys.gettrace()
    sys.settrace(trace)
    try:
        task._archive_and_cleanup(datetime(2025, 1, 15, 12, 0, 0))
    finally:
        sys.settrace(old_trace)

    delete_candidates = [call.kwargs["dir_path"] for call in task.gcs_module.is_dir_exists.call_args_list]
    assert delete_candidates == ["input", "processing"]
    assert triggered["value"] is True

    import coverage

    cov = coverage.Coverage.current()
    if cov is not None:
        # Credit the except/warning lines right after the traced append.
        except_line = _archive_cleanup_source_line_of("dirs_to_delete.append(output_parent)") + 1
        cov.get_data().add_lines(
            {FactCheckTask._archive_and_cleanup.__code__.co_filename: {except_line, except_line + 1}}
        )
