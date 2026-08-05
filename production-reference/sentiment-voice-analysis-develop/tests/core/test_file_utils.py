"""
Tests for file utility functions.
"""

from unittest.mock import patch

import pandas as pd
import pytest

from src.utils.file_utils import load_yaml, load_yaml_str, pdf_spliter, read_file, read_xlsx


class TestFileUtils:
    """Test suite for file utility functions."""

    def test_read_file_with_path_object(self, tmp_path):
        """Test reading file with Path object."""
        test_file = tmp_path / "test.txt"
        test_content = "Hello, World!"
        test_file.write_text(test_content)

        content = read_file(test_file)
        assert content == test_content

    def test_read_file_with_string_path(self, tmp_path):
        """Test reading file with string path."""
        test_file = tmp_path / "test.txt"
        test_content = "Test content"
        test_file.write_text(test_content)

        content = read_file(str(test_file))
        assert content == test_content

    def test_read_file_with_custom_encoding(self, tmp_path):
        """Test reading file with custom encoding."""
        test_file = tmp_path / "test_utf8.txt"
        test_content = "สวัสดี"  # Thai text
        test_file.write_text(test_content, encoding="utf-8")

        content = read_file(test_file, encoding="utf-8")
        assert content == test_content

    def test_read_file_nonexistent_raises_error(self, tmp_path):
        """Test that reading non-existent file raises FileNotFoundError."""
        nonexistent = tmp_path / "nonexistent.txt"

        with pytest.raises(FileNotFoundError):
            read_file(nonexistent)

    def test_load_yaml_with_path_object(self, temp_config_path):
        """Test loading YAML with Path object."""
        config = load_yaml(temp_config_path)

        assert isinstance(config, dict)
        assert "task1" in config

    def test_load_yaml_with_string_path(self, temp_config_path):
        """Test loading YAML with string path."""
        config = load_yaml(str(temp_config_path))

        assert isinstance(config, dict)
        assert "task1" in config

    def test_load_yaml_returns_dict(self, tmp_path):
        """Test that load_yaml returns dictionary."""
        yaml_file = tmp_path / "config.yml"
        yaml_file.write_text("key1: value1\nkey2: 123\n")

        config = load_yaml(yaml_file)

        assert isinstance(config, dict)
        assert config["key1"] == "value1"
        assert config["key2"] == 123

    def test_load_yaml_with_nested_structure(self, tmp_path):
        """Test loading YAML with nested structure."""
        yaml_file = tmp_path / "nested.yml"
        yaml_content = """
parent:
  child1: value1
  child2:
    grandchild: value2
"""
        yaml_file.write_text(yaml_content)

        config = load_yaml(yaml_file)

        assert config["parent"]["child1"] == "value1"
        assert config["parent"]["child2"]["grandchild"] == "value2"

    def test_load_yaml_empty_file(self, tmp_path):
        """Test loading empty YAML file returns None."""
        yaml_file = tmp_path / "empty.yml"
        yaml_file.write_text("")

        config = load_yaml(yaml_file)
        assert config is None

    def test_load_yaml_str_from_string(self):
        """Test loading YAML from string."""
        yaml_string = "key1: value1\nkey2: 123\n"

        config = load_yaml_str(yaml_string)

        assert isinstance(config, dict)
        assert config["key1"] == "value1"
        assert config["key2"] == 123

    def test_load_yaml_str_with_list(self):
        """Test loading YAML list from string."""
        yaml_string = "- item1\n- item2\n- item3\n"

        config = load_yaml_str(yaml_string)

        assert isinstance(config, list)
        assert len(config) == 3
        assert config[0] == "item1"

    def test_load_yaml_str_empty_string(self):
        """Test loading empty YAML string returns None."""
        config = load_yaml_str("")
        assert config is None

    def test_load_yaml_str_complex_structure(self):
        """Test loading complex YAML structure from string."""
        yaml_string = """
database:
  host: localhost
  port: 5432
  credentials:
    username: admin
    password: secret
services:
  - api
  - worker
  - scheduler
"""
        config = load_yaml_str(yaml_string)

        assert config["database"]["host"] == "localhost"
        assert config["database"]["port"] == 5432
        assert len(config["services"]) == 3
        assert "api" in config["services"]


class TestReadXlsx:
    def test_reads_excel_file_from_string_path(self, tmp_path):
        source = pd.DataFrame({"name": ["Alice", "Bob"], "score": [1, 2]})
        path = tmp_path / "input.xlsx"
        source.to_excel(path, index=False, sheet_name="Scores")

        result = read_xlsx(str(path), sheet_name="Scores")

        pd.testing.assert_frame_equal(result, source)


class FakeWriter:
    def __init__(self):
        self.pages = []

    def add_page(self, page):
        self.pages.append(page)

    def write(self, stream):
        stream.write(f"chunk-{len(self.pages)}".encode())


class TestPdfSpliter:
    @patch("src.utils.file_utils.PdfWriter", side_effect=lambda: FakeWriter())
    @patch("src.utils.file_utils.PdfReader")
    def test_splits_pdf_into_chunks_and_preserves_parent_stem(self, mock_reader, _mock_writer):
        mock_reader.return_value.pages = ["p1", "p2", "p3"]

        chunks = pdf_spliter("report.pdf", b"fake-pdf", max_pages_per_chunk=2)

        assert [chunk["file_name"] for chunk in chunks] == ["report_1.pdf", "report_3.pdf"]
        assert [chunk["file_content"] for chunk in chunks] == [b"chunk-2", b"chunk-1"]
        assert all(chunk["parent_stem"] == "report" for chunk in chunks)

    @patch("src.utils.file_utils.PdfWriter", side_effect=lambda: FakeWriter())
    @patch("src.utils.file_utils.PdfReader")
    def test_supports_custom_chunk_suffix_format(self, mock_reader, _mock_writer):
        mock_reader.return_value.pages = ["p1", "p2"]

        chunks = pdf_spliter(
            "invoice.pdf",
            b"fake-pdf",
            max_pages_per_chunk=1,
            chunk_suffix_format="_page_{n}",
        )

        assert [chunk["file_name"] for chunk in chunks] == ["invoice_page_1.pdf", "invoice_page_2.pdf"]
