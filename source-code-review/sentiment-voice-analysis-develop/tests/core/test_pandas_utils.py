"""
Tests for pandas utility functions.
"""

import io

import pandas as pd
import pytest

from src.utils.pandas_utils import (
    clean_invalid_xml_chars,
    convert_df_to_excel,
    convert_df_to_markdown,
    df_to_excel_bytes,
    ensure_df_schema,
    export_pandas_df_to_markdown,
    replace_nan_with_default,
    trim_all_columns,
)


class TestPandasUtils:
    """Test suite for pandas utility functions."""

    def test_export_pandas_df_to_markdown(self, sample_dataframe, tmp_path):
        """Test exporting DataFrame to markdown file."""
        output_file = tmp_path / "output.md"
        export_pandas_df_to_markdown(sample_dataframe, output_file)

        assert output_file.exists()
        content = output_file.read_text()
        assert "call_id" in content
        assert "mobile_number" in content

    def test_export_creates_directory_if_not_exists(self, sample_dataframe, tmp_path):
        """Test that export creates parent directory if it doesn't exist."""
        output_file = tmp_path / "subdir" / "nested" / "output.md"
        export_pandas_df_to_markdown(sample_dataframe, output_file)

        assert output_file.exists()
        assert output_file.parent.exists()

    def test_convert_df_to_excel(self, sample_dataframe):
        """Test converting DataFrame to Excel bytes."""
        excel_bytes = convert_df_to_excel(sample_dataframe)

        assert isinstance(excel_bytes, bytes)
        assert len(excel_bytes) > 0

    def test_convert_df_to_excel_can_be_read_back(self, sample_dataframe):
        """Test that Excel bytes can be read back as DataFrame."""
        excel_bytes = convert_df_to_excel(sample_dataframe)

        # Read back the Excel bytes
        df_read = pd.read_excel(io.BytesIO(excel_bytes))

        assert len(df_read) == len(sample_dataframe)
        assert list(df_read.columns) == list(sample_dataframe.columns)

    def test_ensure_df_schema_adds_missing_columns(self):
        """Test that ensure_df_schema adds missing columns."""
        df = pd.DataFrame({"col1": [1, 2, 3]})
        schemas = ["col1", "col2", "col3"]

        result = ensure_df_schema(df, schemas)

        assert "col2" in result.columns
        assert "col3" in result.columns
        assert len(result.columns) == 3

    def test_ensure_df_schema_reorders_columns(self):
        """Test that ensure_df_schema reorders columns to match schema."""
        df = pd.DataFrame({"col3": [1, 2], "col1": [3, 4], "col2": [5, 6]})
        schemas = ["col1", "col2", "col3"]

        result = ensure_df_schema(df, schemas)

        assert list(result.columns) == schemas

    def test_ensure_df_schema_fills_missing_with_na(self):
        """Test that missing columns are filled with pd.NA."""
        df = pd.DataFrame({"col1": [1, 2]})
        schemas = ["col1", "col2"]

        result = ensure_df_schema(df, schemas)

        assert pd.isna(result["col2"].iloc[0])

    def test_replace_nan_with_default_empty_string(self):
        """Test replacing NaN with empty string (default)."""
        df = pd.DataFrame({"col1": [1, None, 3], "col2": ["a", "nan", "c"]})

        result = replace_nan_with_default(df)

        assert result["col1"].iloc[1] == ""
        assert result["col2"].iloc[1] == ""

    def test_replace_nan_with_custom_default(self):
        """Test replacing NaN with custom default value."""
        df = pd.DataFrame({"col1": [1, None, 3], "col2": ["a", None, "c"]})

        result = replace_nan_with_default(df, default_value="MISSING")

        assert result["col1"].iloc[1] == "MISSING"
        assert result["col2"].iloc[1] == "MISSING"

    def test_replace_nan_specific_columns(self):
        """Test replacing NaN in specific columns only."""
        df = pd.DataFrame({"col1": [1, None, 3], "col2": ["a", None, "c"]})

        result = replace_nan_with_default(df, columns=["col1"])

        assert result["col1"].iloc[1] == ""
        assert pd.isna(result["col2"].iloc[1])

    def test_replace_nan_handles_string_nan(self):
        """Test that string 'nan', 'NaN', 'None' are replaced."""
        df = pd.DataFrame({"col1": ["value", "nan", "NaN", "None", "none"]})

        result = replace_nan_with_default(df)

        assert result["col1"].iloc[0] == "value"
        assert result["col1"].iloc[1] == ""
        assert result["col1"].iloc[2] == ""
        assert result["col1"].iloc[3] == ""
        assert result["col1"].iloc[4] == ""

    def test_df_to_excel_bytes_with_text_columns(self, sample_dataframe):
        """Test df_to_excel_bytes with text_columns parameter."""
        excel_bytes = df_to_excel_bytes(sample_dataframe, text_columns=["call_id", "mobile_number"])

        assert isinstance(excel_bytes, bytes)
        assert len(excel_bytes) > 0

    def test_df_to_excel_bytes_with_freeze_panes(self, sample_dataframe):
        """Test df_to_excel_bytes with freeze_panes parameter."""
        excel_bytes = df_to_excel_bytes(sample_dataframe, freeze_panes="A2")

        assert isinstance(excel_bytes, bytes)
        assert len(excel_bytes) > 0

    def test_df_to_excel_bytes_with_custom_sheet_name(self, sample_dataframe):
        """Test df_to_excel_bytes with custom sheet name."""
        excel_bytes = df_to_excel_bytes(sample_dataframe, sheet_name="CustomSheet")

        # Read back to verify sheet name
        df_read = pd.read_excel(io.BytesIO(excel_bytes), sheet_name="CustomSheet")
        assert len(df_read) == len(sample_dataframe)

    def test_df_to_excel_bytes_text_columns_all(self, sample_dataframe):
        """Test df_to_excel_bytes with text_columns='ALL'."""
        excel_bytes = df_to_excel_bytes(sample_dataframe, text_columns="ALL")

        assert isinstance(excel_bytes, bytes)
        assert len(excel_bytes) > 0

    def test_df_to_excel_bytes_with_nonexistent_text_column(self, sample_dataframe):
        """Test df_to_excel_bytes with text_columns containing non-existent column."""
        excel_bytes = df_to_excel_bytes(sample_dataframe, text_columns=["call_id", "nonexistent_column"])

        # Should not raise error, just skip non-existent column
        assert isinstance(excel_bytes, bytes)


