from __future__ import annotations

# Library imports
import time
from collections.abc import Generator, Sequence
from concurrent.futures import ThreadPoolExecutor
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


class VertexAIEmbeddingError(Exception):
    """Base for every Vertex AI embedding failure raised by this module."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        """Initialize the error.

        Args:
            message: Human-readable description of the failure.
            status_code: HTTP status that produced it, when the failure came from the API.
        """
        super().__init__(message)
        self.status_code = status_code


class VertexAIEmbeddingAuthError(VertexAIEmbeddingError):
    """Credentials were rejected (401) or lack the required IAM role (403)."""


class VertexAIEmbeddingNotFoundError(VertexAIEmbeddingError):
    """The embedding model does not exist in this project and location (404)."""


class VertexAIEmbeddingQuotaError(VertexAIEmbeddingError):
    """The request was throttled or a quota is exhausted (429)."""


# Dispatch on the HTTP status rather than on exception class, for the same reason gcp_genai.py
# does: google-genai's hierarchy is two levels deep and carries nothing a status does not, while
# an except-chain has to stay ordered most-specific-first and breaks silently when edited.
_ERROR_BY_STATUS: dict[int, type[VertexAIEmbeddingError]] = {
    401: VertexAIEmbeddingAuthError,
    403: VertexAIEmbeddingAuthError,
    404: VertexAIEmbeddingNotFoundError,
    429: VertexAIEmbeddingQuotaError,
}


class VertexAIEmbedding:
    """
    Module for producing text embeddings with Vertex AI.

    Vertex-only, matching :class:`src.hook.gcp_genai.VertexAIBatchInference`: authentication is
    Application Default Credentials, not an API key. Embeddings are returned as plain float
    lists so the caller can score them with whatever it likes --
    :mod:`src.google_model.metrics` does cosine.

    Synchronous by design. The batch embedding API exists, but it costs a GCS round-trip and a
    job wait, which is the wrong trade for the few hundred texts an evaluation run compares.
    """

    DEFAULT_API_VERSION = "v1"

    # "global" routes to https://aiplatform.googleapis.com/ instead of a regional host and is
    # supported for the standard embedding models. A model not enabled there fails as a 404, so
    # `location` is a constructor argument rather than a constant.
    DEFAULT_LOCATION = "global"

    DEFAULT_MODEL = "gemini-embedding-001"

    # SEMANTIC_SIMILARITY is the task type for symmetric text-to-text comparison, which is what
    # scoring a generated summary against a human one is. RETRIEVAL_* would optimise the vectors
    # for asymmetric query-to-document matching and score the same pair differently.
    DEFAULT_TASK_TYPE = "SEMANTIC_SIMILARITY"

    # Seconds at this module's boundary, converted to milliseconds once in _build_http_options.
    # HttpOptions.timeout is MILLISECONDS -- a caller passing 60 expecting seconds would
    # otherwise get a 60ms timeout.
    DEFAULT_TIMEOUT = 60.0

    # Vertex accepts a single instance per embed_content request for gemini-embedding-001;
    # passing a list of texts fails with "more than 1 instances". So the fan-out is concurrency,
    # not batching, and several hundred texts still finish in seconds rather than minutes.
    DEFAULT_CONCURRENCY = 8

    # Vertex defaults this to True, which silently discards everything past the model's input
    # token limit and still answers 200. False, deliberately: a caller that miscalculated its
    # chunk size must find out, because a score computed from the first third of a call reads
    # exactly like a score computed from all of it. The caller chunks; this refuses to hide it.
    DEFAULT_AUTO_TRUNCATE = False

    # The scope google-genai itself resolves ADC with (_api_client.py). Matching it means the
    # credentials built here are interchangeable with the ones the client would have built.
    CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"

    def __init__(self, **kwargs) -> None:
        """
        Initialize the Vertex AI embedding client.

        Args:
            project_id (str, optional): GCP project ID. Falls back to the project ADC reports
            location (str, optional): Vertex location. Defaults to DEFAULT_LOCATION
            model (str, optional): Default embedding model. Defaults to DEFAULT_MODEL
            timeout (float, optional): Per-request timeout in seconds. Defaults to DEFAULT_TIMEOUT
            concurrency (int, optional): Parallel requests. Defaults to DEFAULT_CONCURRENCY
            api_version (str, optional): Vertex API version. Defaults to DEFAULT_API_VERSION
            http_options (types.HttpOptions | dict, optional): Overrides. Copied before use

        Raises:
            VertexAIEmbeddingError: If no project ID is given and ADC reports none, or if
                ``concurrency`` is not positive
            google.auth.exceptions.DefaultCredentialsError: If no credentials can be resolved
        """
        started = time.monotonic()

        self.location = kwargs.get("location", self.DEFAULT_LOCATION)
        self.model = kwargs.get("model", self.DEFAULT_MODEL)
        self._timeout = kwargs.get("timeout", self.DEFAULT_TIMEOUT)
        self._concurrency = int(kwargs.get("concurrency", self.DEFAULT_CONCURRENCY))

        if self._concurrency < 1:
            message = f"concurrency must be >= 1, got {self._concurrency}."
            logger.error("vertex_embedding.config.invalid", reason=message)
            raise VertexAIEmbeddingError(message)

        # Resolving credentials here rather than letting genai.Client do it serves three purposes:
        # it is the module's fail-fast check (DefaultCredentialsError raises immediately when ADC
        # is unconfigured), it yields the ADC project so callers on Cloud Run need not pass one,
        # and passing the result to the client stops it resolving ADC a second time.
        credentials, detected_project = google.auth.default(scopes=[self.CLOUD_PLATFORM_SCOPE])

        self.project_id = kwargs.get("project_id") or detected_project

        if not self.project_id:
            message = "Missing required arguments: project_id (and ADC reported no project)."
            logger.error("vertex_embedding.config.invalid", reason=message)
            raise VertexAIEmbeddingError(message)

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
            "vertex_embedding.config.resolved",
            project_id=self.project_id,
            location=self.location,
            model=self.model,
            api_version=self.http_options.api_version,
            timeout=self._timeout,
            concurrency=self._concurrency,
        )

        logger.info(
            "vertex_embedding.connected",
            project_id=self.project_id,
            location=self.location,
            model=self.model,
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

        ``exc.details`` is deliberately not logged: it is the raw response body, and for an
        embedding request that body echoes the submitted text -- here, a customer call summary.

        Args:
            context (str): What was being attempted, for the message.
            **fields: Structured fields to attach to the error record.

        Yields:
            None

        Raises:
            VertexAIEmbeddingAuthError: On 401 or 403.
            VertexAIEmbeddingNotFoundError: On 404.
            VertexAIEmbeddingQuotaError: On 429.
            VertexAIEmbeddingError: On any other API failure.
        """
        try:
            yield
        except APIError as exc:
            status = exc.code
            message = f"{context} failed: {status} {exc.status} {exc.message}"
            logger.error(
                "vertex_embedding.request.failed",
                context=context,
                status=status,
                reason=exc.status,
                **fields,
            )
            raise _ERROR_BY_STATUS.get(status, VertexAIEmbeddingError)(
                message, status_code=status
            ) from exc

    def _embed_one(
        self,
        text: str,
        *,
        model: str,
        task_type: str,
        output_dimensionality: int | None,
        auto_truncate: bool,
    ) -> list[float]:
        """Embed a single text.

        No retry loop: ``_api_client`` already retries 408/429/5xx with exponential backoff and
        jitter over 5 attempts, exactly as documented for ``gcp_gcs`` and ``gcp_genai``.

        Args:
            text (str): The text to embed
            model (str): Embedding model
            task_type (str): Vertex task type
            output_dimensionality (int | None): Truncated dimension, or None for the model default
            auto_truncate (bool): Whether Vertex may silently cut input over the token limit

        Returns:
            list[float]: The embedding vector

        Raises:
            VertexAIEmbeddingError: If the API rejects the request, or returns no embedding
        """
        config = types.EmbedContentConfig(
            task_type=task_type,
            output_dimensionality=output_dimensionality,
            auto_truncate=auto_truncate,
        )

        # Neither the text nor the vector reaches the log -- these are call summaries. The
        # character count is enough to find an oversized input.
        with self._translate_errors(
            f"Embedding text with {model}", model=model, chars=len(text)
        ):
            response = self.client.models.embed_content(
                model=model, contents=text, config=config
            )

        embeddings = response.embeddings or []

        # A 200 with no embedding is possible when the input is filtered. Raising here rather
        # than returning [] keeps the caller's index alignment honest: a silently short result
        # would pair summary N with summary N+1 and score both wrong.
        if not embeddings or not embeddings[0].values:
            raise VertexAIEmbeddingError(
                f"Embedding response for {model} carried no vector (chars={len(text)})."
            )

        return list(embeddings[0].values)

    def embed_texts(
        self,
        texts: Sequence[str],
        *,
        model: str | None = None,
        task_type: str | None = None,
        output_dimensionality: int | None = None,
        auto_truncate: bool | None = None,
    ) -> list[list[float]]:
        """
        Embed a sequence of texts, preserving input order.

        Vertex accepts one instance per request for ``gemini-embedding-001``, so this issues one
        request per text and fans them out over a thread pool. ``ThreadPoolExecutor.map`` is what
        guarantees the ordering: results come back positionally, not in completion order, which
        matters because the caller pairs element *i* of one call with element *i* of another.

        Args:
            texts (Sequence[str]): Texts to embed. An empty sequence returns an empty list
            model (str | None): Embedding model. Defaults to the instance's
            task_type (str | None): Vertex task type. Defaults to DEFAULT_TASK_TYPE
            output_dimensionality (int | None): Truncated dimension, or None for the model default
            auto_truncate (bool | None): Whether Vertex may cut over-long input. Defaults to
                DEFAULT_AUTO_TRUNCATE, which is False so an over-long text raises

        Returns:
            list[list[float]]: One vector per input text, in input order

        Raises:
            VertexAIEmbeddingError: If any text is empty, or the API rejects a request
        """
        if not texts:
            logger.debug("vertex_embedding.embed.skipped", reason="no_texts")
            return []

        model = model or self.model
        task_type = task_type or self.DEFAULT_TASK_TYPE
        # `is None`, not `or`: an explicit False is the meaningful value here, and `or` would
        # silently promote it back to the default.
        if auto_truncate is None:
            auto_truncate = self.DEFAULT_AUTO_TRUNCATE
        started = time.monotonic()

        # Checked up front rather than per-request. An empty string is rejected by the API with a
        # 400 partway through the fan-out, having already paid for the texts before it.
        blank = [index for index, text in enumerate(texts) if not text or not text.strip()]
        if blank:
            message = f"Cannot embed empty text at positions: {blank}."
            logger.error("vertex_embedding.embed.invalid", reason=message, count=len(blank))
            raise VertexAIEmbeddingError(message)

        logger.debug(
            "vertex_embedding.embed.starting",
            model=model,
            task_type=task_type,
            count=len(texts),
            concurrency=self._concurrency,
            auto_truncate=auto_truncate,
        )

        def embed(text: str) -> list[float]:
            return self._embed_one(
                text,
                model=model,
                task_type=task_type,
                output_dimensionality=output_dimensionality,
                auto_truncate=auto_truncate,
            )

        with ThreadPoolExecutor(max_workers=min(self._concurrency, len(texts))) as pool:
            vectors = list(pool.map(embed, texts))

        logger.info(
            "vertex_embedding.embed.completed",
            model=model,
            task_type=task_type,
            count=len(vectors),
            dimensions=len(vectors[0]) if vectors else 0,
            elapsed_ms=self._elapsed_ms(started),
        )
        return vectors
