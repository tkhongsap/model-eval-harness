"""Unit tests for app/utils/common.py."""
from __future__ import annotations

import io
import os
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from app.utils.common import (
    add_months,
    convert_df_to_excel,
    export_pandas_df_to_markdown,
    get_value_by_path,
    load_yaml,
    load_yaml_string,
    percentage_format,
    pydantic_resolve_refs,
    read_file,
    recursive_dict_value_by_key,
    resolve_date,
    resolve_env,
    safe_list_get,
)


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------

class TestReadFile:
    def test_reads_with_str_path(self, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_text("hello world", encoding="utf-8")
        assert read_file(str(f)) == "hello world"

    def test_reads_with_path_object(self, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_text("path object", encoding="utf-8")
        assert read_file(f) == "path object"

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            read_file(str(tmp_path / "missing.txt"))


# ---------------------------------------------------------------------------
# load_yaml
# ---------------------------------------------------------------------------

class TestLoadYaml:
    def test_loads_yaml_from_str_path(self, tmp_path: Path) -> None:
        f = tmp_path / "config.yaml"
        f.write_text("key: value\nnumber: 42\n", encoding="utf-8")
        result = load_yaml(str(f))
        assert result == {"key": "value", "number": 42}

    def test_loads_yaml_from_path_object(self, tmp_path: Path) -> None:
        f = tmp_path / "config.yaml"
        f.write_text("a: 1\n", encoding="utf-8")
        assert load_yaml(f) == {"a": 1}


# ---------------------------------------------------------------------------
# load_yaml_string
# ---------------------------------------------------------------------------

class TestLoadYamlString:
    def test_parses_valid_yaml(self) -> None:
        result = load_yaml_string("key: value\nlist:\n  - a\n  - b\n")
        assert result["key"] == "value"
        assert result["list"] == ["a", "b"]

    def test_empty_string_returns_none(self) -> None:
        assert load_yaml_string("") is None


# ---------------------------------------------------------------------------
# pydantic_resolve_refs
# ---------------------------------------------------------------------------

class TestPydanticResolveRefs:
    def test_no_refs_passthrough(self) -> None:
        schema = {"type": "object", "properties": {"name": {"type": "string"}}}
        result = pydantic_resolve_refs(schema)
        assert result["properties"]["name"]["type"] == "string"

    def test_single_ref_inlined(self) -> None:
        schema = {
            "$defs": {"MyModel": {"type": "object", "properties": {"x": {"type": "integer"}}}},
            "properties": {"item": {"$ref": "#/$defs/MyModel"}},
        }
        result = pydantic_resolve_refs(schema)
        assert result["properties"]["item"]["type"] == "object"
        assert "$defs" not in result

    def test_missing_ref_target_returns_node(self) -> None:
        schema = {
            "$defs": {},
            "properties": {"item": {"$ref": "#/$defs/Missing"}},
        }
        result = pydantic_resolve_refs(schema)
        # Missing ref is returned as-is
        assert result["properties"]["item"]["$ref"] == "#/$defs/Missing"

    def test_non_defs_ref_returned_as_is(self) -> None:
        schema = {"properties": {"x": {"$ref": "http://example.com/schema"}}}
        result = pydantic_resolve_refs(schema)
        assert result["properties"]["x"]["$ref"] == "http://example.com/schema"

    def test_list_containing_refs_resolved(self) -> None:
        schema = {
            "$defs": {"Item": {"type": "string"}},
            "anyOf": [{"$ref": "#/$defs/Item"}, {"type": "null"}],
        }
        result = pydantic_resolve_refs(schema)
        assert result["anyOf"][0]["type"] == "string"

    def test_nested_refs_resolved(self) -> None:
        schema = {
            "$defs": {
                "Inner": {"type": "integer"},
                "Outer": {"properties": {"val": {"$ref": "#/$defs/Inner"}}},
            },
            "properties": {"outer": {"$ref": "#/$defs/Outer"}},
        }
        result = pydantic_resolve_refs(schema)
        assert result["properties"]["outer"]["properties"]["val"]["type"] == "integer"

    def test_defs_removed_from_output(self) -> None:
        schema = {"$defs": {"A": {"type": "string"}}, "type": "object"}
        result = pydantic_resolve_refs(schema)
        assert "$defs" not in result


# ---------------------------------------------------------------------------
# recursive_dict_value_by_key
# ---------------------------------------------------------------------------

class TestRecursiveDictValueByKey:
    def test_key_at_root(self) -> None:
        assert recursive_dict_value_by_key({"name": "Alice"}, "name") == ["Alice"]

    def test_key_in_nested_dict(self) -> None:
        data = {"a": {"b": {"name": "Bob"}}}
        assert recursive_dict_value_by_key(data, "name") == ["Bob"]

    def test_key_in_list_of_dicts(self) -> None:
        data = {"items": [{"id": 1}, {"id": 2}]}
        assert sorted(recursive_dict_value_by_key(data, "id")) == [1, 2]

    def test_key_missing_returns_empty(self) -> None:
        assert recursive_dict_value_by_key({"x": 1}, "missing") == []

    def test_multiple_occurrences(self) -> None:
        data = {"a": {"x": 1}, "b": {"x": 2}}
        result = recursive_dict_value_by_key(data, "x")
        assert sorted(result) == [1, 2]


# ---------------------------------------------------------------------------
# get_value_by_path
# ---------------------------------------------------------------------------

class TestGetValueByPath:
    def test_single_key(self) -> None:
        assert get_value_by_path({"a": 42}, "a") == 42

    def test_nested_keys(self) -> None:
        assert get_value_by_path({"a": {"b": {"c": "deep"}}}, "a.b.c") == "deep"

    def test_list_index(self) -> None:
        assert get_value_by_path({"a": [10, 20, 30]}, "a.1") == 20

    def test_list_index_out_of_bounds_returns_default(self) -> None:
        assert get_value_by_path({"a": [1, 2]}, "a.5") is None

    def test_missing_key_returns_default(self) -> None:
        assert get_value_by_path({"a": 1}, "b", default="fallback") == "fallback"

    def test_non_digit_key_on_list_returns_default(self) -> None:
        assert get_value_by_path({"a": [1, 2]}, "a.notdigit") is None

    def test_mixed_dict_list_path(self) -> None:
        data = {"a": [{"b": "found"}]}
        assert get_value_by_path(data, "a.0.b") == "found"

    def test_returns_default_on_exception(self) -> None:
        # Passing None as data would raise; should return default
        assert get_value_by_path(None, "a.b", default="safe") == "safe"  # type: ignore[arg-type]

    def test_intermediate_none_returns_default(self) -> None:
        assert get_value_by_path({"a": None}, "a.b") is None


# ---------------------------------------------------------------------------
# export_pandas_df_to_markdown
# ---------------------------------------------------------------------------

class TestExportPandasDfToMarkdown:
    def test_creates_markdown_file_str_path(self, tmp_path: Path) -> None:
        df = pd.DataFrame({"col": [1, 2]})
        out = tmp_path / "report.md"
        export_pandas_df_to_markdown(df, str(out))
        assert out.exists()
        assert "col" in out.read_text(encoding="utf-8")

    def test_creates_markdown_file_path_object(self, tmp_path: Path) -> None:
        df = pd.DataFrame({"x": ["a", "b"]})
        out = tmp_path / "out.md"
        export_pandas_df_to_markdown(df, out)
        assert out.exists()

    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        df = pd.DataFrame({"v": [1]})
        out = tmp_path / "subdir" / "deep" / "report.md"
        export_pandas_df_to_markdown(df, out)
        assert out.exists()


# ---------------------------------------------------------------------------
# convert_df_to_excel
# ---------------------------------------------------------------------------

class TestConvertDfToExcel:
    def test_returns_bytes(self) -> None:
        df = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})
        result = convert_df_to_excel(df)
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_round_trip(self) -> None:
        df = pd.DataFrame({"col": [10, 20, 30]})
        xlsx_bytes = convert_df_to_excel(df)
        reloaded = pd.read_excel(io.BytesIO(xlsx_bytes))
        assert list(reloaded["col"]) == [10, 20, 30]

    def test_empty_df(self) -> None:
        df = pd.DataFrame()
        result = convert_df_to_excel(df)
        assert isinstance(result, bytes)


