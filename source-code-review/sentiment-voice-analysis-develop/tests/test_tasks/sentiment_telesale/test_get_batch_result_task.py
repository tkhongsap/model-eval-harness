from datetime import datetime
from unittest.mock import Mock, patch

import pytest

from tasks.sentiment_telesale.get_batch_result_task import GetBatchResultTask


class TestGetBatchResultTask:
    @pytest.fixture
    def mock_deps(self):
        with (
            patch("tasks.sentiment_telesale.get_batch_result_task.SharePointModule") as sp,
            patch("tasks.sentiment_telesale.get_batch_result_task.GCSModule") as gcs,
            patch("tasks.sentiment_telesale.get_batch_result_task.GeminiBatchModule") as gbm,
            patch("tasks.sentiment_telesale.get_batch_result_task.load_yaml") as ly,
            patch("tasks.sentiment_telesale.get_batch_result_task.resolve_env") as re,
            patch("tasks.sentiment_telesale.get_batch_result_task.resolve_date") as rd,
        ):
            re.side_effect = lambda x: x
            rd.side_effect = lambda text, replace_date: text
            ly.return_value = {"verint": {"site_domain": "v.com"}, "control": {"site_domain": "c.com"}}
            yield sp, gcs, gbm, ly, re, rd

    @pytest.fixture
    def task(self, mock_deps):
        t = GetBatchResultTask()
        t.gcs = {
            "project_id": "p",
            "bucket_name": "b",
            "output_folder": "out",
        }
        t.framework = {"lookback_days": "7"}
        t.packages = {"execution_dt": datetime(2025, 1, 1)}
        t.get_package = lambda k, d=None: t.packages.get(k, d)

        # Mock initialized modules
        t.sharepoint_verint = Mock()
        t.sharepoint_control = Mock()
        t.gcs_module = Mock()

        # Mock validation mapping (campaign -> (ValidationClass, build_fn) tuple)
        _validation_model = Mock()
        _validation_model.model_validate_json.return_value.model_dump.return_value = {"parsed": "json"}
        t.validate_mapping = {"13_True_Promo_End": (_validation_model, Mock())}

        return t

    def test_pre_execute(self, task, mock_deps):
        task.sharepoint_verint = None
        task.pre_execute()
        assert task.sharepoint_verint is not None
        assert task.sharepoint_control is not None
        assert task.gcs_module is not None

    def test_execute_task_no_batches(self, task):
        task.gcs_module.list_files.return_value = []

        result = task.execute_task()

        assert result["list_batchs"] == []
        assert result["batch_results"] == []

    def test_execute_task_success(self, task, mock_deps):
        _, _, gbm, _, _, _ = mock_deps
        # Set lookback to 0 to process only the execution date (1 file)
        task.framework["lookback_days"] = 0
        task.gcs_module.list_files.return_value = ["batch1_predictions.jsonl"]

        # Mock batch retrieval
        gbm.retrieve_batch_results.return_value = [
            {
                "lookback_days": 1,
                "batch_prefix": "prefix",
                "file_pattern": "pattern",
                "response": {
                    "modelVersion": "gemini-1.5",
                    "createTime": "2025-01-01T10:00:00Z",
                    "usageMetadata": {},
                    "candidates": [
                        {
                            "content": {
                                "parts": [{"text": '{"campaign_name": "13_True_Promo_End", "13_True_Promo_End": true}'}]
                            }
                        }
                    ],
                },
                "fileUri": "gs://b/20250101/call_id_phone_time_agent_first_last_prov_20250101_dur.wav",
                "processed_time": "2025-01-01T10:05:00Z",
            }
        ]

        result = task.execute_task()

        assert len(result["list_batchs"]) == 1
        assert len(result["batch_results"]) == 1
        assert result["failed_batches"] == []

        payload = result["batch_results"][0]
        assert payload["file_metadata"]["file_name"] == "call_id_phone_time_agent_first_last_prov_20250101_dur"
        assert payload["prediction"]["status"] == "SUCCESS"

    def test_proc_raw_prediction_error_status(self, task):
        batch = "b1"
        datetime(2025, 1, 1)
        raw_jsonl = [{"fileUri": "gs://b/f1.wav", "status": "PERMISSION_DENIED", "response": {}}]

        task.batch_results = []
        task._proc_raw_prediction(batch, raw_jsonl)

        assert len(task.batch_results) == 1
        assert task.batch_results[0]["prediction"]["status"] == "FAILED"
        assert task.batch_results[0]["prediction"]["message"] == "PERMISSION_DENIED"

    def test_proc_raw_prediction_invalid_filename(self, task):
        batch = "b1"
        datetime(2025, 1, 1)
        # Filename without expected underscores
        raw_jsonl = [
            {"fileUri": "gs://b/invalid.wav", "response": {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]}}
        ]

        task.batch_results = []
        task._proc_raw_prediction(batch, raw_jsonl)

        # Should still process but with None values
        assert len(task.batch_results) == 1
        meta = task.batch_results[0]["file_metadata"]
        assert meta["file_name"] == "invalid"
        assert meta["agent_id"] is None

    def test_proc_raw_prediction_parsing_error(self, task):
        batch = "b1"
        datetime(2025, 1, 1)
        # Prediction text that isn't valid JSON for the model
        raw_jsonl = [
            {
                "fileUri": "gs://b/f1_20250101.wav",
                "response": {"candidates": [{"content": {"parts": [{"text": '{"unknown_key": true}'}]}}]},
            }
        ]

        task.batch_results = []
        task._proc_raw_prediction(batch, raw_jsonl)

        assert len(task.batch_results) == 1
        assert task.batch_results[0]["prediction"]["status"] == "FAILED"
        assert "No suitable validation model" in task.batch_results[0]["prediction"]["message"]


