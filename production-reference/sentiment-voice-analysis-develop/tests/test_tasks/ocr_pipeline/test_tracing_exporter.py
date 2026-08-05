"""Tests for TracingLogExporter — SharePoint-only, one CSV per month + 3-month file retention."""

import io
from unittest.mock import Mock

import pandas as pd

from tasks.ocr_tax_invoice_pipeline.module.tracing_exporter import TracingLogExporter

FOLDER = "/control/ocr"
MONTH_FILE = "tracing_log_202607.csv"
SP_PATH = f"{FOLDER}/{MONTH_FILE}"


def _csv_bytes(df):
    return df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")


def _uploaded_frame(sp_mock):
    return pd.read_csv(io.BytesIO(sp_mock.upload_file.call_args[0][1]), dtype=str)


def _sp(existing=None, month_files=(MONTH_FILE,)):
    """A SharePoint mock whose read returns *existing* and whose folder holds *month_files*."""
    sp = Mock()
    if existing is None:
        sp.get_item_by_path.side_effect = Exception("404 Not Found")
    else:
        sp.get_item_by_path.return_value = Mock(content=_csv_bytes(existing))
    sp.list_files.return_value = [{"name": name} for name in month_files]
    return sp


def test_appends_new_rows_to_the_month_file():
    # Arrange
    sp = _sp(existing=pd.DataFrame({"job_id": ["old"]}))

    # Act
    TracingLogExporter(sp).save(pd.DataFrame({"job_id": ["new"]}), SP_PATH)

    # Assert
    assert _uploaded_frame(sp)["job_id"].tolist() == ["old", "new"]
    assert sp.upload_file.call_args[0][0] == SP_PATH
    sp.delete_item.assert_not_called()


def test_first_write_uploads_only_new_rows_when_file_absent():
    # Arrange — this month's file does not exist yet.
    sp = _sp(existing=None)

    # Act
    TracingLogExporter(sp).save(pd.DataFrame({"job_id": ["new"]}), SP_PATH)

    # Assert
    assert _uploaded_frame(sp)["job_id"].tolist() == ["new"]


def test_empty_new_rows_skips_upload_and_read():
    # Arrange
    sp = Mock()

    # Act
    TracingLogExporter(sp).save(pd.DataFrame(), SP_PATH)

    # Assert
    sp.upload_file.assert_not_called()
    sp.get_item_by_path.assert_not_called()


def test_retention_deletes_month_files_older_than_window():
    # Arrange — current month 202607; folder holds current + several older month-files.
    sp = _sp(
        existing=None,
        month_files=(
            "tracing_log_202607.csv",  # delta 0  → keep
            "tracing_log_202604.csv",  # delta 3  → keep (== window)
            "tracing_log_202603.csv",  # delta 4  → delete
            "tracing_log_202512.csv",  # delta 7  → delete
            "notes.csv",  # not month-partitioned → ignore
        ),
    )

    # Act
    TracingLogExporter(sp).save(pd.DataFrame({"job_id": ["new"]}), SP_PATH)

    # Assert
    deleted = {call.args[0] for call in sp.delete_item.call_args_list}
    assert deleted == {f"{FOLDER}/tracing_log_202603.csv", f"{FOLDER}/tracing_log_202512.csv"}


def test_non_month_partitioned_path_skips_retention():
    # Arrange — a static filename (no YYYYMM) must not trigger a folder sweep.
    sp = _sp(existing=None)

    # Act
    TracingLogExporter(sp).save(pd.DataFrame({"job_id": ["new"]}), f"{FOLDER}/tracing_log.csv")

    # Assert
    sp.list_files.assert_not_called()
    sp.delete_item.assert_not_called()
