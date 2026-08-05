"""Config smoke tests: every task in the fact-check YAMLs is registered, finalize stays last."""

import tasks  # noqa: F401  (import side effect: registers all task classes)
from src.core.task_registry import task_registry
from src.utils.file_utils import load_yaml

PRE_CONFIG = "config/tax_invoice_extraction/ocr_pipeline_fact_check_pre_tasks.yml"
POST_CONFIG = "config/tax_invoice_extraction/ocr_pipeline_fact_check_post_tasks.yml"


def _task_keys(config_path):
    config = load_yaml(config_path)
    return [key for key in config if key != "pipeline_name"]


def test_pre_config_task_keys_are_registered():
    for key in _task_keys(PRE_CONFIG):
        task_registry.get_task(key)  # raises KeyError if not registered


def test_post_config_task_keys_are_registered():
    for key in _task_keys(POST_CONFIG):
        task_registry.get_task(key)  # raises KeyError if not registered


def test_post_config_runs_retrieve_then_factcheck_then_finalize_last():
    keys = _task_keys(POST_CONFIG)
    assert keys[-1] == "OCRFinalizeTask"  # must be last so it stamps only after fact-check
    assert keys == ["OCRRetrieveTask", "TaxInvoiceFactCheckTask", "OCRFinalizeTask"]