"""
Phase 3 coverage enhancement tests for GetBatchResultTask.
Target: 72% → 80% coverage

Focus areas:
- pre_execute error handling (lines 72-74, 87-89, 100-102, 108-110)
- Date calculation error handling (lines 117, 126-128, 139-141)
- File listing error handling (lines 163-166)
- Batch retrieval error handling (lines 192-195, 198-201)
- FileUri missing handling (lines 251-253)
- Record date extraction error (lines 274-276)
- Filename parsing error (lines 292-295)
- Usage metadata error handling (lines 328-329, 381-384, 393-396)
- Error status processing (lines 339-362)
"""
import pytest


@pytest.fixture
def task_instance():
    """Create GetBatchResultTask with mocked init."""
    with patch.object(GetBatchResultTask, "__init__", lambda self, **kw: None):
        task = GetBatchResultTask.__new__(GetBatchResultTask)
        task.config = {}
        task.packages = {"execution_dt": datetime(2025, 1, 1)}
        task.gcs = {
            "project_id": "test-project",
            "bucket_name": "test-bucket",
            "output_folder": "output/%{DATA_DATE_YYYYMMDD}",
        }
        task.sharepoint = {}
        task.framework = {"lookback_days": "7"}
        task.verint_access = {"site_domain": "verint.com"}
        task.control_access = {"site_domain": "control.com"}
        task.DEFAULT_MODEL_VERSION = "gemini-2.5-flash"
        task.DEFAULT_RECORD_DATE = "99991231"
        task.DEFAULT_JSONL_PREDICTION_FILE = "predictions.jsonl"
        task.DEFAULT_SHEET_NAME = "SentimentTelesale"
        task.DEFAULT_SCHEMA_FROM_FW = ["agent_id", "phone_number", "record_date"]

        def mock_get_package(key, default=None):
            return task.packages.get(key, default)

        task.get_package = mock_get_package

        return task


