"""Tests for the pure terminal-status finalization functions."""

import pandas as pd

from tasks.ocr_tax_invoice_pipeline.helper.constant import JobStatus, OCROutputStatus
from tasks.ocr_tax_invoice_pipeline.module.status_finalizer import (
    build_terminal_log_rows,
    resolve_terminal_statuses,
    rollup_status,
)

PRE_LOG_COLUMNS = [
    "job_id",
    "pipeline_name",
    "domain_name",
    "sharepoint_input_path",
    "gcp_project_id",
    "gcs_project_id",
    "gcs_landing_path",
    "gcs_payload_path",
    "vertexai_project_id",
    "batch_inference_location",
    "batch_inference_model_name",
    "batch_inference_job_name",
    "batch_inference_display_name",
    "batch_inference_output_path",
    "status",
    "load_dt",
    "update_dt",
    "datadate",
    "message",
]


def _pre_log(rows):
    """Build a full-column pre-processing-log frame from (sp_path, status, job_name) tuples."""
    records = []
    for i, (sp_path, status, job_name) in enumerate(rows):
        record = {col: f"v_{col}" for col in PRE_LOG_COLUMNS}
        record.update(
            sharepoint_input_path=sp_path,
            status=status,
            batch_inference_job_name=job_name,
            update_dt=f"2026-06-10T10:0{i}:00+00:00",
        )
        records.append(record)
    return pd.DataFrame(records, columns=PRE_LOG_COLUMNS)


def _final_df(rows):
    """Build a finalized frame from (file_path, status) tuples."""
    return pd.DataFrame([{"FILE_PATH": fp, "STATUS": st} for fp, st in rows])


class TestRollupStatus:
    def test_all_success(self):
        assert rollup_status({OCROutputStatus.SUCCESS.value}) == JobStatus.SUCCESS.value

    def test_some_success_is_success_with_failure(self):
        statuses = {OCROutputStatus.SUCCESS.value, OCROutputStatus.FAILED.value}
        assert rollup_status(statuses) == JobStatus.SUCCESS_WITH_FAILURE.value

    def test_no_success_is_failed(self):
        statuses = {OCROutputStatus.BLANK.value, OCROutputStatus.FAILED.value}
        assert rollup_status(statuses) == JobStatus.FAILED.value

    def test_empty_is_failed(self):
        assert rollup_status(set()) == JobStatus.FAILED.value


class TestResolveTerminalStatuses:
    def test_rolls_up_each_file_by_file_path(self):
        final_df = _final_df(
            [
                ("/a", OCROutputStatus.SUCCESS.value),
                ("/a", OCROutputStatus.SUCCESS.value),
                ("/b", OCROutputStatus.SUCCESS.value),
                ("/b", OCROutputStatus.FAILED.value),
            ]
        )
        statuses = resolve_terminal_statuses(final_df, pd.DataFrame(), dead_job_names=[], running_job_names=[])
        assert statuses == {"/a": JobStatus.SUCCESS.value, "/b": JobStatus.SUCCESS_WITH_FAILURE.value}

    def test_dead_job_forces_failed_for_accepted_file_with_no_rows(self):
        # A fully-accepted file on a dead job emits no final_df rows; force FAILED from the log.
        pre_log = _pre_log([("/a", JobStatus.PENDING.value, "job-dead")])
        statuses = resolve_terminal_statuses(pd.DataFrame(), pre_log, dead_job_names=["job-dead"], running_job_names=[])
        assert statuses == {"/a": JobStatus.FAILED.value}

    def test_dead_job_only_affects_in_flight_files(self):
        pre_log = _pre_log([("/a", JobStatus.PENDING.value, "job-dead"), ("/b", JobStatus.SUCCESS.value, "job-dead")])
        statuses = resolve_terminal_statuses(pd.DataFrame(), pre_log, dead_job_names=["job-dead"], running_job_names=[])
        assert statuses == {"/a": JobStatus.FAILED.value}  # /b already terminal, untouched

    def test_running_job_file_is_excluded(self):
        final_df = _final_df([("/a", OCROutputStatus.SUCCESS.value)])
        pre_log = _pre_log([("/a", JobStatus.PENDING.value, "job-run")])
        statuses = resolve_terminal_statuses(final_df, pre_log, dead_job_names=[], running_job_names=["job-run"])
        assert statuses == {}


class TestBuildTerminalLogRows:
    def test_in_flight_file_is_cloned_and_stamped(self):
        pre_log = _pre_log([("/a", JobStatus.PENDING.value, "job1")])
        rows = build_terminal_log_rows({"/a": JobStatus.SUCCESS.value}, pre_log, "2026-06-13T12:00:00+00:00")

        assert len(rows) == 1
        row = rows[0]
        assert set(PRE_LOG_COLUMNS).issubset(row.keys())  # every schema column carried over
        assert row["sharepoint_input_path"] == "/a"
        assert row["status"] == JobStatus.SUCCESS.value
        assert row["update_dt"] == "2026-06-13T12:00:00+00:00"
        assert row["message"] is None

    def test_already_terminal_file_is_skipped(self):
        pre_log = _pre_log([("/a", JobStatus.SUCCESS.value, "job1")])
        rows = build_terminal_log_rows({"/a": JobStatus.FAILED.value}, pre_log, "2026-06-13T12:00:00+00:00")
        assert rows == []

    def test_unknown_path_warns_and_is_skipped(self, caplog):
        pre_log = _pre_log([("/a", JobStatus.PENDING.value, "job1")])
        with caplog.at_level("WARNING"):
            rows = build_terminal_log_rows({"/zzz": JobStatus.FAILED.value}, pre_log, "2026-06-13T12:00:00+00:00")
        assert rows == []
        assert any("absent from pre-processing log" in rec.message for rec in caplog.records)

    def test_empty_inputs_return_empty(self):
        assert build_terminal_log_rows({}, _pre_log([("/a", JobStatus.PENDING.value, "j")]), "t") == []
        assert build_terminal_log_rows({"/a": "x"}, pd.DataFrame(), "t") == []
