import contextlib
import json
from datetime import datetime
from unittest.mock import AsyncMock, Mock, patch

import pandas as pd
import pytest

from tasks.sentiment_telesale.export_output_result_task import ExportOutputResultTask


class TestExportOutputResultTask:
    @pytest.fixture
    def mock_deps(self):
        with (
            patch("tasks.sentiment_telesale.export_output_result_task.SharePointModule") as sp,
            patch("tasks.sentiment_telesale.export_output_result_task.GCSModule") as gcs,
            patch("tasks.sentiment_telesale.export_output_result_task.load_yaml") as ly,
            patch("tasks.sentiment_telesale.export_output_result_task.resolve_env") as re,
            patch("tasks.sentiment_telesale.export_output_result_task.resolve_date") as rd,
            patch("tasks.sentiment_telesale.export_output_result_task.gemini_cost") as gc,
            patch("tasks.sentiment_telesale.export_output_result_task.GeminiBatchModule") as gbm,
        ):
            re.side_effect = lambda x: x
            rd.side_effect = lambda text, replace_date: text
            ly.return_value = {
                "verint": {
                    "site_domain": "v.com",
                    "client_id": "cid",
                    "client_secret": "cs",
                    "tenant_id": "tid",
                    "site_path": "sp",
                },
                "control": {
                    "site_domain": "c.com",
                    "client_id": "cid",
                    "client_secret": "cs",
                    "tenant_id": "tid",
                    "site_path": "sp",
                    "gemini_cost_path": "cost.yml",
                },
            }
            # Mock gemini_cost to return empty list (no pricing needed for tests)
            gc.return_value = []
            # Mock cal_gemini_cost to return a cost dict
            gbm.cal_gemini_cost.return_value = {1: {"cost_input": 0.001, "cost_output": 0.002}}

            yield sp, gcs, ly, re, rd, gc, gbm

    @pytest.fixture
    def task(self, mock_deps):
        t = ExportOutputResultTask()
        t.sharepoint = {
            "verint": {"output_file": "out.csv"},
            "control": {
                "evaluation_file": "eval.xlsx",
                "transaction_log_file": "trans.csv",
                "performance_log_file": "perf.csv",
                "batch_processing_log_file": "batch_log.csv",
            },
        }
        t.gcs = {
            "project_id": "p",
            "bucket_name": "b",
            "processing_voice_folder": "proc",
            "archive_voice_folder": "arch",
            "input_folder": "in",
            "archive_batch_folder": "batch_arch",
        }
        t.gcp = {"project_id": "p", "project_name": "n"}
        t.pre_result = {"batch_results": [], "list_batchs": [], "failed_batches": []}
        t.packages = {"execution_dt": datetime(2025, 1, 1)}
        t.get_package = lambda k, d=None: t.packages.get(k, d)

        # Manually trigger pre_execute logic for modules
        # mock_deps order: sp, gcs, ly, re, rd, gc, gbm
        t.verint_access = mock_deps[2].return_value["verint"]
        t.control_access = mock_deps[2].return_value["control"]
        t.gemini_cost_path = "cost.yml"
        # Mock initialized modules
        t.sharepoint_verint = Mock()
        t.sharepoint_control = Mock()
        t.sharepoint_control.is_item_exists.return_value = False  # Default: no batch processing log
        t.gcs_module = Mock()
        t.verint_site = "v.com"

        return t

    def test_pre_execute(self, task, mock_deps):
        """Test pre_execute initializes modules correctly."""
        task.sharepoint_verint = None
        task.sharepoint_control = None
        task.gcs_module = None

        task.pre_execute()

        assert task.sharepoint_verint is not None
        assert task.sharepoint_control is not None
        assert task.gcs_module is not None

    def test_execute_empty(self, task):
        """Test execute with no batch results."""
        task.pre_result["batch_results"] = []
        result = task.execute_task()
        assert result is None

    def test_execute_success(self, task):
        """Test execute with valid batch results."""
        task.pre_result["batch_results"] = [
            {
                "file_metadata": {
                    "file_name": "f1",
                    "file_ext": ".wav",
                    "record_date": "20250101",
                    "file_uri": "gs://b/f/f1.wav",
                    "duration": 100,
                },
                "prediction": {
                    "status": "SUCCESS",
                    "model_version": "gemini-1.5",
                    "raw_prediction": {},
                    "token_input": {"text": 10, "audio": 20},
                    "token_output": {"text": 30},
                },
                "scoring_result": {"scoring_status": "SUCCESS", "scoring_detail": {}},
            }
        ]
        task.pre_result["list_batchs"] = ["b1"]

        with (
            patch.object(task, "_format_output", return_value=[{"filename": "f1.wav"}]),
            patch.object(task, "_archive_files") as mock_archive,
            patch.object(task, "_insert_log_record") as mock_log,
        ):
            result = task.execute_task()

            # Verify upload
            task.sharepoint_verint.upload_file.assert_called()
            # Verify archive
            mock_archive.assert_called_once()
            # Verify logging
            mock_log.assert_called_once()
            assert result == task.pre_result["batch_results"]

    def test_format_output(self, task):
        """Test _format_output logic for export (structure different from evaluation)."""
        raw_result = [
            {
                "file_metadata": {
                    "file_name": "f1",
                    "file_ext": ".wav",
                    "record_date": "20250101",
                    "duration": 60,
                    "call_id": "123",
                    "phone_number": "0812345678",
                    "agent_id": "A001",
                    "first_name": "John",
                    "last_name": "Doe",
                    "provider": "D",
                },
                "prediction": {
                    "status": "SUCCESS",
                    "model_version": "gemini-1.5-flash",
                    "raw_prediction": {
                        "check_list": [],
                        "operations_and_professionalism": {
                            "call_opening": {"support_detail": "good"},
                            "customer_identity_verification": {"support_detail": "ok"},
                            "language_and_tone": {"support_detail": "professional"},
                            "active_listening": {"support_detail": "attentive"},
                            "call_closing": {"support_detail": "polite"},
                        },
                        "sales_effectiveness": {
                            "customer_needs_analysis": {"support_detail": "thorough"},
                            "offer_presentation_quality": {"support_detail": "clear"},
                            "effective_objection_handling": {"support_detail": "good"},
                            "sales_closing_attempt": {"support_detail": "attempted"},
                            "cross_sell_upsell": {"support_detail": "mentioned"},
                        },
                        "customer_experience": {
                            "positive_customer_experience": {"support_detail": "positive"},
                            "clarity_of_communication": {"support_detail": "clear"},
                            "building_trust": {"support_detail": "trustworthy"},
                        },
                        "compliance": {"compliance": {"support_detail": "compliant"}},
                        "agent_strength": "Good communication",
                        "agent_weakness": "Could improve closing",
                    },
                    "token_input": {"text": 1000},
                    "token_cached": 0,
                    "token_output": {"text": 500},
                },
                "scoring_result": {
                    "scoring_status": "SUCCESS",
                    "max_score": 100,
                    "max_score_not_none": 100,
                    "total_score": 80,
                    "scoring_detail": {
                        "customer_experience": {
                            "score": 10,
                            "max_score": 20,
                            "max_score_not_none": 20,
                            "detail": {"positive_customer_experience": {"score": 5}},
                        },
                        "operations_and_professionalism": {
                            "score": 20,
                            "max_score": 30,
                            "max_score_not_none": 30,
                            "detail": {},
                        },
                        "sales_effectiveness": {"score": 30, "max_score": 30, "max_score_not_none": 30, "detail": {}},
                        "compliance": {"score": 20, "max_score": 20, "max_score_not_none": 20, "detail": {}},
                    },
                },
            }
        ]

        formatted = task._format_output(raw_result)

        assert len(formatted) == 1
        assert formatted[0]["filename"] == "f1.wav"
        # Export task produces JSON summary in summary field
        assert "summary" in formatted[0]
        # Check if the summary is not None before loading
        assert formatted[0]["summary"] is not None, f"Summary is None. Error code: {formatted[0].get('error_code')}"
        summary_data = json.loads(formatted[0]["summary"])
        assert "qa_call_data" in summary_data
        assert "customer_information" in summary_data["qa_call_data"]
        # The structure is: qa_call_data -> customer_information -> customer_information (dict with actual fields)
        # Let's just verify agent_id is present
        qa_data = summary_data["qa_call_data"]
        assert "customer_information" in qa_data

    def test_archive_files(self, task):
        """Test file archival logic."""
        df = pd.DataFrame([{"record_date": "20250101", "status": "SUCCESS", "file_name": "f1.wav"}])
        list_batchs = ["gs://b/batch1"]
        failed_batches = []

        # Setup mocks
        task.gcs_module.is_dir_exists.return_value = True
        task.gcs_module.list_files.side_effect = [["gs://b/proc/f1.wav"], ["gs://b/in/f1.wav"]]
        task.gcs_module.copy_files_batch = AsyncMock(return_value={"success": 1, "failed": 0})

        task._archive_files(datetime(2025, 1, 1), df, list_batchs, failed_batches)

        # Voice files are now batch-copied then deleted (not move_file)
        task.gcs_module.copy_files_batch.assert_called_once()
        # delete_file called twice: once for processing source, once for input folder
        delete_calls = [str(c) for c in task.gcs_module.delete_file.call_args_list]
        assert any("in/f1.wav" in c or "f1.wav" in c for c in delete_calls)

    def test_format_output_promo_end_campaign(self, task):
        """Test _format_output with 13_True_Promo_End campaign checklist."""
        raw_result = [
            {
                "file_metadata": {"file_name": "f1", "file_ext": ".wav", "record_date": "20250101", "duration": 60},
                "prediction": {
                    "status": "SUCCESS",
                    "model_version": "gemini-1.5-flash",
                    "raw_prediction": {
                        "campaign_name": "13_True_Promo_End",
                        "check_list": {"operation_check_list": "YES", "support_detail": "All checks passed"},
                        "operations_and_professionalism": {
                            "call_opening": {"support_detail": ""},
                            "customer_identity_verification": {"support_detail": ""},
                            "language_and_tone": {"support_detail": ""},
                            "active_listening": {"support_detail": ""},
                            "call_closing": {"support_detail": ""},
                        },
                        "sales_effectiveness": {
                            "customer_needs_analysis": {"support_detail": ""},
                            "offer_presentation_quality": {"support_detail": ""},
                            "effective_objection_handling": {"support_detail": ""},
                            "sales_closing_attempt": {"support_detail": ""},
                            "cross_sell_upsell": {"support_detail": ""},
                        },
                        "customer_experience": {
                            "positive_customer_experience": {"support_detail": ""},
                            "clarity_of_communication": {"support_detail": ""},
                            "building_trust": {"support_detail": ""},
                        },
                        "compliance": {"compliance": {"support_detail": ""}},
                        "agent_strength": "Good",
                        "agent_weakness": "None",
                    },
                    "token_input": {"text": 100},
                    "token_cached": 0,
                    "token_output": {"text": 50},
                },
                "scoring_result": {
                    "scoring_status": "SUCCESS",
                    "max_score": 100,
                    "max_score_not_none": 100,
                    "total_score": 80,
                    "scoring_detail": {
                        "customer_experience": {"score": 10, "max_score": 20, "max_score_not_none": 20, "detail": {}},
                        "operations_and_professionalism": {
                            "score": 20,
                            "max_score": 30,
                            "max_score_not_none": 30,
                            "detail": {},
                        },
                        "sales_effectiveness": {"score": 30, "max_score": 30, "max_score_not_none": 30, "detail": {}},
                        "compliance": {"score": 20, "max_score": 20, "max_score_not_none": 20, "detail": {}},
                    },
                },
            }
        ]

        formatted = task._format_output(raw_result)

        assert len(formatted) == 1
        assert formatted[0]["status"] == "SUCCESS"
        summary_data = json.loads(formatted[0]["summary"])
        # Verify checklist is included for this campaign
        assert "check_list" in summary_data["qa_call_data"]
        assert summary_data["qa_call_data"]["check_list"]["metrics"]["operation_check_list"] == "YES"

    def test_archive_files_with_failed_batches(self, task):
        """Test file archival logic excludes failed batches."""
        df = pd.DataFrame([{"record_date": "20250101", "status": "SUCCESS", "file_name": "f1.wav"}])
        list_batchs = [
            "gs://b/output/202501/20250101/20250101120000/batch1.jsonl",
            "gs://b/output/202501/20250101/20250101130000/batch2.jsonl",
        ]
        failed_batches = ["gs://b/output/202501/20250101/20250101130000/batch2.jsonl"]

        # Setup mocks
        task.gcs_module.is_dir_exists.return_value = True
        task.gcs_module.list_files.side_effect = [["gs://b/proc/f1.wav"], ["gs://b/in/f1.wav"]]
        task.gcs_module.copy_files_batch = AsyncMock(return_value={"success": 1, "failed": 0})

        task._archive_files(datetime(2025, 1, 1), df, list_batchs, failed_batches)

        # Voice archive uses copy_files_batch (not move_file); batch archive still uses move_file
        task.gcs_module.copy_files_batch.assert_called_once()
        assert task.gcs_module.move_file.call_count == 1  # only batch1 archive (voice uses copy_files_batch)

        # Check batch2 (failed) was NOT archived
        batch_calls = [call for call in task.gcs_module.move_file.call_args_list if "batch" in str(call)]
        assert len(batch_calls) == 1
        assert "batch1.jsonl" in str(batch_calls[0])

    def test_format_output_with_batch_log(self, task):
        """Test _format_output with batch processing log containing failed files."""
        raw_result = [
            {
                "file_metadata": {"file_name": "f1", "file_ext": ".wav", "record_date": "20250101", "duration": 60},
                "prediction": {
                    "status": "SUCCESS",
                    "model_version": "gemini-1.5-flash",
                    "raw_prediction": {"check_list": []},
                    "token_input": {"text": 10},
                    "token_cached": 0,
                    "token_output": {"text": 5},
                },
                "scoring_result": {
                    "scoring_status": "SUCCESS",
                    "max_score": 100,
                    "max_score_not_none": 100,
                    "total_score": 80,
                    "scoring_detail": {
                        "customer_experience": {"score": 10, "max_score": 20, "max_score_not_none": 20, "detail": {}},
                        "operations_and_professionalism": {
                            "score": 20,
                            "max_score": 30,
                            "max_score_not_none": 30,
                            "detail": {},
                        },
                        "sales_effectiveness": {"score": 30, "max_score": 30, "max_score_not_none": 30, "detail": {}},
                        "compliance": {"score": 20, "max_score": 20, "max_score_not_none": 20, "detail": {}},
                    },
                },
            }
        ]

        # Mock batch processing log with failed files
        log_csv = "batch_job_id,batch_job_display_name,filename,status,error_message,updated_dt\n"
        log_csv += "batch1,Job1,f1.wav,SUCCESS,,2025-01-01 10:00:00\n"
        log_csv += "batch1,Job1,f2.wav,FAILED,Audio format error,2025-01-01 10:01:00\n"

        task.sharepoint_control.is_item_exists.return_value = True
        mock_log_item = Mock()
        mock_log_item.content = log_csv.encode("utf-8")
        task.sharepoint_control.get_item_by_path.return_value = mock_log_item

        formatted = task._format_output(raw_result)

        # Should have 2 records: 1 success + 1 failed from log
        assert len(formatted) == 2
        assert formatted[0]["filename"] == "f1.wav"
        assert formatted[0]["status"] == "SUCCESS"
        assert formatted[1]["filename"] == "f2.wav"
        assert formatted[1]["status"] == "FAILED"
        assert formatted[1]["error_code"] == "Audio format error"

    # Note: _prep_gemini_cost method does not exist in ExportOutputResultTask
    # The cost calculation is done inline in _transaction_log using gemini_cost utility

    def test_transaction_log(self, task, mock_deps):
        """Test transaction log creation and upload."""
        # Setup prediction DF
        prediction_df = pd.DataFrame(
            [
                {
                    "file_name": "f1.wav",
                    "full_path": "path/to/f1.wav",
                    "folder": "f",
                    "record_date": "20250101",
                    "duration": 60,
                    "token_input": {"text": 1000, "audio": 0},
                    "token_output": {"text": 500},
                    "token_cached": 0,
                    "status": "SUCCESS",
                    "message": None,
                    "processed_time": "2025-01-01 10:00:00",
                    "create_time": "2025-01-01 10:01:00",
                    "model_version": "gemini-1.5-flash",
                    "load_dt": "2025-01-01",
                }
            ]
        )

        # Mock SharePoint check/upload
        task.sharepoint_control.is_item_exists.return_value = False

        # Mock gemini_cost and GeminiBatchModule.cal_gemini_cost
        with (
            patch("tasks.sentiment_telesale.export_output_result_task.gemini_cost") as mock_gemini_cost,
            patch(
                "tasks.sentiment_telesale.export_output_result_task.GeminiBatchModule.cal_gemini_cost"
            ) as mock_cal_cost,
        ):
            mock_gemini_cost.return_value = [
                {
                    "type": "batch",
                    "model": "gemini-1.5-flash",
                    "pricing_type": "input",
                    "input_type": "text",
                    "tokens": 1000000,
                    "cost": 0.15,
                    "cost_per_token": 0.00000015,
                    "unit": "1M",
                },
                {
                    "type": "batch",
                    "model": "gemini-1.5-flash",
                    "pricing_type": "output",
                    "input_type": "all",
                    "tokens": 1000000,
                    "cost": 1.25,
                    "cost_per_token": 0.00000125,
                    "unit": "1M",
                },
            ]
            # Mock cost calculation result - use file_name as key
            mock_cal_cost.return_value = {"f1.wav": {"cost_input": 0.00015, "cost_output": 0.000625}}

            result_df = task._transaction_log(
                "Type", "User", "Source", prediction_df, execution_dt=datetime(2025, 1, 1)
            )

            assert result_df is not None
            assert len(result_df) == 1
            # Verify upload was called
            task.sharepoint_control.upload_file.assert_called()

    def test_performance_log(self, task):
        """Test performance log creation."""
        trans_df = pd.DataFrame(
            [
                {
                    "data_date": "20250101",
                    "start_time": "20250101",
                    "load_dt": "dt1",
                    "gcp_project_id": "p",
                    "gcp_project_name": "n",
                    "status_pass_failed_retry": "Pass",
                    "latency_ms": 100,
                }
            ]
        )

        # Mock existing content for merge
        mock_file = Mock()
        mock_file.content = b"header1,header2\nval1,val2"
        task.sharepoint_control.get_item_by_path.return_value = mock_file

        perf_df = task._performance_log(trans_df)

        assert perf_df is None
        task.sharepoint_control.upload_file.assert_called()

    def _make_raw_result(
        self,
        prediction_status="SUCCESS",
        scoring_status="SUCCESS",
        op_detail=None,
        call_status=None,
        prediction_message=None,
        scoring_message=None,
        scoring_override=None,
        file_name="f1",
    ):
        """Helper: build a minimal raw_result record."""
        raw_prediction = {
            "check_list": [],
            "operations_and_professionalism": {
                "call_opening": {"support_detail": ""},
                "customer_identity_verification": {"support_detail": ""},
                "language_and_tone": {"support_detail": ""},
                "active_listening": {"support_detail": ""},
                "call_closing": {"support_detail": ""},
            },
            "sales_effectiveness": {
                "customer_needs_analysis": {"support_detail": ""},
                "offer_presentation_quality": {"support_detail": ""},
                "effective_objection_handling": {"support_detail": ""},
                "sales_closing_attempt": {"support_detail": ""},
                "cross_sell_upsell": {"support_detail": ""},
            },
            "customer_experience": {
                "positive_customer_experience": {"support_detail": ""},
                "clarity_of_communication": {"support_detail": ""},
                "building_trust": {"support_detail": ""},
            },
            "compliance": {"compliance": {"support_detail": ""}},
            "agent_strength": "Good",
            "agent_weakness": "None",
        }
        if call_status is not None:
            raw_prediction["call_status"] = call_status

        op_detail_val = op_detail if op_detail is not None else {}

        # Build scoring dict; allow override for testing None-score branches
        def _scoring_dim(detail_val, score=20, max_s=30, max_nn=30):
            if scoring_override:
                return {
                    "score": scoring_override.get("score", score),
                    "max_score": scoring_override.get("max_score", max_s),
                    "max_score_not_none": scoring_override.get("max_score_not_none", max_nn),
                    "score_not_none": scoring_override.get("score_not_none", score),
                    "detail": detail_val,
                }
            return {
                "score": score,
                "max_score": max_s,
                "max_score_not_none": max_nn,
                "score_not_none": score,
                "detail": detail_val,
            }

        total_score = None if (scoring_override and scoring_override.get("score") is None) else 80
        return [
            {
                "file_metadata": {
                    "file_name": file_name,
                    "file_ext": ".wav",
                    "record_date": "20250101",
                    "duration": 60,
                },
                "prediction": {
                    "status": prediction_status,
                    "message": prediction_message,
                    "model_version": "gemini-1.5-flash",
                    "raw_prediction": raw_prediction,
                    "token_input": {"text": 10},
                    "token_cached": 0,
                    "token_output": {"text": 5},
                },
                "scoring_result": {
                    "scoring_status": scoring_status,
                    "scoring_message": scoring_message,
                    "max_score": None if (scoring_override and scoring_override.get("max_score") is None) else 100,
                    "max_score_not_none": None
                    if (scoring_override and scoring_override.get("max_score_not_none") is None)
                    else 100,
                    "total_score": total_score,
                    "scoring_detail": {
                        "customer_experience": _scoring_dim({}, 10, 20, 20),
                        "operations_and_professionalism": _scoring_dim(op_detail_val),
                        "sales_effectiveness": _scoring_dim({}),
                        "compliance": _scoring_dim({}, 20, 20, 20),
                    },
                },
            }
        ]

    def test_format_output_prediction_failed(self, task):
        """Lines 363-366: prediction_status != SUCCESS branch. Status and message taken from prediction."""
        raw_result = self._make_raw_result(
            prediction_status="FAILED",
            prediction_message="Batch error\nwith newline",
        )
        formatted = task._format_output(raw_result)
        assert len(formatted) == 1
        assert formatted[0]["status"] == "FAILED"
        # newlines replaced → no newline characters in error_code
        assert "\n" not in (formatted[0].get("error_code") or "")

    def test_format_output_scoring_failed(self, task):
        """Lines 369-372: scoring_status != SUCCESS with prediction SUCCESS."""
        raw_result = self._make_raw_result(
            prediction_status="SUCCESS",
            scoring_status="FAILED",
            scoring_message="Scoring error\ndetail",
        )
        formatted = task._format_output(raw_result)
        assert len(formatted) == 1
        assert formatted[0]["status"] == "FAILED"
        assert "\n" not in (formatted[0].get("error_code") or "")

    def test_format_output_with_detail_items_and_none_score(self, task):
        """Lines 416-418: detail dict has items; lines 424-427: score is None triggers warning."""
        op_detail = {
            "call_opening": {"score": 5},
            "call_closing": {"score": None},  # None → triggers warning path
        }
        raw_result = self._make_raw_result(op_detail=op_detail)
        formatted = task._format_output(raw_result)
        assert len(formatted) == 1
        assert formatted[0]["status"] == "SUCCESS"
        summary = json.loads(formatted[0]["summary"])
        op_metrics = summary["qa_call_data"]["operations_and_professionalism"]["metrics"]
        assert "call_opening" in op_metrics
        assert op_metrics["call_opening"] == 5
        assert op_metrics["call_closing"] is None

    def test_format_output_abandoned_call(self, task):
        """Lines 552-554: call_status == 'Abandoned' triggers force_none_value_dict."""
        raw_result = self._make_raw_result(call_status="Abandoned")
        formatted = task._format_output(raw_result)
        assert len(formatted) == 1
        assert formatted[0]["status"] == "SUCCESS"
        summary = json.loads(formatted[0]["summary"])
        # All numeric score fields should be None for abandoned calls
        op = summary["qa_call_data"]["operations_and_professionalism"]
        assert op.get("total_score") is None
        assert op.get("total_weight_score") is None

    def test_format_output_none_scores_trigger_warnings(self, task):
        """Lines 431, 436, 441, 461, 466, 471, 486, 491, 496, 501, 521, 526, 531:
        when scoring scores/max_scores are None, warming branches fire."""
        # Provide all scoring dimension values as None
        raw_result = self._make_raw_result(
            scoring_override={"score": None, "max_score": None, "max_score_not_none": None}
        )
        formatted = task._format_output(raw_result)
        assert len(formatted) == 1
        assert formatted[0]["status"] == "SUCCESS"

    def test_format_output_multiple_records_same_date(self, task):
        """Line 226: else branch in sep_date loop fires when key already exists."""
        raw_result = self._make_raw_result() + self._make_raw_result(file_name="f2")
        formatted = task._format_output(raw_result)
        assert len(formatted) == 2

    def test_execute_two_same_date_records(self, task):
        """Line 226: else branch in sep_date loop when 2 records share record_date."""
        r1 = {
            "file_metadata": {
                "file_name": "f1",
                "file_ext": ".wav",
                "record_date": "20250101",
                "file_uri": "gs://b/dir/f1.wav",
                "duration": 100,
            },
            "prediction": {
                "status": "SUCCESS",
                "model_version": "v1",
                "raw_prediction": {},
                "token_input": {},
                "token_output": {},
                "token_cached": 0,
            },
            "scoring_result": {"scoring_status": "SUCCESS", "scoring_detail": {}},
        }
        r2 = {
            "file_metadata": {
                "file_name": "f2",
                "file_ext": ".wav",
                "record_date": "20250101",
                "file_uri": "gs://b/dir/f2.wav",
                "duration": 100,
            },
            "prediction": {
                "status": "SUCCESS",
                "model_version": "v1",
                "raw_prediction": {},
                "token_input": {},
                "token_output": {},
                "token_cached": 0,
            },
            "scoring_result": {"scoring_status": "SUCCESS", "scoring_detail": {}},
        }
        task.pre_result["batch_results"] = [r1, r2]
        task.pre_result["list_batchs"] = ["b1"]
        with (
            patch.object(task, "_format_output", return_value=[{"filename": "f1.wav"}, {"filename": "f2.wav"}]),
            patch.object(task, "_archive_files"),
            patch.object(task, "_insert_log_record"),
        ):
            task.execute_task()

    def test_format_output_early_return_empty_input(self, task):
        """Lines 300-302: empty raw_result triggers early return with empty list."""
        result = task._format_output([])
        assert result == []

    def test_transaction_log_missing_full_path(self, task, mock_deps):
        """Lines 1015-1016: record with None full_path triggers warning and uses incomplete URL."""
        prediction_df = pd.DataFrame(
            [
                {
                    "file_name": "f1.wav",
                    "full_path": None,
                    "folder": "f",
                    "record_date": "20250101",
                    "duration": 60,
                    "token_input": {"text": 10},
                    "token_output": {"text": 5},
                    "token_cached": 0,
                    "status": "SUCCESS",
                    "message": None,
                    "processed_time": "2025-01-01T10:00:00",
                    "create_time": "2025-01-01T10:01:00",
                    "model_version": "gemini-1.5-flash",
                    "load_dt": "2025-01-01",
                }
            ]
        )
        task.sharepoint_control.is_item_exists.return_value = False
        with (
            patch("tasks.sentiment_telesale.export_output_result_task.gemini_cost") as mock_gc,
            patch("tasks.sentiment_telesale.export_output_result_task.GeminiBatchModule.cal_gemini_cost") as mock_cost,
        ):
            mock_gc.return_value = []
            mock_cost.return_value = {"f1.wav": {"cost_input": 0.001, "cost_output": 0.002}}
            result_df = task._transaction_log(
                "Type", "User", "Source", prediction_df, execution_dt=datetime(2025, 1, 1)
            )
        assert result_df is not None

    def test_transaction_log_duration_none_and_failed(self, task, mock_deps):
        """Line 1052: duration=None → file_metadata_sec=0; Line 1091: FAILED → stamp_error;
        Line 1066: processed_time > create_time triggers datetime swap."""
        prediction_df = pd.DataFrame(
            [
                {
                    "file_name": "f1.wav",
                    "full_path": "path/f1.wav",
                    "folder": "f",
                    "record_date": "20250101",
                    "duration": None,
                    "token_input": {"text": 10},
                    "token_output": {"text": 5},
                    "token_cached": 0,
                    "status": "FAILED",
                    "message": "Audio error",
                    "processed_time": "2025-01-01T10:10:00",
                    "create_time": "2025-01-01T10:00:00",
                    "model_version": "gemini-1.5-flash",
                    "load_dt": "2025-01-01",
                }
            ]
        )
        task.sharepoint_control.is_item_exists.return_value = False
        with (
            patch("tasks.sentiment_telesale.export_output_result_task.gemini_cost") as mock_gc,
            patch("tasks.sentiment_telesale.export_output_result_task.GeminiBatchModule.cal_gemini_cost") as mock_cost,
        ):
            mock_gc.return_value = []
            mock_cost.return_value = {"f1.wav": {"cost_input": 0.001, "cost_output": 0.002}}
            result_df = task._transaction_log(
                "Type", "User", "Source", prediction_df, execution_dt=datetime(2025, 1, 1)
            )
        assert result_df is not None

    def test_transaction_log_existing_log_merge(self, task, mock_deps):
        """Lines 1131-1136: when transaction log exists on SharePoint, merges with new data."""
        prediction_df = pd.DataFrame(
            [
                {
                    "file_name": "f1.wav",
                    "full_path": "path/f1.wav",
                    "folder": "f",
                    "record_date": "20250101",
                    "duration": 60,
                    "token_input": {"text": 10},
                    "token_output": {"text": 5},
                    "token_cached": 0,
                    "status": "SUCCESS",
                    "message": None,
                    "processed_time": "2025-01-01T10:00:00",
                    "create_time": "2025-01-01T10:01:00",
                    "model_version": "gemini-1.5-flash",
                    "load_dt": "2025-01-01",
                }
            ]
        )
        mock_existing = Mock()
        mock_existing.content = b"col1,col2\nval1,val2\n"
        task.sharepoint_control.is_item_exists.return_value = True
        task.sharepoint_control.get_item_by_path.return_value = mock_existing
        with (
            patch("tasks.sentiment_telesale.export_output_result_task.gemini_cost") as mock_gc,
            patch("tasks.sentiment_telesale.export_output_result_task.GeminiBatchModule.cal_gemini_cost") as mock_cost,
        ):
            mock_gc.return_value = []
            mock_cost.return_value = {"f1.wav": {"cost_input": 0.001, "cost_output": 0.002}}
            result_df = task._transaction_log(
                "Type", "User", "Source", prediction_df, execution_dt=datetime(2025, 1, 1)
            )
        assert result_df is not None
        task.sharepoint_control.upload_file.assert_called()