# ---------------------------------------------------------------------------
# percentage_format
# ---------------------------------------------------------------------------

class TestPercentageFormat:
    def test_float_input(self) -> None:
        assert percentage_format(0.5) == "50.00"

    def test_str_input(self) -> None:
        assert percentage_format("0.75") == "75.00"

    def test_int_input(self) -> None:
        assert percentage_format(1) == "100.00"

    def test_precision_zero(self) -> None:
        assert percentage_format(0.5, precision=0) == "50"

    def test_precision_one(self) -> None:
        assert percentage_format(0.333, precision=1) == "33.3"

    def test_zero_value(self) -> None:
        assert percentage_format(0.0) == "0.00"


# ---------------------------------------------------------------------------
# add_months
# ---------------------------------------------------------------------------

class TestAddMonths:
    def test_add_one_month_from_jan31_clamps_to_feb28(self) -> None:
        result = add_months(datetime(2023, 1, 31), 1)
        assert result.month == 2
        assert result.day == 28

    def test_add_one_month_from_jan31_leap_year(self) -> None:
        # 2020 is a leap year → Feb 29
        result = add_months(datetime(2020, 1, 31), 1)
        assert result.month == 2
        assert result.day == 29

    def test_add_twelve_months(self) -> None:
        result = add_months(datetime(2023, 3, 15), 12)
        assert result.year == 2024
        assert result.month == 3
        assert result.day == 15

    def test_subtract_one_month_crosses_year(self) -> None:
        result = add_months(datetime(2023, 1, 15), -1)
        assert result.year == 2022
        assert result.month == 12

    def test_non_leap_year_feb(self) -> None:
        # 2019 is not a leap year → Feb 28
        result = add_months(datetime(2019, 1, 31), 1)
        assert result.day == 28

    def test_add_25_months(self) -> None:
        result = add_months(datetime(2024, 1, 15), 25)
        assert result.year == 2026
        assert result.month == 2

    def test_day_30_to_feb(self) -> None:
        result = add_months(datetime(2023, 3, 30), -1)
        assert result.month == 2
        assert result.day == 28


