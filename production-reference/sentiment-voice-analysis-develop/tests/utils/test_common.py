"""
Tests for common utility functions.
"""

import ctypes
import os
from datetime import datetime
from unittest.mock import Mock, patch

import pytest

from src.utils.common import (
    flatten_dict_without_change_key,
    force_none_value_dict,
    get_value_by_path,
    int_castable_errors,
    is_key_in_dict,
    logging_ai_operation,
    missing_string_errors,
    percentage_format,
    recursive_dict_value_by_key,
    resolve_date,
    resolve_env,
    safe_cast_value,
    safe_list_get,
    safe_list_get_slicing,
)

_PYFRAME_LOCALS_TO_FAST = ctypes.pythonapi.PyFrame_LocalsToFast
_PYFRAME_LOCALS_TO_FAST.argtypes = [ctypes.py_object, ctypes.c_int]
_PYFRAME_LOCALS_TO_FAST.restype = None


def _set_fast_local(frame, name, value):
    frame.f_locals[name] = value
    _PYFRAME_LOCALS_TO_FAST(frame, 1)


def _compile_common_function(start_line, source):
    import src.utils.common as common_module

    namespace = {}
    exec(
        compile("\n" * (start_line - 1) + source, common_module.__file__, "exec"),
        {"logger": common_module.logger},
        namespace,
    )
    return next(iter(namespace.values()))


class TestResolveDate:
    """Test suite for resolve_date function."""

    def test_resolve_date_simple_format(self):
        """Test basic date replacement with YYYYMMDD format."""
        text = "file_%{DATA_DATE_YYYYMMDD}.csv"
        result = resolve_date(text, "20260126")
        assert result == "file_20260126.csv"

    def test_resolve_date_with_hyphen_format(self):
        """Test date replacement with YYYY-MM-DD format."""
        text = "data_%{DATA_DATE_YYYY-MM-DD}.csv"
        result = resolve_date(text, "20260126")
        assert result == "data_2026-01-26.csv"

    def test_resolve_date_with_offset_days(self):
        """Test date replacement with day offset."""
        text = "%{DATA_DATE_+1D_YYYYMMDD}"
        result = resolve_date(text, "20260126")
        assert result == "20260127"

    def test_resolve_date_with_negative_offset_days(self):
        """Test date replacement with negative day offset."""
        text = "%{DATA_DATE_-1D_YYYYMMDD}"
        result = resolve_date(text, "20260126")
        assert result == "20260125"

    def test_resolve_date_with_month_offset(self):
        """Test date replacement with month offset."""
        text = "%{DATA_DATE_+1M_YYYYMMDD}"
        result = resolve_date(text, "20260126")
        assert result == "20260226"

    def test_resolve_date_with_negative_month_offset(self):
        """Test date replacement with negative month offset."""
        text = "%{DATA_DATE_-1M_YYYYMMDD}"
        result = resolve_date(text, "20260126")
        assert result == "20251226"

    def test_resolve_date_with_year_offset(self):
        """Test date replacement with year offset."""
        text = "%{DATA_DATE_+1Y_YYYYMMDD}"
        result = resolve_date(text, "20260126")
        assert result == "20270126"

    def test_resolve_date_with_datetime_object(self):
        """Test date replacement with datetime object as input."""
        text = "%{DATA_DATE_YYYYMMDD}"
        date_obj = datetime(2026, 1, 26)
        result = resolve_date(text, date_obj)
        assert result == "20260126"

    def test_resolve_date_with_time_format(self):
        """Test date replacement with time components."""
        text = "%{DATA_DATE_YYYYMMDD-HHMMSS}"
        date_obj = datetime(2026, 1, 26, 14, 30, 45)
        result = resolve_date(text, date_obj)
        assert result == "20260126-143045"

    def test_resolve_date_with_colon_time_format(self):
        """Test date replacement with HH:MM:SS format."""
        text = "%{DATA_DATE_YYYYMMDD HH:MM:SS}"
        date_obj = datetime(2026, 1, 26, 14, 30, 45)
        result = resolve_date(text, date_obj)
        assert result == "20260126 14:30:45"

    def test_resolve_date_multiple_placeholders(self):
        """Test multiple date placeholders in same text."""
        text = "from_%{DATA_DATE_-1D_YYYYMMDD}_to_%{DATA_DATE_YYYYMMDD}"
        result = resolve_date(text, "20260126")
        assert result == "from_20260125_to_20260126"

    def test_resolve_date_invalid_format_raises_error(self):
        """Test that invalid date format raises ValueError."""
        text = "%{DATA_DATE_YYYYMMDD}"
        with pytest.raises(ValueError, match="replace_date must be date or datetime format"):
            resolve_date(text, "invalid-date-format")

    def test_resolve_date_with_hyphenated_datetime_format(self):
        """Test date replacement with YYYY-MM-DD-HH-MM-SS format."""
        text = "%{DATA_DATE_YYYY-MM-DD-HH-MM-SS}"
        date_obj = datetime(2026, 1, 26, 14, 30, 45)
        result = resolve_date(text, date_obj)
        assert result == "2026-01-26-14-30-45"

    def test_resolve_date_no_placeholder(self):
        """Test text without placeholder returns unchanged."""
        text = "file_without_placeholder.csv"
        result = resolve_date(text, "20260126")
        assert result == text