class TestPreExecuteErrorHandling:
    """Test pre_execute error handling - Lines 72-74, 87-89, 100-102, 108-110."""

    @patch("tasks.sentiment_telesale.get_batch_result_task.SharePointModule", side_effect=Exception("Verint SP Error"))
    @patch("tasks.sentiment_telesale.get_batch_result_task.resolve_env", side_effect=lambda x: x)
    def test_pre_execute_verint_sharepoint_error(self, mock_resolve, mock_sp, task_instance):
        """Test SharePoint Verint initialization error - Lines 72-74."""
        with pytest.raises(Exception, match="Verint SP Error"):
            task_instance.pre_execute()

    @patch("tasks.sentiment_telesale.get_batch_result_task.SharePointModule")
    @patch("tasks.sentiment_telesale.get_batch_result_task.resolve_env", side_effect=lambda x: x)
    def test_pre_execute_control_sharepoint_error(self, mock_resolve, mock_sp, task_instance):
        """Test SharePoint Control initialization error - Lines 87-89."""
        # First call (Verint) succeeds, second call (Control) fails
        mock_sp.side_effect = [Mock(), Exception("Control SP Error")]

        with pytest.raises(Exception, match="Control SP Error"):
            task_instance.pre_execute()

    @patch("tasks.sentiment_telesale.get_batch_result_task.GCSModule", side_effect=Exception("GCS Error"))
    @patch("tasks.sentiment_telesale.get_batch_result_task.SharePointModule")
    @patch("tasks.sentiment_telesale.get_batch_result_task.resolve_env", side_effect=lambda x: x)
    def test_pre_execute_gcs_error(self, mock_resolve, mock_sp, mock_gcs, task_instance):
        """Test GCS initialization error - Lines 100-102."""
        with pytest.raises(Exception, match="GCS Error"):
            task_instance.pre_execute()

    # Note: Validation mapping initialization (lines 108-110) doesn't have explicit error handling
    # The validate_mapping is just a dict assignment, not a function that can fail


class TestExecuteTaskDateHandling:
    """Test execute_task date calculation errors - Lines 117, 126-128, 139-141."""

    @patch("tasks.sentiment_telesale.get_batch_result_task.add_date", side_effect=Exception("Date calculation error"))
    @patch("tasks.sentiment_telesale.get_batch_result_task.resolve_env", side_effect=lambda x: x)
    def test_execute_date_range_calculation_error(self, mock_resolve, mock_add_date, task_instance):
        """Test date range calculation error - Line 117."""
        task_instance.packages = {"execution_dt": datetime(2025, 1, 1)}

        with pytest.raises(Exception, match="Cannot determine processing date range"):
            task_instance.execute_task()

    @patch("tasks.sentiment_telesale.get_batch_result_task.list_date", side_effect=Exception("List date error"))
    @patch("tasks.sentiment_telesale.get_batch_result_task.add_date", return_value=datetime(2024, 12, 25))
    @patch("tasks.sentiment_telesale.get_batch_result_task.resolve_env", side_effect=lambda x: x)
    def test_execute_date_list_generation_error(self, mock_resolve, mock_add_date, mock_list_date, task_instance):
        """Test date list generation error - Lines 126-128."""
        task_instance.packages = {"execution_dt": datetime(2025, 1, 1)}
        task_instance.gcs_module = Mock()

        with pytest.raises(Exception, match="Cannot generate date list"):
            task_instance.execute_task()


class TestExecuteTaskFileListing:
    """Test execute_task file listing error - Lines 163-166."""

    @patch("tasks.sentiment_telesale.get_batch_result_task.list_date", return_value=["20250101", "20250102"])
    @patch("tasks.sentiment_telesale.get_batch_result_task.add_date", return_value=datetime(2024, 12, 25))
    @patch(
        "tasks.sentiment_telesale.get_batch_result_task.resolve_date",
        side_effect=lambda text, replace_date: text.replace("%{DATA_DATE_YYYYMMDD}", replace_date),
    )
    @patch("tasks.sentiment_telesale.get_batch_result_task.resolve_env", side_effect=lambda x: x)
    def test_execute_file_listing_error_continues(
        self, mock_resolve, mock_resolve_date, mock_add_date, mock_list_date, task_instance
    ):
        """Test file listing error for one date continues to next - Lines 163-166."""
        task_instance.gcs_module = Mock()
        # First date fails, second date succeeds
        task_instance.gcs_module.list_files.side_effect = [
            Exception("GCS list error"),
            ["output/20250102/predictions.jsonl"],
        ]

        result = task_instance.execute_task()

        # Should continue despite first date error
        assert len(result["list_batchs"]) == 1
        assert result["list_batchs"][0] == "output/20250102/predictions.jsonl"


