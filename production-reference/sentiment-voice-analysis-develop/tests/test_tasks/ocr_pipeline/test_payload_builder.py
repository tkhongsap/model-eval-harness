"""Tests for PayloadBuilder — Vertex AI Batch JSONL payload construction."""

from __future__ import annotations

import json

from tasks.ocr_tax_invoice_pipeline.module.payload_builder import PayloadBuilder

_GENERATION_CONFIG = {"temperature": 0.0, "max_output_tokens": 4096}


def _builder(**overrides) -> PayloadBuilder:
    kwargs = {
        "model": "gemini-2.0-flash",
        "generation_config": _GENERATION_CONFIG,
        "system_prompt": "You are an OCR extraction model.",
    }
    kwargs.update(overrides)
    return PayloadBuilder(**kwargs)


class TestBuildBatches:
    def test_empty_chunk_uris_returns_empty_list(self):
        # Arrange
        builder = _builder()

        # Act
        batches = builder.build_batches([], dt_suffix="20260101000000")

        # Assert
        assert batches == []

    def test_single_batch_under_line_limit(self):
        # Arrange
        builder = _builder()
        uris = ["gs://proc/a_p001.pdf", "gs://proc/a_p002.pdf"]

        # Act
        batches = builder.build_batches(uris, dt_suffix="20260605120000")

        # Assert
        assert len(batches) == 1
        filename, batch_uris, jsonl_bytes = batches[0]
        assert filename == "ocr_tax_invoice_pipeline_20260605120000_001.jsonl"
        assert batch_uris == uris
        assert isinstance(jsonl_bytes, bytes)

    def test_splits_into_multiple_batches_at_line_limit(self):
        # Arrange
        builder = _builder(line_limit=2, pipeline_prefix="my_pipeline")
        uris = [f"gs://proc/p{i}.pdf" for i in range(5)]

        # Act
        batches = builder.build_batches(uris, dt_suffix="20260605120000")

        # Assert
        assert len(batches) == 3
        assert [len(b[1]) for b in batches] == [2, 2, 1]
        assert batches[0][0] == "my_pipeline_20260605120000_001.jsonl"
        assert batches[1][0] == "my_pipeline_20260605120000_002.jsonl"
        assert batches[2][0] == "my_pipeline_20260605120000_003.jsonl"
        # batch_uris is the exact slice serialized into that file
        assert batches[0][1] == uris[0:2]
        assert batches[1][1] == uris[2:4]
        assert batches[2][1] == uris[4:5]


class TestSerializeBatch:
    def test_serialized_bytes_are_valid_jsonl_with_one_request_per_uri(self):
        # Arrange
        builder = _builder()
        uris = ["gs://proc/a.pdf", "gs://proc/b.jpg"]

        # Act
        _, _, jsonl_bytes = builder.build_batches(uris, dt_suffix="20260605120000")[0]

        # Assert
        lines = jsonl_bytes.decode("utf-8").split("\n")
        assert len(lines) == 2
        parsed = [json.loads(line) for line in lines]
        assert parsed[0]["request"]["contents"][0]["parts"][0]["file_data"]["file_uri"] == "gs://proc/a.pdf"
        assert parsed[1]["request"]["contents"][0]["parts"][0]["file_data"]["file_uri"] == "gs://proc/b.jpg"


class TestBuildRequest:
    def test_request_shape_carries_prompt_and_generation_config(self):
        # Arrange
        builder = _builder()

        # Act
        request = builder._build_request("gs://proc/a.pdf")["request"]

        # Assert
        assert request["contents"] == [
            {
                "role": "user",
                "parts": [{"file_data": {"mime_type": "application/pdf", "file_uri": "gs://proc/a.pdf"}}],
            }
        ]
        assert request["system_instruction"] == {"parts": [{"text": "You are an OCR extraction model."}]}
        assert request["generation_config"]["temperature"] == 0.0
        assert request["generation_config"]["response_mime_type"] == "application/json"
        assert "response_schema" in request["generation_config"]

    def test_mime_type_guessed_from_known_extension(self):
        # Arrange
        builder = _builder()

        # Act
        request = builder._build_request("gs://proc/photo.jpg")["request"]

        # Assert
        assert request["contents"][0]["parts"][0]["file_data"]["mime_type"] == "image/jpeg"

    def test_mime_type_defaults_to_pdf_for_unknown_extension(self):
        # Arrange
        builder = _builder()

        # Act
        request = builder._build_request("gs://proc/unknown_file")["request"]

        # Assert
        assert request["contents"][0]["parts"][0]["file_data"]["mime_type"] == "application/pdf"


class TestBuildResponseSchema:
    def test_response_schema_has_no_unresolved_refs_and_enum_values_are_strings(self):
        # Arrange / Act
        builder = _builder()
        schema = builder._response_schema

        # Assert
        assert "$defs" not in schema
        assert "DOC_NAME" in schema["properties"]
        doc_type_schema = schema["properties"]["DOC_TYPE"]
        assert all(isinstance(v, str) for v in doc_type_schema["enum"])
        assert doc_type_schema["type"] == "STRING"
