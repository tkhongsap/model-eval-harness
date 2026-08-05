"""Batch job client — submit a JSONL payload to Vertex AI Batch."""

from __future__ import annotations

import time
from typing import Any

from google import genai

from src.modules.google.gemini_batch import GeminiBatchModule
from src.utils.logger import Logger

logger = Logger(__name__)

_TERMINAL_BAD_STATES = frozenset({"JOB_STATE_FAILED", "JOB_STATE_CANCELLED", "JOB_STATE_EXPIRED"})


class BatchJobClient:
    """Thin wrapper around :class:`GeminiBatchModule` for payload submission.

    Submits one JSONL file per call and polls the initial status to surface
    terminal-bad states at submission time rather than hours later.
    """

    def __init__(self, gemini_batch: GeminiBatchModule, poll_delay_seconds: int = 2) -> None:
        """Initialise the client.

        Args:
            gemini_batch: Initialised :class:`GeminiBatchModule` instance.
            poll_delay_seconds: Seconds to wait before polling initial job
                status.  A short delay lets Vertex AI register the job.
        """
        self._gemini = gemini_batch
        self._poll_delay = poll_delay_seconds

    def submit(
        self,
        payload_gcs_uri: str,
        output_gcs_uri: str,
        job_name: str,
        model: str,
    ) -> Any:
        """Submit a batch job and verify it is not immediately terminal-bad.

        Args:
            payload_gcs_uri: Full GCS URI (``gs://...``) of the JSONL payload.
            output_gcs_uri: Full GCS URI (``gs://...``) where Vertex AI should
                write predictions.
            job_name: Display name for the batch job.
            model: Vertex AI model name (e.g. ``gemini-2.0-flash``).

        Returns:
            The Vertex AI BatchJob object returned by the API.

        Raises:
            Exception: If job creation fails or the initial status is a
                terminal-bad state (FAILED / CANCELLED / EXPIRED).
        """
        logger.info(f"Submitting batch job: {job_name}")
        job = self._gemini.create_batch_job(
            model_nm=model,
            src_uri=payload_gcs_uri,
            config={"dest": output_gcs_uri, "display_name": job_name},
        )
        logger.info(f"Batch job created: {job.name}")
        self._verify_initial_status(job)
        return job

    def _verify_initial_status(self, job: Any) -> None:
        """Poll once after a short delay and raise on terminal-bad states.

        Args:
            job: The Vertex AI BatchJob object just created.

        Raises:
            Exception: When the polled status is in :data:`_TERMINAL_BAD_STATES`.
        """
        time.sleep(self._poll_delay)
        status = self._gemini.status_check_batch_job(job_name=job.name)
        if status in _TERMINAL_BAD_STATES:
            raise Exception(f"Batch job {job.name!r} reached terminal-bad state immediately: {status}")
        logger.info(f"Batch job initial status: {status}")

    def pull_job_detail(self, job_name: str) -> genai.types.BatchJob:
        """Fetch the current full BatchJob detail for ``job_name``.

        Args:
            job_name: The Vertex AI batch job resource name to poll.

        Returns:
            The current :class:`genai.types.BatchJob`, including its ``state``, ``dest``,
            and (when terminally failed) ``error``.
        """
        return self._gemini.pull_batch_job(job_name=job_name)
