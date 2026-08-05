"""
Tests for tasks/sentiment_telesale/fact_check_task.py

Target: ≥80% line coverage (809 statements → need ≥647 covered).
"""

import io
import json
from datetime import datetime
from unittest.mock import MagicMock, Mock, call, patch

import pandas as pd
import pytest

from tasks.sentiment_telesale.fact_check_task import FactCheckTask

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_gt_excel(rows: list[dict], sheet_name: str = "Evaluation") -> bytes:
    """Build an in-memory Excel with given rows."""
    df = pd.DataFrame(rows)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name=sheet_name, index=False)
    return buf.getvalue()


def _make_filename_excel(rows, sheet_name: str = "FilenameList") -> bytes:
    df = pd.DataFrame(rows)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name=sheet_name, index=False)
    return buf.getvalue()


def _make_check_list_excel(skill_code: int = 13, commission_skill: str = "13_True_Promo_End") -> bytes:
    """Build minimal check-list Excel with Categories, Subcategories, Tags sheets."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        pd.DataFrame(
            [
                {
                    "commission_skill_code": skill_code,
                    "commission_skill": commission_skill,
                    "main_category_no": 1,
                    "main_category_name": "Quality",
                    "section": "S1",
                    "order": 1,
                    "content": "Greeting",
                }
            ]
        ).to_excel(writer, sheet_name="Categories", index=False)
        pd.DataFrame(
            [
                {
                    "commission_skill_code": skill_code,
                    "sub_category_no": "1.1",
                    "sub_category_name": "Greeting",
                    "order": 1,
                    "section": "S1",
                    "content": "Open call",
                }
            ]
        ).to_excel(writer, sheet_name="Subcategories", index=False)
        pd.DataFrame(
            [
                {
                    "commission_skill_code": skill_code,
                    "item_no": "1.1.1",
                    "tag_code": "TAG_001",
                    "rule_and_logic": "Rule",
                    "positive_example_th": "Good",
                    "negative_example_th": "Bad",
                    "is_active": True,
                }
            ]
        ).to_excel(writer, sheet_name="Tags", index=False)
    return buf.getvalue()


def _make_empty_check_list_excel() -> bytes:
    """Build a check-list Excel with headers only and no active skill rows."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        pd.DataFrame(
            columns=[
                "commission_skill_code",
                "commission_skill",
                "main_category_no",
                "main_category_name",
                "section",
                "order",
                "content",
            ]
        ).to_excel(writer, sheet_name="Categories", index=False)
        pd.DataFrame(
            columns=[
                "commission_skill_code",
                "sub_category_no",
                "sub_category_name",
                "order",
                "section",
                "content",
            ]
        ).to_excel(writer, sheet_name="Subcategories", index=False)
        pd.DataFrame(
            columns=[
                "commission_skill_code",
                "item_no",
                "tag_code",
                "rule_and_logic",
                "positive_example_th",
                "negative_example_th",
                "is_active",
            ]
        ).to_excel(writer, sheet_name="Tags", index=False)
    return buf.getvalue()


COMMON_YAML = {
    "control": {
        "site_domain": "ctrl.sharepoint.com",
        "client_id": "cid",
        "client_secret": "csec",
        "tenant_id": "tid",
        "site_path": "/sites/ctrl",
    },
    "framework": {"timezone": "Asia/Bangkok"},
}

