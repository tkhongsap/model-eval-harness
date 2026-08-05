"""Tests for DocumentProcessor — per-page PDF splitting and IQS scoring."""

from __future__ import annotations

import io

from PIL import Image, ImageDraw

from tasks.ocr_tax_invoice_pipeline.helper.constant import QualityStatus
from tasks.ocr_tax_invoice_pipeline.module.document_processor import DocumentProcessor

_IQS_CONFIG = {
    "weights": {"vq": 1.0, "sq": 0.0, "ct": 0.0},
    "threshold": 0.6,
    "sub_thresholds": {},
}

_REAL_CT_IQS_CONFIG = {
    "weights": {"vq": 0.0, "sq": 0.0, "ct": 1.0},
    "threshold": 0.6,
    "sub_thresholds": {},
}


def _processor() -> DocumentProcessor:
    return DocumentProcessor(_IQS_CONFIG)


def _make_png_bytes(image: Image.Image) -> bytes:
    """Encode a PIL image as PNG bytes."""
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _rectangle_png_bytes(size: int = 100, box_ratio: float = 0.7) -> bytes:
    """A white page with a black rectangle covering roughly ``box_ratio ** 2`` of the area."""
    image = Image.new("L", (size, size), color=255)
    box = int(size * box_ratio)
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, box, box], fill=0)
    return _make_png_bytes(image.convert("RGB"))


def _blank_png_bytes(size: int = 100) -> bytes:
    """A pure-white blank page — no foreground content."""
    return _make_png_bytes(Image.new("RGB", (size, size), color=(255, 255, 255)))


class TestProcessDispatch:
    def test_process_pdf_extension_routes_to_pdf_scoring(self, mocker):
        # Arrange
        mocker.patch(
            "tasks.ocr_tax_invoice_pipeline.module.document_processor.image_utils.score_pdf_pages",
            return_value=[{"page_no": 1, "vq": 0.9, "sq": 0.9, "ct": 0.9, "iqs": 0.9, "passed": True}],
        )
        mocker.patch(
            "tasks.ocr_tax_invoice_pipeline.module.document_processor.extract_single_page",
            return_value=b"page-bytes",
        )
        score_image = mocker.patch(
            "tasks.ocr_tax_invoice_pipeline.module.document_processor.image_utils.score_image_bytes"
        )
        processor = _processor()

        # Act
        manifest_rows, gcs_uploads = processor.process("JOB", "pipeline", "invoice.PDF", b"pdf-bytes", "proc/JOB")

        # Assert
        assert len(manifest_rows) == 1
        assert len(gcs_uploads) == 1
        score_image.assert_not_called()

    def test_process_image_extension_routes_to_image_scoring(self, mocker):
        # Arrange
        score_pdf = mocker.patch("tasks.ocr_tax_invoice_pipeline.module.document_processor.image_utils.score_pdf_pages")
        mocker.patch(
            "tasks.ocr_tax_invoice_pipeline.module.document_processor.image_utils.score_image_bytes",
            return_value={
                "vq": 0.9,
                "sq": 0.9,
                "ct": 0.9,
                "iqs": 0.9,
                "per_page": [{"vq": 0.9, "sq": 0.9, "ct": 0.9, "iqs": 0.9}],
                "passed": True,
            },
        )
        processor = _processor()

        # Act
        manifest_rows, gcs_uploads = processor.process("JOB", "pipeline", "invoice.jpg", b"img-bytes", "proc/JOB")

        # Assert
        assert len(manifest_rows) == 1
        assert len(gcs_uploads) == 1
        score_pdf.assert_not_called()


