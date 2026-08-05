import io
import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd
import pytest

from tasks.sentiment_qa.prep_payload_task import PrepPayloadTask
from tests.test_tasks.sentiment_qa.test_export_output_result_task import _mock_config_excel

EXECUTION_DT = datetime(2025, 1, 3, 9, 0, 0)
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


def _user_prompt_df():
    return pd.DataFrame(
        [
            {
                "no_cate": 2,
                "category": "Greeting",
                "sub_category": "opening",
                "item": "salutation",
                "rule_and_logic": "Say hello politely.",
            },
            {
                "no_cate": pd.NA,
                "category": pd.NA,
                "sub_category": "issue_type",
                "item": pd.NA,
                "rule_and_logic": "Describe issue type only for network calls.",
            },
        ]
    )


def _excel_bytes(df, sheet_name="user_prompt"):
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name=sheet_name)
    buffer.seek(0)
    return buffer.read()


@pytest.fixture(autouse=True)
def patch_common():
    with (
        patch("tasks.sentiment_qa.prep_payload_task.load_yaml", return_value=COMMON_CONFIG),
        patch(
            "tasks.sentiment_qa.prep_payload_task.resolve_env",
            side_effect=lambda value: value,
        ),
        patch(
            "tasks.sentiment_qa.prep_payload_task.resolve_date",
            side_effect=_fake_resolve_date,
        ),
        patch("tasks.sentiment_qa.prep_payload_task.asyncio.run") as async_run,
    ):
        async_run.return_value = {"success": 1, "failed": 0, "total": 1}
        yield async_run


@pytest.fixture
def task():
    qa_task = PrepPayloadTask(
        task_param={
            "gcs": {
                "project_id": "qa-project",
                "bucket_name": "qa-bucket",
                "input_folder": "input/%{DATA_DATE_YYYYMMDD}",
                "processing_voice_folder": "processing/%{DATA_DATE_YYYYMMDD}",
                "processing_batch_folder": "batch/%{DATA_DATE_YYYYMMDD}",
                "output_folder": "output/%{DATA_DATE_YYYYMMDD}",
                "backup_excel_prompt_path": "backup/%{DATA_DATE_YYYYMMDD}/user_prompt.xlsx",
            },
            "vertexai": {"generation_config": {"temperature": 0.1}},
            "sharepoint": {
                "control": {"user_config_path": "control/user_prompt.xlsx"},
                "verint": {"input_folder_list": "PROMO_END"},
            },
            "framework": {
                "lookback_days": "1",
                "concurrency_upload": "5",
                "system_prompt_file": "prompts/system.txt",
                "user_config_path": "local/user_prompt.xlsx",
            },
        },
        packages={"execution_dt": EXECUTION_DT},
    )
    qa_task.sharepoint_control = Mock()
    qa_task.sharepoint_verint = Mock()
    qa_task.gcs_module = Mock(project_id="qa-project", bucket_name="qa-bucket")
    qa_task.msgraph_module = Mock()
    return qa_task


@pytest.fixture
def fixed_now():
    return datetime(2025, 1, 3, 10, 30, 0)