class TestPandasUtilsAdditional:
    def test_export_markdown_accepts_string_path(self, sample_dataframe, tmp_path):
        output_path = tmp_path / "nested" / "table.md"

        export_pandas_df_to_markdown(sample_dataframe, str(output_path))

        assert output_path.exists()
        assert "call_id" in output_path.read_text(encoding="utf-8")

    def test_convert_df_to_markdown_returns_markdown_text(self, sample_dataframe):
        result = convert_df_to_markdown(sample_dataframe)

        assert "call_id" in result
        assert "123456789" in result

    def test_trim_all_columns_strips_only_string_columns(self):
        df = pd.DataFrame({"text": ["  hello  ", " world"], "score": [1, 2]})

        result = trim_all_columns(df)

        assert result["text"].tolist() == ["hello", "world"]
        assert result["score"].tolist() == [1, 2]

    def test_trim_all_columns_rejects_non_dataframe(self):
        with pytest.raises(ValueError, match="Input must be a pandas DataFrame"):
            trim_all_columns(["not", "a", "dataframe"])

    def test_trim_all_columns_wraps_strip_errors(self):
        df = pd.DataFrame({"bad": pd.Series([1, 2], dtype="object")})

        with pytest.raises(ValueError, match="Error trimming DataFrame columns"):
            trim_all_columns(df)

    def test_clean_invalid_xml_chars_removes_control_characters(self):
        assert clean_invalid_xml_chars("bad\x00text\x07") == "badtext"

    def test_clean_invalid_xml_chars_returns_non_string_value_unchanged(self):
        assert clean_invalid_xml_chars(123) == 123

    def test_df_to_excel_bytes_wraps_writer_errors(self, sample_dataframe):
        def raise_writer_error(*args, **kwargs):
            raise RuntimeError("boom")

        with pytest.raises(Exception, match="Error converting DataFrame to Excel bytes: boom"):
            with pytest.MonkeyPatch.context() as monkeypatch:
                monkeypatch.setattr("src.utils.pandas_utils.pd.ExcelWriter", raise_writer_error)
                df_to_excel_bytes(sample_dataframe)

    def test_df_to_excel_bytes_ignores_column_width_calculation_errors(self, sample_dataframe):
        class BadValue:
            def __str__(self):
                raise ValueError("bad value")

        class FakeCell:
            def __init__(self, value, column_letter):
                self.value = value
                self.column_letter = column_letter
                self.number_format = None

        class FakeDimension:
            width = None

        class FakeWorksheet:
            def __init__(self):
                self.auto_filter = type("AutoFilter", (), {"ref": None})()
                self.dimensions = "A1:A2"
                self.column_dimensions = {"A": FakeDimension()}
                self.columns = [(FakeCell("header", "A"), FakeCell(BadValue(), "A"))]

            def __getitem__(self, key):
                return [FakeCell("header", key), FakeCell("value", key)]

        class FakeWriter:
            def __init__(self, *args, **kwargs):
                self.sheets = {"Sheet1": FakeWorksheet()}

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setattr("src.utils.pandas_utils.pd.ExcelWriter", FakeWriter)
            monkeypatch.setattr(pd.DataFrame, "to_excel", lambda self, writer, index=False, sheet_name="Sheet1": None)
            result = df_to_excel_bytes(sample_dataframe)

        assert isinstance(result, bytes)
