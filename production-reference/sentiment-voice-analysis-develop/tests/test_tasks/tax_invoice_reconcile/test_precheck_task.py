"""Tests for ReconcilePrecheckTask — pre-reconcile dependency check for the tax-invoice pipeline.

Covers: validate (required-key errors), pre_execute (SharePoint + notifier init), execute_task
(happy path + DependencyMissingError halt), on_error (business vs. system exception branches),
_missing_dependencies, _notify_missing, _recipients, and _subject.
"""

from __future__ import annotations

import copy
from datetime import datetime
from zoneinfo import ZoneInfo

from tasks.tax_invoice_reconcile.precheck_task import (
    _CASE_BUSINESS,
    _DEPENDENCY_MISSING_TEMPLATE,
    _PROCESSING_FAILED_TEMPLATE,
    _SUBJECT_DEPENDENCY_MISSING,
    _SUBJECT_PROCESSING_FAILED,
    DependencyMissingError,
    ReconcilePrecheckTask,
)

EXECUTION_DT = datetime(2026, 6, 10, tzinfo=ZoneInfo("UTC"))
COMMON_CONFIG = {
    "framework": {"timezone": "Asia/Bangkok"},
    "control": {"site_name": "ctrl"},
    "msgraph": {"client_id": "mg"},
}

_SOURCE = {
    "site_name": "s",
    "site_domain": "d",
    "site_path": "p",
    "client_id": "ci",
    "client_secret": "cs",
    "tenant_id": "ti",
    "master_buyer_path": "mb",
    "master_buyer_file": "MB.xlsx",
    "z45_report_path": "z",
    "z45_report_file": "Z.xlsx",
    "master_vendor_path": "mv",
    "master_vendor_file": "MV.xlsx",
}

_FRAMEWORK = {
    "email_template_dir": "tmpl",
    "notifications": {
        "business_exception": {"sender_email": "biz@x.com", "receiver_email": "biz-to@x.com"},
        "system_exception": {"sender_email": "sys@x.com", "receiver_email": "sys-to@x.com"},
    },
}


def _task_param():
    return {
        "domain": "treasury",
        "sharepoint": {"source_site": _SOURCE},
        "framework": _FRAMEWORK,
    }


def _make_task(mocker, task_param=None):
    """Construct a ReconcilePrecheckTask with common.yml stubbed and no live I/O."""
    packages = {"execution_dt": EXECUTION_DT, "job_id": "JOB", "pipeline_name": "tax_invoice_extraction"}
    mocker.patch("tasks.tax_invoice_reconcile.helper.task_context.load_yaml", return_value=COMMON_CONFIG)
    return ReconcilePrecheckTask(task_param=task_param or _task_param(), packages=packages)


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


def test_validate_returns_true_when_all_required_keys_present(mocker):
    task = _make_task(mocker)

    assert task.validate() is True


def test_validate_returns_false_when_required_key_missing(mocker):
    param = copy.deepcopy(_task_param())
    del param["sharepoint"]["source_site"]["master_buyer_file"]
    task = _make_task(mocker, task_param=param)

    assert task.validate() is False


# ---------------------------------------------------------------------------
# pre_execute
# ---------------------------------------------------------------------------


def test_pre_execute_initializes_sharepoint_and_notifier(mocker):
    mock_sp = mocker.patch("tasks.tax_invoice_reconcile.precheck_task.init_sharepoint", return_value=mocker.Mock())
    mock_notifier = mocker.patch(
        "tasks.tax_invoice_reconcile.precheck_task.init_email_notifier", return_value=mocker.Mock()
    )
    task = _make_task(mocker)

    task.pre_execute()

    mock_sp.assert_called_once_with("Source", task.ctx.source_site)
    mock_notifier.assert_called_once_with(task.ctx.framework, task.ctx.msgraph_access)
    assert task._sp_source is mock_sp.return_value
    assert task._notifier is mock_notifier.return_value


# ---------------------------------------------------------------------------
# execute_task
# ---------------------------------------------------------------------------


def test_execute_task_all_dependencies_present_returns_none(mocker):
    task = _make_task(mocker)
    task._sp_source = mocker.Mock()
    task._sp_source.list_files_pattern.return_value = ["found.xlsx"]
    task._notifier = mocker.Mock()

    assert task.execute_task() is None
    task._notifier.send_template.assert_not_called()


def test_execute_task_missing_dependency_raises_and_notifies(mocker):
    task = _make_task(mocker)
    task._sp_source = mocker.Mock()

    def list_files_pattern(folder_path, pattern):  # noqa: ARG001
        return [] if pattern == "MB.xlsx" else ["found.xlsx"]

    task._sp_source.list_files_pattern.side_effect = list_files_pattern
    task._notifier = mocker.Mock()

    try:
        task.execute_task()
        raise AssertionError("expected DependencyMissingError")
    except DependencyMissingError as exc:
        assert "Master Buyer file" in str(exc)

    task._notifier.send_template.assert_called_once()
    args, kwargs = task._notifier.send_template.call_args
    assert args[0] == _DEPENDENCY_MISSING_TEMPLATE
    assert kwargs["MISSING_FILES"] == "- Master Buyer file"