class TestInitAndPreExecute:
    def test_init_sets_expected_defaults(self, task):
        assert task.project_id == "qa-project"
        assert task.msgraph_sender_email == "sender@example.com"

    def test_pre_execute_initializes_modules(self, task):
        with (
            patch("tasks.sentiment_qa.prep_payload_task.SharePointModule") as sharepoint_cls,
            patch("tasks.sentiment_qa.prep_payload_task.GCSModule") as gcs_cls,
            patch("tasks.sentiment_qa.prep_payload_task.MSGraphModule") as msgraph_cls,
        ):
            task.pre_execute()

        assert sharepoint_cls.call_count == 2
        gcs_cls.assert_called_once_with(project_id="qa-project", bucket_name="qa-bucket")
        msgraph_cls.assert_called_once_with(
            tenant_id="graph-tenant",
            client_id="graph-client",
            client_secret="graph-secret",
        )

    def test_pre_execute_raises_for_control_sharepoint_error(self, task):
        with (
            patch(
                "tasks.sentiment_qa.prep_payload_task.SharePointModule",
                side_effect=Exception("sharepoint boom"),
            ),
            patch("tasks.sentiment_qa.prep_payload_task.GCSModule"),
            patch("tasks.sentiment_qa.prep_payload_task.MSGraphModule"),
            pytest.raises(Exception, match="sharepoint boom"),
        ):
            task.pre_execute()

    def test_pre_execute_raises_for_verint_sharepoint_error(self, task):
        with (
            patch(
                "tasks.sentiment_qa.prep_payload_task.SharePointModule",
                side_effect=[Mock(), Exception("verint boom")],
            ),
            patch("tasks.sentiment_qa.prep_payload_task.GCSModule"),
            patch("tasks.sentiment_qa.prep_payload_task.MSGraphModule"),
            pytest.raises(Exception, match="verint boom"),
        ):
            task.pre_execute()

    def test_pre_execute_raises_for_gcs_error(self, task):
        with (
            patch("tasks.sentiment_qa.prep_payload_task.SharePointModule"),
            patch(
                "tasks.sentiment_qa.prep_payload_task.GCSModule",
                side_effect=Exception("gcs boom"),
            ),
            patch("tasks.sentiment_qa.prep_payload_task.MSGraphModule"),
            pytest.raises(Exception, match="gcs boom"),
        ):
            task.pre_execute()

    def test_pre_execute_raises_for_msgraph_error(self, task):
        with (
            patch("tasks.sentiment_qa.prep_payload_task.SharePointModule"),
            patch("tasks.sentiment_qa.prep_payload_task.GCSModule"),
            patch(
                "tasks.sentiment_qa.prep_payload_task.MSGraphModule",
                side_effect=Exception("msgraph boom"),
            ),
            pytest.raises(Exception, match="msgraph boom"),
        ):
            task.pre_execute()


class TestExecuteTask:
    def test_execute_uses_rerun_date(self, task):
        task.packages["rerun_data_dt"] = "2025-01-03"

        with patch.object(task, "_prepare_payload", return_value=("payloads.jsonl", "output", [])) as prepare_payload:
            result = task.execute_task()

        prepare_payload.assert_called_once_with("20250102", "20250102", EXECUTION_DT)
        assert result == ("payloads.jsonl", "output", [])

    def test_execute_wraps_date_errors(self, task):
        with (
            patch(
                "tasks.sentiment_qa.prep_payload_task.add_date",
                side_effect=ValueError("bad date"),
            ),
            pytest.raises(Exception, match="Cannot determine processing date range: bad date"),
        ):
            task.execute_task()

    def test_execute_wraps_prepare_payload_errors(self, task):
        with (
            patch.object(task, "_prepare_payload", side_effect=RuntimeError("payload boom")),
            pytest.raises(Exception, match="Cannot prepare batch payload: payload boom"),
        ):
            task.execute_task()


