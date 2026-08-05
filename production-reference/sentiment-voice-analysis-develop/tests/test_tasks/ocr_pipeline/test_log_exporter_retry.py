"""Tests for the LogExporter optimistic-concurrency retry (the log-write race fix)."""

import io
from unittest.mock import Mock

import pandas as pd
import pytest
from google.api_core.exceptions import PreconditionFailed

from tasks.ocr_tax_invoice_pipeline.module.log_exporter import LogExporter


def _csv_bytes(df):
    return df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")


def _written_frame(call_args):
    return pd.read_csv(io.BytesIO(call_args[0][0]), dtype=str)


def test_load_returns_dataframe_not_tuple():
    gcs = Mock(bucket_name="b")
    gcs.download_bytes_with_generation.return_value = (_csv_bytes(pd.DataFrame({"a": ["x"]})), 3)

    df = LogExporter(gcs, Mock()).load_log("gs://b/log.csv")

    assert isinstance(df, pd.DataFrame)
    assert df["a"].tolist() == ["x"]


def test_fresh_log_uses_create_only_precondition():
    gcs = Mock(bucket_name="b")
    gcs.download_bytes_with_generation.side_effect = FileNotFoundError()

    LogExporter(gcs, Mock()).save_log(pd.DataFrame({"a": ["1"]}), "gs://b/log.csv", "/sp/log.csv")

    args = gcs.update_content_to_gcs.call_args
    assert args[1]["if_generation_match"] == 0  # object must not yet exist
    assert args[0][2] == "log.csv"  # bucket-relative path


def test_append_to_existing_uses_its_generation():
    gcs = Mock(bucket_name="b")
    gcs.download_bytes_with_generation.return_value = (_csv_bytes(pd.DataFrame({"a": ["old"]})), 5)

    LogExporter(gcs, Mock()).save_log(pd.DataFrame({"a": ["new"]}), "gs://b/log.csv", "/sp/log.csv")

    args = gcs.update_content_to_gcs.call_args
    assert args[1]["if_generation_match"] == 5
    assert _written_frame(args)["a"].tolist() == ["old", "new"]


def test_retries_once_and_merges_concurrent_rows():
    gcs = Mock(bucket_name="b")
    # First read sees generation 5; after the lost race the reload sees the concurrent writer's
    # rows at generation 9.
    gcs.download_bytes_with_generation.side_effect = [
        (_csv_bytes(pd.DataFrame({"a": ["old"]})), 5),
        (_csv_bytes(pd.DataFrame({"a": ["old", "concurrent"]})), 9),
    ]
    gcs.update_content_to_gcs.side_effect = [PreconditionFailed("race"), None]

    LogExporter(gcs, Mock()).save_log(pd.DataFrame({"a": ["new"]}), "gs://b/log.csv", "/sp/log.csv")

    assert gcs.update_content_to_gcs.call_count == 2
    last = gcs.update_content_to_gcs.call_args
    assert last[1]["if_generation_match"] == 9
    assert _written_frame(last)["a"].tolist() == ["old", "concurrent", "new"]  # nothing dropped


def test_raises_after_losing_the_race_twice():
    gcs = Mock(bucket_name="b")
    gcs.download_bytes_with_generation.return_value = (_csv_bytes(pd.DataFrame({"a": ["old"]})), 5)
    gcs.update_content_to_gcs.side_effect = PreconditionFailed("race")

    with pytest.raises(PreconditionFailed):
        LogExporter(gcs, Mock()).save_log(pd.DataFrame({"a": ["new"]}), "gs://b/log.csv", "/sp/log.csv")

    assert gcs.update_content_to_gcs.call_count == 2


def test_save_log_when_sharepoint_mirror_fails_logs_warning_and_does_not_raise(caplog):
    gcs = Mock(bucket_name="b")
    gcs.download_bytes_with_generation.side_effect = FileNotFoundError()
    sp = Mock()
    sp.upload_file.side_effect = Exception("sharepoint down")

    with caplog.at_level("WARNING"):
        LogExporter(gcs, sp).save_log(pd.DataFrame({"a": ["1"]}), "gs://b/log.csv", "/sp/log.csv")

    assert gcs.update_content_to_gcs.call_count == 1  # GCS write still succeeded
    assert any("SharePoint mirror failed" in rec.message for rec in caplog.records)


def test_save_log_with_sort_by_sorts_merged_rows_descending():
    gcs = Mock(bucket_name="b")
    gcs.download_bytes_with_generation.return_value = (_csv_bytes(pd.DataFrame({"a": ["1", "3"]})), 5)

    LogExporter(gcs, Mock()).save_log(pd.DataFrame({"a": ["2"]}), "gs://b/log.csv", "/sp/log.csv", sort_by="a")

    args = gcs.update_content_to_gcs.call_args
    assert _written_frame(args)["a"].tolist() == ["3", "2", "1"]


def test_load_csv_when_read_raises_non_file_not_found_returns_empty_frame_and_warns(caplog):
    gcs = Mock(bucket_name="b")
    gcs.download_bytes_with_generation.side_effect = ValueError("corrupt")

    with caplog.at_level("WARNING"):
        df = LogExporter(gcs, Mock()).load_log("gs://b/log.csv")

    assert df.empty
    assert any("Could not load existing CSV" in rec.message for rec in caplog.records)


def test_save_log_with_bucket_relative_path_leaves_it_unchanged():
    gcs = Mock(bucket_name="b")
    gcs.download_bytes_with_generation.side_effect = FileNotFoundError()

    LogExporter(gcs, Mock()).save_log(pd.DataFrame({"a": ["1"]}), "bucket-relative/log.csv", "/sp/log.csv")

    args = gcs.update_content_to_gcs.call_args
    assert args[0][2] == "bucket-relative/log.csv"
