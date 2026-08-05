"""Tests for BatchJobClient — batch submission and initial-status verification."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from tasks.ocr_tax_invoice_pipeline.module.batch_job_client import BatchJobClient


def _client(gemini_batch=None) -> BatchJobClient:
    gemini_batch = gemini_batch or Mock()
    return BatchJobClient(gemini_batch=gemini_batch, poll_delay_seconds=0)


class TestSubmit:
    def test_submit_happy_path_returns_job(self):
        # Arrange
        gemini = Mock()
        job = Mock()
        job.name = "projects/p/locations/l/batchPredictionJobs/123"
        gemini.create_batch_job.return_value = job
        gemini.status_check_batch_job.return_value = "JOB_STATE_RUNNING"
        client = _client(gemini)

        # Act
        result = client.submit(
            payload_gcs_uri="gs://payload/pl_001.jsonl",
            output_gcs_uri="gs://output/pl_001",
            job_name="pl_001",
            model="gemini-2.0-flash",
        )

        # Assert
        assert result is job
        gemini.create_batch_job.assert_called_once_with(
            model_nm="gemini-2.0-flash",
            src_uri="gs://payload/pl_001.jsonl",
            config={"dest": "gs://output/pl_001", "display_name": "pl_001"},
        )
        gemini.status_check_batch_job.assert_called_once_with(job_name=job.name)

    def test_submit_raises_when_initial_status_is_terminal_bad(self):
        # Arrange
        gemini = Mock()
        job = Mock()
        job.name = "projects/p/locations/l/batchPredictionJobs/dead"
        gemini.create_batch_job.return_value = job
        gemini.status_check_batch_job.return_value = "JOB_STATE_FAILED"
        client = _client(gemini)

        # Act / Assert
        with pytest.raises(Exception, match="terminal-bad state"):
            client.submit(
                payload_gcs_uri="gs://payload/pl_002.jsonl",
                output_gcs_uri="gs://output/pl_002",
                job_name="pl_002",
                model="gemini-2.0-flash",
            )

    def test_submit_propagates_create_batch_job_failure(self):
        # Arrange
        gemini = Mock()
        gemini.create_batch_job.side_effect = Exception("create failed")
        client = _client(gemini)

        # Act / Assert
        with pytest.raises(Exception, match="create failed"):
            client.submit(
                payload_gcs_uri="gs://payload/pl_003.jsonl",
                output_gcs_uri="gs://output/pl_003",
                job_name="pl_003",
                model="gemini-2.0-flash",
            )
        gemini.status_check_batch_job.assert_not_called()

    @pytest.mark.parametrize("status", ["JOB_STATE_CANCELLED", "JOB_STATE_EXPIRED"])
    def test_submit_raises_for_every_terminal_bad_state(self, status):
        # Arrange
        gemini = Mock()
        job = Mock()
        job.name = "projects/p/locations/l/batchPredictionJobs/x"
        gemini.create_batch_job.return_value = job
        gemini.status_check_batch_job.return_value = status
        client = _client(gemini)

        # Act / Assert
        with pytest.raises(Exception, match=status):
            client.submit(
                payload_gcs_uri="gs://payload/x.jsonl",
                output_gcs_uri="gs://output/x",
                job_name="x",
                model="gemini-2.0-flash",
            )


class TestPullJobDetail:
    def test_pull_job_detail_delegates_to_gemini_module(self):
        # Arrange
        gemini = Mock()
        expected = Mock()
        gemini.pull_batch_job.return_value = expected
        client = _client(gemini)

        # Act
        result = client.pull_job_detail("projects/p/locations/l/batchPredictionJobs/123")

        # Assert
        assert result is expected
        gemini.pull_batch_job.assert_called_once_with(job_name="projects/p/locations/l/batchPredictionJobs/123")