class TestPreparePayload:
    def test_prepare_payload_uploads_jsonl_and_returns_logs(self, task, patch_common):
        processing_file = (
            "processing/20250102/PROMO_END/call123_0812345678_090000_AGENT1_alice_smith_provider_20250102_60_IN.wav"
        )
        task.gcs_module.is_dir_exists.return_value = True
        task.gcs_module.list_files.side_effect = [["input/file.wav"], [processing_file]]

        with patch.object(task, "_prepare_prompt", return_value="Prompt for {date}"):
            payload_path, output_path, processing_logs = task._prepare_payload("20250102", "20250102", EXECUTION_DT)

        upload_kwargs = task.gcs_module.update_content_to_gcs.call_args.kwargs
        payload_lines = upload_kwargs["content"].decode("utf-8").splitlines()
        payload = json.loads(payload_lines[0])
        assert payload_path == "batch/20250103/payloads.jsonl"
        assert output_path == "output/20250103"
        assert payload["request"]["contents"][0]["parts"][0]["text"] == "Prompt for 2025-01-02"
        assert payload["request"]["contents"][0]["parts"][1]["fileData"]["mimeType"] == "audio/wav"
        assert len(processing_logs) == 1
        assert processing_logs[0].status == "SUCCESS"
        assert processing_logs[0].prediction_payload_path == "batch/20250103/payloads.jsonl"

    def test_prepare_payload_returns_none_when_no_supported_files(self, task, patch_common):
        task.gcs_module.is_dir_exists.return_value = True
        task.gcs_module.list_files.side_effect = [["input/file.mp3"], ["processing/20250102/PROMO_END/file.mp3"]]

        with patch.object(task, "_prepare_prompt", return_value="Prompt for {date}"):
            result = task._prepare_payload("20250102", "20250102", EXECUTION_DT)

        assert result == (None, None, None)
        task.gcs_module.update_content_to_gcs.assert_not_called()

    def test_prepare_payload_skips_missing_directories(self, task):
        task.gcs_module.is_dir_exists.return_value = False

        with patch.object(task, "_prepare_prompt", return_value="Prompt for {date}"):
            result = task._prepare_payload("20250102", "20250102", EXECUTION_DT)

        assert result == (None, None, None)

    def test_prepare_payload_wraps_list_date_errors(self, task):
        with (
            patch(
                "tasks.sentiment_qa.prep_payload_task.list_date",
                side_effect=ValueError("bad list"),
            ),
            pytest.raises(Exception, match="Cannot generate date list: bad list"),
        ):
            task._prepare_payload("20250102", "20250102", EXECUTION_DT)

    def test_prepare_payload_wraps_prompt_errors(self, task):
        with patch.object(task, "_prepare_prompt", side_effect=RuntimeError("prompt boom")):
            with pytest.raises(Exception, match="Cannot load prompt mappings: prompt boom"):
                task._prepare_payload("20250102", "20250102", EXECUTION_DT)

    def test_prepare_payload_wraps_batch_path_resolution_errors(self, task):
        with (
            patch(
                "tasks.sentiment_qa.prep_payload_task.resolve_date",
                side_effect=RuntimeError("path boom"),
            ),
            pytest.raises(Exception, match="Cannot resolve GCS paths: path boom"),
        ):
            task._prepare_payload("20250102", "20250102", EXECUTION_DT)

    def test_prepare_payload_skips_copy_failures(self, task, patch_common):
        task.gcs_module.is_dir_exists.return_value = True
        task.gcs_module.list_files.return_value = ["input/file.wav"]
        patch_common.side_effect = RuntimeError("copy boom")

        with patch.object(task, "_prepare_prompt", return_value="Prompt for {date}"):
            result = task._prepare_payload("20250102", "20250102", EXECUTION_DT)

        assert result == (None, None, None)

    def test_prepare_payload_keeps_failed_logs_when_one_file_errors(self, task, patch_common):
        file_one = (
            "processing/20250102/PROMO_END/call123_0812345678_090000_AGENT1_alice_smith_provider_20250102_60_IN.wav"
        )
        file_two = (
            "processing/20250102/PROMO_END/call124_0812345679_090500_AGENT1_alice_smith_provider_20250102_30_IN.wav"
        )
        task.gcs_module.is_dir_exists.return_value = True
        task.gcs_module.list_files.side_effect = [["input/file1.wav", "input/file2.wav"], [file_one, file_two]]

        class FlakyConfig(dict):
            def __init__(self):
                super().__init__(temperature=0.1)
                self.calls = 0

            def copy(self):
                self.calls += 1
                if self.calls == 1:
                    return {"temperature": 0.1}
                return ["bad"]

        task.vertexai["generation_config"] = FlakyConfig()

        with patch.object(task, "_prepare_prompt", return_value="Prompt for {date}"):
            payload_path, _, processing_logs = task._prepare_payload("20250102", "20250102", EXECUTION_DT)

        uploaded_lines = task.gcs_module.update_content_to_gcs.call_args.kwargs["content"].decode("utf-8").splitlines()
        assert payload_path == "batch/20250103/payloads.jsonl"
        assert len(uploaded_lines) == 1
        assert [log.status for log in processing_logs] == ["SUCCESS", "FAILED"]
        assert "Failed to create payload" in processing_logs[1].error_message

    def test_prepare_payload_skips_when_date_path_resolution_fails(self, task):
        def flaky_resolve_date(text, replace_date):
            if text in {task.gcs["input_folder"], task.gcs["processing_voice_folder"]}:
                raise RuntimeError("path boom")
            return _fake_resolve_date(text, replace_date)

        with (
            patch(
                "tasks.sentiment_qa.prep_payload_task.resolve_date",
                side_effect=flaky_resolve_date,
            ),
            patch.object(task, "_prepare_prompt", return_value="Prompt for {date}"),
        ):
            result = task._prepare_payload("20250102", "20250102", EXECUTION_DT)

        assert result == (None, None, None)

    def test_prepare_payload_skips_when_directory_check_fails(self, task):
        task.gcs_module.is_dir_exists.side_effect = RuntimeError("dir boom")

        with patch.object(task, "_prepare_prompt", return_value="Prompt for {date}"):
            result = task._prepare_payload("20250102", "20250102", EXECUTION_DT)

        assert result == (None, None, None)

    def test_prepare_payload_skips_when_input_listing_is_empty(self, task):
        task.gcs_module.is_dir_exists.return_value = True
        task.gcs_module.list_files.return_value = []

        with patch.object(task, "_prepare_prompt", return_value="Prompt for {date}"):
            result = task._prepare_payload("20250102", "20250102", EXECUTION_DT)

        assert result == (None, None, None)

    def test_prepare_payload_skips_when_input_listing_fails(self, task):
        task.gcs_module.is_dir_exists.return_value = True
        task.gcs_module.list_files.side_effect = RuntimeError("list boom")

        with patch.object(task, "_prepare_prompt", return_value="Prompt for {date}"):
            result = task._prepare_payload("20250102", "20250102", EXECUTION_DT)

        assert result == (None, None, None)

    def test_prepare_payload_logs_partial_copy_failures(self, task, patch_common):
        processing_file = (
            "processing/20250102/PROMO_END/call123_0812345678_090000_AGENT1_alice_smith_provider_20250102_60_IN.wav"
        )
        task.gcs_module.is_dir_exists.return_value = True
        task.gcs_module.list_files.side_effect = [["input/file.wav"], [processing_file]]
        patch_common.return_value = {"success": 1, "failed": 1, "total": 2, "errors": ["copy boom"]}

        with patch.object(task, "_prepare_prompt", return_value="Prompt for {date}"):
            payload_path, _, processing_logs = task._prepare_payload("20250102", "20250102", EXECUTION_DT)

        assert payload_path == "batch/20250103/payloads.jsonl"
        assert [log.status for log in processing_logs] == ["SUCCESS"]

    def test_prepare_payload_returns_none_when_processing_folder_is_empty(self, task, patch_common):
        task.gcs_module.is_dir_exists.return_value = True
        task.gcs_module.list_files.side_effect = [["input/file.wav"], []]
        patch_common.return_value = {"success": 1, "failed": 0, "total": 1}

        with patch.object(task, "_prepare_prompt", return_value="Prompt for {date}"):
            result = task._prepare_payload("20250102", "20250102", EXECUTION_DT)

        assert result == (None, None, None)

    def test_prepare_payload_raises_after_processing_list_failure(self, task, patch_common):
        task.gcs_module.is_dir_exists.return_value = True
        task.gcs_module.list_files.side_effect = [["input/file.wav"], RuntimeError("process list boom")]
        patch_common.return_value = {"success": 1, "failed": 0, "total": 1}

        with patch.object(task, "_prepare_prompt", return_value="Prompt for {date}"):
            with pytest.raises(UnboundLocalError, match="on_prcoess_files"):
                task._prepare_payload("20250102", "20250102", EXECUTION_DT)

    def test_prepare_payload_marks_key_error_logs_and_keeps_successes(self, task, patch_common):
        file_one = (
            "processing/20250102/PROMO_END/call123_0812345678_090000_AGENT1_alice_smith_provider_20250102_60_IN.wav"
        )
        file_two = (
            "processing/20250102/PROMO_END/call124_0812345679_090500_AGENT1_alice_smith_provider_20250102_30_IN.wav"
        )
        task.gcs_module.is_dir_exists.return_value = True
        task.gcs_module.list_files.side_effect = [["input/file1.wav", "input/file2.wav"], [file_one, file_two]]

        class KeyErrorConfig(dict):
            def __init__(self):
                super().__init__(temperature=0.1)
                self.calls = 0

            def copy(self):
                self.calls += 1
                if self.calls == 1:
                    raise KeyError("missing prompt")
                return {"temperature": 0.1}

        task.vertexai["generation_config"] = KeyErrorConfig()

        with patch.object(task, "_prepare_prompt", return_value="Prompt for {date}"):
            payload_path, _, processing_logs = task._prepare_payload("20250102", "20250102", EXECUTION_DT)

        assert payload_path == "batch/20250103/payloads.jsonl"
        assert [log.status for log in processing_logs] == ["FAILED", "SUCCESS"]
        assert "No prompt mapping found" in processing_logs[0].error_message

    def test_prepare_payload_wraps_payload_upload_errors(self, task, patch_common):
        processing_file = (
            "processing/20250102/PROMO_END/call123_0812345678_090000_AGENT1_alice_smith_provider_20250102_60_IN.wav"
        )
        task.gcs_module.is_dir_exists.return_value = True
        task.gcs_module.list_files.side_effect = [["input/file.wav"], [processing_file]]
        task.gcs_module.update_content_to_gcs.side_effect = RuntimeError("upload boom")
        patch_common.return_value = {"success": 1, "failed": 0, "total": 1}

        with patch.object(task, "_prepare_prompt", return_value="Prompt for {date}"):
            with pytest.raises(Exception, match="Cannot upload payload to GCS: upload boom"):
                task._prepare_payload("20250102", "20250102", EXECUTION_DT)