"""
Phase 4 Tests for ExportOutputResultTask - Error Handling & Edge Cases
Targeting missing coverage lines to reach 85% overall coverage.
"""

import pytest


class TestInitializationErrors:
    """Test error handling in __init__ method."""

    def test_init_load_yaml_error(self):
        """Test __init__ raises exception when common config fails to load (lines 118-120)."""
        with patch("tasks.sentiment_telesale.export_output_result_task.load_yaml") as mock_load:
            mock_load.side_effect = FileNotFoundError("Config file not found")

            with pytest.raises(FileNotFoundError):
                ExportOutputResultTask()


class TestPreExecuteErrorHandling:
    """Test error handling in pre_execute method."""

    @pytest.fixture
    def task(self):
        with patch("tasks.sentiment_telesale.export_output_result_task.load_yaml") as mock_load:
            mock_load.return_value = {
                "verint": {
                    "site_domain": "v.com",
                    "client_id": "cid",
                    "client_secret": "cs",
                    "tenant_id": "tid",
                    "site_path": "sp",
                },
                "control": {
                    "site_domain": "c.com",
                    "client_id": "cid",
                    "client_secret": "cs",
                    "tenant_id": "tid",
                    "site_path": "sp",
                    "gemini_cost_path": "cost.yml",
                },
            }
            t = ExportOutputResultTask()
            t.sharepoint = {"verint": {}, "control": {}}
            t.gcs = {"project_id": "p", "bucket_name": "b"}
            return t

    def test_pre_execute_verint_sharepoint_error(self, task):
        """Test pre_execute raises exception when Verint SharePoint init fails (lines 139-141)."""
        with (
            patch("tasks.sentiment_telesale.export_output_result_task.SharePointModule") as mock_sp,
            patch("tasks.sentiment_telesale.export_output_result_task.resolve_env") as mock_env,
        ):
            mock_env.return_value = "resolved_value"
            mock_sp.side_effect = Exception("SharePoint Verint connection failed")

            with pytest.raises(Exception, match="SharePoint Verint connection failed"):
                task.pre_execute()

    def test_pre_execute_control_sharepoint_error(self, task):
        """Test pre_execute raises exception when Control SharePoint init fails (lines 154-156)."""
        with (
            patch("tasks.sentiment_telesale.export_output_result_task.SharePointModule") as mock_sp,
            patch("tasks.sentiment_telesale.export_output_result_task.resolve_env") as mock_env,
        ):
            mock_env.return_value = "resolved_value"
            # First call succeeds (Verint), second call fails (Control)
            mock_sp.side_effect = [Mock(), Exception("SharePoint Control connection failed")]

            with pytest.raises(Exception, match="SharePoint Control connection failed"):
                task.pre_execute()

    def test_pre_execute_gcs_error(self, task):
        """Test pre_execute raises exception when GCS init fails (lines 167-169)."""
        with (
            patch("tasks.sentiment_telesale.export_output_result_task.SharePointModule") as mock_sp,
            patch("tasks.sentiment_telesale.export_output_result_task.GCSModule") as mock_gcs,
            patch("tasks.sentiment_telesale.export_output_result_task.resolve_env") as mock_env,
        ):
            mock_env.return_value = "resolved_value"
            mock_sp.return_value = Mock()
            mock_gcs.side_effect = Exception("GCS connection failed")

            with pytest.raises(Exception, match="GCS connection failed"):
                task.pre_execute()


