"""Tests for SourceFileLoader — SharePoint listing, filtering, and landing upload.

Covers ``list_files`` (folder skip + supported/unsupported extension partitioning +
parent-path filtering), ``filter_new`` (in-flight dedupe + skip logging),
``upload_to_landing`` (async upload success/failure split), and ``_build_file_entry``
edge cases not already exercised via ``list_files``. ``list_files_union`` and
``OCRSubmitTask._resolve_src_paths`` are covered in ``test_source_window.py`` and are
intentionally not re-tested here.
"""

from unittest.mock import AsyncMock, Mock

from tasks.ocr_tax_invoice_pipeline.module.source_loader import SourceFileLoader


def _loader(sp_conn=None, gcs_conn=None):
    return SourceFileLoader(sp_conn or Mock(), gcs_conn or Mock())


class TestListFiles:
    def test_list_files_partitions_supported_and_unsupported(self, caplog):
        # Arrange
        sp = Mock()
        sp.list_files.return_value = [
            {"folder": {"childCount": 1}, "name": "somefolder"},
            {"name": "invoice.pdf", "parentReference": {"path": "/drive/root:/Root/Input"}},
            {"name": "notes.txt", "parentReference": {"path": "/drive/root:/Root/Input"}},
        ]
        loader = _loader(sp_conn=sp)

        # Act
        with caplog.at_level("WARNING"):
            supported, unsupported = loader.list_files("/Root/Input", [".pdf", ".jpg", ".jpeg", ".png"])

        # Assert
        assert len(supported) == 1
        entry = supported[0]
        assert entry["name"] == "invoice.pdf"
        assert entry["sp_path"] == "/Root/Input/invoice.pdf"
        assert entry["mime_type"] == "application/pdf"
        sp.list_files.assert_called_once_with("/Root/Input", recursive=True)

        assert len(unsupported) == 1
        assert unsupported[0]["sp_path"] == "/Root/Input/notes.txt"
        assert any("Unsupported file type" in rec.message and "notes.txt" in rec.message for rec in caplog.records)

    def test_list_files_classifies_extensionless_file_as_unsupported(self):
        # Arrange
        sp = Mock()
        sp.list_files.return_value = [{"name": "README", "parentReference": {"path": "/drive/root:/Root/Input"}}]
        loader = _loader(sp_conn=sp)

        # Act
        supported, unsupported = loader.list_files("/Root/Input", [".pdf"])

        # Assert
        assert supported == []
        assert len(unsupported) == 1
        assert unsupported[0]["sp_path"] == "/Root/Input/README"

    def test_list_files_unsupported_entry_carries_name_sp_path_mime(self):
        # Arrange
        sp = Mock()
        sp.list_files.return_value = [{"name": "notes.txt", "parentReference": {"path": "/drive/root:/Root/Input"}}]
        loader = _loader(sp_conn=sp)

        # Act
        _supported, unsupported = loader.list_files("/Root/Input", [".pdf"])

        # Assert
        assert unsupported == [{"name": "notes.txt", "sp_path": "/Root/Input/notes.txt", "mime_type": "text/plain"}]


class TestFilterNew:
    def test_filter_new_excludes_in_flight_files_and_logs_skip_count(self, caplog):
        # Arrange
        files = [{"sp_path": "/a"}, {"sp_path": "/b"}, {"sp_path": "/c"}]
        loader = _loader()

        # Act
        with caplog.at_level("INFO"):
            result = loader.filter_new(files, {"/b"})

        # Assert
        assert [f["sp_path"] for f in result] == ["/a", "/c"]
        assert any("Skipped 1 in-flight file(s)" in rec.message for rec in caplog.records)

    def test_filter_new_with_no_in_flight_files_does_not_log_skip(self, caplog):
        # Arrange
        files = [{"sp_path": "/a"}, {"sp_path": "/b"}]
        loader = _loader()

        # Act
        with caplog.at_level("INFO"):
            result = loader.filter_new(files, set())

        # Assert
        assert [f["sp_path"] for f in result] == ["/a", "/b"]
        assert not any("in-flight file(s)" in rec.message for rec in caplog.records)


class TestUploadToLanding:
    async def test_upload_to_landing_with_empty_files_returns_empty_lists(self):
        # Arrange
        loader = _loader()

        # Act
        uploaded, failed = await loader.upload_to_landing([], "prefix", 5)

        # Assert
        assert (uploaded, failed) == ([], [])

    async def test_upload_to_landing_splits_succeeded_and_failed_files(self):
        # Arrange
        gcs = Mock(bucket_name="test-bucket")
        gcs.upload_sharepoint_to_gcs = AsyncMock(
            return_value={"errors": [{"download_path": "/sp/b.pdf", "error": "network timeout"}]}
        )
        sp = Mock()
        loader = _loader(sp_conn=sp, gcs_conn=gcs)
        files = [
            {"name": "a.pdf", "sp_path": "/sp/a.pdf", "mime_type": "application/pdf"},
            {"name": "b.pdf", "sp_path": "/sp/b.pdf", "mime_type": "application/pdf"},
        ]

        # Act
        uploaded, failed = await loader.upload_to_landing(files, "prefix", 5)

        # Assert
        gcs.upload_sharepoint_to_gcs.assert_called_once_with(
            sharepoint_object=sp,
            stream_list=[
                {"download": "/sp/a.pdf", "upload": "prefix/a.pdf", "mime_type": "application/pdf"},
                {"download": "/sp/b.pdf", "upload": "prefix/b.pdf", "mime_type": "application/pdf"},
            ],
            max_concurrent_uploads=5,
        )
        assert uploaded == [{"name": "a.pdf", "sp_path": "/sp/a.pdf", "gcs_path": "gs://test-bucket/prefix/a.pdf"}]
        assert failed == [{"name": "b.pdf", "sp_path": "/sp/b.pdf", "error": "network timeout"}]


class TestBuildFileEntry:
    def test_build_file_entry_with_missing_parent_reference_returns_none_and_warns(self, caplog):
        # Arrange
        loader = _loader()
        item = {"name": "invoice.pdf"}

        # Act
        with caplog.at_level("WARNING"):
            entry = loader._build_file_entry(item)

        # Assert
        assert entry is None
        assert any("missing parentReference.path" in rec.message for rec in caplog.records)
