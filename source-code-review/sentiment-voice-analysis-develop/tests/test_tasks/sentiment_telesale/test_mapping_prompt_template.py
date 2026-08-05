"""
Tests for PrepPayloadTask._mapping_prompt_template method.
Tests the critical prompt template mapping logic with various scenarios.
"""

import io
from unittest.mock import Mock, patch

import pandas as pd
import pytest

from tasks.sentiment_telesale.prep_payload_task import PrepPayloadTask


class MockSharePointResponse:
    """Mock SharePoint response object."""

    def __init__(self, content: bytes):
        self.content = content


def create_mock_agent_mapping_excel() -> bytes:
    """Create mock agent mapping Excel file in memory."""
    data = {
        "emp_id": ["12345", "67890", "11111", "22222", "12345"],
        "commission_skill_code": ["0001", "0001", "0002", "0003", "0001"],
        "updatedate": pd.to_datetime(
            [
                "2026-01-01 10:00:00",
                "2026-01-02 10:00:00",
                "2026-01-03 10:00:00",
                "2026-01-04 10:00:00",
                "2026-01-05 10:00:00",
            ]
        ),
        "agent_name": ["Agent A", "Agent B", "Agent C", "Agent D", "Agent A Updated"],
    }
    df = pd.DataFrame(data)

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="list", index=False)
    buffer.seek(0)
    return buffer.read()


def create_mock_check_list_excel(skill_code: str = "1") -> bytes:
    """Create a minimal valid 3-sheet check_list_prompt.xlsx in memory."""
    code = int(skill_code)
    categories = pd.DataFrame(
        {
            "commission_skill_code": [code],
            "commission_skill": ["TestSkill"],
            "main_category_no": [1],
            "main_category_name": ["Main Cat"],
            "section": ["campaign_description"],
            "order": [1],
            "content": ["Test campaign"],
        }
    )
    subcategories = pd.DataFrame(
        {
            "commission_skill_code": [code],
            "sub_category_no": ["1.1"],
            "sub_category_name": ["Sub Cat"],
            "order": [1],
            "section": ["principle"],
            "content": ["Test principle"],
        }
    )
    tags = pd.DataFrame(
        {
            "commission_skill_code": [code],
            "item_no": ["1.1.1"],
            "tag_code": ["TestTag"],
            "rule_and_logic": ["Test rule"],
            "positive_example_th": ["Good"],
            "negative_example_th": ["Bad"],
            "is_active": [True],
        }
    )
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        categories.to_excel(writer, sheet_name="Categories", index=False)
        subcategories.to_excel(writer, sheet_name="Subcategories", index=False)
        tags.to_excel(writer, sheet_name="Tags", index=False)
    buf.seek(0)
    return buf.read()


def setup_sharepoint_control_for_prompts(mock_task, skill_code: str = "1") -> None:
    """Configure sharepoint_control mock to successfully serve common_prompt and check_list Excel."""
    excel_bytes = create_mock_check_list_excel(skill_code)

    def _get_item(path):
        if "check_list" in str(path):
            return MockSharePointResponse(excel_bytes)
        return MockSharePointResponse(b"Common prompt text ${KNOWLEDGE_BASE} ${CAMPAIGN_CHECKLIST}")

    mock_task.sharepoint_control.is_item_exists.return_value = True
    mock_task.sharepoint_control.get_item_by_path.side_effect = _get_item


def create_mock_prompt_mapping_yaml_data() -> list:
    """Create mock framework prompt mapping data."""
    return [
        {
            "commission_skill_code": "0001",
            "commission_skill": "13_True_Promo_End",
            "common_prompt_path": "config/prompt/common_prompt_0001.txt",
            "check_list_prompt_path": "/sharepoint/prompts/checklist_0001.txt",
        },
        {
            "commission_skill_code": "0002",
            "commission_skill": "13_True_Promo_End",
            "common_prompt_path": "config/prompt/common_prompt_0002.txt",
            "check_list_prompt_path": "/sharepoint/prompts/checklist_0002.txt",
        },
        {
            "commission_skill_code": "0003",
            "commission_skill": "13_True_Promo_End",
            "common_prompt_path": "config/prompt/common_prompt_0003.txt",
            "check_list_prompt_path": "/sharepoint/prompts/checklist_0003.txt",
        },
    ]


@pytest.fixture
def mock_task():
    """Create a PrepPayloadTask instance with mocked dependencies."""
    with patch.object(PrepPayloadTask, "__init__", lambda self, **kwargs: None):
        task = PrepPayloadTask.__new__(PrepPayloadTask)

        # Set required attributes manually
        task.config = {}
        task.packages = {}
        task.task_name = "PrepPayloadTask"
        task.pre_result = None

        task.framework = {
            "lookback_days": "7",
            "concurrency_upload": "5",
            "prompt_mapping_file": "config/prompt/prompt_mapping.yml",
            "system_prompt_file": "config/prompt/system_prompt.txt",
            "common_prompt_file": "/sharepoint/control/0_common_prompt.txt",
            "check_list_prompt_file": "/sharepoint/control/check_list_prompt.xlsx",
        }
        task.sharepoint = {
            "control": {
                "agent_mapping_file": "/sharepoint/control/agent_mapping.xlsx",
                "common_prompt_file": "/sharepoint/control/0_common_prompt.txt",
                "check_list_prompt_file": "/sharepoint/control/check_list_prompt.xlsx",
            },
            "verint": {
                "source_folder": "/sharepoint/verint/",
                "master_folder": "/sharepoint/master/",
                "master_pattern": "*.xlsx",
            },
        }

        # Setup mock modules
        task.gcs_module = Mock()
        task.gcs_module.bucket_name = "test-bucket"
        task.sharepoint_verint = Mock()
        task.sharepoint_control = Mock()
        task.DEFAULT_MASTER_SHEET_NAME = "list"
        task.MASTER_SCHEMA = ["emp_id", "commission_skill_code", "updatedate"]
        task._cache_literal_checklist = {}
        task._cache_mapping_dict = {}
        task._backup_prompt_dict = {}

        return task


