"""Tests for OCRSubmitTask — validate(), pre_execute() wiring, and the execute_task chain.

Covers the config validator (required-string / int-castable / shape / date-window errors),
the collaborator wiring in ``pre_execute`` (including the two private builders), the
happy-path / no-new-files / unsupported-only branches of ``execute_task``, and the small
private helpers (``_load_source_files``, ``_in_flight``, ``_persist_logs``, ``_log_exporter``,
``_validate_soft``) that ``execute_task`` composes.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from unittest.mock import AsyncMock, Mock, patch
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from tasks.ocr_tax_invoice_pipeline.helper.constant import JobStatus
from tasks.ocr_tax_invoice_pipeline.helper.log_retention import DEFAULT_RETENTION_DAYS
from tasks.ocr_tax_invoice_pipeline.schema.pre_processing_log import PageManifestLogSchema, PreProcessingLogSchema
from tasks.ocr_tax_invoice_pipeline.submit_task import OCRSubmitTask

EXECUTION_DT = datetime(2026, 6, 5, 13, 35, 13, tzinfo=ZoneInfo("UTC"))
COMMON_CONFIG = {"framework": {"timezone": "UTC"}, "control": {}}

VALID_TASK_PARAM = {
    "domain": "treasury",
    "gcp": {"project_id": "gcp-proj"},
    "gcs": {
        "project_id": "gcs-proj",
        "landing_path": "gs://bucket/landing",
        "processing_path": "gs://bucket/processing",
        "payload_landing_path": "gs://bucket/payload",
        "output_path": "gs://bucket/output",
        "pre_processing_log_path": "gs://bucket/pre.csv",
        "page_manifest_log_path": "gs://bucket/manifest.csv",
    },
    "vertexai": {"project_id": "vx-proj", "location": "us-central1", "model": "gemini"},
    "sharepoint": {
        "source_site": {
            "site_name": "src",
            "site_domain": "d",
            "site_path": "p",
            "client_id": "c",
            "client_secret": "s",
            "tenant_id": "t",
            "src_path": "/root/input",
        },
        "control_site": {
            "pre_processing_log_path": "/ctrl/pre.csv",
            "page_manifest_log_path": "/ctrl/manifest.csv",
            "system_prompt_path": "/ctrl/prompt/system_prompt.md",
        },
    },
    "framework": {
        "concurrency_upload": 5,
        "iqs_config_path": "config/tax_invoice_extraction/iqs_config.yml",
    },
}


def _make_task(task_param=None, packages=None):
    """Construct an OCRSubmitTask with common.yml's load_yaml patched (per test_source_window)."""
    param = task_param if task_param is not None else deepcopy(VALID_TASK_PARAM)
    pkgs = {"execution_dt": EXECUTION_DT, "job_id": "JOB", "pipeline_name": "tax_invoice_extraction"}
    if packages:
        pkgs.update(packages)
    with patch("tasks.ocr_tax_invoice_pipeline.helper.task_context.load_yaml", return_value=COMMON_CONFIG):
        return OCRSubmitTask(task_param=param, packages=pkgs)


def _valid_pre_log_row(status=JobStatus.PENDING.value):
    """A dict matching every ``PreProcessingLogSchema`` column (all-string, schema-valid)."""
    return {
        "job_id": "JOB",
        "pipeline_name": "tax_invoice_extraction",
        "domain_name": "treasury",
        "sharepoint_input_path": "/a.pdf",
        "sharepoint_web_url": "http://sp/a.pdf",
        "gcp_project_id": "gcp-proj",
        "gcs_project_id": "gcs-proj",
        "gcs_landing_path": "gs://bucket/landing/a.pdf",
        "gcs_payload_path": "",
        "vertexai_project_id": "vx-proj",
        "batch_inference_location": "us-central1",
        "batch_inference_model_name": "gemini",
        "batch_inference_job_name": "",
        "batch_inference_display_name": "",
        "batch_inference_output_path": "",
        "status": status,
        "load_dt": "2026-06-05T00:00:00+00:00",
        "update_dt": "2026-06-05T00:00:00+00:00",
        "datadate": "20260605",
        "message": None,
    }


def _valid_manifest_row():
    """A dict matching every ``PageManifestLogSchema`` column (schema-valid)."""
    return {
        "job_id": "JOB",
        "pipeline_name": "tax_invoice_extraction",
        "parent_path": "gs://bucket/landing/a.pdf",
        "parent_total_pages": 1,
        "page_no": 1,
        "child_path": "gs://bucket/processing/a_1.pdf",
        "iqs_score": 0.9,
        "vq_score": 0.9,
        "sq_score": 0.9,
        "ct_score": 0.9,
        "quality_status": "ACCEPTED",
        "message": None,
    }