class TestExecuteTaskBatchProcessing:
    """Test execute_task batch retrieval error - Lines 192-195, 198-201."""

    @patch("tasks.sentiment_telesale.get_batch_result_task.GeminiBatchModule")
    @patch("tasks.sentiment_telesale.get_batch_result_task.list_date", return_value=["20250101"])
    @patch("tasks.sentiment_telesale.get_batch_result_task.add_date", return_value=datetime(2024, 12, 25))
    @patch("tasks.sentiment_telesale.get_batch_result_task.resolve_date", side_effect=lambda text, replace_date: text)
    @patch("tasks.sentiment_telesale.get_batch_result_task.resolve_env", side_effect=lambda x: x)
    def test_execute_batch_retrieval_error(
        self, mock_resolve, mock_resolve_date, mock_add_date, mock_list_date, mock_gemini, task_instance
    ):
        """Test batch retrieval error handling - Lines 192-195."""
        task_instance.gcs_module = Mock()
        task_instance.gcs_module.list_files.return_value = ["batch1/predictions.jsonl"]

        # Mock retrieve_batch_results to raise error
        mock_gemini.retrieve_batch_results.side_effect = Exception("Batch retrieval failed")

        result = task_instance.execute_task()

        # Should add to failed_batches
        assert len(result["failed_batches"]) == 1
        assert result["failed_batches"][0] == "batch1/predictions.jsonl"
        assert len(result["batch_results"]) == 0

    @patch("tasks.sentiment_telesale.get_batch_result_task.GeminiBatchModule")
    @patch("tasks.sentiment_telesale.get_batch_result_task.list_date", return_value=["20250101"])
    @patch("tasks.sentiment_telesale.get_batch_result_task.add_date", return_value=datetime(2024, 12, 25))
    @patch("tasks.sentiment_telesale.get_batch_result_task.resolve_date", side_effect=lambda text, replace_date: text)
    @patch("tasks.sentiment_telesale.get_batch_result_task.resolve_env", side_effect=lambda x: x)
    def test_execute_batch_processing_error(
        self, mock_resolve, mock_resolve_date, mock_add_date, mock_list_date, mock_gemini, task_instance
    ):
        """Test batch processing error (not retrieval) - Lines 198-201."""
        task_instance.gcs_module = Mock()
        task_instance.gcs_module.list_files.return_value = ["batch1/predictions.jsonl"]

        # Mock retrieve_batch_results succeeds but _proc_raw_prediction fails
        mock_gemini.retrieve_batch_results.return_value = [{"fileUri": "gs://test/file.wav"}]

        with patch.object(task_instance, "_proc_raw_prediction", side_effect=Exception("Processing error")):
            result = task_instance.execute_task()

        # Should add to failed_batches
        assert len(result["failed_batches"]) == 1
        assert result["failed_batches"][0] == "batch1/predictions.jsonl"


class TestProcRawPredictionEdgeCases:
    """Test _proc_raw_prediction edge cases - Lines 251-253, 274-276, 292-295."""

    def test_proc_raw_prediction_no_file_uri(self, task_instance):
        """Test processing record with no fileUri - Lines 251-253."""
        batch = "test_batch"
        raw_jsonl = [
            {"response": {"candidates": []}},  # No fileUri field
        ]

        task_instance.batch_results = []
        task_instance._proc_raw_prediction(batch, raw_jsonl)

        # Should skip record
        assert len(task_instance.batch_results) == 0

    # Note: Lines 274-276 (record date extraction error) and 292-295 (filename parsing error)
    # are difficult to test in isolation as they involve complex mocking of file parsing logic.
    # The existing tests cover the main paths through this code.


