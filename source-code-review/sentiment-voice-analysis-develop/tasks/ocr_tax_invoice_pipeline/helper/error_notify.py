"""Best-effort, config-driven system-error email for the OCR-pipeline tasks.

Wired into each OCR task's ``on_error`` hook. Sending is gated by a per-task
``framework.notifications.system_exception`` config block: the subject comes from config and the
body is either an inline string (``body``) or the contents of a ``.txt`` file (``body_path``).
Microsoft Graph credentials are read from the shared ``common.yml msgraph`` block (carried on the
:class:`OCRTaskContext`). Every failure here is swallowed — a notification problem must never mask
or replace the original task error the framework is about to re-raise.
"""

from __future__ import annotations

from datetime import datetime

from src.modules.microsoft.msgraph import MSGraphModule
from src.utils.common import resolve_env
from src.utils.file_utils import read_file
from src.utils.logger import Logger
from tasks.ocr_tax_invoice_pipeline.helper.task_context import OCRTaskContext

logger = Logger(__name__)

_DEFAULT_SUBJECT = "[OCR Pipeline][System Exception] {task} on {date}"


def notify_system_error(ctx: OCRTaskContext, task_name: str, error: Exception) -> None:
    """Send a system-error email when enabled in config (best-effort, never raises).

    Args:
        ctx: The task's per-run context (notification config + Graph credentials).
        task_name: The failing task's name (substituted into subject/body as ``{task}``).
        error: The exception that aborted the task (substituted as ``{error}``).
    """
    cfg = (ctx.notifications or {}).get("system_exception") or {}
    if not _enabled(cfg):
        return
    try:
        placeholders = {
            "task": task_name,
            "pipeline": ctx.pipeline_name or "",
            "date": _run_date(ctx),
            "error": str(error),
        }
        msgraph = MSGraphModule(
            client_id=resolve_env(ctx.msgraph_access.get("client_id")),
            client_secret=resolve_env(ctx.msgraph_access.get("client_secret")),
            tenant_id=resolve_env(ctx.msgraph_access.get("tenant_id")),
        )
        msgraph.send_email(
            subject=_subject(cfg, placeholders),
            body=_body(cfg, placeholders),
            **_recipients(cfg, ctx.msgraph_access),
        )
        logger.info(f"Sent system-error notification for {task_name}.")
    except Exception as exc:
        logger.error(f"System-error notification failed to send: {exc}", exc_info=True)


def _enabled(cfg: dict) -> bool:
    """Return True only when a notification block exists and is not explicitly disabled."""
    return bool(cfg) and bool(cfg.get("enabled", True))


def _run_date(ctx: OCRTaskContext) -> str:
    """Return the run date as ``YYYY-MM-DD`` in the configured timezone."""
    dt = ctx.execution_dt or datetime.now(tz=ctx.timezone)
    local_dt = dt.astimezone(ctx.timezone) if dt.tzinfo else dt
    return local_dt.strftime("%Y-%m-%d")


def _subject(cfg: dict, placeholders: dict) -> str:
    """Resolve the configured subject (env + placeholders), defaulting when absent."""
    subject = resolve_env(cfg.get("subject")) or _DEFAULT_SUBJECT
    return _safe_format(subject, placeholders)


def _body(cfg: dict, placeholders: dict) -> str:
    """Resolve the body from ``body_path`` (file) or inline ``body``, then render as HTML."""
    body_path = resolve_env(cfg.get("body_path"))
    text = read_file(body_path) if body_path else (cfg.get("body", "") or "")
    return _safe_format(text, placeholders).replace("\n", "<br>\n")


def _recipients(cfg: dict, msgraph_access: dict) -> dict[str, str | None]:
    """Resolve from/to/cc, falling back to the shared ``msgraph`` defaults when omitted."""
    return {
        "sender_email": resolve_env(cfg.get("sender_email") or msgraph_access.get("sender_email")),
        "receiver_email": resolve_env(cfg.get("receiver_email") or msgraph_access.get("receiver_email")),
        "cc_email": resolve_env(cfg.get("cc_email") or msgraph_access.get("cc_email")),
    }


def _safe_format(text: str, placeholders: dict) -> str:
    """Fill ``{name}`` placeholders, falling back to the raw text on any format error.

    Guards against stray/literal braces in a configured subject or body file that would
    otherwise raise from :meth:`str.format`.
    """
    try:
        return text.format(**placeholders)
    except (KeyError, IndexError, ValueError) as exc:
        logger.warning(f"Notification template formatting failed ({exc}); sending raw text.")
        return text
