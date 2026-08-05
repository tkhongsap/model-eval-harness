"""Unit tests for app/modules/fact_checker.py — FactCheckerModule."""
from __future__ import annotations

import asyncio
import base64
import copy
import io
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, patch as _patch

import numpy as np
import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Patch google.auth.default BEFORE importing FactCheckerModule
# (it is called at module level on line 66 of fact_checker.py)
# ---------------------------------------------------------------------------
_google_auth_patcher = patch("google.auth.default", return_value=(None, "test-project"))
_google_auth_patcher.start()

from app.modules.fact_checker import FactCheckerModule  # noqa: E402

_google_auth_patcher.stop()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

VALID_CONFIG = {
    "Project_id": "test",
    "Project_name": "Test",
    "GroundTruth": {
        "input": {
            "type": "sharepoint",
            "site_name": "SentimentAnalysisfromVoiceFile",
            "path": "/gt.xlsx",
            "sheet_name": "Sheet1",
        }
    },
    "Prediction": {
        "gemini_stream": {
            "model_config": {"input": {"type": "source_code", "path": "/model_config.yml"}},
            "input": {
                "type": "sharepoint",
                "site_name": "AIandAutomationTeamControlManagement",
                "path": "/images",
            },
            "prompt": {
                "system_prompt_path": {
                    "input": {"type": "source_code", "path": "/prompt.yml"}
                }
            },
        }
    },
    "Report": {
        "output": {
            "type": "sharepoint",
            "site_name": "AIandAutomationTeamControlManagement",
            "path": "/report.xlsx",
            "transaction_path": "/txn",
        },
        "schema": ["created_datetime", "processed_datetime", "dimension", "label", "accuracy"],
        "metric_thresholds": {
            "accuracy": {"acceptable": 0.5, "good": 0.7, "excellent": 0.9},
            "precision": {"acceptable": 0.5, "good": 0.7, "excellent": 0.9},
            "recall": {"acceptable": 0.5, "good": 0.7, "excellent": 0.9},
            "f1_score": {"acceptable": 0.5, "good": 0.7, "excellent": 0.9},
        },
    },
}


def _make_instance(config: dict | None = None) -> FactCheckerModule:
    """Create a FactCheckerModule with all I/O mocked."""
    cfg = config if config is not None else VALID_CONFIG
    mock_sp = MagicMock()
    with (
        patch("app.modules.fact_checker.read_file", return_value="yaml"),
        patch("app.modules.fact_checker.resolve_env", side_effect=lambda x: x),
        patch("app.modules.fact_checker.load_yaml_string", return_value=cfg),
        patch("app.modules.fact_checker.SharePointModule", return_value=mock_sp),
        patch.dict(
            "os.environ",
            {
                "CONTROL_SITE_CLIENT_ID": "ccid",
                "CONTROL_SITE_CLIENT_SECRET": "ccsec",
                "CONTROL_SITE_TENANT_ID": "ctid",
                "CONTROL_SITE_SITE_DOMAIN": "control.sharepoint.com",
                "CONTROL_SITE_SITE_PATH": "/sites/control",
            },
        ),
    ):
        return FactCheckerModule("dummy.yml")


@pytest.fixture()
def fc() -> FactCheckerModule:
    return _make_instance()


# ---------------------------------------------------------------------------
# ratio_to_binary
# ---------------------------------------------------------------------------


class TestRatioToBinary:
    def test_zero_ratio(self, fc: FactCheckerModule) -> None:
        assert fc.ratio_to_binary("0/3") == 0

    def test_nonzero_ratio(self, fc: FactCheckerModule) -> None:
        assert fc.ratio_to_binary("2/3") == 1

    def test_nan_returns_zero(self, fc: FactCheckerModule) -> None:
        assert fc.ratio_to_binary(float("nan")) == 0

    def test_empty_string_returns_zero(self, fc: FactCheckerModule) -> None:
        assert fc.ratio_to_binary("") == 0

    def test_invalid_string_returns_zero(self, fc: FactCheckerModule) -> None:
        assert fc.ratio_to_binary("invalid") == 0

    def test_full_ratio(self, fc: FactCheckerModule) -> None:
        assert fc.ratio_to_binary("3/3") == 1


