"""Page processor — read landing files, split + IQS-score pages, upload accepted chunks."""

from __future__ import annotations

from src.modules.google.gcs import GCSModule
from src.utils.logger import Logger
from tasks.ocr_tax_invoice_pipeline.module.document_processor import DocumentProcessor
from tasks.ocr_tax_invoice_pipeline.schema.contracts import ChunkEntry

logger = Logger(__name__)


class PageProcessor:
    """Turns landing-uploaded files into page-manifest rows + accepted GCS chunk entries.

    For each landed file: read its bytes from the GCS landing bucket, run
    :class:`DocumentProcessor` (per-page split + IQS scoring), enrich the manifest rows with
    full ``gs://`` chunk URIs, and upload accepted pages to the processing prefix.
    """

    def __init__(
        self,
        landing_gcs: GCSModule,
        processing_gcs: GCSModule,
        processing_prefix: str,
        doc_processor: DocumentProcessor,
        job_id: str,
        pipeline_name: str,
    ) -> None:
        """Initialise with the landing/processing GCS modules and the document processor.

        Args:
            landing_gcs: GCS module for the bucket holding the immutable landing copies.
            processing_gcs: GCS module for the bucket receiving accepted page chunks.
            processing_prefix: Bucket-relative prefix for accepted page chunks.
            doc_processor: Per-page split + IQS scorer.
            job_id: Current run's job id (passed to the document processor).
            pipeline_name: Current pipeline name (passed to the document processor).
        """
        self._landing_gcs = landing_gcs
        self._processing_gcs = processing_gcs
        self._processing_prefix = processing_prefix
        self._doc_processor = doc_processor
        self._job_id = job_id
        self._pipeline_name = pipeline_name

    def run(self, uploaded: list[dict]) -> tuple[list[dict], list[ChunkEntry]]:
        """Process every landed file; return ``(manifest_rows, chunk_entries)``.

        Args:
            uploaded: Landing-upload result dicts (``name``, ``sp_path``, ``gcs_path``).

        Returns:
            All page-manifest rows and the accepted-chunk entries uploaded this run.
        """
        all_manifest: list[dict] = []
        chunk_entries: list[ChunkEntry] = []
        for item in uploaded:
            manifest_rows, gcs_uploads = self._process_file(item)
            # Rewrite each non-empty child_path to a full gs://bucket/... URI; rejected and
            # technical-failure rows keep child_path == "" (no chunk was uploaded).
            for row in manifest_rows:
                if row.get("child_path"):
                    row["child_path"] = f"gs://{self._processing_gcs.bucket_name}/{row['child_path']}"
            all_manifest.extend(manifest_rows)
            for upload in gcs_uploads:
                self._processing_gcs.update_content_to_gcs(
                    upload["content"], upload["mime_type"], upload["destination_path"]
                )
                gcs_uri = f"gs://{self._processing_gcs.bucket_name}/{upload['destination_path']}"
                chunk_entries.append(ChunkEntry(parent_landing_path=item["gcs_path"], gcs_uri=gcs_uri))
        return all_manifest, chunk_entries

    def _process_file(self, item: dict) -> tuple[list[dict], list[dict]]:
        """Read one landing copy and run the document processor; enrich ``parent_path``.

        The file is read back from its immutable GCS landing copy rather than re-downloaded
        from SharePoint. A read failure logs a warning and yields no rows (one bad file must
        not abort the batch).
        """
        try:
            content = self._landing_gcs.download_file_from_gcs(item["gcs_path"])
        except Exception as exc:
            logger.warning(f"Failed to read landing copy {item['gcs_path']}: {exc}")
            return [], []

        manifest_rows, gcs_uploads = self._doc_processor.process(
            job_id=self._job_id,
            pipeline_name=self._pipeline_name,
            file_name=item["name"],
            content=content,
            processing_gcs_prefix=self._processing_prefix,
        )
        for row in manifest_rows:
            row["parent_path"] = item["gcs_path"]
        return manifest_rows, gcs_uploads
