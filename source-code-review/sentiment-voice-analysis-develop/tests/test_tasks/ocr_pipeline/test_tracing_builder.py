"""Tests for TracingLogBuilder — raw-Gemini tracing-log row construction.

Focus: ``line_to_record`` extracts join keys, trims the static request parts, JSON-serialises
the request/response cells (response verbatim), degrades to nulls for the empty ``{}`` line of a
failed job; ``build_tracing_log`` stamps a tz-aware ``load_dt`` and produces a schema-valid frame.
"""

import json
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pandas as pd

from tasks.ocr_tax_invoice_pipeline.module.tracing_builder import TracingLogBuilder, TracingLogContext
from tasks.ocr_tax_invoice_pipeline.schema.tracing_log import TracingLogSchema

CTX = TracingLogContext(
    job_id="JOB",
    pipeline_name="tax_invoice_extraction",
    domain_name="treasury",
    gcp_project_id="gcp-proj",
    vertexai_project_id="vx-proj",
    batch_inference_location="us-central1",
    timezone=ZoneInfo("Asia/Bangkok"),
)


def _job(error_message=None):
    job = Mock()
    job.model = "gemini-2.5-flash"
    job.name = "projects/p/locations/l/batchPredictionJobs/123"
    job.display_name = "ocr-batch-123"
    job.error = Mock(message=error_message) if error_message else None
    return job


def _line():
    return {
        "request": {
            "contents": [{"role": "user", "parts": [{"file_data": {"file_uri": "gs://bucket/inv_p003.pdf"}}]}],
            "system_instruction": {"parts": [{"text": "BIG SYSTEM PROMPT"}]},
            "generation_config": {"temperature": 0, "response_schema": {"big": "schema"}},
        },
        "status": "",
        "response": {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]},
        "processed_time": "2026-06-24T10:00:00Z",
    }


class TestLineToRecord:
    def test_succeeded_line_fills_run_job_and_line_fields(self):
        # Arrange
        builder = TracingLogBuilder(CTX)

        # Act
        record = builder.line_to_record(_line(), _job())

        # Assert
        assert record["job_id"] == "JOB"
        assert record["domain_name"] == "treasury"
        assert record["batch_inference_model_name"] == "gemini-2.5-flash"
        assert record["batch_inference_display_name"] == "ocr-batch-123"
        assert record["batch_status"] == ""
        assert record["batch_processed_time"] == "2026-06-24T10:00:00Z"
        assert record["message"] is None

    def test_extracts_source_uri_and_page_number(self):
        # Arrange / Act
        record = TracingLogBuilder(CTX).line_to_record(_line(), _job())

        # Assert
        assert record["source_file_uri"] == "gs://bucket/inv_p003.pdf"
        assert record["page_no"] == "3"

    def test_request_is_json_string_trimmed_of_static_parts(self):
        # Arrange / Act
        record = TracingLogBuilder(CTX).line_to_record(_line(), _job())

        # Assert — valid JSON, static parts stripped, file URI kept.
        request = json.loads(record["batch_request"])
        assert "system_instruction" not in request
        assert "response_schema" not in request["generation_config"]
        assert request["generation_config"]["temperature"] == 0
        assert request["contents"][0]["parts"][0]["file_data"]["file_uri"] == "gs://bucket/inv_p003.pdf"

    def test_response_with_unknown_field_round_trips_verbatim(self):
        # Arrange — a response carrying a field the schema never declared.
        line = _line()
        line["response"]["candidates"][0]["novelField"] = {"x": 1}

        # Act
        record = TracingLogBuilder(CTX).line_to_record(line, _job())

        # Assert — stored verbatim as JSON, unknown field survives.
        assert json.loads(record["batch_response"])["candidates"][0]["novelField"] == {"x": 1}

    def test_failed_job_empty_line_yields_null_line_fields_and_error_message(self):
        # Arrange
        builder = TracingLogBuilder(CTX)

        # Act
        record = builder.line_to_record({}, _job(error_message="boom"))

        # Assert
        assert record["source_file_uri"] is None
        assert record["page_no"] is None
        assert record["batch_request"] is None
        assert record["batch_response"] is None
        assert record["batch_status"] is None
        assert record["batch_processed_time"] is None
        assert record["message"] == "boom"


class TestBuildTracingLog:
    def test_stamps_tzaware_load_dt_and_passes_schema(self):
        # Arrange
        builder = TracingLogBuilder(CTX)
        rows = [builder.line_to_record(_line(), _job())]

        # Act
        df = builder.build_tracing_log(rows)

        # Assert
        assert isinstance(df["load_dt"].iloc[0], pd.Timestamp)
        assert df["load_dt"].iloc[0].tzinfo is not None
        TracingLogSchema.validate(df)  # raises on failure

    def test_empty_rows_returns_empty_frame(self):
        # Arrange / Act
        df = TracingLogBuilder(CTX).build_tracing_log([])

        # Assert
        assert df.empty