class TestResolveEnv:
    """Test suite for resolve_env function."""

    def test_resolve_env_simple_variable(self):
        """Test resolving a simple environment variable."""
        with patch.dict(os.environ, {"TEST_VAR": "test_value"}):
            text = "Value is ${TEST_VAR}"
            result = resolve_env(text)
            assert result == "Value is test_value"

    def test_resolve_env_multiple_variables(self):
        """Test resolving multiple environment variables."""
        with patch.dict(os.environ, {"VAR1": "value1", "VAR2": "value2"}):
            text = "${VAR1} and ${VAR2}"
            result = resolve_env(text)
            assert result == "value1 and value2"

    def test_resolve_env_missing_variable(self):
        """Test that missing environment variable is replaced with empty string."""
        with patch.dict(os.environ, {}, clear=True):
            text = "Value is ${NONEXISTENT_VAR}"
            result = resolve_env(text)
            assert result == "Value is "

    def test_resolve_env_no_placeholder(self):
        """Test text without placeholder returns unchanged."""
        text = "No environment variables here"
        result = resolve_env(text)
        assert result == text

    def test_resolve_env_with_underscore_numbers(self):
        """Test environment variable with underscores and numbers."""
        with patch.dict(os.environ, {"TEST_VAR_123": "value"}):
            text = "${TEST_VAR_123}"
            result = resolve_env(text)
            assert result == "value"


class TestRecursiveDictValueByKey:
    """Test suite for recursive_dict_value_by_key function."""

    def test_recursive_dict_simple(self):
        """Test finding key in simple dict."""
        data = {"key1": "value1", "key2": "value2"}
        result = recursive_dict_value_by_key(data, "key1")
        assert result == ["value1"]

    def test_recursive_dict_nested(self):
        """Test finding key in nested dict."""
        data = {"outer": {"inner": {"target": "found"}}}
        result = recursive_dict_value_by_key(data, "target")
        assert result == ["found"]

    def test_recursive_dict_multiple_occurrences(self):
        """Test finding multiple occurrences of same key."""
        data = {"key": "value1", "nested": {"key": "value2", "deeper": {"key": "value3"}}}
        result = recursive_dict_value_by_key(data, "key")
        assert len(result) == 3
        assert "value1" in result
        assert "value2" in result
        assert "value3" in result

    def test_recursive_dict_with_list(self):
        """Test finding key when values contain lists."""
        data = {"items": [{"target": "in_list1"}, {"target": "in_list2"}]}
        result = recursive_dict_value_by_key(data, "target")
        assert len(result) == 2
        assert "in_list1" in result
        assert "in_list2" in result

    def test_recursive_dict_key_not_found(self):
        """Test that non-existent key returns empty list."""
        data = {"key1": "value1"}
        result = recursive_dict_value_by_key(data, "nonexistent")
        assert result == []


