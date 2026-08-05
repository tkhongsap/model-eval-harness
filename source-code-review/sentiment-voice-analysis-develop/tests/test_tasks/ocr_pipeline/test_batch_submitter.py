"""Tests for BatchSubmitter — payload upload, job submission, and per-job failure isolation."""

from unittest.mock import Mock

from tasks.ocr_tax_invoice_pipeline.module.batch_submitter import BatchSubmitter
from tasks.ocr_tax_invoice_pipeline.schema.contracts import ChunkEntry


def _submitter(batch_client, build_batches_return):
    payload_builder = Mock()
    payload_builder.build_batches.return_value = build_batches_return
    payload_gcs = Mock(bucket_name="payload-bucket")
    submitter = BatchSubmitter(
        payload_builder=payload_builder,
        batch_client=batch_client,
        payload_gcs=payload_gcs,
        output_bucket="output-bucket",
        payload_prefix="payloads/JOB",
        output_prefix="output/JOB",
        model="gemini",
        job_id="JOB",
        dt_suffix="20260605120000",
    )
    return submitter, payload_builder, payload_gcs


def _chunk(uri, parent="gs://landing/a.pdf"):
    return ChunkEntry(parent_landing_path=parent, gcs_uri=uri)


def test_empty_chunks_submits_no_jobs():
    batch_client = Mock()
    submitter, payload_builder, _ = _submitter(batch_client, [])

    result = submitter.run([])

    assert result == []
    payload_builder.build_batches.assert_not_called()
    batch_client.submit.assert_not_called()


def test_successful_submission_has_expected_uri_shapes():
    job = Mock()
    batch_client = Mock()
    batch_client.submit.return_value = job
    batches = [("pl_001.jsonl", ["gs://proc/a_1.pdf"], b"{}")]
    submitter, _, payload_gcs = _submitter(batch_client, batches)

    result = submitter.run([_chunk("gs://proc/a_1.pdf")])

    assert len(result) == 1
    sub = result[0]
    assert sub.payload_uri == "gs://payload-bucket/payloads/JOB/pl_001.jsonl"
    assert sub.output_uri == "gs://output-bucket/output/JOB/pl_001"  # .jsonl stripped
    assert sub.parent_paths == frozenset({"gs://landing/a.pdf"})
    assert sub.job is job
    assert sub.error is None
    payload_gcs.update_content_to_gcs.assert_called_once()


def test_one_submission_fails_others_succeed():
    job = Mock()
    batch_client = Mock()
    batch_client.submit.side_effect = [job, Exception("submit boom")]
    batches = [
        ("pl_001.jsonl", ["gs://proc/a_1.pdf"], b"{}"),
        ("pl_002.jsonl", ["gs://proc/b_1.pdf"], b"{}"),
    ]
    submitter, _, _ = _submitter(batch_client, batches)

    result = submitter.run(
        [_chunk("gs://proc/a_1.pdf", "gs://landing/a.pdf"), _chunk("gs://proc/b_1.pdf", "gs://landing/b.pdf")]
    )

    assert len(result) == 2
    assert result[0].error is None and result[0].job is job
    assert result[1].job is None
    assert "submit boom" in result[1].error