# ---------------------------------------------------------------------------
# _missing_dependencies
# ---------------------------------------------------------------------------


def test_missing_dependencies_returns_empty_when_all_present(mocker):
    task = _make_task(mocker)
    task._sp_source = mocker.Mock()
    task._sp_source.list_files_pattern.return_value = ["found.xlsx"]

    assert task._missing_dependencies() == []


def test_missing_dependencies_returns_labels_for_absent_files(mocker):
    task = _make_task(mocker)
    task._sp_source = mocker.Mock()

    def list_files_pattern(folder_path, pattern):  # noqa: ARG001
        return [] if pattern in ("Z.xlsx", "MV.xlsx") else ["found.xlsx"]

    task._sp_source.list_files_pattern.side_effect = list_files_pattern

    assert task._missing_dependencies() == ["Z45 report file", "Master Vendor file"]


# ---------------------------------------------------------------------------
# _notify_missing
# ---------------------------------------------------------------------------


def test_notify_missing_sends_consolidated_email(mocker):
    task = _make_task(mocker)
    task._notifier = mocker.Mock()

    task._notify_missing(["Master Buyer file", "Z45 report file"])

    task._notifier.send_template.assert_called_once()
    args, kwargs = task._notifier.send_template.call_args
    assert args[0] == _DEPENDENCY_MISSING_TEMPLATE
    assert kwargs["sender_email"] == "biz@x.com"
    assert kwargs["receiver_email"] == "biz-to@x.com"
    assert kwargs["MISSING_FILES"] == "- Master Buyer file\n- Z45 report file"


def test_notify_missing_swallows_notifier_exception(mocker):
    task = _make_task(mocker)
    task._notifier = mocker.Mock()
    task._notifier.send_template.side_effect = Exception("graph down")

    task._notify_missing(["Master Buyer file"])  # must not raise


# ---------------------------------------------------------------------------
# on_error
# ---------------------------------------------------------------------------


def test_on_error_dependency_missing_error_does_not_notify(mocker):
    task = _make_task(mocker)
    task._notifier = mocker.Mock()

    task.on_error(DependencyMissingError("Missing required source file(s): Master Buyer file"))

    task._notifier.send_template.assert_not_called()


def test_on_error_generic_exception_sends_processing_failed_notification(mocker):
    task = _make_task(mocker)
    task._notifier = mocker.Mock()

    task.on_error(RuntimeError("boom"))

    task._notifier.send_template.assert_called_once()
    args, kwargs = task._notifier.send_template.call_args
    assert args[0] == _PROCESSING_FAILED_TEMPLATE
    assert kwargs["sender_email"] == "sys@x.com"
    assert kwargs["receiver_email"] == "sys-to@x.com"


def test_on_error_generic_exception_swallows_notifier_failure(mocker):
    task = _make_task(mocker)
    task._notifier = mocker.Mock()
    task._notifier.send_template.side_effect = Exception("graph down")

    task.on_error(RuntimeError("boom"))  # must not raise


# ---------------------------------------------------------------------------
# ctx.recipients / ctx.subject (shared context helpers, exercised via this task's ctx)
# ---------------------------------------------------------------------------


def test_recipients_resolves_case_addresses(mocker):
    task = _make_task(mocker)

    result = task.ctx.recipients(_CASE_BUSINESS)

    assert result == {"sender_email": "biz@x.com", "receiver_email": "biz-to@x.com", "cc_email": None}


def test_recipients_returns_none_addresses_for_unknown_case(mocker):
    task = _make_task(mocker)

    result = task.ctx.recipients("unknown_case")

    assert result == {"sender_email": None, "receiver_email": None, "cc_email": None}


def test_subject_formats_run_date_in_configured_timezone(mocker):
    task = _make_task(mocker)

    local_dt = EXECUTION_DT.astimezone(ZoneInfo("Asia/Bangkok"))
    expected = _SUBJECT_DEPENDENCY_MISSING.format(date=local_dt.strftime("%Y-%m-%d"))

    assert task.ctx.subject(_SUBJECT_DEPENDENCY_MISSING) == expected


def test_subject_uses_processing_failed_template(mocker):
    task = _make_task(mocker)

    local_dt = EXECUTION_DT.astimezone(ZoneInfo("Asia/Bangkok"))
    expected = _SUBJECT_PROCESSING_FAILED.format(date=local_dt.strftime("%Y-%m-%d"))

    assert task.ctx.subject(_SUBJECT_PROCESSING_FAILED) == expected
