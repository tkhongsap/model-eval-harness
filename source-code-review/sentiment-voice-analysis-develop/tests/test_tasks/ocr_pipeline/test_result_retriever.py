"""Tests for BatchResultRetriever timestamp handling (the fromisoformat-on-None crash fix).

Error/failed prediction lines carry a status but no createTime/processed_time. The retriever
must parse those defensively (via the shared ``parse_datetime`` util) and record the line as
FAILED instead of crashing the whole task.
"""

import json
from types import SimpleNamespace
from unittest.mock import Mock

from tasks.ocr_tax_invoice_pipeline.helper.constant import OCROutputStatus
from tasks.ocr_tax_invoice_pipeline.helper.messages import STATUS_MESSAGES
from tasks.ocr_tax_invoice_pipeline.module.result_retriever import BatchResultRetriever


def _retriever(gemini_batch=None, gcs_factory=None, tracing_builder=None):
    return BatchResultRetriever(
        gemini_batch=gemini_batch or Mock(),
        gcs_factory=gcs_factory or Mock(),
        tracing_builder=tracing_builder or Mock(),
    )


def _job(dest=None, error=None):
    job = Mock()
    job.name = "projects/p/locations/l/batchPredictionJobs/123"
    job.dest = dest
    job.error = error
    return job


def _receipt_json(**overrides):
    base = {
        "DOC_NAME": "Tax Invoice",
        "DOC_TYPE": "TaxInvoice",
        "TAX_INVOICE_NUMBER": "INV-1",
        "TAX_INVOICE_DATE": "2026-06-01",
        "VENDOR_TAX_ID": "1111111111111",
        "CUSTOMER_TAX_ID": "2222222222222",
        "BEFORE_VAT_AMOUNT": 100.0,
        "VAT_AMOUNT": 7.0,
        "AFTER_VAT_AMOUNT": 107.0,
        "WITHHOLDING_TAX_AMOUNT": None,
        "NET_AMOUNT": 107.0,
        "VENDOR_NAME_TH": "V",
        "VENDOR_ADDRESS_TH": "VA",
        "CUSTOMER_NAME_TH": "C",
        "CUSTOMER_ADDRESS_TH": "CA",
        "VENDOR_NAME_ENG": "V",
        "VENDOR_ADDRESS_ENG": "VA",
        "CUSTOMER_NAME_ENG": "C",
        "CUSTOMER_ADDRESS_ENG": "CA",
        "COPY": False,
        "PAYEE_SIGNATURE_FLAG": False,
        "PAYEE_SIGNATURE_NAME": None,
        "AUTHORIZED_RECEIVER_SIGNATURE_FLAG": False,
        "AUTHORIZED_RECEIVER_SIGNATURE_NAME": None,
        "AUTHORIZED_SIGNATORY_SIGNATURE_FLAG": False,
        "AUTHORIZED_SIGNATORY_SIGNATURE_NAME": None,
        "STAMP": False,
        "line_items": [],
    }
    base.update(overrides)
    return json.dumps(base)


_LINE_ITEM = {
    "INVOICE_NUMBER": "INV-1",
    "INVOICE_AMOUNT_BEFORE_VAT": 100.0,
    "INVOICE_VAT_AMOUNT": 7.0,
    "INVOICE_AMOUNT_AFTER_VAT": 107.0,
}


def _batch_line(text, file_uri="gs://bucket/doc_p001.pdf", create_time="2026-06-01T10:00:00+00:00"):
    return {
        "request": {
            "contents": [{"role": "user", "parts": [{"file_data": {"file_uri": file_uri}}]}],
        },
        "status": "",
        "response": {
            "createTime": create_time,
            "candidates": [{"content": {"parts": [{"text": text}]}}],
            "usageMetadata": {"totalTokenCount": 100},
        },
        "processed_time": "2026-06-01T10:05:00+00:00",
    }


class TestRowsForLineWithoutTimestamps:
    def test_error_line_without_createtime_yields_failed_row_not_crash(self):
        # Arrange — an error line carries a status but no createTime/processed_time.
        line = {"status": "INTERNAL_ERROR"}

        # Act
        rows = _retriever()._rows_for_line(line, _job())

        # Assert — recorded as FAILED with null timestamps, no fromisoformat crash.
        assert len(rows) == 1
        assert rows[0]["status"] == OCROutputStatus.FAILED.value
        assert rows[0]["start_time"] is None
        assert rows[0]["end_time"] is None
        assert "INTERNAL_ERROR" in rows[0]["message"]


