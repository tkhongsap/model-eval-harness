"""Result finalizer — join predictions to source file/page and shape to OCROutputSchema."""

from __future__ import annotations

import json

import pandas as pd

from src.utils.duckdb_utils import connect_decimal_safe
from src.utils.logger import Logger
from tasks.ocr_tax_invoice_pipeline.helper.constant import OCROutputStatus, QualityStatus
from tasks.ocr_tax_invoice_pipeline.schema.ocr_output import OCROutputSchema

logger = Logger(__name__)


class ResultFinalizer:
    """Joins predictions to their source file/page and shapes them to the output contract.

    Combines two row sources: the model predictions (joined to their page via ``child_path``
    == ``source_file_uri``) and the IQS-rejected manifest pages for the same jobs (model
    fields null, ``STATUS = FAILED``, reject reason in ``MESSAGE``). The union is validated
    against :class:`OCROutputSchema` and narrowed to its columns. Pure transform; no I/O.
    """

    def run(
        self, result_df: pd.DataFrame, pre_processing_log: pd.DataFrame, page_manifest_log: pd.DataFrame
    ) -> pd.DataFrame:
        """Join predictions to source file/page, union IQS-rejected pages, and validate.

        Args:
            result_df: Collected predictions (one row per line item).
            pre_processing_log: Append-only pre-processing log.
            page_manifest_log: Per-page manifest with GCS chunk placement and IQS scores.

        Returns:
            The finalized, schema-validated frame to forward to the next phase.
        """
        # usage_metadata dict-repr workaround; see DEVELOPER_GUIDE.md § 6.5(a).
        result_df = result_df.copy()
        result_df["usage_metadata"] = result_df["usage_metadata"].map(
            lambda d: json.dumps(d) if isinstance(d, dict) else None
        )

        con = connect_decimal_safe()
        con.register("result_df", result_df)
        con.register("pre_processing_log", pre_processing_log)
        con.register("page_manifest_log", page_manifest_log)
        predicted = con.execute(self._predicted_stmt()).df()
        rejected = con.execute(self._rejected_stmt()).df()

        df = pd.concat([predicted, rejected], ignore_index=True)
        df.columns = df.columns.str.upper()
        df["USAGE_METADATA"] = df["USAGE_METADATA"].map(lambda s: json.loads(s) if isinstance(s, str) else None)
        df = df[list(OCROutputSchema.to_schema().columns.keys())]
        df = self._coerce_invoice_date(df)
        df = self._append_page_to_suspicious(df)
        return OCROutputSchema.validate(df)

    @staticmethod
    def _append_page_to_suspicious(df: pd.DataFrame) -> pd.DataFrame:
        """Append ``(page N)`` to each SUSPICIOUS row's MESSAGE once PAGE_NO is joined on.

        The retriever sets a Suspicious row's MESSAGE to the model's reason but cannot know the
        page number (joined here from the manifest); the page is appended so the reason + page
        flow together into the Output Report and the pre-processing-log terminal message.
        """
        mask = (df["STATUS"] == OCROutputStatus.SUSPICIOUS.value) & df["PAGE_NO"].notna()
        if not mask.any():
            return df
        df.loc[mask, "MESSAGE"] = df.loc[mask].apply(
            lambda r: f"{(r['MESSAGE'] or '').strip()} (page {int(r['PAGE_NO'])})".strip(), axis=1
        )
        return df

    @staticmethod
    def _predicted_stmt() -> str:
        """Build the SQL that surfaces model-predicted pages of this run's jobs with file/page context.

        FILE_PATH is always the SharePoint path of the original document, never a GCS URI; see
        DEVELOPER_GUIDE.md § 6.5(c).
        """
        return """
        WITH map_page_to_file AS (
            SELECT DISTINCT ppl.sharepoint_input_path AS FILE_PATH
            , ppl.batch_inference_job_name AS BATCH_JOB_NAME
            , pml.parent_path AS GCS_LANDING_PATH
            , pml.child_path AS GCS_PROCESSING_PATH
            , pml.page_no AS PAGE_NO
            , pml.iqs_score AS IQS_SCORE
            , ppl.datadate AS DATADATE
            FROM pre_processing_log ppl
            LEFT JOIN page_manifest_log pml
            ON ppl.job_id = pml.job_id
            AND ppl.gcs_landing_path = pml.parent_path
            AND ppl.batch_inference_job_name IS NOT NULL
        )
        SELECT rdf.*
            , mpf.FILE_PATH
            , SPLIT_PART(mpf.FILE_PATH, '/', -1) AS FILE_NAME
            , mpf.PAGE_NO
            , mpf.IQS_SCORE
            , mpf.DATADATE
        FROM result_df rdf
        LEFT JOIN map_page_to_file mpf
        ON rdf.batch_inference_job_name = mpf.BATCH_JOB_NAME
        AND rdf.source_file_uri = mpf.GCS_PROCESSING_PATH
        """

    @staticmethod
    def _rejected_stmt() -> str:
        """Build the SQL that surfaces IQS-rejected manifest pages of this run's jobs as FAILED."""
        return f"""
        SELECT ppl.batch_inference_job_name AS batch_inference_job_name
            , pml.child_path AS source_file_uri
            , '{OCROutputStatus.FAILED.value}' AS status
            , pml.message AS message
            , ppl.sharepoint_input_path AS FILE_PATH
            , SPLIT_PART(pml.parent_path, '/', -1) AS FILE_NAME
            , pml.page_no AS PAGE_NO
            , pml.iqs_score AS IQS_SCORE
            , ppl.datadate AS DATADATE
        FROM page_manifest_log pml
        JOIN pre_processing_log ppl
        ON ppl.job_id = pml.job_id
        AND ppl.gcs_landing_path = pml.parent_path
        AND ppl.batch_inference_job_name IS NOT NULL
        WHERE pml.quality_status <> '{QualityStatus.ACCEPTED.value}'
        AND ppl.batch_inference_job_name IN (
            SELECT DISTINCT batch_inference_job_name FROM result_df
            WHERE batch_inference_job_name IS NOT NULL
        )
        """

    @staticmethod
    def _coerce_invoice_date(df: pd.DataFrame) -> pd.DataFrame:
        """Land ``TAX_INVOICE_DATE`` (model ISO strings) as ``date`` objects, null-safe.

        The schema column is object-typed because pandera's ``date`` Series type rejects an
        all-null column; this converts real values to ``datetime.date`` and leaves blanks
        (FAILED/rejected rows, unparseable dates) as ``None``.
        """
        parsed = pd.to_datetime(df["TAX_INVOICE_DATE"], errors="coerce").dt.date
        df["TAX_INVOICE_DATE"] = parsed.astype(object).where(pd.notna(parsed), None)
        return df
