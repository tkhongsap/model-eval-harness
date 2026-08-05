import pytest

from src.utils.path_utils import extract_date_from_path, strip_gs_prefix, strip_page_suffix


class TestExtractDateFromPath:
    def test_returns_last_date_from_gcs_uri(self):
        path = "gs://bucket/input/202601/20260115/file.pdf"
        assert extract_date_from_path(path) == "20260115"

    def test_returns_empty_string_when_date_missing(self):
        assert extract_date_from_path("gs://bucket/input/no-date/file.pdf") == ""

    def test_uses_last_match_when_multiple_dates_exist(self):
        path = "gs://bucket/20250101/input/20260115/output/20260201/file.pdf"
        assert extract_date_from_path(path) == "20260201"


class TestStripGsPrefix:
    def test_strips_matching_bucket_prefix(self):
        assert strip_gs_prefix("gs://my-bucket/folder/file.txt", "my-bucket") == "folder/file.txt"

    def test_returns_original_when_prefix_missing(self):
        uri = "folder/file.txt"
        assert strip_gs_prefix(uri, "my-bucket") == uri

    def test_returns_original_when_bucket_differs(self):
        uri = "gs://other-bucket/folder/file.txt"
        assert strip_gs_prefix(uri, "my-bucket") == uri


class TestStripPageSuffix:
    @pytest.mark.parametrize(
        ("filename", "expected"),
        [
            ("invoice_001_p1.pdf", "invoice_001.pdf"),
            ("invoice_001_p27.pdf", "invoice_001.pdf"),
            ("invoice_001.pdf", "invoice_001.pdf"),
        ],
    )
    def test_handles_page_suffix_variants(self, filename, expected):
        assert strip_page_suffix(filename) == expected
