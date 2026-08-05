import io
import json
from typing import Any

import google.auth
from google import genai
from google.genai.types import CreateBatchJobConfig, HttpOptions, JobState

from src.modules.google.gcs import GCSModule
from src.modules.security.tls import TlsPolicy
from src.utils.logger import Logger

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
    def retrieve_batch_results(gcs_module: GCSModule, batch_output_path: str) -> list[dict[str, Any]]:
        """
        Retrieve and parse batch results from GCS.

        Parameters:
            gcs_module (GCSModule): An instance of GCSModule to handle GCS operations
            batch_output_path (str): The GCS path to the batch output file (e.g., gs://bucket/path/file.jsonl)

        Returns:
            list[dict[str, Any]]: A list of dictionaries representing the parsed JSONL results

        Raises:
            Exception: If download or parsing fails
        """
        logger.info(f"Retrieving batch results from: {batch_output_path}")

        try:
            # Download file from GCS
            logger.debug("Downloading file from GCS...")
            raw_content = gcs_module.download_file_from_gcs(batch_output_path)
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

    @staticmethod
    def sum_tokens_usage_for_billing(usage_metadata: dict[str, Any]) -> dict[str, Any]:
        """
        Summarize token usage from usage metadata for billing purposes.
        Parameters:
            usage_metadata (dict): The usage metadata containing token details
        Returns:
            dict: A summary of token usage categorized by input/output and modality
        Raises:
            None
        """
        schema_info = {"token_input": {}, "token_cached": 0, "token_output": {}}
        try:
            sum_usage_info = schema_info.copy()

            input_tokens_detail = usage_metadata.get("promptTokensDetails", [])
            candidate_tokens_detail = usage_metadata.get("candidatesTokensDetails", [])
            thought_tokens = usage_metadata.get("thoughtsTokenCount", 0)
            cached_tokens = usage_metadata.get("cachedContentTokenCount", 0)
            sum_usage_info["token_cached"] = cached_tokens

            for token_info in input_tokens_detail:
                if token_info.get("modality") == "TEXT":
                    sum_usage_info["token_input"]["text"] = sum_usage_info["token_input"].get(
                        "text", 0
                    ) + token_info.get("tokenCount", 0)
                elif token_info.get("modality") == "AUDIO":
                    sum_usage_info["token_input"]["audio"] = sum_usage_info["token_input"].get(
                        "audio", 0
                    ) + token_info.get("tokenCount", 0)
                elif token_info.get("modality") == "IMAGE":
                    sum_usage_info["token_input"]["image"] = sum_usage_info["token_input"].get(
                        "image", 0
                    ) + token_info.get("tokenCount", 0)
                else:
                    logger.warning(f"Unknown input modality found in usage metadata: {token_info.get('modality')}")

            for token_info in candidate_tokens_detail:
                if token_info.get("modality") == "TEXT":
                    sum_usage_info["token_output"]["text"] = sum_usage_info["token_output"].get(
                        "text", 0
                    ) + token_info.get("tokenCount", 0)
                elif token_info.get("modality") == "AUDIO":
                    sum_usage_info["token_output"]["audio"] = sum_usage_info["token_output"].get(
                        "audio", 0
                    ) + token_info.get("tokenCount", 0)
                elif token_info.get("modality") == "IMAGE":
                    sum_usage_info["token_output"]["image"] = sum_usage_info["token_output"].get(
                        "image", 0
                    ) + token_info.get("tokenCount", 0)
                else:
                    logger.warning(f"Unknown output modality found in usage metadata: {token_info.get('modality')}")

            sum_usage_info["token_output"]["thoughts"] = thought_tokens
            return sum_usage_info
        except Exception as e:
            logger.error(f"Error summarizing token usage for billing: {e}", exc_info=True)
            return schema_info

    @staticmethod
    def cal_gemini_cost(usage_detail: dict, cost_config: list[dict]) -> dict:
        """
        Calculate Gemini API costs for multiple records with cached token deduction.

        This function processes usage details for one or more records, automatically deducting
        cached tokens from input tokens before calculating costs. Cached tokens are FREE and
        deducted in priority order: TEXT → AUDIO → IMAGE → VIDEO.

        Integration:
            This function is designed to work with gemini_cost() from src.utils.token_utils:
            1. Call gemini_cost(api_type="batch", model_list=["gemini-2.5-flash"])
            2. Pass the result as cost_config parameter to this function
            3. gemini_cost() loads pricing from gemini-cost.yaml via SharePoint

        Parameters:
            usage_detail (dict): Dictionary of usage records keyed by record ID (e.g., 'PK', 'PK2').
                Each record must contain:
                - 'model' (str): Model name (e.g., 'gemini-2.5-flash')
                - 'token_input' (dict): Input tokens by modality
                    - 'text' (int, optional): Text input tokens
                    - 'audio' (int, optional): Audio input tokens
                    - 'image' (int, optional): Image input tokens
                    - 'video' (int, optional): Video input tokens
                - 'token_cached' (int): Number of cached tokens (deducted from input)
                - 'token_output' (dict): Output tokens by modality
                    - 'text' (int, optional): Text output tokens
                    - 'audio' (int, optional): Audio output tokens
                    - 'image' (int, optional): Image output tokens
                    - 'video' (int, optional): Video output tokens
                    - 'thoughts' (int, optional): Thought tokens (for thinking models)

                Example:
                {
                    "PK_001": {
                        "model": "gemini-2.0-flash-exp",
                        "token_input": {"text": 18353, "audio": 4425},
                        "token_cached": 5000,
                        "token_output": {"text": 1200}
                    },
                    "PK_002": {
                        "model": "gemini-2.0-flash-exp",
                        "token_input": {"text": 15000},
                        "token_cached": 0,
                        "token_output": {"text": 800}
                    }
                }

            cost_config (list[dict]): Pricing configuration from gemini_cost() function.
                Generated by: gemini_cost(api_type="batch", model_list=["gemini-2.5-flash"])

                Each entry contains:
                - 'type' (str): API type (e.g., 'batch', 'stream')
                - 'model' (str): Model name (e.g., 'gemini-2.5-flash')
                - 'pricing_type' (str): Either 'input' or 'output'
                - 'input_type' (str): Modality type or 'all'
                    - For input: 'text', 'audio', 'image', 'video', or 'all'
                    - For output: 'text', 'audio', 'image', 'video', 'thoughts', or 'all'
                    - 'all' type applies to all modalities (common for output in production)
                    - 'thoughts' is ONLY for output (thinking models)
                - 'tokens' (int): Token count for pricing tier (e.g., 1000000)
                - 'cost' (float): Cost for the token tier in specified unit
                - 'cost_per_token' (float): Pre-calculated cost per single token
                - 'unit' (str): Currency unit (e.g., 'usd')

                Example from gemini-cost.yaml (batch tier):
                [
                    {"type": "batch", "model": "gemini-2.5-flash", "pricing_type": "input",
                     "input_type": "text", "tokens": 1000000, "cost": 0.15,
                     "cost_per_token": 0.00000015, "unit": "usd"},
                    {"type": "batch", "model": "gemini-2.5-flash", "pricing_type": "input",
                     "input_type": "audio", "tokens": 1000000, "cost": 0.5,
                     "cost_per_token": 0.0000005, "unit": "usd"},
                    {"type": "batch", "model": "gemini-2.5-flash", "pricing_type": "output",
                     "input_type": "all", "tokens": 1000000, "cost": 1.25,
                     "cost_per_token": 0.00000125, "unit": "usd"}
                ]

        Returns:
            dict: Cost breakdown keyed by record ID. Each record contains:
                - 'cost_input' (float): Total input cost in USD (after cache deduction)
                - 'cost_output' (float): Total output cost in USD (includes thoughts if present)

                Example:
                {
                    "PK_001": {
                        "cost_input": 0.022203,
                        "cost_output": 0.003600
                    },
                    "PK_002": {
                        "cost_input": 0.015000,
                        "cost_output": 0.002400
                    }
                }

        Behavior:
            - Cached tokens are deducted from INPUT tokens only, priority: TEXT → AUDIO → IMAGE → VIDEO
            - Only billable tokens (after cache deduction) are used for cost calculation
            - Output tokens (text, audio, image, video, thoughts) are always fully billed with NO cache deduction
            - Thoughts tokens are output-only and generated by thinking models during reasoning
            - Price matching: tries exact modality match first, then falls back to 'all' type
            - 'all' type is commonly used for unified output pricing (see gemini-cost.yaml)
            - Costs are rounded to 6 decimal places
            - Returns empty dict if an error occurs

        Example 1 - Production usage with gemini-cost.yaml batch pricing:
            >>> from src.utils.token_utils import gemini_cost
            >>> # Step 1: Load pricing from gemini-cost.yaml
            >>> cost_config = gemini_cost(api_type="batch", model_list=["gemini-2.5-flash"])
            >>> # cost_config now contains pricing like:
            >>> # (each entry's "model": "gemini-2.5-flash" omitted below for brevity)
            >>> # [{"pricing_type": "input", "input_type": "text", "cost_per_token": 0.00000015, ...},
            >>> #  {"pricing_type": "input", "input_type": "audio", "cost_per_token": 0.0000005, ...},
            >>> #  {"pricing_type": "output", "input_type": "all", "cost_per_token": 0.00000125, ...}]
            >>>
            >>> # Step 2: Prepare usage data
            >>> usage = {
            ...     "PK_001": {
            ...         "model": "gemini-2.5-flash",
            ...         "token_input": {"text": 18353, "audio": 4425},
            ...         "token_cached": 5000,
            ...         "token_output": {"text": 1200}
            ...     }
            ... }
            >>>
            >>> # Step 3: Calculate costs
            >>> GeminiBatchModule.cal_gemini_cost(usage, cost_config)
            {'PK_001': {'cost_input': 0.003475, 'cost_output': 0.0015}}

            # Calculation breakdown for PK_001 (batch pricing from gemini-cost.yaml):
            # INPUT (after cache deduction):
            #   - TEXT: 18353 - 5000 (cached) = 13353 billable tokens
            #   - AUDIO: 4425 tokens (no cache remaining)
            #   - Input cost = (13353 × $0.00000015) + (4425 × $0.0000005)
            #   - Input cost = $0.00200295 + $0.00221250 = $0.003416
            # OUTPUT (using "all" type):
            #   - TEXT: 1200 tokens (matches "all" type from config)
            #   - Output cost = 1200 × $0.00000125 = $0.0015
            # TOTAL = $0.003416 + $0.0015 = $0.004916

        Example 2 - With thoughts tokens (thinking model):
            >>> usage_thinking = {
            ...     "PK_002": {
            ...         "model": "gemini-2.0-flash-thinking-exp",
            ...         "token_input": {"text": 10000},
            ...         "token_cached": 2000,
            ...         "token_output": {"text": 500, "thoughts": 1500}
            ...     }
            ... }
            >>> config_thinking = [
            ...     {"model": "gemini-2.0-flash-thinking-exp", "pricing_type": "input",
            ...      "input_type": "text", "cost_per_token": 0.000001},
            ...     {"model": "gemini-2.0-flash-thinking-exp", "pricing_type": "output",
            ...      "input_type": "text", "cost_per_token": 0.000003},
            ...     {"model": "gemini-2.0-flash-thinking-exp", "pricing_type": "output",
            ...      "input_type": "thoughts", "cost_per_token": 0.000003}
            ... ]
            >>> GeminiBatchModule.cal_gemini_cost(usage_thinking, config_thinking)
            {'PK_002': {'cost_input': 0.008000, 'cost_output': 0.006000}}

            # Calculation breakdown for PK_002:
            # INPUT (after cache deduction):
            #   - TEXT: 10000 - 2000 (cached) = 8000 billable tokens
            #   - Input cost = 8000 × $0.000001 = $0.008000
            # OUTPUT (no cache deduction):
            #   - TEXT: 500 tokens
            #   - THOUGHTS: 1500 tokens (reasoning tokens from thinking model)
            #   - Output cost = (500 × $0.000003) + (1500 × $0.000003) = $0.006000
            # TOTAL = $0.008000 + $0.006000 = $0.014000
        """
        try:
            # Pre-index cost_config by (model, pricing_type, input_type) for O(1) lookup
            # instead of O(n) linear scan per modality per record
            price_index: dict = {}
            for price in cost_config:
                key = (price.get("model", ""), price.get("pricing_type", ""), (price.get("input_type") or "").lower())
                price_index[key] = price

            def _find_price(model_name: str, pricing_type: str, modality: str):
                exact = price_index.get((model_name, pricing_type, modality.lower()))
                if exact:
                    return exact
                return price_index.get((model_name, pricing_type, "all"))

            # Initialize result dictionary
            cost_result = {}

            # Process each record (PK, PK2, etc.)
            for record_id, usage in usage_detail.items():
                logger.debug(f"Processing cost calculation for record: {record_id}")

                # Deduct cached tokens from input tokens (TEXT -> AUDIO -> IMAGE priority)
                cached_tokens = usage.get("token_cached", 0)
                if cached_tokens > 0:
                    logger.info(
                        f"[{record_id}] Cached tokens detected: {cached_tokens}. Deducting from input tokens for billing."  # noqa: E501
                    )

                    remaining_cached = cached_tokens

                    # Deduct from TEXT first
                    if "text" in usage["token_input"] and remaining_cached > 0:
                        text_tokens = usage["token_input"]["text"]
                        deducted = min(text_tokens, remaining_cached)
                        usage["token_input"]["text"] = max(0, text_tokens - deducted)
                        remaining_cached -= deducted
                        logger.debug(
                            f"[{record_id}] Deducted {deducted} cached tokens from TEXT. Billable TEXT tokens: {usage['token_input']['text']}"  # noqa: E501
                        )

                    # Deduct from AUDIO if cached tokens remain
                    if "audio" in usage["token_input"] and remaining_cached > 0:
                        audio_tokens = usage["token_input"]["audio"]
                        deducted = min(audio_tokens, remaining_cached)
                        usage["token_input"]["audio"] = max(0, audio_tokens - deducted)
                        remaining_cached -= deducted
                        logger.debug(
                            f"[{record_id}] Deducted {deducted} cached tokens from AUDIO. Billable AUDIO tokens: {usage['token_input']['audio']}"  # noqa: E501
                        )

                    # Deduct from IMAGE if cached tokens still remain
                    if "image" in usage["token_input"] and remaining_cached > 0:
                        image_tokens = usage["token_input"]["image"]
                        deducted = min(image_tokens, remaining_cached)
                        usage["token_input"]["image"] = max(0, image_tokens - deducted)
                        remaining_cached -= deducted
                        logger.debug(
                            f"[{record_id}] Deducted {deducted} cached tokens from IMAGE. Billable IMAGE tokens: {usage['token_input']['image']}"  # noqa: E501
                        )

                    # Deduct from VIDEO if cached tokens still remain
                    if "video" in usage["token_input"] and remaining_cached > 0:
                        video_tokens = usage["token_input"]["video"]
                        deducted = min(video_tokens, remaining_cached)
                        usage["token_input"]["video"] = max(0, video_tokens - deducted)
                        remaining_cached -= deducted
                        logger.debug(
                            f"[{record_id}] Deducted {deducted} cached tokens from VIDEO. Billable VIDEO tokens: {usage['token_input']['video']}"  # noqa: E501
                        )

                    if remaining_cached > 0:
                        logger.warning(
                            f"[{record_id}] Cached tokens ({remaining_cached}) exceed total input tokens. Cost calculation may be affected."  # noqa: E501
                        )

                    logger.info(f"[{record_id}] After deduction - Billable input tokens: {usage['token_input']}")

                # Calculate costs for this record
                record_input_cost = 0.0
                record_output_cost = 0.0

                model_name = usage.get("model", "")

                # Calculate input cost
                for modality, tokens in usage.get("token_input", {}).items():
                    if tokens > 0:
                        price_to_use = _find_price(model_name, "input", modality)
                        if price_to_use:
                            cost_per_token = price_to_use.get("cost_per_token", 0)
                            modality_cost = tokens * cost_per_token
                            record_input_cost += modality_cost
                            logger.debug(
                                f"[{record_id}] Input {modality.upper()}: {tokens} tokens x ${cost_per_token} = ${modality_cost:.6f}"  # noqa: E501
                            )
                        else:
                            logger.warning(
                                f"[{record_id}] No pricing found for input modality '{modality}' in model '{model_name}'"  # noqa: E501
                            )

                # Calculate output cost
                for modality, tokens in usage.get("token_output", {}).items():
                    if tokens > 0:
                        price_to_use = _find_price(model_name, "output", modality)
                        if price_to_use:
                            cost_per_token = price_to_use.get("cost_per_token", 0)
                            modality_cost = tokens * cost_per_token
                            record_output_cost += modality_cost
                            logger.debug(
                                f"[{record_id}] Output {modality.upper()}: {tokens} tokens x ${cost_per_token} = ${modality_cost:.6f}"  # noqa: E501
                            )
                        else:
                            logger.warning(
                                f"[{record_id}] No pricing found for output modality '{modality}' in model '{model_name}'"  # noqa: E501
                            )

                # Store result for this record
                cost_result[record_id] = {
                    "cost_input": round(record_input_cost, 6),
                    "cost_output": round(record_output_cost, 6),
                }

                logger.info(
                    f"[{record_id}] Cost calculation complete - Input: ${record_input_cost:.6f}, Output: ${record_output_cost:.6f}"  # noqa: E501
                )

            return cost_result

        except Exception as e:
            logger.error(f"Error calculating Gemini cost: {e}", exc_info=True)
            return {}