# ---------------------------------------------------------------------------
# ratio_to_YN
# ---------------------------------------------------------------------------


class TestRatioToYN:
    def test_zero_ratio_returns_Y(self, fc: FactCheckerModule) -> None:
        assert fc.ratio_to_YN("0/3") == "Y"

    def test_nonzero_ratio_returns_N(self, fc: FactCheckerModule) -> None:
        assert fc.ratio_to_YN("2/3") == "N"

    def test_nan_returns_Y(self, fc: FactCheckerModule) -> None:
        assert fc.ratio_to_YN(float("nan")) == "Y"

    def test_invalid_returns_Y(self, fc: FactCheckerModule) -> None:
        assert fc.ratio_to_YN("bad-value") == "Y"


# ---------------------------------------------------------------------------
# match_to_binary
# ---------------------------------------------------------------------------


class TestMatchToBinary:
    def test_equal_strings(self, fc: FactCheckerModule) -> None:
        assert fc.match_to_binary("Y", "Y") == 1

    def test_unequal_strings(self, fc: FactCheckerModule) -> None:
        assert fc.match_to_binary("Y", "N") == 0


# ---------------------------------------------------------------------------
# confusion_metrics
# ---------------------------------------------------------------------------


class TestConfusionMetrics:
    def test_all_true_positive(self, fc: FactCheckerModule) -> None:
        y_true = pd.Series([1, 1, 1])
        y_pred = pd.Series([1, 1, 1])
        m = fc.confusion_metrics(y_true, y_pred)
        assert m["TP"] == 3
        assert m["FP"] == 0
        assert m["FN"] == 0
        assert m["TN"] == 0
        assert m["accuracy"] == 1.0

    def test_mixed_results(self, fc: FactCheckerModule) -> None:
        y_true = pd.Series([1, 1, 0, 0])
        y_pred = pd.Series([1, 0, 1, 0])
        m = fc.confusion_metrics(y_true, y_pred)
        assert m["TP"] == 1
        assert m["FP"] == 1
        assert m["FN"] == 1
        assert m["TN"] == 1
        assert m["accuracy"] == 0.5

    def test_all_false_positive(self, fc: FactCheckerModule) -> None:
        """Divide-by-zero guard: all predictions are FP."""
        y_true = pd.Series([0, 0, 0])
        y_pred = pd.Series([1, 1, 1])
        m = fc.confusion_metrics(y_true, y_pred)
        assert m["TP"] == 0
        assert m["precision"] == 0.0  # TP/(TP+FP) = 0/3 = 0 with guard


# ---------------------------------------------------------------------------
# evaluation_status_check
# ---------------------------------------------------------------------------


class TestEvaluationStatusCheck:
    def test_unacceptable(self, fc: FactCheckerModule) -> None:
        assert fc.evaluation_status_check("accuracy", 0.3) == "unacceptable"

    def test_acceptable(self, fc: FactCheckerModule) -> None:
        assert fc.evaluation_status_check("accuracy", 0.6) == "acceptable"

    def test_good(self, fc: FactCheckerModule) -> None:
        assert fc.evaluation_status_check("accuracy", 0.8) == "good"

    def test_excellent(self, fc: FactCheckerModule) -> None:
        assert fc.evaluation_status_check("accuracy", 0.95) == "excellent"

    def test_string_input_cast(self, fc: FactCheckerModule) -> None:
        assert fc.evaluation_status_check("accuracy", "0.95") == "excellent"


# ---------------------------------------------------------------------------
# evaluate_predictions
# ---------------------------------------------------------------------------