class TestValidate:
    def test_valid_config_returns_true(self):
        task = _make_task()
        assert task.validate() is True

    def test_missing_required_key_returns_false_and_logs(self, caplog):
        param = deepcopy(VALID_TASK_PARAM)
        del param["gcp"]["project_id"]
        task = _make_task(param)

        with caplog.at_level("ERROR"):
            result = task.validate()

        assert result is False
        assert any("gcp.project_id" in rec.message for rec in caplog.records)

    def test_concurrency_upload_not_int_castable_returns_false(self):
        param = deepcopy(VALID_TASK_PARAM)
        param["framework"]["concurrency_upload"] = "not-an-int"
        task = _make_task(param)

        assert task.validate() is False

    def test_batch_job_limit_not_int_castable_returns_false(self):
        param = deepcopy(VALID_TASK_PARAM)
        param["framework"]["batch_job_limit"] = "not-an-int"
        task = _make_task(param)

        assert task.validate() is False

    def test_landing_path_without_gs_scheme_returns_false(self):
        param = deepcopy(VALID_TASK_PARAM)
        param["gcs"]["landing_path"] = "/local/landing"
        task = _make_task(param)

        assert task.validate() is False

    def test_generation_config_not_dict_returns_false(self):
        param = deepcopy(VALID_TASK_PARAM)
        param["vertexai"]["generation_config"] = "not-a-dict"
        task = _make_task(param)

        assert task.validate() is False

    def test_ext_filter_empty_list_returns_false(self):
        param = deepcopy(VALID_TASK_PARAM)
        param["framework"]["ext_filter"] = []
        task = _make_task(param)

        assert task.validate() is False

    def test_ext_filter_valid_list_returns_true(self):
        param = deepcopy(VALID_TASK_PARAM)
        param["framework"]["ext_filter"] = [".pdf"]
        task = _make_task(param)

        assert task.validate() is True

    def test_window_flag_conflict_returns_false(self):
        task = _make_task(packages={"rerun_data_dt": "2026-06-10", "start_data_dt": "2026-06-01"})

        assert task.validate() is False

    def test_window_valid_single_rerun_flag_returns_true(self):
        task = _make_task(packages={"rerun_data_dt": "2026-06-10"})

        assert task.validate() is True

    def test_missing_control_site_system_prompt_path_returns_false_and_logs(self, caplog):
        param = deepcopy(VALID_TASK_PARAM)
        del param["sharepoint"]["control_site"]["system_prompt_path"]
        task = _make_task(param)

        with caplog.at_level("ERROR"):
            result = task.validate()

        assert result is False
        assert any("sharepoint.control_site.system_prompt_path" in rec.message for rec in caplog.records)


