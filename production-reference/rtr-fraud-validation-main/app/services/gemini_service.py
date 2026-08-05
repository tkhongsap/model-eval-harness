"""GeminiService — wraps the Gemini generative AI client.

Extracts ``fraud_validation_task()`` from ``utility.py``.
The ``@retry`` decorator is placed on the method so it can be tested
or swapped independently of ``ShopProcessor``.

The Gemini SDK ``generate_content`` call is synchronous; it is run in the
default thread-pool executor so callers can ``await`` it without blocking
the event loop.
"""
from __future__ import annotations
from typing import Any
import asyncio
import base64
import json
import logging
from json import JSONDecodeError

from google import genai
from google.api_core.exceptions import (
    Aborted,
    BadGateway,
    InternalServerError,
    ResourceExhausted,
    ServiceUnavailable,
    TooManyRequests,
)
from google.genai import types
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_fixed,
)

from app.core.exceptions import GeminiServiceError
from app.core.models import TokenUsage
from app.share_log import get_logger

logger = get_logger(__name__)

_RETRYABLE = (
    ResourceExhausted,
    InternalServerError,
    Aborted,
    TooManyRequests,
    BadGateway,
    ServiceUnavailable,
    asyncio.TimeoutError,
    RuntimeError,
    KeyError,
    TypeError,
)

_DEFAULT_MODEL = "gemini-2.5-flash"

_SAFETY_SETTINGS = [
    types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="OFF"),
    types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="OFF"),
    types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="OFF"),
    types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="OFF"),
]