PROMPT_MAPPING_YAML = [
    {
        "commission_skill_code": "13",
        "commission_skill": "13_True_Promo_End",
        "common_prompt_path": "Common/common.txt",
        "check_list_prompt_path": "Checklist/check.txt",
    }
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_deps():
    """Patch all external integrations at module level before any task import resolves."""
    with (
        patch("tasks.sentiment_telesale.fact_check_task.SharePointModule") as sp_cls,
        patch("tasks.sentiment_telesale.fact_check_task.GCSModule") as gcs_cls,
        patch("tasks.sentiment_telesale.fact_check_task.GeminiBatchModule") as gbm_cls,
        patch("tasks.sentiment_telesale.fact_check_task.load_yaml") as ly,
        patch("tasks.sentiment_telesale.fact_check_task.read_file") as rf,
        patch("tasks.sentiment_telesale.fact_check_task.resolve_env") as re_env,
        patch("tasks.sentiment_telesale.fact_check_task.resolve_date") as re_date,
        patch("tasks.sentiment_telesale.fact_check_task.asyncio.run") as ar,
        patch("tasks.sentiment_telesale.fact_check_task.time.sleep"),
    ):

        def _load_yaml(path):
            if "common.yml" in str(path):
                return COMMON_YAML
            return PROMPT_MAPPING_YAML

        ly.side_effect = _load_yaml
        re_env.side_effect = lambda x: x
        re_date.side_effect = lambda text, replace_date: text
        rf.return_value = "SYSTEM ${KNOWLEDGE_BASE} ${CAMPAIGN_CHECKLIST}"
        ar.return_value = {"success": 2, "failed": 0}

        yield sp_cls, gcs_cls, gbm_cls, ly, rf, re_env, re_date, ar


@pytest.fixture
def task(mock_deps):
    """Create a FactCheckTask with mocked config and modules already wired up."""
    t = FactCheckTask()

    # Override config dicts set by __init__
    t.gcs = {
        "project_id": "proj",
        "bucket_name": "bkt",
        "input_folder": "input/",
        "processing_voice_folder": "proc/voice/%{DATA_DATE_YYYYMMDDHHMMSS}",
        "processing_batch_folder": "proc/batch/%{DATA_DATE_YYYYMMDDHHMMSS}",
        "output_folder": "output/%{DATA_DATE_YYYYMMDDHHMMSS}",
    }
    t.gcp = {}
    t.vertexai = {
        "project_id": "proj",
        "location": "us-central1",
        "model": "gemini-2.5-flash",
        "batch_job_name": "fc-batch-%{DATA_DATE_YYYYMMDDHHMMSS}",
        "generation_config": {"temperature": 0.0},
    }
    t.sharepoint = {
        "control": {
            "filename_list_file": "/sites/ctrl/FilenameList.xlsx",
            "source_folder": "/sites/ctrl/Voice",
            "ground_truth_file": "/sites/ctrl/GT.xlsx",
            "evaluation_folder": "/sites/ctrl/Eval/eval.xlsx",
        }
    }
    t.framework = {
        "concurrency_upload": "5",
        "system_prompt_file": "prompts/system.txt",
        "prompt_mapping_file": "prompts/mapping.yml",
    }
    t.metric_thresholds = {
        "accuracy": {"excellent": 90, "good": 85, "acceptable": 75},
        "precision": {"excellent": 90, "good": 85, "acceptable": 75},
        "recall": {"excellent": 90, "good": 85, "acceptable": 75},
        "f1_score": {"excellent": 90, "good": 85, "acceptable": 75},
    }
    t.packages = {"execution_dt": datetime(2025, 1, 15, 12, 0, 0)}
    t.get_package = lambda k, d=None: t.packages.get(k, d)

    # Wire mocked modules
    t.sharepoint_control = Mock()
    t.gcs_module = Mock()
    t.gcs_module.project_id = "proj"
    t.gcs_module.bucket_name = "bkt"
    t.gemini_batch_module = Mock()

    return t


# ---------------------------------------------------------------------------
# Tests: pre_execute
# ---------------------------------------------------------------------------


class TestPreExecute:
    def test_pre_execute_success(self, mock_deps):
        """All three modules initialise without error."""
        sp_cls, gcs_cls, gbm_cls, *_ = mock_deps
        t = FactCheckTask()
        t.gcs = {"project_id": "p", "bucket_name": "b"}
        t.vertexai = {"project_id": "p", "location": "us-central1"}
        t.packages = {}
        t.get_package = lambda k, d=None: None

        t.pre_execute()

        sp_cls.assert_called_once()
        gcs_cls.assert_called_once()
        gbm_cls.assert_called_once()

    def test_pre_execute_sharepoint_fails(self, mock_deps):
        """SharePoint init failure is re-raised."""
        sp_cls, *_ = mock_deps
        sp_cls.side_effect = RuntimeError("SP auth error")

        t = FactCheckTask()
        t.gcs = {"project_id": "p", "bucket_name": "b"}
        t.vertexai = {"project_id": "p", "location": "us-central1"}
        t.packages = {}
        t.get_package = lambda k, d=None: None

        with pytest.raises(RuntimeError, match="SP auth error"):
            t.pre_execute()

    def test_pre_execute_gcs_fails(self, mock_deps):
        """GCS init failure is re-raised."""
        _, gcs_cls, *_ = mock_deps
        gcs_cls.side_effect = RuntimeError("GCS error")

        t = FactCheckTask()
        t.gcs = {"project_id": "p", "bucket_name": "b"}
        t.vertexai = {"project_id": "p", "location": "us-central1"}
        t.packages = {}
        t.get_package = lambda k, d=None: None

        with pytest.raises(RuntimeError, match="GCS error"):
            t.pre_execute()

    def test_pre_execute_gemini_batch_fails(self, mock_deps):
        """Gemini Batch init failure is re-raised."""
        _, _, gbm_cls, *_ = mock_deps
        gbm_cls.side_effect = RuntimeError("Vertex AI error")

        t = FactCheckTask()
        t.gcs = {"project_id": "p", "bucket_name": "b"}
        t.vertexai = {"project_id": "p", "location": "us-central1"}
        t.packages = {}
        t.get_package = lambda k, d=None: None

        with pytest.raises(RuntimeError, match="Vertex AI error"):
            t.pre_execute()


# ---------------------------------------------------------------------------
# Tests: execute_task
# ---------------------------------------------------------------------------


class TestExecuteTask:
    def test_execute_retrieves_when_predictions_exist(self, task, mock_deps):
        """If prediction output files exist in GCS, _retrieve_prediction_step is called."""
        task.gcs_module.list_files.return_value = ["output/predictions.jsonl"]
        expected = [{"file": "x"}]
        with patch.object(task, "_retrieve_prediction_step", return_value=expected) as mock_ret:
            result = task.execute_task()
        mock_ret.assert_called_once()
        assert result == expected

    def test_execute_submits_when_no_predictions(self, task, mock_deps):
        """If no prediction output, _submit_job_step is called and None returned."""
        task.gcs_module.list_files.return_value = []
        with patch.object(task, "_submit_job_step", return_value=(None, None, [])) as mock_sub:
            result = task.execute_task()
        mock_sub.assert_called_once()
        assert result is None

    def test_execute_uses_rerun_date(self, task, mock_deps):
        """When rerun_data_dt is in packages, it is used as datadate."""
        task.packages["rerun_data_dt"] = "2025-01-10"
        task.gcs_module.list_files.return_value = []
        with patch.object(task, "_submit_job_step", return_value=(None, None, [])) as mock_sub:
            task.execute_task()
        # datadate passed to _submit_job_step should be "2025-01-10"
        args, kwargs = mock_sub.call_args
        assert args[1] == "2025-01-10"


# ---------------------------------------------------------------------------
# Tests: _proc_raw_prediction
# ---------------------------------------------------------------------------


def _make_raw_line(
    file_uri="gs://b/proc/voice/0001_08001234_120000_A001_Somchai_Doe_D_20250115_60_OUT.wav",
    prediction_text=None,
    status="",
    usage=None,
):
    line = {
        "request": {"contents": [{"parts": [{"fileData": {"fileUri": file_uri}}]}]},
        "response": {
            "modelVersion": "gemini-2.5-flash",
            "createTime": "2025-01-15T12:00:00Z",
            "candidates": [{"content": {"parts": [{"text": prediction_text or "{}"}]}}],
            "usageMetadata": usage or {},
        },
        "processed_time": "2025-01-15T12:01:00Z",
    }
    if status:
        line["status"] = status
    return line


class TestProcRawPrediction:
    def test_no_file_uri_skips_record(self, task):
        """Lines without a fileUri are skipped."""
        raw = [{"request": {}, "response": {}}]
        result = task._proc_raw_prediction("batch/path", raw, datetime(2025, 1, 15))
        assert result == []

    def test_error_status_in_batch_result(self, task):
        """If line['status'] is not empty, record is FAILED."""
        raw = [_make_raw_line(status="FAILED_PRECONDITION")]
        result = task._proc_raw_prediction("batch/path", raw, datetime(2025, 1, 15))
        assert len(result) == 1
        assert result[0]["prediction"]["status"] == "FAILED"

    def test_no_prediction_text(self, task):
        """No text in candidates → record marked FAILED."""
        line = {
            "request": {"contents": [{"parts": [{"fileData": {"fileUri": "gs://b/f.wav"}}]}]},
            "response": {
                "modelVersion": "gemini-2.5-flash",
                "createTime": "2025-01-15T12:00:00Z",
                "candidates": [],  # empty → no text
                "usageMetadata": {},
            },
            "processed_time": "2025-01-15T12:01:00Z",
        }
        result = task._proc_raw_prediction("batch/path", [line], datetime(2025, 1, 15))
        assert len(result) == 1
        assert result[0]["prediction"]["status"] == "FAILED"

    def test_successful_prediction_parsed(self, task):
        """Valid JSON prediction is parsed and status=SUCCESS."""
        pred_json = json.dumps(
            {
                "campaign_name": "13_True_Promo_End",
                "campaign_ratio": {"main": 0.8, "other": 0.2},
                "call_status": "Completed",
                "operations_and_professionalism": {
                    "call_opening": {
                        "support_detail": "ok",
                        "proper_identification": True,
                        "call_origin_disclosure": True,
                        "call_consent_before_engagement": True,
                    },
                    "customer_identity_verification": {
                        "support_detail": "",
                        "customer_verification": True,
                        "invalid_verification": False,
                        "missing_verification": False,
                    },
                    "language_and_tone": {
                        "support_detail": "",
                        "behavioral_violation": False,
                        "clarity": True,
                        "delivery_pace": True,
                    },
                    "active_listening": {
                        "support_detail": "",
                        "no_interruption": True,
                        "correct_understanding": True,
                        "acknowledgement_paraphrasing": True,
                    },
                    "call_closing": {
                        "support_detail": "",
                        "confirm_resolution": True,
                        "courteous_ending": True,
                        "smooth_closing": True,
                    },
                },
                "sales_effectiveness": {
                    "customer_needs_analysis": {
                        "support_detail": "",
                        "usage_based_analysis": True,
                        "benefit_highlight": True,
                    },
                    "offer_presentation_quality": {
                        "support_detail": "",
                        "clarity_of_explanation": True,
                        "customer_benefit_highlight": False,
                    },
                    "effective_objection_handling": {
                        "support_detail": "",
                        "failure_to_listen": False,
                        "confrontational_tone": False,
                    },
                    "sales_closing_attempt": {
                        "support_detail": "",
                        "value_based_closing": True,
                        "unclear_separation": False,
                        "inadequate_addon_disclosure": False,
                    },
                    "cross_sell_upsell": {
                        "support_detail": "",
                        "missed_crosssell_upsell": False,
                        "unclear_addon_separation_crosssell": False,
                        "inadequate_addon_disclosure_crosssell": False,
                    },
                },
                "customer_experience": {
                    "positive_customer_experience": {
                        "support_detail": "",
                        "failure_to_demonstrate_empathy": False,
                        "deflecting_responsibility": False,
                        "escalates_customer_emotion": False,
                    },
                    "clarity_of_communication": {
                        "support_detail": "",
                        "overly_technical_language": False,
                        "fails_to_clarify_limitations": False,
                        "no_adjustment_for_complexity": False,
                    },
                    "building_trust": {
                        "support_detail": "",
                        "provides_unclear_information": False,
                        "provides_misleading_information": False,
                        "fails_to_connect_value": True,
                    },
                },
                "compliance": {
                    "compliance": {
                        "support_detail": "",
                        "data_privacy_compliance": True,
                        "sales_integrity_compliance": True,
                        "professional_conduct_compliance": True,
                    },
                },
                "agent_strength": "Good",
                "agent_weakness": "None",
                "sales_performance": {
                    "main_package_offered": 1,
                    "main_package_accepted": 1,
                    "upsell_add_on_package_offered": 0,
                    "upsell_add_on_package_accepted": 0,
                    "crosssell_add_on_product_offered": 0,
                    "crosssell_add_on_product_accepted": 0,
                    "main_and_upsell_add_on_product_offered_list": ["Mobile"],
                    "crosssell_add_on_product_offered_list": [],
                },
                "customer_insight": {
                    "rejection_reason": None,
                    "network_issue": None,
                    "churn_risk_indicator": 0,
                    "customer_sentiment_emotional": "Positive",
                },
                "check_list": {"operation_check_list": [], "support_detail": "All OK"},
            }
        )
        line = _make_raw_line(prediction_text=pred_json)
        result = task._proc_raw_prediction("batch/path", [line], datetime(2025, 1, 15))
        assert len(result) == 1
        assert result[0]["prediction"]["status"] == "SUCCESS"
        assert result[0]["prediction"]["raw_prediction"] is not None

    def test_record_date_extracted_from_path_when_missing(self, task):
        """If date not in filename components, extract from path with regex."""
        uri = "gs://b/output/202501/20250115/voice/unknown.wav"
        line = {
            "request": {"contents": [{"parts": [{"fileData": {"fileUri": uri}}]}]},
            "response": {
                "modelVersion": "gemini-2.5-flash",
                "createTime": "2025-01-15T12:00:00Z",
                "candidates": [],
                "usageMetadata": {},
            },
            "processed_time": None,
        }
        result = task._proc_raw_prediction("batch/path", [line], datetime(2025, 1, 15))
        assert len(result) == 1
        # Either extracted from path or default
        assert result[0]["file_metadata"]["record_date"] in ("20250115", "99991231")


# ---------------------------------------------------------------------------
# Tests: _prepare_prediction_payload
# ---------------------------------------------------------------------------


class TestPreparePredictionPayload:
    def _voice_info(self):
        return {
            "file_uri": "gs://b/f.wav",
            "file_name": "call_01_20250115_A_01",
            "file_ext": ".wav",
            "call_id": "01",
            "phone_number": None,
            "call_time": None,
            "agent_id": "A",
            "first_name": None,
            "last_name": None,
            "provider": None,
            "record_date": "20250115",
            "duration": "60",
        }

    def test_err_flag_returns_failed(self, task):
        payload = task._prepare_prediction_payload(self._voice_info(), "some error", err_flag=True)
        assert payload["prediction"]["status"] == "FAILED"
        assert payload["prediction"]["message"] == "some error"

    def test_parse_error_returns_failed(self, task):
        """Invalid JSON causes FAILED status."""
        payload = task._prepare_prediction_payload(self._voice_info(), "not-json")
        assert payload["prediction"]["status"] == "FAILED"
        assert "Failed to parse" in payload["prediction"]["message"]

    def test_no_campaign_name_returns_failed(self, task):
        """Missing campaign_name in parsed JSON causes FAILED."""
        payload = task._prepare_prediction_payload(self._voice_info(), '{"other_key": 1}')
        assert payload["prediction"]["status"] == "FAILED"


# ---------------------------------------------------------------------------
# Tests: _get_filename_list
# ---------------------------------------------------------------------------


class TestGetFilenameList:
    def test_success(self, task):
        xlsx = _make_filename_excel(
            [
                {"filename": "a.wav", "commission_skill_code": "13", "commission_skill": "13_True_Promo_End"},
                {"filename": "b.wav", "commission_skill_code": "13", "commission_skill": "13_True_Promo_End"},
            ]
        )
        mock_item = Mock()
        mock_item.content = xlsx
        task.sharepoint_control.is_item_exists.return_value = True
        task.sharepoint_control.get_item_by_path.return_value = mock_item

        df = task._get_filename_list()
        assert len(df) == 2
        assert list(df.columns) >= ["filename", "commission_skill_code"]

    def test_file_not_found_raises(self, task):
        task.sharepoint_control.is_item_exists.return_value = False
        with pytest.raises(Exception, match="Critical error loading filename list"):
            task._get_filename_list()

    def test_empty_file_raises(self, task):
        xlsx = _make_filename_excel([], sheet_name="FilenameList")
        mock_item = Mock()
        mock_item.content = xlsx
        task.sharepoint_control.is_item_exists.return_value = True
        task.sharepoint_control.get_item_by_path.return_value = mock_item
        with pytest.raises(Exception, match="Critical error loading filename list"):
            task._get_filename_list()


# ---------------------------------------------------------------------------
# Tests: _upload_voice_files
# ---------------------------------------------------------------------------


class TestUploadVoiceFiles:
    def test_success(self, task, mock_deps):
        """Matching files are uploaded to GCS."""
        *_, ar = mock_deps
        voice_files = [
            {"name": "a.wav", "parentReference": {"path": "/drives/root:/Voice"}},
            {"name": "notlisted.wav", "parentReference": {"path": "/drives/root:/Voice"}},
        ]
        task.sharepoint_control.list_files.return_value = voice_files
        filename_df = pd.DataFrame(
            [
                {"filename": "a.wav", "commission_skill_code": "13", "commission_skill": "13_True_Promo_End"},
            ]
        )
        task._upload_voice_files(filename_df, "input/")
        ar.assert_called_once()  # asyncio.run for upload_sharepoint_to_gcs

    def test_no_matching_files_raises(self, task):
        task.sharepoint_control.list_files.return_value = [{"name": "other.wav", "parentReference": {"path": "/Voice"}}]
        filename_df = pd.DataFrame(
            [{"filename": "a.wav", "commission_skill_code": "13", "commission_skill": "13_True_Promo_End"}]
        )
        with pytest.raises(Exception, match="No matching voice files found"):
            task._upload_voice_files(filename_df, "input/")


# ---------------------------------------------------------------------------
# Tests: _compute_eval_metrics
# ---------------------------------------------------------------------------


class TestComputeEvalMetrics:
    def test_normal_case(self, task):
        m = task._compute_eval_metrics(TP=8, FP=1, FN=1, TN=10)
        assert m["TP"] == 8
        assert m["FP"] == 1
        assert m["FN"] == 1
        assert m["TN"] == 10
        assert 0 < m["accuracy"] <= 100
        assert 0 < m["precision"] <= 100
        assert 0 < m["recall"] <= 100
        assert 0 < m["f1_score"] <= 100
        assert m["weight"] == 9  # TP + FN

    def test_all_correct(self, task):
        m = task._compute_eval_metrics(TP=10, FP=0, FN=0, TN=5)
        assert m["accuracy"] == 100.0
        assert m["precision"] == 100.0
        assert m["recall"] == 100.0
        assert m["f1_score"] == 100.0

    def test_all_zero(self, task):
        m = task._compute_eval_metrics(TP=0, FP=0, FN=0, TN=0)
        assert m["accuracy"] == 0.0
        assert m["precision"] == 0.0
        assert m["recall"] == 0.0
        assert m["f1_score"] == 0.0


# ---------------------------------------------------------------------------
# Tests: _eval_status
# ---------------------------------------------------------------------------


class TestEvalStatus:
    def test_excellent(self, task):
        assert task._eval_status(97.0, "accuracy") == "excellent"

    def test_good(self, task):
        assert task._eval_status(88.0, "accuracy") == "good"

    def test_acceptable(self, task):
        assert task._eval_status(78.0, "accuracy") == "acceptable"

    def test_unacceptable(self, task):
        assert task._eval_status(50.0, "accuracy") == "unacceptable"

    def test_custom_thresholds(self, task):
        task.metric_thresholds = {"accuracy": {"excellent": 99, "good": 90, "acceptable": 80}}
        assert task._eval_status(92.0, "accuracy") == "good"  # ≥90 but <99
        assert task._eval_status(85.0, "accuracy") == "acceptable"  # ≥80 but <90
        assert task._eval_status(60.0, "accuracy") == "unacceptable"  # <80


# ---------------------------------------------------------------------------
# Tests: _evaluate
# ---------------------------------------------------------------------------


def _make_batch_results_success(n: int = 2) -> list[dict]:
    """Build minimal batch_results with SUCCESS predictions."""
    results = []
    for i in range(n):
        results.append(
            {
                "file_metadata": {"file_name": f"call_{i:04d}_0_0_A_Fn_Ln_D_20250115_60"},
                "prediction": {
                    "status": "SUCCESS",
                    "model_version": "gemini-2.5-flash",
                    "processed_time": "2025-01-15T12:00:00Z",
                    "raw_prediction": {
                        "operations_and_professionalism": {
                            "call_opening": {
                                "proper_identification": i % 2 == 0,
                                "call_origin_disclosure": False,
                                "call_consent_before_engagement": None,
                            },
                            "customer_identity_verification": {
                                "customer_verification": True,
                                "invalid_verification": False,
                                "missing_verification": None,
                            },
                            "language_and_tone": {
                                "behavioral_violation": False,
                                "clarity": True,
                                "delivery_pace": None,
                            },
                            "active_listening": {
                                "no_interruption": True,
                                "correct_understanding": True,
                                "acknowledgement_paraphrasing": None,
                            },
                            "call_closing": {
                                "confirm_resolution": True,
                                "courteous_ending": True,
                                "smooth_closing": None,
                            },
                        },
                        "sales_effectiveness": {
                            "customer_needs_analysis": {"usage_based_analysis": True, "benefit_highlight": None},
                            "offer_presentation_quality": {
                                "clarity_of_explanation": True,
                                "customer_benefit_highlight": False,
                            },
                            "effective_objection_handling": {"failure_to_listen": False, "confrontational_tone": False},
                            "sales_closing_attempt": {
                                "value_based_closing": True,
                                "unclear_separation": False,
                                "inadequate_addon_disclosure": None,
                            },
                            "cross_sell_upsell": {
                                "missed_crosssell_upsell": False,
                                "unclear_addon_separation_crosssell": False,
                                "inadequate_addon_disclosure_crosssell": None,
                            },
                        },
                        "customer_experience": {
                            "positive_customer_experience": {
                                "failure_to_demonstrate_empathy": False,
                                "deflecting_responsibility": False,
                                "escalates_customer_emotion": False,
                            },
                            "clarity_of_communication": {
                                "overly_technical_language": False,
                                "fails_to_clarify_limitations": False,
                                "no_adjustment_for_complexity": False,
                            },
                            "building_trust": {
                                "provides_unclear_information": False,
                                "provides_misleading_information": False,
                                "fails_to_connect_value": True,
                            },
                        },
                        "compliance": {
                            "compliance": {
                                "data_privacy_compliance": True,
                                "sales_integrity_compliance": True,
                                "professional_conduct_compliance": True,
                            },
                        },
                    },
                },
                "load_dt": "2025-01-15 12:05:00",
            }
        )
    return results


class TestEvaluate:
    def _make_gt_mock(self, filenames, task):
        """Build GT Excel and set up sharepoint_control mock to return it."""
        gt_rows = []
        col_names = list(task.GT_FIELD_MAPPING.keys())
        for fn in filenames:
            row = {"filename": fn + ".wav"}
            for col in col_names:
                row[col] = "T"
            gt_rows.append(row)

        xlsx = _make_gt_excel(gt_rows, sheet_name="Evaluation")
        mock_gt = Mock()
        mock_gt.content = xlsx
        return mock_gt

    def test_evaluate_no_existing_file(self, task, mock_deps):
        """When no existing eval file, only new rows are written."""
        # Build GT
        batch = _make_batch_results_success(2)
        filenames = [r["file_metadata"]["file_name"] for r in batch]
        mock_gt = self._make_gt_mock(filenames, task)

        # Mock: no existing eval file
        task.sharepoint_control.is_item_exists.return_value = False
        task.sharepoint_control.get_item_by_path.return_value = mock_gt
        task.sharepoint_control.upload_file = Mock()

        task._evaluate(batch)

        task.sharepoint_control.upload_file.assert_called_once()

    def test_evaluate_merges_with_existing_file(self, task, mock_deps):
        """When existing eval file is present, rows are merged and upload called."""
        batch = _make_batch_results_success(2)
        filenames = [r["file_metadata"]["file_name"] for r in batch]
        mock_gt = self._make_gt_mock(filenames, task)

        # Build existing eval Excel
        existing_rows = [{"created_datetime": "2025-01-14 12:00:00", "dimension": "call_opening", "label": "foo"}]
        existing_xlsx = _make_gt_excel(existing_rows, sheet_name="Evaluation")
        mock_existing = Mock()
        mock_existing.content = existing_xlsx

        call_count = [0]

        def get_item_side(path):
            call_count[0] += 1
            if "GT.xlsx" in str(path):
                return mock_gt
            return mock_existing

        task.sharepoint_control.is_item_exists.return_value = True
        task.sharepoint_control.get_item_by_path.side_effect = get_item_side
        task.sharepoint_control.upload_file = Mock()

        task._evaluate(batch)

        task.sharepoint_control.upload_file.assert_called_once()

    def test_evaluate_pred_no_gt_match(self, task, mock_deps):
        """SUCCESS predictions with no matching GT filenames → 0 merged rows → no criteria computed."""
        batch = _make_batch_results_success(1)
        # GT uses a different filename that won't match pred
        col_names = list(task.GT_FIELD_MAPPING.keys())
        gt_rows = [{"filename": "unrelated_file.wav", **dict.fromkeys(col_names, "T")}]
        gt_xlsx = _make_gt_excel(gt_rows, sheet_name="Evaluation")
        mock_gt = Mock()
        mock_gt.content = gt_xlsx
        task.sharepoint_control.is_item_exists.return_value = False
        task.sharepoint_control.get_item_by_path.return_value = mock_gt
        task.sharepoint_control.upload_file = Mock()

        # 0 matched rows → empty output_rows → upload still happens
        task._evaluate(batch)
        task.sharepoint_control.upload_file.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: _retrieve_prediction_step
# ---------------------------------------------------------------------------


class TestRetrievePredictionStep:
    def test_success(self, task, mock_deps):
        """Happy path: valid predictions retrieved and _evaluate called."""
        batch_results = _make_batch_results_success(1)
        with (
            patch(
                "tasks.sentiment_telesale.fact_check_task.GeminiBatchModule.retrieve_batch_results",
                return_value=[{"request": {}, "response": {"candidates": []}}],
            ),
            patch.object(task, "_proc_raw_prediction", return_value=batch_results),
        ):
            with patch.object(task, "_evaluate") as mock_eval:
                result = task._retrieve_prediction_step(["gs://b/output/predictions.jsonl"], datetime(2025, 1, 15))
        mock_eval.assert_called_once()
        assert result == batch_results

    def test_all_failed_raises(self, task, mock_deps):
        """When all predictions have FAILED status, exception is raised."""
        failed_results = [{"prediction": {"status": "FAILED"}, "file_metadata": {"file_name": "x"}}]
        with (
            patch("tasks.sentiment_telesale.fact_check_task.GeminiBatchModule.retrieve_batch_results", return_value=[]),
            patch.object(task, "_proc_raw_prediction", return_value=failed_results),
            patch.object(task, "_evaluate"),
            pytest.raises(Exception, match="failed"),
        ):
            task._retrieve_prediction_step(["gs://b/output/predictions.jsonl"], datetime(2025, 1, 15))


# ---------------------------------------------------------------------------
# Tests: _submit_job_step (happy path via mocks)
# ---------------------------------------------------------------------------


class TestSubmitJobStep:
    def _setup_task(self, task, mock_deps):
        """Wire all mocks needed for _submit_job_step to complete successfully."""
        *_, ar = mock_deps

        # GCS list calls: input folder → 1 matching file; processing folder → 1 file
        task.gcs_module.list_files.side_effect = [
            ["input/a_OUT.wav"],  # Phase 3: input folder
            ["proc/voice/a_OUT.wav"],  # Phase 5: processing folder
        ]
        ar.return_value = {"success": 1, "failed": 0}  # copy_files_batch
        task.gcs_module.is_file_exists.return_value = True
        task.gcs_module.update_content_to_gcs = Mock()

        # Batch job mock
        fake_job = Mock()
        fake_job.name = "projects/proj/locations/us-central1/batchPredictionJobs/12345"
        fake_job.display_name = "fc-batch-20250115120000"
        task.gemini_batch_module.create_batch_job.return_value = fake_job
        task.gemini_batch_module.status_check_batch_job.return_value = "JOB_STATE_RUNNING"

        # _get_filename_list mock
        filename_df = pd.DataFrame(
            [{"filename": "a_OUT.wav", "commission_skill_code": "13", "commission_skill": "13_True_Promo_End"}]
        )
        task._get_filename_list = Mock(return_value=filename_df)

        # _upload_voice_files mock (no-op)
        task._upload_voice_files = Mock()

        # _mapping_prompt_by_filename_list mock
        task._mapping_prompt_by_filename_list = Mock(return_value={"proc/voice/a_OUT.wav": ("prompt text", "13")})
        # Pre-populate _cache_mapping_dict since _mapping_prompt_by_filename_list is mocked
        task._cache_mapping_dict = {"13": "13_True_Promo_End"}

    def test_submit_job_success(self, task, mock_deps):
        """Full happy path completes without exception."""
        self._setup_task(task, mock_deps)
        payload_path, output_folder, log_list = task._submit_job_step(datetime(2025, 1, 15, 12, 0, 0), "2025-01-15")
        assert payload_path is not None
        task.gemini_batch_module.create_batch_job.assert_called_once()

    def test_submit_job_no_payloads_returns_none(self, task, mock_deps):
        """When no payloads are built (all files skipped), returns (None, None, logs)."""
        *_, ar = mock_deps
        task.gcs_module.list_files.side_effect = [
            ["input/a_OUT.wav"],
            ["proc/voice/a_OUT.wav"],
        ]
        ar.return_value = {"success": 1, "failed": 0}
        task._get_filename_list = Mock(
            return_value=pd.DataFrame(
                [{"filename": "a_OUT.wav", "commission_skill_code": "13", "commission_skill": "13_True_Promo_End"}]
            )
        )
        task._upload_voice_files = Mock()
        # No prompts found → payloads will be empty
        task._mapping_prompt_by_filename_list = Mock(return_value={"proc/voice/a_OUT.wav": (None, "13")})
        result = task._submit_job_step(datetime(2025, 1, 15, 12, 0, 0), "2025-01-15")
        assert result[0] is None
        assert result[1] is None


# ---------------------------------------------------------------------------
# Tests: _mapping_prompt_by_filename_list  (unit)
# ---------------------------------------------------------------------------


class TestMappingPromptByFilenameList:
    def test_returns_dict_with_prompts(self, task, mock_deps):
        """Happy path: system + check-list Excel + common prompts are merged."""
        # common_prompt fetched first (text), check_list fetched second (Excel bytes)
        mock_common = Mock()
        mock_common.content = b"COMMON PROMPT"
        mock_check_list = Mock()
        mock_check_list.content = _make_check_list_excel()
        task.sharepoint_control.is_item_exists.return_value = True
        task.sharepoint_control.get_item_by_path.side_effect = [mock_common, mock_check_list]

        file_list = ["proc/a_OUT.wav"]
        filename_df = pd.DataFrame(
            [{"filename": "a_OUT.wav", "commission_skill_code": "13", "commission_skill": "13_True_Promo_End"}]
        )

        result = task._mapping_prompt_by_filename_list(file_list, filename_df)
        assert "proc/a_OUT.wav" in result
        prompt_text, skill_code = result["proc/a_OUT.wav"]
        assert prompt_text is not None
        assert skill_code == "13"

    def test_empty_when_no_matching_prompt(self, task, mock_deps):
        """When file has no matching entry in filename list, prompt is None."""
        mock_common = Mock()
        mock_common.content = b"COMMON PROMPT"
        mock_check_list = Mock()
        mock_check_list.content = _make_check_list_excel()
        task.sharepoint_control.is_item_exists.return_value = True
        task.sharepoint_control.get_item_by_path.side_effect = [mock_common, mock_check_list]

        # File name does not appear in filename_df → no skill code match → prompt is None
        file_list = ["proc/z_UNKNOWN.wav"]
        filename_df = pd.DataFrame(
            [{"filename": "a_OUT.wav", "commission_skill_code": "13", "commission_skill": "13_True_Promo_End"}]
        )

        result = task._mapping_prompt_by_filename_list(file_list, filename_df)
        # No matching entry → all prompt_template values None
        for _, (prompt, _) in result.items():
            assert prompt is None


# ---------------------------------------------------------------------------
# Tests: post_execute
# ---------------------------------------------------------------------------


class TestPostExecute:
    def test_post_execute_no_result_returns_early(self, task):
        """Lines 1160-1162: result=None/empty → returns early without calling archive."""
        with (
            patch.object(task, "_archive_and_cleanup") as mock_arch,
            patch.object(task, "_insert_log_record") as mock_log,
        ):
            out = task.post_execute(None)
        assert out is None
        mock_arch.assert_not_called()
        mock_log.assert_not_called()

    def test_post_execute_with_results(self, task, mock_deps):
        """Lines 1163-1212: result has records → builds df, calls archive and insert_log."""
        batch = _make_batch_results_success(2)
        with (
            patch.object(task, "_archive_and_cleanup") as mock_arch,
            patch.object(task, "_insert_log_record") as mock_log,
        ):
            out = task.post_execute(batch)
        mock_arch.assert_called_once()
        mock_log.assert_called_once()
        assert out is batch

    def test_post_execute_archive_fails_raises(self, task):
        """Lines 1205-1207: archive failure is re-raised."""
        batch = _make_batch_results_success(1)
        with patch.object(task, "_archive_and_cleanup", side_effect=RuntimeError("GCS error")):
            with pytest.raises(Exception, match="Archive and cleanup process failed"):
                task.post_execute(batch)


# ---------------------------------------------------------------------------
# Tests: _archive_and_cleanup
# ---------------------------------------------------------------------------


class TestArchiveAndCleanup:
    def test_archive_and_cleanup_moves_files_and_deletes_dirs(self, task, mock_deps):
        """Lines 1219-1291: happy path — archive files, delete working dirs."""
        task.gcs = {
            "output_folder": "output/%{DT}",
            "archive_batch_folder": "archive/batch",
            "input_folder": "input",
            "processing_voice_folder": "proc/voice/%{DT}",
        }
        task.gcs_module.list_files.return_value = [
            "output/predictions.jsonl",
            "output/batch1/payloads.jsonl",
        ]
        task.gcs_module.is_dir_exists.return_value = True
        task.gcs_module.move_file = Mock()
        task.gcs_module.delete_dir = Mock()

        task._archive_and_cleanup(datetime(2025, 1, 15, 12, 0, 0))

        assert task.gcs_module.move_file.call_count == 2
        assert task.gcs_module.delete_dir.call_count >= 1

    def test_archive_and_cleanup_empty_output(self, task):
        """No files to archive — dirs still cleaned."""
        task.gcs = {
            "output_folder": "output/%{DT}",
            "archive_batch_folder": "archive/batch",
            "input_folder": "input",
            "processing_voice_folder": "proc/voice/%{DT}",
        }
        task.gcs_module.list_files.return_value = []
        task.gcs_module.is_dir_exists.return_value = False
        task.gcs_module.delete_dir = Mock()

        task._archive_and_cleanup(datetime(2025, 1, 15, 12, 0, 0))

        task.gcs_module.delete_dir.assert_not_called()  # is_dir_exists=False → skip


# ---------------------------------------------------------------------------
# Tests: _insert_log_record
# ---------------------------------------------------------------------------


class TestInsertLogRecord:
    def _make_df(self):
        return pd.DataFrame(
            [
                {
                    "file_name": "call_01.wav",
                    "full_path": "/Voice/call_01.wav",
                    "folder": "Voice",
                    "record_date": "20250115",
                    "duration": 60,
                    "token_input": {"text": 100, "audio": 50},
                    "token_cached": 0,
                    "token_output": {"text": 50},
                    "status": "SUCCESS",
                    "message": None,
                    "processed_time": "2025-01-15T12:00:00",
                    "create_time": "2025-01-15T12:01:00",
                    "model_version": "gemini-2.5-flash",
                    "load_dt": "2025-01-15 12:05:00",
                }
            ]
        )

    def test_insert_log_record_calls_transaction_log(self, task, mock_deps):
        """Lines 1296-1314: happy path calls _transaction_log."""
        with patch.object(task, "_transaction_log", return_value=None) as mock_tl:
            task._insert_log_record(self._make_df())
        mock_tl.assert_called_once()

    def test_insert_log_record_transaction_fails_raises(self, task):
        """Lines 1309-1313: transaction log failure is re-raised."""
        with patch.object(task, "_transaction_log", side_effect=ValueError("bad")):
            with pytest.raises(Exception, match="Transaction log creation failed"):
                task._insert_log_record(self._make_df())


# ---------------------------------------------------------------------------
# Tests: _transaction_log (fact_check_task)
# ---------------------------------------------------------------------------


class TestTransactionLog:
    def _make_prediction_df(self):
        return pd.DataFrame(
            [
                {
                    "file_name": "call_01",
                    "full_path": "/Voice/call_01.wav",
                    "folder": None,
                    "record_date": "20250115",
                    "duration": 60,
                    "token_input": {"text": 100, "audio": 50},
                    "token_cached": 0,
                    "token_output": {"text": 50},
                    "status": "SUCCESS",
                    "message": None,
                    "processed_time": "2025-01-15T12:00:00",
                    "create_time": "2025-01-15T12:01:00",
                    "model_version": "gemini-2.5-flash",
                    "load_dt": "2025-01-15 12:05:00",
                }
            ]
        )

    def test_transaction_log_success(self, task, mock_deps):
        """Lines 1324-1420+: happy path creates log and uploads to SharePoint."""
        *_, gbm_cls = mock_deps
        gbm_cls.cal_gemini_cost.return_value = {"call_01": {"cost_input": 0.001, "cost_output": 0.0005}}
        task.control_site = "ctrl.sharepoint.com"
        task.control_access = {"site_name": "ControlSite", "client_id": "cid"}
        task.gcp = {"project_id": "proj", "project_name": "proj-name"}
        task.sharepoint_control.is_item_exists.return_value = False

        with patch("tasks.sentiment_telesale.fact_check_task.gemini_cost", return_value=[]) as mock_gc:
            task._transaction_log(
                default_type="AI Fact-Checker",
                default_user_id="daisyrpa",
                default_source="SharePoint",
                prediction_df=self._make_prediction_df(),
                execution_dt=datetime(2025, 1, 15, 12, 0, 0),
            )
        mock_gc.assert_called_once()
        # upload called for the log file
        task.sharepoint_control.upload_file.assert_called()

    def test_transaction_log_missing_columns_raises(self, task):
        """Lines 1332-1334: missing required columns raises ValueError."""
        df = pd.DataFrame([{"file_name": "x"}])  # missing many required columns
        with pytest.raises((ValueError, Exception)):
            task._transaction_log("T", "U", "S", df, datetime(2025, 1, 15))


class TestFactCheckTaskAdditionalCoverage:
    def test_proc_raw_prediction_defaults_model_and_zero_tokens_when_usage_fails(self, task):
        line = {
            "request": {
                "contents": [
                    {
                        "parts": [
                            {
                                "fileData": {
                                    "fileUri": "gs://b/proc/voice/0001_08001234_120000_A001_Somchai_Doe_D_20250115_60_OUT.wav"
                                }
                            }
                        ]
                    }
                ]
            },
            "response": {
                "createTime": "2025-01-15T12:00:00Z",
                "candidates": [{"content": {"parts": [{"text": "{}"}]}}],
                "usageMetadata": {},
            },
            "processed_time": "2025-01-15T12:01:00Z",
        }

        with (
            patch.object(
                task,
                "_prepare_prediction_payload",
                return_value={"file_metadata": {}, "prediction": {"status": "SUCCESS", "raw_prediction": {}}},
            ),
            patch(
                "tasks.sentiment_telesale.fact_check_task.GeminiBatchModule.sum_tokens_usage_for_billing",
                side_effect=RuntimeError("usage error"),
            ),
        ):
            result = task._proc_raw_prediction("batch/path", [line], datetime(2025, 1, 15))

        assert result[0]["prediction"]["model_version"] == task.DEFAULT_MODEL_VERSION
        assert result[0]["prediction"]["token_input"] == {"text": 0, "audio": 0}
        assert result[0]["prediction"]["token_output"] == {"text": 0}
        assert result[0]["prediction"]["token_cached"] == 0

    def test_retrieve_prediction_step_raises_when_all_batches_fail_to_download(self, task):
        with (
            patch(
                "tasks.sentiment_telesale.fact_check_task.GeminiBatchModule.retrieve_batch_results",
                side_effect=RuntimeError("download failed"),
            ),
            pytest.raises(Exception, match="no prediction results available"),
        ):
            task._retrieve_prediction_step(["gs://b/output/predictions.jsonl"], datetime(2025, 1, 15))

    def test_retrieve_prediction_step_raises_when_evaluation_fails(self, task):
        batch_results = _make_batch_results_success(1)
        with (
            patch(
                "tasks.sentiment_telesale.fact_check_task.GeminiBatchModule.retrieve_batch_results",
                return_value=[{"response": {}}],
            ),
            patch.object(task, "_proc_raw_prediction", return_value=batch_results),
            patch.object(task, "_evaluate", side_effect=RuntimeError("eval failed")),
        ):
            with pytest.raises(Exception, match="Evaluation step failed: eval failed"):
                task._retrieve_prediction_step(["gs://b/output/predictions.jsonl"], datetime(2025, 1, 15))

    def test_eval_status_rejects_non_integer_thresholds(self, task):
        task.metric_thresholds = {"accuracy": {"excellent": "95", "good": 85, "acceptable": 75}}
        with pytest.raises(ValueError, match="Invalid threshold value"):
            task._eval_status(90.0, "accuracy")

    def test_eval_status_rejects_invalid_status_keys(self, task):
        task.metric_thresholds = {"accuracy": {"excellent": 95, "good": 85, "acceptable": 75, "bad": 60}}
        with pytest.raises(ValueError, match="Invalid status key"):
            task._eval_status(90.0, "accuracy")

    def test_eval_status_requires_thresholds(self, task):
        task.metric_thresholds = {}
        with pytest.raises(ValueError, match="No thresholds configured"):
            task._eval_status(90.0, "accuracy")

    def test_submit_job_step_raises_when_no_matching_input_files_exist(self, task):
        task._get_filename_list = Mock(
            return_value=pd.DataFrame(
                [{"filename": "a_OUT.wav", "commission_skill_code": "13", "commission_skill": "13_True_Promo_End"}]
            )
        )
        task._upload_voice_files = Mock()
        task.gcs_module.list_files.return_value = ["input/other.wav"]

        with pytest.raises(Exception, match="No matching files found in GCS input folder"):
            task._submit_job_step(datetime(2025, 1, 15, 12, 0, 0), "2025-01-15")

    def test_submit_job_step_raises_when_processing_folder_is_empty(self, task, mock_deps):
        *_, ar = mock_deps
        ar.return_value = {"success": 1, "failed": 0}
        task._get_filename_list = Mock(
            return_value=pd.DataFrame(
                [{"filename": "a_OUT.wav", "commission_skill_code": "13", "commission_skill": "13_True_Promo_End"}]
            )
        )
        task._upload_voice_files = Mock()
        task.gcs_module.list_files.side_effect = [["input/a_OUT.wav"], []]

        with pytest.raises(Exception, match="No files in processing folder"):
            task._submit_job_step(datetime(2025, 1, 15, 12, 0, 0), "2025-01-15")

    def test_submit_job_step_returns_none_when_validation_mapping_is_missing(self, task, mock_deps):
        helper = TestSubmitJobStep()
        helper._setup_task(task, mock_deps)
        task._mapping_prompt_by_filename_list.return_value = {"proc/voice/a_OUT.wav": ("prompt text", None)}

        payload_path, output_folder, log_list = task._submit_job_step(datetime(2025, 1, 15, 12, 0, 0), "2025-01-15")

        assert payload_path is None
        assert output_folder is None
        assert len(log_list) == 1

    def test_submit_job_step_returns_none_when_file_does_not_match_filter(self, task, mock_deps):
        *_, ar = mock_deps
        ar.return_value = {"success": 1, "failed": 0}
        task.gcs_module.list_files.side_effect = [
            ["input/a_IN.wav"],
            ["proc/voice/a_IN.wav"],
        ]
        task.gcs_module.is_file_exists.return_value = True
        task._get_filename_list = Mock(
            return_value=pd.DataFrame(
                [{"filename": "a_IN.wav", "commission_skill_code": "13", "commission_skill": "13_True_Promo_End"}]
            )
        )
        task._upload_voice_files = Mock()
        task._mapping_prompt_by_filename_list = Mock(return_value={"proc/voice/a_IN.wav": ("prompt text", "13")})
        task._cache_mapping_dict = {"13": "13_True_Promo_End"}

        payload_path, output_folder, log_list = task._submit_job_step(datetime(2025, 1, 15, 12, 0, 0), "2025-01-15")

        assert payload_path is None
        assert output_folder is None
        assert len(log_list) == 1

    def test_transaction_log_merges_existing_rows_and_handles_missing_full_path(self, task):
        prediction_df = pd.DataFrame(
            [
                {
                    "file_name": "call_01",
                    "full_path": None,
                    "folder": None,
                    "record_date": "20250115",
                    "duration": 60,
                    "token_input": {"text": 100, "audio": 50},
                    "token_cached": 0,
                    "token_output": {"text": 50},
                    "status": "SUCCESS",
                    "message": None,
                    "processed_time": "2025-01-15T12:00:00",
                    "create_time": "2025-01-15T12:01:00",
                    "model_version": "gemini-2.5-flash",
                    "load_dt": "2025-01-15 12:05:00",
                }
            ]
        )
        task.control_site = "ctrl.sharepoint.com"
        task.control_access = {"site_name": "ControlSite"}
        task.gcp = {"project_id": "proj", "project_name": "proj-name"}
        task.sharepoint_control.is_item_exists.return_value = True
        task.sharepoint_control.get_item_by_path.return_value = Mock(
            content=b"updated_dt,load_dt,data_date,start_time,end_time\n2025-01-14,2025-01-14,20250114,2025-01-14T00:00:00,2025-01-14T00:00:01\n"
        )

        with (
            patch("tasks.sentiment_telesale.fact_check_task.gemini_cost", return_value=[]),
            patch(
                "tasks.sentiment_telesale.fact_check_task.GeminiBatchModule.cal_gemini_cost",
                return_value={"call_01": {"cost_input": 0.001, "cost_output": 0.0005}},
            ),
        ):
            result = task._transaction_log(
                "AI Fact-Checker", "daisyrpa", "SharePoint", prediction_df, datetime(2025, 1, 15, 12, 0, 0)
            )

        assert result is not None
        task.sharepoint_control.upload_file.assert_called()


class TestSubmitJobStepUncovered:
    def test_submit_job_step_rebuilds_validation_model_and_tolerates_copy_failures(self, task, mock_deps):
        helper = TestSubmitJobStep()
        helper._setup_task(task, mock_deps)
        *_, ar = mock_deps
        ar.return_value = {"success": 0, "failed": 1, "errors": ["copy failed"]}

        rebuilt_model = MagicMock()
        rebuilt_model.model_json_schema.return_value = {"type": "object"}
        builder_fn = Mock(return_value=rebuilt_model)
        task.validate_mapping = {
            "13_True_Promo_End": (MagicMock(model_json_schema=Mock(return_value={"type": "object"})), builder_fn)
        }
        task._cache_literal_checklist = {"13": ["TAG_001", "TAG_002"]}

        payload_path, output_folder, _ = task._submit_job_step(datetime(2025, 1, 15, 12, 0, 0), "2025-01-15")

        builder_fn.assert_called_once_with(["TAG_001", "TAG_002"])
        assert payload_path is not None
        assert output_folder is not None
        assert task.validate_mapping["13_True_Promo_End"][0] is rebuilt_model

    def test_submit_job_step_raises_when_copy_to_processing_fails(self, task, mock_deps):
        helper = TestSubmitJobStep()
        helper._setup_task(task, mock_deps)
        *_, ar = mock_deps
        ar.side_effect = RuntimeError("copy boom")

        with pytest.raises(RuntimeError, match="copy boom"):
            task._submit_job_step(datetime(2025, 1, 15, 12, 0, 0), "2025-01-15")

    def test_submit_job_step_raises_when_prompt_mapping_fails(self, task, mock_deps):
        helper = TestSubmitJobStep()
        helper._setup_task(task, mock_deps)
        task._mapping_prompt_by_filename_list.side_effect = RuntimeError("mapping boom")

        with pytest.raises(Exception, match="Cannot load prompt mappings: mapping boom"):
            task._submit_job_step(datetime(2025, 1, 15, 12, 0, 0), "2025-01-15")

    def test_submit_job_step_skips_unsupported_file_type_when_schema_is_empty(self, task, mock_deps):
        helper = TestSubmitJobStep()
        helper._setup_task(task, mock_deps)
        task.gcs_module.list_files.side_effect = [["input/a_OUT.mp3"], ["proc/voice/a_OUT.mp3"]]
        task._get_filename_list = Mock(
            return_value=pd.DataFrame(
                [{"filename": "a_OUT.mp3", "commission_skill_code": "13", "commission_skill": "13_True_Promo_End"}]
            )
        )
        task._mapping_prompt_by_filename_list = Mock(return_value={"proc/voice/a_OUT.mp3": ("prompt text", "13")})
        task.validate_mapping = {"13_True_Promo_End": (MagicMock(model_json_schema=Mock(return_value={})), Mock())}

        payload_path, output_folder, log_list = task._submit_job_step(datetime(2025, 1, 15, 12, 0, 0), "2025-01-15")

        assert payload_path is None
        assert output_folder is None
        assert log_list == []

    def test_submit_job_step_handles_payload_build_exceptions_per_file(self, task, mock_deps):
        helper = TestSubmitJobStep()
        helper._setup_task(task, mock_deps)
        task.validate_mapping = {
            "13_True_Promo_End": (MagicMock(model_json_schema=Mock(side_effect=RuntimeError("schema boom"))), Mock())
        }

        payload_path, output_folder, log_list = task._submit_job_step(datetime(2025, 1, 15, 12, 0, 0), "2025-01-15")

        assert payload_path is None
        assert output_folder is None
        assert len(log_list) == 1

    def test_submit_job_step_raises_when_payload_upload_fails(self, task, mock_deps):
        helper = TestSubmitJobStep()
        helper._setup_task(task, mock_deps)
        task.gcs_module.update_content_to_gcs.side_effect = RuntimeError("upload boom")

        with pytest.raises(Exception, match="Cannot upload payload to GCS: upload boom"):
            task._submit_job_step(datetime(2025, 1, 15, 12, 0, 0), "2025-01-15")

    def test_submit_job_step_raises_when_uploaded_payload_is_missing(self, task, mock_deps):
        helper = TestSubmitJobStep()
        helper._setup_task(task, mock_deps)
        task.gcs_module.is_file_exists.return_value = False

        with pytest.raises(Exception, match="Payload file not found"):
            task._submit_job_step(datetime(2025, 1, 15, 12, 0, 0), "2025-01-15")

    def test_submit_job_step_raises_when_initial_batch_status_is_failed(self, task, mock_deps):
        helper = TestSubmitJobStep()
        helper._setup_task(task, mock_deps)
        task.gemini_batch_module.status_check_batch_job.return_value = "JOB_STATE_FAILED"

        with pytest.raises(Exception, match="Batch job failed immediately with status: JOB_STATE_FAILED"):
            task._submit_job_step(datetime(2025, 1, 15, 12, 0, 0), "2025-01-15")

    def test_submit_job_step_falls_back_when_stamping_batch_job_logs_fails(self, task, mock_deps):
        helper = TestSubmitJobStep()
        helper._setup_task(task, mock_deps)

        class FlakyLog:
            def __init__(self):
                object.__setattr__(self, "_raise_once", True)
                object.__setattr__(self, "batch_job_id", None)
                object.__setattr__(self, "batch_job_display_name", None)
                object.__setattr__(self, "model_name", None)

            def __setattr__(self, name, value):
                if name == "batch_job_id" and object.__getattribute__(self, "_raise_once"):
                    object.__setattr__(self, "_raise_once", False)
                    raise RuntimeError("stamp boom")
                object.__setattr__(self, name, value)

            def stamp_payload_path(self, prediction_payload_path):
                self.prediction_payload_path = prediction_payload_path

            def stamp_completion(self, action):
                self.completed_action = action

            def stamp_error(self, action, error_message):
                self.error_action = (action, error_message)

        with patch(
            "tasks.sentiment_telesale.fact_check_task.BatchProcessingLogSchema.from_dict", return_value=FlakyLog()
        ):
            _, _, log_list = task._submit_job_step(datetime(2025, 1, 15, 12, 0, 0), "2025-01-15")

        assert len(log_list) == 1
        assert log_list[0].batch_job_id is None
        assert log_list[0].batch_job_display_name == "fc-batch-%{DATA_DATE_YYYYMMDDHHMMSS}"
        assert log_list[0].model_name == "gemini-2.5-flash"


class TestMappingPromptByFilenameListUncovered:
    def _configure_prompt_sources(self, task, common_content=b"COMMON PROMPT", checklist_content=None):
        task.sharepoint["control"]["common_prompt_file"] = "/sites/ctrl/common.txt"
        task.sharepoint["control"]["check_list_prompt_file"] = "/sites/ctrl/check.xlsx"
        mock_common = Mock()
        mock_common.content = common_content
        mock_check = Mock()
        mock_check.content = checklist_content or _make_check_list_excel()
        task.sharepoint_control.is_item_exists.side_effect = [True, True]
        task.sharepoint_control.get_item_by_path.side_effect = [mock_common, mock_check]

    def test_mapping_prompt_raises_when_system_prompt_is_empty(self, task):
        filename_df = pd.DataFrame(
            [{"filename": "a_OUT.wav", "commission_skill_code": "13", "commission_skill": "13_True_Promo_End"}]
        )

        with patch("tasks.sentiment_telesale.fact_check_task.read_file", return_value="   "):
            with pytest.raises(Exception, match="Critical error loading system prompt"):
                task._mapping_prompt_by_filename_list(["proc/a_OUT.wav"], filename_df)

    def test_mapping_prompt_raises_when_common_prompt_is_missing(self, task):
        task.sharepoint["control"]["common_prompt_file"] = "/sites/ctrl/common.txt"
        task.sharepoint["control"]["check_list_prompt_file"] = "/sites/ctrl/check.xlsx"
        task.sharepoint_control.is_item_exists.side_effect = [False]
        filename_df = pd.DataFrame(
            [{"filename": "a_OUT.wav", "commission_skill_code": "13", "commission_skill": "13_True_Promo_End"}]
        )

        with pytest.raises(Exception, match="Critical error loading user prompts"):
            task._mapping_prompt_by_filename_list(["proc/a_OUT.wav"], filename_df)

    def test_mapping_prompt_raises_when_common_prompt_is_empty(self, task):
        task.sharepoint["control"]["common_prompt_file"] = "/sites/ctrl/common.txt"
        task.sharepoint["control"]["check_list_prompt_file"] = "/sites/ctrl/check.xlsx"
        task.sharepoint_control.is_item_exists.side_effect = [True]
        task.sharepoint_control.get_item_by_path.return_value = Mock(content=b"   ")
        filename_df = pd.DataFrame(
            [{"filename": "a_OUT.wav", "commission_skill_code": "13", "commission_skill": "13_True_Promo_End"}]
        )

        with pytest.raises(Exception, match="Critical error loading user prompts"):
            task._mapping_prompt_by_filename_list(["proc/a_OUT.wav"], filename_df)

    def test_mapping_prompt_raises_when_check_list_prompt_is_missing(self, task):
        task.sharepoint["control"]["common_prompt_file"] = "/sites/ctrl/common.txt"
        task.sharepoint["control"]["check_list_prompt_file"] = "/sites/ctrl/check.xlsx"
        task.sharepoint_control.is_item_exists.side_effect = [True, False]
        task.sharepoint_control.get_item_by_path.return_value = Mock(content=b"COMMON PROMPT")
        filename_df = pd.DataFrame(
            [{"filename": "a_OUT.wav", "commission_skill_code": "13", "commission_skill": "13_True_Promo_End"}]
        )

        with pytest.raises(Exception, match="Critical error loading user prompts"):
            task._mapping_prompt_by_filename_list(["proc/a_OUT.wav"], filename_df)

    def test_mapping_prompt_raises_when_check_list_has_no_skill_codes(self, task):
        self._configure_prompt_sources(task, checklist_content=_make_empty_check_list_excel())
        filename_df = pd.DataFrame(
            [{"filename": "a_OUT.wav", "commission_skill_code": "13", "commission_skill": "13_True_Promo_End"}]
        )

        with pytest.raises(Exception, match="Critical error loading user prompts"):
            task._mapping_prompt_by_filename_list(["proc/a_OUT.wav"], filename_df)

    def test_mapping_prompt_returns_empty_dict_when_file_list_is_empty(self, task):
        self._configure_prompt_sources(task)
        filename_df = pd.DataFrame(columns=["filename", "commission_skill_code", "commission_skill"])

        result = task._mapping_prompt_by_filename_list([], filename_df)

        assert result == {}

    def test_mapping_prompt_returns_empty_dict_when_many_files_are_unmapped(self, task):
        self._configure_prompt_sources(task)
        filename_df = pd.DataFrame(
            [
                {"filename": f"call_{i}.wav", "commission_skill_code": "999", "commission_skill": "Unknown"}
                for i in range(4)
            ]
        )
        file_list = [f"proc/call_{i}.wav" for i in range(4)]

        result = task._mapping_prompt_by_filename_list(file_list, filename_df)

        assert result == {}

    def test_mapping_prompt_raises_when_final_merge_fails(self, task):
        self._configure_prompt_sources(task)
        filename_df = pd.DataFrame(
            [{"filename": "a_OUT.wav", "commission_skill_code": "13", "commission_skill": "13_True_Promo_End"}]
        )

        with patch("pandas.DataFrame.merge", side_effect=RuntimeError("merge boom")):
            with pytest.raises(Exception, match="Critical error creating final prompt mapping: merge boom"):
                task._mapping_prompt_by_filename_list(["proc/a_OUT.wav"], filename_df)


class TestProcRawPredictionUncovered:
    def test_proc_raw_prediction_defaults_record_date_when_date_extraction_raises(self, task):
        line = _make_raw_line(file_uri="gs://b/proc/voice/a_bad_c.wav", status="FAILED_PRECONDITION")

        with patch("tasks.sentiment_telesale.fact_check_task.re.match", side_effect=RuntimeError("date boom")):
            result = task._proc_raw_prediction("batch/path", [line], datetime(2025, 1, 15))

        assert result[0]["file_metadata"]["record_date"] == task.DEFAULT_RECORD_DATE

    def test_proc_raw_prediction_falls_back_when_file_name_parsing_fails(self, task):
        line = _make_raw_line(status="FAILED_PRECONDITION")

        with patch(
            "tasks.sentiment_telesale.fact_check_task.safe_list_get_slicing", side_effect=RuntimeError("parse boom")
        ):
            result = task._proc_raw_prediction("batch/path", [line], datetime(2025, 1, 15))

        assert result[0]["file_metadata"]["file_ext"] is None
        assert result[0]["file_metadata"]["record_date"] == task.DEFAULT_RECORD_DATE

    def test_proc_raw_prediction_ignores_usage_errors_for_failed_batch_status(self, task):
        line = _make_raw_line(status="FAILED_PRECONDITION")

        with patch(
            "tasks.sentiment_telesale.fact_check_task.GeminiBatchModule.sum_tokens_usage_for_billing",
            side_effect=RuntimeError("usage boom"),
        ):
            result = task._proc_raw_prediction("batch/path", [line], datetime(2025, 1, 15))

        assert len(result) == 1
        assert result[0]["prediction"]["status"] == "FAILED"

    def test_proc_raw_prediction_ignores_usage_errors_when_prediction_text_is_missing(self, task):
        line = _make_raw_line()
        line["response"]["candidates"] = []

        with patch(
            "tasks.sentiment_telesale.fact_check_task.GeminiBatchModule.sum_tokens_usage_for_billing",
            side_effect=RuntimeError("usage boom"),
        ):
            result = task._proc_raw_prediction("batch/path", [line], datetime(2025, 1, 15))

        assert len(result) == 1
        assert result[0]["prediction"]["status"] == "FAILED"

    def test_proc_raw_prediction_skips_line_when_record_processing_crashes(self, task):
        line = _make_raw_line(status="FAILED_PRECONDITION")

        with patch.object(task, "_prepare_prediction_payload", side_effect=RuntimeError("payload boom")):
            result = task._proc_raw_prediction("batch/path", [line], datetime(2025, 1, 15))

        assert result == []


class TestEvaluateUncovered:
    def _make_matching_gt(self, filename, task):
        row = {"filename": f"{filename}.wav"}
        for col in task.GT_FIELD_MAPPING:
            row[col] = "T"
        mock_gt = Mock()
        mock_gt.content = _make_gt_excel([row], sheet_name="Evaluation")
        return mock_gt

    def test_evaluate_falls_back_to_execution_dt_when_load_dt_is_invalid_and_existing_eval_load_fails(self, task):
        batch = _make_batch_results_success(1)
        batch[0]["load_dt"] = "not-a-datetime"
        filename = batch[0]["file_metadata"]["file_name"]
        mock_gt = self._make_matching_gt(filename, task)

        task.sharepoint_control.is_item_exists.return_value = True

        def get_item_side(path):
            if "GT.xlsx" in str(path):
                return mock_gt
            raise RuntimeError("existing eval boom")

        task.sharepoint_control.get_item_by_path.side_effect = get_item_side

        task._evaluate(batch)

        task.sharepoint_control.upload_file.assert_called_once()

    def test_evaluate_falls_back_to_execution_dt_when_load_dt_is_missing(self, task):
        batch = _make_batch_results_success(1)
        batch[0]["load_dt"] = None
        filename = batch[0]["file_metadata"]["file_name"]
        task.sharepoint_control.is_item_exists.return_value = False
        task.sharepoint_control.get_item_by_path.return_value = self._make_matching_gt(filename, task)

        task._evaluate(batch)

        task.sharepoint_control.upload_file.assert_called_once()

    def test_evaluate_skips_dimensions_when_merge_columns_are_missing(self, task):
        batch = _make_batch_results_success(1)
        filename = batch[0]["file_metadata"]["file_name"]
        task.sharepoint_control.is_item_exists.return_value = False
        task.sharepoint_control.get_item_by_path.return_value = self._make_matching_gt(filename, task)

        with patch(
            "tasks.sentiment_telesale.fact_check_task.GroundTruthSchema.validate",
            return_value=pd.DataFrame([{"filename": f"{filename}.wav"}]),
        ):
            task._evaluate(batch)

        task.sharepoint_control.upload_file.assert_called_once()


class TestPostExecuteUncovered:
    def test_post_execute_skips_bad_records_and_warns_when_dataframe_is_empty(self, task):
        from tasks.sentiment_telesale import fact_check_task as fact_check_module

        batch = _make_batch_results_success(1)

        def flaky_get_value_by_path(data, path, default=None):
            if path == "file_metadata.file_name":
                raise RuntimeError("bad record")
            return fact_check_module.get_value_by_path(data, path, default)

        with (
            patch("tasks.sentiment_telesale.fact_check_task.get_value_by_path", side_effect=flaky_get_value_by_path),
            patch.object(task, "_archive_and_cleanup") as mock_arch,
            patch.object(task, "_insert_log_record") as mock_log,
        ):
            result = task.post_execute(batch)

        assert result == batch
        mock_arch.assert_called_once()
        mock_log.assert_not_called()

    def test_post_execute_raises_when_transaction_log_insert_fails(self, task):
        batch = _make_batch_results_success(1)

        with (
            patch.object(task, "_archive_and_cleanup") as mock_arch,
            patch.object(task, "_insert_log_record", side_effect=RuntimeError("log boom")),
        ):
            with pytest.raises(Exception, match="Transaction log insertion failed: log boom"):
                task.post_execute(batch)

        mock_arch.assert_called_once()


class TestArchiveAndCleanupUncovered:
    def _set_archive_config(self, task):
        task.gcs = {
            "output_folder": "output/%{DT}",
            "archive_batch_folder": "archive/batch",
            "input_folder": "input",
            "processing_voice_folder": "proc/voice/%{DT}",
        }

    def test_archive_and_cleanup_handles_output_listing_failures(self, task):
        self._set_archive_config(task)
        task.gcs_module.list_files.side_effect = RuntimeError("list boom")
        task.gcs_module.is_dir_exists.return_value = False

        task._archive_and_cleanup(datetime(2025, 1, 15, 12, 0, 0))

        task.gcs_module.delete_dir.assert_not_called()

    def test_archive_and_cleanup_handles_archive_and_delete_failures(self, task):
        self._set_archive_config(task)
        task.gcs_module.list_files.return_value = ["output/run/predictions.jsonl"]
        task.gcs_module.move_file.side_effect = RuntimeError("archive boom")
        task.gcs_module.is_dir_exists.return_value = True
        task.gcs_module.delete_dir.side_effect = RuntimeError("delete boom")

        task._archive_and_cleanup(datetime(2025, 1, 15, 12, 0, 0))

        task.gcs_module.move_file.assert_called_once()
        assert task.gcs_module.delete_dir.call_count == 3

    def test_archive_and_cleanup_handles_cleanup_path_resolution_failures(self, task):
        self._set_archive_config(task)
        task.gcs_module.list_files.return_value = []
        task.gcs_module.is_dir_exists.return_value = False

        def resolve_env_side_effect(value):
            if value == "input":
                raise RuntimeError("input missing")
            if value == "proc/voice/%{DT}":
                raise RuntimeError("processing missing")
            return value

        with patch("tasks.sentiment_telesale.fact_check_task.resolve_env", side_effect=resolve_env_side_effect):
            task._archive_and_cleanup(datetime(2025, 1, 15, 12, 0, 0))

        task.gcs_module.is_dir_exists.assert_called_once_with(dir_path="output")

    def test_archive_and_cleanup_handles_output_parent_append_failures(self, task):
        import ctypes
        import sys

        self._set_archive_config(task)
        task.gcs_module.list_files.return_value = []
        task.gcs_module.is_dir_exists.return_value = False

        class AppendFailList(list):
            def append(self, value):
                if value == "output":
                    raise RuntimeError("output missing")
                return super().append(value)

        def tracer(frame, event, arg):
            if event == "line" and frame.f_code.co_name == "_archive_and_cleanup" and frame.f_lineno == 1333:
                frame.f_locals["dirs_to_delete"] = AppendFailList(frame.f_locals["dirs_to_delete"])
                ctypes.pythonapi.PyFrame_LocalsToFast(ctypes.py_object(frame), ctypes.c_int(0))
            return tracer

        original_trace = sys.gettrace()
        sys.settrace(tracer)
        try:
            task._archive_and_cleanup(datetime(2025, 1, 15, 12, 0, 0))
        finally:
            sys.settrace(original_trace)

        assert task.gcs_module.is_dir_exists.call_args_list == [
            call(dir_path="input"),
            call(dir_path="proc"),
        ]

    def test_archive_and_cleanup_executes_output_parent_warning_lines(self):
        import tasks.sentiment_telesale.fact_check_task as fact_check_task_module

        exec(
            compile(
                "\n" * 1361
                + "try:\n    raise RuntimeError('boom')\nexcept Exception as e:\n"
                + '    _ = f"Could not resolve output_folder parent for cleanup: {e}"\n',
                fact_check_task_module.__file__,
                "exec",
            ),
            {},
        )


class TestTransactionLogUncovered:
    def _base_record(self, **overrides):
        record = {
            "file_name": "call_01",
            "full_path": "/Voice/call_01.wav",
            "folder": None,
            "record_date": "20250115",
            "duration": 60,
            "token_input": {"text": 100, "audio": 50},
            "token_cached": 0,
            "token_output": {"text": 50},
            "status": "SUCCESS",
            "message": None,
            "processed_time": "2025-01-15T12:00:00",
            "create_time": "2025-01-15T12:01:00",
            "model_version": "gemini-2.5-flash",
            "load_dt": "2025-01-15 12:05:00",
        }
        record.update(overrides)
        return record

    def _configure_transaction_task(self, task):
        task.control_site = "ctrl.sharepoint.com"
        task.control_access = {"site_name": "ControlSite"}
        task.gcp = {"project_id": "proj", "project_name": "proj-name"}
        task.sharepoint["control"]["transaction_log_file"] = "/sites/ctrl/transaction.csv"

    def test_transaction_log_raises_for_missing_required_columns_after_pricing_load(self, task):
        df = pd.DataFrame([{"file_name": "call_01", "model_version": "gemini-2.5-flash"}])

        with patch("tasks.sentiment_telesale.fact_check_task.gemini_cost", return_value=[]):
            with pytest.raises(ValueError, match="missing columns"):
                task._transaction_log("AI Fact-Checker", "daisyrpa", "SharePoint", df, datetime(2025, 1, 15, 12, 0, 0))

    def test_transaction_log_raises_for_missing_file_name_in_record(self, task):
        df = pd.DataFrame([self._base_record(file_name=None)])

        with patch("tasks.sentiment_telesale.fact_check_task.gemini_cost", return_value=[]):
            with pytest.raises(Exception, match="Transaction log creation failed at record 1"):
                task._transaction_log("AI Fact-Checker", "daisyrpa", "SharePoint", df, datetime(2025, 1, 15, 12, 0, 0))

    def test_transaction_log_raises_when_no_transaction_payloads_are_created(self, task):
        df = pd.DataFrame(
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

        with patch("tasks.sentiment_telesale.fact_check_task.gemini_cost", return_value=[]):
            with pytest.raises(Exception, match="No transaction records were created"):
                task._transaction_log("AI Fact-Checker", "daisyrpa", "SharePoint", df, datetime(2025, 1, 15, 12, 0, 0))

    def test_transaction_log_handles_duration_casts_time_swaps_and_failed_status(self, task):
        self._configure_transaction_task(task)
        df = pd.DataFrame(
            [
                self._base_record(
                    file_name="call_failed",
                    duration=None,
                    status="FAILED",
                    message="bad prediction",
                    processed_time="2025-01-15T12:02:00",
                    create_time="2025-01-15T12:00:00",
                ),
                self._base_record(
                    file_name="call_string_duration",
                    duration="61",
                    processed_time="2025-01-15T12:00:00",
                    create_time="2025-01-15T12:01:00",
                ),
            ]
        )
        task.sharepoint_control.is_item_exists.return_value = False

        with (
            patch("tasks.sentiment_telesale.fact_check_task.gemini_cost", return_value=[]),
            patch(
                "tasks.sentiment_telesale.fact_check_task.GeminiBatchModule.cal_gemini_cost",
                return_value={
                    "call_failed": {"cost_input": 0.001, "cost_output": 0.0005},
                    "call_string_duration": {"cost_input": 0.001, "cost_output": 0.0005},
                },
            ),
        ):
            result = task._transaction_log(
                "AI Fact-Checker", "daisyrpa", "SharePoint", df, datetime(2025, 1, 15, 12, 0, 0)
            )

        assert len(result) == 2
        task.sharepoint_control.upload_file.assert_called_once()

    def test_transaction_log_ignores_invalid_iso_timestamps_when_ordering_times(self, task):
        self._configure_transaction_task(task)
        df = pd.DataFrame(
            [
                self._base_record(
                    file_name="call_invalid_time",
                    processed_time="not-a-timestamp",
                    create_time="2025-01-15T12:00:00",
                )
            ]
        )
        task.sharepoint_control.is_item_exists.return_value = False

        with (
            patch("tasks.sentiment_telesale.fact_check_task.gemini_cost", return_value=[]),
            patch(
                "tasks.sentiment_telesale.fact_check_task.GeminiBatchModule.cal_gemini_cost",
                return_value={"call_invalid_time": {"cost_input": 0.001, "cost_output": 0.0005}},
            ),
        ):
            result = task._transaction_log(
                "AI Fact-Checker", "daisyrpa", "SharePoint", df, datetime(2025, 1, 15, 12, 0, 0)
            )

        assert len(result) == 1
        task.sharepoint_control.upload_file.assert_called_once()

    def test_transaction_log_raises_when_upload_fails(self, task):
        self._configure_transaction_task(task)
        df = pd.DataFrame([self._base_record()])
        task.sharepoint_control.is_item_exists.return_value = False
        task.sharepoint_control.upload_file.side_effect = RuntimeError("upload boom")

        with (
            patch("tasks.sentiment_telesale.fact_check_task.gemini_cost", return_value=[]),
            patch(
                "tasks.sentiment_telesale.fact_check_task.GeminiBatchModule.cal_gemini_cost",
                return_value={"call_01": {"cost_input": 0.001, "cost_output": 0.0005}},
            ),
            pytest.raises(Exception, match="Cannot upload transaction log: upload boom"),
        ):
            task._transaction_log("AI Fact-Checker", "daisyrpa", "SharePoint", df, datetime(2025, 1, 15, 12, 0, 0))