class TestPreparePrompt:
    def test_prepare_prompt_reads_sharepoint_excel_and_backs_up_to_gcs(self, task, patch_common):
        task.sharepoint_control.get_item_by_path.return_value = SimpleNamespace(content=_mock_config_excel())

        def mock_local_read_xlsx(file_path, sheet_name=None):
            return pd.read_excel(io.BytesIO(_mock_config_excel()), sheet_name=sheet_name)

        with (
            patch(
                "tasks.sentiment_qa.prep_payload_task.read_file",
                return_value="HEADER {user_prompt} company_verification `self_service`",
            ),
            patch(
                "tasks.sentiment_qa.prep_payload_task.read_xlsx",
                side_effect=mock_local_read_xlsx,
            ),
        ):
            prompt = task._prepare_prompt(EXECUTION_DT, "user_prompt_inbound")

        assert "Category 1.: `Greeting`" in prompt
        assert "customer_verification" in prompt
        assert "`true_application`" in prompt
        task.gcs_module.upload_sharepoint_to_gcs.assert_called_once()
        backup_call = task.gcs_module.upload_sharepoint_to_gcs.call_args.kwargs
        assert backup_call["stream_list"][0]["upload"] == "backup/20250103/user_prompt.xlsx"

    def test_prepare_prompt_falls_back_to_local_file(self, task, patch_common):
        task.sharepoint_control.get_item_by_path.side_effect = RuntimeError("missing sharepoint file")

        def mock_local_read_xlsx(file_path, sheet_name=None):
            return pd.read_excel(io.BytesIO(_mock_config_excel()), sheet_name=sheet_name)

        with (
            patch(
                "tasks.sentiment_qa.prep_payload_task.read_file",
                return_value="HEADER {user_prompt}",
            ),
            patch(
                "tasks.sentiment_qa.prep_payload_task.read_xlsx",
                side_effect=mock_local_read_xlsx,
            ),
        ):
            prompt = task._prepare_prompt(EXECUTION_DT, "user_prompt_inbound")

        assert "Say hello politely." in prompt
        assert "Category 1.: `Greeting`" in prompt
        assert "Describe issue type only for network calls." in prompt
        task.gcs_module.upload_sharepoint_to_gcs.assert_not_called()

    def test_prepare_prompt_keeps_building_when_backup_upload_fails(self, task):
        base_excel_bytes = _mock_config_excel()
        with pd.ExcelFile(io.BytesIO(base_excel_bytes)) as xls:
            sheet_dict = {sheet_name: pd.read_excel(xls, sheet_name=sheet_name) for sheet_name in xls.sheet_names}

        additional_row = pd.DataFrame(
            [
                {
                    "no_cate": pd.NA,
                    "category": pd.NA,
                    "sub_category": "additional_notes",
                    "item": pd.NA,
                    "rule_and_logic": "Include notes when provided.",
                }
            ]
        )
        sheet_dict["user_prompt_inbound"] = pd.concat(
            [sheet_dict["user_prompt_inbound"], additional_row], ignore_index=True
        )

        modified_buffer = io.BytesIO()
        with pd.ExcelWriter(modified_buffer, engine="openpyxl") as writer:
            for sheet_name, df in sheet_dict.items():
                df.to_excel(writer, sheet_name=sheet_name, index=False)
        modified_buffer.seek(0)

        task.sharepoint_control.get_item_by_path.return_value = SimpleNamespace(content=modified_buffer.getvalue())
        task.gcs_module.upload_sharepoint_to_gcs.side_effect = RuntimeError("backup boom")

        def mock_local_read_xlsx(file_path, sheet_name=None):
            return pd.read_excel(io.BytesIO(modified_buffer.getvalue()), sheet_name=sheet_name)

        with (
            patch(
                "tasks.sentiment_qa.prep_payload_task.read_file",
                return_value="HEADER {user_prompt}",
            ),
            patch(
                "tasks.sentiment_qa.prep_payload_task.read_xlsx",
                side_effect=mock_local_read_xlsx,
            ),
        ):
            prompt = task._prepare_prompt(EXECUTION_DT, "user_prompt_inbound")

        assert "`additional_notes`" in prompt
        assert "Include notes when provided." in prompt

    def test_prepare_prompt_wraps_errors(self, task):
        with (
            patch(
                "tasks.sentiment_qa.prep_payload_task.read_file",
                side_effect=RuntimeError("read boom"),
            ),
            pytest.raises(Exception, match="Cannot prepare prompt: read boom"),
        ):
            task._prepare_prompt(EXECUTION_DT, "user_prompt_inbound")