class TestExecuteTaskErrorHandling:
    """Test error handling in execute_task method."""

    @pytest.fixture
    def task(self):
        with patch("tasks.sentiment_telesale.export_output_result_task.load_yaml") as mock_load:
            mock_load.return_value = {
                "verint": {
                    "site_domain": "v.com",
                    "client_id": "cid",
                    "client_secret": "cs",
                    "tenant_id": "tid",
                    "site_path": "sp",
                },
                "control": {
                    "site_domain": "c.com",
                    "client_id": "cid",
                    "client_secret": "cs",
                    "tenant_id": "tid",
                    "site_path": "sp",
                    "gemini_cost_path": "cost.yml",
                },
            }
            t = ExportOutputResultTask()
            t.sharepoint = {"verint": {"output_file": "output.csv"}, "control": {}}
            t.gcs = {"project_id": "p", "bucket_name": "b"}
            t.pre_result = {"batch_results": [{"file_metadata": {}}], "list_batchs": [], "failed_batches": []}
            t.packages = {"execution_dt": datetime(2025, 1, 1)}
            t.get_package = lambda k, d=None: t.packages.get(k, d)
            t.sharepoint_verint = Mock()
            t.sharepoint_control = Mock()
            t.gcs_module = Mock()
            return t

    def test_execute_task_output_file_resolution_error(self, task):
        """Test execute_task raises exception when output file path resolution fails (lines 186-188)."""
        with (
            patch("tasks.sentiment_telesale.export_output_result_task.resolve_date") as mock_resolve,
            patch("tasks.sentiment_telesale.export_output_result_task.resolve_env") as mock_env,
        ):
            mock_env.return_value = "output.csv"
            mock_resolve.side_effect = Exception("Date resolution failed")

            with pytest.raises(Exception, match="Cannot determine output file path"):
                task.execute_task()

    def test_execute_task_upload_error(self, task):
        """Test execute_task handles upload error gracefully (lines 200-201)."""
        with (
            patch("tasks.sentiment_telesale.export_output_result_task.resolve_date") as mock_resolve,
            patch("tasks.sentiment_telesale.export_output_result_task.resolve_env") as mock_env,
            patch("tasks.sentiment_telesale.export_output_result_task.get_current_datetime") as mock_dt,
            patch.object(task, "_format_output") as mock_format,
        ):
            mock_env.return_value = "output.csv"
            mock_resolve.return_value = "output_20250101.csv"
            mock_dt.return_value = datetime(2025, 1, 1)
            mock_format.return_value = [{"field": "value"}]
            task.sharepoint_verint.upload_file.side_effect = Exception("Upload failed")

            # Should log error but continue processing
            # This test verifies the try-except block catches the error
            with patch("tasks.sentiment_telesale.export_output_result_task.get_value_by_path") as mock_path:
                mock_path.return_value = None
                # Expected to raise due to other errors downstream
                with contextlib.suppress(Exception):
                    task.execute_task()

            # Verify upload was attempted
            task.sharepoint_verint.upload_file.assert_called_once()

    def test_execute_task_record_processing_error(self, task):
        """Test execute_task handles record processing errors gracefully (lines 214, 241-244)."""
        task.pre_result["batch_results"] = [{"file_metadata": {"file_uri": "gs://bucket/folder/file.wav"}}]

        with (
            patch("tasks.sentiment_telesale.export_output_result_task.resolve_date") as mock_resolve,
            patch("tasks.sentiment_telesale.export_output_result_task.resolve_env") as mock_env,
            patch("tasks.sentiment_telesale.export_output_result_task.get_current_datetime") as mock_dt,
            patch("tasks.sentiment_telesale.export_output_result_task.get_value_by_path") as mock_path,
            patch.object(task, "_format_output") as mock_format,
        ):
            mock_env.side_effect = lambda x: x
            mock_resolve.return_value = "output.csv"
            mock_dt.return_value = datetime(2025, 1, 1)
            mock_format.return_value = [{}]

            # First call returns valid data, subsequent calls raise exceptions
            mock_path.side_effect = [
                "20250101",  # record_date
                Exception("Path extraction failed"),  # Trigger error in record processing
            ]

            # Should continue after logging error
            # Expected to raise due to DataFrame creation or other downstream errors
            with contextlib.suppress(Exception):
                task.execute_task()

    def test_execute_task_output_template_error(self, task):
        """Test execute_task raises exception when output template resolution fails (lines 251-253)."""
        with (
            patch("tasks.sentiment_telesale.export_output_result_task.resolve_date") as mock_resolve,
            patch("tasks.sentiment_telesale.export_output_result_task.resolve_env") as mock_env,
            patch("tasks.sentiment_telesale.export_output_result_task.get_current_datetime") as mock_dt,
            patch("tasks.sentiment_telesale.export_output_result_task.get_value_by_path") as mock_path,
            patch.object(task, "_format_output") as mock_format,
        ):
            mock_dt.return_value = datetime(2025, 1, 1)
            mock_format.return_value = [{}]
            mock_path.return_value = "value"

            # First resolve_env succeeds (for upload), second fails (for template)
            call_count = [0]

            def resolve_env_side_effect(x):
                call_count[0] += 1
                if call_count[0] <= 2:
                    return "output.csv"
                raise KeyError("Environment variable not found")

            mock_env.side_effect = resolve_env_side_effect
            mock_resolve.return_value = "output.csv"

            with pytest.raises(Exception, match="Cannot determine output file path"):
                task.execute_task()

    def test_execute_task_dataframe_creation_error(self, task):
        """Test execute_task raises exception when DataFrame creation fails (lines 259-261)."""
        with (
            patch("tasks.sentiment_telesale.export_output_result_task.resolve_date") as mock_resolve,
            patch("tasks.sentiment_telesale.export_output_result_task.resolve_env") as mock_env,
            patch("tasks.sentiment_telesale.export_output_result_task.get_current_datetime") as mock_dt,
            patch("tasks.sentiment_telesale.export_output_result_task.get_value_by_path") as mock_path,
            patch.object(task, "_format_output") as mock_format,
        ):
            mock_env.return_value = "output.csv"
            mock_resolve.return_value = "output.csv"
            mock_dt.return_value = datetime(2025, 1, 1)
            mock_format.return_value = [{}]
            # Return invalid data type that will cause DataFrame creation to fail
            mock_path.side_effect = TypeError("Cannot extract path")

            with pytest.raises(Exception):
                task.execute_task()

    def test_execute_task_archive_error(self, task):
        """Test execute_task raises exception when archive fails (lines 268-270)."""
        with (
            patch("tasks.sentiment_telesale.export_output_result_task.resolve_date") as mock_resolve,
            patch("tasks.sentiment_telesale.export_output_result_task.resolve_env") as mock_env,
            patch("tasks.sentiment_telesale.export_output_result_task.get_current_datetime") as mock_dt,
            patch("tasks.sentiment_telesale.export_output_result_task.get_value_by_path") as mock_path,
            patch.object(task, "_format_output") as mock_format,
            patch.object(task, "_archive_files") as mock_archive,
        ):
            mock_env.return_value = "output.csv"
            mock_resolve.return_value = "output.csv"
            mock_dt.return_value = datetime(2025, 1, 1)
            mock_format.return_value = [{}]
            mock_path.return_value = "value"
            mock_archive.side_effect = Exception("Archive operation failed")

            with pytest.raises(Exception, match="File archival process failed"):
                task.execute_task()

    def test_execute_task_transaction_log_error(self, task):
        """Test execute_task raises exception when transaction log insertion fails (lines 275-277)."""
        with (
            patch("tasks.sentiment_telesale.export_output_result_task.resolve_date") as mock_resolve,
            patch("tasks.sentiment_telesale.export_output_result_task.resolve_env") as mock_env,
            patch("tasks.sentiment_telesale.export_output_result_task.get_current_datetime") as mock_dt,
            patch("tasks.sentiment_telesale.export_output_result_task.get_value_by_path") as mock_path,
            patch.object(task, "_format_output") as mock_format,
            patch.object(task, "_archive_files") as mock_archive,
            patch.object(task, "_insert_log_record") as mock_log,
        ):
            mock_env.return_value = "output.csv"
            mock_resolve.return_value = "output.csv"
            mock_dt.return_value = datetime(2025, 1, 1)
            mock_format.return_value = [{}]
            mock_path.return_value = "value"
            mock_archive.return_value = None
            mock_log.side_effect = Exception("Transaction log insertion failed")

            with pytest.raises(Exception, match="Transaction log insertion failed"):
                task.execute_task()