class TestRetrieveSucceeded:
    def test_retrieve_succeeded_builds_rows_and_tracing_records_for_each_line(self):
        # Arrange
        job = _job(dest=SimpleNamespace(gcs_uri="gs://bucket/path/to"))
        job.display_name = "ocr-batch"
        gcs_factory = Mock()
        gcs_module = Mock()
        gcs_module.list_files.return_value = ["path/to/predictions.jsonl"]
        gcs_factory.return_value = gcs_module

        line_success = _batch_line(_receipt_json(line_items=[_LINE_ITEM, _LINE_ITEM]))
        line_blank = _batch_line(_receipt_json(line_items=[]), file_uri="gs://bucket/doc_p002.pdf")
        gemini_batch = Mock()
        gemini_batch.retrieve_batch_results.return_value = [line_success, line_blank]

        tracing_builder = Mock()
        retriever = _retriever(gemini_batch=gemini_batch, gcs_factory=gcs_factory, tracing_builder=tracing_builder)

        # Act
        df, tracing_rows = retriever.retrieve_succeeded([job])

        # Assert
        assert len(df) == 3  # 2 rows from line_success + 1 row from line_blank
        assert set(df["status"]) == {OCROutputStatus.SUCCESS.value, OCROutputStatus.BLANK.value}
        assert len(tracing_rows) == 2
        assert tracing_builder.line_to_record.call_count == 2
        gemini_batch.retrieve_batch_results.assert_called_once_with(
            gcs_module=gcs_module, batch_output_path="gs://bucket/path/to/predictions.jsonl"
        )


class TestRetrieveFailed:
    def test_retrieve_failed_builds_one_row_per_job_with_error_message(self):
        # Arrange
        job_with_error = _job(error=SimpleNamespace(message="quota exceeded"))
        job_without_error = _job(error=None)
        tracing_builder = Mock()
        retriever = _retriever(tracing_builder=tracing_builder)

        # Act
        df, tracing_rows = retriever.retrieve_failed([job_with_error, job_without_error])

        # Assert
        assert len(df) == 2
        assert (df["status"] == OCROutputStatus.FAILED.value).all()
        assert df.iloc[0]["message"] == "quota exceeded"
        assert df.iloc[1]["message"] is None
        for field in retriever._doc_fields:
            assert df.iloc[0][field] is None
            assert df.iloc[1][field] is None
        assert tracing_builder.line_to_record.call_count == 2
        tracing_builder.line_to_record.assert_any_call({}, job_with_error)
        tracing_builder.line_to_record.assert_any_call({}, job_without_error)


class TestLoadPredictionLines:
    def test_load_prediction_lines_when_no_uri_located_returns_empty_list(self):
        # Arrange — job with no dest, so _locate_predictions returns None.
        retriever = _retriever()
        job = _job(dest=None)

        # Act
        lines = retriever._load_prediction_lines(job)

        # Assert
        assert lines == []

    def test_load_prediction_lines_when_retrieve_batch_results_raises_returns_empty_list_and_logs_error(self, caplog):
        # Arrange
        gcs_factory = Mock()
        gcs_factory.return_value.list_files.return_value = ["path/to/predictions.jsonl"]
        gemini_batch = Mock()
        gemini_batch.retrieve_batch_results.side_effect = Exception("gcs read error")
        retriever = _retriever(gemini_batch=gemini_batch, gcs_factory=gcs_factory)
        job = _job(dest=SimpleNamespace(gcs_uri="gs://bucket/path/to"))

        # Act
        with caplog.at_level("ERROR"):
            lines = retriever._load_prediction_lines(job)

        # Assert
        assert lines == []
        assert any("Failed to retrieve predictions" in rec.message for rec in caplog.records)


class TestLocatePredictions:
    def test_locate_predictions_with_no_dest_returns_none_and_warns(self, caplog):
        # Arrange
        retriever = _retriever()
        job = _job(dest=None)

        # Act
        with caplog.at_level("WARNING"):
            result = retriever._locate_predictions(job)

        # Assert
        assert result is None
        assert any("no usable gs:// dest" in rec.message for rec in caplog.records)

    def test_locate_predictions_when_list_files_raises_returns_none_and_logs_error(self, caplog):
        # Arrange
        gcs_factory = Mock()
        gcs_factory.return_value.list_files.side_effect = Exception("boom")
        retriever = _retriever(gcs_factory=gcs_factory)
        job = _job(dest=SimpleNamespace(gcs_uri="gs://bucket/path/to"))

        # Act
        with caplog.at_level("ERROR"):
            result = retriever._locate_predictions(job)

        # Assert
        assert result is None
        assert any("Failed to list files" in rec.message for rec in caplog.records)

    def test_locate_predictions_with_no_matching_file_returns_none_and_warns(self, caplog):
        # Arrange
        gcs_factory = Mock()
        gcs_factory.return_value.list_files.return_value = ["path/to/other.jsonl"]
        retriever = _retriever(gcs_factory=gcs_factory)
        job = _job(dest=SimpleNamespace(gcs_uri="gs://bucket/path/to"))

        # Act
        with caplog.at_level("WARNING"):
            result = retriever._locate_predictions(job)

        # Assert
        assert result is None
        assert any("No 'predictions.jsonl' found" in rec.message for rec in caplog.records)

    def test_locate_predictions_happy_path_returns_first_matching_uri(self):
        # Arrange
        gcs_factory = Mock()
        gcs_factory.return_value.list_files.return_value = [
            "path/to/predictions.jsonl",
            "path/to/other/predictions.jsonl",
        ]
        retriever = _retriever(gcs_factory=gcs_factory)
        job = _job(dest=SimpleNamespace(gcs_uri="gs://bucket/path/to"))

        # Act
        result = retriever._locate_predictions(job)

        # Assert
        assert result == "gs://bucket/path/to/predictions.jsonl"