class TestProcessPdf:
    def test_scoring_raises_yields_single_failed_row(self, mocker):
        # Arrange
        mocker.patch(
            "tasks.ocr_tax_invoice_pipeline.module.document_processor.image_utils.score_pdf_pages",
            side_effect=RuntimeError("boom"),
        )
        processor = _processor()

        # Act
        manifest_rows, gcs_uploads = processor.process("JOB", "pipeline", "invoice.pdf", b"pdf-bytes", "proc/JOB")

        # Assert
        assert gcs_uploads == []
        assert len(manifest_rows) == 1
        row = manifest_rows[0]
        assert row["quality_status"] == QualityStatus.REJECTED.value
        assert "PDF scoring failed" in row["message"]
        assert row["child_path"] == ""

    def test_empty_page_scores_yields_single_failed_row(self, mocker):
        # Arrange
        mocker.patch(
            "tasks.ocr_tax_invoice_pipeline.module.document_processor.image_utils.score_pdf_pages",
            return_value=[],
        )
        processor = _processor()

        # Act
        manifest_rows, gcs_uploads = processor.process("JOB", "pipeline", "invoice.pdf", b"pdf-bytes", "proc/JOB")

        # Assert
        assert gcs_uploads == []
        assert len(manifest_rows) == 1
        assert manifest_rows[0]["quality_status"] == QualityStatus.REJECTED.value

    def test_mixed_pass_fail_pages_builds_expected_rows_and_uploads(self, mocker):
        # Arrange
        mocker.patch(
            "tasks.ocr_tax_invoice_pipeline.module.document_processor.image_utils.score_pdf_pages",
            return_value=[
                {"page_no": 1, "vq": 0.9, "sq": 0.9, "ct": 0.9, "iqs": 0.9, "passed": True},
                {"page_no": 2, "vq": 0.1, "sq": 0.1, "ct": 0.1, "iqs": 0.1, "passed": False},
            ],
        )
        extract_page = mocker.patch(
            "tasks.ocr_tax_invoice_pipeline.module.document_processor.extract_single_page",
            return_value=b"page-1-bytes",
        )
        processor = _processor()

        # Act
        manifest_rows, gcs_uploads = processor.process("JOB", "pipeline", "invoice.pdf", b"pdf-bytes", "proc/JOB/")

        # Assert
        assert len(manifest_rows) == 2
        accepted, rejected = manifest_rows
        assert accepted["quality_status"] == QualityStatus.ACCEPTED.value
        assert accepted["child_path"] == "proc/JOB/invoice_p001.pdf"
        assert accepted["message"] == ""
        assert accepted["parent_total_pages"] == 2
        assert rejected["quality_status"] == QualityStatus.REJECTED.value
        assert rejected["child_path"] == ""
        assert rejected["message"] != ""

        assert len(gcs_uploads) == 1
        upload = gcs_uploads[0]
        assert upload["content"] == b"page-1-bytes"
        assert upload["mime_type"] == "application/pdf"
        assert upload["destination_path"] == "proc/JOB/invoice_p001.pdf"
        extract_page.assert_called_once_with(b"pdf-bytes", 0)