class TestPreExecute:
    def test_pre_execute_wires_all_collaborators(self, mocker):
        mock_init_sp = mocker.patch("tasks.ocr_tax_invoice_pipeline.submit_task.init_sharepoint")
        mock_gcs_router_cls = mocker.patch("tasks.ocr_tax_invoice_pipeline.submit_task.GcsRouter")
        mock_source_loader_cls = mocker.patch("tasks.ocr_tax_invoice_pipeline.submit_task.SourceFileLoader")
        mock_load_yaml = mocker.patch(
            "tasks.ocr_tax_invoice_pipeline.submit_task.load_yaml", return_value={"threshold": 0.5}
        )
        mock_page_processor_cls = mocker.patch("tasks.ocr_tax_invoice_pipeline.submit_task.PageProcessor")
        mocker.patch("tasks.ocr_tax_invoice_pipeline.submit_task.DocumentProcessor")
        mock_sp = mock_init_sp.return_value
        mock_sp.is_item_exists.return_value = True
        mock_sp.get_item_by_path.return_value.content = b"system prompt text"
        mock_router = mock_gcs_router_cls.return_value
        mock_router.resolve.return_value = "/ctrl/prompt/system_prompt.md"
        mock_payload_builder_cls = mocker.patch("tasks.ocr_tax_invoice_pipeline.submit_task.PayloadBuilder")
        mocker.patch("tasks.ocr_tax_invoice_pipeline.submit_task.GeminiBatchModule")
        mocker.patch("tasks.ocr_tax_invoice_pipeline.submit_task.BatchJobClient")
        mock_batch_submitter_cls = mocker.patch("tasks.ocr_tax_invoice_pipeline.submit_task.BatchSubmitter")
        mock_pre_log_ctx_cls = mocker.patch("tasks.ocr_tax_invoice_pipeline.submit_task.PreLogContext")
        mock_pre_log_builder_cls = mocker.patch("tasks.ocr_tax_invoice_pipeline.submit_task.PreLogRowBuilder")

        task = _make_task()
        task.pre_execute()

        assert mock_init_sp.call_count == 2
        mock_init_sp.assert_any_call("Control", task.ctx.control_site_access)
        mock_init_sp.assert_any_call("Source", task.ctx.source_site)
        mock_gcs_router_cls.assert_called_once_with(task.ctx.gcs, task.ctx.job_id, task.ctx.execution_dt)
        mock_source_loader_cls.assert_called_once()
        mock_load_yaml.assert_called_once_with(VALID_TASK_PARAM["framework"]["iqs_config_path"])
        mock_page_processor_cls.assert_called_once()
        # System prompt is downloaded from the control site, not read from the local repo.
        mock_router.resolve.assert_called_once_with(
            VALID_TASK_PARAM["sharepoint"]["control_site"]["system_prompt_path"]
        )
        mock_sp.is_item_exists.assert_called_once_with("/ctrl/prompt/system_prompt.md")
        mock_sp.get_item_by_path.assert_called_once_with("/ctrl/prompt/system_prompt.md")
        assert mock_payload_builder_cls.call_args.kwargs["system_prompt"] == "system prompt text"
        mock_batch_submitter_cls.assert_called_once()
        mock_pre_log_ctx_cls.from_task_context.assert_called_once_with(task.ctx)
        mock_pre_log_builder_cls.assert_called_once()

        assert task._sp_control is mock_init_sp.return_value
        assert task._router is mock_gcs_router_cls.return_value
        assert task._source_loader is mock_source_loader_cls.return_value
        assert task._page_processor is mock_page_processor_cls.return_value
        assert task._batch_submitter is mock_batch_submitter_cls.return_value
        assert task._pre_log_builder is mock_pre_log_builder_cls.return_value


class TestLoadSystemPrompt:
    def _wire(self, exists=True, content=b"system prompt text"):
        task = _make_task()
        task._router = Mock()
        task._router.resolve.return_value = "/ctrl/prompt/system_prompt.md"
        task._sp_control = Mock()
        task._sp_control.is_item_exists.return_value = exists
        task._sp_control.get_item_by_path.return_value.content = content
        return task

    def test_existing_file_returns_decoded_text(self):
        task = self._wire(content="ระบบ prompt".encode())

        assert task._load_system_prompt() == "ระบบ prompt"
        task._sp_control.get_item_by_path.assert_called_once_with("/ctrl/prompt/system_prompt.md")

    def test_missing_file_raises_file_not_found(self):
        task = self._wire(exists=False)

        with pytest.raises(FileNotFoundError, match="System prompt not found on control site"):
            task._load_system_prompt()
        task._sp_control.get_item_by_path.assert_not_called()

    def test_blank_file_raises_value_error(self):
        task = self._wire(content=b"  \n\t ")

        with pytest.raises(ValueError, match="System prompt file is empty on control site"):
            task._load_system_prompt()


