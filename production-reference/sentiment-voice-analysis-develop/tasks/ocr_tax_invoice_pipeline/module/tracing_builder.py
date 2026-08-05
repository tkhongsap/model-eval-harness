"""Tracing-log row construction — raw Gemini batch request/response audit trail."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from pandera.errors import SchemaErrors

from src.utils.common import recursive_dict_value_by_key, resolve_env, safe_list_get
from src.utils.logger import Logger
from tasks.ocr_tax_invoice_pipeline.helper.task_context import OCRTaskContext
from tasks.ocr_tax_invoice_pipeline.schema.tracing_log import TracingLogSchema

logger = Logger(__name__)

# Request keys stripped before persistence — identical on every row and dominate row size
# (system prompt + full response JSON-schema). Removed recursively, wherever they nest.
_STRIP_REQUEST_KEYS = ("system_instruction", "response_schema")
# Per-page files are named ``{stem}_p{page_no:03d}.pdf`` (DocumentProcessor); pull the page number back out.
_PAGE_NO_PATTERN = re.compile(r"_p(\d+)\.")


@dataclass(frozen=True)
class TracingLogContext:
    """Resolved, per-run constants stamped onto every tracing-log row."""

    job_id: str
    pipeline_name: str
    domain_name: str
    gcp_project_id: str
    vertexai_project_id: str
    batch_inference_location: str
    timezone: ZoneInfo

    @classmethod
    def from_task_context(cls, ctx: OCRTaskContext) -> TracingLogContext:
        """Build from an :class:`OCRTaskContext`, env-resolving the project/location fields."""
        return cls(
            job_id=ctx.job_id,
            pipeline_name=ctx.pipeline_name,
            domain_name=ctx.domain,
            gcp_project_id=resolve_env(ctx.gcp.get("project_id", "")),
            vertexai_project_id=resolve_env(ctx.vertexai.get("project_id", "")),
            batch_inference_location=resolve_env(ctx.vertexai.get("location", "")),
            timezone=ctx.timezone,
        )


class TracingLogBuilder:
    """Builds ``TracingLogSchema`` rows from raw Vertex AI batch prediction lines."""

    def __init__(self, context: TracingLogContext) -> None:
        """Initialise with the resolved per-run :class:`TracingLogContext`."""
        self._ctx = context

    def line_to_record(self, line: dict, job: Any) -> dict:
        """Build one tracing record from a raw prediction *line* and its batch *job*.

        The request is trimmed of its static parts (system prompt + response schema) and
        both request and response are serialised to JSON strings so they round-trip through
        CSV; the response is a verbatim passthrough, so a model-output change never breaks
        tracing. For terminally-failed jobs ``line`` is ``{}`` and the per-line fields come
        back ``None``. ``load_dt`` is stamped later in :meth:`build_tracing_log` so every
        row in a run shares one timestamp.
        """
        source_uri = self._extract_source_uri(line)
        return {
            "job_id": self._ctx.job_id,
            "pipeline_name": self._ctx.pipeline_name,
            "domain_name": self._ctx.domain_name,
            "gcp_project_id": self._ctx.gcp_project_id,
            "vertexai_project_id": self._ctx.vertexai_project_id,
            "batch_inference_location": self._ctx.batch_inference_location,
            "batch_inference_model_name": getattr(job, "model", None),
            "batch_inference_job_name": getattr(job, "name", None),
            "batch_inference_display_name": getattr(job, "display_name", None),
            "source_file_uri": source_uri,
            "page_no": self._page_no_from_uri(source_uri),
            "batch_request": self._to_json(self._trim_request(line.get("request"))),
            "batch_status": line.get("status"),
            "batch_response": self._to_json(line.get("response")),
            "batch_processed_time": line.get("processed_time"),
            "message": getattr(getattr(job, "error", None), "message", None),
        }

    @staticmethod
    def _extract_source_uri(line: dict) -> str | None:
        """Pull the source page's ``file_uri`` out of the raw line (``None`` when absent)."""
        return safe_list_get(recursive_dict_value_by_key(data=line, target_key="file_uri"), 0, None)

    @staticmethod
    def _page_no_from_uri(uri: str | None) -> str | None:
        """Parse the 1-based page number from a ``..._pNNN.pdf`` page URI (``None`` when absent).

        Returned as a leading-zero-stripped string (``"3"``) to match the all-string persisted CSV.
        """
        if not uri:
            return None
        match = _PAGE_NO_PATTERN.search(uri)
        return str(int(match.group(1))) if match else None

    @staticmethod
    def _trim_request(request: Any) -> Any:
        """Return a copy of *request* with the static, per-row-identical keys stripped.

        Drops ``system_instruction`` and ``response_schema`` wherever they nest, keeping the
        file URI and generation params. Non-dict inputs pass through unchanged.
        """
        if isinstance(request, dict):
            return {k: TracingLogBuilder._trim_request(v) for k, v in request.items() if k not in _STRIP_REQUEST_KEYS}
        if isinstance(request, list):
            return [TracingLogBuilder._trim_request(v) for v in request]
        return request

    @staticmethod
    def _to_json(obj: Any) -> str | None:
        """Serialise *obj* to a JSON string (``None`` passes through as ``None``)."""
        return json.dumps(obj, ensure_ascii=False) if obj is not None else None

    def build_tracing_log(self, tracing_rows: list[dict]) -> pd.DataFrame:
        """Assemble tracing rows into a load_dt-stamped, schema-validated DataFrame.

        Stamps ``load_dt`` as a tz-aware :class:`pandas.Timestamp` in the system
        timezone, then soft-validates (log-don't-crash). Returns an empty frame when
        there are no rows.
        """
        if not tracing_rows:
            return pd.DataFrame()
        df = pd.DataFrame(tracing_rows)
        df["load_dt"] = pd.Timestamp.now(tz=self._ctx.timezone)
        self._validate_soft(df)
        return df

    @staticmethod
    def _validate_soft(df: pd.DataFrame) -> None:
        """Validate against TracingLogSchema without aborting (log-don't-crash)."""
        try:
            TracingLogSchema.validate(df, lazy=True)
        except SchemaErrors as exc:
            logger.warning(f"tracing rows failed schema validation (writing anyway): {exc}")