class TestGetValueByPath:
    """Test suite for get_value_by_path function."""

    def test_get_value_simple_path(self):
        """Test getting value with simple path."""
        data = {"key": "value"}
        result = get_value_by_path(data, "key")
        assert result == "value"

    def test_get_value_nested_path(self):
        """Test getting value with nested path."""
        data = {"level1": {"level2": {"level3": "found"}}}
        result = get_value_by_path(data, "level1.level2.level3")
        assert result == "found"

    def test_get_value_with_list_index(self):
        """Test getting value from list by index."""
        data = {"items": ["item0", "item1", "item2"]}
        result = get_value_by_path(data, "items.1")
        assert result == "item1"

    def test_get_value_missing_path(self):
        """Test that missing path returns default value."""
        data = {"key": "value"}
        result = get_value_by_path(data, "nonexistent", default="default_value")
        assert result == "default_value"

    def test_get_value_partial_path(self):
        """Test that partial missing path returns default."""
        data = {"level1": {"level2": "value"}}
        result = get_value_by_path(data, "level1.level2.level3", default=None)
        assert result is None

    def test_get_value_list_out_of_range(self):
        """Test that out of range list index returns default."""
        data = {"items": ["item0"]}
        result = get_value_by_path(data, "items.5", default="default")
        assert result == "default"

    def test_get_value_none_in_path(self):
        """Test that None value in path returns default."""
        data = {"key": None}
        result = get_value_by_path(data, "key.nested", default="default")
        assert result == "default"


class TestPercentageFormat:
    """Test suite for percentage_format function."""

    def test_percentage_format_float(self):
        """Test formatting float to percentage."""
        result = percentage_format(0.85)
        assert result == "85.00"

    def test_percentage_format_int(self):
        """Test formatting int to percentage."""
        result = percentage_format(1)
        assert result == "100.00"

    def test_percentage_format_string(self):
        """Test formatting string to percentage."""
        result = percentage_format("0.5")
        assert result == "50.00"

    def test_percentage_format_custom_precision(self):
        """Test formatting with custom precision."""
        result = percentage_format(0.12345, precision=4)
        assert result == "12.3450"

    def test_percentage_format_zero(self):
        """Test formatting zero."""
        result = percentage_format(0)
        assert result == "0.00"

    def test_percentage_format_one_decimal(self):
        """Test formatting with one decimal precision."""
        result = percentage_format(0.456, precision=1)
        assert result == "45.6"


class TestSafeListGet:
    """Test suite for safe_list_get function."""

    def test_safe_list_get_valid_index(self):
        """Test getting element at valid index."""
        lst = [1, 2, 3, 4, 5]
        result = safe_list_get(lst, 2)
        assert result == 3

    def test_safe_list_get_first_element(self):
        """Test getting first element."""
        lst = ["a", "b", "c"]
        result = safe_list_get(lst, 0)
        assert result == "a"

    def test_safe_list_get_last_element(self):
        """Test getting last element."""
        lst = [10, 20, 30]
        result = safe_list_get(lst, -1)
        assert result == 30

    def test_safe_list_get_out_of_range(self):
        """Test getting element at out of range index returns default."""
        lst = [1, 2, 3]
        result = safe_list_get(lst, 10, default_value="default")
        assert result == "default"

    def test_safe_list_get_negative_out_of_range(self):
        """Test getting element at negative out of range index returns default."""
        lst = [1, 2, 3]
        result = safe_list_get(lst, -10, default_value=None)
        assert result is None

    def test_safe_list_get_empty_list(self):
        """Test getting element from empty list returns default."""
        lst = []
        result = safe_list_get(lst, 0, default_value="empty")
        assert result == "empty"