# ---------------------------------------------------------------------------
# resolve_date
# ---------------------------------------------------------------------------

class TestResolveDate:
    def test_datetime_input_no_offset(self) -> None:
        dt = datetime(2024, 3, 15)
        result = resolve_date("date: %{DATA_DATE_%Y%m%d}", dt)
        assert result == "date: 20240315"

    def test_string_yyyymmdd_input(self) -> None:
        result = resolve_date("%{DATA_DATE_%Y-%m-%d}", "20240315")
        assert result == "2024-03-15"

    def test_string_yyyy_mm_dd_input(self) -> None:
        result = resolve_date("%{DATA_DATE_%Y%m%d}", "2024-03-15")
        assert result == "20240315"

    def test_string_datetime_hhmmss(self) -> None:
        result = resolve_date("%{DATA_DATE_%Y%m%d}", "2024-03-15 10:30:00")
        assert result == "20240315"

    def test_string_datetime_hhmm(self) -> None:
        result = resolve_date("%{DATA_DATE_%Y%m%d}", "2024-03-15 10:30")
        assert result == "20240315"

    def test_invalid_string_raises(self) -> None:
        with pytest.raises(ValueError):
            resolve_date("%{DATA_DATE_%Y%m%d}", "not-a-date")

    def test_no_placeholder_returns_unchanged(self) -> None:
        assert resolve_date("no placeholders here", datetime(2024, 1, 1)) == "no placeholders here"

    def test_offset_plus_1d(self) -> None:
        result = resolve_date("%{DATA_DATE_1D_%Y%m%d}", datetime(2024, 3, 15))
        assert result == "20240316"

    def test_offset_minus_1d(self) -> None:
        result = resolve_date("%{DATA_DATE_-1D_%Y%m%d}", datetime(2024, 3, 15))
        assert result == "20240314"

    def test_offset_plus_1m(self) -> None:
        result = resolve_date("%{DATA_DATE_1M_%Y%m%d}", datetime(2024, 1, 31))
        assert result == "20240229"  # 2024 is a leap year

    def test_offset_plus_1y(self) -> None:
        result = resolve_date("%{DATA_DATE_1Y_%Y%m%d}", datetime(2023, 3, 15))
        assert result == "20240315"

    def test_format_yyyy_mm_dd(self) -> None:
        result = resolve_date("%{DATA_DATE_%Y-%m-%d}", datetime(2024, 6, 5))
        assert result == "2024-06-05"

    def test_format_with_time_hhmmss(self) -> None:
        result = resolve_date("%{DATA_DATE_%Y%m%dHHMMSS}", datetime(2024, 3, 15, 10, 30, 45))
        assert result == "20240315103045"

    def test_multiple_placeholders(self) -> None:
        text = "start: %{DATA_DATE_%Y%m%d} end: %{DATA_DATE_1D_%Y%m%d}"
        result = resolve_date(text, datetime(2024, 3, 15))
        assert "20240315" in result
        assert "20240316" in result


# ---------------------------------------------------------------------------
# resolve_env
# ---------------------------------------------------------------------------

class TestResolveEnv:
    def test_replaces_set_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_VAR", "hello")
        assert resolve_env("value=${MY_VAR}") == "value=hello"

    def test_unset_var_replaced_with_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("UNSET_VAR_XYZ", raising=False)
        assert resolve_env("val=${UNSET_VAR_XYZ}") == "val="

    def test_no_placeholder_unchanged(self) -> None:
        assert resolve_env("plain text") == "plain text"

    def test_multiple_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("A_VAR", "foo")
        monkeypatch.setenv("B_VAR", "bar")
        result = resolve_env("${A_VAR}-${B_VAR}")
        assert result == "foo-bar"


# ---------------------------------------------------------------------------
# safe_list_get
# ---------------------------------------------------------------------------

class TestSafeListGet:
    def test_valid_index(self) -> None:
        assert safe_list_get([10, 20, 30], 1) == 20

    def test_out_of_bounds_returns_none(self) -> None:
        assert safe_list_get([1, 2], 5) is None

    def test_out_of_bounds_returns_custom_default(self) -> None:
        assert safe_list_get([], 0, default_value="missing") == "missing"

    def test_negative_index_raises_index_error_returns_default(self) -> None:
        # Python allows negative indexing normally, but -10 on a 2-element list → IndexError
        assert safe_list_get([1, 2], -10) is None

    def test_zero_index(self) -> None:
        assert safe_list_get(["a", "b", "c"], 0) == "a"