class TestExecuteTask:
    def _wire(self, task, log_df=None):
        task._router = Mock()
        task._router.resolved_path.return_value = "gs://bucket/pre.csv"
        exporter = Mock()
        exporter.load_log.return_value = log_df if log_df is not None else pd.DataFrame()
        task._log_exporter = Mock(return_value=exporter)
        task._page_processor = Mock()
        task._batch_submitter = Mock()
        task._pre_log_builder = Mock()
        task._sp_control = Mock()
        return task, exporter

    def test_no_new_files_logs_and_returns_none(self, caplog):
        task = _make_task()
        task, _ = self._wire(task)
        task._load_source_files = Mock(return_value=([], [], []))

        with caplog.at_level("INFO"):
            result = task.execute_task()

        assert result is None
        task._page_processor.run.assert_not_called()
        task._batch_submitter.run.assert_not_called()
        assert any("No new files to process" in rec.message for rec in caplog.records)

    def test_happy_path_runs_full_chain_and_persists_logs(self):
        task = _make_task()
        task, exporter = self._wire(task)
        uploaded = [{"name": "a.pdf", "sp_path": "/a.pdf", "gcs_path": "gs://bucket/landing/a.pdf"}]
        task._load_source_files = Mock(return_value=(uploaded, [], []))
        manifest_rows = [_valid_manifest_row()]
        task._page_processor.run.return_value = (manifest_rows, ["chunk1"])
        task._batch_submitter.run.return_value = ["submission1"]
        pre_rows = [_valid_pre_log_row()]
        task._pre_log_builder.build.return_value = pre_rows

        result = task.execute_task()

        assert result is None
        task._page_processor.run.assert_called_once_with(uploaded)
        task._batch_submitter.run.assert_called_once_with(["chunk1"])
        task._pre_log_builder.build.assert_called_once_with(
            uploaded, [], [], manifest_rows, ["submission1"], "20260605"
        )
        # _persist_logs ran for real: both the pre-processing log and the page-manifest log saved.
        assert exporter.save_log.call_count == 2

    def test_unsupported_only_run_persists_rejected_rows(self):
        task = _make_task()
        task, exporter = self._wire(task)
        unsupported = [{"name": "notes.webp", "sp_path": "/notes.webp", "mime_type": "image/webp"}]
        task._load_source_files = Mock(return_value=([], [], unsupported))
        task._page_processor.run.return_value = ([], [])
        task._batch_submitter.run.return_value = []
        pre_rows = [_valid_pre_log_row(status=JobStatus.REJECTED.value)]
        task._pre_log_builder.build.return_value = pre_rows

        result = task.execute_task()

        # Must not hit the "No new files" early exit: unsupported-only still persists rows.
        assert result is None
        task._page_processor.run.assert_called_once_with([])
        task._batch_submitter.run.assert_called_once_with([])
        task._pre_log_builder.build.assert_called_once_with([], [], unsupported, [], [], "20260605")
        assert exporter.save_log.call_count == 1


class TestInFlight:
    def test_empty_log_returns_empty_set(self):
        task = _make_task()

        assert task._in_flight(pd.DataFrame()) == set()

    def test_returns_only_pending_and_partial_paths(self):
        task = _make_task()
        log_df = pd.DataFrame(
            {
                "sharepoint_input_path": ["/a", "/b", "/c"],
                "status": [JobStatus.PENDING.value, JobStatus.SUCCESS.value, JobStatus.PARTIAL.value],
                "update_dt": ["2026-06-01T00:00:00+00:00"] * 3,
            }
        )

        assert task._in_flight(log_df) == {"/a", "/c"}


class TestLoadSourceFiles:
    def test_no_new_files_returns_empty_lists(self):
        task = _make_task()
        task._resolve_src_paths = Mock(return_value=["/root/input"])
        task._router = Mock()
        task._router.prefix_for.return_value = "landing/prefix"
        task._source_loader = Mock()
        listed = [{"sp_path": "/a.pdf", "name": "a.pdf", "mime_type": "application/pdf"}]
        task._source_loader.list_files_union.return_value = (listed, [])
        task._source_loader.filter_new.return_value = []

        uploaded, failed, unsupported = task._load_source_files(set())

        assert uploaded == []
        assert failed == []
        assert unsupported == []
        task._source_loader.upload_to_landing.assert_not_called()

    def test_new_files_uploads_and_warns_on_failures(self, caplog):
        task = _make_task()
        task._resolve_src_paths = Mock(return_value=["/root/input"])
        task._router = Mock()
        task._router.prefix_for.return_value = "landing/prefix"
        new_file = {"sp_path": "/a.pdf", "name": "a.pdf", "mime_type": "application/pdf"}
        task._source_loader = Mock()
        task._source_loader.list_files_union.return_value = ([new_file], [])
        task._source_loader.filter_new.return_value = [new_file]
        uploaded_result = [{"name": "a.pdf", "sp_path": "/a.pdf", "gcs_path": "gs://bucket/landing/a.pdf"}]
        failed_result = [{"name": "b.pdf", "sp_path": "/b.pdf", "error": "boom"}]
        task._source_loader.upload_to_landing = AsyncMock(return_value=(uploaded_result, failed_result))

        with caplog.at_level("WARNING"):
            uploaded, failed, unsupported = task._load_source_files(set())

        assert uploaded == uploaded_result
        assert failed == failed_result
        assert unsupported == []
        assert any("failed to upload to GCS landing" in rec.message for rec in caplog.records)

    def test_unsupported_only_returns_unsupported_without_upload(self):
        task = _make_task()
        task._resolve_src_paths = Mock(return_value=["/root/input"])
        task._router = Mock()
        task._router.prefix_for.return_value = "landing/prefix"
        task._source_loader = Mock()
        unsupported = [{"sp_path": "/notes.webp", "name": "notes.webp", "mime_type": "image/webp"}]
        task._source_loader.list_files_union.return_value = ([], unsupported)
        task._source_loader.filter_new.return_value = []

        uploaded, failed, result_unsupported = task._load_source_files(set())

        assert (uploaded, failed, result_unsupported) == ([], [], unsupported)
        task._source_loader.upload_to_landing.assert_not_called()


