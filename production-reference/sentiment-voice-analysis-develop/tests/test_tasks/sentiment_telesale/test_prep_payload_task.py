import io
from datetime import datetime
from unittest.mock import Mock, patch

import pandas as pd
import pytest

from tasks.sentiment_telesale.prep_payload_task import PrepPayloadTask


def _create_check_list_excel_bytes(skill_code: int = 1) -> bytes:
    """Create minimal valid 3-sheet check_list_prompt.xlsx bytes."""
    categories = pd.DataFrame(
        {
            "commission_skill_code": [skill_code],
            "commission_skill": ["13_True_Promo_End"],
            "main_category_no": [1],
            "main_category_name": ["Main Cat"],
            "section": ["campaign_description"],
            "order": [1],
            "content": ["Test campaign"],
        }
    )
    subcategories = pd.DataFrame(
        {
            "commission_skill_code": [skill_code],
            "sub_category_no": ["1.1"],
            "sub_category_name": ["Sub Cat"],
            "order": [1],
            "section": ["principle"],
            "content": ["Test principle"],
        }
    )
    tags = pd.DataFrame(
        {
            "commission_skill_code": [skill_code],
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


def _create_empty_check_list_excel_bytes() -> bytes:
    """Create a 3-sheet check_list_prompt.xlsx with valid columns but zero rows (no skill codes)."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        pd.DataFrame(
            columns=[
                "commission_skill_code",
                "commission_skill",
                "main_category_no",
                "main_category_name",
                "section",
                "order",
                "content",
            ]
        ).to_excel(writer, sheet_name="Categories", index=False)
        pd.DataFrame(
            columns=["commission_skill_code", "sub_category_no", "sub_category_name", "order", "section", "content"]
        ).to_excel(writer, sheet_name="Subcategories", index=False)
        pd.DataFrame(
            columns=[
                "commission_skill_code",
                "item_no",
                "tag_code",
                "rule_and_logic",
                "positive_example_th",
                "negative_example_th",
                "is_active",
            ]
        ).to_excel(writer, sheet_name="Tags", index=False)
    buf.seek(0)
    return buf.read()


def _create_master_excel_bytes(emp_id: str = "AGENT1", skill_code: str = "1") -> bytes:
    """Create minimal valid master Excel bytes matching AgentMasterSchema."""
    df = pd.DataFrame(
        {
            "emp_id": [emp_id],
            "commission_skill_code": [skill_code],
            "commission_skill": ["TestSkill"],
            "updatedate": [datetime(2024, 1, 1)],
        }
    )
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="list", index=False)
    buf.seek(0)
    return buf.read()


class _MockResponse:
    def __init__(self, content: bytes):
        self.content = content


class TestPrepPayloadTask:
    @pytest.fixture
    def mock_deps(self):
        with (
            patch("tasks.sentiment_telesale.prep_payload_task.SharePointModule") as sp,
            patch("tasks.sentiment_telesale.prep_payload_task.GCSModule") as gcs,
            patch("tasks.sentiment_telesale.prep_payload_task.load_yaml") as ly,
            patch("tasks.sentiment_telesale.prep_payload_task.read_file") as rf,
            patch("tasks.sentiment_telesale.prep_payload_task.resolve_env") as re,
            patch("tasks.sentiment_telesale.prep_payload_task.resolve_date") as rd,
            patch("tasks.sentiment_telesale.prep_payload_task.asyncio.run") as ar,
        ):
            re.side_effect = lambda x: x
            rd.side_effect = lambda text, replace_date: text

            # Mock framework mapping and common config
            def mock_load_yaml(path):
                if "common.yml" in str(path):
                    return {
                        "verint": {"site_domain": "v.com"},
                        "control": {"site_domain": "c.com"},
                        "framework": {"timezone": "UTC"},
                    }
                return [
                    {
                        "commission_skill_code": "13",
                        "commission_skill": "13_True_Promo_End",
                        "common_prompt_path": "common_prompt.txt",
                        "check_list_prompt_path": "checklist.txt",
                    }
                ]

            ly.side_effect = mock_load_yaml
            rf.return_value = "System Prompt ${KNOWLEDGE_BASE} ${CAMPAIGN_CHECKLIST}"
            yield sp, gcs, ly, rf, re, rd, ar

    @pytest.fixture
    def task(self, mock_deps):
        t = PrepPayloadTask()
        t.gcs = {
            "project_id": "p",
            "bucket_name": "b",
            "input_folder": "in",
            "processing_voice_folder": "proc",
            "output_folder": "out",
            "processing_batch_folder": "batch",
        }
        t.framework = {
            "lookback_days": "7",
            "concurrency_upload": "10",
            "system_prompt_file": "sys.txt",
            "prompt_mapping_file": "map.yml",
            "common_prompt_file": "/sharepoint/prompts/0_common_prompt.txt",
            "check_list_prompt_file": "/sharepoint/prompts/check_list_prompt.xlsx",
        }
        t.sharepoint = {
            "control": {
                "common_prompt_file": "/sharepoint/prompts/0_common_prompt.txt",
                "check_list_prompt_file": "/sharepoint/prompts/check_list_prompt.xlsx",
            }
        }
        t.packages = {"execution_dt": datetime(2025, 1, 1)}
        t.get_package = lambda k, d=None: t.packages.get(k, d)

        # Mock initialized modules
        t.sharepoint_verint = Mock()
        t.sharepoint_control = Mock()
        t.gcs_module = Mock()

        # Default SharePoint control: serve common_prompt text + check_list Excel
        _excel_bytes = _create_check_list_excel_bytes(skill_code=13)

        def _ctrl_get_item(path):
            if "check_list" in str(path):
                return _MockResponse(_excel_bytes)
            return _MockResponse(b"Common prompt text")

        t.sharepoint_control.is_item_exists.return_value = True
        t.sharepoint_control.get_item_by_path.side_effect = _ctrl_get_item

        # Setup common mock returns
        t.gcs_module.project_id = "p"
        t.gcs_module.bucket_name = "b"

        return t

    def test_pre_execute(self, task):
        task.sharepoint_verint = None
        task.pre_execute()
        assert task.sharepoint_verint is not None
        assert task.sharepoint_control is not None
        assert task.gcs_module is not None

    def test_execute_success(self, task, mock_deps):
        _, _, ly, _, _, _, ar = mock_deps

        # Mock _prepare_payload return (now returns 3 values)
        with patch.object(task, "_prepare_payload", return_value=("payload.jsonl", "out_path", [])):
            payload, output, logs = task.execute_task()
            assert payload == "payload.jsonl"
            assert output == "out_path"
            assert logs == []

    def test_prepare_payload(self, task, mock_deps):
        _, _, ly, rf, _, _, ar = mock_deps
        ar.return_value = {"success": 1, "failed": 0}  # Copy success for 1 file

        # 1. Setup mock files
        # Input has 1 file
        task.gcs_module.list_files.side_effect = [
            ["f1_agent1_20250101_OUT.wav"],  # input folder
            ["f1_agent1_20250101_OUT.wav"],  # processing folder (after copy)
        ]
        task.gcs_module.is_dir_exists.return_value = True

        # 2. Setup prompts mapping logic
        # Mock extraction of metadata: 3rd component is agent_id, 7th is date
        # f1_agent1_20250101_OUT.wav -> split breaks down differently depending on underscores
        # Let's assume filename: call_phone_time_AGENT1_first_last_prov_20250101_dur_OUT.wav
        filename = "call_p_t_AGENT1_f_l_prov_20250101_dur_OUT.wav"
        task.gcs_module.list_files.side_effect = [[filename], [filename]]

        # 3. Mock logic inside _mapping_prompt_template
        # Needs to find master file in sharepoint
        task.sharepoint_verint.list_files_pattern.return_value = ["master.xlsx"]
        task.sharepoint_verint.is_item_exists.return_value = True

        # Mock Master Excel (valid bytes so pd.read_excel can parse it)
        task.sharepoint_verint.get_item_by_path.return_value = _MockResponse(
            _create_master_excel_bytes(emp_id="AGENT1", skill_code="13")
        )

        # Run
        payload_path, output_path, processing_log_list = task._prepare_payload(
            "20250101", "20250101", datetime(2025, 1, 1)
        )

        # Verify payload upload
        task.gcs_module.update_content_to_gcs.assert_called()
        # Verify processed logs populated
        assert isinstance(processing_log_list, list)

    def test_mapping_prompt_template_empty_agent(self, task):
        # Test case where agent mapping fails
        task.sharepoint_verint.list_files_pattern.return_value = []
        result = task._mapping_prompt_template(["f1.wav"])
        assert result == {}

    # --- pre_execute error branches ---

    def test_pre_execute_sharepoint_control_init_fails(self, mock_deps):
        """Lines 114-116: SP Control init raises → exception propagates."""
        sp, gcs, ly, rf, re, rd, ar = mock_deps
        sp.side_effect = Exception("SP Control connection failed")
        t = PrepPayloadTask()
        t.control_access = {"site_domain": "c.com"}
        t.verint_access = {"site_domain": "v.com"}
        with pytest.raises(Exception, match="SP Control connection failed"):
            t.pre_execute()

    def test_pre_execute_sharepoint_verint_init_fails(self, mock_deps):
        """Lines 128-130: SP Verint init raises after SP Control succeeds."""
        sp, gcs, ly, rf, re, rd, ar = mock_deps
        # First call succeeds (SP Control), second raises (SP Verint)
        sp.side_effect = [Mock(), Exception("SP Verint connection failed")]
        t = PrepPayloadTask()
        t.control_access = {"site_domain": "c.com"}
        t.verint_access = {"site_domain": "v.com"}
        with pytest.raises(Exception, match="SP Verint connection failed"):
            t.pre_execute()

    def test_pre_execute_gcs_init_fails(self, mock_deps):
        """Lines 141-143: GCS init raises → exception propagates."""
        sp, gcs, ly, rf, re, rd, ar = mock_deps
        gcs.side_effect = Exception("GCS init failed")
        t = PrepPayloadTask()
        t.control_access = {"site_domain": "c.com"}
        t.verint_access = {"site_domain": "v.com"}
        with pytest.raises(Exception, match="GCS init failed"):
            t.pre_execute()

    # --- execute_task branches ---

    def test_execute_with_rerun_date(self, task, mock_deps):
        """Line 155: When rerun_data_dt is in packages, datadate = rerun_date."""
        task.packages["rerun_data_dt"] = "2025-01-15"
        with patch.object(task, "_prepare_payload", return_value=("p.jsonl", "out", [])) as m:
            payload, output, logs = task.execute_task()
            assert payload == "p.jsonl"
            # Verify _prepare_payload was called with dates derived from rerun_date
            m.assert_called_once()

    def test_execute_prepare_payload_raises(self, task):
        """Lines 192-194: When _prepare_payload raises, execute_task re-raises."""
        with patch.object(task, "_prepare_payload", side_effect=Exception("payload error")):
            with pytest.raises(Exception, match="Cannot prepare batch payload"):
                task.execute_task()

    # --- _prepare_payload branches ---

    def test_prepare_payload_dir_not_exists_skips(self, task, mock_deps):
        """Line 265: When GCS dir doesn't exist for a date, that date is skipped."""
        _, _, ly, rf, _, _, ar = mock_deps
        ar.return_value = {"success": 0, "failed": 0}
        task.gcs_module.list_files.return_value = []
        task.gcs_module.is_dir_exists.return_value = False  # Dir does NOT exist

        payload_path, output_path, log_list = task._prepare_payload("20250101", "20250101", datetime(2025, 1, 1))
        # No payloads created → returns None
        assert payload_path is None

    def test_prepare_payload_empty_input_files_skips(self, task, mock_deps):
        """Lines 268-270: When list_files returns empty, skips the date."""
        _, _, ly, rf, _, _, ar = mock_deps
        ar.return_value = {"success": 0, "failed": 0}
        task.gcs_module.is_dir_exists.return_value = True
        task.gcs_module.list_files.return_value = []  # Empty input folder

        payload_path, output_path, log_list = task._prepare_payload("20250101", "20250101", datetime(2025, 1, 1))
        assert payload_path is None

    def test_prepare_payload_no_payloads_returns_none(self, task, mock_deps):
        """Lines 379-380: When no payloads, return (None, None, None)."""
        _, _, ly, rf, _, _, ar = mock_deps
        ar.return_value = {"success": 1, "failed": 0}
        task.gcs_module.is_dir_exists.return_value = True

        # Input has one file but prompt_mappings returns empty (no match)
        task.gcs_module.list_files.return_value = ["proc/f1_a_b_AGENT1_fn_ln_prov_20250101_dur_OUT.wav"]
        task.sharepoint_verint.list_files_pattern.return_value = []

        payload_path, output_path, log_list = task._prepare_payload("20250101", "20250101", datetime(2025, 1, 1))
        assert payload_path is None

    def test_prepare_payload_copy_result_with_failures(self, task, mock_deps):
        """Lines 276-279: Copy result with failures logs warning but continues."""
        _, _, ly, rf, _, _, ar = mock_deps
        # copy_files_batch returns partial failure
        ar.return_value = {"success": 0, "failed": 1, "errors": ["copy failed"]}
        task.gcs_module.is_dir_exists.return_value = True
        task.gcs_module.list_files.return_value = ["proc/f1_a_b_AGENT1_fn_ln_prov_20250101_dur_OUT.wav"]
        task.sharepoint_verint.list_files_pattern.return_value = []

        # Should not raise; just copies with warning
        payload_path, output_path, log_list = task._prepare_payload("20250101", "20250101", datetime(2025, 1, 1))
        assert payload_path is None

    # --- post_execute ---

    def test_post_execute_calls_cleanup(self, task):
        """Covers the post_execute method body."""
        task.gcs_module.cleanup_empty_dir_markers.return_value = None
        result = task.post_execute(result="test_result")
        assert result == "test_result"
        task.gcs_module.cleanup_empty_dir_markers.assert_called_once()

    def test_post_execute_cleanup_error_does_not_raise(self, task):
        """post_execute swallows cleanup errors."""
        task.gcs_module.cleanup_empty_dir_markers.side_effect = Exception("cleanup error")
        result = task.post_execute(result=None)  # Should not raise
        assert result is None

    # --- Additional _prepare_payload branch coverage ---

    def _setup_single_date_payload(self, task, mock_deps, processing_file, mapping_override=None):
        """Helper: set up mocks for a single-date _prepare_payload run with one file."""
        _, _, ly, rf, _, _, ar = mock_deps
        ar.return_value = {"success": 1, "failed": 0}
        task.gcs_module.is_dir_exists.return_value = True
        task.gcs_module.list_files.side_effect = [["in/x.wav"], [processing_file]]
        return {processing_file: mapping_override or ["valid prompt", "SKILL1"]}

    def test_prepare_payload_prompt_text_none(self, task, mock_deps):
        """Lines 308-311: prompt_text is None → stamp_error and skip file."""
        pf = "proc/20250101/call_p_t_AGENT1_f_l_prov_20250101_dur_OUT.wav"
        mapping = self._setup_single_date_payload(task, mock_deps, pf, [None, "SKILL1"])
        with patch.object(task, "_mapping_prompt_template", return_value=mapping):
            result = task._prepare_payload("20250101", "20250101", datetime(2025, 1, 1))
        assert result == (None, None, None)

    def test_prepare_payload_validate_match_none(self, task, mock_deps):
        """Lines 315-318: validate_match is None → stamp_error and skip file."""
        pf = "proc/20250101/call_p_t_AGENT1_f_l_prov_20250101_dur_OUT.wav"
        mapping = self._setup_single_date_payload(task, mock_deps, pf, ["prompt text", None])
        with patch.object(task, "_mapping_prompt_template", return_value=mapping):
            result = task._prepare_payload("20250101", "20250101", datetime(2025, 1, 1))
        assert result == (None, None, None)

    def test_prepare_payload_filter_call_no_match(self, task, mock_deps):
        """Lines 321-324: file doesn't match call type filter (_OUT. required but not present)."""
        pf = "proc/20250101/call_p_t_AGENT1_f_l_prov_20250101_dur_IN.wav"
        mapping = self._setup_single_date_payload(task, mock_deps, pf, ["prompt text", "SKILL1"])
        with patch.object(task, "_mapping_prompt_template", return_value=mapping):
            result = task._prepare_payload("20250101", "20250101", datetime(2025, 1, 1))
        assert result == (None, None, None)

    def test_prepare_payload_unsupported_mime_type(self, task, mock_deps):
        """Lines 339-341: file extension not in mime map → skip file."""
        pf = "proc/20250101/call_p_t_AGENT1_f_l_prov_20250101_dur_OUT.xyz"
        mapping = self._setup_single_date_payload(task, mock_deps, pf, ["prompt text", "SKILL1"])
        with patch.object(task, "_mapping_prompt_template", return_value=mapping):
            result = task._prepare_payload("20250101", "20250101", datetime(2025, 1, 1))
        assert result == (None, None, None)

    def test_prepare_payload_file_not_in_mappings_key_error(self, task, mock_deps):
        """Lines 372-376: file not in prompt_mappings → KeyError caught, stamped, skipped."""
        _, _, ly, rf, _, _, ar = mock_deps
        ar.return_value = {"success": 1, "failed": 0}
        task.gcs_module.is_dir_exists.return_value = True
        pf = "proc/20250101/f1_OUT.wav"
        task.gcs_module.list_files.side_effect = [["in/f1.wav"], [pf]]
        with patch.object(task, "_mapping_prompt_template", return_value={}):
            result = task._prepare_payload("20250101", "20250101", datetime(2025, 1, 1))
        assert result == (None, None, None)

    def test_prepare_payload_mapping_prompt_raises(self, task, mock_deps):
        """Lines 286-288: _mapping_prompt_template raises → propagated."""
        _, _, ly, rf, _, _, ar = mock_deps
        ar.return_value = {"success": 1, "failed": 0}
        task.gcs_module.is_dir_exists.return_value = True
        task.gcs_module.list_files.side_effect = [["in/f1.wav"], ["proc/f1.wav"]]
        with patch.object(task, "_mapping_prompt_template", side_effect=Exception("mapping failed")):
            with pytest.raises(Exception, match="Cannot load prompt mappings"):
                task._prepare_payload("20250101", "20250101", datetime(2025, 1, 1))

    def test_prepare_payload_list_date_fails(self, task, mock_deps):
        """Lines 192-194: list_date raises → exception propagated."""
        with patch("tasks.sentiment_telesale.prep_payload_task.list_date", side_effect=ValueError("bad dates")):
            with pytest.raises(Exception, match="Cannot generate date list"):
                task._prepare_payload("bad", "bad", datetime(2025, 1, 1))

    def test_prepare_payload_is_dir_exists_raises(self, task, mock_deps):
        """Lines 239-241: gcs_module.is_dir_exists raises → date skipped → (None, None, None)."""
        task.gcs_module.is_dir_exists.side_effect = Exception("GCS connection error")
        result = task._prepare_payload("20250101", "20250101", datetime(2025, 1, 1))
        assert result == (None, None, None)

    def test_prepare_payload_input_list_files_raises(self, task, mock_deps):
        """Lines 250-252: first list_files (input) raises → date skipped → (None, None, None)."""
        task.gcs_module.is_dir_exists.return_value = True
        task.gcs_module.list_files.side_effect = Exception("list files failed")
        result = task._prepare_payload("20250101", "20250101", datetime(2025, 1, 1))
        assert result == (None, None, None)

    def test_prepare_payload_copy_files_raises(self, task, mock_deps):
        """Lines 268-270: asyncio.run(copy_files_batch) raises → date skipped → (None, None, None)."""
        _, _, ly, rf, _, _, ar = mock_deps
        task.gcs_module.is_dir_exists.return_value = True
        task.gcs_module.list_files.return_value = ["in/f1.wav"]
        ar.side_effect = Exception("copy failed")
        result = task._prepare_payload("20250101", "20250101", datetime(2025, 1, 1))
        assert result == (None, None, None)

    def test_prepare_payload_processing_list_raises(self, task, mock_deps):
        """Lines 276-279: second list_files (processing) raises → error logged, then NameError on
        on_prcoess_files which bubbles up through the mapping block → propagates as exception."""
        _, _, ly, rf, _, _, ar = mock_deps
        ar.return_value = {"success": 1, "failed": 0}
        task.gcs_module.is_dir_exists.return_value = True
        task.gcs_module.list_files.side_effect = [["in/f1.wav"], Exception("processing list failed")]
        # The second list_files raises for the processing folder; error is logged.
        # Because on_prcoess_files is not assigned, the next block referencing it raises NameError
        # which is caught by the outer _mapping_prompt try/except and re-raised.
        with pytest.raises(Exception):
            task._prepare_payload("20250101", "20250101", datetime(2025, 1, 1))

    def test_execute_date_calculation_fails(self, task, mock_deps):
        """Lines 163-165: add_date raises → execute_task re-raises."""
        with patch("tasks.sentiment_telesale.prep_payload_task.add_date", side_effect=ValueError("bad date")):
            with pytest.raises(Exception, match="Cannot determine processing date range"):
                task.execute_task()

    def test_prepare_payload_empty_processing_folder_skips(self, task, mock_deps):
        """Lines 276-277: processing folder is empty → logs warning and continues → returns None."""
        _, _, ly, rf, _, _, ar = mock_deps
        ar.return_value = {"success": 1, "failed": 0}
        task.gcs_module.is_dir_exists.return_value = True
        # First list_files (input folder) = non-empty, second (processing folder) = empty
        task.gcs_module.list_files.side_effect = [["in/f1.wav"], []]
        result = task._prepare_payload("20250101", "20250101", datetime(2025, 1, 1))
        assert result == (None, None, None)

    def test_mapping_prompt_template_empty_check_list_raises(self, task):
        """Empty check-list Excel (no skill codes) → critical error raised."""
        empty_bytes = _create_empty_check_list_excel_bytes()

        def _ctrl_get_item(path):
            if "check_list" in str(path):
                return _MockResponse(empty_bytes)
            return _MockResponse(b"Common prompt text")

        task.sharepoint_control.get_item_by_path.side_effect = _ctrl_get_item

        with pytest.raises(Exception):
            task._mapping_prompt_template(["f1.wav"])

    def _make_agent_task(self, task, mock_deps):
        """Set up task with a proper agent mapping (master file found on SharePoint)."""
        import io

        master_df = pd.DataFrame(
            {
                "emp_id": ["AGENT1"],
                "commission_skill_code": ["SKILL1"],
                "updatedate": [datetime(2024, 1, 1)],
            }
        )
        master_content = Mock()
        mock_excel_bytes = io.BytesIO()
        master_df.to_excel(mock_excel_bytes, index=False, engine="openpyxl")
        master_content.content = mock_excel_bytes.getvalue()

        task.sharepoint = {"verint": {"master_folder": "masters/", "master_pattern": "*.xlsx"}}
        task.sharepoint_verint.list_files_pattern.return_value = ["masters/2025-01-01/emp.xlsx"]
        task.sharepoint_verint.is_item_exists.return_value = True
        task.sharepoint_verint.get_item_by_path.return_value = master_content
        return master_content

    def test_mapping_prompt_template_check_list_not_found(self, task, mock_deps):
        """check_list prompt file not found on SharePoint → critical error raised.
        In the new architecture check_list is loaded once upfront; missing it is fatal."""
        task.sharepoint = {
            "control": {
                "common_prompt_file": "/sharepoint/prompts/0_common_prompt.txt",
                "check_list_prompt_file": "/sharepoint/prompts/check_list_prompt.xlsx",
            },
            "verint": {"master_folder": "masters/", "master_pattern": "*.xlsx"},
        }

        def _is_item_exists(path):
            return "check_list" not in str(path)

        task.sharepoint_control.is_item_exists.side_effect = _is_item_exists

        file_list = ["proc/20250101/call_p_t_AGENT1_f_l_prov_20250101_dur_OUT.wav"]
        with pytest.raises(Exception, match="Critical error loading user prompts"):
            task._mapping_prompt_template(file_list)

    def test_mapping_prompt_template_happy_path(self, task, mock_deps):
        """Happy path: valid agent, master, and prompts → returns file→prompt map."""
        task.sharepoint = {
            "control": {
                "common_prompt_file": "/sharepoint/prompts/0_common_prompt.txt",
                "check_list_prompt_file": "/sharepoint/prompts/check_list_prompt.xlsx",
            },
            "verint": {"master_folder": "masters/", "master_pattern": "*.xlsx"},
        }
        task.sharepoint_verint.list_files_pattern.return_value = ["masters/2025-01-01/emp.xlsx"]
        task.sharepoint_verint.is_item_exists.return_value = True
        task.sharepoint_verint.get_item_by_path.return_value = _MockResponse(
            _create_master_excel_bytes(emp_id="AGENT1", skill_code="13")
        )
        # Fixture's sharepoint_control side_effect handles common_prompt + check_list Excel

        file_list = ["proc/20250101/call_p_t_AGENT1_f_l_prov_20250101_dur_OUT.wav"]
        result = task._mapping_prompt_template(file_list)
        assert isinstance(result, dict)
        assert len(result) >= 1

    def test_mapping_prompt_template_master_not_exists(self, task, mock_deps):
        """Lines 501-502: is_item_exists returns False for master → agent group skipped → returns {}."""
        task.sharepoint = {
            "control": {
                "common_prompt_file": "/sharepoint/prompts/0_common_prompt.txt",
                "check_list_prompt_file": "/sharepoint/prompts/check_list_prompt.xlsx",
            },
            "verint": {"master_folder": "masters/", "master_pattern": "*.xlsx"},
        }
        task.sharepoint_verint.list_files_pattern.return_value = ["masters/2025-01-01/emp.xlsx"]
        task.sharepoint_verint.is_item_exists.return_value = False  # master not found
        file_list = ["proc/20250101/call_p_t_AGENT1_f_l_prov_20250101_dur_OUT.wav"]
        result = task._mapping_prompt_template(file_list)
        assert result == {}

    def test_mapping_prompt_template_short_filename_no_agent(self, task, mock_deps):
        """Lines 456-468: files with short names → agent_id and record_date can't be extracted → warnings."""
        task.sharepoint = {
            "control": {
                "common_prompt_file": "/sharepoint/prompts/0_common_prompt.txt",
                "check_list_prompt_file": "/sharepoint/prompts/check_list_prompt.xlsx",
            },
            "verint": {"master_folder": "masters/", "master_pattern": "*.xlsx"},
        }
        task.sharepoint_verint.list_files_pattern.return_value = []
        # file with too few underscores → agent_id=None, record_date=None → filtered out → returns {}
        file_list = ["proc/f1.wav", "proc/f2.wav"]
        result = task._mapping_prompt_template(file_list)
        assert isinstance(result, dict)

    def test_mapping_prompt_template_empty_system_prompt_raises(self, task, mock_deps):
        """Lines 425-429: system_prompt.strip() is empty → ValueError → raises Exception."""
        _, _, ly, rf, _, _, ar = mock_deps
        rf.return_value = "   "  # empty / whitespace system prompt
        with pytest.raises(Exception, match="Critical error loading system prompt"):
            task._mapping_prompt_template(["proc/f1.wav"])

    def test_prepare_payload_response_schema_empty_warning(self, task, mock_deps):
        """Line 336: pydantic_resolve_refs returns empty dict → warning branch.
        Payload still built but with no responseSchema key in generationConfig."""
        _, _, ly, rf, _, _, ar = mock_deps
        ar.return_value = {"success": 1, "failed": 0}
        task.gcs_module.is_dir_exists.return_value = True
        pf = "proc/20250101/call_p_t_AGENT1_f_l_prov_20250101_dur_OUT.wav"
        task.gcs_module.list_files.side_effect = [["in/f1.wav"], [pf]]
        mapping = {pf: ["valid prompt", "13"]}
        # Populate cache that _mapping_prompt_template would normally fill
        task._cache_mapping_dict["13"] = "13_True_Promo_End"
        with (
            patch.object(task, "_mapping_prompt_template", return_value=mapping),
            patch("tasks.sentiment_telesale.prep_payload_task.pydantic_resolve_refs", return_value={}),
            patch("tasks.sentiment_telesale.prep_payload_task.sanitize_json_schema_enums", return_value={}),
        ):
            result = task._prepare_payload("20250101", "20250101", datetime(2025, 1, 1))
        # pf filename ends in .wav which IS in mime_type map → payload should be created
        assert result != (None, None, None)
        assert result[0] is not None


class TestPrepPayloadTaskAdditional(TestPrepPayloadTask):
    def test_prepare_payload_batch_path_resolution_error(self, task):
        def _resolve_date(text, replace_date):
            if text == "batch":
                raise ValueError("bad batch path")
            return text

        with patch("tasks.sentiment_telesale.prep_payload_task.resolve_date", side_effect=_resolve_date):
            with pytest.raises(Exception, match="Cannot resolve GCS paths"):
                task._prepare_payload("20250101", "20250101", datetime(2025, 1, 1))

    def test_prepare_payload_upload_payload_failure_raises(self, task, mock_deps):
        _, _, _, _, _, _, ar = mock_deps
        ar.return_value = {"success": 1, "failed": 0}
        task.gcs_module.is_dir_exists.return_value = True
        processing_file = "proc/20250101/call_p_t_AGENT1_f_l_prov_20250101_dur_OUT.wav"
        task.gcs_module.list_files.side_effect = [["in/f1.wav"], [processing_file]]
        task.gcs_module.update_content_to_gcs.side_effect = [None, Exception("upload failed")]
        task._cache_mapping_dict["13"] = "13_True_Promo_End"

        with patch.object(task, "_mapping_prompt_template", return_value={processing_file: ["prompt text", "13"]}):
            with pytest.raises(Exception, match="Cannot upload payload to GCS"):
                task._prepare_payload("20250101", "20250101", datetime(2025, 1, 1))


class TestPrepPayloadTaskExtraCoverage(TestPrepPayloadTask):
    def _prompt_sharepoint(self, task, checklist_bytes=None):
        task.sharepoint = {
            "control": {
                "common_prompt_file": "/sharepoint/prompts/0_common_prompt.txt",
                "check_list_prompt_file": "/sharepoint/prompts/check_list_prompt.xlsx",
            },
            "verint": {"master_folder": "masters/", "master_pattern": "*.xlsx"},
        }
        excel_bytes = checklist_bytes or _create_check_list_excel_bytes(skill_code=13)
        task.sharepoint_control.is_item_exists.return_value = True
        task.sharepoint_control.get_item_by_path.side_effect = lambda path: _MockResponse(
            excel_bytes if "check_list" in str(path) else b"Common prompt text"
        )

    def _master_bytes(self, rows):
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            pd.DataFrame(rows).to_excel(writer, sheet_name="list", index=False)
        buf.seek(0)
        return buf.read()

    def _empty_checklist_bytes(self):
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            pd.DataFrame(
                columns=[
                    "commission_skill_code",
                    "commission_skill",
                    "main_category_no",
                    "main_category_name",
                    "section",
                    "order",
                    "content",
                ]
            ).to_excel(writer, sheet_name="Categories", index=False)
            pd.DataFrame(
                columns=["commission_skill_code", "sub_category_no", "sub_category_name", "order", "section", "content"]
            ).to_excel(writer, sheet_name="Subcategories", index=False)
            pd.DataFrame(
                columns=[
                    "commission_skill_code",
                    "item_no",
                    "tag_code",
                    "rule_and_logic",
                    "positive_example_th",
                    "negative_example_th",
                    "is_active",
                ]
            ).to_excel(writer, sheet_name="Tags", index=False)
        buf.seek(0)
        return buf.read()

    @patch("tasks.sentiment_telesale.prep_payload_task.TaskInterface.__init__", return_value=None)
    @patch.object(PrepPayloadTask, "get_config", side_effect=[{}, {}, {}, {}])
    @patch("tasks.sentiment_telesale.prep_payload_task.load_yaml", return_value={"verint": {}, "control": {}})
    def test_init_validate_mapping_assignment_failure(self, mock_load_yaml, mock_get_config, mock_task_init):
        def guarded_setattr(self, name, value):
            if name == "validate_mapping":
                raise RuntimeError("validate mapping failure")
            object.__setattr__(self, name, value)

        with patch.object(PrepPayloadTask, "__setattr__", guarded_setattr):
            with pytest.raises(RuntimeError, match="validate mapping failure"):
                PrepPayloadTask()

    @patch("tasks.sentiment_telesale.prep_payload_task.TaskInterface.__init__", return_value=None)
    @patch.object(PrepPayloadTask, "get_config", side_effect=[{}, {}, {}, {}])
    @patch("tasks.sentiment_telesale.prep_payload_task.load_yaml", return_value={"verint": {}, "control": {}})
    def test_init_filter_call_assignment_failure(self, mock_load_yaml, mock_get_config, mock_task_init):
        def guarded_setattr(self, name, value):
            if name == "filter_call":
                raise RuntimeError("filter mapping failure")
            object.__setattr__(self, name, value)

        with patch.object(PrepPayloadTask, "__setattr__", guarded_setattr):
            with pytest.raises(RuntimeError, match="filter mapping failure"):
                PrepPayloadTask()

    def test_prepare_payload_per_date_path_resolution_failure_skips_date(self, task):
        def fake_resolve_date(text, replace_date):
            if text in ("batch", "out"):
                return text
            raise ValueError("bad daily path")

        with patch("tasks.sentiment_telesale.prep_payload_task.resolve_date", side_effect=fake_resolve_date):
            result = task._prepare_payload("20250101", "20250101", datetime(2025, 1, 1))

        assert result == (None, None, None)

    def test_prepare_payload_backup_prompt_upload_failure_is_non_fatal(self, task, mock_deps):
        _, _, _, _, _, _, ar = mock_deps
        ar.return_value = {"success": 1, "failed": 0}
        processing_file = "proc/20250101/call_p_t_AGENT1_f_l_prov_20250101_dur_OUT.wav"
        task.gcs_module.is_dir_exists.return_value = True
        task.gcs_module.list_files.side_effect = [["in/f1.wav"], [processing_file]]
        task.gcs_module.update_content_to_gcs.side_effect = [Exception("zip failed"), None]
        task._cache_mapping_dict["13"] = "13_True_Promo_End"

        with patch.object(task, "_mapping_prompt_template", return_value={processing_file: ["prompt text", "13"]}):
            result = task._prepare_payload("20250101", "20250101", datetime(2025, 1, 1))

        assert result[0] is not None

    def test_prepare_payload_filter_call_mismatch_skips_file(self, task, mock_deps):
        _, _, _, _, _, _, ar = mock_deps
        ar.return_value = {"success": 1, "failed": 0}
        processing_file = "proc/20250101/call_p_t_AGENT1_f_l_prov_20250101_dur_IN.wav"
        task.gcs_module.is_dir_exists.return_value = True
        task.gcs_module.list_files.side_effect = [["in/f1.wav"], [processing_file]]
        task._cache_mapping_dict["13"] = "13_True_Promo_End"

        with patch.object(task, "_mapping_prompt_template", return_value={processing_file: ["prompt text", "13"]}):
            result = task._prepare_payload("20250101", "20250101", datetime(2025, 1, 1))

        assert result == (None, None, None)

    def test_prepare_payload_unsupported_mime_type_skips_file(self, task, mock_deps):
        _, _, _, _, _, _, ar = mock_deps
        ar.return_value = {"success": 1, "failed": 0}
        processing_file = "proc/20250101/call_p_t_AGENT1_f_l_prov_20250101_dur_OUT.xyz"
        task.gcs_module.is_dir_exists.return_value = True
        task.gcs_module.list_files.side_effect = [["in/f1.wav"], [processing_file]]
        task._cache_mapping_dict["13"] = "13_True_Promo_End"

        with patch.object(task, "_mapping_prompt_template", return_value={processing_file: ["prompt text", "13"]}):
            result = task._prepare_payload("20250101", "20250101", datetime(2025, 1, 1))

        assert result == (None, None, None)

    def test_prepare_payload_payload_creation_failure_marks_log_error(self, task, mock_deps):
        _, _, _, _, _, _, ar = mock_deps
        ar.return_value = {"success": 1, "failed": 0}
        processing_file = "proc/20250101/call_p_t_AGENT1_f_l_prov_20250101_dur_OUT.wav"
        task.gcs_module.is_dir_exists.return_value = True
        task.gcs_module.list_files.side_effect = [["in/f1.wav"], [processing_file]]
        task._cache_mapping_dict["13"] = "13_True_Promo_End"

        with (
            patch.object(task, "_mapping_prompt_template", return_value={processing_file: ["prompt text", "13"]}),
            patch(
                "tasks.sentiment_telesale.prep_payload_task.pydantic_resolve_refs",
                side_effect=RuntimeError("schema failed"),
            ),
        ):
            result = task._prepare_payload("20250101", "20250101", datetime(2025, 1, 1))

        assert result == (None, None, None)

    def test_mapping_prompt_template_empty_skill_codes_raises(self, task):
        self._prompt_sharepoint(task, self._empty_checklist_bytes())

        with pytest.raises(Exception, match="Critical error loading user prompts"):
            task._mapping_prompt_template(["proc/20250101/call_p_t_AGENT1_f_l_prov_20250101_dur_OUT.wav"])

    def test_mapping_prompt_template_missing_agent_metadata_returns_empty_map(self, task):
        self._prompt_sharepoint(task)

        with patch.object(pd.DataFrame, "astype", lambda self, dtype_map: self):
            result = task._mapping_prompt_template(["proc/short.wav"])

        assert result == {}

    def test_mapping_prompt_template_fetch_agent_mapping_failure_returns_empty(self, task):
        self._prompt_sharepoint(task)
        task.sharepoint_verint.list_files_pattern.return_value = ["masters/20250101/emp.xlsx"]
        task.sharepoint_verint.is_item_exists.return_value = True
        task.sharepoint_verint.get_item_by_path.side_effect = RuntimeError("fetch failed")

        result = task._mapping_prompt_template(["proc/20250101/call_p_t_AGENT1_f_l_prov_20250101_dur_OUT.wav"])

        assert result == {}

    def test_mapping_prompt_template_empty_agent_master_is_skipped(self, task):
        self._prompt_sharepoint(task)
        task.sharepoint_verint.list_files_pattern.return_value = ["masters/20250101/emp.xlsx"]
        task.sharepoint_verint.is_item_exists.return_value = True
        task.sharepoint_verint.get_item_by_path.return_value = _MockResponse(
            self._master_bytes(
                [
                    {
                        "emp_id": None,
                        "commission_skill_code": None,
                        "commission_skill": "Skill",
                        "updatedate": datetime(2025, 1, 1),
                    }
                ]
            )
        )

        result = task._mapping_prompt_template(["proc/20250101/call_p_t_AGENT1_f_l_prov_20250101_dur_OUT.wav"])

        assert result == {}

    def test_mapping_prompt_template_drops_invalid_agent_master_rows(self, task):
        self._prompt_sharepoint(task)
        task.sharepoint_verint.list_files_pattern.return_value = ["masters/20250101/emp.xlsx"]
        task.sharepoint_verint.is_item_exists.return_value = True
        task.sharepoint_verint.get_item_by_path.return_value = _MockResponse(
            self._master_bytes(
                [
                    {
                        "emp_id": "AGENT1",
                        "commission_skill_code": "13",
                        "commission_skill": "Skill",
                        "updatedate": datetime(2025, 1, 2),
                    },
                    {
                        "emp_id": None,
                        "commission_skill_code": "13",
                        "commission_skill": "Skill",
                        "updatedate": datetime(2025, 1, 1),
                    },
                ]
            )
        )

        result = task._mapping_prompt_template(["proc/20250101/call_p_t_AGENT1_f_l_prov_20250101_dur_OUT.wav"])

        assert result["proc/20250101/call_p_t_AGENT1_f_l_prov_20250101_dur_OUT.wav"][1] == "13"

    def test_mapping_prompt_template_agent_mapping_failure_then_merge_raises(self, task):
        self._prompt_sharepoint(task)
        task.sharepoint_verint.list_files_pattern.return_value = ["masters/20250101/emp.xlsx"]
        task.sharepoint_verint.is_item_exists.side_effect = RuntimeError("exists failed")

        with pytest.raises(Exception, match="Critical error during dataframe merge operations"):
            task._mapping_prompt_template(["proc/20250101/call_p_t_AGENT1_f_l_prov_20250101_dur_OUT.wav"])

    def test_mapping_prompt_template_unmatched_agents_return_empty(self, task):
        self._prompt_sharepoint(task)
        task.sharepoint_verint.list_files_pattern.return_value = ["masters/20250101/emp.xlsx"]
        task.sharepoint_verint.is_item_exists.return_value = True
        task.sharepoint_verint.get_item_by_path.return_value = _MockResponse(
            self._master_bytes(
                [
                    {
                        "emp_id": "AGENT999",
                        "commission_skill_code": "13",
                        "commission_skill": "Skill",
                        "updatedate": datetime(2025, 1, 1),
                    }
                ]
            )
        )

        result = task._mapping_prompt_template(["proc/20250101/call_p_t_AGENT1_f_l_prov_20250101_dur_OUT.wav"])

        assert result == {}

    def test_mapping_prompt_template_warns_when_prompt_template_missing(self, task):
        self._prompt_sharepoint(task)
        task.sharepoint_verint.list_files_pattern.return_value = ["masters/20250101/emp.xlsx"]
        task.sharepoint_verint.is_item_exists.return_value = True
        task.sharepoint_verint.get_item_by_path.return_value = _MockResponse(
            self._master_bytes(
                [
                    {
                        "emp_id": "AGENT1",
                        "commission_skill_code": "99",
                        "commission_skill": "OtherSkill",
                        "updatedate": datetime(2025, 1, 1),
                    }
                ]
            )
        )

        result = task._mapping_prompt_template(["proc/20250101/call_p_t_AGENT1_f_l_prov_20250101_dur_OUT.wav"])

        assert result["proc/20250101/call_p_t_AGENT1_f_l_prov_20250101_dur_OUT.wav"] == (None, "99")

    def test_mapping_prompt_template_logs_more_than_three_missing_metadata_items(self, task):
        self._prompt_sharepoint(task)

        def fake_safe_list_get(values, index, default=None):
            if index in (3, -3):
                return None
            try:
                return values[index]
            except Exception:
                return default

        with (
            patch("tasks.sentiment_telesale.prep_payload_task.safe_list_get", side_effect=fake_safe_list_get),
            patch(
                "tasks.sentiment_telesale.prep_payload_task.pd.DataFrame.astype", new=lambda self, *args, **kwargs: self
            ),
        ):
            result = task._mapping_prompt_template(
                [
                    "proc/a.wav",
                    "proc/b.wav",
                    "proc/c.wav",
                    "proc/d.wav",
                    "proc/e.wav",
                ]
            )

        assert result == {}

    def test_mapping_prompt_template_logs_more_than_three_unmatched_agents(self, task):
        self._prompt_sharepoint(task)
        task.sharepoint_verint.list_files_pattern.return_value = ["masters/20250101/emp.xlsx"]
        task.sharepoint_verint.is_item_exists.return_value = True
        task.sharepoint_verint.get_item_by_path.return_value = _MockResponse(
            self._master_bytes(
                [
                    {
                        "emp_id": "OTHER",
                        "commission_skill_code": "13",
                        "commission_skill": "Skill",
                        "updatedate": datetime(2025, 1, 1),
                    }
                ]
            )
        )

        result = task._mapping_prompt_template(
            [
                "proc/20250101/call_p_t_AGENT1_f_l_prov_20250101_dur_OUT.wav",
                "proc/20250101/call_p_t_AGENT2_f_l_prov_20250101_dur_OUT.wav",
                "proc/20250101/call_p_t_AGENT3_f_l_prov_20250101_dur_OUT.wav",
                "proc/20250101/call_p_t_AGENT4_f_l_prov_20250101_dur_OUT.wav",
                "proc/20250101/call_p_t_AGENT5_f_l_prov_20250101_dur_OUT.wav",
            ]
        )

        assert result == {}

    def test_mapping_prompt_template_logs_more_than_three_missing_prompt_templates(self, task):
        self._prompt_sharepoint(task)
        task.sharepoint_verint.list_files_pattern.return_value = ["masters/20250101/emp.xlsx"]
        task.sharepoint_verint.is_item_exists.return_value = True
        task.sharepoint_verint.get_item_by_path.return_value = _MockResponse(
            self._master_bytes(
                [
                    {
                        "emp_id": "AGENT1",
                        "commission_skill_code": "99",
                        "commission_skill": "OtherSkill",
                        "updatedate": datetime(2025, 1, 1),
                    },
                    {
                        "emp_id": "AGENT2",
                        "commission_skill_code": "99",
                        "commission_skill": "OtherSkill",
                        "updatedate": datetime(2025, 1, 1),
                    },
                    {
                        "emp_id": "AGENT3",
                        "commission_skill_code": "99",
                        "commission_skill": "OtherSkill",
                        "updatedate": datetime(2025, 1, 1),
                    },
                    {
                        "emp_id": "AGENT4",
                        "commission_skill_code": "99",
                        "commission_skill": "OtherSkill",
                        "updatedate": datetime(2025, 1, 1),
                    },
                    {
                        "emp_id": "AGENT5",
                        "commission_skill_code": "99",
                        "commission_skill": "OtherSkill",
                        "updatedate": datetime(2025, 1, 1),
                    },
                ]
            )
        )

        result = task._mapping_prompt_template(
            [
                "proc/20250101/call_p_t_AGENT1_f_l_prov_20250101_dur_OUT.wav",
                "proc/20250101/call_p_t_AGENT2_f_l_prov_20250101_dur_OUT.wav",
                "proc/20250101/call_p_t_AGENT3_f_l_prov_20250101_dur_OUT.wav",
                "proc/20250101/call_p_t_AGENT4_f_l_prov_20250101_dur_OUT.wav",
                "proc/20250101/call_p_t_AGENT5_f_l_prov_20250101_dur_OUT.wav",
            ]
        )

        assert len(result) == 5

    def test_mapping_prompt_template_returns_empty_when_final_map_empty(self, task):
        self._prompt_sharepoint(task)
        task.sharepoint_verint.list_files_pattern.return_value = ["masters/20250101/emp.xlsx"]
        task.sharepoint_verint.is_item_exists.return_value = True
        task.sharepoint_verint.get_item_by_path.return_value = _MockResponse(
            self._master_bytes(
                [
                    {
                        "emp_id": "AGENT1",
                        "commission_skill_code": "13",
                        "commission_skill": "Skill",
                        "updatedate": datetime(2025, 1, 1),
                    }
                ]
            )
        )

        original_to_dict = pd.DataFrame.to_dict

        def fake_to_dict(self, orient="dict", *args, **kwargs):
            if orient == "records" and {"file_path", "commission_skill_code", "prompt_template"}.issubset(
                set(self.columns)
            ):
                return []
            return original_to_dict(self, *args, orient=orient, **kwargs)

        with patch.object(pd.DataFrame, "to_dict", fake_to_dict):
            result = task._mapping_prompt_template(["proc/20250101/call_p_t_AGENT1_f_l_prov_20250101_dur_OUT.wav"])

        assert result == {}

    def test_mapping_prompt_template_final_replace_failure_raises(self, task):
        self._prompt_sharepoint(task)
        task.sharepoint_verint.list_files_pattern.return_value = ["masters/20250101/emp.xlsx"]
        task.sharepoint_verint.is_item_exists.return_value = True
        task.sharepoint_verint.get_item_by_path.return_value = _MockResponse(
            self._master_bytes(
                [
                    {
                        "emp_id": "AGENT1",
                        "commission_skill_code": "13",
                        "commission_skill": "Skill",
                        "updatedate": datetime(2025, 1, 1),
                    }
                ]
            )
        )

        original_replace = pd.DataFrame.replace

        def fake_replace(self, *args, **kwargs):
            if {"file_path", "commission_skill_code", "prompt_template"}.issubset(set(self.columns)):
                raise RuntimeError("replace failed")
            return original_replace(self, *args, **kwargs)

        with patch.object(pd.DataFrame, "replace", fake_replace):
            with pytest.raises(Exception, match="Critical error creating final prompt mapping"):
                task._mapping_prompt_template(["proc/20250101/call_p_t_AGENT1_f_l_prov_20250101_dur_OUT.wav"])