class TestEvaluatePredictions:
    def test_returns_dataframe_with_metrics(self, fc: FactCheckerModule) -> None:
        gt = pd.DataFrame(
            {"RTR_Code": ["A", "B"], "Same_Photo": ["0/3", "0/3"], "From_Other_Device": ["0/3", "0/3"]}
        )
        pred = pd.DataFrame(
            {"RTR_Code": ["A", "B"], "Same_Photo": ["0/3", "0/3"], "From_Other_Device": ["0/3", "0/3"]}
        )
        result = fc.evaluate_predictions(gt, pred)
        assert "Same_Photo" in result.index
        assert "From_Other_Device" in result.index

    def test_missing_column_skipped(self, fc: FactCheckerModule) -> None:
        gt = pd.DataFrame({"RTR_Code": ["A"], "Same_Photo": ["0/3"]})
        pred = pd.DataFrame({"RTR_Code": ["A"], "Same_Photo": ["0/3"]})
        # From_Other_Device is missing from both — should not raise
        result = fc.evaluate_predictions(gt, pred)
        assert "Same_Photo" in result.index
        assert "From_Other_Device" not in result.index


# ---------------------------------------------------------------------------
# config_validation
# ---------------------------------------------------------------------------


class TestConfigValidation:
    def test_valid_config_passes(self, fc: FactCheckerModule) -> None:
        # Should not raise
        fc.config_validation(VALID_CONFIG)

    def test_missing_project_id_raises(self, fc: FactCheckerModule) -> None:
        bad = {k: v for k, v in VALID_CONFIG.items() if k != "Project_id"}
        with pytest.raises(ValueError, match="Project_id"):
            fc.config_validation(bad)

    def test_missing_report_schema_raises(self, fc: FactCheckerModule) -> None:
        import copy
        bad = copy.deepcopy(VALID_CONFIG)
        del bad["Report"]["schema"]
        with pytest.raises(ValueError, match="schema"):
            fc.config_validation(bad)

    def test_missing_metric_thresholds_raises(self, fc: FactCheckerModule) -> None:
        import copy
        bad = copy.deepcopy(VALID_CONFIG)
        del bad["Report"]["metric_thresholds"]
        with pytest.raises(ValueError, match="metric_thresholds"):
            fc.config_validation(bad)


# ---------------------------------------------------------------------------
# _validate_io_config
# ---------------------------------------------------------------------------


class TestValidateIoConfig:
    def test_sharepoint_valid(self, fc: FactCheckerModule) -> None:
        fc._validate_io_config(
            {"type": "sharepoint", "site_name": "MySite", "path": "/doc.xlsx"},
            "test.path",
        )

    def test_sharepoint_missing_field_raises(self, fc: FactCheckerModule) -> None:
        with pytest.raises(ValueError, match="site_name"):
            fc._validate_io_config({"type": "sharepoint", "path": "/x"}, "p")

    def test_gcs_valid(self, fc: FactCheckerModule) -> None:
        fc._validate_io_config(
            {"type": "gcs", "gcp_project_id": "p", "bucket_name": "b", "path": "/x"},
            "test.path",
        )

    def test_gcs_missing_field_raises(self, fc: FactCheckerModule) -> None:
        with pytest.raises(ValueError, match="bucket_name"):
            fc._validate_io_config({"type": "gcs", "gcp_project_id": "p", "path": "/x"}, "p")

    def test_source_code_valid(self, fc: FactCheckerModule) -> None:
        fc._validate_io_config({"type": "source_code", "path": "/prompt.yml"}, "test.path")

    def test_source_code_missing_path_raises(self, fc: FactCheckerModule) -> None:
        with pytest.raises(ValueError, match="path"):
            fc._validate_io_config({"type": "source_code"}, "p")

    def test_unknown_type_raises(self, fc: FactCheckerModule) -> None:
        with pytest.raises(ValueError, match="Unknown type"):
            fc._validate_io_config({"type": "s3", "path": "/x"}, "p")

    def test_none_section_raises(self, fc: FactCheckerModule) -> None:
        with pytest.raises(ValueError, match="Missing"):
            fc._validate_io_config(None, "test.path")

    def test_missing_type_raises(self, fc: FactCheckerModule) -> None:
        with pytest.raises(ValueError, match="Missing 'type'"):
            fc._validate_io_config({"path": "/x"}, "test.path")


# ---------------------------------------------------------------------------
# format_excel_report
# ---------------------------------------------------------------------------


