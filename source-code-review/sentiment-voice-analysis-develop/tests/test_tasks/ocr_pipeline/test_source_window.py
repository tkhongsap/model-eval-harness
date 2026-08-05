"""Tests for the submit-side source date-window resolution and SharePoint listing union.

Covers ``OCRSubmitTask._resolve_src_paths`` (only ``src_path`` uses the data window; coarse
date formats dedupe across days) and ``SourceFileLoader.list_files_union`` (first-wins dedupe
of both the supported and unsupported partitions; missing folders skipped; all-fail raises).
"""

from datetime import datetime
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import pytest
import requests

from tasks.ocr_tax_invoice_pipeline.module.gcs_router import GcsRouter
from tasks.ocr_tax_invoice_pipeline.module.source_loader import SourceFileLoader
from tasks.ocr_tax_invoice_pipeline.submit_task import OCRSubmitTask

EXECUTION_DT = datetime(2026, 6, 5, 13, 35, 13, tzinfo=ZoneInfo("UTC"))
COMMON_CONFIG = {"framework": {"timezone": "UTC"}, "control": {}}


def _make_task(src_path, **flags):
    """Construct an OCRSubmitTask with a real GcsRouter, ready for _resolve_src_paths."""
    task_param = {
        "domain": "treasury",
        "gcp": {"project_id": "gcp-proj"},
        "gcs": {"project_id": "gcs-proj"},
        "vertexai": {},
        "sharepoint": {"source_site": {"src_path": src_path}, "control_site": {}},
        "framework": {},
    }
    packages = {"execution_dt": EXECUTION_DT, "job_id": "JOB", "pipeline_name": "tax_invoice_extraction", **flags}
    with patch("tasks.ocr_tax_invoice_pipeline.helper.task_context.load_yaml", return_value=COMMON_CONFIG):
        task = OCRSubmitTask(task_param=task_param, packages=packages)
    task._router = GcsRouter(task.ctx.gcs, task.ctx.job_id, task.ctx.execution_dt)
    return task


class TestResolveSrcPaths:
    def test_no_placeholder_returns_single_path(self):
        task = _make_task("/root/input")
        assert task._resolve_src_paths() == ["/root/input"]

    def test_no_placeholder_with_flags_still_single_path_and_warns(self, caplog):
        task = _make_task("/root/input", rerun_data_dt="2026-06-10")
        with caplog.at_level("WARNING"):
            paths = task._resolve_src_paths()
        assert paths == ["/root/input"]
        assert any("no %{DATA_DATE}" in rec.message for rec in caplog.records)

    def test_placeholder_with_rerun_resolves_single_date(self):
        task = _make_task("/root/input/%{DATA_DATE_YYYYMMDD}", rerun_data_dt="2026-06-10")
        assert task._resolve_src_paths() == ["/root/input/20260610"]

    def test_placeholder_daily_range_lists_each_day(self):
        task = _make_task("/root/input/%{DATA_DATE_YYYYMMDD}", start_data_dt="2026-06-10", end_data_dt="2026-06-12")
        assert task._resolve_src_paths() == [
            "/root/input/20260610",
            "/root/input/20260611",
            "/root/input/20260612",
        ]

    def test_coarse_format_dedupes_across_month_boundary(self):
        task = _make_task("/root/%{DATA_DATE_YYYYMM}", start_data_dt="2026-05-30", end_data_dt="2026-06-02")
        # 4 days span 2 months → deduped to two paths, order preserved.
        assert task._resolve_src_paths() == ["/root/202605", "/root/202606"]


def _loader_with(mapping):
    """Build a SourceFileLoader whose list_files is stubbed per path from ``mapping``.

    Each mapping value is either an exception to raise, or a ``(supported, unsupported)``
    tuple matching the real ``list_files`` return shape.
    """
    loader = SourceFileLoader(Mock(), Mock())

    def _list(path, *_):
        value = mapping[path]
        if isinstance(value, Exception):
            raise value
        return value

    loader.list_files = Mock(side_effect=_list)
    return loader


class TestListFilesUnion:
    def test_unions_and_dedupes_first_wins(self):
        loader = _loader_with(
            {
                "/p1": ([{"sp_path": "/a"}, {"sp_path": "/b"}], []),
                "/p2": ([{"sp_path": "/b"}, {"sp_path": "/c"}], []),
            }
        )
        supported, unsupported = loader.list_files_union(["/p1", "/p2"], [".pdf"])
        assert [r["sp_path"] for r in supported] == ["/a", "/b", "/c"]
        assert unsupported == []

    def test_missing_folder_is_skipped_with_warning(self, caplog):
        loader = _loader_with(
            {
                "/missing": requests.exceptions.HTTPError("404 Not Found"),
                "/ok": ([{"sp_path": "/a"}], []),
            }
        )
        with caplog.at_level("WARNING"):
            supported, unsupported = loader.list_files_union(["/missing", "/ok"], [".pdf"])
        assert [r["sp_path"] for r in supported] == ["/a"]
        assert unsupported == []
        assert any("Failed to list source path /missing" in rec.message for rec in caplog.records)

    def test_all_paths_failing_raises_runtimeerror(self):
        loader = _loader_with(
            {
                "/m1": requests.exceptions.HTTPError("404"),
                "/m2": requests.exceptions.HTTPError("404"),
            }
        )
        with pytest.raises(RuntimeError, match="All 2 source path"):
            loader.list_files_union(["/m1", "/m2"], [".pdf"])

    def test_union_dedupes_unsupported_across_paths(self):
        loader = _loader_with(
            {
                "/p1": ([{"sp_path": "/a"}], [{"sp_path": "/notes.txt"}]),
                "/p2": ([], [{"sp_path": "/notes.txt"}, {"sp_path": "/other.doc"}]),
            }
        )
        supported, unsupported = loader.list_files_union(["/p1", "/p2"], [".pdf"])
        assert [r["sp_path"] for r in supported] == ["/a"]
        assert [r["sp_path"] for r in unsupported] == ["/notes.txt", "/other.doc"]
