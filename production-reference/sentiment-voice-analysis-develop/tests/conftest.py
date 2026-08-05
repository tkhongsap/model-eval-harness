"""
Pytest configuration and shared fixtures for all tests.
"""

import sys
from pathlib import Path

# Add project root to Python path BEFORE any imports
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from unittest.mock import Mock

import pandas as pd
import pytest


def pytest_configure(config):
    """
    Pytest hook called after command line options have been parsed.
    Ensures project root is in sys.path.
    """
    project_root = Path(__file__).parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))


@pytest.fixture
def sample_dataframe():
    """Create a sample DataFrame for testing."""
    return pd.DataFrame(
        {
            "call_id": ["123456789", "987654321"],
            "mobile_number": ["0812345678", "0898765432"],
            "agent_id": ["A001", "A002"],
            "text": ["Sample text 1", "Sample text 2"],
            "score": [0.85, 0.92],
        }
    )


@pytest.fixture
def temp_config_path(tmp_path):
    """Create a temporary YAML config file."""
    config_file = tmp_path / "test_config.yml"
    config_content = """
task1:
  type: test_task
  params:
    key1: value1
    key2: value2
"""
    config_file.write_text(config_content)
    return config_file


@pytest.fixture
def mock_gcs_client():
    """Mock Google Cloud Storage client."""
    mock_client = Mock()
    mock_bucket = Mock()
    mock_blob = Mock()

    mock_client.bucket.return_value = mock_bucket
    mock_bucket.blob.return_value = mock_blob
    mock_bucket.exists.return_value = True

    return mock_client


@pytest.fixture
def mock_sharepoint_module():
    """Mock SharePoint module."""
    mock = Mock()
    mock.download_file.return_value = b"test file content"
    mock.upload_file.return_value = {"success": True}
    return mock


@pytest.fixture
def mock_genai_client():
    """Mock Google GenAI client."""
    mock = Mock()
    mock.batches = Mock()
    mock.batches.create.return_value = Mock(name="test_batch_job")
    return mock


@pytest.fixture
def sample_transaction_payload():
    """Sample transaction log payload."""
    return {
        "data_date": "2026-01-26",
        "start_time": "2026-01-26 10:00:00",
        "end_time": "2026-01-26 10:30:00",
        "type": "batch_prediction",
        "gcp_project_id": "test-project",
        "gcp_project_name": "Test Project",
        "user_id": "test_user",
        "source": "test_source",
        "storage_path": "gs://test-bucket/path",
        "folder": "test_folder",
        "filename": "test_file.jsonl",
        "file_metadata_sec": 1800,
        "status_pass_failed_retry": "pass",
        "error_log_if": "",
        "token_usage_input": 1000,
        "token_usage_output": 500,
        "total_cost_usd": 0.05,
    }


@pytest.fixture
def sample_yaml_content():
    """Sample YAML configuration content."""
    return {"task1": {"type": "test_task", "params": {"param1": "value1", "param2": 123}}}