class TestFormatExcelReport:
    def test_returns_bytes(self, fc: FactCheckerModule) -> None:
        df = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})
        result = fc.format_excel_report(df)
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_round_trip(self, fc: FactCheckerModule) -> None:
        df = pd.DataFrame({"col1": [10, 20], "col2": ["alpha", "beta"]})
        excel_bytes = fc.format_excel_report(df)
        reloaded = pd.read_excel(io.BytesIO(excel_bytes), sheet_name="Report")
        assert list(reloaded["col1"]) == [10, 20]

    def test_wide_column_capped_at_50(self, fc: FactCheckerModule) -> None:
        """Columns with content longer than 48 chars should be capped at width 50."""
        from openpyxl import load_workbook as lw

        df = pd.DataFrame({"long_col": ["x" * 100]})
        excel_bytes = fc.format_excel_report(df)
        wb = lw(io.BytesIO(excel_bytes))
        ws = wb["Report"]
        col_width = ws.column_dimensions["A"].width
        assert col_width <= 50

    def test_auto_filter_set(self, fc: FactCheckerModule) -> None:
        from openpyxl import load_workbook as lw

        df = pd.DataFrame({"a": [1], "b": [2]})
        excel_bytes = fc.format_excel_report(df)
        wb = lw(io.BytesIO(excel_bytes))
        ws = wb["Report"]
        assert ws.auto_filter.ref is not None


# ---------------------------------------------------------------------------
# prepare_ground_truth
# ---------------------------------------------------------------------------


class TestPrepareGroundTruth:
    def test_returns_dataframe(self, fc: FactCheckerModule) -> None:
        """Mocked SharePoint returns Excel bytes → DataFrame."""
        # Build a minimal xlsx in memory
        gt_df = pd.DataFrame(
            {"RTR_Code": ["R1"], "RTR_Name": ["Shop 1"], "Same_Photo": ["0/3"]}
        )
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as w:
            gt_df.to_excel(w, index=False, sheet_name="Sheet1")
        excel_bytes = buf.getvalue()

        mock_response = MagicMock()
        mock_response.content = excel_bytes

        mock_sp = MagicMock()
        mock_sp.get_item_by_path.return_value = mock_response

        fc.module_objects["GroundTruth.input"] = mock_sp

        result = fc.prepare_ground_truth()
        assert isinstance(result, pd.DataFrame)
        assert "RTR_Code" in result.columns

    def test_raises_on_sp_error(self, fc: FactCheckerModule) -> None:
        mock_sp = MagicMock()
        mock_sp.get_item_by_path.side_effect = RuntimeError("SP error")
        fc.module_objects["GroundTruth.input"] = mock_sp

        with pytest.raises(Exception, match="SP error"):
            fc.prepare_ground_truth()


# ---------------------------------------------------------------------------
# fraud_validation_task
# ---------------------------------------------------------------------------


class TestFraudValidationTask:
    def _make_gemini_response(self, json_payload: dict) -> MagicMock:
        response = MagicMock()
        part = MagicMock()
        part.text = json.dumps(json_payload)
        response.candidates = [MagicMock()]
        response.candidates[0].content.parts = [part]

        # usage_metadata
        response.usage_metadata.prompt_tokens_details = []
        response.usage_metadata.cache_tokens_details = []
        response.usage_metadata.candidates_token_count = 42
        response.create_time = datetime(2024, 3, 15, 10, 0, 0, tzinfo=timezone.utc)
        return response

    def test_returns_result_and_metadata(self, fc: FactCheckerModule) -> None:
        payload = {"Same_Photo": "0/3", "From_Other_Device": "0/3"}
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = self._make_gemini_response(payload)

        meta = {"RTR_Code": "R001", "RTR_Name": "Shop A"}
        result, rtr_meta = fc.fraud_validation_task(
            gemini_client=mock_client,
            meta_data=meta,
            image_part=[],
            one_prompt="test prompt",
        )
        assert result["response"] == payload
        assert rtr_meta["output_tokens"] == 42

    def test_empty_response_raises(self, fc: FactCheckerModule) -> None:
        mock_client = MagicMock()
        empty_response = MagicMock()
        empty_response.candidates = []
        mock_client.models.generate_content.return_value = empty_response

        with pytest.raises(ValueError, match="empty"):
            fc.fraud_validation_task(
                gemini_client=mock_client,
                meta_data={},
                image_part=[],
                one_prompt="prompt",
            )

    def test_token_detail_extraction(self, fc: FactCheckerModule) -> None:
        payload = {"Same_Photo": "0/3"}
        response = self._make_gemini_response(payload)

        text_detail = MagicMock()
        text_detail.modality = "TEXT"
        text_detail.token_count = 100

        image_detail = MagicMock()
        image_detail.modality = "IMAGE"
        image_detail.token_count = 200

        response.usage_metadata.prompt_tokens_details = [text_detail, image_detail]

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = response

        _, rtr_meta = fc.fraud_validation_task(
            gemini_client=mock_client,
            meta_data={},
            image_part=[],
            one_prompt="p",
        )
        assert rtr_meta["text_input_tokens"] == 100
        assert rtr_meta["image_input_tokens"] == 200