class TestProcRawPredictionUsageMetadata:
    """Test usage metadata error handling - Lines 328-329, 381-384, 393-396."""

    @patch("tasks.sentiment_telesale.get_batch_result_task.GeminiBatchModule")
    @patch("tasks.sentiment_telesale.get_batch_result_task.get_current_datetime", return_value=datetime(2025, 1, 1))
    @patch(
        "tasks.sentiment_telesale.get_batch_result_task.recursive_dict_value_by_key",
        return_value=["gs://bucket/20250101/test.wav"],
    )
    def test_proc_raw_prediction_error_status_usage_metadata_error(
        self, mock_recursive, mock_datetime, mock_gemini, task_instance
    ):
        """Test usage metadata extraction error for error status - Lines 328-329."""
        batch = "test_batch"
        raw_jsonl = [
            {
                "fileUri": "gs://bucket/20250101/test.wav",
                "status": "PERMISSION_DENIED",
                "response": {
                    "modelVersion": "gemini-2.5-flash",
                    "createTime": "2025-01-01T10:00:00Z",
                    "usageMetadata": {},
                },
                "processed_time": "2025-01-01T10:05:00Z",
            }
        ]

        # Mock sum_tokens_usage_for_billing to raise error
        mock_gemini.sum_tokens_usage_for_billing.side_effect = Exception("Usage metadata error")

        task_instance.batch_results = []
        task_instance._proc_raw_prediction(batch, raw_jsonl)

        # Should handle error gracefully and still process record
        assert len(task_instance.batch_results) == 1
        assert task_instance.batch_results[0]["prediction"]["status"] == "FAILED"
        assert task_instance.batch_results[0]["prediction"]["message"] == "PERMISSION_DENIED"

    @patch("tasks.sentiment_telesale.get_batch_result_task.GeminiBatchModule")
    @patch("tasks.sentiment_telesale.get_batch_result_task.get_current_datetime", return_value=datetime(2025, 1, 1))
    @patch(
        "tasks.sentiment_telesale.get_batch_result_task.recursive_dict_value_by_key",
        return_value=["gs://bucket/20250101/test.wav"],
    )
    def test_proc_raw_prediction_no_prediction_usage_metadata_error(
        self, mock_recursive, mock_datetime, mock_gemini, task_instance
    ):
        """Test usage metadata extraction error for missing prediction - Lines 381-384."""
        batch = "test_batch"
        raw_jsonl = [
            {
                "fileUri": "gs://bucket/20250101/test.wav",
                "response": {
                    "modelVersion": "gemini-2.5-flash",
                    "createTime": "2025-01-01T10:00:00Z",
                    "candidates": [{"content": {"parts": []}}],  # No text prediction
                    "usageMetadata": {},
                },
                "processed_time": "2025-01-01T10:05:00Z",
            }
        ]

        # Mock sum_tokens_usage_for_billing to raise error
        mock_gemini.sum_tokens_usage_for_billing.side_effect = Exception("Usage metadata error")

        task_instance.batch_results = []
        task_instance._proc_raw_prediction(batch, raw_jsonl)

        # Should handle error gracefully
        assert len(task_instance.batch_results) == 1
        assert task_instance.batch_results[0]["prediction"]["status"] == "FAILED"

    @patch("tasks.sentiment_telesale.get_batch_result_task.GeminiBatchModule")
    @patch("tasks.sentiment_telesale.get_batch_result_task.get_current_datetime", return_value=datetime(2025, 1, 1))
    @patch(
        "tasks.sentiment_telesale.get_batch_result_task.recursive_dict_value_by_key",
        return_value=["gs://bucket/20250101/test.wav"],
    )
    def test_proc_raw_prediction_success_usage_metadata_error(
        self, mock_recursive, mock_datetime, mock_gemini, task_instance
    ):
        """Test usage metadata extraction error for successful prediction - Lines 393-396."""
        batch = "test_batch"
        raw_jsonl = [
            {
                "fileUri": "gs://bucket/20250101/test.wav",
                "response": {
                    "modelVersion": "gemini-2.5-flash",
                    "createTime": "2025-01-01T10:00:00Z",
                    "candidates": [
                        {
                            "content": {
                                "parts": [{"text": '{"campaign_name": "13_True_Promo_End", "13_True_Promo_End": true}'}]
                            }
                        }
                    ],
                    "usageMetadata": {},
                },
                "processed_time": "2025-01-01T10:05:00Z",
            }
        ]

        # Mock sum_tokens_usage_for_billing to raise error
        mock_gemini.sum_tokens_usage_for_billing.side_effect = Exception("Usage metadata error")

        task_instance.batch_results = []
        _validation_model = Mock()
        _validation_model.model_validate_json.return_value.model_dump.return_value = {}
        task_instance.validate_mapping = {"13_True_Promo_End": (_validation_model, Mock())}

        task_instance._proc_raw_prediction(batch, raw_jsonl)

        # Should add default empty usage metadata
        assert len(task_instance.batch_results) == 1
        assert task_instance.batch_results[0]["prediction"]["status"] == "SUCCESS"
        # Check default usage metadata added
        assert "token_input" in task_instance.batch_results[0]["prediction"]
        assert task_instance.batch_results[0]["prediction"]["token_input"] == {"text": 0, "audio": 0}