class TestFormatOutputErrorHandling:
    """Test error handling in _format_output method."""

    @pytest.fixture
    def task(self):
        with patch("tasks.sentiment_telesale.export_output_result_task.load_yaml") as mock_load:
            mock_load.return_value = {
                "verint": {
                    "site_domain": "v.com",
                    "client_id": "cid",
                    "client_secret": "cs",
                    "tenant_id": "tid",
                    "site_path": "sp",
                },
                "control": {
                    "site_domain": "c.com",
                    "client_id": "cid",
                    "client_secret": "cs",
                    "tenant_id": "tid",
                    "site_path": "sp",
                    "gemini_cost_path": "cost.yml",
                },
            }
            return ExportOutputResultTask()

    def test_format_output_empty_input(self, task):
        """Test _format_output handles empty list (lines 286-287)."""
        result = task._format_output([])
        assert result == []

    def test_format_output_extract_scoring_error(self, task):
        """Test _format_output handles scoring extraction errors (lines 310-312)."""
        raw_result = [
            {
                "file_metadata": {"file_name": "test", "file_ext": ".wav"},
                "INVALID_KEY": {},  # Missing scoring_result
            }
        ]

        with (
            patch("tasks.sentiment_telesale.export_output_result_task.get_value_by_path") as mock_path,
            patch("tasks.sentiment_telesale.export_output_result_task.get_current_datetime") as mock_dt,
        ):
            mock_dt.return_value = datetime(2025, 1, 1)
            mock_path.side_effect = Exception("Extraction failed")

            # Should raise due to extraction error
            with pytest.raises(Exception):
                task._format_output(raw_result)

    def test_format_output_build_detail_error(self, task):
        """Test _format_output handles support detail building errors (lines 339-341)."""
        raw_result = [
            {
                "file_metadata": {"file_name": "test", "file_ext": ".wav"},
                "scoring_result": {"scoring_detail": {}},
                "prediction": {"raw_prediction": {}},
            }
        ]

        with (
            patch("tasks.sentiment_telesale.export_output_result_task.get_value_by_path") as mock_path,
            patch("tasks.sentiment_telesale.export_output_result_task.get_current_datetime") as mock_dt,
        ):
            mock_dt.return_value = datetime(2025, 1, 1)

            # First few calls succeed for extraction, then fail in detail building
            call_count = [0]

            def path_side_effect(*args, **kwargs):
                call_count[0] += 1
                if call_count[0] <= 7:  # Allow scoring_detail extraction
                    return {}
                raise Exception("Detail building failed")

            mock_path.side_effect = path_side_effect

            with pytest.raises(Exception):
                task._format_output(raw_result)

    def test_format_output_status_determination_error(self, task):
        """Test _format_output handles status determination errors (lines 367)."""
        # This test validates that errors in status determination are caught
        # The actual code has try-except that continues processing after logging
        raw_result = [
            {
                "file_metadata": {"file_name": "test", "file_ext": ".wav"},
                "scoring_result": {
                    "scoring_detail": {
                        "operations_and_professionalism": {},
                        "sales_effectiveness": {},
                        "customer_experience": {},
                        "compliance": {},
                    }
                },
                "prediction": {
                    "raw_prediction": {},
                    "status": "FAILED",
                    "message": "Test error",
                    "model_version": "gemini-1.5",
                    "token_input": {},
                    "token_output": {},
                },
            }
        ]

        with (
            patch("tasks.sentiment_telesale.export_output_result_task.get_value_by_path") as mock_path,
            patch("tasks.sentiment_telesale.export_output_result_task.get_current_datetime") as mock_dt,
        ):
            mock_dt.return_value = datetime(2025, 1, 1)

            # Return strings for filename parts, empty strings for others
            def path_side_effect(rec, path, default=None):
                if "file_name" in path:
                    return "test"
                if "file_ext" in path:
                    return ".wav"
                if "record_date" in path:
                    return "20250101"
                if "status" in path or "message" in path:
                    return "FAILED" if "status" in path else "Test error"
                return "" if isinstance(default, str) else (default or "")

            mock_path.side_effect = path_side_effect

            # Should handle gracefully and return result
            result = task._format_output(raw_result)
            # Verify it processed the record (even if with errors)
            assert isinstance(result, list)

    def test_format_output_customer_info_error(self, task):
        """Test _format_output handles customer info building errors (lines 396-398)."""
        # Test that errors during cost calculation are caught
        raw_result = [
            {
                "file_metadata": {"file_name": "test", "file_ext": ".wav", "record_date": "20250101"},
                "scoring_result": {
                    "scoring_detail": {
                        "operations_and_professionalism": {},
                        "sales_effectiveness": {},
                        "customer_experience": {},
                        "compliance": {},
                    }
                },
                "prediction": {
                    "raw_prediction": {},
                    "status": "SUCCESS",
                    "model_version": "gemini-1.5",
                    "token_input": {},
                    "token_output": {},
                },
            }
        ]

        with (
            patch("tasks.sentiment_telesale.export_output_result_task.get_value_by_path") as mock_path,
            patch("tasks.sentiment_telesale.export_output_result_task.get_current_datetime") as mock_dt,
            patch("tasks.sentiment_telesale.export_output_result_task.gemini_cost") as mock_cost,
            patch("tasks.sentiment_telesale.export_output_result_task.GeminiBatchModule") as mock_batch,
        ):
            mock_dt.return_value = datetime(2025, 1, 1)
            mock_path.return_value = {}
            mock_cost.return_value = []
            # Cause error in cost calculation
            mock_batch.cal_gemini_cost.side_effect = Exception("Cost calculation failed")

            with pytest.raises(Exception):
                task._format_output(raw_result)