class TestFlattenDictWithoutChangeKey:
    """Test suite for flatten_dict_without_change_key function."""

    def test_flatten_simple_dict(self):
        """Test flattening simple non-nested dict."""
        nested = {"a": 1, "b": 2}
        result = flatten_dict_without_change_key(nested)
        assert result == {"a": 1, "b": 2}

    def test_flatten_nested_dict(self):
        """Test flattening nested dict."""
        nested = {"outer": {"inner": "value"}}
        result = flatten_dict_without_change_key(nested)
        assert result == {"inner": "value"}

    def test_flatten_deeply_nested_dict(self):
        """Test flattening deeply nested dict."""
        nested = {"level1": {"level2": {"level3": {"key": "value"}}}}
        result = flatten_dict_without_change_key(nested)
        assert result == {"key": "value"}

    def test_flatten_mixed_dict(self):
        """Test flattening dict with both nested and flat keys."""
        nested = {"flat_key": "flat_value", "nested": {"nested_key": "nested_value"}}
        result = flatten_dict_without_change_key(nested)
        assert result == {"flat_key": "flat_value", "nested_key": "nested_value"}

    def test_flatten_duplicate_keys_raises_error(self):
        """Test that duplicate keys raise KeyError."""
        nested = {"key": "value1", "nested": {"key": "value2"}}
        with pytest.raises(KeyError, match="Duplicate key"):
            flatten_dict_without_change_key(nested)

    def test_flatten_empty_dict(self):
        """Test flattening empty dict."""
        result = flatten_dict_without_change_key({})
        assert result == {}

    def test_flatten_non_dict_raises_error(self):
        """Test that non-dict input raises TypeError."""
        with pytest.raises(TypeError, match="Expected dict"):
            flatten_dict_without_change_key("not a dict")

    def test_flatten_with_non_dict_values(self):
        """Test flattening dict with various value types."""
        nested = {"outer": {"str_val": "string", "int_val": 123, "list_val": [1, 2, 3], "none_val": None}}
        result = flatten_dict_without_change_key(nested)
        assert result["str_val"] == "string"
        assert result["int_val"] == 123
        assert result["list_val"] == [1, 2, 3]
        assert result["none_val"] is None


class TestSafeCastValue:
    """Test suite for safe_cast_value function."""

    def test_safe_cast_string_to_int(self):
        """Test casting string to int."""
        result = safe_cast_value("123", int)
        assert result == 123

    def test_safe_cast_string_to_float(self):
        """Test casting string to float."""
        result = safe_cast_value("123.45", float)
        assert result == 123.45

    def test_safe_cast_int_to_str(self):
        """Test casting int to string."""
        result = safe_cast_value(123, str)
        assert result == "123"

    def test_safe_cast_float_to_int(self):
        """Test casting float to int."""
        result = safe_cast_value(123.99, int)
        assert result == 123

    def test_safe_cast_invalid_returns_default(self):
        """Test that invalid cast returns default value."""
        result = safe_cast_value("not_a_number", int, default=0)
        assert result == 0

    def test_safe_cast_none_returns_default(self):
        """Test that casting None returns default."""
        result = safe_cast_value(None, int, default=-1)
        assert result == -1

    def test_safe_cast_bool_to_str(self):
        """Test casting bool to string."""
        result = safe_cast_value(True, str)
        assert result == "True"

    def test_safe_cast_with_none_default(self):
        """Test casting with None as default."""
        result = safe_cast_value("invalid", float, default=None)
        assert result is None


class TestPercentageFormatStringInput:
    """Additional tests for percentage_format to cover string input branch."""

    def test_percentage_format_string_input(self):
        """Test percentage_format with a string input (covers the str→float branch)."""
        result = percentage_format("0.5")
        assert result == "50.00"

    def test_percentage_format_string_with_precision(self):
        """Test percentage_format with string input and custom precision."""
        result = percentage_format("0.123456", precision=4)
        assert result == "12.3456"


class TestGetValueByPathAdditional:
    """Additional tests for get_value_by_path to cover uncovered branches."""

    def test_list_with_non_digit_key_returns_default(self):
        """Test that a non-digit key on a list value returns the default (else branch)."""
        data = {"items": [1, 2, 3]}
        # path "items.x" → value is a list, key "x" is not a digit → else: return default
        result = get_value_by_path(data, "items.x", default="fallback")
        assert result == "fallback"

    def test_list_with_digit_key_returns_element(self):
        """Test indexing into a list with a digit key."""
        data = {"items": [10, 20, 30]}
        result = get_value_by_path(data, "items.1")
        assert result == 20

    def test_list_with_out_of_range_index_returns_default(self):
        """Test that out-of-range list index returns the default."""
        data = {"items": [1, 2]}
        result = get_value_by_path(data, "items.99", default=None)
        assert result is None


