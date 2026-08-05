"""Unit tests for FraudValidationPipeline."""
from __future__ import annotations

import io
import pandas as pd
import polars as pl
import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch, call

from app.core.models import PipelineConfig, ShopResult
from app.pipeline.fraud_pipeline import FraudValidationPipeline


# ---------------------------------------------------------------------------
# Helpers / Fixtures
# ---------------------------------------------------------------------------

def _make_config(today: datetime | None = None) -> PipelineConfig:
    return PipelineConfig(
        batch_size=2,
        s3_bucket="my-bucket",
        gcs_bucket="gcs-bucket",
        today=today or datetime(2024, 3, 15),
        project_id="proj-123",
        project_name="RTR",
        fraud_site_base_root="fraud_root",
        input_folder="input",
        output_folder="output",
        backup_folder="backup",
        archive_folder="archive",
        control_site_base_root="control_root",
        control_site_prompts_root="prompts",
        control_site_transaction_log_path="tx_log",
        control_site_performance_log_path="perf_log",
        control_site_cost_path="cost",
        recipient_emails=["team@example.com"],
        rsa_private_key="rsa_key",
    )


@pytest.fixture()
def mock_deps() -> dict:
    """Return a dict of mock dependencies for FraudValidationPipeline."""
    return {
        "config": _make_config(),
        "fraud_sp": MagicMock(),
        "control_sp": MagicMock(),
        "s3": MagicMock(),
        "gcs": MagicMock(),
        "shop_processor": MagicMock(),
        "report_builder": MagicMock(),
        "email_composer": MagicMock(),
        "email_service": MagicMock(),
    }


@pytest.fixture()
def pipeline(mock_deps: dict) -> FraudValidationPipeline:
    return FraudValidationPipeline(
        config=mock_deps["config"],
        fraud_sp=mock_deps["fraud_sp"],
        control_sp=mock_deps["control_sp"],
        s3=mock_deps["s3"],
        gcs=mock_deps["gcs"],
        shop_processor=mock_deps["shop_processor"],
        report_builder=mock_deps["report_builder"],
        email_composer=mock_deps["email_composer"],
        email_service=mock_deps["email_service"],
    )


def _make_minimal_df(**extra_cols: list) -> pl.DataFrame:
    """Build a minimal DataFrame with columns required by _process_shops."""
    base = {
        "original_row_id": [0],
        "status": [""],
        "xd_rtr_code": ["XD001"],
        "xd_rtr_name": ["Shop A"],
        "xt_rtr_code": ["XT001"],
        "xt_rtr_name": ["Shop B"],
        "xt_ga": [1],
        "photo_1_path": ["s3://bucket/photo1.jpg"],
        "photo_2_path": ["s3://bucket/photo2.jpg"],
        "photo_3_path": ["s3://bucket/photo3.jpg"],
        "shop_path": ["some/path"],
        "rtr_lat": ["13.0"],
        "rtr_lon": ["100.0"],
    }
    base.update(extra_cols)
    return pl.DataFrame(base)


# ---------------------------------------------------------------------------
# run() — top-level entry point
# ---------------------------------------------------------------------------

class TestRun:
    async def test_happy_path_calls_notify_and_cleanup(
        self, pipeline: FraudValidationPipeline
    ) -> None:
        join_df = pl.DataFrame({"col": ["val"]})
        excel_bytes = b"excel"

        with patch.object(
            pipeline, "_run_pipeline", new_callable=AsyncMock, return_value=(None, join_df, excel_bytes)
        ):
            with patch.object(pipeline, "_notify", new_callable=AsyncMock) as mock_notify:
                with patch.object(pipeline, "_cleanup") as mock_cleanup:
                    await pipeline.run()

        mock_notify.assert_awaited_once_with(join_df, excel_bytes)
        mock_cleanup.assert_called_once()

    async def test_exception_calls_notify_error_and_reraises(
        self, pipeline: FraudValidationPipeline
    ) -> None:
        error = RuntimeError("stage failed")

        with patch.object(pipeline, "_run_pipeline", new_callable=AsyncMock, side_effect=error):
            with patch.object(pipeline, "_notify_error", new_callable=AsyncMock) as mock_err:
                with patch.object(pipeline, "_cleanup"):
                    with pytest.raises(RuntimeError, match="stage failed"):
                        await pipeline.run()

        mock_err.assert_awaited_once_with(error)

    async def test_cleanup_always_called_even_on_exception(
        self, pipeline: FraudValidationPipeline
    ) -> None:
        with patch.object(
            pipeline, "_run_pipeline", new_callable=AsyncMock, side_effect=Exception("boom")
        ):
            with patch.object(pipeline, "_notify_error", new_callable=AsyncMock):
                with patch.object(pipeline, "_cleanup") as mock_cleanup:
                    with pytest.raises(Exception):
                        await pipeline.run()

        mock_cleanup.assert_called_once()


# ---------------------------------------------------------------------------
# _process_shops()
# ---------------------------------------------------------------------------

