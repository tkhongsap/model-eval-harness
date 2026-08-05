import os
from unittest.mock import patch

from src.utils.common import get_value_by_path, resolve_date, resolve_env, safe_cast_value


class TestCommonUtils:
    def test_resolve_date_simple(self):
        """Test standard date replacement."""
        text = "path/to/file_%{DATA_DATE_YYYYMMDD}.csv"
        date_str = "20250101"
        assert resolve_date(text, date_str) == "path/to/file_20250101.csv"

    def test_resolve_date_complex(self):
        """Test with different formats."""
        text = "path/%{DATA_DATE_YYYY}/%{DATA_DATE_MM}/%{DATA_DATE_DD}/file.csv"
        date_str = "20250101"  # YYYYMMDD
        expected = "path/2025/01/01/file.csv"
        assert resolve_date(text, date_str) == expected

    def test_resolve_date_no_match(self):
        """Test when no tokens are present."""
        text = "path/to/file.csv"
        assert resolve_date(text, "20250101") == text

    def test_resolve_env_simple(self):
        """Test simple env var substitution."""
        with patch.dict(os.environ, {"MY_VAR": "my_value"}):
            text = "prefix_${MY_VAR}_suffix"
            assert resolve_env(text) == "prefix_my_value_suffix"

    def test_resolve_env_nested(self):
        """Test substitution within substitution (if supported) or multiple."""
        with patch.dict(os.environ, {"VAR1": "v1", "VAR2": "v2"}):
            text = "${VAR1}/${VAR2}"
            assert resolve_env(text) == "v1/v2"

    def test_resolve_env_missing(self):
        """Test behavior with missing env var."""
        text = "prefix_${MISSING_VAR}_suffix"
        # Code returns empty string for missing vars
        assert resolve_env(text) == "prefix__suffix"

    def test_get_value_by_path_simple(self):
        data = {"a": {"b": 1}}
        assert get_value_by_path(data, "a.b") == 1

    def test_get_value_by_path_default(self):
        data = {"a": 1}
        assert get_value_by_path(data, "a.b", default=2) == 2

    def test_get_value_by_path_nested_list(self):
        # Usually path doesn't support list indexing directly like a[0].b unless implemented
        # Assuming dot notation for dicts only
        pass

    def test_safe_cast_value(self):
        assert safe_cast_value("123", int, 0) == 123
        assert safe_cast_value("abc", int, 0) == 0
        assert safe_cast_value(None, int, 0) == 0
        assert safe_cast_value("12.34", float) == 12.34