class TestNormalizeDestUri:
    def test_normalize_dest_uri_with_none_dest_returns_none(self):
        assert BatchResultRetriever._normalize_dest_uri(_job(dest=None)) is None

    def test_normalize_dest_uri_with_plain_gs_string_returns_it(self):
        assert BatchResultRetriever._normalize_dest_uri(_job(dest="gs://b/x")) == "gs://b/x"

    def test_normalize_dest_uri_with_non_gs_string_returns_none(self):
        assert BatchResultRetriever._normalize_dest_uri(_job(dest="not-a-uri")) is None

    def test_normalize_dest_uri_with_gcs_uri_attribute_returns_it(self):
        dest = SimpleNamespace(gcs_uri="gs://b/y")
        assert BatchResultRetriever._normalize_dest_uri(_job(dest=dest)) == "gs://b/y"


class TestExtractBucket:
    def test_extract_bucket_returns_bucket_name(self):
        assert BatchResultRetriever._extract_bucket("gs://my-bucket/some/key.jsonl") == "my-bucket"


class TestRowsForLineStatusVariants:
    def test_rows_for_line_suspicious_doc_type_uses_reason_as_message(self):
        # Arrange
        line = _batch_line(_receipt_json(DOC_TYPE="Suspicious", SUSPICIOUS_REASON="prompt injection detected"))

        # Act
        rows = _retriever()._rows_for_line(line, _job())

        # Assert
        assert len(rows) == 1
        assert rows[0]["status"] == OCROutputStatus.SUSPICIOUS.value
        assert rows[0]["message"] == "prompt injection detected"

    def test_rows_for_line_other_doc_type_is_unsupported(self):
        # Arrange
        line = _batch_line(_receipt_json(DOC_TYPE="Other"))

        # Act
        rows = _retriever()._rows_for_line(line, _job())

        # Assert
        assert len(rows) == 1
        assert rows[0]["status"] == OCROutputStatus.UNSUPPORTED.value
        assert rows[0]["message"] == STATUS_MESSAGES["UNSUPPORTED"]

    def test_rows_for_line_tax_invoice_with_no_line_items_is_blank(self):
        # Arrange
        line = _batch_line(_receipt_json(DOC_TYPE="TaxInvoice", line_items=[]))

        # Act
        rows = _retriever()._rows_for_line(line, _job())

        # Assert
        assert len(rows) == 1
        assert rows[0]["status"] == OCROutputStatus.BLANK.value
        assert rows[0]["message"] == STATUS_MESSAGES["BLANK"]

    def test_rows_for_line_tax_invoice_with_line_items_returns_one_row_per_item(self):
        # Arrange
        line = _batch_line(_receipt_json(DOC_TYPE="TaxInvoice", line_items=[_LINE_ITEM, _LINE_ITEM]))

        # Act
        rows = _retriever()._rows_for_line(line, _job())

        # Assert
        assert len(rows) == 2
        assert all(row["status"] == OCROutputStatus.SUCCESS.value for row in rows)
        assert all(row["message"] is None for row in rows)

    def test_rows_for_line_with_missing_response_text_is_failed(self):
        # Arrange
        line = {
            "request": {"contents": [{"parts": [{"file_data": {"file_uri": "gs://bucket/doc.pdf"}}]}]},
            "status": "",
            "processed_time": "2026-06-01T10:05:00+00:00",
        }

        # Act
        rows = _retriever()._rows_for_line(line, _job())

        # Assert
        assert len(rows) == 1
        assert rows[0]["status"] == OCROutputStatus.FAILED.value
        assert "no prediction text" in rows[0]["message"]

    def test_rows_for_line_with_malformed_json_text_is_failed(self):
        # Arrange
        line = _batch_line("not json")

        # Act
        rows = _retriever()._rows_for_line(line, _job())

        # Assert
        assert len(rows) == 1
        assert rows[0]["status"] == OCROutputStatus.FAILED.value
        assert rows[0]["message"]

    def test_rows_for_line_swaps_start_and_end_time_when_out_of_order(self):
        # Arrange — createTime is AFTER processed_time.
        line = {
            "request": {"contents": [{"parts": [{"file_data": {"file_uri": "gs://bucket/doc.pdf"}}]}]},
            "status": "",
            "response": {
                "createTime": "2026-06-01T10:10:00+00:00",
                "candidates": [{"content": {"parts": [{"text": _receipt_json()}]}}],
            },
            "processed_time": "2026-06-01T10:00:00+00:00",
        }

        # Act
        rows = _retriever()._rows_for_line(line, _job())

        # Assert
        assert rows[0]["start_time"] < rows[0]["end_time"]
