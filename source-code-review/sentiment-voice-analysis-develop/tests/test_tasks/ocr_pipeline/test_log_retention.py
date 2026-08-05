"""Tests for the env-driven retention window shared by every tax-invoice log.

Two invariants carry the most weight:

* **Rows age out purely by timestamp, regardless of status.** An aged in-flight (PENDING/PARTIAL)
  row is a stuck file — pruning it is the deliberate backstop that makes the file re-processable.
* **A negative ``TAX_INVOICE_LOG_RETENTION_DAYS`` (e.g. ``-1``) disables retention entirely.**
"""

import io
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pandas as pd
from google.api_core.exceptions import PreconditionFailed

from tasks.ocr_tax_invoice_pipeline.helper.log_retention import (
    DEFAULT_RETENTION_DAYS,
    DEFAULT_TIMEZONE,
    expired_job_ids,
    expired_month_files,
    month_file_pattern,
    prune_by_timestamp,
    prune_manifest,
    resolve_retention_days,
    retention_cutoff,
    sweep_month_files,
)
from tasks.ocr_tax_invoice_pipeline.module.log_exporter import LogExporter

BANGKOK = ZoneInfo(DEFAULT_TIMEZONE)
RETENTION_DAYS = 90


def _aged(days: int) -> str:
    """ISO-8601 ``update_dt`` (with offset) for a row written *days* days ago."""
    return (pd.Timestamp.now(tz=BANGKOK) - pd.Timedelta(days=days)).isoformat()


def _pre_row(path: str, status: str, days_old: int, job_id: str = "job-1") -> dict:
    return {
        "job_id": job_id,
        "sharepoint_input_path": path,
        "status": status,
        "update_dt": _aged(days_old),
    }


def _pre_log(*rows: dict) -> pd.DataFrame:
    return pd.DataFrame(list(rows))


def _cutoff() -> pd.Timestamp:
    return retention_cutoff(RETENTION_DAYS, tz=BANGKOK)


class TestResolveRetentionDays:
    def test_numeric_string_is_parsed(self):
        assert resolve_retention_days("30") == 30

    def test_negative_passes_through_as_the_disable_switch(self):
        assert resolve_retention_days("-1") == -1

    def test_unset_env_var_placeholder_falls_back_to_the_default(self, monkeypatch, caplog):
        # Arrange — resolve_env (now folded into the helper) substitutes a missing env var with "",
        # which int() would reject.
        monkeypatch.delenv("THIS_ENV_VAR_DOES_NOT_EXIST", raising=False)

        # Act
        with caplog.at_level("WARNING"):
            days = resolve_retention_days("${THIS_ENV_VAR_DOES_NOT_EXIST}")

        # Assert
        assert days == DEFAULT_RETENTION_DAYS
        assert any("not a valid day count" in rec.message for rec in caplog.records)

    def test_garbage_value_falls_back_to_the_default_instead_of_raising(self):
        assert resolve_retention_days("ninety") == DEFAULT_RETENTION_DAYS

    def test_none_falls_back_to_the_default(self):
        assert resolve_retention_days(None) == DEFAULT_RETENTION_DAYS


class TestRetentionDisabled:
    """A negative window must make every prune a no-op."""

    def test_negative_days_yields_no_cutoff(self):
        assert retention_cutoff(-1, tz=BANGKOK) is None

    def test_ancient_row_survives_when_disabled(self):
        # Arrange
        log = _pre_log(_pre_row("/sp/ancient.pdf", "SUCCESS", days_old=3650))

        # Act
        kept = prune_by_timestamp(log, retention_cutoff(-1, tz=BANGKOK), "update_dt")

        # Assert
        assert kept["sharepoint_input_path"].tolist() == ["/sp/ancient.pdf"]

    def test_no_job_expires_when_disabled(self):
        # Arrange
        log = _pre_log(_pre_row("/sp/ancient.pdf", "SUCCESS", 3650, job_id="job-old"))

        # Act / Assert
        assert expired_job_ids(log, retention_cutoff(-1, tz=BANGKOK)) == set()

    def test_no_month_file_expires_when_disabled(self):
        # Act / Assert
        assert expired_month_files(["tracing_log_201901.csv"], month_file_pattern("tracing_log"), None) == []