class TestProcessShops:
    async def test_returns_df_unchanged_when_zero_rows(
        self, pipeline: FraudValidationPipeline
    ) -> None:
        empty_df = pl.DataFrame(
            {
                "original_row_id": pl.Series([], dtype=pl.Int64),
                "status": pl.Series([], dtype=pl.Utf8),
                "xd_rtr_code": pl.Series([], dtype=pl.Utf8),
                "xt_rtr_code": pl.Series([], dtype=pl.Utf8),
                "xt_ga": pl.Series([], dtype=pl.Int64),
            }
        )
        result = await pipeline._process_shops(empty_df, [], "prompt")
        assert result.height == 0

    async def test_processes_rows_and_updates_df(
        self, pipeline: FraudValidationPipeline, mock_deps: dict
    ) -> None:
        output_headers = [
            "Run_Date", "folder_name", "rtr_code", "rtr_name", "number_of_images",
            "photo_name_1", "photo_name_2", "photo_name_3",
            "photo1_lat", "photo1_long", "rtr1_lat", "rtr1_long", "Photo1_Flag300",
            "same_photo", "from_other_device", "closed_business", "un_relate",
            "un_relate_human", "un_relate_animal", "un_relate_location",
            "un_relate_object", "Complaint_Status",
        ]
        # Build df with all output header columns pre-populated as empty
        df_data: dict = {
            "original_row_id": [0],
            "status": [""],
            "xd_rtr_code": ["XD001"],
            "xt_rtr_code": ["XT001"],
            "xt_ga": [1],
            "photo_1_path": ["s3://b/photo1.jpg"],
            "photo_2_path": ["s3://b/photo2.jpg"],
            "photo_3_path": ["s3://b/photo3.jpg"],
            "shop_path": ["path"],
            "rtr_lat": ["13.0"],
            "rtr_lon": ["100.0"],
            "xd_rtr_name": ["Shop A"],
            "xt_rtr_name": ["Shop B"],
        }
        for col in output_headers:
            df_data[col] = [""]
        df = pl.DataFrame(df_data)

        # Create a mock ShopResult with matching output list
        mock_result = MagicMock(spec=ShopResult)
        mock_result.original_row_id = 0
        mock_result.rtr_code = "XD001"
        mock_result.status = "success"
        mock_result.to_output_list.return_value = [""] * 22
        mock_result.to_log_dict.return_value = {"status": "success", "rtr_code": "XD001"}

        mock_deps["shop_processor"].process = AsyncMock(return_value=mock_result)
        mock_deps["report_builder"].protect_excel_value = MagicMock(side_effect=lambda x: x)
        pipeline._gcs.invalidate_cache = MagicMock()
        pipeline._gcs.write_text = MagicMock()

        result = await pipeline._process_shops(df, output_headers, "prompt")
        mock_deps["shop_processor"].process.assert_awaited_once()
        assert result is not None


# ---------------------------------------------------------------------------
# _cleanup()
# ---------------------------------------------------------------------------

class TestCleanup:
    def test_deletes_all_set_gcs_paths(self, pipeline: FraudValidationPipeline) -> None:
        pipeline._input_gcs_path = "input/file.csv"
        pipeline._lookup_gcs_path = "input/lookup.csv"
        pipeline._user_gcs_path = "output/report.xlsx"
        pipeline._cleanup()
        pipeline._gcs.delete.assert_any_call("input/file.csv")
        pipeline._gcs.delete.assert_any_call("input/lookup.csv")
        pipeline._gcs.delete.assert_any_call("output/report.xlsx")

    def test_skips_empty_paths(self, pipeline: FraudValidationPipeline) -> None:
        pipeline._input_gcs_path = ""
        pipeline._lookup_gcs_path = ""
        pipeline._user_gcs_path = ""
        pipeline._cleanup()
        pipeline._gcs.delete.assert_not_called()

    def test_deletes_only_set_paths(self, pipeline: FraudValidationPipeline) -> None:
        pipeline._input_gcs_path = "input/file.csv"
        pipeline._lookup_gcs_path = ""
        pipeline._user_gcs_path = ""
        pipeline._cleanup()
        pipeline._gcs.delete.assert_called_once_with("input/file.csv")


# ---------------------------------------------------------------------------
# _notify()
# ---------------------------------------------------------------------------

class TestNotify:
    async def test_composes_and_sends_email(
        self, pipeline: FraudValidationPipeline, mock_deps: dict
    ) -> None:
        join_df = pl.DataFrame({"col": ["val"]})
        user_excel = b"excel_bytes"
        pipeline._user_file_name = "report.xlsx"

        mock_deps["email_composer"].compose.return_value = ("Subject", "<html>", {"img.png": "b64"})
        mock_deps["email_service"].send = AsyncMock()

        await pipeline._notify(join_df, user_excel)

        mock_deps["email_composer"].compose.assert_called_once()
        mock_deps["email_service"].send.assert_awaited_once()
        call_kwargs = mock_deps["email_service"].send.call_args[1]
        assert call_kwargs["subject"] == "Subject"
        assert call_kwargs["recipients"] == ["team@example.com"]


