from __future__ import annotations

# Library imports
import re
import time
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

import google.auth
from google import genai
from google.genai import types
from google.genai.errors import APIError

# Source code imports
from src.hook.tls import TlsPolicy
from src.logger import Logger

logger = Logger.get_logger(__name__)


class VertexAIBatchInferenceError(Exception):
    """Base for every Vertex AI batch-inference failure raised by this module."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        """Initialize the error.

        Args:
            message: Human-readable description of the failure.
            status_code: HTTP status that produced it, when the failure came from the API.
        """
        super().__init__(message)
        self.status_code = status_code


class VertexAIBatchAuthError(VertexAIBatchInferenceError):
    """Credentials were rejected (401) or lack the required IAM role (403)."""


class VertexAIBatchNotFoundError(VertexAIBatchInferenceError):
    """The batch job, the model, or the source object does not exist (404)."""


class VertexAIBatchQuotaError(VertexAIBatchInferenceError):
    """The request was throttled or a quota is exhausted (429)."""


class VertexAIBatchTimeoutError(VertexAIBatchInferenceError):
    """``wait_for_batch_job`` gave up before the job reached a terminal state.

    Not an API status, so ``status_code`` stays ``None``. It is a distinct type because "still
    running" and "failed" are different outcomes and the caller must be able to tell them apart.
    """


# Dispatch on the HTTP status rather than on exception class. google-genai's hierarchy is only two
# levels deep (ClientError/ServerError under APIError), so it carries no information a status does
# not -- and an except-chain would have to stay ordered most-specific-first, which a later edit
# breaks silently. A status lookup has no ordering to get wrong.
_ERROR_BY_STATUS: dict[int, type[VertexAIBatchInferenceError]] = {
    401: VertexAIBatchAuthError,
    403: VertexAIBatchAuthError,
    404: VertexAIBatchNotFoundError,
    429: VertexAIBatchQuotaError,
}

# The Vertex resource is batchPredictionJobs -- NOT batchJobs. google.genai._transformers
# .t_batch_job_name validates against exactly this shape and raises ValueError on anything else,
# and every path template in batches.py is "batchPredictionJobs/{name}".
_RESOURCE_NAME_PATTERN = re.compile(
    r"^projects/[^/]+/locations/[^/]+/batchPredictionJobs/[^/]+$"
)


class VertexAIBatchInference:
    """
    Module for submitting and tracking Vertex AI batch inference jobs.

    Vertex-only by design. The Gemini Developer API (api_key auth) rejects a GCS or BigQuery
    source outright -- it accepts only ``files/...`` or inlined requests -- so the batch contract
    this module implements does not exist there. Authentication is Application Default
    Credentials.

    Results are not downloaded here: ``pull_batch_job_results`` returns the ``gs://`` output
    directory and the caller fetches the JSONL shards (``GCSModule`` in
    :mod:`src.hook.gcp_gcs` does that job).
    """

    DEFAULT_API_VERSION = "v1"

    # "global" is supported for batch inference with standard Gemini models, and routes to
    # https://aiplatform.googleapis.com/ instead of a regional host. It is NOT supported for
    # *tuned* models -- those need a real region such as "us-central1".
    DEFAULT_LOCATION = "global"

    # Seconds at this module's boundary, converted to milliseconds once in _build_http_options.
    # HttpOptions.timeout is milliseconds (types.py documents it, _api_client divides by 1000),
    # so a caller who passes 60 expecting seconds would otherwise get a 60ms timeout.
    DEFAULT_TIMEOUT = 60.0

    # Seconds between wait_for_batch_job polls, and the point at which it gives up. Batch jobs
    # routinely run for tens of minutes, so the ceiling is an hour rather than a few minutes.
    DEFAULT_POLL_INTERVAL = 30.0
    DEFAULT_POLL_TIMEOUT = 3600.0

    # The scope google-genai itself resolves ADC with (_api_client.py). Matching it means the
    # credentials built here are interchangeable with the ones the client would have built.
    CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"

    # JOB_STATE_PARTIALLY_SUCCEEDED is deliberately included. It is terminal, but the SDK's own
    # BatchJob.done tests membership in JOB_STATES_ENDED, which omits it -- so a loop polling on
    # that property never exits for a partially-succeeded job. Never use .done here.
    TERMINAL_STATES = frozenset(
        {
            types.JobState.JOB_STATE_SUCCEEDED,
            types.JobState.JOB_STATE_PARTIALLY_SUCCEEDED,
            types.JobState.JOB_STATE_FAILED,
            types.JobState.JOB_STATE_CANCELLED,
            types.JobState.JOB_STATE_EXPIRED,
        }
    )

    # States whose output is readable. PARTIALLY_SUCCEEDED still wrote rows for the requests that
    # did succeed, so its destination is worth reading.
    READABLE_STATES = frozenset(
        {
            types.JobState.JOB_STATE_SUCCEEDED,
            types.JobState.JOB_STATE_PARTIALLY_SUCCEEDED,
        }
    )

    def __init__(self, **kwargs) -> None:
        """
        Initialize the Vertex AI batch-inference client.

        Args:
            project_id (str, optional): GCP project ID. Falls back to the project ADC reports
            location (str, optional): Vertex location. Defaults to DEFAULT_LOCATION
            timeout (float, optional): Per-request timeout in seconds. Defaults to DEFAULT_TIMEOUT
            poll_interval (float, optional): Seconds between polls in ``wait_for_batch_job``
            poll_timeout (float, optional): Seconds before ``wait_for_batch_job`` gives up
            api_version (str, optional): Vertex API version. Defaults to DEFAULT_API_VERSION
            http_options (types.HttpOptions | dict, optional): Overrides. Copied before use

        Raises:
            VertexAIBatchInferenceError: If no project ID is given and ADC reports none
            google.auth.exceptions.DefaultCredentialsError: If no credentials can be resolved
        """
        started = time.monotonic()

        self.location = kwargs.get("location", self.DEFAULT_LOCATION)
        self._timeout = kwargs.get("timeout", self.DEFAULT_TIMEOUT)
        self._poll_interval = kwargs.get("poll_interval", self.DEFAULT_POLL_INTERVAL)
        self._poll_timeout = kwargs.get("poll_timeout", self.DEFAULT_POLL_TIMEOUT)

        # Resolving credentials here rather than letting genai.Client do it serves three purposes:
        # it is the module's fail-fast check (DefaultCredentialsError raises immediately when ADC
        # is unconfigured), it yields the ADC project so callers on Cloud Run need not pass one,
        # and passing the result to the client stops it resolving ADC a second time.
        credentials, detected_project = google.auth.default(scopes=[self.CLOUD_PLATFORM_SCOPE])

        self.project_id = kwargs.get("project_id") or detected_project

        if not self.project_id:
            message = "Missing required arguments: project_id (and ADC reported no project)."
            logger.error("vertex_batch.config.invalid", reason=message)
            raise VertexAIBatchInferenceError(message)

        self.http_options = self._build_http_options(
            kwargs.get("http_options"), kwargs.get("api_version", self.DEFAULT_API_VERSION)
        )

        # vertexai=True is the documented spelling; the SDK's newer name for the same flag is
        # enterprise=. Passing both with conflicting values raises ValueError, so pass only one.
        self.client = genai.Client(
            vertexai=True,
            project=self.project_id,
            location=self.location,
            credentials=credentials,
            http_options=self.http_options,
        )

        logger.debug(
            "vertex_batch.config.resolved",
            project_id=self.project_id,
            location=self.location,
            api_version=self.http_options.api_version,
            timeout=self._timeout,
            poll_interval=self._poll_interval,
            poll_timeout=self._poll_timeout,
        )

        logger.info(
            "vertex_batch.connected",
            project_id=self.project_id,
            location=self.location,
            elapsed_ms=self._elapsed_ms(started),
        )

    @staticmethod
    def _elapsed_ms(started: float) -> float:
        """Milliseconds since a ``time.monotonic()`` reading, rounded for logging."""
        return round((time.monotonic() - started) * 1000, 1)

    def _build_http_options(
        self, provided: types.HttpOptions | dict[str, Any] | None, api_version: str
    ) -> types.HttpOptions:
        """Build the client's HTTP options with a TLS floor and a timeout in the SDK's unit.

        A caller-supplied ``HttpOptions`` is copied before it is touched: the fields below are
        assigned in place, so mutating the original would leak this client's TLS context into any
        other client built from the same object.

        ``client_args`` and ``async_client_args`` are forwarded verbatim to ``httpx.Client(...)``,
        which accepts an ``ssl.SSLContext`` as its ``verify`` argument -- that is how the TLS 1.2+
        floor reaches the transport. Existing keys are preserved.

        Args:
            provided (types.HttpOptions | dict | None): Caller overrides, if any
            api_version (str): Version to use when the caller did not pin one

        Returns:
            types.HttpOptions: Options safe to hand to ``genai.Client``
        """
        if provided is None:
            options = types.HttpOptions(api_version=api_version)
        elif isinstance(provided, dict):
            options = types.HttpOptions(**provided)
        else:
            options = provided.model_copy(deep=True)

        if options.api_version is None:
            options.api_version = api_version

        # HttpOptions.timeout is MILLISECONDS; self._timeout is seconds.
        if options.timeout is None:
            options.timeout = int(self._timeout * 1000)

        tls_context = TlsPolicy().context()
        options.client_args = {**(options.client_args or {}), "verify": tls_context}
        options.async_client_args = {**(options.async_client_args or {}), "verify": tls_context}

        return options

    @contextmanager
    def _translate_errors(self, context: str, **fields: Any) -> Generator[None]:
        """
        Map google-genai API errors onto this module's exception hierarchy.

        This is the single ERROR site for API faults -- outer handlers must not log again, or
        every fault produces two records. ``APIError`` is the base of both ``ClientError`` (4xx)
        and ``ServerError`` (5xx), so one clause covers the SDK's whole API surface.

        ``exc.details`` is deliberately not logged: it is the raw response body, the one field
        that can echo request content back into the log.

        Args:
            context (str): What was being attempted, for the message.
            **fields: Structured fields to attach to the error record.

        Yields:
            None

        Raises:
            VertexAIBatchAuthError: On 401 or 403.
            VertexAIBatchNotFoundError: On 404.
            VertexAIBatchQuotaError: On 429.
            VertexAIBatchInferenceError: On any other API failure.
        """
        try:
            yield
        except APIError as exc:
            status = exc.code
            message = f"{context} failed: {status} {exc.status} {exc.message}"
            logger.error(
                "vertex_batch.request.failed",
                context=context,
                status=status,
                reason=exc.status,
                **fields,
            )
            raise _ERROR_BY_STATUS.get(status, VertexAIBatchInferenceError)(
                message, status_code=status
            ) from exc

    def get_batch_job_name(self, job_id: str) -> str:
        """
        Build the full resource name for a batch job.

        Idempotent: a value that is already a full resource name is returned unchanged, so a
        ``job.name`` handed back from :meth:`submit_batch_job` can be passed straight in.

        Args:
            job_id (str): A bare job ID or a full resource name

        Returns:
            str: ``projects/{project}/locations/{location}/batchPredictionJobs/{id}``

        Raises:
            VertexAIBatchInferenceError: If ``job_id`` is empty
        """
        if not job_id:
            raise VertexAIBatchInferenceError("Missing required argument: job_id.")

        if _RESOURCE_NAME_PATTERN.match(job_id):
            return job_id

        return (
            f"projects/{self.project_id}/locations/{self.location}"
            f"/batchPredictionJobs/{job_id}"
        )

    @staticmethod
    def _destination_uri(job: types.BatchJob) -> str | None:
        """Return the job's GCS output directory, or ``None`` if it has no GCS destination."""
        if job.output_info is not None and job.output_info.gcs_output_directory:
            return job.output_info.gcs_output_directory
        if job.dest is not None and job.dest.gcs_uri:
            return job.dest.gcs_uri
        return None

    def submit_batch_job(
        self,
        model: str,
        src: str,
        *,
        dest: str | None = None,
        display_name: str | None = None,
    ) -> types.BatchJob:
        """
        Submit a batch inference job to Vertex AI.

        Each row of a JSONL source is a serialized ``GenerateContentRequest``::

            {"key": ..., "request": {"contents": [...], "generation_config": {...}}}

        ``SchemaHelper.vertex_generation_config()`` in :mod:`src.google_model.schema.model_response`
        renders that ``generation_config`` block.

        When ``dest`` is omitted the SDK **derives one** rather than failing: a source of
        ``gs://bucket/in.jsonl`` becomes ``gs://bucket/in/dest``. The resolved destination is
        therefore logged, so a run never leaves its output somewhere unrecorded.

        Args:
            model (str): Model to run, e.g. ``gemini-2.5-flash``
            src (str): Source URI -- ``gs://.../*.jsonl`` or ``bq://project.dataset.table``
            dest (str | None): Output URI. Derived from ``src`` when omitted
            display_name (str | None): Job label. The SDK generates one when omitted

        Returns:
            types.BatchJob: The submitted job, carrying ``name`` and the initial ``state``

        Raises:
            VertexAIBatchInferenceError: If ``src`` is not a GCS or BigQuery URI, or the API
                rejects the submission
        """
        started = time.monotonic()

        # Guard before the call rather than letting the SDK's transformer raise: a bare string
        # that is not gs:// or bq:// is read as a Developer-API "files/..." source or a dataset
        # name, and fails much later with a message that does not mention Vertex.
        if not isinstance(src, str) or not src.startswith(("gs://", "bq://")):
            message = f"Batch source must be a gs:// or bq:// URI on Vertex, got: {src!r}"
            logger.error("vertex_batch.submit.invalid", reason=message)
            raise VertexAIBatchInferenceError(message)

        logger.debug("vertex_batch.submit.starting", model=model, src=src, dest=dest)

        config = types.CreateBatchJobConfig(dest=dest, display_name=display_name)

        with self._translate_errors(f"Submitting batch job for {model}", model=model, src=src):
            job = self.client.batches.create(model=model, src=src, config=config)

        logger.info(
            "vertex_batch.submit.completed",
            job=job.name,
            model=model,
            src=src,
            dest=self._destination_uri(job),
            state=job.state,
            elapsed_ms=self._elapsed_ms(started),
        )
        return job

    def pull_batch_job(self, job_name: str) -> types.BatchJob:
        """
        Fetch the current state of a batch job.

        Args:
            job_name (str): A bare job ID or a full resource name

        Returns:
            types.BatchJob: The job as Vertex currently reports it

        Raises:
            VertexAIBatchNotFoundError: If no such job exists
            VertexAIBatchInferenceError: If the request fails for any other reason
        """
        name = self.get_batch_job_name(job_name)

        with self._translate_errors(f"Fetching batch job {name}", job=name):
            job = self.client.batches.get(name=name)

        # A status read, not a state change -- DEBUG, so a poll loop cannot flood an INFO log.
        logger.debug("vertex_batch.job.fetched", job=name, state=job.state)
        return job

    def pull_batch_job_results(self, job: types.BatchJob | str) -> str:
        """
        Resolve the GCS directory a finished batch job wrote its output to.

        Returns the URI only. The output is a *directory* of JSONL shards, not one file, and
        downloading it is the caller's job -- ``GCSModule.list_files`` enumerates it.

        Args:
            job (types.BatchJob | str): A job object, or a name to fetch first

        Returns:
            str: The ``gs://`` output directory

        Raises:
            VertexAIBatchInferenceError: If the job has not reached a readable state, or its
                destination is not GCS (a BigQuery destination has no ``gs://`` URI -- read
                ``job.output_info.bigquery_output_table`` instead)
        """
        if isinstance(job, str):
            job = self.pull_batch_job(job)

        if job.state not in self.READABLE_STATES:
            raise VertexAIBatchInferenceError(
                f"Batch job {job.name} has no readable output: state={job.state}"
            )

        directory = self._destination_uri(job)

        if not directory:
            raise VertexAIBatchInferenceError(
                f"Batch job {job.name} has no GCS destination; its output is not in GCS."
            )

        logger.debug("vertex_batch.results.resolved", job=job.name, uri=directory)
        return directory

    def wait_for_batch_job(
        self,
        job_name: str,
        *,
        poll_interval: float | None = None,
        timeout: float | None = None,
    ) -> types.BatchJob:
        """
        Poll a batch job until it reaches a terminal state.

        Terminal is decided by :attr:`TERMINAL_STATES`, not by ``BatchJob.done`` -- that property
        omits ``JOB_STATE_PARTIALLY_SUCCEEDED``, so a loop built on it never exits for a job that
        partially succeeded.

        Args:
            job_name (str): A bare job ID or a full resource name
            poll_interval (float | None): Seconds between polls. Defaults to the instance value
            timeout (float | None): Seconds before giving up. Defaults to the instance value

        Returns:
            types.BatchJob: The job in its terminal state -- which may be a failure. Check
                ``job.state`` rather than assuming success

        Raises:
            VertexAIBatchTimeoutError: If the job is still running when the deadline passes
            VertexAIBatchNotFoundError: If no such job exists
            VertexAIBatchInferenceError: If a poll fails for any other reason
        """
        started = time.monotonic()
        interval = poll_interval if poll_interval is not None else self._poll_interval
        deadline = timeout if timeout is not None else self._poll_timeout
        name = self.get_batch_job_name(job_name)

        logger.debug(
            "vertex_batch.wait.starting", job=name, poll_interval=interval, timeout=deadline
        )

        while True:
            job = self.pull_batch_job(name)

            if job.state in self.TERMINAL_STATES:
                break

            remaining = deadline - (time.monotonic() - started)

            if remaining <= 0:
                message = f"Batch job {name} did not finish within {deadline}s (state={job.state})"
                logger.warning(
                    "vertex_batch.wait.timeout",
                    job=name,
                    state=job.state,
                    timeout=deadline,
                    elapsed_ms=self._elapsed_ms(started),
                )
                raise VertexAIBatchTimeoutError(message)

            time.sleep(min(interval, remaining))

        fields = {
            "job": name,
            "state": job.state,
            "elapsed_ms": self._elapsed_ms(started),
        }

        # Same event either way, but a terminal state that is not success is abnormal and worth a
        # WARNING -- including PARTIALLY_SUCCEEDED, where some rows are simply missing.
        if job.state == types.JobState.JOB_STATE_SUCCEEDED:
            logger.info("vertex_batch.wait.completed", **fields)
        elif job.state == types.JobState.JOB_STATE_PARTIALLY_SUCCEEDED:
            logger.warning("vertex_batch.wait.completed", partial=True, **fields)
        else:
            logger.warning(
                "vertex_batch.wait.completed",
                error_code=job.error.code if job.error else None,
                error_message=job.error.message if job.error else None,
                **fields,
            )

        return job

    def cancel_batch_job(self, job_name: str) -> None:
        """
        Cancel a running batch job.

        Cancellation is asynchronous: the job moves to ``JOB_STATE_CANCELLING`` and reaches
        ``JOB_STATE_CANCELLED`` later.

        Args:
            job_name (str): A bare job ID or a full resource name

        Raises:
            VertexAIBatchNotFoundError: If no such job exists
            VertexAIBatchInferenceError: If the cancellation fails for any other reason
        """
        name = self.get_batch_job_name(job_name)

        logger.debug("vertex_batch.cancel.starting", job=name)

        with self._translate_errors(f"Cancelling batch job {name}", job=name):
            self.client.batches.cancel(name=name)

        logger.info("vertex_batch.cancel.completed", job=name)

    def list_batch_jobs(
        self, *, job_filter: str | None = None, page_size: int | None = None
    ) -> list[types.BatchJob]:
        """
        List the batch jobs in this project and location.

        Args:
            job_filter (str | None): Vertex list filter, e.g. ``state="JOB_STATE_SUCCEEDED"``.
                Named ``job_filter`` rather than ``filter`` so it does not shadow the builtin
            page_size (int | None): Jobs per page. Paging is handled here, not by the caller

        Returns:
            list[types.BatchJob]: Every job the filter matched, newest first

        Raises:
            VertexAIBatchInferenceError: If the listing fails
        """
        started = time.monotonic()

        config = types.ListBatchJobsConfig(filter=job_filter, page_size=page_size)

        logger.debug("vertex_batch.listing.starting", job_filter=job_filter, page_size=page_size)

        # The Pager is materialized inside the block: iteration is where the paged requests
        # actually happen, so a fault on page two would otherwise escape untranslated.
        with self._translate_errors("Listing batch jobs", job_filter=job_filter):
            jobs = list(self.client.batches.list(config=config))

        logger.info(
            "vertex_batch.listing.completed",
            job_filter=job_filter,
            items=len(jobs),
            elapsed_ms=self._elapsed_ms(started),
        )
        return jobs