class TestPruneByTimestamp:
    """The one row rule for every log: age decides, status does not."""

    def test_row_older_than_the_window_is_pruned(self):
        # Arrange
        df = pd.DataFrame({"load_dt": [_aged(120)], "id": ["a"]})

        # Act
        kept = prune_by_timestamp(df, _cutoff(), "load_dt")

        # Assert
        assert kept.empty

    def test_row_inside_the_window_survives(self):
        # Arrange
        df = pd.DataFrame({"load_dt": [_aged(10)], "id": ["a"]})

        # Act
        kept = prune_by_timestamp(df, _cutoff(), "load_dt")

        # Assert
        assert kept["id"].tolist() == ["a"]

    def test_aged_in_flight_row_is_pruned_like_any_other(self):
        """A PENDING row past the window is a stuck file — pruning it makes it re-processable."""
        # Arrange
        log = _pre_log(
            _pre_row("/sp/stuck.pdf", "INITIAL", days_old=270),
            _pre_row("/sp/stuck.pdf", "PENDING", days_old=270),
            _pre_row("/sp/recent.pdf", "PENDING", days_old=30),
        )

        # Act
        kept = prune_by_timestamp(log, _cutoff(), "update_dt")

        # Assert — only the in-window row survives, whatever its status.
        assert kept["sharepoint_input_path"].tolist() == ["/sp/recent.pdf"]

    def test_naive_local_timestamp_is_parsed_and_pruned(self):
        # Arrange — ExportLogging writes load_dt naive ("%Y-%m-%d %H:%M:%S").
        naive = (pd.Timestamp.now(tz=BANGKOK) - pd.Timedelta(days=200)).strftime("%Y-%m-%d %H:%M:%S")
        df = pd.DataFrame({"load_dt": [naive], "id": ["a"]})

        # Act
        kept = prune_by_timestamp(df, _cutoff(), "load_dt")

        # Assert
        assert kept.empty

    def test_row_with_unparseable_timestamp_survives(self):
        """Regression guard for the QA bug (P3-13): NaT < cutoff is False, so NaT must be kept."""
        # Arrange
        df = pd.DataFrame({"load_dt": ["not-a-timestamp"], "id": ["a"]})

        # Act
        kept = prune_by_timestamp(df, _cutoff(), "load_dt")

        # Assert
        assert kept["id"].tolist() == ["a"]

    def test_missing_timestamp_column_leaves_the_frame_untouched(self):
        # Arrange
        df = pd.DataFrame({"id": ["a"]})

        # Act
        kept = prune_by_timestamp(df, _cutoff(), "load_dt")

        # Assert
        assert kept["id"].tolist() == ["a"]

    def test_zero_prune_still_logs_a_summary_line(self, caplog):
        """Cloud Logging must show that retention ran even on a day when nothing was old enough."""
        # Arrange
        df = pd.DataFrame({"load_dt": [_aged(10)], "id": ["a"]})

        # Act
        with caplog.at_level("INFO"):
            prune_by_timestamp(df, _cutoff(), "load_dt", label="transaction log")

        # Assert
        assert any("transaction log retention: pruned 0 of 1 row(s); 1 kept" in rec.message for rec in caplog.records)

    def test_prune_logs_pruned_and_kept_counts(self, caplog):
        # Arrange
        df = pd.DataFrame({"load_dt": [_aged(120), _aged(10)], "id": ["a", "b"]})

        # Act
        with caplog.at_level("INFO"):
            prune_by_timestamp(df, _cutoff(), "load_dt", label="transaction log")

        # Assert
        assert any("transaction log retention: pruned 1 of 2 row(s); 1 kept" in rec.message for rec in caplog.records)