# ---------------------------------------------------------------------------
# _notify_error()
# ---------------------------------------------------------------------------

class TestNotifyError:
    async def test_sends_error_email(
        self, pipeline: FraudValidationPipeline, mock_deps: dict
    ) -> None:
        mock_deps["email_service"].send = AsyncMock()
        exc = RuntimeError("something broke")
        await pipeline._notify_error(exc)
        mock_deps["email_service"].send.assert_awaited_once()
        call_kwargs = mock_deps["email_service"].send.call_args[1]
        assert "Fail" in call_kwargs["subject"]
        assert "something broke" in call_kwargs["body_html"]

    async def test_swallows_exception_from_send(
        self, pipeline: FraudValidationPipeline, mock_deps: dict
    ) -> None:
        mock_deps["email_service"].send = AsyncMock(side_effect=Exception("email error"))
        # Must NOT raise; the exception from send is swallowed
        await pipeline._notify_error(RuntimeError("original"))


# ---------------------------------------------------------------------------
# _normalise_transaction_df() (static)
# ---------------------------------------------------------------------------

class TestNormaliseTransactionDf:
    def test_converts_timestamp_columns(self) -> None:
        df = pd.DataFrame(
            {
                "start_time": ["2024-03-15 10:00:00+00:00"],
                "end_time": ["2024-03-15 10:01:00+00:00"],
                "data_date": ["20240315"],
            }
        )
        FraudValidationPipeline._normalise_transaction_df(df)
        # Should not raise and data_date should be string
        assert df["data_date"].dtype == object

    def test_handles_missing_columns_gracefully(self) -> None:
        df = pd.DataFrame({"other_col": [1, 2]})
        FraudValidationPipeline._normalise_transaction_df(df)
        assert list(df.columns) == ["other_col"]


# ---------------------------------------------------------------------------
# _build_user_excel()
# ---------------------------------------------------------------------------

class TestBuildUserExcel:
    def test_returns_bytes(self, pipeline: FraudValidationPipeline) -> None:
        join_df = pl.DataFrame(
            {
                "Complaint_Status": ["inComplaint"],
                "Suspicious": ["Yes"],
                "xd_rtr_code": ["XD001"],
                "xd_rtr_name": ["Shop A"],
                "xt_rtr_code": ["XT001"],
                "xt_rtr_name": ["Shop B"],
                "zone_name": ["North"],
                "cluster_name": ["C1"],
                "xd_pbh_sale_name": ["pbh"],
                "xd_rsr_sale_name": ["rsr"],
                "xt_pbh_sale_name": ["pbh"],
                "xt_rsr_sale_name": ["rsr"],
                "verified_by_pbh": [""],
                "date_of_verified_by_pbh": [""],
            }
        )
        resolve_config: dict = {}

        mock_buf = MagicMock()
        mock_buf.getvalue.return_value = b"excel_content"

        with patch("app.pipeline.fraud_pipeline.get_value_by_path", return_value=["xd_rtr_code"]):
            with patch("app.pipeline.fraud_pipeline.ReportBuilder") as mock_rb_cls:
                mock_rb_inst = MagicMock()
                mock_rb_inst.build_user_excel.return_value = mock_buf
                mock_rb_cls.return_value = mock_rb_inst
                result = pipeline._build_user_excel(join_df, resolve_config)

        assert result == b"excel_content"


# ---------------------------------------------------------------------------
# _build_join_df() — day-01 branch
# ---------------------------------------------------------------------------

