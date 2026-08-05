"""Payload builder — creates Vertex AI Batch JSONL request files."""

from __future__ import annotations

import json
import mimetypes

from src.utils.logger import Logger
from src.utils.pydantic_utils import pydantic_resolve_refs, sanitize_json_schema_enums
from tasks.ocr_tax_invoice_pipeline.schema.model_response import ReceiptExtraction

logger = Logger(__name__)

BATCH_LINE_LIMIT = 100_000
_DEFAULT_PIPELINE_PREFIX = "ocr_tax_invoice_pipeline"


class PayloadBuilder:
    """Builds Vertex AI Batch JSONL payloads from a list of page GCS URIs.

    Payloads are split at :data:`BATCH_LINE_LIMIT` lines so each submitted
    job stays within the 200 000-line hard limit.  Each JSONL file is named
    ``{pipeline_prefix}_{dt_suffix}_{seq:03d}.jsonl``.
    """

    def __init__(
        self,
        model: str,
        generation_config: dict,
        system_prompt: str,
        pipeline_prefix: str = _DEFAULT_PIPELINE_PREFIX,
        line_limit: int = BATCH_LINE_LIMIT,
    ) -> None:
        """Initialise the builder.

        Args:
            model: Vertex AI model name (e.g. ``gemini-2.0-flash``).
            generation_config: Generation config dict forwarded verbatim
                to each JSONL request line.
            system_prompt: System instruction text sent as ``system_instruction``
                in every request.
            pipeline_prefix: Prefix for generated JSONL filenames.
            line_limit: Maximum JSONL lines per batch file. Defaults to
                :data:`BATCH_LINE_LIMIT`.
        """
        self._model = model
        self._generation_config = generation_config
        self._system_prompt = system_prompt
        self._pipeline_prefix = pipeline_prefix
        self._line_limit = line_limit
        self._response_schema = self._build_response_schema()

    def build_batches(
        self,
        chunk_uris: list[str],
        dt_suffix: str,
    ) -> list[tuple[str, list[str], bytes]]:
        """Split URIs into JSONL batches of at most ``line_limit`` lines.

        Args:
            chunk_uris: GCS URIs of IQS-valid page files.
            dt_suffix: Datetime string used in filenames (``YYYYMMDDHHMMSS``).

        Returns:
            List of ``(filename, batch_uris, jsonl_bytes)`` tuples, one per batch.
            ``batch_uris`` is the exact slice of URIs serialized into that file,
            so callers can map records back to their batch without re-deriving
            the partition arithmetically. Empty list when ``chunk_uris`` is empty.
        """
        if not chunk_uris:
            return []

        batches = []
        for seq, start in enumerate(range(0, len(chunk_uris), self._line_limit), start=1):
            batch_uris = chunk_uris[start : start + self._line_limit]
            filename = f"{self._pipeline_prefix}_{dt_suffix}_{seq:03d}.jsonl"
            jsonl_bytes = self._serialize_batch(batch_uris)
            batches.append((filename, batch_uris, jsonl_bytes))
            logger.info(f"Batch {seq}: {len(batch_uris)} lines → {filename}")

        return batches

    def _serialize_batch(self, uris: list[str]) -> bytes:
        """Serialize a list of GCS URIs to JSONL bytes.

        Args:
            uris: GCS URIs for a single batch.

        Returns:
            UTF-8 encoded JSONL bytes.
        """
        lines = (json.dumps(self._build_request(uri), ensure_ascii=False) for uri in uris)
        return "\n".join(lines).encode("utf-8")

    def _build_request(self, gcs_uri: str) -> dict:
        """Build one Vertex AI Batch request line for a single page URI.

        Args:
            gcs_uri: Full GCS URI (``gs://...``) of a page file.

        Returns:
            Request dict suitable for one JSONL line.
        """
        mime_type = mimetypes.guess_type(gcs_uri)[0] or "application/pdf"
        return {
            "request": {
                "contents": [
                    {
                        "role": "user",
                        "parts": [
                            {
                                "file_data": {
                                    "mime_type": mime_type,
                                    "file_uri": gcs_uri,
                                }
                            }
                        ],
                    }
                ],
                "system_instruction": {"parts": [{"text": self._system_prompt}]},
                "generation_config": {
                    **self._generation_config,
                    "response_mime_type": "application/json",
                    "response_schema": self._response_schema,
                },
            }
        }

    @staticmethod
    def _build_response_schema() -> dict:
        """Derive the Vertex AI response schema from :class:`ReceiptExtraction`."""
        raw = ReceiptExtraction.model_json_schema()
        resolved = pydantic_resolve_refs(raw)
        return sanitize_json_schema_enums(resolved)