class TestExpiredJobIds:
    def test_job_whose_every_row_is_aged_is_expired(self):
        # Arrange
        log = _pre_log(_pre_row("/sp/a.pdf", "SUCCESS", 120, job_id="job-old"))

        # Act / Assert
        assert expired_job_ids(log, _cutoff()) == {"job-old"}

    def test_job_with_one_recent_row_is_not_expired(self):
        # Arrange
        log = _pre_log(
            _pre_row("/sp/a.pdf", "SUCCESS", 120, job_id="job-mixed"),
            _pre_row("/sp/b.pdf", "SUCCESS", 30, job_id="job-mixed"),
        )

        # Act / Assert
        assert expired_job_ids(log, _cutoff()) == set()

    def test_job_with_only_aged_in_flight_rows_expires(self):
        """Status no longer protects a job: an aged PENDING job is stuck, and its pages age out."""
        # Arrange
        log = _pre_log(_pre_row("/sp/stuck.pdf", "PENDING", 270, job_id="job-stuck"))

        # Act / Assert
        assert expired_job_ids(log, _cutoff()) == {"job-stuck"}

    def test_job_with_an_unparseable_timestamp_row_is_not_expired(self):
        # Arrange
        log = _pre_log(_pre_row("/sp/a.pdf", "SUCCESS", 120, job_id="job-odd"))
        log.loc[0, "update_dt"] = "not-a-timestamp"

        # Act / Assert
        assert expired_job_ids(log, _cutoff()) == set()


class TestPruneManifest:
    def test_pages_of_an_expired_job_are_pruned(self):
        # Arrange
        manifest = pd.DataFrame({"job_id": ["job-old", "job-old"], "page_no": ["1", "2"]})

        # Act
        kept = prune_manifest(manifest, expired_ids={"job-old"})

        # Assert
        assert kept.empty

    def test_pages_of_a_job_absent_from_the_pre_log_survive(self):
        """Fail-safe: an unknown job_id (e.g. a concurrent run's fresh rows) is never pruned."""
        # Arrange
        manifest = pd.DataFrame({"job_id": ["job-concurrent"], "page_no": ["1"]})

        # Act
        kept = prune_manifest(manifest, expired_ids={"job-old"})

        # Assert
        assert kept["job_id"].tolist() == ["job-concurrent"]

    def test_prunes_only_the_expired_jobs_pages(self):
        # Arrange
        manifest = pd.DataFrame({"job_id": ["job-old", "job-live"], "page_no": ["1", "1"]})

        # Act
        kept = prune_manifest(manifest, expired_ids={"job-old"})

        # Assert
        assert kept["job_id"].tolist() == ["job-live"]

    def test_no_expired_jobs_returns_the_frame_unchanged(self):
        # Arrange
        manifest = pd.DataFrame({"job_id": ["job-live"], "page_no": ["1"]})

        # Act
        kept = prune_manifest(manifest, expired_ids=set())

        # Assert
        assert kept["job_id"].tolist() == ["job-live"]

    def test_prune_logs_a_summary_with_row_and_job_counts(self, caplog):
        # Arrange
        manifest = pd.DataFrame({"job_id": ["job-old", "job-live"], "page_no": ["1", "1"]})

        # Act
        with caplog.at_level("INFO"):
            prune_manifest(manifest, expired_ids={"job-old"})

        # Assert
        assert any(
            "page-manifest retention: pruned 1 of 2 row(s) across 1 fully-expired job(s)" in rec.message
            for rec in caplog.records
        )

    def test_no_expired_jobs_still_logs_a_zero_summary(self, caplog):
        # Arrange
        manifest = pd.DataFrame({"job_id": ["job-live"], "page_no": ["1"]})

        # Act
        with caplog.at_level("INFO"):
            prune_manifest(manifest, expired_ids=set())

        # Assert
        assert any(
            "page-manifest retention: pruned 0 of 1 row(s) across 0 fully-expired job(s)" in rec.message
            for rec in caplog.records
        )