class TestBuildJoinDf:
    def _make_gcs_csv(self) -> bytes:
        """Return CSV bytes with all required columns for _build_join_df."""
        cols = [
            "Run_Date", "xd_rtr_code", "xd_rtr_name", "xt_rtr_code", "xt_rtr_name",
            "Complaint_Status", "xd_zone_name", "xt_zone_name",
            "xd_cluster_name", "xt_cluster_name",
            "xd_pbh_sale_name", "xd_rsr_sale_name", "xt_pbh_sale_name", "xt_rsr_sale_name",
            "xd_ga", "xt_ga", "last_photo_update", "no_visit_last_month",
            "no_visit", "last_visit_date",
            "xd_appointment_date", "xt_appointment_date",
            "Photo1_Flag300", "xd_last_verified_by", "xt_last_verified_by",
            "xd_last_verified_date", "xt_last_verified_date",
            "partner_status", "partner_code",
        ]
        # xt_ga must be numeric (or empty) — use "1" to satisfy Int64 cast
        vals = ["2024-03-01", "XD001", "ShopA", "XT001", "ShopB", "inComplaint",
                "North", "North", "C1", "C1",
                "pbh", "rsr", "pbh", "rsr",
                "1", "1", "2024-03-01", "0",
                "0", "2024-03-01",
                "", "", "Y",
                "", "", "", "",
                "Active", "XD001"]
        rows = [",".join(cols), ",".join(vals)]
        return "\n".join(rows).encode("utf-8")

    def test_day_01_branch(self, pipeline: FraudValidationPipeline) -> None:
        """On day 01, _build_join_df uses GA from original file (no S3 fetch)."""
        pipeline._cfg = _make_config(today=datetime(2024, 3, 1))
        pipeline._lookup_gcs_path = "input/lookup.csv"
        pipeline._input_gcs_path = "input/data.csv"

        lookup_csv = b"partner_code,partner_status\nXD001,Active\n"
        input_csv = self._make_gcs_csv()

        pipeline._gcs.read_bytes.return_value = lookup_csv
        pipeline._gcs.read_text.return_value = input_csv.decode("utf-8")
        pipeline._gcs.invalidate_cache = MagicMock()

        result = pipeline._build_join_df(initial_headers=[])
        assert isinstance(result, pl.DataFrame)

    def test_non_day_01_calls_join_with_month_start(
        self, pipeline: FraudValidationPipeline
    ) -> None:
        pipeline._cfg = _make_config(today=datetime(2024, 3, 15))
        pipeline._lookup_gcs_path = "input/lookup.csv"
        pipeline._input_gcs_path = "input/data.csv"

        lookup_csv = b"partner_code,partner_status\nXD001,Active\n"
        input_csv = self._make_gcs_csv()

        pipeline._gcs.read_bytes.return_value = lookup_csv
        pipeline._gcs.read_text.return_value = input_csv.decode("utf-8")
        pipeline._gcs.invalidate_cache = MagicMock()

        with patch.object(pipeline, "_join_with_month_start_file") as mock_join:
            mock_join.return_value = pl.DataFrame({"col": ["val"]})
            result = pipeline._build_join_df(initial_headers=[])

        mock_join.assert_called_once()
        assert isinstance(result, pl.DataFrame)


# ---------------------------------------------------------------------------
# _join_with_month_start_file() — fallback branch
# ---------------------------------------------------------------------------

class TestJoinWithMonthStartFile:
    def _make_original_df(self) -> pl.DataFrame:
        return pl.DataFrame(
            {
                "Run_Date": ["2024-03-15"],
                "xd_rtr_code": ["XD001"],
                "xd_rtr_name": ["ShopA"],
                "xt_rtr_code": ["XT001"],
                "xt_rtr_name": ["ShopB"],
                "Complaint_Status": ["inComplaint"],
                "xd_zone_name": ["North"],
                "xt_zone_name": ["North"],
                "xd_cluster_name": ["C1"],
                "xt_cluster_name": ["C1"],
                "xd_pbh_sale_name": ["pbh"],
                "xd_rsr_sale_name": ["rsr"],
                "xt_pbh_sale_name": ["pbh"],
                "xt_rsr_sale_name": ["rsr"],
                "xd_ga": ["1"],
                "xt_ga": [1],
                "last_photo_update": ["2024-03-01"],
                "no_visit_last_month": [0],
                "no_visit": [0],
                "last_visit_date": ["2024-03-01"],
                "xd_appointment_date": [""],
                "xt_appointment_date": [""],
                "Photo1_Flag300": ["Y"],
                "xd_last_verified_by": [""],
                "xt_last_verified_by": [""],
                "xd_last_verified_date": [""],
                "xt_last_verified_date": [""],
            }
        )

    def test_falls_back_to_original_on_s3_error(
        self, pipeline: FraudValidationPipeline
    ) -> None:
        pipeline._s3.normalise_key.side_effect = Exception("S3 unavailable")

        original_df = self._make_original_df()
        suspicious_expr = pl.lit("").alias("Suspicious")
        either_invalid = pl.lit(False)

        with patch("app.pipeline.fraud_pipeline.load_yaml_string"):
            with patch("app.pipeline.fraud_pipeline.resolve_env"):
                with patch("app.pipeline.fraud_pipeline.read_file"):
                    with patch("app.pipeline.fraud_pipeline.get_value_by_path", return_value="path/yyyymmdd/file.csv"):
                        result = pipeline._join_with_month_start_file(
                            original_df, suspicious_expr, either_invalid, []
                        )
        assert isinstance(result, pl.DataFrame)


# ---------------------------------------------------------------------------
# _publish()
# ---------------------------------------------------------------------------

class TestPublish:
    def _make_join_df(self) -> pl.DataFrame:
        return pl.DataFrame(
            {
                "original_row_id": [0],
                "col1": ["val"],
            }
        )

    async def test_uploads_to_sharepoint_and_gcs(
        self, pipeline: FraudValidationPipeline, mock_deps: dict
    ) -> None:
        join_df = self._make_join_df()
        user_excel = b"excel"
        resolve_config: dict = {}

        mock_deps["fraud_sp"].upload_file = MagicMock()
        mock_deps["gcs"].write_bytes = MagicMock()
        mock_deps["gcs"].write_text = MagicMock()

        with patch("app.pipeline.fraud_pipeline.get_value_by_path", return_value="output/file.csv"):
            with patch.object(pipeline, "_upload_transaction_log", new_callable=AsyncMock):
                with patch.object(pipeline, "_upload_performance_log", new_callable=AsyncMock):
                    await pipeline._publish(join_df, user_excel, resolve_config)

        # Input CSV uploaded to SharePoint
        assert mock_deps["fraud_sp"].upload_file.call_count >= 2
        # User Excel written to GCS
        mock_deps["gcs"].write_bytes.assert_called_once()