class GeminiService:
    """Async wrapper around the Gemini generative AI client."""

    def __init__(
        self,
        client: genai.Client,
        model_version: str = _DEFAULT_MODEL,
    ) -> None:
        self._client = client
        self._model = model_version

    @retry(
        wait=wait_fixed(20),
        stop=stop_after_attempt(3),
        retry=retry_if_exception_type(_RETRYABLE),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    async def validate(
        self, images_b64: list[str], prompt: str
    ) -> tuple[dict, TokenUsage]:
        """Send *images_b64* + *prompt* to Gemini and return parsed JSON + token usage.

        Raises ``ValueError`` on empty or unparseable responses.
        Raises ``GeminiServiceError`` on API or network failures.
        """
        try:
            parts: list[types.Part] = [
                types.Part.from_bytes(data=base64.b64decode(img), mime_type="image/jpeg")
                for img in images_b64
            ]
            parts.append(types.Part.from_text(text="Process These Images"))

            contents = [types.Content(role="user", parts=parts)]

            config = types.GenerateContentConfig(
                temperature=0,
                seed=0,
                max_output_tokens=65535,
                safety_settings=_SAFETY_SETTINGS,
                system_instruction=[types.Part.from_text(text=prompt)],
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            )

            # Run the synchronous SDK call in the default thread-pool executor
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self._client.models.generate_content(
                    model=self._model,
                    contents=contents,
                    config=config,
                ),
            )

            response_text = self._extract_text(response)
            if not response_text:
                logger.warning("Gemini returned an empty response")
                raise ValueError("Gemini API returned an empty response.")

            token_usage = self._parse_token_usage(response)

            try:
                clean = response_text.replace("`", "").replace("json", "").strip()
                return json.loads(clean), token_usage
            except JSONDecodeError as exc:
                raise ValueError(
                    f"JSON decode error in Gemini response: {exc}. Raw: '{response_text[:200]}'"
                ) from exc
        except GeminiServiceError:
            raise
        except ValueError:
            raise
        except Exception as exc:
            logger.error(f"Gemini validate failed: {exc}")
            raise GeminiServiceError(
                f"Gemini validate failed: {exc}",
                details={"model": self._model, "image_count": len(images_b64)}
            ) from exc

    @retry(
        wait=wait_fixed(20),
        stop=stop_after_attempt(3),
        retry=retry_if_exception_type(_RETRYABLE),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    async def validate_text(
        self, args: Any, prompt: str
    ) -> tuple[list[dict[str, Any]], TokenUsage]:
        """Send *text* + *prompt* to Gemini and return parsed JSON + token usage.

        Same as ``validate`` but for text-only inputs (no images).
        Raises ``GeminiServiceError`` on API or network failures.
        """
        try:
            comment = args.get("comment")
            emp_id= str(args.get("emp_id"))
            comment_id = args.get("comment_id")

            contents = [
                types.Content(
                    role="user",
                    parts=[types.Part.from_text(text=f"Perform Sentiment Analysis of this comment '{comment}' for me")],
                )
            ]

            config = types.GenerateContentConfig(
                temperature=0,
                seed=0,
                max_output_tokens=65535,
                safety_settings=_SAFETY_SETTINGS,
                system_instruction=[types.Part.from_text(text=prompt)],
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            )
 
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self._client.models.generate_content(
                    model=self._model,
                    contents=contents,
                    config=config,
                ),
            )

            response_text = self._extract_text(response)
            if not response_text:
                logger.warning("Gemini returned an empty response")
                raise ValueError("Gemini API returned an empty response.")

            token_usage = self._parse_token_usage(response)

            try:
                clean = response_text.replace("`", "").replace("json", "").strip()
                clean_json = json.loads(clean)

                if isinstance(clean_json, list):
                    for item in clean_json:
                        if isinstance(item, dict):
                            item["comment"] = comment
                            item["emp_id"] = emp_id
                            item["comment_id"] = comment_id

                return clean_json, token_usage
            except JSONDecodeError as exc:
                raise ValueError(
                    f"JSON decode error in Gemini response: {exc}. Raw: '{response_text[:200]}'"
                ) from exc
        except GeminiServiceError:
            raise
        except ValueError:
            raise
        except Exception as exc:
            logger.error(f"Gemini validate_text failed: {exc}")
            raise GeminiServiceError(
                f"Gemini validate_text failed: {exc}",
                details={"model": self._model, "args": args}
            ) from exc

    @retry(
        wait=wait_fixed(20),
        stop=stop_after_attempt(3),
        retry=retry_if_exception_type(_RETRYABLE),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    async def validate_pdf(
        self, pdf_bytes: bytes, prompt: str
    ) -> tuple[dict, TokenUsage]:
        """Send a PDF document + *prompt* to Gemini and return parsed JSON + token usage.
        
        Raises ``GeminiServiceError`` on API or network failures.
        """
        try:
            parts: list[types.Part] = [
                types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
                types.Part.from_text(text="Process this invoice document"),
            ]

            contents = [types.Content(role="user", parts=parts)]

            config = types.GenerateContentConfig(
                temperature=0,
                seed=0,
                max_output_tokens=65535,
                safety_settings=_SAFETY_SETTINGS,
                system_instruction=[types.Part.from_text(text=prompt)],
                thinking_config=types.ThinkingConfig(thinking_budget=32768), #5000
            )

            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self._client.models.generate_content(
                    model=self._model,
                    contents=contents,
                    config=config,
                ),
            )

            response_text = self._extract_text(response)
            if not response_text:
                logger.warning("Gemini returned an empty response")
                raise ValueError("Gemini API returned an empty response.")

            token_usage = self._parse_token_usage(response)
            try:
                clean = response_text.replace("`", "").replace("json", "").strip()
                return json.loads(clean), token_usage
            except JSONDecodeError as exc:
                logger.warning(f"JSON decode error in Gemini response: {exc}. Raw: '{response_text[:200]}'")
                return clean, token_usage
        except GeminiServiceError:
            raise
        except ValueError:
            raise
        except Exception as exc:
            logger.error(f"Gemini validate_pdf failed: {exc}")
            raise GeminiServiceError(
                f"Gemini validate_pdf failed: {exc}",
                details={"model": self._model, "pdf_size": len(pdf_bytes)}
            ) from exc

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_text(response: object) -> str:
        try:
            for part in response.candidates[0].content.parts:  # type: ignore[union-attr]
                if part.text:
                    return part.text
        except Exception:
            pass
        return ""

    @staticmethod
    def _parse_token_usage(response: object) -> TokenUsage:
        usage = TokenUsage()
        try:
            meta = response.usage_metadata  # type: ignore[union-attr]

            for detail in (meta.prompt_tokens_details or []):
                modality = getattr(detail.modality, 'value', detail.modality)
                if modality == "TEXT":
                    usage.text_input_tokens = detail.token_count
                elif modality == "IMAGE":
                    usage.image_input_tokens = detail.token_count

            for detail in (getattr(meta, 'cache_tokens_details', None) or []):
                modality = getattr(detail.modality, 'value', detail.modality)
                if modality == "TEXT":
                    usage.text_cache_tokens = detail.token_count
                elif modality == "IMAGE":
                    usage.image_cache_tokens = detail.token_count

            usage.output_tokens = meta.candidates_token_count or 0

        except Exception:
            pass
        return usage
