"""Tests for the per-page split that feeds the batch payload.

The PDFs here are built in memory by pdfium rather than checked in as fixtures -- a two-page
document is under a kilobyte, and generating it keeps the test honest about what pdfium will
actually accept back.

The stub client is deliberately dumb: it records what it was asked to upload and where. Two of
the bugs this file exists to catch -- a document handle closed after the first page, and pages
uploaded to the project id instead of the bucket -- are invisible to any assertion about the
return value alone.
"""

import io

import pypdfium2 as pdfium
import pytest

from src.google_model.documents.google_output import split_doc

BUCKET = "internal-model-poc"
PROCESSING_URI = f"gs://{BUCKET}/processing/2026-01-01_00-00-00"


def make_pdf(pages: int) -> bytes:
    """A minimal readable PDF with the given number of blank pages."""
    doc = pdfium.PdfDocument.new()
    try:
        for _ in range(pages):
            doc.new_page(200, 200)
        buf = io.BytesIO()
        doc.save(buf)
    finally:
        doc.close()
    return buf.getvalue()


def page_count(content: bytes) -> int:
    """Re-open uploaded bytes, so 'it uploaded something' is not mistaken for 'it uploaded a PDF'."""
    doc = pdfium.PdfDocument(content)
    try:
        return len(doc)
    finally:
        doc.close()


class StubGCS:
    """Records uploads and serves canned downloads, with GCSModule's keyword signatures."""

    def __init__(self, sources: dict[str, bytes]):
        self._sources = sources
        self.uploads: list[dict] = []
        # The attribute the module used to pass as bucket_name; present so a regression to it
        # produces a wrong bucket rather than an AttributeError that names the mistake for us.
        self.project_id = "some-gcp-project"

    def download_file(self, bucket_name: str, file_path: str) -> bytes:
        return self._sources[file_path]

    def upload_file(self, bucket_name: str, upload_path: str, content: bytes, **kwargs) -> None:
        self.uploads.append(
            {"bucket_name": bucket_name, "upload_path": upload_path, "content": content}
        )


@pytest.fixture
def two_page_pdf():
    return make_pdf(2)


class TestPdfSplit:
    def test_returns_page_name_to_source_document(self, two_page_pdf):
        client = StubGCS({"src/doc.pdf": two_page_pdf})

        metadata = split_doc(["src/doc.pdf"], client, BUCKET, PROCESSING_URI)

        # A mapping, not a list of records -- run() looks a page up by name.
        assert metadata == {"doc_p1.pdf": "doc.pdf", "doc_p2.pdf": "doc.pdf"}

    def test_reaches_every_page(self, two_page_pdf):
        client = StubGCS({"src/doc.pdf": two_page_pdf})

        split_doc(["src/doc.pdf"], client, BUCKET, PROCESSING_URI)

        # Closing the source document inside the page loop used to make page 2 unreachable.
        assert len(client.uploads) == 2

    def test_each_uploaded_page_is_a_readable_single_page_pdf(self, two_page_pdf):
        client = StubGCS({"src/doc.pdf": two_page_pdf})

        split_doc(["src/doc.pdf"], client, BUCKET, PROCESSING_URI)

        assert [page_count(upload["content"]) for upload in client.uploads] == [1, 1]

    def test_uploads_go_to_the_given_bucket(self, two_page_pdf):
        client = StubGCS({"src/doc.pdf": two_page_pdf})

        split_doc(["src/doc.pdf"], client, BUCKET, PROCESSING_URI)

        # Never client.project_id: a project is not a bucket.
        assert {upload["bucket_name"] for upload in client.uploads} == {BUCKET}

    def test_pages_are_written_under_the_processing_directory(self, two_page_pdf):
        client = StubGCS({"src/doc.pdf": two_page_pdf})

        split_doc(["src/doc.pdf"], client, BUCKET, PROCESSING_URI)

        assert [upload["upload_path"] for upload in client.uploads] == [
            f"{PROCESSING_URI}/doc_p1.pdf",
            f"{PROCESSING_URI}/doc_p2.pdf",
        ]

    def test_pages_of_several_documents_all_map_to_their_own_source(self, two_page_pdf):
        client = StubGCS({"src/a.pdf": two_page_pdf, "src/b.pdf": make_pdf(1)})

        metadata = split_doc(["src/a.pdf", "src/b.pdf"], client, BUCKET, PROCESSING_URI)

        assert metadata == {
            "a_p1.pdf": "a.pdf",
            "a_p2.pdf": "a.pdf",
            "b_p1.pdf": "b.pdf",
        }


class TestOtherSources:
    def test_an_image_passes_through_unchanged_as_page_one(self):
        client = StubGCS({"src/scan.jpg": b"\xff\xd8jpeg-bytes"})

        metadata = split_doc(["src/scan.jpg"], client, BUCKET, PROCESSING_URI)

        # _p1 even though nothing was split, so a unique_name has one shape whatever the source.
        assert metadata == {"scan_p1.jpg": "scan.jpg"}
        assert client.uploads[0]["content"] == b"\xff\xd8jpeg-bytes"

    def test_an_unsupported_extension_is_skipped_without_aborting_the_batch(self, two_page_pdf):
        client = StubGCS({"src/notes.txt": b"text", "src/doc.pdf": two_page_pdf})

        metadata = split_doc(["src/notes.txt", "src/doc.pdf"], client, BUCKET, PROCESSING_URI)

        # The .txt contributes nothing and does not stop the .pdf behind it being split.
        assert "notes_p1.txt" not in metadata
        assert set(metadata) == {"doc_p1.pdf", "doc_p2.pdf"}

    def test_no_files_yields_an_empty_mapping(self):
        assert split_doc([], StubGCS({}), BUCKET, PROCESSING_URI) == {}
