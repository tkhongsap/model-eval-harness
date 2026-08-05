"""Batch submitter — build JSONL payloads, upload them, and submit one job per payload."""

from __future__ import annotations

from pathlib import Path

from src.modules.google.gcs import GCSModule
from src.utils.logger import Logger
from tasks.ocr_tax_invoice_pipeline.module.batch_job_client import BatchJobClient
from tasks.ocr_tax_invoice_pipeline.module.payload_builder import PayloadBuilder
from tasks.ocr_tax_invoice_pipeline.schema.contracts import BatchSubmission, ChunkEntry

logger = Logger(__name__)

_PAYLOAD_MIME = "application/json"


class BatchSubmitter:
    """Uploads JSONL payloads and submits one Vertex batch job per payload file.

    A per-job submission failure is captured in the returned :class:`BatchSubmission`
    (``error`` set, ``job`` None) rather than raised, so one bad submission never kills the
    others.
    """

    def __init__(
        self,
        payload_builder: PayloadBuilder,
        batch_client: BatchJobClient,
        payload_gcs: GCSModule,
        output_bucket: str,
        payload_prefix: str,
        output_prefix: str,
        model: str,
        job_id: str,
        dt_suffix: str,
    ) -> None:
        """Initialise the submitter.

        Args:
            payload_builder: Builds JSONL batches from chunk URIs.
            batch_client: Vertex AI batch submission client.
            payload_gcs: GCS module for the payload bucket (JSONL files land here).
            output_bucket: Bucket name where Vertex writes predictions.
            payload_prefix: Bucket-relative prefix for JSONL payloads.
            output_prefix: Bucket-relative prefix for prediction output directories.
            model: Vertex AI model name.
            job_id: Current run's job id (used as the batch display name).
            dt_suffix: ``YYYYMMDDHHMMSS`` suffix for generated JSONL filenames.
        """
        self._payload_builder = payload_builder
        self._batch_client = batch_client
        self._payload_gcs = payload_gcs
        self._output_bucket = output_bucket
        self._payload_prefix = payload_prefix
        self._output_prefix = output_prefix
        self._model = model
        self._job_id = job_id
        self._dt_suffix = dt_suffix

    def run(self, chunks: list[ChunkEntry]) -> list[BatchSubmission]:
        """Build payloads from accepted chunks and submit one job per payload.

        Args:
            chunks: Accepted page chunk entries (URI + parent landing path).

        Returns:
            One :class:`BatchSubmission` per payload (empty list when there are no chunks).
        """
        chunk_uris = [c.gcs_uri for c in chunks]
        if not chunk_uris:
            logger.warning("No IQS-valid pages to submit")
            return []

        uri_to_parent = {c.gcs_uri: c.parent_landing_path for c in chunks}
        batches = self._payload_builder.build_batches(chunk_uris, self._dt_suffix)
        submissions = []
        for payload_name, batch_uris, jsonl_bytes in batches:
            parent_paths = frozenset(uri_to_parent[u] for u in batch_uris)
            submissions.append(self._submit_one(payload_name, jsonl_bytes, parent_paths))
        return submissions

    def _submit_one(self, payload_name: str, jsonl_bytes: bytes, parent_paths: frozenset[str]) -> BatchSubmission:
        """Upload one JSONL payload and submit its job; never raises on submission failure."""
        payload_path = f"{self._payload_prefix.rstrip('/')}/{payload_name}"
        self._payload_gcs.update_content_to_gcs(jsonl_bytes, _PAYLOAD_MIME, payload_path)
        payload_uri = f"gs://{self._payload_gcs.bucket_name}/{payload_path}"
        # Strip the .jsonl suffix so predictions land in a per-batch directory.
        output_uri = f"gs://{self._output_bucket}/{self._output_prefix.rstrip('/')}/{Path(payload_name).stem}"

        try:
            job = self._batch_client.submit(payload_uri, output_uri, self._job_id, self._model)
            return BatchSubmission(payload_name, payload_uri, output_uri, parent_paths, job=job, error=None)
        except Exception as exc:
            logger.error(f"Batch job submission failed for {payload_name}: {exc}", exc_info=True)
            return BatchSubmission(payload_name, payload_uri, output_uri, parent_paths, job=None, error=str(exc))
