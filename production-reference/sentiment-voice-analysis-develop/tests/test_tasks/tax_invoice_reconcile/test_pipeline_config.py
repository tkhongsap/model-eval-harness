"""Config smoke tests: every task named in the OCR-pipeline v2 tax-invoice YAMLs is registered.

Guards against the "registration is an import side effect" footgun — a missing export only
surfaces as a KeyError at engine runtime, so resolve every top-level task key here.
"""

import tasks  # noqa: F401  (import side effect: registers all task classes)
from src.core.task_registry import task_registry
from src.utils.file_utils import load_yaml

PRE_CONFIG = "config/tax_invoice_extraction/ocr_pipeline_pre_tasks.yml"
POST_CONFIG = "config/tax_invoice_extraction/ocr_pipeline_post_tasks.yml"


def _task_keys(config_path):
    config = load_yaml(config_path)
    return [key for key in config if key != "pipeline_name"]


def test_pre_config_task_keys_are_registered():
    for key in _task_keys(PRE_CONFIG):
        task_registry.get_task(key)  # raises KeyError if not registered


def test_post_config_task_keys_are_registered():
    for key in _task_keys(POST_CONFIG):
        task_registry.get_task(key)  # raises KeyError if not registered


def test_post_config_runs_retrieve_then_business_then_finalize_last():
    keys = _task_keys(POST_CONFIG)
    assert keys[-1] == "OCRFinalizeTask"  # must be last so it stamps only after business logic
    assert keys == ["ReconcilePrecheckTask", "OCRRetrieveTask", "ReconcileTask", "OCRFinalizeTask"]