class TestProcRawPredictionErrorStatusPath:
    """Test error status processing path - Lines 310-362 (line 328 is part of this)."""

    @patch("tasks.sentiment_telesale.get_batch_result_task.GeminiBatchModule")
    @patch("tasks.sentiment_telesale.get_batch_result_task.get_current_datetime", return_value=datetime(2025, 1, 1))
    @patch(
        "tasks.sentiment_telesale.get_batch_result_task.recursive_dict_value_by_key",
        return_value=["gs://bucket/20250101/test.wav"],
    )
    def test_proc_raw_prediction_error_status_full_path(
        self, mock_recursive, mock_datetime, mock_gemini, task_instance
    ):
        """Test full error status processing path - Lines 310-335."""
        batch = "test_batch"
        raw_jsonl = [
            {
                "fileUri": "gs://bucket/20250101/call_phone_time_agent_first_last_prov_20250101_dur.wav",
                "status": "PERMISSION_DENIED",
                "response": {
                    "modelVersion": "gemini-2.5-flash",
                    "createTime": "2025-01-01T10:00:00Z",
                    "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 50, "totalTokenCount": 150},
                },
                "processed_time": "2025-01-01T10:05:00Z",
            }
        ]

        # Mock sum_tokens_usage_for_billing to return usage summary
        mock_gemini.sum_tokens_usage_for_billing.return_value = {
            "token_input": {"text": 100},
            "token_output": {"text": 50},
            "token_cached": 0,
        }

        task_instance.batch_results = []
        task_instance._proc_raw_prediction(batch, raw_jsonl)

        # Should process error status with full metadata
        assert len(task_instance.batch_results) == 1
        payload = task_instance.batch_results[0]
        assert payload["prediction"]["status"] == "FAILED"
        assert payload["prediction"]["message"] == "PERMISSION_DENIED"
        assert payload["prediction"]["model_version"] == "gemini-2.5-flash"
        assert payload["prediction"]["create_time"] == "2025-01-01T10:00:00Z"
        assert payload["prediction"]["processed_time"] == "2025-01-01T10:05:00Z"
        assert "token_input" in payload["prediction"]
        assert payload["load_dt"] is not None


