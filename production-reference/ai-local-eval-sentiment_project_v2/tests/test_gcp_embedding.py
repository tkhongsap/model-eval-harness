"""Tests for the Vertex embedding hook.

No credentials and no network: the constructor resolves ADC, so these build the instance with
``object.__new__`` and exercise the pieces that do not need a client. What is worth pinning here
is the millisecond conversion and the copy-before-mutate on ``HttpOptions`` -- both are documented
traps that a passing integration run would not catch.
"""

import pytest
from google.genai import types

from src.hook.gcp_embedding import (
    _ERROR_BY_STATUS,
    VertexAIEmbedding,
    VertexAIEmbeddingAuthError,
    VertexAIEmbeddingError,
    VertexAIEmbeddingNotFoundError,
    VertexAIEmbeddingQuotaError,
)


@pytest.fixture
def client():
    """An instance with the fields _build_http_options needs, and no ADC lookup."""
    instance = object.__new__(VertexAIEmbedding)
    instance._timeout = VertexAIEmbedding.DEFAULT_TIMEOUT
    return instance


class TestBuildHttpOptions:
    def test_timeout_is_converted_from_seconds_to_milliseconds(self, client):
        options = client._build_http_options(None, "v1")

        # HttpOptions.timeout is milliseconds; passing the 60.0 second default straight through
        # would give a 60ms timeout and fail every request.
        assert options.timeout == 60_000

    def test_caller_supplied_timeout_is_left_alone(self, client):
        options = client._build_http_options(types.HttpOptions(timeout=5_000), "v1")

        assert options.timeout == 5_000

    def test_tls_context_reaches_both_transports(self, client):
        options = client._build_http_options(None, "v1")

        assert "verify" in options.client_args
        assert "verify" in options.async_client_args
        # TLS 1.2 floor, not 1.3 -- endpoints without 1.3 must keep working.
        assert options.client_args["verify"].minimum_version.name.endswith("1_2")

    def test_provided_options_are_copied_not_mutated(self, client):
        original = types.HttpOptions(api_version="v1")

        built = client._build_http_options(original, "v1")

        # Assigning in place would leak this client's SSL context into every other client built
        # from the same object.
        assert original.client_args is None
        assert built.client_args is not None

    def test_api_version_defaults_only_when_unset(self, client):
        assert client._build_http_options(None, "v1").api_version == "v1"
        assert (
            client._build_http_options(types.HttpOptions(api_version="v1beta1"), "v1").api_version
            == "v1beta1"
        )

    def test_accepts_a_dict(self, client):
        options = client._build_http_options({"api_version": "v1"}, "v1")

        assert options.api_version == "v1"
        assert options.timeout == 60_000


class TestErrorMapping:
    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            (401, VertexAIEmbeddingAuthError),
            (403, VertexAIEmbeddingAuthError),
            (404, VertexAIEmbeddingNotFoundError),
            (429, VertexAIEmbeddingQuotaError),
        ],
    )
    def test_status_maps_to_its_error_type(self, status, expected):
        assert _ERROR_BY_STATUS[status] is expected

    def test_unmapped_status_falls_back_to_the_base_error(self):
        assert _ERROR_BY_STATUS.get(500, VertexAIEmbeddingError) is VertexAIEmbeddingError

    def test_every_error_is_a_vertex_embedding_error(self):
        for error_type in _ERROR_BY_STATUS.values():
            assert issubclass(error_type, VertexAIEmbeddingError)


class TestEmbedTexts:
    def test_empty_input_returns_empty_without_calling_the_api(self, client):
        assert VertexAIEmbedding.embed_texts(client, []) == []

    def test_blank_text_is_rejected_before_any_request(self, client):
        client.model = VertexAIEmbedding.DEFAULT_MODEL

        # Checked up front rather than per-request: a 400 partway through the fan-out has already
        # paid for every text before it.
        with pytest.raises(VertexAIEmbeddingError, match="empty text at positions"):
            VertexAIEmbedding.embed_texts(client, ["fine", "   ", "also fine"])


class TestAutoTruncate:
    """Vertex defaults auto_truncate to True, which is the wrong default for this project.

    A transcript over the model's token limit would come back as a 200 carrying an embedding of
    its opening only -- indistinguishable, in the output, from an embedding of the whole call.
    """

    def test_default_is_off_so_oversized_input_fails_loudly(self):
        assert VertexAIEmbedding.DEFAULT_AUTO_TRUNCATE is False

    def test_default_reaches_the_request_config(self, client, monkeypatch):
        client.model = VertexAIEmbedding.DEFAULT_MODEL
        client._concurrency = 1
        seen = []

        def capture(self, text, **kwargs):
            seen.append(kwargs["auto_truncate"])
            return [1.0]

        monkeypatch.setattr(VertexAIEmbedding, "_embed_one", capture)
        VertexAIEmbedding.embed_texts(client, ["some text"])

        assert seen == [False]

    def test_explicit_false_is_not_promoted_back_to_the_default(self, client, monkeypatch):
        client.model = VertexAIEmbedding.DEFAULT_MODEL
        client._concurrency = 1
        seen = []

        def capture(self, text, **kwargs):
            seen.append(kwargs["auto_truncate"])
            return [1.0]

        monkeypatch.setattr(VertexAIEmbedding, "_embed_one", capture)
        # `or` rather than `is None` would silently turn this back into the default -- which
        # happens to be False today, so the bug would only surface if the default ever changed.
        VertexAIEmbedding.embed_texts(client, ["some text"], auto_truncate=False)
        VertexAIEmbedding.embed_texts(client, ["some text"], auto_truncate=True)

        assert seen == [False, True]

    def test_config_carries_the_flag(self, client):
        config = types.EmbedContentConfig(
            task_type=VertexAIEmbedding.DEFAULT_TASK_TYPE,
            output_dimensionality=None,
            auto_truncate=False,
        )

        assert config.auto_truncate is False
