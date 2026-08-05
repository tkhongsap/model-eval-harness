"""Pandera contracts for the two append-only CSV logs written by ``OCRSubmitTask``."""

import pandera.pandas as pa
from pandera import Field


class PreProcessingLogSchema(pa.DataFrameModel):
    """Append-only transaction log — one row per source-file status transition."""

    job_id: pa.typing.Series[str] = Field(nullable=False)
    pipeline_name: pa.typing.Series[str] = Field(nullable=True)
    domain_name: pa.typing.Series[str] = Field(nullable=False)
    sharepoint_input_path: pa.typing.Series[str] = Field(nullable=False)
    sharepoint_web_url: pa.typing.Series[str] = Field(nullable=False)
    gcp_project_id: pa.typing.Series[str] = Field(nullable=False)
    gcs_project_id: pa.typing.Series[str] = Field(nullable=False)
    gcs_landing_path: pa.typing.Series[str] = Field(nullable=False)
    gcs_payload_path: pa.typing.Series[str] = Field(nullable=False)
    vertexai_project_id: pa.typing.Series[str] = Field(nullable=False)
    batch_inference_location: pa.typing.Series[str] = Field(nullable=False)
    batch_inference_model_name: pa.typing.Series[str] = Field(nullable=False)
    batch_inference_job_name: pa.typing.Series[str] = Field(nullable=False)
    batch_inference_display_name: pa.typing.Series[str] = Field(nullable=False)
    batch_inference_output_path: pa.typing.Series[str] = Field(nullable=False)
    status: pa.typing.Series[str] = Field(nullable=False)
    load_dt: pa.typing.Series[str] = Field(nullable=False)
    update_dt: pa.typing.Series[str] = Field(nullable=False)
    datadate: pa.typing.Series[str] = Field(nullable=False)
    message: pa.typing.Series[str] = Field(nullable=True)

    class Config:
        coerce = True
        strict = False


class PageManifestLogSchema(pa.DataFrameModel):
    """One row per page — tracks IQS scores and GCS chunk placement.

    ``parent_path`` is the GCS landing-file URI of the source document
    (``gs://.../ocr_landing/.../JOB_ID/<filename>``), not the SharePoint path —
    join to ``PreProcessingLogSchema.gcs_landing_path`` to recover the
    SharePoint source via ``sharepoint_input_path``.
    """

    job_id: pa.typing.Series[str] = Field(nullable=False)
    pipeline_name: pa.typing.Series[str] = Field(nullable=True)
    parent_path: pa.typing.Series[str] = Field(nullable=False)
    parent_total_pages: pa.typing.Series[int] = Field(nullable=False)
    page_no: pa.typing.Series[int] = Field(nullable=False)
    child_path: pa.typing.Series[str] = Field(nullable=False)
    iqs_score: pa.typing.Series[float] = Field(nullable=False)
    vq_score: pa.typing.Series[float] = Field(nullable=False)
    sq_score: pa.typing.Series[float] = Field(nullable=False)
    ct_score: pa.typing.Series[float] = Field(nullable=False)
    quality_status: pa.typing.Series[str] = Field(nullable=False)
    message: pa.typing.Series[str] = Field(nullable=True)

    class Config:
        coerce = True
        strict = False
