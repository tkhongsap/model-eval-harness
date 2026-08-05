"""Tests for the config-driven OCR system-error email (``notify_system_error``).

The helper is best-effort: it sends only when a ``system_exception`` block is present and not
disabled, fills ``{task}``/``{pipeline}``/``{date}``/``{error}`` placeholders in the subject and
body, resolves the body from an inline string or a ``.txt`` file, and swallows every transport
error so it never masks the original task failure. MSGraph is mocked at the boundary.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from tasks.ocr_tax_invoice_pipeline.helper.error_notify import notify_system_error

_TZ = ZoneInfo("Asia/Bangkok")
_EXEC_DT = datetime(2026, 6, 10, tzinfo=ZoneInfo("UTC"))


def _ctx(notifications: dict) -> SimpleNamespace:
    """Build a minimal context stub exposing only what the helper reads."""
    return SimpleNamespace(
        notifications=notifications,
        msgraph_access={"client_id": "cid", "client_secret": "csec", "tenant_id": "tid"},
        pipeline_name="tax_invoice_extraction",
        execution_dt=_EXEC_DT,
        timezone=_TZ,
    )


def _enabled_block(**overrides) -> dict:
    block = {
        "enabled": True,
        "sender_email": "bot@x.com",
        "receiver_email": "oper@x.com",
        "cc_email": "dev@x.com",
        "subject": "[OCR][System Exception] {task} on {date}",
        "body": "Task {task} in {pipeline} failed on {date}.\nError: {error}",
    }
    block.update(overrides)
    return {"system_exception": block}


def test_enabled_inline_body_sends_formatted_email(mocker):
    # Arrange
    instance = mocker.Mock()
    msgraph = mocker.patch("tasks.ocr_tax_invoice_pipeline.helper.error_notify.MSGraphModule", return_value=instance)
    ctx = _ctx(_enabled_block())

    # Act
    notify_system_error(ctx, "OCRRetrieveTask", ValueError("boom"))

    # Assert — credentials wired, placeholders substituted, newlines rendered as HTML <br>.
    msgraph.assert_called_once_with(client_id="cid", client_secret="csec", tenant_id="tid")
    kwargs = instance.send_email.call_args.kwargs
    assert kwargs["subject"] == "[OCR][System Exception] OCRRetrieveTask on 2026-06-10"
    assert "Task OCRRetrieveTask in tax_invoice_extraction failed on 2026-06-10." in kwargs["body"]
    assert "Error: boom" in kwargs["body"]
    assert "<br>" in kwargs["body"]
    assert kwargs["sender_email"] == "bot@x.com"
    assert kwargs["receiver_email"] == "oper@x.com"
    assert kwargs["cc_email"] == "dev@x.com"


def test_disabled_block_does_not_send(mocker):
    # Arrange
    msgraph = mocker.patch("tasks.ocr_tax_invoice_pipeline.helper.error_notify.MSGraphModule")
    ctx = _ctx(_enabled_block(enabled=False))

    # Act
    notify_system_error(ctx, "OCRSubmitTask", RuntimeError("x"))

    # Assert
    msgraph.assert_not_called()


def test_absent_block_does_not_send(mocker):
    # Arrange
    msgraph = mocker.patch("tasks.ocr_tax_invoice_pipeline.helper.error_notify.MSGraphModule")
    ctx = _ctx({})  # no system_exception case

    # Act
    notify_system_error(ctx, "OCRFinalizeTask", RuntimeError("x"))

    # Assert
    msgraph.assert_not_called()


def test_body_path_is_read_from_file(mocker):
    # Arrange
    instance = mocker.Mock()
    mocker.patch("tasks.ocr_tax_invoice_pipeline.helper.error_notify.MSGraphModule", return_value=instance)
    read_file = mocker.patch(
        "tasks.ocr_tax_invoice_pipeline.helper.error_notify.read_file", return_value="FROM FILE: {task} / {error}"
    )
    block = _enabled_block(body_path="config/x/ocr_system_error.txt")
    block["system_exception"].pop("body")
    ctx = _ctx(block)

    # Act
    notify_system_error(ctx, "OCRRetrieveTask", ValueError("kaboom"))

    # Assert
    read_file.assert_called_once_with("config/x/ocr_system_error.txt")
    assert "FROM FILE: OCRRetrieveTask / kaboom" in instance.send_email.call_args.kwargs["body"]


def test_recipients_fall_back_to_msgraph_defaults(mocker):
    # Arrange
    instance = mocker.Mock()
    mocker.patch("tasks.ocr_tax_invoice_pipeline.helper.error_notify.MSGraphModule", return_value=instance)
    block = _enabled_block()
    for key in ("sender_email", "receiver_email", "cc_email"):
        block["system_exception"].pop(key)
    ctx = _ctx(block)
    ctx.msgraph_access.update({"sender_email": "fallback-bot@x.com", "receiver_email": "fallback-oper@x.com"})

    # Act
    notify_system_error(ctx, "OCRRetrieveTask", ValueError("x"))

    # Assert
    kwargs = instance.send_email.call_args.kwargs
    assert kwargs["sender_email"] == "fallback-bot@x.com"
    assert kwargs["receiver_email"] == "fallback-oper@x.com"


def test_send_failure_is_swallowed(mocker):
    # Arrange — MSGraph construction blows up; the helper must not propagate it.
    mocker.patch(
        "tasks.ocr_tax_invoice_pipeline.helper.error_notify.MSGraphModule", side_effect=Exception("graph down")
    )
    ctx = _ctx(_enabled_block())

    # Act / Assert — no exception escapes.
    notify_system_error(ctx, "OCRRetrieveTask", ValueError("x"))


def test_stray_braces_in_body_fall_back_to_raw_text(mocker):
    # Arrange — a literal brace would break str.format; helper must degrade gracefully.
    instance = mocker.Mock()
    mocker.patch("tasks.ocr_tax_invoice_pipeline.helper.error_notify.MSGraphModule", return_value=instance)
    ctx = _ctx(_enabled_block(body="Has a stray {brace} and {task}"))

    # Act
    notify_system_error(ctx, "OCRRetrieveTask", ValueError("x"))

    # Assert — raw text is sent unformatted rather than raising.
    assert "Has a stray {brace} and {task}" in instance.send_email.call_args.kwargs["body"]
