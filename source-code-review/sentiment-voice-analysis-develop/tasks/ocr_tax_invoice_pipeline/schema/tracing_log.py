"""Pandera contract for the raw-Gemini tracing log written by ``OCRRetrieveTask``."""

import pandas as pd
import pandera.pandas as pa
from pandera import Field


class TracingLogSchema(pa.DataFrameModel):
    """Append-only raw-Gemini audit log — one row per batch prediction line.

    Captures the raw request / status / response and run-level metadata for every
    line returned by a Vertex AI batch job, kept separate from the business-facing
    pre-processing and page-manifest logs. Persisted SharePoint-only, partitioned into
    one CSV per month (``tracing_log_YYYYMM.csv``); month-files older than the retention
    window (3 months) are deleted on write.
    """

    job_id: pa.typing.Series[str] = Field(nullable=False)
    pipeline_name: pa.typing.Series[str] = Field(nullable=True)
    domain_name: pa.typing.Series[str] = Field(nullable=False)
    gcp_project_id: pa.typing.Series[str] = Field(nullable=False)
    vertexai_project_id: pa.typing.Series[str] = Field(nullable=False)
    batch_inference_location: pa.typing.Series[str] = Field(nullable=False)
    # Nullable: terminally-failed jobs have no model/name to report.
    batch_inference_model_name: pa.typing.Series[str] = Field(nullable=True)
    batch_inference_job_name: pa.typing.Series[str] = Field(nullable=True)
    batch_inference_display_name: pa.typing.Series[str] = Field(nullable=False)
    # Join keys back to the source document/page (parsed from the page URI; null for failed jobs).
    # ``page_no`` is kept as a string like the rest of the persisted CSV (avoids nullable-int / NaN churn).
    source_file_uri: pa.typing.Series[str] = Field(nullable=True)
    page_no: pa.typing.Series[str] = Field(nullable=True)
    # Raw Gemini request (static parts trimmed) / response, stored as JSON strings so they
    # round-trip through CSV; the response is a verbatim passthrough (never validated field-by-field).
    batch_request: pa.typing.Series[str] = Field(nullable=True)
    batch_status: pa.typing.Series[str] = Field(nullable=True)
    batch_response: pa.typing.Series[str] = Field(nullable=True)
    batch_processed_time: pa.typing.Series[str] = Field(nullable=True)  # keep as string to avoid timezone issues
    load_dt: pa.typing.Series[pd.Timestamp] = Field(nullable=False)
    message: pa.typing.Series[str] = Field(nullable=True)

    class Config:
        coerce = True
        strict = False