class TestGetBatchResultTaskAdditional:
    @patch("tasks.sentiment_telesale.get_batch_result_task.TaskInterface.__init__", return_value=None)
    @patch.object(GetBatchResultTask, "get_config", side_effect=[{}, {}, {}])
    @patch("tasks.sentiment_telesale.get_batch_result_task.load_yaml", return_value={"verint": {}, "control": {}})
    def test_init_validation_mapping_assignment_failure(self, mock_load_yaml, mock_get_config, mock_task_init):
        # validate_mapping is assigned in __init__, so the failure surfaces at construction.
        def guarded_setattr(self, name, value):
            if name == "validate_mapping":
                raise RuntimeError("mapping init failed")
            object.__setattr__(self, name, value)

        with patch.object(GetBatchResultTask, "__setattr__", guarded_setattr):
            with pytest.raises(RuntimeError, match="mapping init failed"):
                GetBatchResultTask()

    @patch("tasks.sentiment_telesale.get_batch_result_task.list_date", return_value=["20250103"])
    @patch("tasks.sentiment_telesale.get_batch_result_task.add_date", return_value=datetime(2024, 12, 27))
    @patch("tasks.sentiment_telesale.get_batch_result_task.resolve_env", side_effect=lambda x: x)
    def test_execute_task_prefers_rerun_date(self, mock_resolve, mock_add_date, mock_list_date, task_instance):
        task_instance.packages = {
            "execution_dt": datetime(2025, 1, 1),
            "rerun_data_dt": "2025-01-03",
        }
        task_instance.gcs_module = Mock()
        task_instance.gcs_module.list_files.return_value = []

        task_instance.execute_task()

        assert mock_add_date.call_args[0][0] == "2025-01-03"

    def test_proc_raw_prediction_record_date_error_falls_back_to_default(self, task_instance):
        model = Mock()
        model.model_validate_json.return_value.model_dump.return_value = {"campaign_name": "13_True_Promo_End"}
        task_instance.validate_mapping = {"13_True_Promo_End": model}
        task_instance.batch_results = []
        raw_jsonl = [
            {
                "fileUri": "gs://bucket/20250101/call_phone_time_agent_first_last_prov_20250101_dur_OUT.wav",
                "response": {
                    "candidates": [{"content": {"parts": [{"text": '{"campaign_name": "13_True_Promo_End"}'}]}}],
                    "usageMetadata": {},
                },
            }
        ]

        with patch("tasks.sentiment_telesale.get_batch_result_task.re.match", side_effect=RuntimeError("bad regex")):
            task_instance._proc_raw_prediction("batch", raw_jsonl)

        assert task_instance.batch_results[0]["file_metadata"]["record_date"] == task_instance.DEFAULT_RECORD_DATE

    def test_proc_raw_prediction_filename_parse_error_uses_default_voice_info(self, task_instance):
        model = Mock()
        model.model_validate_json.return_value.model_dump.return_value = {"campaign_name": "13_True_Promo_End"}
        task_instance.validate_mapping = {"13_True_Promo_End": model}
        task_instance.batch_results = []
        raw_jsonl = [
            {
                "fileUri": "gs://bucket/20250101/call_phone_time_agent_first_last_prov_20250101_dur_OUT.wav",
                "response": {
                    "candidates": [{"content": {"parts": [{"text": '{"campaign_name": "13_True_Promo_End"}'}]}}],
                    "usageMetadata": {},
                },
            }
        ]

        with patch(
            "tasks.sentiment_telesale.get_batch_result_task.safe_list_get_slicing",
            side_effect=RuntimeError("parse failed"),
        ):
            task_instance._proc_raw_prediction("batch", raw_jsonl)

        meta = task_instance.batch_results[0]["file_metadata"]
        assert meta["file_name"] == "call_phone_time_agent_first_last_prov_20250101_dur_OUT"
        assert meta["file_ext"] is None
        assert meta["agent_id"] is None
        assert meta["record_date"] == "20250101"

    @patch("tasks.sentiment_telesale.get_batch_result_task.GeminiBatchModule")
    def test_proc_raw_prediction_missing_prediction_adds_usage_metadata(self, mock_gemini, task_instance):
        task_instance.batch_results = []
        raw_jsonl = [
            {
                "fileUri": "gs://bucket/20250101/call_phone_time_agent_first_last_prov_20250101_dur_OUT.wav",
                "response": {
                    "candidates": [{"content": {"parts": []}}],
                    "usageMetadata": {"promptTokenCount": 10},
                },
            }
        ]
        mock_gemini.sum_tokens_usage_for_billing.return_value = {
            "token_input": {"text": 10, "audio": 0},
            "token_output": {"text": 0},
            "token_cached": 0,
        }

        task_instance._proc_raw_prediction("batch", raw_jsonl)

        assert task_instance.batch_results[0]["prediction"]["token_input"] == {"text": 10, "audio": 0}

    def test_proc_raw_prediction_line_error_is_skipped(self, task_instance):
        task_instance.batch_results = []
        raw_jsonl = [
            {
                "fileUri": "gs://bucket/20250101/call_phone_time_agent_first_last_prov_20250101_dur_OUT.wav",
                "response": {
                    "candidates": [{"content": {"parts": [{"text": '{"campaign_name": "13_True_Promo_End"}'}]}}],
                    "usageMetadata": {},
                },
            }
        ]

        with patch.object(task_instance, "_prepare_prediction_payload", side_effect=RuntimeError("boom")):
            task_instance._proc_raw_prediction("batch", raw_jsonl)

        assert task_instance.batch_results == []