# ---------------------------------------------------------------------------
# _upload_transaction_log()
# ---------------------------------------------------------------------------

class TestUploadTransactionLog:
    async def test_uploads_new_log_when_no_existing(
        self, pipeline: FraudValidationPipeline, mock_deps: dict
    ) -> None:
        pipeline._logs = [
            {
                "status": "success",
                "start_time": "2024-03-15 10:00:00",
                "end_time": "2024-03-15 10:01:00",
                "process_time": 60,
                "message": "ok",
                "rtr_code": "XD001",
                "rtr_name": "Shop A",
                "image_parts": ["a", "b"],
                "meta_data": {"text_input_tokens": 100, "image_input_tokens": 50,
                              "text_cache_tokens": 0, "image_cache_tokens": 0,
                              "output_tokens": 20, "total_input_tokens": 150},
            }
        ]
        resolve_config: dict = {}
        mock_deps["control_sp"].get_download_url.return_value = None
        mock_deps["control_sp"].upload_file = MagicMock()

        with patch("app.pipeline.fraud_pipeline.get_value_by_path", return_value=["status", "rtr_code"]):
            with patch("app.pipeline.fraud_pipeline.ReportBuilder") as mock_rb_cls:
                mock_rb_inst = MagicMock()
                mock_rb_inst.build_transaction_row.return_value = {"status": "success", "rtr_code": "XD001"}
                mock_rb_cls.return_value = mock_rb_inst
                await pipeline._upload_transaction_log(resolve_config)

        mock_deps["control_sp"].upload_file.assert_called_once()

    async def test_merges_with_existing_log(
        self, pipeline: FraudValidationPipeline, mock_deps: dict
    ) -> None:
        pipeline._logs = []
        resolve_config: dict = {}
        existing_csv = b"status,rtr_code\nsuccess,OLD001\n"
        mock_deps["control_sp"].get_download_url.return_value = "http://existing.csv"
        mock_deps["control_sp"].upload_file = MagicMock()

        with patch("app.pipeline.fraud_pipeline.get_value_by_path", return_value=["status"]):
            with patch("app.pipeline.fraud_pipeline.ReportBuilder") as mock_rb_cls:
                mock_rb_inst = MagicMock()
                mock_rb_inst.build_transaction_row.return_value = {}
                mock_rb_cls.return_value = mock_rb_inst
                with patch("app.pipeline.fraud_pipeline.requests.get") as mock_req:
                    mock_req.return_value.content = existing_csv
                    await pipeline._upload_transaction_log(resolve_config)

        mock_deps["control_sp"].upload_file.assert_called_once()


# ---------------------------------------------------------------------------
# _upload_performance_log()
# ---------------------------------------------------------------------------

class TestUploadPerformanceLog:
    async def test_processes_transaction_files(
        self, pipeline: FraudValidationPipeline, mock_deps: dict
    ) -> None:
        tx_csv = (
            "data_date,start_time,end_time,status_pass_failed_retry,error_log_if,"
            "gcp_project_id,gcp_project_name\n"
            "20240315,2024-03-15 10:00:00+00:00,2024-03-15 10:01:00+00:00,success,none,proj,RTR\n"
        )
        mock_deps["control_sp"].list_folder_names.return_value = ["2024"]
        mock_deps["control_sp"].list_files.return_value = [{"id": "file-1", "name": "transaction_log_202403.csv"}]
        mock_deps["control_sp"].download_file_by_id.return_value = tx_csv.encode("utf-8")
        mock_deps["control_sp"].upload_file = MagicMock()

        await pipeline._upload_performance_log()

        mock_deps["control_sp"].upload_file.assert_called_once()

    async def test_skips_file_on_exception(
        self, pipeline: FraudValidationPipeline, mock_deps: dict
    ) -> None:
        mock_deps["control_sp"].list_folder_names.return_value = ["2024"]
        mock_deps["control_sp"].list_files.return_value = [{"id": "bad", "name": "tx.csv"}]
        mock_deps["control_sp"].download_file_by_id.side_effect = Exception("download failed")
        mock_deps["control_sp"].upload_file = MagicMock()

        # Should not raise — exceptions are logged and skipped
        await pipeline._upload_performance_log()
        mock_deps["control_sp"].upload_file.assert_not_called()

    async def test_no_folders_does_nothing(
        self, pipeline: FraudValidationPipeline, mock_deps: dict
    ) -> None:
        mock_deps["control_sp"].list_folder_names.return_value = []
        mock_deps["control_sp"].upload_file = MagicMock()

        await pipeline._upload_performance_log()
        mock_deps["control_sp"].upload_file.assert_not_called()


