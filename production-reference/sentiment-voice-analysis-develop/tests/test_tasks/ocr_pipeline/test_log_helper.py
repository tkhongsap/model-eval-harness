"""Tests for the shared pre-processing-log dedup helper.

Focus: ``latest_status_per_file`` must pick the furthest-progressed status per file
even when two append-only rows share an identical ``update_dt`` (the Windows
coarse-clock collision that made the old ``sort_values`` non-deterministic).
"""

import pandas as pd

from tasks.ocr_tax_invoice_pipeline.helper.constant import JobStatus
from tasks.ocr_tax_invoice_pipeline.helper.log_helper import latest_status_per_file

SP_PATH = "/in/invoice.pdf"
SAME_TICK = "2026-06-10T17:01:34.290071+07:00"


def _row(status, update_dt, job_name="job-123"):
    return {
        "sharepoint_input_path": SP_PATH,
        "status": status.value,
        "update_dt": update_dt,
        "batch_inference_job_name": job_name,
    }


class TestLatestStatusPerFile:
    def test_initial_and_pending_share_update_dt_pending_wins(self):
        # Arrange: same file, identical update_dt — INITIAL then PENDING.
        df = pd.DataFrame([_row(JobStatus.INITIAL, SAME_TICK), _row(JobStatus.PENDING, SAME_TICK)])

        # Act
        latest = latest_status_per_file(df)

        # Assert: the later lifecycle status is chosen.
        assert latest.loc[0, "status"] == JobStatus.PENDING.value

    def test_tie_resolves_to_pending_regardless_of_row_order(self):
        # Arrange: same tie, but PENDING is inserted *before* INITIAL.
        df = pd.DataFrame([_row(JobStatus.PENDING, SAME_TICK), _row(JobStatus.INITIAL, SAME_TICK)])

        # Act
        latest = latest_status_per_file(df)

        # Assert: order-independent — PENDING still wins.
        assert latest.loc[0, "status"] == JobStatus.PENDING.value

    def test_later_tick_terminal_status_wins(self):
        # Arrange: PENDING from an earlier run, SUCCESS appended by a later run.
        df = pd.DataFrame(
            [
                _row(JobStatus.PENDING, "2026-06-10T17:01:34.290071+07:00"),
                _row(JobStatus.SUCCESS, "2026-06-10T18:20:00.000000+07:00"),
            ]
        )

        # Act
        latest = latest_status_per_file(df)

        # Assert: the strictly-later update_dt drives the result.
        assert latest.loc[0, "status"] == JobStatus.SUCCESS.value

    def test_empty_frame_returns_empty(self):
        assert latest_status_per_file(pd.DataFrame()).empty

    def test_missing_required_column_returns_empty(self):
        df = pd.DataFrame([{"sharepoint_input_path": SP_PATH, "status": JobStatus.PENDING.value}])

        assert latest_status_per_file(df).empty