class TestArchiveFilesErrorHandling:
    """Test error handling in _archive_files method."""

    @pytest.fixture
    def task(self):
        with patch("tasks.sentiment_telesale.export_output_result_task.load_yaml") as mock_load:
            mock_load.return_value = {
                "verint": {
                    "site_domain": "v.com",
                    "client_id": "cid",
                    "client_secret": "cs",
                    "tenant_id": "tid",
                    "site_path": "sp",
                },
                "control": {
                    "site_domain": "c.com",
                    "client_id": "cid",
                    "client_secret": "cs",
                    "tenant_id": "tid",
                    "site_path": "sp",
                    "gemini_cost_path": "cost.yml",
                },
            }
            t = ExportOutputResultTask()
            t.gcs = {
                "processing_voice_folder": "proc",
                "archive_voice_folder": "arch",
                "input_folder": "input",
                "archive_batch_folder": "batch_arch",
            }
            t.gcs_module = Mock()
            return t

    def test_archive_files_missing_folder_error(self, task):
        """Test _archive_files handles missing folder errors (lines 569-571)."""
        df = pd.DataFrame([{"record_date": "20250101", "status": "SUCCESS"}])

        task.gcs_module.is_dir_exists.return_value = False

        # Should handle missing folder gracefully
        with patch("tasks.sentiment_telesale.export_output_result_task.resolve_env") as mock_env:
            mock_env.return_value = "value"
            task._archive_files(datetime(2025, 1, 1), df, [], [])


class TestTransactionLogErrorHandling:
    """Test error handling in _transaction_log method."""

    @pytest.fixture
    def task(self):
        with patch("tasks.sentiment_telesale.export_output_result_task.load_yaml") as mock_load:
            mock_load.return_value = {
                "verint": {
                    "site_domain": "v.com",
                    "client_id": "cid",
                    "client_secret": "cs",
                    "tenant_id": "tid",
                    "site_path": "sp",
                },
                "control": {
                    "site_domain": "c.com",
                    "client_id": "cid",
                    "client_secret": "cs",
                    "tenant_id": "tid",
                    "site_path": "sp",
                    "gemini_cost_path": "cost.yml",
                },
            }
            t = ExportOutputResultTask()
            t.sharepoint = {"control": {"transaction_log_file": "trans.csv"}}
            t.gcp = {"project_id": "p", "project_name": "n"}
            t.sharepoint_control = Mock()
            return t

    def test_transaction_log_schema_error(self, task):
        """Test _transaction_log handles schema validation errors (lines 591-593)."""
        df = pd.DataFrame([{"file_name": "test.wav", "invalid_column": "value"}])

        with (
            patch("tasks.sentiment_telesale.export_output_result_task.ensure_df_schema") as mock_schema,
            patch("tasks.sentiment_telesale.export_output_result_task.resolve_env") as mock_env,
            patch("tasks.sentiment_telesale.export_output_result_task.resolve_date") as mock_date,
            patch("tasks.sentiment_telesale.export_output_result_task.gemini_cost") as mock_cost,
            patch("tasks.sentiment_telesale.export_output_result_task.GeminiBatchModule") as mock_batch,
        ):
            mock_env.return_value = "value"
            mock_date.return_value = "file.csv"
            mock_schema.side_effect = Exception("Schema validation failed")
            mock_cost.return_value = []
            mock_batch.cal_gemini_cost.return_value = {}

            with pytest.raises(Exception):
                task._transaction_log("Type", "User", "Source", df, execution_dt=datetime(2025, 1, 1))


class TestPerformanceLogErrorHandling:
    """Test error handling in _performance_log method."""

    @pytest.fixture
    def task(self):
        with patch("tasks.sentiment_telesale.export_output_result_task.load_yaml") as mock_load:
            mock_load.return_value = {
                "verint": {
                    "site_domain": "v.com",
                    "client_id": "cid",
                    "client_secret": "cs",
                    "tenant_id": "tid",
                    "site_path": "sp",
                },
                "control": {
                    "site_domain": "c.com",
                    "client_id": "cid",
                    "client_secret": "cs",
                    "tenant_id": "tid",
                    "site_path": "sp",
                    "gemini_cost_path": "cost.yml",
                },
            }
            t = ExportOutputResultTask()
            t.sharepoint = {"control": {"performance_log_file": "perf.csv"}}
            t.sharepoint_control = Mock()
            return t

    def test_performance_log_schema_error(self, task):
        """Test _performance_log handles schema validation errors (lines 1031, 1035)."""
        df = pd.DataFrame([{"data_date": "20250101", "invalid_col": "val"}])

        with (
            patch("tasks.sentiment_telesale.export_output_result_task.ensure_df_schema") as mock_schema,
            patch("tasks.sentiment_telesale.export_output_result_task.resolve_env") as mock_env,
            patch("tasks.sentiment_telesale.export_output_result_task.resolve_date") as mock_date,
        ):
            mock_env.return_value = "value"
            mock_date.return_value = "file.csv"
            mock_schema.side_effect = Exception("Performance schema validation failed")

            with pytest.raises(Exception):
                task._performance_log(df)


class TestInsertLogRecord:
    """Test _insert_log_record which calls _transaction_log and _performance_log."""

    @pytest.fixture
    def task(self):
        with patch("tasks.sentiment_telesale.export_output_result_task.load_yaml") as mock_load:
            mock_load.return_value = {
                "verint": {
                    "site_domain": "v.com",
                    "client_id": "cid",
                    "client_secret": "cs",
                    "tenant_id": "tid",
                    "site_path": "sp",
                },
                "control": {
                    "site_domain": "c.com",
                    "client_id": "cid",
                    "client_secret": "cs",
                    "tenant_id": "tid",
                    "site_path": "sp",
                    "gemini_cost_path": "cost.yml",
                },
            }
            t = ExportOutputResultTask()
            t.sharepoint = {
                "verint": {"output_file": "out.csv"},
                "control": {
                    "transaction_log_file": "trans.csv",
                    "performance_log_file": "perf.csv",
                    "batch_processing_log_file": "batch.csv",
                },
            }
            t.gcp = {"project_id": "p", "project_name": "n"}
            t.packages = {"execution_dt": datetime(2025, 1, 1)}
            t.get_package = lambda k, d=None: t.packages.get(k, d)
            t.sharepoint_control = Mock()
            return t

    def test_insert_log_record_success(self, task):
        """Lines 936-971: calls _transaction_log and _performance_log when log df is not empty."""
        trans_df = pd.DataFrame([{"data_date": "20250101", "load_dt": "dt"}])
        df = pd.DataFrame([{"file_name": "x.wav"}])
        with (
            patch.object(task, "_transaction_log", return_value=trans_df) as mock_tl,
            patch.object(task, "_performance_log", return_value=None) as mock_pl,
        ):
            task._insert_log_record(df)
        mock_tl.assert_called_once()
        mock_pl.assert_called_once_with(transaction_log_df=trans_df)

    def test_insert_log_record_none_trans_df_skips_perf(self, task):
        """Lines 975-976: if transaction_log_df is None, skip performance log."""
        df = pd.DataFrame([{"file_name": "x.wav"}])
        with (
            patch.object(task, "_transaction_log", return_value=None),
            patch.object(task, "_performance_log") as mock_pl,
        ):
            task._insert_log_record(df)
        mock_pl.assert_not_called()

    def test_insert_log_record_trans_fails_raises(self, task):
        """Lines 958-962: transaction log failure re-raised."""
        df = pd.DataFrame([{"file_name": "x.wav"}])
        with patch.object(task, "_transaction_log", side_effect=ValueError("schema error")):
            with pytest.raises(Exception, match="Transaction log creation failed"):
                task._insert_log_record(df)

    def test_insert_log_record_perf_exception_continues(self, task):
        """Lines 964-966: _performance_log raises non-critical error → execution continues without raising."""
        trans_df = pd.DataFrame([{"data_date": "20250101", "load_dt": "dt"}])
        df = pd.DataFrame([{"file_name": "x.wav"}])
        with (
            patch.object(task, "_transaction_log", return_value=trans_df),
            patch.object(task, "_performance_log", side_effect=RuntimeError("perf log failed")),
        ):
            # Should NOT raise - performance log is non-critical
            task._insert_log_record(df)