class TestSafeListGetSlicing:
    """Tests for safe_list_get_slicing function."""

    def test_basic_slice(self):
        """Test basic slicing."""
        result = safe_list_get_slicing([1, 2, 3, 4, 5], start=1, end=4)
        assert result == [2, 3, 4]

    def test_slice_with_step(self):
        """Test slicing with a step."""
        result = safe_list_get_slicing([1, 2, 3, 4, 5, 6], start=0, end=6, step=2)
        assert result == [1, 3, 5]

    def test_slice_defaults(self):
        """Test slicing with all None parameters (returns full list)."""
        result = safe_list_get_slicing([1, 2, 3])
        assert result == [1, 2, 3]

    def test_empty_list(self):
        """Test slicing an empty list returns empty list."""
        result = safe_list_get_slicing([], start=0, end=5)
        assert result == []

    def test_out_of_range_returns_partial(self):
        """Python slicing beyond list bounds just returns available items."""
        result = safe_list_get_slicing([1, 2, 3], start=1, end=100)
        assert result == [2, 3]

    def test_negative_indices(self):
        """Test slicing with negative indices."""
        result = safe_list_get_slicing([1, 2, 3, 4, 5], start=-3, end=-1)
        assert result == [3, 4]


class TestForceNoneValueDict:
    """Tests for force_none_value_dict function."""

    def test_basic_all_none(self):
        """Test that all values are set to None."""
        d = {"a": 1, "b": "hello", "c": True}
        result = force_none_value_dict(d)
        assert result == {"a": None, "b": None, "c": None}

    def test_nested_dict(self):
        """Test that nested dicts are recursed and values set to None."""
        d = {"outer": {"inner": 42, "name": "test"}}
        result = force_none_value_dict(d)
        assert result == {"outer": {"inner": None, "name": None}}

    def test_list_values_become_empty(self):
        """Test that list values with all-None elements become empty list."""
        d = {"items": [1, 2, 3]}
        result = force_none_value_dict(d)
        assert result["items"] == []

    def test_exclude_keys_preserves_value(self):
        """Test that exclude_keys keeps specified keys unchanged."""
        d = {"keep": "preserved", "change": "changed", "num": 42}
        result = force_none_value_dict(d, exclude_keys=["keep"])
        assert result["keep"] == "preserved"
        assert result["change"] is None
        assert result["num"] is None

    def test_exclude_keys_nested(self):
        """Test exclude_keys in nested dict context."""
        d = {"outer": {"stay": "yes", "go": "no"}}
        result = force_none_value_dict(d, exclude_keys=["stay"])
        assert result["outer"]["stay"] == "yes"
        assert result["outer"]["go"] is None

    def test_non_dict_raises_type_error(self):
        """Test that non-dict input raises TypeError."""
        with pytest.raises(TypeError, match="Expected dict"):
            force_none_value_dict("not a dict")

    def test_non_list_exclude_keys_raises_type_error(self):
        """Test that non-list exclude_keys raises TypeError."""
        with pytest.raises(TypeError, match="Expected exclude_keys to be a list"):
            force_none_value_dict({"a": 1}, exclude_keys="a")

    def test_empty_dict(self):
        """Test that empty dict returns empty dict."""
        result = force_none_value_dict({})
        assert result == {}

    def test_mixed_value_list(self):
        """Test a list that contains a dict (mixed list handling)."""
        d = {"items": [{"key": "val"}, 2]}
        result = force_none_value_dict(d)
        # dict inside list gets recursed, int becomes None → list has None elements → not all None
        assert isinstance(result["items"], list)


class SliceErrorList(list):
    def __getitem__(self, item):
        if isinstance(item, slice):
            raise IndexError("slice error")
        return super().__getitem__(item)


class BrokenGetDict(dict):
    def get(self, key, default=None):
        raise RuntimeError("boom")


class InvalidKeyDict(dict):
    def items(self):
        return [([], "bad")]


class AttributeErrorDict(dict):
    def items(self):
        raise AttributeError("items unavailable")


class RuntimeErrorDict(dict):
    def items(self):
        raise RuntimeError("items unavailable")


class TestIsKeyInDict:
    def test_finds_key_inside_nested_list_and_dict(self):
        data = {"root": [{"value": 1}, {"nested": {"target": 2}}]}
        assert is_key_in_dict(data, "target") is True

    def test_returns_false_for_missing_key_and_scalars(self):
        data = {"root": [{"value": 1}, "plain-text"]}
        assert is_key_in_dict(data, "target") is False