# ---------------------------------------------------------------------------
# _process_shops() — warning log branch (status not in success/fail)
# ---------------------------------------------------------------------------

class TestProcessShopsWarningBranch:
    async def test_logs_warning_for_retry_status(
        self, pipeline: FraudValidationPipeline, mock_deps: dict
    ) -> None:
        """When result.status == 'retry', the warning branch (line 289) is taken."""
        output_headers = [
            "Run_Date", "folder_name", "rtr_code", "rtr_name", "number_of_images",
            "photo_name_1", "photo_name_2", "photo_name_3",
            "photo1_lat", "photo1_long", "rtr1_lat", "rtr1_long", "Photo1_Flag300",
            "same_photo", "from_other_device", "closed_business", "un_relate",
            "un_relate_human", "un_relate_animal", "un_relate_location",
            "un_relate_object", "Complaint_Status",
        ]
        df_data: dict = {
            "original_row_id": [0],
            "status": [""],
            "xd_rtr_code": ["XD001"],
            "xt_rtr_code": ["XT001"],
            "xt_ga": [1],
            "photo_1_path": ["s3://b/photo1.jpg"],
            "photo_2_path": ["s3://b/photo2.jpg"],
            "photo_3_path": ["s3://b/photo3.jpg"],
            "shop_path": ["path"],
            "rtr_lat": ["13.0"],
            "rtr_lon": ["100.0"],
            "xd_rtr_name": ["Shop A"],
            "xt_rtr_name": ["Shop B"],
        }
        for col in output_headers:
            df_data[col] = [""]
        df = pl.DataFrame(df_data)

        mock_result = MagicMock(spec=ShopResult)
        mock_result.original_row_id = 0
        mock_result.rtr_code = "XD001"
        mock_result.status = "retry"  # triggers warning branch
        mock_result.to_output_list.return_value = [""] * 22
        mock_result.to_log_dict.return_value = {"status": "retry", "rtr_code": "XD001"}

        mock_deps["shop_processor"].process = AsyncMock(return_value=mock_result)
        pipeline._gcs.invalidate_cache = MagicMock()
        pipeline._gcs.write_text = MagicMock()

        result = await pipeline._process_shops(df, output_headers, "prompt")
        assert result is not None


# ---------------------------------------------------------------------------
# _upload_transaction_log() — exception swallow branch (lines 584-585)
# ---------------------------------------------------------------------------

class TestUploadTransactionLogExceptionBranch:
    async def test_continues_when_download_raises(
        self, pipeline: FraudValidationPipeline, mock_deps: dict
    ) -> None:
        """When requests.get raises, the exception is swallowed and upload proceeds."""
        pipeline._logs = []
        mock_deps["control_sp"].get_download_url.return_value = "http://some-url"
        mock_deps["control_sp"].upload_file = MagicMock()

        with patch("app.pipeline.fraud_pipeline.requests.get", side_effect=Exception("network")):
            with patch("app.pipeline.fraud_pipeline.get_value_by_path", return_value=[]):
                with patch("app.pipeline.fraud_pipeline.ReportBuilder") as mock_rb_cls:
                    mock_rb_inst = MagicMock()
                    mock_rb_inst.build_transaction_row.return_value = {}
                    mock_rb_cls.return_value = mock_rb_inst
                    await pipeline._upload_transaction_log({})

        mock_deps["control_sp"].upload_file.assert_called_once()


# ---------------------------------------------------------------------------
# _build_join_df() — xlsx lookup branch (lines 312-320)
# ---------------------------------------------------------------------------

class TestBuildJoinDfXlsx:
    def test_xlsx_lookup_branch(self, pipeline: FraudValidationPipeline) -> None:
        """When lookup file is .xlsx, pandas reads it instead of polars CSV."""
        pipeline._cfg = _make_config(today=datetime(2024, 3, 1))
        pipeline._lookup_gcs_path = "input/lookup.xlsx"
        pipeline._input_gcs_path = "input/data.csv"

        # Build a minimal xlsx bytes using pandas
        import io as _io
        import pandas as _pd
        xlsx_buf = _io.BytesIO()
        _pd.DataFrame({"partner_code": ["XD001"], "partner_status": ["Active"], "Detail_sheet": ["x"]}).to_excel(
            xlsx_buf, sheet_name="Detail", index=False
        )
        xlsx_buf.seek(0)
        xlsx_bytes = xlsx_buf.read()

        # Build minimal input CSV
        cols = [
            "Run_Date", "xd_rtr_code", "xd_rtr_name", "xt_rtr_code", "xt_rtr_name",
            "Complaint_Status", "xd_zone_name", "xt_zone_name",
            "xd_cluster_name", "xt_cluster_name",
            "xd_pbh_sale_name", "xd_rsr_sale_name", "xt_pbh_sale_name", "xt_rsr_sale_name",
            "xd_ga", "xt_ga", "last_photo_update", "no_visit_last_month",
            "no_visit", "last_visit_date",
            "xd_appointment_date", "xt_appointment_date",
            "Photo1_Flag300", "xd_last_verified_by", "xt_last_verified_by",
            "xd_last_verified_date", "xt_last_verified_date",
        ]
        vals = ["2024-03-01", "XD001", "ShopA", "XT001", "ShopB", "inComplaint",
                "North", "North", "C1", "C1",
                "pbh", "rsr", "pbh", "rsr",
                "1", "1", "2024-03-01", "0",
                "0", "2024-03-01",
                "", "", "Y",
                "", "", "", ""]
        input_csv = "\n".join([",".join(cols), ",".join(vals)])

        pipeline._gcs.read_bytes.return_value = xlsx_bytes
        pipeline._gcs.read_text.return_value = input_csv
        pipeline._gcs.invalidate_cache = MagicMock()

        result = pipeline._build_join_df(initial_headers=[])
        assert isinstance(result, pl.DataFrame)


