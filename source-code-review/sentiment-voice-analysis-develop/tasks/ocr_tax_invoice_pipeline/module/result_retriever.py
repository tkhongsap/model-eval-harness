"""Batch result retriever — pull, validate, and explode Vertex AI predictions.

Locates each succeeded batch job's ``predictions.jsonl`` in GCS, validates every
prediction line against :class:`ReceiptExtraction`, and collects the results into
one exploded DataFrame: one row per invoice/statement line item, with the
document-level fields repeated on each row. Documents with no line items (incl.
``Other``/unsupported and lines that fail validation) still emit a single row so
nothing disappears from the audit trail.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from pydantic import BaseModel, ValidationError

from src.modules.google.gcs import GCSModule
from src.modules.google.gemini_batch import GeminiBatchModule
from src.utils.common import get_value_by_path, recursive_dict_value_by_key, safe_list_get
from src.utils.date_utils import parse_datetime
from src.utils.logger import Logger
from tasks.ocr_tax_invoice_pipeline.helper.constant import OCROutputStatus
from tasks.ocr_tax_invoice_pipeline.helper.messages import STATUS_MESSAGES
from tasks.ocr_tax_invoice_pipeline.module.tracing_builder import TracingLogBuilder
from tasks.ocr_tax_invoice_pipeline.schema.model_response import InvoiceLineItem, ReceiptExtraction

logger = Logger(__name__)

DEFAULT_PREDICTION_FILE = "predictions.jsonl"
# Parse timestamps in UTC; _collect re-normalises to the pipeline timezone downstream.
_UTC = ZoneInfo("UTC")

# Metadata columns prepended to every row, ahead of the schema-derived fields.
META_COLUMNS = (
    "batch_inference_job_name",
    "source_file_uri",
    "start_time",
    "end_time",
    "status",
    "message",
    "usage_metadata",
)


class BatchResultRetriever:
    """Retrieve, validate, and collect Vertex AI batch predictions into a DataFrame.

    Pure retrieve → validate → collect; no orchestration or job polling (the task
    classifies jobs and hands over only the succeeded ones). One module instance
    serves every job in a run.
    """

    def __init__(
        self,
        gemini_batch: GeminiBatchModule,
        gcs_factory: Callable[[str], GCSModule],
        tracing_builder: TracingLogBuilder,
        response_schema: type[BaseModel] = ReceiptExtraction,
        prediction_filename: str = DEFAULT_PREDICTION_FILE,
    ) -> None:
        """Initialise with injected Gemini, a per-bucket GCS resolver, and the schema.

        Args:
            gemini_batch: Initialised :class:`GeminiBatchModule` (used only for its
                JSONL retrieval/parse helper).
            gcs_factory: Maps a bucket name to a cached :class:`GCSModule`; each
                ``job.dest`` may name a different bucket, so predictions are read
                through the module bound to that bucket.
            tracing_builder: Builds the raw-Gemini tracing record for each prediction
                line (or terminally-failed job), independent of schema validation.
            response_schema: Pydantic model each prediction line is validated
                against. Defaults to :class:`ReceiptExtraction`.
            prediction_filename: Object-name suffix of the predictions JSONL.
        """
        self._gemini = gemini_batch
        self._gcs_factory = gcs_factory
        self._schema = response_schema
        self._prediction_filename = prediction_filename
        self.tracing_builder = tracing_builder

        self._doc_fields = [name for name in response_schema.model_fields if name != "line_items"]
        self._item_fields = list(InvoiceLineItem.model_fields)
        self._columns = [*META_COLUMNS, *self._doc_fields, *self._item_fields]

    def retrieve_succeeded(self, succeeded_jobs: list[Any]) -> tuple[pd.DataFrame, list[dict]]:
        """Pull and validate every succeeded job's predictions into one DataFrame.

        Args:
            succeeded_jobs: ``BatchJob`` objects in ``JOB_STATE_SUCCEEDED``.

        Returns:
            One row per line item (document fields repeated), plus one row for each
            document with no line items or that failed validation. Empty (with the
            fixed column set) when there are no jobs or no prediction lines.
        """
        rows: list[dict] = []
        tracing_rows: list[dict] = []
        for idx, job in enumerate(succeeded_jobs, 1):
            display = getattr(job, "display_name", getattr(job, "name", "?"))
            logger.info(f"Retrieving predictions [{idx}/{len(succeeded_jobs)}]: {display}")
            for line in self._load_prediction_lines(job):
                rows.extend(self._rows_for_line(line, job))
                tracing_rows.append(self.tracing_builder.line_to_record(line, job))

        return (pd.DataFrame(rows, columns=self._columns), tracing_rows)

    def retrieve_failed(self, failed_jobs: list[Any]) -> tuple[pd.DataFrame, list[dict]]:
        """Emit one summary row per terminally-failed batch job.

        FAILED/CANCELLED/EXPIRED jobs produce no predictions.jsonl, so each row is
        built straight from job metadata (no GCS read): all document fields null,
        status FAILED, message taken from ``job.error.message``.

        Args:
            failed_jobs: ``BatchJob`` objects in a terminal-failed state.

        Returns:
            One row per job (document/line fields null), or an empty frame with the
            fixed column set when there are no failed jobs.
        """
        rows: list[dict] = []
        tracing_rows: list[dict] = []
        for idx, job in enumerate(failed_jobs, 1):
            display = getattr(job, "display_name", getattr(job, "name", "?"))
            logger.info(f"Recording failed job [{idx}/{len(failed_jobs)}]: {display}")
            error = getattr(job, "error", None)
            message = getattr(error, "message", None)
            rows.append(
                {
                    **self._metadata(
                        job,
                        None,
                        getattr(job, "start_time", None),
                        getattr(job, "end_time", None),
                        OCROutputStatus.FAILED.value,
                        message,
                        None,
                    ),
                    **dict.fromkeys(self._doc_fields),
                }
            )
            tracing_rows.append(self.tracing_builder.line_to_record({}, job))

        return (pd.DataFrame(rows, columns=self._columns), tracing_rows)

    def _load_prediction_lines(self, job: Any) -> list[dict]:
        """Locate and parse one job's ``predictions.jsonl``; ``[]`` on any failure."""
        uri = self._locate_predictions(job)
        if not uri:
            return []
        bucket = self._extract_bucket(uri)
        try:
            return self._gemini.retrieve_batch_results(gcs_module=self._gcs_factory(bucket), batch_output_path=uri)
        except Exception as exc:
            logger.error(f"Failed to retrieve predictions from {uri}: {exc}", exc_info=True)
            return []

    def _locate_predictions(self, job: Any) -> str | None:
        """Find the ``predictions.jsonl`` URI under a job's GCS output ``dest``.

        Returns:
            Full ``gs://bucket/key`` URI, or ``None`` (with WARNING) when the dest
            is missing/malformed, the list call fails, or no matching file exists.
        """
        dest_uri = self._normalize_dest_uri(job)
        if not dest_uri:
            logger.warning(f"Batch job {getattr(job, 'display_name', '?')} has no usable gs:// dest")
            return None
        bucket = self._extract_bucket(dest_uri)
        prefix = dest_uri[len(f"gs://{bucket}/") :]
        try:
            files = self._gcs_factory(bucket).list_files(prefix=prefix)
        except Exception as exc:
            logger.error(f"Failed to list files under {dest_uri}: {exc}", exc_info=True)
            return None
        hits = [name for name in files if name.endswith(self._prediction_filename)]
        if not hits:
            logger.warning(f"No '{self._prediction_filename}' found under {dest_uri}")
            return None
        return f"gs://{bucket}/{hits[0]}"

    def _rows_for_line(self, line: dict, job: Any) -> list[dict]:
        """Validate one prediction line and expand it to one or more flat rows."""
        source_uri = safe_list_get(recursive_dict_value_by_key(data=line, target_key="file_uri"), 0, "")
        # Error/failed lines carry a status but no createTime/processed_time; parse defensively so
        # they reach _validate_line (→ FAILED) instead of crashing the whole task on fromisoformat(None).
        start_time = parse_datetime(
            safe_list_get(recursive_dict_value_by_key(data=line, target_key="createTime"), 0), _UTC
        )
        end_time = parse_datetime(
            safe_list_get(recursive_dict_value_by_key(data=line, target_key="processed_time"), 0), _UTC
        )
        usage_metadata = safe_list_get(recursive_dict_value_by_key(data=line, target_key="usageMetadata"), 0, None)
        if start_time and end_time and start_time > end_time:
            start_time, end_time = end_time, start_time

        ok, message, parsed = self._validate_line(line)
        if not ok:
            logger.error(f"Validation failed for {source_uri!r}: {message}")
            return [
                self._metadata(
                    job, source_uri, start_time, end_time, OCROutputStatus.FAILED.value, message, usage_metadata
                )
            ]

        dump = parsed.model_dump(mode="json")
        items = dump.get("line_items") or []
        status = self._derive_status(dump.get("DOC_TYPE"), items)
        # SUSPICIOUS carries the model's reason; BLANK/UNSUPPORTED a fixed reason; SUCCESS has no message.
        if status == OCROutputStatus.SUSPICIOUS.value:
            message = dump.get("SUSPICIOUS_REASON") or "Suspicious document (possible prompt injection)"
        else:
            message = STATUS_MESSAGES.get(status)
        base = {
            **self._metadata(job, source_uri, start_time, end_time, status, message, usage_metadata),
            **{field: dump.get(field) for field in self._doc_fields},
        }
        if not items:
            return [base]
        return [{**base, **{field: item.get(field) for field in self._item_fields}} for item in items]

    @staticmethod
    def _derive_status(doc_type: str | None, items: list) -> str:
        """Pick the row status from doc type and line-item presence.

        ``Suspicious`` (prompt injection) and ``Other`` (unsupported) are terminal and take
        precedence over ``BLANK`` (both emit no line items). Anything else is ``SUCCESS`` — the
        row was extracted; each consuming domain applies its own validation downstream.
        """
        if doc_type == "Suspicious":
            return OCROutputStatus.SUSPICIOUS.value
        if doc_type == "Other":
            return OCROutputStatus.UNSUPPORTED.value
        if not items:
            return OCROutputStatus.BLANK.value
        return OCROutputStatus.SUCCESS.value

    def _validate_line(self, line: dict) -> tuple[bool, str | None, BaseModel | None]:
        """Run the three-gate per-line validation chain.

        Returns:
            ``(True, None, parsed)`` on success; ``(False, error_message, None)``
            when the batch line carries a status, has no response text, or fails
            schema validation.
        """
        line_status = line.get("status", "")
        if line_status:
            return False, f"batch line status: {line_status}", None
        text = get_value_by_path(line, "response.candidates.0.content.parts.0.text", None)
        if text is None:
            return False, "no prediction text in response", None
        try:
            parsed = self._schema.model_validate_json(text)
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            return False, str(exc), None
        return True, None, parsed

    @staticmethod
    def _metadata(
        job: Any,
        source_uri: str,
        start_time: datetime | None,
        end_time: datetime | None,
        status: str,
        message: str | None,
        usage_metadata: dict | None,
    ) -> dict:
        """Build the metadata cells shared by every row of a prediction line."""
        return {
            "batch_inference_job_name": getattr(job, "name", ""),
            "start_time": start_time,
            "end_time": end_time,
            "source_file_uri": source_uri,
            "status": status,
            "message": message,
            "usage_metadata": usage_metadata,
        }

    @staticmethod
    def _normalize_dest_uri(job: Any) -> str | None:
        """Best-effort ``gs://`` URI from ``job.dest`` (object with ``gcs_uri`` or str)."""
        dest = getattr(job, "dest", None)
        if not dest:
            return None
        uri = getattr(dest, "gcs_uri", None) or str(dest)
        return uri if uri.startswith("gs://") else None

    @staticmethod
    def _extract_bucket(gcs_uri: str) -> str:
        """Extract the bucket name from a ``gs://bucket/key`` URI."""
        return gcs_uri[len("gs://") :].split("/", 1)[0]
