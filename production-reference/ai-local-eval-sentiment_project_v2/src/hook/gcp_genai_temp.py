import io
import json
from typing import Any

import google.auth
from google import genai
from google.genai.types import CreateBatchJobConfig, HttpOptions, JobState

from src.hook.gcp_gcs import GCSModule
from src.hook.tls import TlsPolicy
from src.logger import Logger

logger = Logger(__name__)


class GeminiBatchModule:
    """
    Module for interacting with Google Gemini Batch API.
    Handles batch job creation, status checking, and result retrieval.
    """

    # Default Configuration Constants
    DEFAULT_LOCATION = "global"
    DEFAULT_API_VERSION = "v1"
    DEFAULT_USE_VERTEXAI = True

    def __init__(self, **kwargs):
        """
        Initialize Gemini Batch module with authentication and configuration.

        Parameters:
            google_api_key (str, optional): Google API key for authentication
            genai_project_id (str, optional): GCP project ID (auto-detected if not provided)
            genai_location (str, optional): GCP region. Defaults to 'global'
            vertexai (bool, optional): Whether to use Vertex AI. Defaults to True
            http_options (HttpOptions, optional): HTTP configuration options

        Raises:
            Exception: If authentication or client initialization fails
        """
        logger.info("Initializing GeminiBatchModule...")

        google_api_key = kwargs.get("google_api_key")
        genai_project_id = kwargs.get("genai_project_id")
        genai_location = kwargs.get("genai_location", self.DEFAULT_LOCATION) or self.DEFAULT_LOCATION
        vertexai = kwargs.get("vertexai", self.DEFAULT_USE_VERTEXAI)
        http_options = kwargs.get("http_options", HttpOptions(api_version=self.DEFAULT_API_VERSION))

        # Pin a TLS 1.2+ floor on the underlying httpx transport (sync + async).
        # client_args / async_client_args are forwarded verbatim to httpx.Client(...),
        # which accepts an ssl.SSLContext as its `verify` argument.
        tls_context = TlsPolicy().context()
        http_options.client_args = {**(http_options.client_args or {}), "verify": tls_context}
        http_options.async_client_args = {**(http_options.async_client_args or {}), "verify": tls_context}

        try:
            if google_api_key:
                logger.info("Initializing GenAI Client with API Key authentication")
                logger.debug(
                    f"API Key provided: {google_api_key[:10]}..." if len(google_api_key) > 10 else "API Key too short"
                )

                self.genai_client = genai.Client(http_options=http_options, api_key=google_api_key)
                self.auth_method = "api_key"
                logger.info("GenAI Client initialized successfully with API Key")

            else:
                logger.info("No API Key provided, using Application Default Credentials (ADC)")

                if not genai_project_id:
                    logger.debug("Project ID not provided, attempting to detect from ADC...")
                    try:
                        # Attempt to get project from Application Default Credentials (ADC)
                        # This handles Cloud Run, Cloud Functions, and local gcloud auth
                        credentials, detected_project = google.auth.default()
                        genai_project_id = detected_project
                        logger.info(f"Auto-detected GCP Project ID from ADC: {genai_project_id}")
                    except Exception as e:
                        logger.error(f"Could not detect project from ADC: {e}")
                        raise ValueError(
                            "genai_project_id is required when not using API key and ADC detection failed"
                        ) from e
                else:
                    logger.debug(f"Using provided Project ID: {genai_project_id}")

                logger.debug(f"Initializing with Vertex AI: {vertexai}, Location: {genai_location}")

                self.genai_client = genai.Client(
                    vertexai=vertexai, project=genai_project_id, location=genai_location, http_options=http_options
                )
                self.auth_method = "adc"

                logger.info("GenAI Client initialized successfully with Vertex AI")
                logger.info(
                    f"Configuration - Project: {genai_project_id}, Location: {genai_location}, Vertex AI: {vertexai}"
                )

            # Store configuration for reference
            self.project_id = genai_project_id
            self.location = genai_location
            self.vertexai_enabled = vertexai

            logger.info(f"GeminiBatchModule initialization complete (auth method: {self.auth_method})")

        except Exception as e:
            logger.error(f"Failed to initialize GeminiBatchModule: {e}", exc_info=True)
            raise

    @staticmethod
    def resolve_job_name(project_id: str, location: str, job_id: str | int) -> str:
        """
        Accept bare ID or full path; return full resource path.

        Parameters:
            project_id (str): GCP project ID
            location (str): GCP region
            job_id (str | int): Either the bare batch job ID

        Returns:
            str: Full resource path for the batch job
        """
        if isinstance(job_id, int):
            job_id = str(job_id)
        else:
            raise ValueError(f"job_id must be an integer or string, got {type(job_id)}")
        job_id = job_id.strip()
        if job_id.startswith("projects/"):
            return job_id
        return f"projects/{project_id}/locations/{location}/batchPredictionJobs/{job_id}"

    def create_batch_job(
        self,
        model_nm: str,
        src_uri: str,
        config: dict[str, Any] | CreateBatchJobConfig,
    ) -> genai.types.BatchJob:
        """
        Create a new batch job for processing.

        Parameters:
            model_nm (str): The name/identifier of the model to use
            src_uri (str): The source URI (e.g., GCS path) containing input data
            config (dict[str, Any] | CreateBatchJobConfig): Configuration for the batch job

        Returns:
            genai.types.BatchJob: The created batch job object

        Raises:
            Exception: If batch job creation fails
        """
        logger.info(f"Creating batch job with model: {model_nm}")
        logger.debug(f"Source URI: {src_uri}")

        try:
            if isinstance(config, dict):
                config = CreateBatchJobConfig(**config)

            job = self.genai_client.batches.create(
                model=model_nm,
                src=src_uri,
                config=config,
            )

            logger.info(f"Batch job created: {job.name} (state: {JobState(job.state).name})")

            return job

        except Exception as e:
            logger.error(f"Failed to create batch job: {e}", exc_info=True)
            raise Exception(f"Error creating batch job: {e}") from e

    def pull_batch_job(self, job_name: str) -> genai.types.BatchJob:
        """
        Pull the latest state of a batch job.

        Parameters:
            job_name (str): The name/ID of the batch job to pull

        Returns:
            genai.types.BatchJob: The latest batch job object with updated state

        Raises:
            Exception: If pulling batch job fails
        """
        logger.info(f"Pulling batch job: {job_name}")

        try:
            job = self.genai_client.batches.get(name=job_name)
            logger.info(f"Batch job pulled: {job.name} (state: {JobState(job.state).name})")
            return job

        except Exception as e:
            logger.error(f"Failed to pull batch job '{job_name}': {e}", exc_info=True)
            raise Exception(f"Error pulling batch job: {e}") from e

    def status_check_batch_job(self, job_name: str) -> str:
        """
        Check the status of a batch job.

        Parameters:
            job_name (str): The name/ID of the batch job to check

        Returns:
            str: The current state of the job (e.g., 'RUNNING', 'COMPLETED', 'FAILED')

        Raises:
            Exception: If status check fails
        """
        logger.info(f"Checking status for batch job: {job_name}")

        try:
            job = self.genai_client.batches.get(name=job_name)
            job_state = JobState(job.state).name

            logger.info(f"Batch job status: {job_state}")

            # Log request counts if available
            if hasattr(job, "request_count") and job.request_count:
                logger.info(
                    f"  Requests: {job.request_count} total, {getattr(job, 'processed_request_count', 0)} processed, {getattr(job, 'failed_request_count', 0)} failed"  # noqa: E501
                )

            # Log error details if job failed
            if (
                job_state in ["JOB_STATE_FAILED", "JOB_STATE_CANCELLED", "JOB_STATE_EXPIRED"]
                and hasattr(job, "error")
                and job.error
            ):
                logger.error(f"  Error: {getattr(job.error, 'message', 'No error message available')}")

            return job_state

        except Exception as e:
            logger.error(f"Failed to check status for batch job '{job_name}': {e}", exc_info=True)
            raise Exception(f"Error checking batch job status: {e}") from e

    @staticmethod
    def retrieve_batch_results(gcs_module: GCSModule, bucket: str, batch_output_path: str) -> list[dict[str, Any]]:
        """
        Retrieve and parse batch results from GCS.

        Parameters:
            gcs_module (GCSModule): An instance of GCSModule to handle GCS operations
            bucket (str): The GCS bucket name
            batch_output_path (str): The GCS path to the batch output file within the bucket (e.g., path/to/file.jsonl)

        Returns:
            list[dict[str, Any]]: A list of dictionaries representing the parsed JSONL results

        Raises:
            Exception: If download or parsing fails
        """
        logger.info(f"Retrieving batch results from: {batch_output_path}")

        try:
            # Download file from GCS
            raw_content = gcs_module.download_file(bucket, batch_output_path)
            content_size_kb = len(raw_content) / 1024
            logger.debug(f"Downloaded {content_size_kb:.2f} KB from GCS")

            # Parse JSONL line-by-line via StringIO to avoid holding a duplicate lines list in memory
            results = []
            parse_errors = 0
            total_lines = 0

            with io.StringIO(raw_content.decode("utf-8")) as f:
                for line_num, line in enumerate(f, 1):
                    line = line.rstrip("\n")
                    if not line.strip():
                        logger.debug(f"Skipping empty line {line_num}")
                        continue
                    total_lines += 1
                    try:
                        line_dict = json.loads(line)
                        results.append(line_dict)
                    except json.JSONDecodeError as e:
                        parse_errors += 1
                        logger.error(f"JSON parse error on line {line_num}: {e}")
                        logger.debug(f"Problematic line content: {line[:100]}...")
                    except Exception as e:
                        parse_errors += 1
                        logger.error(f"Unexpected error parsing line {line_num}: {e}")

            # Summary logging
            if parse_errors > 0:
                logger.warning(
                    f"Retrieved {len(results)} results with {parse_errors} parse error(s) from {batch_output_path}"
                )
            else:
                logger.info(f"Successfully retrieved {len(results)} results from {batch_output_path}")

            logger.debug(
                f"Parse summary - Total lines: {total_lines}, Successful: {len(results)}, Errors: {parse_errors}"
            )

            return results

        except Exception as e:
            logger.error(f"Failed to retrieve batch results from '{batch_output_path}': {e}", exc_info=True)
            raise Exception(f"Error retrieving batch results: {e}") from e