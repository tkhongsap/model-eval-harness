"""Emit fact-check metric rows as structured ``AI-Operation Fact Check log`` lines.

Each metric row (per field, plus the ``overall`` aggregate) is emitted through the shared
:func:`logging_ai_operation` util with ``log_type="fact_check"`` — the same channel the
telesale/QA/reconcile pipelines use for their AI-operation summaries — so it lands in Cloud
Logging as one JSON line with the metrics under the top-level ``data`` payload.
"""

from __future__ import annotations

from src.utils.common import logging_ai_operation
from src.utils.logger import Logger
from tasks.tax_invoice_reconcile.helper.constant import (
    FACT_CHECK_LOG_MESSAGE,
    FACT_CHECK_LOG_TYPE,
)

logger = Logger(__name__)


def emit_fact_check_logs(
    metric_rows: list[dict],
    created_datetime: str,
    processed_datetime: str | None,
    gcp_project_id: str,
) -> None:
    """Emit each metric row as an ``AI-Operation Fact Check log`` JSON line.

    Args:
        metric_rows: Per-field + overall dicts (``label``/``accuracy``/``precision``/
            ``recall``/``f1_score``) from :class:`FactCheckEvaluator`.
        created_datetime: The run start datetime string.
        processed_datetime: The Gemini batch processed time (most common across the run's
            predictions), or ``None`` when no prediction carried one.
        gcp_project_id: The GCP project id for the run.
    """
    if not metric_rows:
        logger.info("No fact-check metric rows to emit.")
        return
    for row in metric_rows:
        log_obj = {
            "created_datetime": created_datetime,
            "processed_datetime": processed_datetime,
            "gcp_project_id": gcp_project_id,
            "label": row.get("label"),
            "accuracy": row.get("accuracy"),
            "precision": row.get("precision"),
            "recall": row.get("recall"),
            "f1_score": row.get("f1_score"),
        }
        logging_ai_operation(
            log_instance=logger,
            log_obj=log_obj,
            log_type=FACT_CHECK_LOG_TYPE,
            message=FACT_CHECK_LOG_MESSAGE,
        )
    logger.info(f"Emitted {len(metric_rows)} fact-check log line(s).")
