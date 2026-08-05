"""The three OCR tasks must route ``on_error`` to the config-driven system-error notifier.

This checks the wiring only (delegation to ``notify_system_error`` with the task's context,
name, and the raised error) — the notifier's own behavior is covered in ``test_error_notify``.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from tasks.ocr_tax_invoice_pipeline.finalize_task import OCRFinalizeTask
from tasks.ocr_tax_invoice_pipeline.retrieve_task import OCRRetrieveTask
from tasks.ocr_tax_invoice_pipeline.submit_task import OCRSubmitTask

_EXEC_DT = datetime(2026, 6, 10, tzinfo=ZoneInfo("UTC"))
_COMMON = {
    "framework": {"timezone": "Asia/Bangkok"},
    "control": {"site_name": "ctrl"},
    "msgraph": {"client_id": "mg"},
}
_PACKAGES = {"execution_dt": _EXEC_DT, "job_id": "JOB", "pipeline_name": "tax_invoice_extraction"}

# (task class, its module path) — on_error / notify_system_error live in each task's own module.
_CASES = [
    (OCRSubmitTask, "tasks.ocr_tax_invoice_pipeline.submit_task"),
    (OCRRetrieveTask, "tasks.ocr_tax_invoice_pipeline.retrieve_task"),
    (OCRFinalizeTask, "tasks.ocr_tax_invoice_pipeline.finalize_task"),
]


@pytest.mark.parametrize(("task_cls", "module_path"), _CASES)
def test_on_error_delegates_to_notify_system_error(mocker, task_cls, module_path):
    # Arrange
    mocker.patch("tasks.ocr_tax_invoice_pipeline.helper.task_context.load_yaml", return_value=_COMMON)
    notify = mocker.patch(f"{module_path}.notify_system_error")
    task = task_cls(task_param={"domain": "treasury", "sharepoint": {}}, packages=_PACKAGES)
    error = ValueError("boom")

    # Act
    task.on_error(error)

    # Assert
    notify.assert_called_once_with(task.ctx, task_cls.__name__, error)