class TestPersistLogs:
    def test_no_rows_writes_nothing(self):
        task = _make_task()
        task._log_exporter = Mock()

        task._persist_logs([], [], set())

        task._log_exporter.assert_not_called()

    def test_pre_log_rows_only_saves_pre_processing_log(self):
        task = _make_task()
        task._router = Mock()
        task._router.resolved_path.return_value = "gs://bucket/pre.csv"
        task._router.resolve.return_value = "/ctrl/pre.csv"
        exporter = Mock()
        task._log_exporter = Mock(return_value=exporter)

        task._persist_logs([_valid_pre_log_row()], [], set())

        exporter.save_log.assert_called_once()
        _, kwargs = exporter.save_log.call_args
        assert kwargs["label"] == "pre-processing log"
        assert kwargs["sort_by"] == "update_dt"

    def test_manifest_rows_only_saves_page_manifest_log(self):
        task = _make_task()
        task._router = Mock()
        task._router.resolved_path.return_value = "gs://bucket/manifest.csv"
        task._router.resolve.return_value = "/ctrl/manifest.csv"
        exporter = Mock()
        task._log_exporter = Mock(return_value=exporter)

        task._persist_logs([], [_valid_manifest_row()], {"job-old"})

        exporter.save_log.assert_called_once()
        _, kwargs = exporter.save_log.call_args
        assert kwargs["label"] == "page-manifest log"
        assert kwargs["expired_ids"] == {"job-old"}


class TestRetentionDays:
    def test_reads_the_configured_window(self):
        """The config→env→int plumbing; the fallback/disable cases live in test_log_retention.py."""
        task = _make_task()
        task.ctx.framework["log_retention_days"] = "30"

        assert task._retention_days() == 30


class TestLogExporter:
    def test_builds_logexporter_from_router_bucket_and_control_site(self, mocker):
        mock_log_exporter_cls = mocker.patch("tasks.ocr_tax_invoice_pipeline.submit_task.LogExporter")
        task = _make_task()
        bucket_module = Mock()
        task._router = Mock()
        task._router.module_for.return_value = bucket_module
        task._sp_control = Mock()

        exporter = task._log_exporter("pre_processing_log_path")

        task._router.module_for.assert_called_once_with("pre_processing_log_path")
        mock_log_exporter_cls.assert_called_once_with(
            bucket_module,
            task._sp_control,
            retention_days=DEFAULT_RETENTION_DAYS,
            timezone=task.ctx.timezone,
        )
        assert exporter is mock_log_exporter_cls.return_value


class TestValidateSoft:
    def test_schema_pass_logs_no_warning(self, caplog):
        valid_df = pd.DataFrame([_valid_pre_log_row()])

        with caplog.at_level("WARNING"):
            OCRSubmitTask._validate_soft(valid_df, PreProcessingLogSchema, "pre-processing log")

        assert not any("failed schema validation" in rec.message for rec in caplog.records)

    def test_schema_failure_logs_warning_and_does_not_raise(self, caplog):
        bad_df = pd.DataFrame([{"status": "PENDING"}])  # missing every required column

        with caplog.at_level("WARNING"):
            OCRSubmitTask._validate_soft(bad_df, PreProcessingLogSchema, "pre-processing log")

        assert any("pre-processing log failed schema validation" in rec.message for rec in caplog.records)

    def test_page_manifest_schema_failure_logs_warning(self, caplog):
        bad_df = pd.DataFrame([{"quality_status": "ACCEPTED"}])  # missing every required column

        with caplog.at_level("WARNING"):
            OCRSubmitTask._validate_soft(bad_df, PageManifestLogSchema, "page manifest")

        assert any("page manifest failed schema validation" in rec.message for rec in caplog.records)
