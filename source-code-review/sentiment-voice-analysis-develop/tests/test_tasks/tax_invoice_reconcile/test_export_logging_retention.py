"""Retention for the month-partitioned transaction + performance logs.

These two logs are one CSV per month, so retention means deleting whole expired month-files (the
row-prune only bites when the window is shorter than a month). A negative
``TAX_INVOICE_LOG_RETENTION_DAYS`` disables both.
"""

from datetime import datetime
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from tasks.tax_invoice_reconcile.module.export_logging import ExportLogging

BANGKOK = ZoneInfo("Asia/Bangkok")
EXECUTION_DT = datetime(2026, 7, 14, 9, 0, tzinfo=BANGKOK)
TXN_PATH = "ctrl/logs/transaction_log_202607.csv"


def _ocr_df() -> pd.DataFrame:
    """Minimal frame carrying every column ExportLogging's constructor validates."""
    return pd.DataFrame(
        {
            "FILE_PATH": ["/sp/a.pdf"],
            "PAGE_NO": [1],
            "START_TIME": ["2026-07-14T09:00:00+07:00"],
            "END_TIME": ["2026-07-14T09:00:05+07:00"],
            "STATUS": ["SUCCESS"],
            "MESSAGE": [""],
            "USAGE_METADATA": [{}],
            "DATADATE": ["20260714"],
        }
    )


def _exporter(sharepoint: Mock, retention_days: object) -> ExportLogging:
    return ExportLogging(
        execution_dt=EXECUTION_DT,
        ocr_df=_ocr_df(),
        pre_log_df=pd.DataFrame(),
        cfg={"retention_days": retention_days, "transaction_log_path": TXN_PATH},
        sharepoint=sharepoint,
    )


def _sharepoint_with(names: list[str]) -> Mock:
    sp = Mock()
    sp.is_item_exists.return_value = False
    sp.list_files.return_value = [{"name": name} for name in names]
    return sp


@pytest.fixture
def rows() -> pd.DataFrame:
    """One in-window transaction row (load_dt drives the row-level rule)."""
    return pd.DataFrame({"load_dt": ["2026-07-14 09:00:00"], "data_date": ["20260714"], "id": ["a"]})


class TestMonthFileSweep:
    def test_expired_month_files_are_deleted_after_the_upload(self, rows):
        # Arrange — 90-day window from 2026-07-14 → cutoff in April; Jan/Feb are entirely before it.
        sp = _sharepoint_with(
            ["transaction_log_202601.csv", "transaction_log_202602.csv", "transaction_log_202607.csv"]
        )

        # Act
        _exporter(sp, 90)._append_and_upload(rows, TXN_PATH, ["load_dt"], "transaction log")

        # Assert
        deleted = [call.args[0] for call in sp.delete_item.call_args_list]
        assert deleted == ["ctrl/logs/transaction_log_202601.csv", "ctrl/logs/transaction_log_202602.csv"]

    def test_current_month_file_is_never_deleted(self, rows):
        # Arrange
        sp = _sharepoint_with(["transaction_log_202607.csv"])

        # Act
        _exporter(sp, 90)._append_and_upload(rows, TXN_PATH, ["load_dt"], "transaction log")

        # Assert
        sp.delete_item.assert_not_called()

    def test_negative_retention_days_deletes_nothing(self, rows):
        # Arrange
        sp = _sharepoint_with(["transaction_log_201901.csv"])

        # Act
        _exporter(sp, -1)._append_and_upload(rows, TXN_PATH, ["load_dt"], "transaction log")

        # Assert
        sp.delete_item.assert_not_called()
        sp.upload_file.assert_called_once()  # the log itself is still written

    def test_sweep_failure_is_swallowed_so_the_run_survives(self, rows, caplog):
        # Arrange — the log bytes are already uploaded by the time the sweep runs.
        sp = _sharepoint_with(["transaction_log_202601.csv"])
        sp.delete_item.side_effect = Exception("sharepoint down")

        # Act
        with caplog.at_level("WARNING"):
            _exporter(sp, 90)._append_and_upload(rows, TXN_PATH, ["load_dt"], "transaction log")

        # Assert
        sp.upload_file.assert_called_once()
        assert any("retention sweep failed" in rec.message for rec in caplog.records)


class TestRowPrune:
    def test_rows_older_than_a_sub_month_window_are_pruned_from_the_current_file(self):
        # Arrange — a 1-day window; the aged row sits in the same month-file as the fresh one, so
        # only the row-level rule can remove it.
        sp = _sharepoint_with(["transaction_log_202607.csv"])
        fresh = pd.Timestamp.now(tz=BANGKOK).strftime("%Y-%m-%d %H:%M:%S")
        stale = (pd.Timestamp.now(tz=BANGKOK) - pd.Timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S")
        rows = pd.DataFrame({"load_dt": [fresh, stale], "data_date": ["20260714", "20260704"], "id": ["a", "b"]})

        # Act
        _exporter(sp, 1)._append_and_upload(rows, TXN_PATH, ["load_dt"], "transaction log")

        # Assert
        written = pd.read_csv(pd.io.common.BytesIO(sp.upload_file.call_args[0][1]))
        assert written["id"].tolist() == ["a"]

    def test_negative_retention_days_keeps_every_row(self):
        # Arrange
        sp = _sharepoint_with([])
        stale = "2019-01-01 00:00:00"
        rows = pd.DataFrame({"load_dt": [stale], "data_date": ["20190101"], "id": ["ancient"]})

        # Act
        _exporter(sp, -1)._append_and_upload(rows, TXN_PATH, ["load_dt"], "transaction log")

        # Assert
        written = pd.read_csv(pd.io.common.BytesIO(sp.upload_file.call_args[0][1]))
        assert written["id"].tolist() == ["ancient"]