class TestMappingPromptTemplateSystemPrompt:
    """Tests for system prompt loading in _mapping_prompt_template."""

    def test_system_prompt_load_failure(self, mock_task):
        """Test behavior when system prompt file cannot be loaded."""
        with patch("tasks.sentiment_telesale.prep_payload_task.read_file", side_effect=FileNotFoundError("Not found")):
            with pytest.raises(Exception, match="Critical error loading system prompt"):
                mock_task._mapping_prompt_template(["file1.wav"])

    def test_empty_system_prompt_raises(self, mock_task):
        """Test behavior when system prompt file is empty."""
        with patch("tasks.sentiment_telesale.prep_payload_task.read_file", return_value="   "):
            with pytest.raises(Exception, match="Critical error loading system prompt"):
                mock_task._mapping_prompt_template(["file1.wav"])


class TestMappingPromptTemplateFrameworkMapping:
    """Tests for SharePoint user prompt loading."""

    def test_common_prompt_not_found_raises(self, mock_task):
        """Test behavior when common_prompt_file is not found on SharePoint."""
        mock_task.sharepoint_control.is_item_exists.return_value = False
        with patch("tasks.sentiment_telesale.prep_payload_task.read_file", return_value="System prompt text"):
            with pytest.raises(Exception, match="Critical error loading user prompts"):
                mock_task._mapping_prompt_template(["file1.wav"])

    def test_empty_common_prompt_raises(self, mock_task):
        """Test behavior when common_prompt_file content is empty."""
        mock_task.sharepoint_control.is_item_exists.return_value = True
        mock_response = Mock()
        mock_response.content = b""
        mock_task.sharepoint_control.get_item_by_path.return_value = mock_response
        with patch("tasks.sentiment_telesale.prep_payload_task.read_file", return_value="System prompt text"):
            with pytest.raises(Exception, match="Critical error loading user prompts"):
                mock_task._mapping_prompt_template(["file1.wav"])


class TestMappingPromptTemplateFileMetadata:
    """Tests for file metadata extraction."""

    def test_files_with_no_valid_agent_id(self, mock_task):
        """Test files where agent_id cannot be extracted return empty mapping."""
        file_list = ["invalid.wav", "bad_format.wav"]
        setup_sharepoint_control_for_prompts(mock_task)
        with (
            patch(
                "tasks.sentiment_telesale.prep_payload_task.read_file",
                return_value="System ${KNOWLEDGE_BASE} ${CAMPAIGN_CHECKLIST}",
            ),
            patch("tasks.sentiment_telesale.prep_payload_task.resolve_env", side_effect=lambda x: x),
        ):
            with patch("tasks.sentiment_telesale.prep_payload_task.safe_list_get", return_value=None):
                result = mock_task._mapping_prompt_template(file_list)

        assert result == {}

    def test_empty_file_list_raises(self, mock_task):
        """An empty file list fails metadata extraction: the empty frame has no agent_id column."""
        setup_sharepoint_control_for_prompts(mock_task)
        with (
            patch(
                "tasks.sentiment_telesale.prep_payload_task.read_file",
                return_value="System ${KNOWLEDGE_BASE} ${CAMPAIGN_CHECKLIST}",
            ),
            pytest.raises(Exception, match="Critical error processing file metadata"),
        ):
            mock_task._mapping_prompt_template([])


class TestMappingPromptTemplateAgentMapping:
    """Tests for agent mapping from SharePoint."""

    def test_missing_agent_mapping_file_returns_empty(self, mock_task):
        """Test when agent mapping file is not found in SharePoint."""
        file_list = ["folder/voice_call_recording_12345_extra_extra_extra_20260127_file.wav"]

        setup_sharepoint_control_for_prompts(mock_task)
        # Override verint to simulate missing master file
        mock_task.sharepoint_verint.is_item_exists.return_value = False
        mock_task.sharepoint_verint.list_files_pattern.return_value = ["master_20260127.xlsx"]

        with (
            patch(
                "tasks.sentiment_telesale.prep_payload_task.read_file",
                return_value="System ${KNOWLEDGE_BASE} ${CAMPAIGN_CHECKLIST}",
            ),
            patch("tasks.sentiment_telesale.prep_payload_task.resolve_env", side_effect=lambda x: x),
            patch(
                "tasks.sentiment_telesale.prep_payload_task.resolve_date",
                side_effect=lambda text, replace_date: text,
            ),
        ):
            result = mock_task._mapping_prompt_template(file_list)
            assert result == {}
