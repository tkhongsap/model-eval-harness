"""Unit tests for GeminiService."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.core.models import TokenUsage
from app.services.gemini_service import GeminiService
from tests.conftest import make_b64_jpeg


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_response(
    text: str,
    prompt_details: list | None = None,
    cache_details: list | None = None,
    output_tokens: int = 5,
) -> MagicMock:
    """Build a minimal mock Gemini response object."""
    mock_part = MagicMock()
    mock_part.text = text

    mock_resp = MagicMock()
    mock_resp.candidates = [MagicMock()]
    mock_resp.candidates[0].content.parts = [mock_part]

    meta = MagicMock()
    meta.prompt_tokens_details = prompt_details or []
    meta.cache_tokens_details = cache_details or []
    meta.candidates_token_count = output_tokens
    mock_resp.usage_metadata = meta
    return mock_resp


def _make_service(response: MagicMock) -> GeminiService:
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = response
    return GeminiService(client=mock_client)


# ---------------------------------------------------------------------------
# _extract_text (static)
# ---------------------------------------------------------------------------

class TestExtractText:
    def test_returns_text_from_first_part(self) -> None:
        mock_part = MagicMock()
        mock_part.text = "hello"
        resp = MagicMock()
        resp.candidates = [MagicMock()]
        resp.candidates[0].content.parts = [mock_part]
        assert GeminiService._extract_text(resp) == "hello"

    def test_returns_empty_on_attribute_error(self) -> None:
        # spec=[] gives an object with no attributes → AttributeError
        resp = MagicMock(spec=[])
        result = GeminiService._extract_text(resp)
        assert result == ""

    def test_returns_empty_when_part_has_no_text(self) -> None:
        mock_part = MagicMock()
        mock_part.text = None  # falsy → skip
        resp = MagicMock()
        resp.candidates = [MagicMock()]
        resp.candidates[0].content.parts = [mock_part]
        assert GeminiService._extract_text(resp) == ""

    def test_returns_first_text_part_skipping_none(self) -> None:
        part_none = MagicMock()
        part_none.text = None
        part_text = MagicMock()
        part_text.text = "found"
        resp = MagicMock()
        resp.candidates = [MagicMock()]
        resp.candidates[0].content.parts = [part_none, part_text]
        assert GeminiService._extract_text(resp) == "found"


# ---------------------------------------------------------------------------
# _parse_token_usage (static)
# ---------------------------------------------------------------------------

class TestParseTokenUsage:
    def test_parses_text_and_image_tokens(self) -> None:
        text_detail = MagicMock()
        text_detail.modality = "TEXT"
        text_detail.token_count = 100

        image_detail = MagicMock()
        image_detail.modality = "IMAGE"
        image_detail.token_count = 50

        resp = MagicMock()
        resp.usage_metadata.prompt_tokens_details = [text_detail, image_detail]
        resp.usage_metadata.cache_tokens_details = []
        resp.usage_metadata.candidates_token_count = 20

        usage = GeminiService._parse_token_usage(resp)
        assert usage.text_input_tokens == 100
        assert usage.image_input_tokens == 50
        assert usage.output_tokens == 20

    def test_parses_cache_tokens(self) -> None:
        cache_text = MagicMock()
        cache_text.modality = "TEXT"
        cache_text.token_count = 30

        cache_image = MagicMock()
        cache_image.modality = "IMAGE"
        cache_image.token_count = 10

        resp = MagicMock()
        resp.usage_metadata.prompt_tokens_details = []
        resp.usage_metadata.cache_tokens_details = [cache_text, cache_image]
        resp.usage_metadata.candidates_token_count = 5

        usage = GeminiService._parse_token_usage(resp)
        assert usage.text_cache_tokens == 30
        assert usage.image_cache_tokens == 10

    def test_returns_zero_usage_on_attribute_error(self) -> None:
        resp = MagicMock(spec=[])  # No attributes
        usage = GeminiService._parse_token_usage(resp)
        assert isinstance(usage, TokenUsage)
        assert usage.text_input_tokens == 0
        assert usage.output_tokens == 0


# ---------------------------------------------------------------------------
# validate (async, uses __wrapped__ to bypass tenacity retry + wait)
# ---------------------------------------------------------------------------

class TestValidate:
    async def test_parses_plain_json_response(self) -> None:
        resp = _make_mock_response('{"fraud": true}')
        svc = _make_service(resp)
        result_dict, usage = await GeminiService.validate.__wrapped__(  # type: ignore[attr-defined]
            svc, [make_b64_jpeg()], "test prompt"
        )
        assert result_dict == {"fraud": True}
        assert isinstance(usage, TokenUsage)

    async def test_strips_markdown_backtick_fencing(self) -> None:
        resp = _make_mock_response("```json\n{\"fraud\": false}\n```")
        svc = _make_service(resp)
        result_dict, _ = await GeminiService.validate.__wrapped__(  # type: ignore[attr-defined]
            svc, [make_b64_jpeg()], "prompt"
        )
        assert result_dict == {"fraud": False}

    async def test_raises_value_error_on_empty_response(self) -> None:
        resp = _make_mock_response("")  # _extract_text returns ""
        svc = _make_service(resp)
        with pytest.raises(ValueError, match="empty response"):
            await GeminiService.validate.__wrapped__(  # type: ignore[attr-defined]
                svc, [make_b64_jpeg()], "prompt"
            )

    async def test_raises_value_error_on_bad_json(self) -> None:
        resp = _make_mock_response("not valid json {{{")
        svc = _make_service(resp)
        with pytest.raises(ValueError, match="JSON decode error"):
            await GeminiService.validate.__wrapped__(  # type: ignore[attr-defined]
                svc, [make_b64_jpeg()], "prompt"
            )

    async def test_passes_images_and_prompt_to_client(self) -> None:
        resp = _make_mock_response('{"ok": 1}')
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = resp
        svc = GeminiService(client=mock_client)

        await GeminiService.validate.__wrapped__(  # type: ignore[attr-defined]
            svc, [make_b64_jpeg()], "my prompt"
        )
        mock_client.models.generate_content.assert_called_once()
        call_kwargs = mock_client.models.generate_content.call_args[1]
        assert call_kwargs["model"] == svc._model