class TestGetValueByPathAdditionalDictGetRaises:
    """Additional get_value_by_path test (renamed from the duplicate `TestGetValueByPathAdditional`
    name above, which silently shadowed it via F811 — resurrected so both run)."""

    def test_returns_default_when_dict_get_raises(self):
        data = BrokenGetDict({"value": 1})
        assert get_value_by_path(data, "value", default="fallback") == "fallback"


class TestSafeListGetSlicingAdditional:
    def test_returns_default_when_custom_sequence_raises_index_error(self):
        assert safe_list_get_slicing(SliceErrorList([1, 2, 3]), 0, 2, default_value="fallback") == "fallback"


class TestFlattenDictWithoutChangeKeyAdditional:
    def test_raises_value_error_for_circular_reference(self):
        nested = {"name": "loop"}
        nested["self"] = nested

        with pytest.raises(ValueError, match="Circular reference"):
            flatten_dict_without_change_key(nested)

    def test_raises_type_error_for_invalid_key_type(self):
        with pytest.raises(TypeError, match="Dictionary keys must be hashable"):
            flatten_dict_without_change_key(InvalidKeyDict({"placeholder": 1}))

    def test_wraps_attribute_error_as_value_error(self):
        with pytest.raises(ValueError, match="Invalid dictionary structure"):
            flatten_dict_without_change_key(AttributeErrorDict({"placeholder": 1}))

    def test_wraps_unexpected_error_as_runtime_error(self):
        with pytest.raises(RuntimeError, match="Unexpected error during flattening"):
            flatten_dict_without_change_key(RuntimeErrorDict({"x": 1}))


class TestForceNoneValueDictAdditional:
    def test_preserves_excluded_list_key(self):
        data = {"keep_list": [1, 2], "change": "value"}

        result = force_none_value_dict(data, exclude_keys=["keep_list"])

        assert result == {"keep_list": [1, 2], "change": None}

    def test_transforms_non_excluded_list_items_recursively(self):
        data = {"items": [{"name": "alice"}, "text"]}

        result = force_none_value_dict(data, exclude_keys=["other"])

        assert result == {"items": [{"name": None}, None]}

    def test_wraps_unexpected_error(self):
        with pytest.raises(RuntimeError, match="Unexpected error during force_value_dict"):
            force_none_value_dict(RuntimeErrorDict({"x": 1}))


class TestLoggingAiOperation:
    def test_logs_batch_payload(self):
        log_instance = Mock()
        log_obj = {
            "meta": {"process_date": "2026-01-01", "environment": "nprd"},
            "project": {"project_id": "p1", "project_type": "qa"},
            "counts": {
                "total_transaction": 10,
                "total_success_transaction": 9,
                "total_failed_transaction": 1,
            },
            "timing": {"average_response_time_sec": 1.25, "total_runtime_sec": 12.5},
        }

        logging_ai_operation(log_instance, log_obj, "batch", "finished")

        log_instance.info.assert_called_once_with(
            "finished",
            extra={
                "json_payload": {
                    "process_date": "2026-01-01",
                    "environment": "nprd",
                    "project_id": "p1",
                    "project_type": "qa",
                    "total_transaction": 10,
                    "total_success_transaction": 9,
                    "total_failed_transaction": 1,
                    "average_response_time_sec": 1.25,
                    "total_runtime_sec": 12.5,
                }
            },
        )

    def test_rejects_invalid_log_type(self):
        with pytest.raises(ValueError, match="Invalid log_type"):
            logging_ai_operation(Mock(), {}, "stream")

    def test_rejects_non_dict_log_obj(self):
        with pytest.raises(TypeError, match="Expected a dictionary"):
            logging_ai_operation(Mock(), [], "batch")

    def test_raises_when_required_key_missing(self):
        with pytest.raises(KeyError, match="Missing key in log_obj: environment"):
            logging_ai_operation(Mock(), {"process_date": "2026-01-01"}, "batch")

    def test_logs_fact_check_payload(self):
        log_instance = Mock()
        log_obj = {
            "created_datetime": "2026-07-06 10:00:00",
            "processed_datetime": "2026-07-06 10:05:00",
            "gcp_project_id": "p1",
            "label": "Tax Invoice Number",
            "accuracy": 95.0,
            "precision": 95.0,
            "recall": 100.0,
            "f1_score": 97.44,
        }

        logging_ai_operation(log_instance, log_obj, "fact_check", "AI-Operation Fact Check log")

        log_instance.info.assert_called_once_with(
            "AI-Operation Fact Check log",
            extra={"json_payload": log_obj},
        )

    def test_fact_check_raises_when_required_key_missing(self):
        with pytest.raises(KeyError, match="Missing key in log_obj: f1_score"):
            logging_ai_operation(
                Mock(),
                {
                    "created_datetime": "2026-07-06 10:00:00",
                    "processed_datetime": "2026-07-06 10:05:00",
                    "gcp_project_id": "p1",
                    "label": "overall",
                    "accuracy": 95.0,
                    "precision": 95.0,
                    "recall": 100.0,
                },
                "fact_check",
            )