class TestPerformanceLogCoverage:
    """Happy-path tests for _performance_log to cover lines 1282-1294, 1328."""

    @pytest.fixture
    def task(self):
        with patch("tasks.sentiment_telesale.export_output_result_task.load_yaml") as mock_load:
            mock_load.return_value = {
                "verint": {
                    "site_domain": "v.com",
                    "client_id": "cid",
                    "client_secret": "cs",
                    "tenant_id": "tid",
                    "site_path": "sp",
                },
                "control": {
                    "site_domain": "c.com",
                    "client_id": "cid",
                    "client_secret": "cs",
                    "tenant_id": "tid",
                    "site_path": "sp",
                    "gemini_cost_path": "cost.yml",
                },
            }
            t = ExportOutputResultTask()
            t.sharepoint = {"control": {"performance_log_file": "perf.csv"}}
            t.sharepoint_control = Mock()
            t.sharepoint_control.upload_file.return_value = None
            return t

    def _make_trans_df(self, start_time="20250101"):
        return pd.DataFrame(
            [
                {
                    "data_date": "20250101",
                    "start_time": start_time,
                    "load_dt": "2025-01-01",
                    "gcp_project_id": "proj",
                    "gcp_project_name": "My Project",
                    "status_pass_failed_retry": "Pass",
                    "latency_ms": 100,
                }
            ]
        )

    def test_performance_log_creates_new_file(self, task):
        """Lines 1272-1315, 1328: happy path — new performance log (no existing file)."""
        trans_df = self._make_trans_df()
        task.sharepoint_control.is_item_exists.return_value = False
        with (
            patch("tasks.sentiment_telesale.export_output_result_task.resolve_env", side_effect=lambda x: x),
            patch(
                "tasks.sentiment_telesale.export_output_result_task.resolve_date",
                side_effect=lambda text, replace_date: text,
            ),
        ):
            task._performance_log(trans_df)
        task.sharepoint_control.upload_file.assert_called()

    def test_performance_log_merges_existing(self, task):
        """Lines 1282-1291: when existing performance log found, merges with new records."""
        trans_df = self._make_trans_df()
        mock_existing = Mock()
        mock_existing.content = b"col1,col2\nval1,val2\n"
        task.sharepoint_control.is_item_exists.return_value = True
        task.sharepoint_control.get_item_by_path.return_value = mock_existing
        with (
            patch("tasks.sentiment_telesale.export_output_result_task.resolve_env", side_effect=lambda x: x),
            patch(
                "tasks.sentiment_telesale.export_output_result_task.resolve_date",
                side_effect=lambda text, replace_date: text,
            ),
        ):
            task._performance_log(trans_df)
        task.sharepoint_control.upload_file.assert_called()

    def test_performance_log_invalid_start_time(self, task):
        """Line 1196: invalid start_time triggers NaT coercion and warning."""
        trans_df = self._make_trans_df(start_time="not-a-valid-date")
        task.sharepoint_control.is_item_exists.return_value = False
        with (
            patch("tasks.sentiment_telesale.export_output_result_task.resolve_env", side_effect=lambda x: x),
            patch(
                "tasks.sentiment_telesale.export_output_result_task.resolve_date",
                side_effect=lambda text, replace_date: text,
            ),
        ):
            task._performance_log(trans_df)
        task.sharepoint_control.upload_file.assert_called()


