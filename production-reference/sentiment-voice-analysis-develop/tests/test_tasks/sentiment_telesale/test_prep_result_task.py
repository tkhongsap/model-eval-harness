"""
Tests for PrepResultTask.
"""

from unittest.mock import patch

import pytest

from tasks.sentiment_telesale.prep_result_task import PrepResultTask


@pytest.fixture
def task_instance():
    """Create a PrepResultTask with mocked init."""
    with patch.object(PrepResultTask, "__init__", lambda self, **kw: None):
        task = PrepResultTask.__new__(PrepResultTask)
        task.config = {}
        task.packages = {}
        task.task_name = "PrepResultTask"
        task.pre_result = None
        task.framework = {"telesale_scoring_file": "config/scoring/telesale_scoring.yml"}
        return task


class TestPrepResultTaskInit:
    """Test PrepResultTask initialization."""

    def test_init_sets_framework(self, task_instance):
        assert task_instance.framework["telesale_scoring_file"] == "config/scoring/telesale_scoring.yml"


class TestExecuteTask:
    """Test execute_task method."""

    def test_execute_with_no_batch_results(self, task_instance):
        task_instance.pre_result = {
            "list_batchs": ["batch1"],
            "batch_results": [],
            "failed_batches": [],
        }
        result = task_instance.execute_task()
        assert result["batch_results"] == []
        assert result["list_batchs"] == ["batch1"]

    def test_execute_with_none_pre_result_raises(self, task_instance):
        task_instance.pre_result = None
        with pytest.raises(Exception):
            task_instance.execute_task()

    @patch("tasks.sentiment_telesale.prep_result_task.load_yaml")
    def test_execute_with_valid_results(self, mock_load_yaml, task_instance):
        mock_load_yaml.return_value = {
            "category1": {
                "max": 10,
                "criterion1": -5,
                "criterion2": -3,
            }
        }
        task_instance.pre_result = {
            "list_batchs": ["batch1"],
            "batch_results": [
                {
                    "prediction": {
                        "status": "SUCCESS",
                        "raw_prediction": {"category1": {"criterion1": True, "criterion2": False}},
                    },
                    "file_metadata": {"file_name": "test.wav"},
                }
            ],
            "failed_batches": [],
        }
        result = task_instance.execute_task()
        assert len(result["batch_results"]) == 1
        assert "scoring_result" in result["batch_results"][0]
        assert result["batch_results"][0]["scoring_result"]["scoring_status"] == "SUCCESS"

    @patch("tasks.sentiment_telesale.prep_result_task.load_yaml")
    def test_execute_with_failed_prediction_skips_scoring(self, mock_load_yaml, task_instance):
        mock_load_yaml.return_value = {"category1": {"max": 10, "c1": -5}}
        task_instance.pre_result = {
            "list_batchs": ["batch1"],
            "batch_results": [
                {
                    "prediction": {"status": "FAILED", "raw_prediction": {}},
                    "file_metadata": {"file_name": "test.wav"},
                }
            ],
            "failed_batches": [],
        }
        result = task_instance.execute_task()
        assert result["batch_results"][0]["scoring_result"]["scoring_status"] == "SKIPPED"