class TestProcessImage:
    def test_scoring_exception_yields_rejected_row_no_upload(self, mocker):
        # Arrange
        mocker.patch(
            "tasks.ocr_tax_invoice_pipeline.module.document_processor.image_utils.score_image_bytes",
            side_effect=ValueError("cannot decode"),
        )
        processor = _processor()

        # Act
        manifest_rows, gcs_uploads = processor.process("JOB", "pipeline", "photo.jpg", b"img-bytes", "proc/JOB")

        # Assert
        assert gcs_uploads == []
        assert len(manifest_rows) == 1
        row = manifest_rows[0]
        assert row["quality_status"] == QualityStatus.REJECTED.value
        assert row["iqs_score"] == 0.0
        assert row["child_path"] == ""

    def test_accepted_image_builds_upload_with_guessed_mime_type(self, mocker):
        # Arrange
        mocker.patch(
            "tasks.ocr_tax_invoice_pipeline.module.document_processor.image_utils.score_image_bytes",
            return_value={
                "vq": 0.9,
                "sq": 0.9,
                "ct": 0.9,
                "iqs": 0.9,
                "per_page": [{"vq": 0.9, "sq": 0.9, "ct": 0.9, "iqs": 0.9}],
                "passed": True,
            },
        )
        processor = _processor()

        # Act
        manifest_rows, gcs_uploads = processor.process("JOB", "pipeline", "photo.png", b"img-bytes", "proc/JOB")

        # Assert
        assert len(manifest_rows) == 1
        assert manifest_rows[0]["quality_status"] == QualityStatus.ACCEPTED.value
        assert manifest_rows[0]["child_path"] == "proc/JOB/photo.png"
        assert len(gcs_uploads) == 1
        assert gcs_uploads[0]["mime_type"] == "image/png"
        assert gcs_uploads[0]["content"] == b"img-bytes"
        assert gcs_uploads[0]["destination_path"] == "proc/JOB/photo.png"

    def test_rejected_image_yields_no_upload(self, mocker):
        # Arrange
        mocker.patch(
            "tasks.ocr_tax_invoice_pipeline.module.document_processor.image_utils.score_image_bytes",
            return_value={
                "vq": 0.1,
                "sq": 0.1,
                "ct": 0.1,
                "iqs": 0.1,
                "per_page": [{"vq": 0.1, "sq": 0.1, "ct": 0.1, "iqs": 0.1}],
                "passed": False,
            },
        )
        processor = _processor()

        # Act
        manifest_rows, gcs_uploads = processor.process("JOB", "pipeline", "photo.png", b"img-bytes", "proc/JOB")

        # Assert
        assert gcs_uploads == []
        assert manifest_rows[0]["quality_status"] == QualityStatus.REJECTED.value

    def test_image_result_without_passed_key_is_rejected(self, mocker):
        # Arrange — an aggregate result missing the top-level "passed" key must not be treated
        # as accepted (regression guard for the per-page-vs-aggregate unwrapping bug).
        mocker.patch(
            "tasks.ocr_tax_invoice_pipeline.module.document_processor.image_utils.score_image_bytes",
            return_value={
                "vq": 0.9,
                "sq": 0.9,
                "ct": 0.9,
                "iqs": 0.9,
                "per_page": [{"vq": 0.9, "sq": 0.9, "ct": 0.9, "iqs": 0.9}],
            },
        )
        processor = _processor()

        # Act
        manifest_rows, gcs_uploads = processor.process("JOB", "pipeline", "photo.jpg", b"img-bytes", "proc/JOB")

        # Assert
        assert gcs_uploads == []
        assert manifest_rows[0]["quality_status"] == QualityStatus.REJECTED.value


class TestProcessImageRealScoring:
    """Regression coverage: run real PNG bytes through the real ``image_utils`` scorer.

    No mocking of ``image_utils`` here — this is the test that would have caught the
    aggregate-vs-per-page unwrapping bug in ``_process_image``.
    """

    def test_process_image_real_png_above_threshold_is_accepted(self):
        # Arrange — a rectangle covering ~49% of the page sits inside the CT density
        # band [0.02, 0.60], so ct == 1.0 and (with an all-ct weight config) iqs == 1.0.
        png_bytes = _rectangle_png_bytes(box_ratio=0.7)
        processor = DocumentProcessor(_REAL_CT_IQS_CONFIG)

        # Act
        manifest_rows, gcs_uploads = processor.process("JOB", "pipeline", "photo.png", png_bytes, "proc/JOB")

        # Assert
        assert len(manifest_rows) == 1
        row = manifest_rows[0]
        assert row["quality_status"] == QualityStatus.ACCEPTED.value
        assert row["iqs_score"] == 1.0
        assert len(gcs_uploads) == 1
        assert gcs_uploads[0]["destination_path"] == "proc/JOB/photo.png"

    def test_process_image_real_blank_png_is_rejected(self):
        # Arrange — a pure-white page has zero foreground density, so ct == 0.0.
        png_bytes = _blank_png_bytes()
        processor = DocumentProcessor(_REAL_CT_IQS_CONFIG)

        # Act
        manifest_rows, gcs_uploads = processor.process("JOB", "pipeline", "photo.png", png_bytes, "proc/JOB")

        # Assert
        assert gcs_uploads == []
        row = manifest_rows[0]
        assert row["quality_status"] == QualityStatus.REJECTED.value
        assert row["message"] != ""