class TestExpiredMonthFiles:
    PATTERN = month_file_pattern("transaction_log")

    def test_month_entirely_before_the_cutoffs_month_is_expired(self):
        # Arrange
        cutoff = pd.Timestamp("2026-04-15", tz=BANGKOK)
        names = ["transaction_log_202601.csv", "transaction_log_202603.csv"]

        # Act / Assert
        assert expired_month_files(names, self.PATTERN, cutoff) == names

    def test_the_cutoffs_own_month_is_never_deleted(self):
        """The cutoff falls mid-month, so that file still holds in-window rows."""
        # Arrange
        cutoff = pd.Timestamp("2026-04-15", tz=BANGKOK)

        # Act / Assert
        assert expired_month_files(["transaction_log_202604.csv"], self.PATTERN, cutoff) == []

    def test_a_future_month_is_never_deleted(self):
        # Arrange
        cutoff = pd.Timestamp("2026-04-15", tz=BANGKOK)

        # Act / Assert
        assert expired_month_files(["transaction_log_202607.csv"], self.PATTERN, cutoff) == []

    def test_non_matching_names_are_ignored(self):
        # Arrange
        cutoff = pd.Timestamp("2026-04-15", tz=BANGKOK)
        names = ["README.md", "performance_log_202601.csv", "transaction_log_202601.csv"]

        # Act / Assert — the performance file must not match the transaction pattern
        assert expired_month_files(names, self.PATTERN, cutoff) == ["transaction_log_202601.csv"]


class TestSweepMonthFiles:
    PATTERN = month_file_pattern("transaction_log")

    @staticmethod
    def _sp_with(names: list[str]) -> Mock:
        sp = Mock()
        sp.list_files.return_value = [{"name": name} for name in names]
        return sp

    def test_summary_counts_deletions_against_month_file_candidates_only(self, caplog):
        # Arrange — README.md must not count as a candidate.
        sp = self._sp_with(["transaction_log_202001.csv", "transaction_log_202607.csv", "README.md"])
        cutoff = pd.Timestamp("2026-04-15", tz=BANGKOK)

        # Act
        with caplog.at_level("INFO"):
            sweep_month_files(sp, "/sp/logs/transaction_log_202607.csv", self.PATTERN, cutoff, "transaction log")

        # Assert
        sp.delete_item.assert_called_once_with("/sp/logs/transaction_log_202001.csv")
        assert any(
            "transaction log retention sweep: deleted 1 of 2 month-file(s) in /sp/logs" in rec.message
            for rec in caplog.records
        )

    def test_nothing_expired_still_logs_a_zero_summary(self, caplog):
        # Arrange
        sp = self._sp_with(["transaction_log_202607.csv"])
        cutoff = pd.Timestamp("2026-04-15", tz=BANGKOK)

        # Act
        with caplog.at_level("INFO"):
            sweep_month_files(sp, "/sp/logs/transaction_log_202607.csv", self.PATTERN, cutoff, "transaction log")

        # Assert
        sp.delete_item.assert_not_called()
        assert any(
            "transaction log retention sweep: deleted 0 of 1 month-file(s) in /sp/logs" in rec.message
            for rec in caplog.records
        )


class TestRetentionCutoff:
    def test_cutoff_is_the_window_back_in_the_given_timezone(self):
        # Act
        cutoff = retention_cutoff(RETENTION_DAYS, tz=BANGKOK)

        # Assert
        assert str(cutoff.tzinfo) == DEFAULT_TIMEZONE
        expected = pd.Timestamp.now(tz=BANGKOK) - pd.Timedelta(days=RETENTION_DAYS)
        assert abs((cutoff - expected).total_seconds()) < 5

    def test_cutoff_falls_back_to_the_default_timezone_when_none_is_given(self):
        # Act
        cutoff = retention_cutoff(RETENTION_DAYS, tz=None)

        # Assert
        assert str(cutoff.tzinfo) == DEFAULT_TIMEZONE

    def test_active_window_is_logged_with_cutoff_and_timezone(self, caplog):
        """Cloud Logging must state the resolved window — not only the disabled case."""
        # Act
        with caplog.at_level("INFO"):
            cutoff = retention_cutoff(RETENTION_DAYS, tz=BANGKOK)

        # Assert
        lines = [rec.message for rec in caplog.records if "log retention active" in rec.message]
        assert lines, "expected an INFO line stating the active retention window"
        assert f"window={RETENTION_DAYS} day(s)" in lines[0]
        assert cutoff.isoformat() in lines[0]
        assert DEFAULT_TIMEZONE in lines[0]