# ---------------------------------------------------------------------------
# _join_with_month_start_file() — S3 success + None df_01 branches
# ---------------------------------------------------------------------------

class TestJoinWithMonthStartFileS3Success:
    def _make_original_df(self) -> pl.DataFrame:
        return pl.DataFrame(
            {
                "Run_Date": ["2024-03-15"],
                "xd_rtr_code": ["XD001"],
                "xd_rtr_name": ["ShopA"],
                "xt_rtr_code": ["XT001"],
                "xt_rtr_name": ["ShopB"],
                "Complaint_Status": ["inComplaint"],
                "xd_zone_name": ["North"],
                "xt_zone_name": ["North"],
                "xd_cluster_name": ["C1"],
                "xt_cluster_name": ["C1"],
                "xd_pbh_sale_name": ["pbh"],
                "xd_rsr_sale_name": ["rsr"],
                "xt_pbh_sale_name": ["pbh"],
                "xt_rsr_sale_name": ["rsr"],
                "xd_ga": ["1"],
                "xt_ga": [1],
                "last_photo_update": ["2024-03-01"],
                "no_visit_last_month": [0],
                "no_visit": [0],
                "last_visit_date": ["2024-03-01"],
                "xd_appointment_date": [""],
                "xt_appointment_date": [""],
                "Photo1_Flag300": ["Y"],
                "xd_last_verified_by": [""],
                "xt_last_verified_by": [""],
                "xd_last_verified_date": [""],
                "xt_last_verified_date": [""],
            }
        )

    def test_s3_success_path(self, pipeline: FraudValidationPipeline) -> None:
        """When S3 read succeeds, df_01 is loaded and joined (lines 423-425, 453, 457)."""
        original_df = self._make_original_df()
        suspicious_expr = pl.lit("").alias("Suspicious")
        either_invalid = pl.lit(False)

        # CSV without xd_rtr_code/xt_rtr_code → forces None df_01_xd/df_01_xt branches
        s3_csv = "other_col\nval\n"
        mock_s3_bytes = s3_csv.encode("utf-8")

        pipeline._s3.normalise_key.return_value = "path/to/file.csv"
        pipeline._s3.read_bytes_encrypt.return_value = mock_s3_bytes

        with patch("app.pipeline.fraud_pipeline.load_yaml_string"):
            with patch("app.pipeline.fraud_pipeline.resolve_env"):
                with patch("app.pipeline.fraud_pipeline.read_file"):
                    with patch(
                        "app.pipeline.fraud_pipeline.get_value_by_path",
                        return_value="path/yyyymmdd/file.csv",
                    ):
                        result = pipeline._join_with_month_start_file(
                            original_df, suspicious_expr, either_invalid, []
                        )
        assert isinstance(result, pl.DataFrame)

# ---------------------------------------------------------------------------
# _run_pipeline() — main orchestration method (lines 93-115)
# ---------------------------------------------------------------------------

