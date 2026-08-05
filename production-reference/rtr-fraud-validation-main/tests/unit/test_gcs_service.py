"""Unit tests for GCSService."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services.gcs_service import GCSService


@pytest.fixture()
def mock_fs() -> MagicMock:
    with patch("app.services.gcs_service.gcsfs.GCSFileSystem") as mock_cls:
        fs = MagicMock()
        mock_cls.return_value = fs
        yield fs  # type: ignore[misc]


@pytest.fixture()
def svc(mock_fs: MagicMock) -> GCSService:
    return GCSService("my-project", "my-bucket")


def _mock_open(mock_fs: MagicMock, read_return: bytes | str = b"") -> MagicMock:
    """Helper to set up mock_fs.open as a context manager."""
    mock_file = MagicMock()
    mock_file.read.return_value = read_return
    mock_fs.open.return_value.__enter__ = MagicMock(return_value=mock_file)
    mock_fs.open.return_value.__exit__ = MagicMock(return_value=False)
    return mock_file


class TestUri:
    def test_builds_gcs_uri(self, svc: GCSService) -> None:
        assert svc.uri("some/path.csv") == "gs://my-bucket/some/path.csv"

    def test_empty_relative_path(self, svc: GCSService) -> None:
        assert svc.uri("") == "gs://my-bucket/"


class TestExists:
    def test_returns_true(self, svc: GCSService, mock_fs: MagicMock) -> None:
        mock_fs.exists.return_value = True
        assert svc.exists("some/path.csv") is True
        mock_fs.exists.assert_called_once_with("gs://my-bucket/some/path.csv")

    def test_returns_false(self, svc: GCSService, mock_fs: MagicMock) -> None:
        mock_fs.exists.return_value = False
        assert svc.exists("some/path.csv") is False


class TestReadBytes:
    def test_reads_via_open(self, svc: GCSService, mock_fs: MagicMock) -> None:
        mock_file = _mock_open(mock_fs, b"data")
        result = svc.read_bytes("some/path.csv")
        assert result == b"data"
        mock_fs.open.assert_called_once_with("gs://my-bucket/some/path.csv", "rb")
        mock_file.read.assert_called_once()


class TestWriteBytes:
    def test_invalidates_cache_then_writes(self, svc: GCSService, mock_fs: MagicMock) -> None:
        mock_file = _mock_open(mock_fs)
        svc.write_bytes("some/path.csv", b"data")
        mock_fs.invalidate_cache.assert_called_once_with("gs://my-bucket/some/path.csv")
        mock_file.write.assert_called_once_with(b"data")


class TestWriteText:
    def test_writes_text_with_default_encoding(self, svc: GCSService, mock_fs: MagicMock) -> None:
        mock_file = _mock_open(mock_fs)
        svc.write_text("path.csv", "hello")
        mock_file.write.assert_called_once_with("hello")
        # verify open was called with encoding
        open_call = mock_fs.open.call_args
        assert open_call[1].get("encoding") == "utf-8-sig" or "utf-8-sig" in open_call[0]

    def test_writes_text_with_custom_encoding(self, svc: GCSService, mock_fs: MagicMock) -> None:
        mock_file = _mock_open(mock_fs)
        svc.write_text("path.csv", "hello", encoding="utf-8")
        mock_file.write.assert_called_once_with("hello")


class TestReadText:
    def test_reads_text(self, svc: GCSService, mock_fs: MagicMock) -> None:
        mock_file = _mock_open(mock_fs, "text content")
        result = svc.read_text("path.csv")
        assert result == "text content"


class TestInvalidateCache:
    def test_calls_fs_invalidate(self, svc: GCSService, mock_fs: MagicMock) -> None:
        svc.invalidate_cache("some/path.csv")
        mock_fs.invalidate_cache.assert_called_once_with("gs://my-bucket/some/path.csv")


class TestDelete:
    def test_deletes_via_rm(self, svc: GCSService, mock_fs: MagicMock) -> None:
        svc.delete("some/path.csv")
        mock_fs.rm.assert_called_once_with("gs://my-bucket/some/path.csv")

    def test_swallows_exception_and_logs_warning(self, svc: GCSService, mock_fs: MagicMock) -> None:
        mock_fs.rm.side_effect = Exception("network error")
        # Must NOT raise; exception is swallowed with a warning log
        svc.delete("some/path.csv")


class TestCopyFromSharepointIfMissing:
    def test_writes_content_when_file_missing(self, svc: GCSService, mock_fs: MagicMock) -> None:
        mock_fs.exists.return_value = False
        mock_file = _mock_open(mock_fs)
        result = svc.copy_from_sharepoint_if_missing("path.csv", b"content")
        assert result == "gs://my-bucket/path.csv"
        mock_file.write.assert_called_once_with(b"content")

    def test_skips_write_when_file_exists(self, svc: GCSService, mock_fs: MagicMock) -> None:
        mock_fs.exists.return_value = True
        result = svc.copy_from_sharepoint_if_missing("path.csv", b"content")
        assert result == "gs://my-bucket/path.csv"
        mock_fs.open.assert_not_called()