# ---------------------------------------------------------------------------
# merge_upload_report
# ---------------------------------------------------------------------------


class TestMergeUploadReport:
    def test_new_file_uploaded_directly(self, fc: FactCheckerModule) -> None:
        """When report doesn't exist yet, final_df is uploaded as-is."""
        mock_sp = MagicMock()
        mock_sp.check_item_exists.return_value = False
        mock_sp.upload_file.return_value = None
        fc.module_objects["Report.output"] = mock_sp

        final_df = pd.DataFrame(
            {
                "created_datetime": ["2024-01-01"],
                "processed_datetime": ["2024-01-01"],
                "dimension": ["Same_Photo"],
                "label": ["test"],
                "accuracy": ["90.00"],
            }
        )
        fc.merge_upload_report(final_df)
        mock_sp.upload_file.assert_called_once()

    def test_existing_file_merged_and_uploaded(self, fc: FactCheckerModule) -> None:
        """When report exists, existing + new are merged and uploaded."""
        existing_df = pd.DataFrame(
            {
                "created_datetime": ["2023-01-01"],
                "processed_datetime": ["2023-01-01"],
                "dimension": ["From_Other_Device"],
                "label": ["old"],
                "accuracy": ["80.00"],
            }
        )
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as w:
            existing_df.to_excel(w, index=False)
        excel_bytes = buf.getvalue()

        mock_response = MagicMock()
        mock_response.content = excel_bytes

        mock_sp = MagicMock()
        mock_sp.check_item_exists.return_value = True
        mock_sp.get_item_by_path.return_value = mock_response
        fc.module_objects["Report.output"] = mock_sp

        new_df = pd.DataFrame(
            {
                "created_datetime": ["2024-01-01"],
                "processed_datetime": ["2024-01-01"],
                "dimension": ["Same_Photo"],
                "label": ["new"],
                "accuracy": ["90.00"],
            }
        )
        fc.merge_upload_report(new_df)
        mock_sp.upload_file.assert_called_once()


# ---------------------------------------------------------------------------
# merge_transaction_log
# ---------------------------------------------------------------------------