class TestPostExecuteAndSchema:
    def test_post_execute_calls_cleanup(self, task):
        result = task.post_execute("done")

        task.gcs_module.cleanup_empty_dir_markers.assert_called_once_with(prefix="")
        assert result == "done"

    def test_post_execute_swallows_cleanup_errors(self, task):
        task.gcs_module.cleanup_empty_dir_markers.side_effect = RuntimeError("cleanup boom")

        assert task.post_execute("done") == "done"

    def test_get_analysis_schema_contains_expected_sections(self, task):
        schema = task._get_analysis_schema()

        assert schema["response_mime_type"] == "application/json"
        properties = schema["response_schema"]["properties"]
        assert {"service_quality", "sale_opportunity", "network"}.issubset(properties)


class TestOnError:
    def test_on_error_sends_email(self, task, fixed_now):
        with patch(
            "tasks.sentiment_qa.prep_payload_task.get_current_datetime",
            return_value=fixed_now,
        ):
            task.on_error(RuntimeError("prep boom"))

        kwargs = task.msgraph_module.send_email.call_args.kwargs
        assert kwargs["subject"] == "[AI Failed] [AI-QA]"
        assert "prep boom" in kwargs["body"]
        assert kwargs["sender_email"] == "sender@example.com"

    def test_on_error_swallows_email_failures(self, task, fixed_now):
        task.msgraph_module.send_email.side_effect = RuntimeError("email boom")

        with patch(
            "tasks.sentiment_qa.prep_payload_task.get_current_datetime",
            return_value=fixed_now,
        ):
            task.on_error(RuntimeError("prep boom"))