class TestCalScoring:
    """Test _cal_scoring method."""

    @patch("tasks.sentiment_telesale.prep_result_task.load_yaml")
    def test_scoring_all_pass(self, mock_load_yaml, task_instance):
        """All criteria pass → score == max."""
        mock_load_yaml.return_value = {"cat": {"max": 10, "c1": -3, "c2": -2}}
        records = [
            {
                "prediction": {
                    "status": "SUCCESS",
                    "raw_prediction": {"cat": {"c1": True, "c2": True}},
                },
                "file_metadata": {"file_name": "f.wav"},
            }
        ]
        result = task_instance._cal_scoring(records)
        sr = result[0]["scoring_result"]
        assert sr["scoring_status"] == "SUCCESS"
        assert sr["total_score"] == 10

    @patch("tasks.sentiment_telesale.prep_result_task.load_yaml")
    def test_scoring_one_fail(self, mock_load_yaml, task_instance):
        """One criterion fails → penalty applied."""
        mock_load_yaml.return_value = {"cat": {"max": 10, "c1": -5, "c2": -3}}
        records = [
            {
                "prediction": {
                    "status": "SUCCESS",
                    "raw_prediction": {"cat": {"c1": True, "c2": False}},
                },
                "file_metadata": {"file_name": "f.wav"},
            }
        ]
        result = task_instance._cal_scoring(records)
        sr = result[0]["scoring_result"]
        assert sr["total_score"] == 7  # 10 - 3

    @patch("tasks.sentiment_telesale.prep_result_task.load_yaml")
    def test_scoring_with_none_prediction(self, mock_load_yaml, task_instance):
        """None in prediction → treated as None penalty."""
        mock_load_yaml.return_value = {"cat": {"max": 10, "c1": -5, "c2": -3}}
        records = [
            {
                "prediction": {
                    "status": "SUCCESS",
                    "raw_prediction": {"cat": {"c1": True, "c2": None}},
                },
                "file_metadata": {"file_name": "f.wav"},
            }
        ]
        result = task_instance._cal_scoring(records)
        sr = result[0]["scoring_result"]
        assert sr["scoring_status"] == "SUCCESS"

    @patch("tasks.sentiment_telesale.prep_result_task.load_yaml")
    def test_scoring_nested_criteria(self, mock_load_yaml, task_instance):
        """Nested subcategories are handled recursively."""
        mock_load_yaml.return_value = {
            "category": {
                "sub1": {"max": 5, "c1": -2, "c2": -1},
                "sub2": {"max": 5, "c1": -3},
            }
        }
        records = [
            {
                "prediction": {
                    "status": "SUCCESS",
                    "raw_prediction": {
                        "category": {
                            "sub1": {"c1": True, "c2": True},
                            "sub2": {"c1": False},
                        }
                    },
                },
                "file_metadata": {"file_name": "f.wav"},
            }
        ]
        result = task_instance._cal_scoring(records)
        sr = result[0]["scoring_result"]
        assert sr["scoring_status"] == "SUCCESS"
        assert sr["total_score"] == 7  # (5+0) + (5-3) = 7

    @patch("tasks.sentiment_telesale.prep_result_task.load_yaml")
    def test_scoring_missing_criteria_file_raises(self, mock_load_yaml, task_instance):
        """Missing scoring file raises exception."""
        task_instance.framework = {}
        records = [
            {
                "prediction": {"status": "SUCCESS", "raw_prediction": {}},
                "file_metadata": {"file_name": "f.wav"},
            }
        ]
        with pytest.raises(Exception, match="Scoring criteria file"):
            task_instance._cal_scoring(records)

    @patch("tasks.sentiment_telesale.prep_result_task.load_yaml", side_effect=Exception("File error"))
    def test_scoring_yaml_load_error(self, mock_load_yaml, task_instance):
        """YAML load error raises exception."""
        records = [
            {
                "prediction": {"status": "SUCCESS", "raw_prediction": {}},
                "file_metadata": {"file_name": "f.wav"},
            }
        ]
        with pytest.raises(Exception, match="Cannot load scoring criteria"):
            task_instance._cal_scoring(records)

    @patch("tasks.sentiment_telesale.prep_result_task.load_yaml")
    def test_scoring_error_in_record_handled(self, mock_load_yaml, task_instance):
        """Error in scoring one record is captured, not raised."""
        mock_load_yaml.return_value = {"cat": {"max": 10, "c1": -5}}
        records = [
            {
                "prediction": {
                    "status": "SUCCESS",
                    "raw_prediction": None,  # Will cause error
                },
                "file_metadata": {"file_name": "f.wav"},
            }
        ]
        result = task_instance._cal_scoring(records)
        sr = result[0]["scoring_result"]
        assert sr["scoring_status"] == "FAILED"
        assert sr["scoring_message"] is not None


class TestPrepResultTaskAdditional:
    @patch("tasks.sentiment_telesale.prep_result_task.TaskInterface.__init__", return_value=None)
    @patch.object(PrepResultTask, "get_config", return_value={"telesale_scoring_file": "config.yml"})
    def test_init_loads_framework_config(self, mock_get_config, mock_task_init):
        task = PrepResultTask()

        assert task.framework == {"telesale_scoring_file": "config.yml"}

    def test_execute_wraps_scoring_errors(self, task_instance):
        task_instance.pre_result = {
            "list_batchs": ["batch1"],
            "batch_results": [
                {"prediction": {"status": "SUCCESS", "raw_prediction": {}}, "file_metadata": {"file_name": "f.wav"}}
            ],
            "failed_batches": [],
        }

        with patch.object(task_instance, "_cal_scoring", side_effect=RuntimeError("boom")):
            with pytest.raises(Exception, match="Scoring calculation failed: boom"):
                task_instance.execute_task()

    @patch("tasks.sentiment_telesale.prep_result_task.load_yaml")
    def test_scoring_nested_none_branch_uses_not_none_score(self, mock_load_yaml, task_instance):
        mock_load_yaml.return_value = {"category": {"subcategory": {"max": 5, "criterion": -2}}}
        records = [
            {
                "prediction": {
                    "status": "SUCCESS",
                    "raw_prediction": {"category": {"subcategory": {"criterion": None}}},
                },
                "file_metadata": {"file_name": "f.wav"},
            }
        ]

        result = task_instance._cal_scoring(records)
        sr = result[0]["scoring_result"]

        assert sr["total_score"] == 0
        assert sr["max_score"] == 5
        assert sr["max_score_not_none"] == 0
        assert sr["scoring_detail"]["category"]["detail"]["subcategory"]["score"] is None

    @patch("tasks.sentiment_telesale.prep_result_task.load_yaml")
    def test_scoring_negative_nested_score_is_clipped_to_zero(self, mock_load_yaml, task_instance):
        mock_load_yaml.return_value = {"category": {"subcategory": {"max": 1, "criterion": -5}}}
        records = [
            {
                "prediction": {
                    "status": "SUCCESS",
                    "raw_prediction": {"category": {"subcategory": {"criterion": False}}},
                },
                "file_metadata": {"file_name": "f.wav"},
            }
        ]

        result = task_instance._cal_scoring(records)
        detail = result[0]["scoring_result"]["scoring_detail"]

        assert detail["category"]["detail"]["subcategory"]["score"] == 0
        assert result[0]["scoring_result"]["total_score"] == 0