class TestLogExporterRetention:
    """Retention is intrinsic: LogExporter prunes the merged frame, unconditionally, before upload."""

    @staticmethod
    def _csv_bytes(df: pd.DataFrame) -> bytes:
        return df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")

    @staticmethod
    def _written_frame(call_args) -> pd.DataFrame:
        return pd.read_csv(io.BytesIO(call_args[0][0]), dtype=str)

    def _gcs_with(self, existing: pd.DataFrame) -> Mock:
        gcs = Mock(bucket_name="b")
        gcs.download_bytes_with_generation.return_value = (self._csv_bytes(existing), 5)
        return gcs

    def test_aged_rows_are_pruned_regardless_of_status(self):
        # Arrange — a terminal and an in-flight row, both past the window.
        existing = _pre_log(
            _pre_row("/sp/old.pdf", "SUCCESS", days_old=120),
            _pre_row("/sp/stuck.pdf", "PENDING", days_old=270),
        )
        gcs = self._gcs_with(existing)
        exporter = LogExporter(gcs, Mock(), retention_days=RETENTION_DAYS, timezone=BANGKOK)

        # Act
        exporter.save_log(
            _pre_log(_pre_row("/sp/new.pdf", "PENDING", days_old=0)),
            "gs://b/pre.csv",
            "/sp/pre.csv",
        )

        # Assert
        written = self._written_frame(gcs.update_content_to_gcs.call_args)
        assert written["sharepoint_input_path"].tolist() == ["/sp/new.pdf"]

    def test_negative_retention_days_keeps_every_row(self):
        # Arrange
        existing = _pre_log(_pre_row("/sp/ancient.pdf", "SUCCESS", days_old=3650))
        gcs = self._gcs_with(existing)
        exporter = LogExporter(gcs, Mock(), retention_days=-1, timezone=BANGKOK)

        # Act
        exporter.save_log(
            _pre_log(_pre_row("/sp/new.pdf", "PENDING", days_old=0)),
            "gs://b/pre.csv",
            "/sp/pre.csv",
        )

        # Assert
        written = self._written_frame(gcs.update_content_to_gcs.call_args)
        assert sorted(written["sharepoint_input_path"]) == ["/sp/ancient.pdf", "/sp/new.pdf"]

    def test_manifest_rows_of_an_expired_job_are_pruned(self):
        # Arrange
        existing = pd.DataFrame({"job_id": ["job-old"], "page_no": ["1"]})
        gcs = self._gcs_with(existing)
        exporter = LogExporter(gcs, Mock(), retention_days=RETENTION_DAYS, timezone=BANGKOK)

        # Act
        exporter.save_log(
            pd.DataFrame({"job_id": ["job-new"], "page_no": ["1"]}),
            "gs://b/manifest.csv",
            "/sp/manifest.csv",
            expired_ids={"job-old"},
        )

        # Assert
        written = self._written_frame(gcs.update_content_to_gcs.call_args)
        assert written["job_id"].tolist() == ["job-new"]

    def test_prune_is_reapplied_on_the_precondition_retry(self):
        # Arrange — the reload after the lost race brings back the aged rows, which must be pruned
        # again (the rule runs on every attempt, not once).
        aged = _pre_log(_pre_row("/sp/old.pdf", "SUCCESS", days_old=120))
        gcs = Mock(bucket_name="b")
        gcs.download_bytes_with_generation.side_effect = [
            (self._csv_bytes(aged), 5),
            (self._csv_bytes(aged), 9),
        ]
        gcs.update_content_to_gcs.side_effect = [PreconditionFailed("race"), None]
        exporter = LogExporter(gcs, Mock(), retention_days=RETENTION_DAYS, timezone=BANGKOK)

        # Act
        exporter.save_log(
            _pre_log(_pre_row("/sp/new.pdf", "PENDING", days_old=0)),
            "gs://b/pre.csv",
            "/sp/pre.csv",
        )

        # Assert
        assert gcs.update_content_to_gcs.call_count == 2
        last = gcs.update_content_to_gcs.call_args
        assert last[1]["if_generation_match"] == 9
        assert self._written_frame(last)["sharepoint_input_path"].tolist() == ["/sp/new.pdf"]
