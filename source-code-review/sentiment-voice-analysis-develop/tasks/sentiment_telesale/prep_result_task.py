# Library imports
from typing import Any

from src.core.task_interface import TaskInterface

# Source code imports
from src.core.task_registry import task_registry
from src.utils.file_utils import (
    load_yaml,
)
from src.utils.logger import Logger

logger = Logger(__name__)


@task_registry.register("TelesalePrepResultTask")
class PrepResultTask(TaskInterface):
    """
    Task to prepare batch results for telesale sentiment analysis.
    - Retrieves execution packages, calculates scoring based on predefined criteria, and prepares results for export.
    - Scoring is calculated recursively based on a criteria hierarchy defined in a YAML file.
        - Handles missing or failed predictions gracefully, marking scoring as 'SKIPPED' or 'FAILED' as appropriate.
        - Expects batch results to contain a 'prediction' field with 'status' and 'raw_prediction' for scoring.
        - Returns updated batch results with scoring details included.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # Load parameters from configuration
        self.framework = self.get_config("framework", {})

    def execute_task(self) -> Any:
        """
        Main execution method for PrepResultTask.
        Retrieves execution packages, calculates scoring, and prepares results.
        Returns:
            dict: Processed batch results with scoring details.
        """
        logger.info("Starting PrepResultTask execution")
        try:
            list_batchs = self.pre_result.get("list_batchs", [])
            batch_results = self.pre_result.get("batch_results", [])
            failed_batches = self.pre_result.get("failed_batches", [])
            logger.debug(
                f"Retrieved {len(batch_results)} batch results, {len(list_batchs)} batches, {len(failed_batches)} failed batches"
            )
        except Exception as e:
            logger.error(f"Failed to retrieve execution packages: {e}", exc_info=True)
            raise Exception(f"Cannot proceed with task execution: {e}") from e

        if not batch_results or len(batch_results) == 0:
            logger.warning("No batch results to process, skipping upload and archival")
            return {"batch_results": [], "list_batchs": list_batchs, "failed_batches": failed_batches}

        try:
            batch_results = self._cal_scoring(batch_results)
            logger.info(f"Scoring calculation completed for {len(batch_results)} records")
        except Exception as e:
            logger.error(f"Failed to calculate scoring: {e}", exc_info=True)
            raise Exception(f"Scoring calculation failed: {e}") from e

        return {"batch_results": batch_results, "list_batchs": list_batchs, "failed_batches": failed_batches}

    def _cal_scoring(self, batch_results: list[dict]) -> list[dict]:
        """
        Calculate telesale scoring based on predefined criteria.
        Nested criteria are handled recursively.
        Parameters:
            batch_results (list[dict]): List of batch result records containing predictions.
        Returns:
            list[dict]: Updated batch results with scoring details.
        """

        def cal_recursive(criteria: dict, prediction: dict, level=0) -> tuple[int, int, dict]:
            """
            Recursive function to calculate scoring based on criteria and predictions.
            Parameters:
                criteria (dict): Scoring criteria for the current level.
                prediction (dict): Prediction data for the current record.
                level (int): Current level in the criteria hierarchy (for logging).
            Returns:
                tuple: (max_score, max_score_not_none, penalty, penalty_not_none, detail, none_flag)
            """
            current_level_penalty = 0  # Penalties from leaf criteria at this level
            this_level_max = 0  # Max score from 'max' key at this level
            child_max_total = 0  # Accumulated max from child dictionaries
            child_max_not_none_total = 0  # Accumulated max from child dictionaries (not None)
            child_penalty_total = 0  # Accumulated penalties from child dictionaries
            child_penalty_not_none_total = 0  # Accumulated penalties from child dictionaries (not None)
            check_list_penalty = []  # List to track penalties from leaf criteria for None checking

            detail = {}

            for key, value in criteria.items():
                if key == "max":
                    this_level_max = value
                    continue

                if isinstance(value, dict):
                    # Recurse into nested dictionary
                    (
                        child_max,
                        child_max_not_none,
                        child_penalty,
                        child_penalty_not_none,
                        child_detail,
                        child_none_flag,
                    ) = cal_recursive(value, prediction.get(key, {}), level + 1)

                    # Accumulate from children
                    child_max_total += child_max
                    child_max_not_none_total += child_max_not_none
                    child_penalty_total += child_penalty
                    child_penalty_not_none_total += child_penalty_not_none
                    if child_none_flag:
                        score = None
                        score_not_none = None
                    else:
                        score = child_max + child_penalty
                        if score < 0:
                            score = 0
                    score_not_none = child_max_not_none + child_penalty_not_none

                    detail[key] = {
                        "score": score,
                        "max_score": child_max,
                        "score_not_none": 0 if score_not_none < 0 else score_not_none,
                        "max_score_not_none": child_max_not_none,
                        "detail": child_detail,
                    }
                else:
                    pre_value = prediction.get(key, False)
                    if pre_value is True:
                        penalty = 0
                    elif pre_value is None:
                        penalty = None
                    else:
                        penalty = value
                    detail[key] = penalty
                    check_list_penalty.append(penalty)

            if not check_list_penalty:
                current_level_penalty = 0
            elif all(p is None for p in check_list_penalty):
                current_level_penalty = None
            else:
                current_level_penalty = sum(p for p in check_list_penalty if p is not None)

            # Determine what to return based on whether this level has a 'max'
            if this_level_max > 0:
                # This level defines its own max (e.g., subcategory level)
                if current_level_penalty is None:
                    none_flag = True
                    return_max = this_level_max
                    return_max_not_none = 0
                    return_penalty = 0
                    return_penalty_not_none = 0
                else:
                    none_flag = False
                    return_max = this_level_max
                    return_max_not_none = this_level_max
                    return_penalty = current_level_penalty
                    return_penalty_not_none = current_level_penalty
            else:
                # No max at this level (e.g., category level) - return accumulated from children
                none_flag = False
                return_max = child_max_total
                return_penalty = child_penalty_total + current_level_penalty
                return_max_not_none = child_max_not_none_total  # Use not_none accumulation, not regular
                return_penalty_not_none = child_penalty_not_none_total + current_level_penalty
            return return_max, return_max_not_none, return_penalty, return_penalty_not_none, detail, none_flag

        scoring_criteria_file = self.framework.get("telesale_scoring_file")
        if not scoring_criteria_file:
            logger.error("Telesale scoring criteria file not found in framework configuration")
            raise Exception("Scoring criteria file is required for scoring calculation")

        try:
            scoring_criteria = load_yaml(file_path=scoring_criteria_file)
            logger.debug(f"Loaded scoring criteria from: {scoring_criteria_file}")
        except Exception as e:
            logger.error(f"Failed to load scoring criteria file '{scoring_criteria_file}': {e}", exc_info=True)
            raise Exception(f"Cannot load scoring criteria: {e}") from e

        logger.debug(f"Calculating telesale scoring for {len(batch_results)} batch results")
        for record in batch_results:
            try:
                if record["prediction"]["status"] != "SUCCESS":
                    logger.debug(f"Skipping scoring for failed record: {record['file_metadata']['file_name']}")
                    record["scoring_result"] = {
                        "scoring_detail": None,
                        "scoring_status": "SKIPPED",
                        "total_score": None,
                        "max_score": None,
                        "max_score_not_none": None,
                        "scoring_message": "Prediction failed, scoring skipped",
                    }

                else:
                    (
                        total_max_score,
                        total_max_score_not_none,
                        total_penalty,
                        total_penalty_not_none,
                        scoring_detail,
                        _,
                    ) = cal_recursive(scoring_criteria, record["prediction"]["raw_prediction"], level=0)
                    if total_max_score != total_max_score_not_none:
                        total_score = total_max_score_not_none + total_penalty_not_none
                    else:
                        total_score = total_max_score + total_penalty
                    record["scoring_result"] = {
                        "scoring_detail": scoring_detail,
                        "scoring_status": "SUCCESS",
                        "total_score": total_score if total_score > 0 else 0,
                        "max_score": total_max_score,
                        "max_score_not_none": total_max_score_not_none,
                        "scoring_message": None,
                    }

            except Exception as score_err:
                logger.error(
                    f"Error calculating scoring for record '{record['file_metadata']['file_name']}': {score_err}",
                    exc_info=True,
                )
                record["scoring_result"] = {
                    "scoring_detail": None,
                    "scoring_status": "FAILED",
                    "total_score": None,
                    "max_score": None,
                    "max_score_not_none": None,
                    "scoring_message": str(score_err),
                }
        return batch_results
