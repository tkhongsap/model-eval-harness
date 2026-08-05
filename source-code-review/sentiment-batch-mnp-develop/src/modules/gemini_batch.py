import os, json
import google.auth
from typing import Any
from google import genai
from google.genai.types import CreateBatchJobConfig, JobState, HttpOptions
from src.log import Logger
from src.modules.gcs import GCSModule

logger = Logger(__name__, env=os.environ.get("LOG_ENVIRONMENT", "prod"))

class GeminiBatchModule:
    def __init__(self, **kwargs):
        google_api_key = kwargs.get("google_api_key")
        genai_project_id = kwargs.get("genai_project_id")
        genai_location = kwargs.get("genai_location", "global") or "global"
        vertexai = kwargs.get("vertexai", True)  # Default to Vertex AI for GCP environments
        http_options = kwargs.get("http_options", HttpOptions(api_version="v1"))
        if google_api_key:
            self.genai_client = genai.Client(http_options=http_options, api_key=google_api_key)
            logger.info(f"Initializing GenAI Client with API Key")
        else:
            if not genai_project_id:
                try:
                    # Attempt to get project from Application Default Credentials (ADC)
                    # This handles Cloud Run, Cloud Functions, and local gcloud auth
                    _, genai_project_id = google.auth.default()
                except Exception as e:
                    logger.warning(f"Could not detect project from ADC: {e}")
            self.genai_client = genai.Client(
                vertexai=vertexai,
                project=genai_project_id,
                location=genai_location,
                http_options=http_options
            )
            logger.info(f"Initializing GenAI Client")
            logger.info(f"GCP Project ID: {genai_project_id}")
            logger.info(f"GCP Location: {genai_location}")
            logger.info(f"Vertex AI Enabled: {vertexai}")

    def create_batch_job(
            self, model_nm: str, src_uri: str, config: CreateBatchJobConfig
        ) -> genai.types.BatchJob:
        job = self.genai_client.batches.create(
            model=model_nm,
            src=src_uri,
            config=config,
        )
        logger.info(f"Created batch job with ID: {job.name}")
        return job

    def status_check_batch_job(
            self, job_name: str
        ) -> str:
        job = self.genai_client.batches.get(name=job_name)
        logger.info(f"Batch job {job_name} status: {JobState(job.state).name}")
        return JobState(job.state).name

    @staticmethod
    def retrieve_batch_results(
            gcs_module: GCSModule, batch_output_path: str
        ) -> list[dict[str, Any]]:
        """
        Retrieve and parse batch results from GCS.
        Parameters:
            gcs_module (GCSModule): An instance of GCSModule to handle GCS operations.
            batch_output_path (str): The GCS path to the batch output file.
        Returns:
            list[dict[str, Any]]: A list of dictionaries representing the parsed JSONL results.
        """
        res = gcs_module.download_file_from_gcs(batch_output_path).decode("utf-8").splitlines()
        results = []
        for line in res:
            try:
                line_dict = json.loads(line)
                results.append(line_dict)
            except Exception as e:
                logger.error(f"Error parsing line: {e}")
                continue
        logger.info(f"Retrieved {len(results)} results from batch output, path: {batch_output_path}")
        return results