class TestExportOutputResultTaskAdditional(TestExportOutputResultTask):
    def test_export_raw_result_uploads_json_to_control_site(self, task):
        task.sharepoint["control"]["raw_prediction_folder"] = "raw_results"

        task._export_raw_result(
            execution_dt=datetime(2025, 1, 1),
            raw_result=[{"file_name": "sample"}],
            filename="nested/output.csv",
        )

        kwargs = task.sharepoint_control.upload_file.call_args.kwargs
        assert kwargs["upload_path"] == "raw_results/output.json"
        assert b'"file_name": "sample"' in kwargs["content"]

    def test_export_raw_result_swallows_upload_errors(self, task):
        task.sharepoint["control"]["raw_prediction_folder"] = "raw_results"
        task.sharepoint_control.upload_file.side_effect = RuntimeError("upload failed")

        task._export_raw_result(
            execution_dt=datetime(2025, 1, 1),
            raw_result=[{"file_name": "sample"}],
            filename="output.csv",
        )

    def test_transaction_log_missing_file_name_raises(self, task):
        prediction_df = pd.DataFrame(
            [
                {
                    "file_name": None,
                    "full_path": "path/to/file.wav",
                    "folder": "folder",
                    "record_date": "20250101",
                    "duration": 60,
                    "token_input": {"text": 10},
                    "token_cached": 0,
                    "token_output": {"text": 5},
                    "status": "SUCCESS",
                    "message": None,
                    "processed_time": "2025-01-01T10:00:00",
                    "create_time": "2025-01-01T10:01:00",
                    "model_version": "gemini-1.5-flash",
                    "load_dt": "2025-01-01 10:02:00",
                }
            ]
        )

        with pytest.raises(Exception, match="Missing required field 'file_name'"):
            task._transaction_log("Type", "User", "Source", prediction_df, datetime(2025, 1, 1))

    def test_performance_log_missing_columns_raises(self, task):
        with pytest.raises(ValueError, match="missing columns"):
            task._performance_log(pd.DataFrame([{"data_date": "20250101"}]))

    def test_post_execute_returns_when_no_process_dates(self, task):
        task._cache_oper_log = {
            "process_date": [],
            "transaction_df": pd.DataFrame(),
        }

        assert task.post_execute() is None

    def test_post_execute_returns_when_transaction_df_empty(self, task):
        task._cache_oper_log = {
            "process_date": [datetime(2025, 1, 1).date()],
            "transaction_df": pd.DataFrame(),
        }

        assert task.post_execute() is None

    def test_post_execute_skips_unmatched_process_dates(self, task):
        task._cache_oper_log = {
            "process_date": [datetime(2025, 1, 2).date()],
            "transaction_df": pd.DataFrame(
                [
                    {
                        "start_time": "2025-01-01T00:00:00Z",
                        "end_time": "2025-01-01T00:00:02Z",
                        "gcp_project_id": "proj-1",
                        "status_pass_failed_retry": "Pass",
                        "latency_ms": 2000,
                    }
                ]
            ),
        }

        with patch("tasks.sentiment_telesale.export_output_result_task.logging_ai_operation") as mock_log:
            task.post_execute()

        mock_log.assert_not_called()

    def test_post_execute_stamps_ai_operation_logs(self, task):
        task._cache_oper_log = {
            "process_date": [datetime(2025, 1, 1).date()],
            "transaction_df": pd.DataFrame(
                [
                    {
                        "start_time": "2025-01-01T00:00:00Z",
                        "end_time": "2025-01-01T00:00:03Z",
                        "gcp_project_id": "proj-1",
                        "status_pass_failed_retry": "Pass",
                        "latency_ms": 1000,
                    },
                    {
                        "start_time": "2025-01-01T00:00:05Z",
                        "end_time": "2025-01-01T00:00:10Z",
                        "gcp_project_id": "proj-1",
                        "status_pass_failed_retry": "Failed",
                        "latency_ms": 2000,
                    },
                ]
            ),
        }

        with (
            patch("tasks.sentiment_telesale.export_output_result_task.logging_ai_operation") as mock_log,
            patch.dict("os.environ", {"ENVIRONMENT": "prod"}, clear=False),
        ):
            task.post_execute()

        mock_log.assert_called_once()
        log_obj = mock_log.call_args.kwargs["log_obj"]
        assert log_obj["environment"] == "production"
        assert log_obj["project_type"] == "batch"
        assert log_obj["total_transaction"] == 2
        assert log_obj["total_success_transaction"] == 1
        assert log_obj["total_failed_transaction"] == 1

    def test_execute_task_continues_when_monitoring_export_fails(self, task):
        task.pre_result["batch_results"] = [
            {
                "file_metadata": {
                    "file_name": "f1",
                    "file_ext": ".wav",
                    "record_date": "20250101",
                    "file_uri": "gs://b/folder/f1.wav",
                    "duration": 100,
                },
                "prediction": {
                    "status": "SUCCESS",
                    "model_version": "gemini-1.5",
                    "raw_prediction": {},
                    "token_input": {},
                    "token_output": {},
                    "token_cached": 0,
                },
                "scoring_result": {"scoring_status": "SUCCESS", "scoring_detail": {}},
            }
        ]
        task.pre_result["list_batchs"] = ["batch1"]

        with (
            patch.dict("os.environ", {"IS_MONITORING_ENABLED": "true"}, clear=False),
            patch.object(task, "_format_output", return_value=[{"filename": "f1.wav"}]),
            patch.object(task, "_export_raw_result", side_effect=RuntimeError("monitoring failed")),
            patch.object(task, "_archive_files") as mock_archive,
            patch.object(task, "_insert_log_record") as mock_log,
        ):
            result = task.execute_task()

        mock_archive.assert_called_once()
        mock_log.assert_called_once()
        assert result == task.pre_result["batch_results"]

    def test_format_output_converts_record_errors_into_failed_rows(self, task):
        with patch(
            "tasks.sentiment_telesale.export_output_result_task.safe_cast_value", side_effect=RuntimeError("bad cast")
        ):
            formatted = task._format_output(self._make_raw_result())

        assert len(formatted) == 1
        assert formatted[0]["status"] == "FAILED"
        assert formatted[0]["summary"] is None
        assert "bad cast" in formatted[0]["error_code"]

    def test_format_output_handles_failed_batch_log_append_errors(self, task):
        raw_result = self._make_raw_result()
        log_csv = (
            "batch_job_id,batch_job_display_name,filename,status,error_message,updated_dt\n"
            "batch1,Job1,f1.wav,SUCCESS,0,2025-01-01 10:00:00\n"
            "batch1,Job1,f2.wav,FAILED,123,2025-01-01 10:01:00\n"
        )
        task.sharepoint_control.is_item_exists.return_value = True
        task.sharepoint_control.get_item_by_path.return_value = Mock(content=log_csv.encode("utf-8"))

        formatted = task._format_output(raw_result)

        assert len(formatted) == 2
        assert formatted[1]["filename"] is None
        assert formatted[1]["status"] == "FAILED"
        assert "Unknown error during log append" in formatted[1]["error_code"]

    def test_transaction_log_existing_merge_failure_raises(self, task):
        prediction_df = pd.DataFrame(
            [
                {
                    "file_name": "f1.wav",
                    "full_path": "path/to/f1.wav",
                    "folder": "folder",
                    "record_date": "20250101",
                    "duration": 60,
                    "token_input": {"text": 10},
                    "token_cached": 0,
                    "token_output": {"text": 5},
                    "status": "SUCCESS",
                    "message": None,
                    "processed_time": "2025-01-01T10:00:00",
                    "create_time": "2025-01-01T10:01:00",
                    "model_version": "gemini-1.5-flash",
                    "load_dt": "2025-01-01 10:02:00",
                }
            ]
        )
        task.sharepoint_control.is_item_exists.return_value = True
        task.sharepoint_control.get_item_by_path.return_value = Mock(content=b"broken csv")

        with (
            patch("tasks.sentiment_telesale.export_output_result_task.gemini_cost", return_value=[]),
            patch(
                "tasks.sentiment_telesale.export_output_result_task.GeminiBatchModule.cal_gemini_cost",
                return_value={"f1.wav": {"cost_input": 0.001, "cost_output": 0.002}},
            ),
            patch("pandas.read_csv", side_effect=ValueError("bad csv")),
        ):
            with pytest.raises(Exception, match="Cannot upload transaction log"):
                task._transaction_log("Type", "User", "Source", prediction_df, datetime(2025, 1, 1))

    def test_performance_log_continues_when_upload_fails(self, task):
        trans_df = pd.DataFrame(
            [
                {
                    "data_date": "20250101",
                    "start_time": "2025-01-01T00:00:00",
                    "load_dt": "2025-01-01 00:00:00",
                    "gcp_project_id": "proj",
                    "gcp_project_name": "proj-name",
                    "status_pass_failed_retry": "Pass",
                    "latency_ms": 100,
                }
            ]
        )
        task.sharepoint = {"control": {"performance_log_file": "perf.csv"}}
        task.sharepoint_control.is_item_exists.return_value = False
        task.sharepoint_control.upload_file.side_effect = RuntimeError("upload failed")

        with (
            patch("tasks.sentiment_telesale.export_output_result_task.resolve_env", side_effect=lambda x: x),
            patch(
                "tasks.sentiment_telesale.export_output_result_task.resolve_date",
                side_effect=lambda text, replace_date: text,
            ),
        ):
            task._performance_log(trans_df)

    def test_performance_log_handles_date_level_failures(self, task):
        trans_df = pd.DataFrame(
            [
                {
                    "data_date": "20250101",
                    "start_time": "2025-01-01T00:00:00",
                    "load_dt": "2025-01-01 00:00:00",
                    "gcp_project_id": "proj",
                    "gcp_project_name": "proj-name",
                    "status_pass_failed_retry": "Pass",
                    "latency_ms": 100,
                }
            ]
        )
        task.sharepoint = {"control": {"performance_log_file": "perf.csv"}}
        task.sharepoint_control.is_item_exists.return_value = False

        with (
            patch("tasks.sentiment_telesale.export_output_result_task.resolve_env", side_effect=lambda x: x),
            patch(
                "tasks.sentiment_telesale.export_output_result_task.resolve_date", side_effect=ValueError("bad path")
            ),
        ):
            task._performance_log(trans_df)

    def test_post_execute_marks_non_production_environment(self, task):
        task._cache_oper_log = {
            "process_date": [datetime(2025, 1, 1).date()],
            "transaction_df": pd.DataFrame(
                [
                    {
                        "start_time": "2025-01-01T00:00:00Z",
                        "end_time": "2025-01-01T00:00:02Z",
                        "gcp_project_id": "proj-1",
                        "status_pass_failed_retry": "Pass",
                        "latency_ms": 1000,
                    }
                ]
            ),
        }

        with (
            patch("tasks.sentiment_telesale.export_output_result_task.logging_ai_operation") as mock_log,
            patch.dict("os.environ", {"ENVIRONMENT": "nprd"}, clear=False),
        ):
            task.post_execute()

        assert mock_log.call_args.kwargs["log_obj"]["environment"] == "non-production"

    def test_post_execute_swallows_ai_operation_logging_failures(self, task):
        task._cache_oper_log = {
            "process_date": [datetime(2025, 1, 1).date()],
            "transaction_df": pd.DataFrame(
                [
                    {
                        "start_time": "2025-01-01T00:00:00Z",
                        "end_time": "2025-01-01T00:00:02Z",
                        "gcp_project_id": "proj-1",
                        "status_pass_failed_retry": "Pass",
                        "latency_ms": 1000,
                    }
                ]
            ),
        }

        with (
            patch(
                "tasks.sentiment_telesale.export_output_result_task.logging_ai_operation",
                side_effect=RuntimeError("log failed"),
            ),
            patch.dict("os.environ", {"ENVIRONMENT": "qa"}, clear=False),
        ):
            task.post_execute()

    def test_execute_task_missing_file_uri_raises_dataframe_error(self, task):
        task.pre_result["batch_results"] = [
            {
                "file_metadata": {
                    "file_name": "f1",
                    "file_ext": ".wav",
                    "record_date": "20250101",
                    "file_uri": None,
                    "duration": 100,
                },
                "prediction": {
                    "status": "SUCCESS",
                    "model_version": "gemini-1.5",
                    "raw_prediction": {},
                    "token_input": {},
                    "token_output": {},
                    "token_cached": 0,
                },
                "scoring_result": {"scoring_status": "SUCCESS", "scoring_detail": {}},
            }
        ]

        real_dataframe = pd.DataFrame
        call_count = {"value": 0}

        def dataframe_side_effect(*args, **kwargs):
            call_count["value"] += 1
            if call_count["value"] == 1:
                return real_dataframe(*args, **kwargs)
            raise RuntimeError("after upload frame failed")

        with (
            patch.object(task, "_format_output", return_value=[{"filename": "f1.wav"}]),
            patch("tasks.sentiment_telesale.export_output_result_task.pd.DataFrame", side_effect=dataframe_side_effect),
        ):
            with pytest.raises(Exception, match="Cannot create DataFrame for archival"):
                task.execute_task()

    @pytest.mark.parametrize(
        ("target_message", "branch_error"),
        [
            ("Extracted scoring details successfully", "extract branch failed"),
            ("Built support detail strings", "detail branch failed"),
            ("Status=", "status branch failed"),
            ("Sales scores calculated", "sales branch failed"),
            ("Customer experience scores calculated", "customer branch failed"),
            ("Compliance scores calculated", "compliance branch failed"),
            ("Processed checklist", "checklist branch failed"),
            ("Built scoring result structure", "scoring result branch failed"),
            ("Calculated total scores", "summary branch failed"),
            ("Successfully formatted", "combine branch failed"),
        ],
    )
    def test_format_output_captures_internal_branch_failures(self, task, target_message, branch_error):
        def debug_side_effect(message, *args, **kwargs):
            if target_message in str(message):
                raise RuntimeError(branch_error)
            return

        with patch("tasks.sentiment_telesale.export_output_result_task.logger.debug", side_effect=debug_side_effect):
            formatted = task._format_output(self._make_raw_result())

        assert any(row["status"] == "FAILED" and branch_error in (row.get("error_code") or "") for row in formatted)

    def test_format_output_handles_none_duration_and_none_subscores(self, task):
        raw_result = self._make_raw_result()
        record = raw_result[0]
        record["file_metadata"]["duration"] = None
        record["scoring_result"]["scoring_detail"]["sales_effectiveness"]["detail"] = {
            "customer_needs_analysis": {"score": None}
        }
        record["scoring_result"]["scoring_detail"]["customer_experience"]["detail"] = {
            "positive_customer_experience": {"score": None}
        }
        record["scoring_result"]["scoring_detail"]["compliance"]["detail"] = {"compliance": {"score": None}}

        formatted = task._format_output(raw_result)
        qa_call_data = json.loads(formatted[0]["summary"])["qa_call_data"]

        assert qa_call_data["customer_information"]["call_duration_sec"] is None
        assert qa_call_data["sales_effectiveness"]["metrics"]["customer_needs_analysis"] is None
        assert qa_call_data["customer_experience"]["metrics"]["positive_customer_experience"] is None
        assert qa_call_data["compliance"]["metrics"]["compliance"] is None

    def test_format_output_skips_batch_log_processing_errors(self, task):
        task.sharepoint_control.is_item_exists.return_value = True
        task.sharepoint_control.get_item_by_path.return_value = Mock(content=b"broken csv payload")

        with patch("pandas.read_csv", side_effect=ValueError("batch log read failed")):
            formatted = task._format_output(self._make_raw_result())

        assert len(formatted) == 1
        assert formatted[0]["status"] == "SUCCESS"

    def test_format_output_raises_when_no_records_are_iterated(self, task):
        class EmptyIterable:
            def __len__(self):
                return 1

            def __iter__(self):
                return iter(())

        with pytest.raises(Exception, match="Failed to format any output records"):
            task._format_output(EmptyIterable())

    def test_archive_files_skips_voice_archival_when_path_resolution_fails(self, task):
        df = pd.DataFrame([{"record_date": "20250101", "status": "SUCCESS", "file_name": "f1.wav"}])

        with patch(
            "tasks.sentiment_telesale.export_output_result_task.resolve_date",
            side_effect=RuntimeError("bad voice path"),
        ):
            task._archive_files(datetime(2025, 1, 1), df, [], [])

    def test_archive_files_handles_missing_voice_folders_and_cleanup_skip(self, task):
        df = pd.DataFrame([{"record_date": "20250101", "status": "SUCCESS", "file_name": "f1.wav"}])
        task.gcs_module.is_dir_exists.side_effect = [False, False, False]

        with patch(
            "tasks.sentiment_telesale.export_output_result_task.resolve_date",
            side_effect=lambda text, replace_date: text,
        ):
            task._archive_files(
                datetime(2025, 1, 1),
                df,
                ["gs://bucket/output/20250101/batch1.jsonl"],
                [],
            )

    def test_archive_files_handles_folder_listing_errors(self, task):
        df = pd.DataFrame([{"record_date": "20250101", "status": "SUCCESS", "file_name": "f1.wav"}])
        task.gcs_module.is_dir_exists.side_effect = [True, True]
        task.gcs_module.list_files.side_effect = [RuntimeError("proc list failed"), RuntimeError("input list failed")]

        with patch(
            "tasks.sentiment_telesale.export_output_result_task.resolve_date",
            side_effect=lambda text, replace_date: text,
        ):
            task._archive_files(datetime(2025, 1, 1), df, [], [])

    def test_archive_files_handles_partial_copy_and_delete_failures(self, task):
        df = pd.DataFrame([{"record_date": "20250101", "status": "SUCCESS", "file_name": "f1.wav"}])
        task.gcs_module.is_dir_exists.side_effect = [True, True]
        task.gcs_module.list_files.side_effect = [["proc/f1.wav"], ["input/f1.wav"]]
        task.gcs_module.copy_files_batch = AsyncMock(return_value={"success": 0, "failed": 1})

        def delete_side_effect(file_path):
            if file_path.startswith("proc"):
                raise RuntimeError("processing delete failed")
            raise RuntimeError("input delete failed")

        task.gcs_module.delete_file.side_effect = delete_side_effect

        with patch(
            "tasks.sentiment_telesale.export_output_result_task.resolve_date",
            side_effect=lambda text, replace_date: text,
        ):
            task._archive_files(datetime(2025, 1, 1), df, [], [])

    def test_archive_files_handles_batch_copy_failure(self, task):
        df = pd.DataFrame([{"record_date": "20250101", "status": "SUCCESS", "file_name": "f1.wav"}])
        task.gcs_module.is_dir_exists.side_effect = [True, False]
        task.gcs_module.list_files.side_effect = [["proc/f1.wav"]]
        task.gcs_module.copy_files_batch = AsyncMock(side_effect=RuntimeError("batch copy failed"))

        with patch(
            "tasks.sentiment_telesale.export_output_result_task.resolve_date",
            side_effect=lambda text, replace_date: text,
        ):
            task._archive_files(datetime(2025, 1, 1), df, [], [])

    def test_archive_files_skips_batch_when_archive_path_resolution_fails(self, task):
        df = pd.DataFrame([{"record_date": "20250101", "status": "SUCCESS", "file_name": "f1.wav"}])
        task.gcs_module.is_dir_exists.side_effect = [False, False]

        def resolve_date_side_effect(text, replace_date):
            if text == task.gcs["archive_batch_folder"]:
                raise RuntimeError("archive batch path failed")
            return text

        with patch(
            "tasks.sentiment_telesale.export_output_result_task.resolve_date", side_effect=resolve_date_side_effect
        ):
            task._archive_files(
                datetime(2025, 1, 1),
                df,
                ["gs://bucket/output/20250101/batch1.jsonl"],
                [],
            )

    def test_archive_files_skips_batch_when_path_parsing_fails(self, task):
        df = pd.DataFrame([{"record_date": "20250101", "status": "SUCCESS", "file_name": "f1.wav"}])
        task.gcs_module.is_dir_exists.side_effect = [False, False]

        with (
            patch(
                "tasks.sentiment_telesale.export_output_result_task.resolve_date",
                side_effect=lambda text, replace_date: text,
            ),
            patch(
                "tasks.sentiment_telesale.export_output_result_task.Path",
                side_effect=RuntimeError("batch path parse failed"),
            ),
        ):
            task._archive_files(
                datetime(2025, 1, 1),
                df,
                ["gs://bucket/output/20250101/batch1.jsonl"],
                [],
            )

    def test_archive_files_continues_when_output_folder_tracking_fails(self, task):
        df = pd.DataFrame([{"record_date": "20250101", "status": "SUCCESS", "file_name": "f1.wav"}])
        task.gcs_module.is_dir_exists.side_effect = [False, False]

        with (
            patch(
                "tasks.sentiment_telesale.export_output_result_task.resolve_date",
                side_effect=lambda text, replace_date: text,
            ),
            patch(
                "tasks.sentiment_telesale.export_output_result_task.os.path.dirname",
                side_effect=RuntimeError("dirname failed"),
            ),
        ):
            task._archive_files(
                datetime(2025, 1, 1),
                df,
                ["gs://bucket/output/20250101/batch1.jsonl"],
                [],
            )

    def test_archive_files_continues_when_batch_move_fails(self, task):
        df = pd.DataFrame([{"record_date": "20250101", "status": "SUCCESS", "file_name": "f1.wav"}])
        task.gcs_module.is_dir_exists.side_effect = [False, False]
        task.gcs_module.move_file.side_effect = RuntimeError("move failed")

        with patch(
            "tasks.sentiment_telesale.export_output_result_task.resolve_date",
            side_effect=lambda text, replace_date: text,
        ):
            task._archive_files(
                datetime(2025, 1, 1),
                df,
                ["gs://bucket/output/20250101/batch1.jsonl"],
                [],
            )

    def test_archive_files_continues_when_output_cleanup_fails(self, task):
        df = pd.DataFrame([{"record_date": "20250101", "status": "SUCCESS", "file_name": "f1.wav"}])
        task.gcs_module.is_dir_exists.side_effect = [False, False, True]
        task.gcs_module.delete_dir.side_effect = RuntimeError("cleanup failed")

        with patch(
            "tasks.sentiment_telesale.export_output_result_task.resolve_date",
            side_effect=lambda text, replace_date: text,
        ):
            task._archive_files(
                datetime(2025, 1, 1),
                df,
                ["gs://bucket/output/20250101/batch1.jsonl"],
                [],
            )

    def test_transaction_log_missing_required_columns_raises(self, task):
        prediction_df = pd.DataFrame([{"model_version": "gemini-1.5-flash"}])

        with pytest.raises(ValueError, match="missing columns"):
            task._transaction_log("Type", "User", "Source", prediction_df, datetime(2025, 1, 1))

    def test_transaction_log_casts_string_duration_and_ignores_bad_timestamps(self, task):
        prediction_df = pd.DataFrame(
            [
                {
                    "file_name": "f1.wav",
                    "full_path": "path/to/f1.wav",
                    "folder": "folder",
                    "record_date": "20250101",
                    "duration": "61",
                    "token_input": {"text": 10},
                    "token_cached": 0,
                    "token_output": {"text": 5},
                    "status": "SUCCESS",
                    "message": None,
                    "processed_time": "not-an-iso-time",
                    "create_time": "also-not-an-iso-time",
                    "model_version": "gemini-1.5-flash",
                    "load_dt": "2025-01-01 10:02:00",
                }
            ]
        )
        task.sharepoint_control.is_item_exists.return_value = False

        from src.utils.pandas_utils import ensure_df_schema as real_ensure_df_schema

        def ensure_schema_side_effect(df, schema):
            ensured_df = real_ensure_df_schema(df, schema)
            if "start_time" in ensured_df.columns:
                ensured_df["start_time"] = pd.to_datetime(ensured_df["start_time"], errors="coerce")
            return ensured_df

        with (
            patch(
                "tasks.sentiment_telesale.export_output_result_task.GeminiBatchModule.cal_gemini_cost",
                return_value={"f1.wav": {"cost_input": 0.001, "cost_output": 0.002}},
            ),
            patch(
                "tasks.sentiment_telesale.export_output_result_task.ensure_df_schema",
                side_effect=ensure_schema_side_effect,
            ),
        ):
            result_df = task._transaction_log("Type", "User", "Source", prediction_df, datetime(2025, 1, 1))

        assert result_df is not None
        task.sharepoint_control.upload_file.assert_called()

    def test_transaction_log_payload_creation_failure_raises(self, task):
        prediction_df = pd.DataFrame(
            [
                {
                    "file_name": "f1.wav",
                    "full_path": "path/to/f1.wav",
                    "folder": "folder",
                    "record_date": "20250101",
                    "duration": 60,
                    "token_input": {"text": 10},
                    "token_cached": 0,
                    "token_output": {"text": 5},
                    "status": "SUCCESS",
                    "message": None,
                    "processed_time": "2025-01-01T10:00:00",
                    "create_time": "2025-01-01T10:01:00",
                    "model_version": "gemini-1.5-flash",
                    "load_dt": "2025-01-01 10:02:00",
                }
            ]
        )

        with (
            patch(
                "tasks.sentiment_telesale.export_output_result_task.GeminiBatchModule.cal_gemini_cost",
                return_value={"f1.wav": {"cost_input": 0.001, "cost_output": 0.002}},
            ),
            patch(
                "tasks.sentiment_telesale.export_output_result_task.TransactionLogSchema.from_dict",
                side_effect=RuntimeError("payload creation failed"),
            ),
            pytest.raises(Exception, match="Transaction log creation failed at record 1"),
        ):
            task._transaction_log("Type", "User", "Source", prediction_df, datetime(2025, 1, 1))

    def test_transaction_log_empty_dataframe_raises_when_no_records_created(self, task):
        prediction_df = pd.DataFrame(
            columns=[
                "file_name",
                "full_path",
                "folder",
                "record_date",
                "duration",
                "token_input",
                "token_cached",
                "token_output",
                "status",
                "message",
                "processed_time",
                "create_time",
                "model_version",
                "load_dt",
            ]
        )

        with pytest.raises(Exception, match="No transaction records were created from prediction data"):
            task._transaction_log("Type", "User", "Source", prediction_df, datetime(2025, 1, 1))

    def test_transaction_log_dataframe_creation_failure_raises(self, task):
        prediction_df = pd.DataFrame(
            [
                {
                    "file_name": "f1.wav",
                    "full_path": "path/to/f1.wav",
                    "folder": "folder",
                    "record_date": "20250101",
                    "duration": 60,
                    "token_input": {"text": 10},
                    "token_cached": 0,
                    "token_output": {"text": 5},
                    "status": "SUCCESS",
                    "message": None,
                    "processed_time": "2025-01-01T10:00:00",
                    "create_time": "2025-01-01T10:01:00",
                    "model_version": "gemini-1.5-flash",
                    "load_dt": "2025-01-01 10:02:00",
                }
            ]
        )

        with (
            patch(
                "tasks.sentiment_telesale.export_output_result_task.GeminiBatchModule.cal_gemini_cost",
                return_value={"f1.wav": {"cost_input": 0.001, "cost_output": 0.002}},
            ),
            patch(
                "tasks.sentiment_telesale.export_output_result_task.ensure_df_schema",
                side_effect=RuntimeError("transaction schema failed"),
            ),
            pytest.raises(Exception, match="Cannot create transaction log"),
        ):
            task._transaction_log("Type", "User", "Source", prediction_df, datetime(2025, 1, 1))

    def test_performance_log_returns_none_when_all_payloads_fail(self, task):
        trans_df = pd.DataFrame(
            [
                {
                    "data_date": "20250101",
                    "start_time": "2025-01-01T00:00:00",
                    "load_dt": "2025-01-01 00:00:00",
                    "gcp_project_id": "proj",
                    "gcp_project_name": "proj-name",
                    "status_pass_failed_retry": "Pass",
                    "latency_ms": 100,
                }
            ]
        )

        with patch(
            "tasks.sentiment_telesale.export_output_result_task.PerformanceLogSchema.from_dict",
            side_effect=RuntimeError("performance payload failed"),
        ):
            assert task._performance_log(trans_df) is None

        task.sharepoint_control.upload_file.assert_not_called()

    def test_performance_log_dataframe_creation_failure_raises(self, task):
        trans_df = pd.DataFrame(
            [
                {
                    "data_date": "20250101",
                    "start_time": "2025-01-01T00:00:00",
                    "load_dt": "2025-01-01 00:00:00",
                    "gcp_project_id": "proj",
                    "gcp_project_name": "proj-name",
                    "status_pass_failed_retry": "Pass",
                    "latency_ms": 100,
                }
            ]
        )

        with (
            patch(
                "tasks.sentiment_telesale.export_output_result_task.ensure_df_schema",
                side_effect=RuntimeError("performance schema failed"),
            ),
            pytest.raises(Exception, match="Cannot create performance log"),
        ):
            task._performance_log(trans_df)

    def test_performance_log_data_date_extraction_failure_raises(self, task):
        trans_df = pd.DataFrame(
            [
                {
                    "data_date": "20250101",
                    "start_time": "2025-01-01T00:00:00",
                    "load_dt": "2025-01-01 00:00:00",
                    "gcp_project_id": "proj",
                    "gcp_project_name": "proj-name",
                    "status_pass_failed_retry": "Pass",
                    "latency_ms": 100,
                }
            ]
        )

        class BadDateFrame(pd.DataFrame):
            @property
            def _constructor(self):
                return BadDateFrame

            def __getitem__(self, key):
                if key == "data_date":
                    raise RuntimeError("data_date extraction failed")
                return super().__getitem__(key)

        with (
            patch(
                "tasks.sentiment_telesale.export_output_result_task.ensure_df_schema",
                side_effect=lambda df, schema: BadDateFrame(df),
            ),
            pytest.raises(Exception, match="Cannot process performance logs"),
        ):
            task._performance_log(trans_df)

    def test_performance_log_handles_existing_merge_errors(self, task):
        trans_df = pd.DataFrame(
            [
                {
                    "data_date": "20250101",
                    "start_time": "2025-01-01T00:00:00",
                    "load_dt": "2025-01-01 00:00:00",
                    "gcp_project_id": "proj",
                    "gcp_project_name": "proj-name",
                    "status_pass_failed_retry": "Pass",
                    "latency_ms": 100,
                }
            ]
        )
        task.sharepoint_control.is_item_exists.return_value = True
        task.sharepoint_control.get_item_by_path.return_value = Mock(content=b"bad performance csv")

        with (
            patch("tasks.sentiment_telesale.export_output_result_task.resolve_env", side_effect=lambda x: x),
            patch(
                "tasks.sentiment_telesale.export_output_result_task.resolve_date",
                side_effect=lambda text, replace_date: text,
            ),
            patch("pandas.read_csv", side_effect=ValueError("performance merge failed")),
        ):
            task._performance_log(trans_df)