class TestRunPipeline:
    async def test_calls_all_stages_in_sequence(
        self, pipeline: FraudValidationPipeline
    ) -> None:
        """_run_pipeline wires all stages; mock each stage independently."""
        appended_schema = [
            "Run_Date", "folder_name", "rtr_code", "rtr_name",
            "number_of_images", "photo_name_1", "photo_name_2", "photo_name_3",
            "photo1_lat", "photo1_long", "rtr1_lat", "rtr1_long",
            "Photo1_Flag300", "same_photo", "from_other_device",
            "closed_business", "un_relate", "un_relate_human",
            "un_relate_animal", "un_relate_location", "un_relate_object",
            "Complaint_Status",
        ]  # exactly 22 elements

        mock_df = pl.DataFrame({"col": ["val"]})
        mock_join_df = pl.DataFrame({"col2": ["v"]})
        mock_excel = b"excel"

        with patch("app.pipeline.fraud_pipeline.load_yaml_string", return_value={}):
            with patch("app.pipeline.fraud_pipeline.resolve_env", return_value=""):
                with patch("app.pipeline.fraud_pipeline.read_file", return_value=""):
                    with patch(
                        "app.pipeline.fraud_pipeline.get_value_by_path",
                        side_effect=[appended_schema, []],
                    ):
                        with patch.object(
                            pipeline, "_ingest", new_callable=AsyncMock,
                            return_value=(mock_df, "prompt text"),
                        ):
                            with patch.object(
                                pipeline, "_process_shops", new_callable=AsyncMock,
                                return_value=mock_df,
                            ):
                                with patch.object(
                                    pipeline, "_build_join_df", return_value=mock_join_df
                                ):
                                    with patch.object(
                                        pipeline, "_build_user_excel", return_value=mock_excel
                                    ):
                                        with patch.object(
                                            pipeline, "_publish", new_callable=AsyncMock
                                        ) as mock_publish:
                                            result = await pipeline._run_pipeline()

        assert result == (mock_df, mock_join_df, mock_excel)
        mock_publish.assert_awaited_once()


# ---------------------------------------------------------------------------
# _ingest() — full happy path (lines 123-213)
# ---------------------------------------------------------------------------

class TestIngest:
    async def test_happy_path(
        self, pipeline: FraudValidationPipeline, mock_deps: dict
    ) -> None:
        """Cover the full _ingest happy path with all I/O mocked."""
        # Config values returned in order of get_value_by_path calls
        gvp_values = [
            "yyyymm/yyyymmdd/input.csv",      # input_file_path
            "yyyymm/yyyymmdd/input.enc",       # input_enc_file_path
            "s3/yyyymmdd/input.enc",           # s3 input_file_path
            ["col1", "col2"],                  # initial_schema
            "yyyymm/yyyymmdd/lookup.csv",      # lookup_file_path
        ]
        gvp_iter = iter(gvp_values)

        # Minimal CSV content for the GCS read
        csv_content = "col1,col2,xd_rtr_code,xt_rtr_code\nval1,val2,XD001,XT001\n"
        csv_bytes = csv_content.encode("utf-8")

        mock_deps["s3"].read_bytes.return_value = b"encrypted_bytes"
        mock_deps["fraud_sp"].upload_file = MagicMock()
        mock_deps["fraud_sp"].decrypt = MagicMock(return_value=io.BytesIO(csv_bytes))
        mock_deps["fraud_sp"].download_with_backup = MagicMock(return_value=b"file_bytes")
        mock_deps["gcs"].copy_from_sharepoint_if_missing = MagicMock()
        mock_deps["gcs"].read_bytes.return_value = csv_bytes
        mock_deps["control_sp"].get_download_url.return_value = "http://prompt-url"

        mock_prompt_resp = MagicMock()
        mock_prompt_resp.text = "detect fraud"
        mock_prompt_resp.raise_for_status = MagicMock()

        resolve_config: dict = {}
        output_append_headers = ["col1", "col2"]

        with patch(
            "app.pipeline.fraud_pipeline.get_value_by_path",
            side_effect=lambda cfg, path: next(gvp_iter),
        ):
            with patch(
                "app.pipeline.fraud_pipeline.requests.get",
                return_value=mock_prompt_resp,
            ):
                df, prompt = await pipeline._ingest(resolve_config, output_append_headers)

        assert isinstance(df, pl.DataFrame)
        assert prompt == "detect fraud"
        assert "original_row_id" in df.columns

    async def test_raises_when_prompt_url_missing(
        self, pipeline: FraudValidationPipeline, mock_deps: dict
    ) -> None:
        """When get_download_url returns None, RuntimeError is raised."""
        gvp_values = [
            "yyyymm/yyyymmdd/input.csv",
            "yyyymm/yyyymmdd/input.enc",
            "s3/yyyymmdd/input.enc",
            ["col1"],
            "yyyymm/yyyymmdd/lookup.csv",
        ]
        gvp_iter = iter(gvp_values)

        csv_bytes = b"col1\nval1\n"
        mock_deps["s3"].read_bytes.return_value = b"enc"
        mock_deps["fraud_sp"].upload_file = MagicMock()
        mock_deps["fraud_sp"].decrypt = MagicMock(return_value=io.BytesIO(csv_bytes))
        mock_deps["fraud_sp"].download_with_backup = MagicMock(return_value=b"bytes")
        mock_deps["gcs"].copy_from_sharepoint_if_missing = MagicMock()
        mock_deps["gcs"].read_bytes.return_value = csv_bytes
        mock_deps["control_sp"].get_download_url.return_value = None  # missing prompt

        with patch(
            "app.pipeline.fraud_pipeline.get_value_by_path",
            side_effect=lambda cfg, path: next(gvp_iter),
        ):
            with pytest.raises(RuntimeError, match="Prompt file not found"):
                await pipeline._ingest({}, [])