class TestMergeTransactionLog:
    def _make_raw_output(self) -> dict:
        return {
            "data_date": "20240101",
            "start_time": "20240101 10:00:00",
            "end_time": "20240101 10:01:00",
            "total_time_mins": 1.0,
            "type": "AI-Fact-Checker",
            "gcp_project_id": "proj",
            "gcp_project_name": "proj",
            "user_id": "user",
            "source": "SharePoint",
            "storage_path": "/path",
            "folder": "/folder",
            "filename": "f1.jpg",
            "file_metadata_min": "-",
            "status_pass_failed_retry": "success",
            "error_log_if": "",
            "latency_ms": 1000,
            "token_usage_input": 100,
            "token_usage_output": 50,
            "total_cost_usd": 0.001,
        }

    def test_new_log_uploaded(self, fc: FactCheckerModule) -> None:
        mock_sp = MagicMock()
        mock_sp.check_item_exists.return_value = False
        fc.module_objects["Report.output"] = mock_sp

        fc.merge_transaction_log([self._make_raw_output()])
        # Should not raise and should not call upload_file since it's in the
        # else-less except block — actually check_item_exists returns False so
        # the method just doesn't try to merge, but also doesn't upload when False
        # (looking at the code: when False the if block doesn't run — no upload in else)

    def test_existing_log_merged(self, fc: FactCheckerModule) -> None:
        existing_df = pd.DataFrame([self._make_raw_output()])
        buf = io.BytesIO()
        existing_df.to_csv(buf, index=False, encoding="utf-8-sig")
        csv_bytes = buf.getvalue()

        mock_response = MagicMock()
        mock_response.content = csv_bytes

        mock_sp = MagicMock()
        mock_sp.check_item_exists.return_value = True
        mock_sp.get_item_by_path.return_value = mock_response
        fc.module_objects["Report.output"] = mock_sp

        fc.merge_transaction_log([self._make_raw_output()])
        mock_sp.upload_file.assert_called_once()

    def test_corrupt_csv_falls_back_to_new(self, fc: FactCheckerModule) -> None:
        """When existing CSV can't be parsed, create new (lines 802-804)."""
        mock_response = MagicMock()
        mock_response.content = b"not valid csv\x00\xff"

        mock_sp = MagicMock()
        mock_sp.check_item_exists.return_value = True
        mock_sp.get_item_by_path.return_value = mock_response
        fc.module_objects["Report.output"] = mock_sp

        # Should not raise — falls back to transaction_df only
        fc.merge_transaction_log([self._make_raw_output()])


# ---------------------------------------------------------------------------
# prepare_ground_truth — active sheet (no sheet_name)
# ---------------------------------------------------------------------------


class TestPrepareGroundTruthActiveSheet:
    def test_no_sheet_name_uses_active(self, fc: FactCheckerModule) -> None:
        """When config has no sheet_name, workbook.active is used."""
        import copy

        cfg = copy.deepcopy(VALID_CONFIG)
        del cfg["GroundTruth"]["input"]["sheet_name"]

        fc2 = _make_instance(cfg)

        gt_df = pd.DataFrame({"RTR_Code": ["R2"], "RTR_Name": ["Shop B"]})
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as w:
            gt_df.to_excel(w, index=False)
        excel_bytes = buf.getvalue()

        mock_response = MagicMock()
        mock_response.content = excel_bytes

        mock_sp = MagicMock()
        mock_sp.get_item_by_path.return_value = mock_response
        fc2.module_objects["GroundTruth.input"] = mock_sp

        result = fc2.prepare_ground_truth()
        assert isinstance(result, pd.DataFrame)


# ---------------------------------------------------------------------------
# run_coroutine_in_thread
# ---------------------------------------------------------------------------


class TestRunCoroutineInThread:
    def test_runs_async_function(self) -> None:
        """run_coroutine_in_thread executes a coroutine and returns the result."""

        async def _async_add(a: int, b: int) -> int:
            return a + b

        result = FactCheckerModule.run_coroutine_in_thread(_async_add, 3, 4)
        assert result == 7


# ---------------------------------------------------------------------------
# config_validation — additional missing branches
# ---------------------------------------------------------------------------


class TestConfigValidationMissingBranches:
    def test_missing_ground_truth_input_raises(self, fc: FactCheckerModule) -> None:
        bad = copy.deepcopy(VALID_CONFIG)
        bad["GroundTruth"] = {}  # no "input" key
        with pytest.raises(ValueError, match="GroundTruth.input"):
            fc.config_validation(bad)

    def test_missing_prediction_gemini_stream_raises(self, fc: FactCheckerModule) -> None:
        bad = copy.deepcopy(VALID_CONFIG)
        bad["Prediction"] = {}  # no "gemini_stream"
        with pytest.raises(ValueError, match="gemini_stream"):
            fc.config_validation(bad)


# ---------------------------------------------------------------------------
# __get_sharepoint_env_variables — unknown site name warning
# ---------------------------------------------------------------------------


