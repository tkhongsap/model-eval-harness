"""Document processor — per-page PDF splitting and IQS scoring."""

from __future__ import annotations

import mimetypes
from pathlib import Path

from src.utils import image_utils
from src.utils.logger import Logger
from src.utils.pdf_utils import extract_single_page
from tasks.ocr_tax_invoice_pipeline.helper.constant import QualityStatus
from tasks.ocr_tax_invoice_pipeline.helper.messages import iqs_reject_reason

logger = Logger(__name__)

_PDF_MIME = "application/pdf"


class DocumentProcessor:
    """Splits a PDF or image into one-page files and scores each page with IQS.

    All pages are always scored — there is no ``iqs_enabled`` flag.
    Pages that fail the IQS gate are recorded in the manifest with
    ``QualityStatus.REJECTED`` and excluded from the GCS upload list.
    """

    def __init__(self, iqs_config: dict) -> None:
        """Initialise with an IQS configuration dict.

        Args:
            iqs_config: IQS configuration as loaded from ``iqs_config.yml``.
                Must contain ``weights``, ``threshold``, and sub-section keys.
        """
        self._iqs_config = iqs_config

    def process(
        self,
        job_id: str,
        pipeline_name: str,
        file_name: str,
        content: bytes,
        processing_gcs_prefix: str,
    ) -> tuple[list[dict], list[dict]]:
        """Process a single source file into per-page manifest rows and GCS uploads.

        Args:
            job_id: Current pipeline job identifier.
            pipeline_name: Pipeline name written into every manifest row.
            file_name: Original source filename (used for naming page files).
            content: Raw file bytes (PDF or image).
            processing_gcs_prefix: GCS path prefix (without ``gs://bucket/``)
                where page files will be uploaded.

        Returns:
            A ``(manifest_rows, gcs_uploads)`` tuple. ``manifest_rows`` has one entry per page
            (including rejected). ``gcs_uploads`` has one entry per IQS-valid page
            (``content``, ``mime_type``, ``destination_path``).
        """
        ext = Path(file_name).suffix.lower()
        if ext == ".pdf":
            return self._process_pdf(job_id, pipeline_name, file_name, content, processing_gcs_prefix)
        return self._process_image(job_id, pipeline_name, file_name, content, processing_gcs_prefix)

    def _process_pdf(
        self,
        job_id: str,
        pipeline_name: str,
        file_name: str,
        content: bytes,
        gcs_prefix: str,
    ) -> tuple[list[dict], list[dict]]:
        """Split a PDF page-by-page, score each page, build manifest + upload dicts."""
        try:
            page_scores = image_utils.score_pdf_pages(content, self._iqs_config)
        except Exception as exc:
            logger.warning(f"IQS scoring failed for {file_name}: {exc}")
            page_scores = []

        if not page_scores:
            row = self._scored_row(
                job_id,
                pipeline_name,
                file_name,
                {"page_no": 0},
                0,
                "",
                QualityStatus.REJECTED,
                f"PDF scoring failed: {file_name}",
            )
            return [row], []

        stem = Path(file_name).stem
        manifest_rows: list[dict] = []
        gcs_uploads: list[dict] = []
        total = len(page_scores)

        for score in page_scores:
            page_no = score["page_no"]
            page_name = f"{stem}_p{page_no:03d}.pdf"
            dest = f"{gcs_prefix.rstrip('/')}/{page_name}"
            quality = QualityStatus.ACCEPTED if score["passed"] else QualityStatus.REJECTED
            child = dest if score["passed"] else ""
            row = self._scored_row(job_id, pipeline_name, file_name, score, total, child, quality)
            manifest_rows.append(row)
            if score["passed"]:
                page_bytes = extract_single_page(content, page_no - 1)
                gcs_uploads.append({"content": page_bytes, "mime_type": _PDF_MIME, "destination_path": dest})

        return manifest_rows, gcs_uploads

    def _process_image(
        self,
        job_id: str,
        pipeline_name: str,
        file_name: str,
        content: bytes,
        gcs_prefix: str,
    ) -> tuple[list[dict], list[dict]]:
        """Score a single image file and return manifest row + optional upload dict.

        The aggregate result from ``score_image_bytes`` (top-level ``passed`` + min-page
        sub-scores) drives the gate; for a one-page image the aggregate equals the page.
        A rejected single-image file is a whole-file reject (handled by the caller), so this
        never emits a reject page entry.
        """
        try:
            score = image_utils.score_image_bytes(content, self._iqs_config)
        except Exception as exc:
            logger.warning(f"Image IQS scoring failed for {file_name}: {exc}")
            score = {"vq": 0.0, "sq": 0.0, "ct": 0.0, "iqs": 0.0, "passed": False}

        passed = bool(score.get("passed"))
        dest = f"{gcs_prefix.rstrip('/')}/{file_name}"
        quality = QualityStatus.ACCEPTED if passed else QualityStatus.REJECTED
        child = dest if passed else ""
        row = self._scored_row(job_id, pipeline_name, file_name, {**score, "page_no": 1}, 1, child, quality)

        if not passed:
            return [row], []

        mime_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
        return [row], [{"content": content, "mime_type": mime_type, "destination_path": dest}]

    def _scored_row(
        self,
        job_id: str,
        pipeline_name: str,
        parent_name: str,
        score: dict,
        total_pages: int,
        child_gcs_path: str,
        quality: QualityStatus,
        message: str | None = None,
    ) -> dict:
        """Build one manifest row dict from a scored page result.

        ``message`` overrides the reject reason (e.g. a technical failure); when omitted, an
        accepted page gets ``""`` and a rejected page a human-readable IQS reason.
        """
        if message is None:
            message = "" if quality == QualityStatus.ACCEPTED else iqs_reject_reason(score, self._iqs_config)
        return {
            "job_id": job_id,
            "pipeline_name": pipeline_name,
            "parent_path": parent_name,
            "parent_total_pages": total_pages,
            "page_no": score.get("page_no", 1),
            "child_path": child_gcs_path,
            "iqs_score": round(score.get("iqs", 0.0), 4),
            "vq_score": round(score.get("vq", 0.0), 4),
            "sq_score": round(score.get("sq", 0.0), 4),
            "ct_score": round(score.get("ct", 0.0), 4),
            "quality_status": quality.value,
            "message": message,
        }