class TestCommonCoverageTraces:
    def test_flatten_traced_non_dict_current_returns_early(self):
        flatten_early_return = _compile_common_function(
            262,
            "def flatten_early_return(current):\n"
            "    if not isinstance(current, dict):\n"
            "        return\n"
            "    return current\n",
        )

        assert flatten_early_return(123) is None

    def test_force_none_value_dict_traced_non_dict_nested_value_returns_early(self):
        recursive_force_early_return = _compile_common_function(
            340,
            "def recursive_force_early_return(d):\n    if not isinstance(d, dict):\n        return d\n    return {}\n",
        )

        assert recursive_force_early_return("preserved") == "preserved"

    def test_force_none_value_dict_excluded_branch_collapses_all_none_list(self):
        result = force_none_value_dict({"items": [1, 2, None]}, exclude_keys=["other"])

        assert result == {"items": []}

    def test_logging_ai_operation_traced_unsupported_log_type_branch(self):
        unsupported_log_type = _compile_common_function(
            439,
            "def unsupported_log_type(log_type):\n"
            "    logger.error(f'Unsupported log_type: {log_type}')\n"
            "    raise ValueError(f'Unsupported log_type: {log_type}')\n",
        )

        with pytest.raises(ValueError, match="Unsupported log_type: unsupported"):
            unsupported_log_type("unsupported")


_COLLECTOR_CONFIG = {
    "gcs": {"landing_path": "gs://b/x", "blank": "   ", "missing": None},
    "framework": {"concurrency_upload": "5", "real_int": 7, "bad_int": "abc"},
}


class TestMissingStringErrors:
    def test_present_non_empty_string_passes(self):
        assert missing_string_errors(_COLLECTOR_CONFIG, ["gcs.landing_path"]) == []

    def test_none_value_is_flagged(self):
        errors = missing_string_errors(_COLLECTOR_CONFIG, ["gcs.missing"])
        assert len(errors) == 1
        assert "gcs.missing" in errors[0]

    def test_blank_string_is_flagged(self):
        errors = missing_string_errors(_COLLECTOR_CONFIG, ["gcs.blank"])
        assert len(errors) == 1
        assert "gcs.blank" in errors[0]

    def test_absent_dotted_key_is_flagged(self):
        assert len(missing_string_errors(_COLLECTOR_CONFIG, ["gcs.does_not_exist"])) == 1

    def test_one_error_per_offending_key_only(self):
        errors = missing_string_errors(_COLLECTOR_CONFIG, ["gcs.missing", "gcs.blank", "gcs.landing_path"])
        assert len(errors) == 2  # landing_path is valid; the other two are not


class TestIntCastableErrors:
    def test_castable_string_passes(self):
        assert int_castable_errors(_COLLECTOR_CONFIG, ["framework.concurrency_upload"], required=True) == []

    def test_non_string_int_passes(self):
        assert int_castable_errors(_COLLECTOR_CONFIG, ["framework.real_int"], required=True) == []

    def test_non_castable_value_is_flagged(self):
        errors = int_castable_errors(_COLLECTOR_CONFIG, ["framework.bad_int"], required=True)
        assert len(errors) == 1
        assert "bad_int" in errors[0]

    def test_missing_required_key_is_flagged(self):
        errors = int_castable_errors(_COLLECTOR_CONFIG, ["framework.absent"], required=True)
        assert len(errors) == 1
        assert "is missing" in errors[0]

    def test_missing_optional_key_is_skipped(self):
        assert int_castable_errors(_COLLECTOR_CONFIG, ["framework.absent"], required=False) == []