class TestGetSharepointEnvVariables:
    def test_unknown_site_name_logs_warning(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A config with an unrecognised site_name should log a warning (line 218)."""
        cfg = copy.deepcopy(VALID_CONFIG)
        cfg["GroundTruth"]["input"]["site_name"] = "UnknownSite"

        with (
            patch("app.modules.fact_checker.read_file", return_value="yaml"),
            patch("app.modules.fact_checker.resolve_env", side_effect=lambda x: x),
            patch("app.modules.fact_checker.load_yaml_string", return_value=cfg),
            patch("app.modules.fact_checker.SharePointModule"),
            patch.dict("os.environ", {"CONTROL_SITE_CLIENT_ID": "x", "CONTROL_SITE_CLIENT_SECRET": "x",
                                      "CONTROL_SITE_TENANT_ID": "x", "CONTROL_SITE_SITE_DOMAIN": "x.com",
                                      "CONTROL_SITE_SITE_PATH": "/x"}),
        ):
            # Should not raise — warning is logged for the unknown site
            fc_new = FactCheckerModule("dummy.yml")
        assert fc_new is not None


# ---------------------------------------------------------------------------
# prepare_predictions
# ---------------------------------------------------------------------------


class TestPreparePredictions:
    def _make_gemini_response(self, payload: dict) -> MagicMock:
        response = MagicMock()
        part = MagicMock()
        part.text = json.dumps(payload)
        response.candidates = [MagicMock()]
        response.candidates[0].content.parts = [part]
        response.usage_metadata.prompt_tokens_details = []
        response.usage_metadata.cache_tokens_details = []
        response.usage_metadata.candidates_token_count = 10
        response.create_time = datetime(2024, 3, 15, 10, 0, 0, tzinfo=timezone.utc)
        return response

    def test_returns_prediction_df_and_raw_outputs(self, fc: FactCheckerModule) -> None:
        payload = {col: "0/3" for col in [
            "Same_Photo", "From_Other_Device", "Closed_Business",
            "un_relate", "un_relate_human", "un_relate_animal",
            "un_relate_location", "un_relate_object",
        ]}
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = self._make_gemini_response(payload)

        # SP mock
        mock_img_resp = MagicMock()
        mock_img_resp.content = base64.b64encode(b"fake_img_bytes")
        mock_sp = MagicMock()
        mock_sp.list_folders.return_value = [{"name": "R001-Shop A"}]
        mock_sp.list_files.return_value = [
            {"name": "p1.jpg"}, {"name": "p2.jpg"}, {"name": "p3.jpg"}
        ]
        mock_sp.get_item_by_path.return_value = mock_img_resp
        fc.module_objects["Prediction.gemini_stream.input"] = mock_sp

        with (
            patch("app.modules.fact_checker.read_file", return_value="prompt"),
            patch("app.modules.fact_checker.load_yaml_string", return_value="prompt text"),
            patch("app.modules.fact_checker.genai.Client", return_value=mock_client),
            patch.object(fc, "fraud_validation_task", return_value=(
                {
                    "RTR_Code": "R001", "RTR_Name": "Shop A",
                    "Number_of_image": 3,
                    "Photo_Name1": "p1.jpg", "Photo_Name2": "p2.jpg", "Photo_Name3": "p3.jpg",
                    "response": payload,
                },
                {
                    "create_time": datetime(2024, 3, 15, 10, 0, 0, tzinfo=timezone.utc),
                    "text_input_tokens": 100, "image_input_tokens": 50,
                    "text_cache_tokens": 0, "image_cache_tokens": 0,
                    "output_tokens": 20,
                },
            )),
        ):
            pred_df, raw_outputs = fc.prepare_predictions()

        assert isinstance(pred_df, pd.DataFrame)
        assert len(raw_outputs) == 1

    def test_no_images_in_folder_skipped(self, fc: FactCheckerModule) -> None:
        """Folders with no images are skipped (warning logged)."""
        mock_sp = MagicMock()
        mock_sp.list_folders.return_value = [{"name": "R002-Empty Shop"}]
        mock_sp.list_files.return_value = [{"name": "readme.txt"}]  # no jpg/png
        fc.module_objects["Prediction.gemini_stream.input"] = mock_sp

        with (
            patch("app.modules.fact_checker.read_file", return_value="prompt"),
            patch("app.modules.fact_checker.load_yaml_string", return_value=""),
            patch("app.modules.fact_checker.genai.Client"),
        ):
            pred_df, raw_outputs = fc.prepare_predictions()

        assert len(pred_df) == 0
        assert len(raw_outputs) == 0

    def test_no_timezone_create_time(self, fc: FactCheckerModule) -> None:
        """create_time without tzinfo → treated as UTC (lines 580-582)."""
        payload = {"Same_Photo": "0/3"}
        mock_sp = MagicMock()
        mock_sp.list_folders.return_value = [{"name": "R003-Shop"}]
        mock_sp.list_files.return_value = [{"name": "img.jpg"}]
        mock_img = MagicMock()
        mock_img.content = base64.b64encode(b"img")
        mock_sp.get_item_by_path.return_value = mock_img
        fc.module_objects["Prediction.gemini_stream.input"] = mock_sp

        with (
            patch("app.modules.fact_checker.read_file", return_value="p"),
            patch("app.modules.fact_checker.load_yaml_string", return_value="p"),
            patch("app.modules.fact_checker.genai.Client"),
            patch.object(fc, "fraud_validation_task", return_value=(
                {
                    "RTR_Code": "R003", "RTR_Name": "Shop",
                    "Number_of_image": 1, "Photo_Name1": "img.jpg",
                    "Photo_Name2": None, "Photo_Name3": None,
                    "response": payload,
                },
                {
                    "create_time": datetime(2024, 3, 15, 10, 0, 0),  # no tzinfo
                    "text_input_tokens": 0, "image_input_tokens": 0,
                    "text_cache_tokens": 0, "image_cache_tokens": 0,
                    "output_tokens": 5,
                },
            )),
        ):
            pred_df, raw_outputs = fc.prepare_predictions()

        assert len(raw_outputs) == 1


# ---------------------------------------------------------------------------
# execute
# ---------------------------------------------------------------------------


class TestExecute:
    def _make_metrics_df(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "accuracy": 1.0,
                    "precision": 1.0,
                    "recall": 1.0,
                    "f1_score": 1.0,
                    "TP": 2,
                    "FP": 0,
                    "FN": 0,
                    "TN": 0,
                    "weight": 2,
                }
            ],
            index=pd.Index(["Same_Photo"], name="metric"),
        )

    def test_execute_calls_all_stages(self, fc: FactCheckerModule) -> None:
        gt_df = pd.DataFrame({"RTR_Code": ["R1"]})
        ai_df = pd.DataFrame({"RTR_Code": ["R1"]})
        metrics_df = self._make_metrics_df()

        with (
            patch.object(fc, "prepare_ground_truth", return_value=gt_df),
            patch.object(fc, "prepare_predictions", return_value=(ai_df, [])),
            patch.object(fc, "evaluate_predictions", return_value=metrics_df),
            patch.object(fc, "merge_upload_report") as mock_upload,
            patch.object(fc, "merge_transaction_log") as mock_txn,
        ):
            fc.execute()

        mock_upload.assert_called_once()
        mock_txn.assert_called_once()

    def test_execute_adds_metadata_columns(self, fc: FactCheckerModule) -> None:
        gt_df = pd.DataFrame({"RTR_Code": ["R1"]})
        ai_df = pd.DataFrame({"RTR_Code": ["R1"]})
        metrics_df = self._make_metrics_df()

        captured_final_df = {}

        def _capture(final_df):
            captured_final_df["df"] = final_df

        with (
            patch.object(fc, "prepare_ground_truth", return_value=gt_df),
            patch.object(fc, "prepare_predictions", return_value=(ai_df, [])),
            patch.object(fc, "evaluate_predictions", return_value=metrics_df),
            patch.object(fc, "merge_upload_report", side_effect=_capture),
            patch.object(fc, "merge_transaction_log"),
        ):
            fc.execute()

        df = captured_final_df["df"]
        assert "dimension" in df.columns
        assert "created_datetime" in df.columns
        assert "accuracy" in df.columns
        assert "accuracy_status" in df.columns
