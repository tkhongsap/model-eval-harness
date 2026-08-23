import io
import json
import mimetypes
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from pandera.errors import SchemaError, SchemaErrors
from pydantic import ValidationError

from src.google_model.documents.schema.input_gt_schema import InputGTSchema
from src.google_model.documents.schema.metrics_schema import MetricsSchema
from src.google_model.documents.schema.model_response import ReceiptExtraction, SchemaHelper
from src.google_model.documents.schema.output_schema import OutputSchema
from src.google_model.documents.schema.processing_schema import ProcessingSchema
from src.google_model.usage_metrics import (
    STATUS_FAILED,
    STATUS_SUCCESS,
    USAGE_FIELDS,
    USAGE_TOTALS,
    batch_latency,
    build_summary_df,
    extract_usage,
    plan_row_order,
    summarize_usage,
)
from src.hook.gcp_gcs import GCSModule
from src.hook.gcp_genai import VertexAIBatchInference, VertexAIBatchTimeoutError
from src.hook.sharepoint import SharePointModule
from src.logger import Logger, TracedOperation

logger = Logger.get_logger(__name__)

# Declared ``Decimal`` in InputGTSchema, but the GT sheet is read ``dtype=str`` -- these are the
# columns _coerce_for_validation must parse before pandera can gate the frame.
MONEY_COLUMNS: tuple[str, ...] = (
    "Total amount",
    "Vat amount",
    "Net amount",
    "Withholding tax",
    "Invoice amount",
    "Vat invoice",
)

# Declared ``BooleanDtype`` in InputGTSchema; arrive as ``'True'``/``'False'`` strings from the
# same ``dtype=str`` read, which pandas' boolean coercion rejects outright.
BOOLEAN_COLUMNS: tuple[str, ...] = (
    "Copy",
    "Payee signature flag",
    "Authorized receiver signature flag",
    "Authorized signatory signature flag",
    "Stamp",
)


def _clean(value: Any) -> str:
    """Normalise a cell to a stripped string. NaN and None become ``""``."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


def _to_decimal(text: str) -> Decimal | None:
    """Parse a money cell, tolerating thousands separators. None when it is not a number."""
    try:
        return Decimal(text.replace(",", "").replace(" ", ""))
    except InvalidOperation:
        return None


def _to_bool(value: Any) -> bool | None:
    """Parse an Excel-round-tripped flag cell. None for a blank or unrecognised value.

    Excel stores a ``BooleanDtype`` cell back as the strings ``'True'``/``'False'``, which
    pandera's boolean coercion rejects -- so the flags are mapped here before validation, the
    same way ``debug/gt_build.py`` does it.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    return {"true": True, "false": False, "1": True, "0": False}.get(text)


def _coerce_for_validation(frame: pd.DataFrame) -> pd.DataFrame:
    """Pre-coerce the string GT frame so :class:`InputGTSchema` can gate it.

    The sheet is read ``dtype=str`` but the schema declares ``Decimal`` money and
    ``BooleanDtype`` flags, and pandera's coercion fails on ``'1,234.56'`` and
    ``'True'``. Unlike the evaluators, the returned frame is *kept*: it lands in the
    delivered workbook's ground-truth sheet. An unparsable money cell becomes None and
    is counted in a warning; a missing column is left for the schema to report.
    """
    copied = frame.copy()
    unparsable: dict[str, int] = {}

    for column in MONEY_COLUMNS:
        if column not in copied.columns:
            continue
        # One pass per column: the previous count-then-map ran _clean and
        # _to_decimal up to twice per cell each.
        converted: list[Any] = []
        bad = 0
        for value in copied[column]:
            text = _clean(value)
            if not text:
                converted.append(None)
                continue
            number = _to_decimal(text)
            if number is None:
                bad += 1
            converted.append(number)
        if bad:
            unparsable[column] = bad
        copied[column] = converted

    for column in BOOLEAN_COLUMNS:
        if column not in copied.columns:
            continue
        copied[column] = copied[column].map(_to_bool).astype("boolean")

    if unparsable:
        # Counts and column names only -- the offending cells are document content.
        logger.warning("google_document.gt.money_unparsable", columns=unparsable)

    return copied


def _elapsed_ms(started: float) -> float:
    """Milliseconds since a ``time.monotonic()`` reading, rounded for logging."""
    return round((time.monotonic() - started) * 1000, 1)


def _output_rename_map() -> dict[str, str]:
    """Map :class:`ProcessingSchema`'s aliases onto :class:`OutputSchema`'s sheet headers.

    ``OutputSchema``'s *attribute* name is ``ProcessingSchema``'s *alias* by
    construction, so attribute -> header is also processing -> output. Derived rather
    than spelled out so the mapping cannot drift; the zip is valid because pandera
    keeps class-body declaration order, the same order ``__annotations__`` reports.

    Raises:
      ValueError: If a ``ProcessingSchema`` column has no counterpart -- it would
        otherwise silently vanish in the final reindex.
    """
    renames = dict(
        zip(OutputSchema.__annotations__, OutputSchema.to_schema().columns, strict=True)
    )
    unmapped = [col for col in ProcessingSchema.to_schema().columns if col not in renames]
    if unmapped:
        raise ValueError(
            f"ProcessingSchema columns absent from OutputSchema: {unmapped}"
        )
    return renames


def _page_sort_key(unique_name: str) -> tuple[tuple[int, Any], ...]:
    """Natural-sort key for a page name, so ``_p2`` sorts before ``_p10``.

    Tokens are tagged ``(0, int)`` / ``(1, str)`` so tuples stay comparable when two
    names differ in shape at the same position.
    """
    return tuple(
        (0, int(token)) if token.isdigit() else (1, token)
        for token in re.split(r"(\d+)", unique_name)
    )

@dataclass
class Config:
    # src_file: str = '/poc_internal_model_migration/documentfiles'
    dest_file: str = '/poc_internal_model_migration/documentfiles_output'
    output_file_name: str = 'model_comparison.xlsx'

    # Prompt configuration (Local file)
    system_prompt_file: str = 'src/google_model/documents/prompt/system_prompt.md'

    # GCS configuration
    gcs_bucket: str = 'internal-model-poc'
    gcs_src_path: str = 'poc_internal_model/tax_invoice/google/src_files'
    gcs_payload_path: str = 'poc_internal_model/tax_invoice/google/payload_files'
    gcs_processing_path: str = 'poc_internal_model/tax_invoice/google/processing_files'
    gcs_dest_path: str = 'poc_internal_model/tax_invoice/google/dest_files'

    # Vertex AI configuration
    vertex_location: str = 'global'
    model_name: str = 'gemini-3.1-pro-preview'

    # Bounded re-check window for a terminal-but-unreadable job. Vertex never moves a job
    # out of FAILED/CANCELLED/EXPIRED, so a genuinely failed job costs one extra timeout.
    readable_wait_timeout: float = 600.0
    readable_poll_interval: float = 60.0

    # A dict cannot be a bare dataclass default -- it must go through default_factory.
    generation_config: dict[str, Any] = field(
        default_factory=lambda: {
            "temperature": 0.0,
            "topP": 1,
            "maxOutputTokens": 65535,
            "seed": 0,
            "thinkingConfig": {
                "thinkingBudget": -1
            }
        }
    )

    # Output XLSX configuration
    gt_src_file: str = '/poc_internal_model_migration/AI Benchmark Report.xlsx'
    gt_sheet_name: str = 'Doc - Groundtruth'
    output_sheet_name: str = 'Doc - Google Result'
    metrics_sheet_name: str = 'Matrix'
    metrics_summary_sheet_name: str = 'Matrix Summary'

def _row_order(submitted: list[str], parsed: dict[str, Any]) -> list[str]:
    """Page order for both sheets, with every submitted/parsed disagreement logged.

    Thin wrapper over :func:`~src.google_model.usage_metrics.plan_row_order`; this
    adds the event namespace and the natural page sort (lexicographic would put
    ``_p10`` ahead of ``_p2``).
    """
    order, counts = plan_row_order(submitted, parsed)

    if counts["duplicates"]:
        # A collapse here means two source documents shared a stem -- a real row-count
        # bug; silently losing a page is what this guards against.
        logger.warning(
            "google_document.dataframe.duplicate_submitted", count=counts["duplicates"]
        )
    if counts["missing"]:
        # These pages land in the sheet as blank rows; counts only -- a page name
        # carries the source file name, and this log is indexed by Cloud Logging.
        logger.warning(
            "google_document.dataframe.blank_rows",
            submitted=len(submitted),
            missing=counts["missing"],
        )
    if counts["extra"]:
        logger.warning("google_document.dataframe.unsubmitted_rows", count=counts["extra"])

    return sorted(order, key=_page_sort_key)


def build_output_df(
    items: list[dict[str, Any]],
    submitted: list[str],
    processing_metadata: dict[str, str],
) -> pd.DataFrame:
    """Flatten parsed model responses into the output sheet's DataFrame.

    One row per *printed line item*, in the model's (= printed) order, every amount
    carried verbatim -- a derived figure would score this function, not the model.
    The key is ``<unique_name>#<n>`` by position, not invoice number: the invoice
    number is itself under evaluation, and keying on it would turn a misread into a
    missing row on both sides of the join.

    **Every submitted page contributes at least one row**, parsed or not -- the
    scorers join with ``how="inner"`` and no parity check exists downstream, so a
    dropped page would quietly reduce N. A failed page still gets ``File name`` from
    ``processing_metadata``, the one column a failure can honestly populate.

    Args:
      items: ``{"unique_name", "file_name", "content"}`` rows, ``content`` a
        ``ReceiptExtraction.model_dump(mode="json")``.
      submitted: Every page name the payload was built from. Defines the page set.
      processing_metadata: ``split_doc``'s ``{unique_name: file_name}`` mapping.

    Returns:
      A DataFrame with exactly :class:`OutputSchema`'s columns, in declaration
      order, coerced and validated, and at least ``len(submitted)`` rows.
    """
    # The three columns read off a line item rather than the page -- why one page can
    # emit several rows. Every other column's ProcessingSchema name IS its
    # ReceiptExtraction field name, so it is resolved by name.
    line_item_fields = ("INVOICE_NUMBER", "INVOICE_AMOUNT_BEFORE_VAT", "INVOICE_VAT_AMOUNT")
    # No counterpart in the model at all: FILE_NAME rides on the item rather than being extracted
    # from the page, so it is filled from the split's metadata below instead of from `content`.
    unsourced = ("FILE_NAME",)

    processing_columns = list(ProcessingSchema.to_schema().columns)
    field_to_column = dict(
        zip(ProcessingSchema.__annotations__, processing_columns, strict=True)
    )
    page_fields = [
        name
        for name in ProcessingSchema.__annotations__
        if name not in line_item_fields and name not in unsourced
    ]

    by_page = {item["unique_name"]: item for item in items}
    if collisions := len(items) - len(by_page):
        logger.warning("google_document.dataframe.duplicate_parsed", count=collisions)

    rows: list[dict[str, Any]] = []
    keys: list[str] = []
    for page in _row_order(submitted, by_page):
        item = by_page.get(page)
        if item is None:
            # A page that never came back. Everything the model would have extracted stays null;
            # only the two columns the run knows independently of it are filled.
            page_values = dict.fromkeys(processing_columns)
            page_values[field_to_column["FILE_NAME"]] = processing_metadata.get(page)
            lines = [dict.fromkeys(line_item_fields)]
        else:
            content = item["content"]
            # Indexed, not .get() -- a column with no source is a mapping bug, and
            # .get() would ship a silent all-null column to the sheet.
            page_values = {field_to_column[name]: content[name] for name in page_fields}
            page_values[field_to_column["FILE_NAME"]] = item["file_name"]

            # A page with no detail table still gets its row (line-level columns null),
            # the same shape a failed page takes.
            lines = content["line_items"] or [dict.fromkeys(line_item_fields)]

        # enumerate, not the model's ITEM_NO: ITEM_NO is null whenever the page prints no row
        # number, and it is extracted data besides. The position is ours and is always there.
        for position, line in enumerate(lines, start=1):
            rows.append(
                {
                    **page_values,
                    **{field_to_column[name]: line[name] for name in line_item_fields},
                }
            )
            keys.append(f"{page}#{position}")

    # The explicit columns= makes an empty `items` yield a valid 0-row frame. This is
    # also the coercion step: money strings become Decimal, flags become BooleanDtype.
    processing_df = ProcessingSchema.validate(
        pd.DataFrame(rows, columns=processing_columns)
    )

    output_columns = list(OutputSchema.to_schema().columns)
    output_df = processing_df.rename(columns=_output_rename_map())
    output_df["No"] = range(1, len(output_df) + 1)
    output_df["Unique identifier"] = keys
    output_df = OutputSchema.validate(output_df[output_columns])

    # Unique by construction, so a repeat means a split bug. Count only (a key carries
    # the file name); logged rather than raised so one bad pair keeps the sheet.
    duplicates = len(keys) - len(set(keys))
    if duplicates:
        logger.warning("google_document.dataframe.duplicate_keys", count=duplicates)

    logger.info(
        "google_document.dataframe.built",
        pages=len(keys) and len({key.rsplit("#", 1)[0] for key in keys}),
        rows=len(output_df),
        columns=len(output_df.columns),
        submitted=len(submitted),
        parsed=len(by_page),
    )
    return output_df


def build_metrics_df(
    usage_rows: list[dict[str, Any]],
    submitted: list[str],
    processing_metadata: dict[str, str],
) -> pd.DataFrame:
    """Flatten per-page usage records into the Matrix sheet.

    Seeded from the same submitted list as :func:`build_output_df`, so both sheets
    cover the same pages. Failed rows are included -- they still burned the tokens
    Vertex charged for; a page that never came back gets ``Status=Failed`` with blank
    token cells and a blank ``Error Type``. ``No`` deliberately disagrees with the
    output sheet's ``No`` (that sheet is one row per line item); join on ``Page Name``.

    Args:
      usage_rows: ``{"unique_name", "status", "error_type", **extract_usage(item)}``
        records, one per prediction row.
      submitted: Every page name the payload was built from. Defines the row set.
      processing_metadata: ``split_doc``'s ``{unique_name: file_name}`` mapping.

    Returns:
      A DataFrame with exactly :class:`MetricsSchema`'s columns, in declaration
      order, coerced and validated, and ``len(submitted)`` rows.
    """
    metrics_columns = list(MetricsSchema.to_schema().columns)
    by_page = {row["unique_name"]: row for row in usage_rows}
    # A complete record so every field below can be indexed -- .get() would paper
    # over a wiring bug with a silent null column.
    absent = {"status": STATUS_FAILED, "error_type": "", **dict.fromkeys(USAGE_FIELDS)}

    records = []
    for no, page in enumerate(_row_order(submitted, by_page), start=1):
        row = by_page.get(page, absent)
        records.append(
            {
                "No": no,
                "Page Name": page,
                "File name": processing_metadata.get(page),
                "Status": row["status"],
                "Error Type": row["error_type"],
                **{header: row[key] for key, header in USAGE_TOTALS.items()},
                "Prompt Modalities": row["prompt_modalities"],
                "Traffic Type": row["traffic_type"],
            }
        )

    metrics_df = MetricsSchema.validate(pd.DataFrame(records, columns=metrics_columns))
    logger.info(
        "google_document.metrics.built",
        rows=len(metrics_df),
        columns=len(metrics_df.columns),
    )
    return metrics_df


SPLIT_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png")


def split_doc(
    files: list[str],
    gcs_client: GCSModule,
    bucket_name: str,
    processing_gcs_uri: str,
) -> dict[str, str]:
    """Split each source document into one GCS object per page.

    A batch request carries one file, so a multi-page PDF becomes one object per
    page. Pages are re-saved as PDFs, not rasterized -- Gemini reads the text layer.
    An image is already one page and is copied through unchanged.

    Args:
      files: Object names under the source prefix.
      gcs_client: The GCS client to read the sources and write the pages with.
      bucket_name: The bucket holding both -- a bucket name, not a project id.
      processing_gcs_uri: ``gs://`` directory the pages are written to.

    Returns:
      ``{unique_name: file_name}`` (``<stem>_p{n}<ext>`` -> source document), in page
      order -- what lets ``build_output_df`` report a page under its source document.
      Two sources sharing a stem would collide here; a collision is a better failure
      than two pages silently claiming to be the same one.
    """
    import pypdfium2 as pdfium

    started = time.monotonic()
    metadata: dict[str, str] = {}
    skipped = 0

    for file in files:
        file_name = Path(file).name
        ext = Path(file).suffix.lower()
        stem = Path(file).stem

        if ext != ".pdf" and ext not in SPLIT_IMAGE_EXTENSIONS:
            # Silently dropping it would leave a source file with no page, no metadata row and
            # no record of either -- the run would just come back short.
            skipped += 1
            logger.warning("google_document.split.file_skipped", file=file_name, ext=ext)
            continue

        content = gcs_client.download_file(bucket_name=bucket_name, file_path=file)
        pages = 0

        if ext == ".pdf":
            doc = pdfium.PdfDocument(content)
            try:
                for page_idx in range(len(doc)):
                    writer = pdfium.PdfDocument.new()
                    try:
                        writer.import_pages(doc, pages=[page_idx])
                        buf = io.BytesIO()
                        writer.save(buf)
                        page_content = buf.getvalue()
                    finally:
                        writer.close()

                    unique_name = f"{stem}_p{page_idx + 1}.pdf"
                    gcs_client.upload_file(
                        bucket_name=bucket_name,
                        upload_path=f"{processing_gcs_uri}/{unique_name}",
                        content=page_content,
                    )
                    metadata[unique_name] = file_name
                    pages += 1
            finally:
                # Once, after every page. Closing inside the loop invalidates the handle that
                # the next iteration's import_pages reads from.
                doc.close()
        else:
            # An image is already a single page; it only needs the same _p1 naming, so a
            # unique_name has one shape whatever the source was.
            unique_name = f"{stem}_p1{ext}"
            gcs_client.upload_file(
                bucket_name=bucket_name,
                upload_path=f"{processing_gcs_uri}/{unique_name}",
                content=content,
            )
            metadata[unique_name] = file_name
            pages = 1

        logger.debug("google_document.split.file_completed", file=file_name, pages=pages)

    logger.info(
        "google_document.split.summary",
        source_files=len(files),
        pages=len(metadata),
        skipped=skipped,
        elapsed_ms=_elapsed_ms(started),
    )
    return metadata

def run():
    tz = "Asia/Bangkok"
    run_dt = datetime.now(ZoneInfo(tz)) 
    run_dt_str = run_dt.strftime("%Y-%m-%d_%H-%M-%S")
    run_date = run_dt.strftime("%Y-%m-%d")
    config = Config()

    gcs_dest_file = config.gcs_dest_path + "/" + run_dt_str
    gcs_payload_file = config.gcs_payload_path + "/" + run_dt_str
    gcs_processing_file = config.gcs_processing_path + "/" + run_dt_str

    payload_gcs_uri = f"gs://{config.gcs_bucket}/{gcs_payload_file}/payload.jsonl"
    dest_gcs_uri = f"gs://{config.gcs_bucket}/{gcs_dest_file}"
    processing_gcs_uri = f"gs://{config.gcs_bucket}/{gcs_processing_file}"

    with TracedOperation(
        "google_document.run",
        run_id=run_dt_str,
        model=config.model_name,
    ):
        logger.info(
            "google_document.run.starting",
            run_id=run_dt_str,
            timezone=tz,
            bucket=config.gcs_bucket,
            src_prefix=config.gcs_src_path,
            payload_uri=payload_gcs_uri,
            dest_uri=dest_gcs_uri,
            processing_uri=processing_gcs_uri,
            sharepoint_dest=config.dest_file,
            model=config.model_name,
        )

        client_gcs = GCSModule(
            project_id=os.environ["GCP_PROJECT_ID"],
            timezone=tz,
        )
        client_sb = SharePointModule(
            client_id=os.environ["SANDBOX_CLIENT_ID"],
            client_secret=os.environ["SANDBOX_CLIENT_SECRET"],
            tenant_id=os.environ["SANDBOX_TENANT_ID"],
            site_domain=os.environ["SANDBOX_SITE_DOMAIN"],
            site_path=os.environ["SANDBOX_SITE_PATH"],
            timezone=tz,
        )

        with io.BytesIO(client_sb.download_file(config.gt_src_file).content) as f:
            book = pd.ExcelFile(f, engine="openpyxl")
            if config.gt_sheet_name not in book.sheet_names:
                logger.error(
                    "google_document.run.aborted",
                    reason="gt_sheet_missing",
                    file=config.gt_src_file,
                    sheet=config.gt_sheet_name,
                    # Sheet names only -- never cell content.
                    available=book.sheet_names,
                )
                return
            gt_df = book.parse(
                config.gt_sheet_name,
                dtype=str,
                header=0,
                keep_default_na=False,
                na_values=[""],
            )

        try:
            gt_df = InputGTSchema.validate(_coerce_for_validation(gt_df))
        except (SchemaError, SchemaErrors) as e:
            # Computed here rather than read off the exception: e.data, e.failure_cases
            # and str(e) can all quote offending cell values.
            missing = sorted(set(InputGTSchema.to_schema().columns) - set(gt_df.columns))
            logger.error(
                "google_document.run.aborted",
                reason="gt_schema_invalid",
                file=config.gt_src_file,
                sheet=config.gt_sheet_name,
                missing_columns=missing,
                error_type=type(e).__name__,
                # The column for a per-column fault, the schema name for a frame-level one.
                schema=getattr(e.schema, "name", None),
            )
            return

        logger.info(
            "google_document.gt.loaded",
            file=config.gt_src_file,
            sheet=config.gt_sheet_name,
            rows=len(gt_df),
        )

        src_files = client_gcs.list_files(
            bucket_name=config.gcs_bucket,
            prefix=config.gcs_src_path,
        )

        if not src_files:
            logger.error(
                "google_document.run.aborted",
                reason="no_source_files",
                bucket=config.gcs_bucket,
                prefix=config.gcs_src_path,
            )
            return

        logger.info(
            "google_document.source.listed",
            bucket=config.gcs_bucket,
            prefix=config.gcs_src_path,
            count=len(src_files),
        )

        with TracedOperation("google_document.split"):
            processing_metadata = split_doc(
                files=src_files,
                gcs_client=client_gcs,
                bucket_name=config.gcs_bucket,
                processing_gcs_uri=processing_gcs_uri,
            )

        # This run's prefix, listed recursively: a non-recursive listing of the parent
        # sees no objects, and a recursive one of the parent sweeps in prior runs.
        files = client_gcs.list_files(
            bucket_name=config.gcs_bucket,
            prefix=gcs_processing_file,
            recursive=True,
        )

        logger.info(
            "google_document.processing.listed",
            bucket=config.gcs_bucket,
            prefix=gcs_processing_file,
            objects=len(files),
            metadata_rows=len(processing_metadata),
        )

        if not files or len(processing_metadata) == 0:
            logger.error(
                "google_document.run.aborted",
                reason="no_processing_files",
                bucket=config.gcs_bucket,
                prefix=gcs_processing_file,
            )
            return


        with open(config.system_prompt_file, 'r', encoding='utf-8') as f:
            system_prompt = f.read()

        logger.debug(
            "google_document.prompts.loaded",
            system_prompt_chars=len(system_prompt),
            run_date=run_date,
        )

        response_config = SchemaHelper.vertex_generation_config()
        with TracedOperation("google_document.payload"):
            # Built as bytes directly: a list of row strings joined and encoded at the end
            # holds the payload ~3x at the join. Separator-first keeps the bytes identical.
            buffer = bytearray()
            payload_rows = 0
            started = time.monotonic()
            for file in files:
                file_name = Path(file).name
                gcs_uri = f"gs://{config.gcs_bucket}/{file}"
                mime_type, _ = mimetypes.guess_type(file_name)

                if mime_type is None:
                    logger.warning("google_document.mime.unresolved", file=file_name)

                payload ={
                    "request": {
                        "contents": [
                            {
                                "role": "user",
                                "parts": [
                                    {
                                        "file_data": {
                                            "mime_type": mime_type,
                                            "file_uri": gcs_uri,
                                        }
                                    }
                                ],
                            }
                        ],
                        "system_instruction": {"parts": [{"text": system_prompt}]},
                        "generation_config": {
                            **config.generation_config,
                            **response_config,
                        },
                    }
                }
                if payload_rows:
                    buffer += b"\n"
                buffer += json.dumps(payload, ensure_ascii=False).encode("utf-8")
                payload_rows += 1

            jsonl_byte = bytes(buffer)
            del buffer

            # Bound here: the Matrix Summary reports this duration, and re-reading the
            # anchor later would measure payload-start to *now*.
            payload_ms = _elapsed_ms(started)

            logger.info(
                "google_document.payload.assembled",
                source_files=len(files),
                rows=payload_rows,
                bytes=len(jsonl_byte),
                elapsed_ms=payload_ms,
            )

        with TracedOperation("google_document.batch"):
            batch_started = time.monotonic()
            client_gcs.upload_file(
                bucket_name=config.gcs_bucket,
                upload_path=payload_gcs_uri,
                content=jsonl_byte,
                mime_type="application/json",
            )
            client_genai = VertexAIBatchInference(
                project_id=os.environ["GCP_PROJECT_ID"],
                location=config.vertex_location,
            )
            job = client_genai.submit_batch_job(
                model=config.model_name,
                src=payload_gcs_uri,
                dest=dest_gcs_uri,
                display_name=f"google_documents_{run_dt_str}",
            )

            logger.info(
                "google_document.batch.submitted",
                job=job.name,
                payload_uri=payload_gcs_uri,
                dest_uri=dest_gcs_uri,
            )

            job = client_genai.wait_for_batch_job(job_name=job.name, poll_interval=60)

            # wait_for_batch_job returns any terminal state, FAILED included;
            # READABLE_STATES is the set pull_batch_job_results checks. The bounded
            # re-poll only helps a state still settling -- Vertex never un-fails a job,
            # so a real failure just spends readable_wait_timeout before raising.
            readable_deadline = time.monotonic() + config.readable_wait_timeout
            while job.state not in client_genai.READABLE_STATES:
                remaining = readable_deadline - time.monotonic()

                if remaining <= 0:
                    logger.error(
                        "google_document.batch.finished",
                        job=job.name,
                        state=str(job.state),
                        # The only place the cause is available -- wait_for_batch_job logs
                        # these too, but at WARNING, and never re-reads them after.
                        error_code=job.error.code if job.error else None,
                        error_message=job.error.message if job.error else None,
                        waited=config.readable_wait_timeout,
                        elapsed_ms=_elapsed_ms(batch_started),
                    )
                    raise VertexAIBatchTimeoutError(
                        f"Batch job {job.name} did not reach a readable state within "
                        f"{config.readable_wait_timeout}s (state={job.state})"
                    )

                logger.warning(
                    "google_document.batch.unreadable",
                    job=job.name,
                    state=str(job.state),
                    retry_in=config.readable_poll_interval,
                    remaining=round(remaining, 1),
                )
                time.sleep(min(config.readable_poll_interval, remaining))
                job = client_genai.pull_batch_job(job.name)

            # Bound for the same reason as payload_ms -- the summary sheet reports it.
            batch_ms = _elapsed_ms(batch_started)

            logger.info(
                "google_document.batch.finished",
                job=job.name,
                state=str(job.state),
                elapsed_ms=batch_ms,
            )

            output_uri = client_genai.pull_batch_job_results(job)
            # A directory of JSONL shards, not one file. Reading a fixed
            # "predictions.jsonl" would silently drop rows on a sharded job.
            prediction_shards = client_gcs.list_files(
                bucket_name=config.gcs_bucket,
                prefix=output_uri,
                recursive=True,
                pattern=r"\.jsonl$",
            )

            logger.info(
                "google_document.results.located",
                job=job.name,
                output_uri=output_uri,
                shards=len(prediction_shards),
            )

            if not prediction_shards:
                logger.error(
                    "google_document.run.aborted",
                    reason="no_prediction_shards",
                    job=job.name,
                    output_uri=output_uri,
                )
                return

        with TracedOperation("google_document.results"):
            parse_started = time.monotonic()
            parsed_results = []
            total_lines = 0
            line_errors = 0

            for shard in prediction_shards:
                shard_name = Path(shard).name
                shard_lines = 0
                shard_errors = 0
                content = client_gcs.download_file(
                    bucket_name=config.gcs_bucket,
                    file_path=shard,
                )
                # Decode once and drop the raw bytes before iterating, so the shard is not
                # held twice while its rows are parsed.
                decoded = content.decode("utf-8")
                del content

                with io.StringIO(decoded) as f:
                    del decoded
                    # enumerate over the file, so line_no counts *file* lines -- the old
                    # counter skipped blanks and pointed at the wrong line in the shard.
                    for line_no, raw in enumerate(f, start=1):
                        line = raw.rstrip("\n")
                        if not line.strip():
                            logger.debug(
                                "google_document.parse.line_empty",
                                shard=shard_name,
                                line_no=line_no,
                            )
                            continue
                        shard_lines += 1
                        try:
                            parsed_results.append(json.loads(line))
                        except json.JSONDecodeError as e:
                            shard_errors += 1
                            # Never the line itself: a predictions line carries the
                            # extracted document (PII) and this log is indexed. Shard,
                            # position and size locate it by hand.
                            logger.warning(
                                "google_document.parse.line_failed",
                                shard=shard_name,
                                line_no=line_no,
                                line_bytes=len(line),
                                error_type=type(e).__name__,
                                error_pos=e.pos,
                            )
                        except Exception as e:
                            shard_errors += 1
                            logger.warning(
                                "google_document.parse.line_failed",
                                shard=shard_name,
                                line_no=line_no,
                                line_bytes=len(line),
                                error_type=type(e).__name__,
                                exc_info=True,
                            )

                total_lines += shard_lines
                line_errors += shard_errors
                logger.debug(
                    "google_document.parse.shard",
                    shard=shard_name,
                    lines=shard_lines,
                    errors=shard_errors,
                )

            logger.info(
                "google_document.parse.summary",
                shards=len(prediction_shards),
                total_lines=total_lines,
                parse_errors=line_errors,
                parsed_results=len(parsed_results),
                elapsed_ms=_elapsed_ms(parse_started),
            )

            items = []
            usage_rows = []
            item_errors = 0
            unmatched_pages = 0
            for item in parsed_results:
                # Bound before the try so a failure still names the page it belongs to.
                unique_name = ""
                # Vertex's own error field for a failed row, truncated free text.
                # Logged, never sheeted -- a status message can echo request content.
                vertex_status = str(item.get("status"))[:300] if item.get("status") else None
                # Read before the try by a function that cannot raise: tokens burned are
                # a fact about the row whether or not its content parses.
                usage = extract_usage(item)
                row_status = STATUS_FAILED
                error_type = ""
                try:
                    # Guarded step by step: a row Vertex failed on carries "status", not
                    # "response", and a chained .get() would raise outside any handler.
                    request = item.get("request") or {}
                    contents = request.get("contents") or [{}]
                    req_parts = contents[0].get("parts") or [{}]
                    file_uri = (req_parts[0].get("file_data") or {}).get("file_uri", "")
                    unique_name = Path(file_uri).name if file_uri else file_uri

                    response = item.get("response") or {}
                    candidates = response.get("candidates") or []
                    if not candidates:
                        raise ValueError("prediction row carries no candidates")
                    parts = (candidates[0].get("content") or {}).get("parts") or []
                    if not parts:
                        raise ValueError("prediction candidate carries no parts")
                    text = parts[0].get("text") or ""

                    parsed = ReceiptExtraction.model_validate_json(text)
                    # None on a miss, not "": FILE_NAME is nullable, and a blank cell
                    # reads as missing where "" reads as an extracted value.
                    source_name = processing_metadata.get(unique_name)
                    if source_name is None:
                        unmatched_pages += 1
                    items.append(
                        {
                            "unique_name": unique_name,
                            "file_name": source_name,
                            "content": parsed.model_dump(mode="json"),
                        }
                    )
                    # Last statement in the try, so nothing above it can be skipped and still
                    # report success.
                    row_status = STATUS_SUCCESS
                except ValidationError as e:
                    item_errors += 1
                    error_type = type(e).__name__
                    # Field paths only: pydantic echoes the offending value into
                    # errors() and str(e).
                    logger.warning(
                        "google_document.item.failed",
                        page=unique_name,
                        error_type=error_type,
                        error_fields=[
                            ".".join(str(part) for part in err["loc"]) for err in e.errors()
                        ],
                    )
                except Exception as e:
                    item_errors += 1
                    error_type = type(e).__name__
                    logger.warning(
                        "google_document.item.failed",
                        page=unique_name,
                        error_type=error_type,
                        status=vertex_status,
                        exc_info=True,
                    )

                # After the try/except, not a finally (which would also fire on
                # KeyboardInterrupt): every prediction row contributes exactly one
                # metrics row, failures included.
                usage_rows.append(
                    {
                        "unique_name": unique_name,
                        "status": row_status,
                        "error_type": error_type,
                        **usage,
                    }
                )

            if unmatched_pages:
                # split_doc and the predictions disagree; such rows reach the sheet with
                # a blank File name. Count only -- a page name is a file name.
                logger.warning(
                    "google_document.item.unmatched_pages",
                    count=unmatched_pages,
                )

            logger.info(
                "google_document.item.summary",
                parsed=len(items),
                failed=item_errors,
            )

            # Both sheets' page set: what was re-listed and actually submitted -- not
            # processing_metadata, which is what split_doc wrote; the two are logged
            # side by side above precisely because they can disagree.
            submitted = [Path(file).name for file in files]

            output_df = build_output_df(items, submitted, processing_metadata)
            metrics_df = build_metrics_df(usage_rows, submitted, processing_metadata)
            usage_summary = summarize_usage(metrics_df)
            latency = {
                **batch_latency(job.create_time, job.start_time, job.update_time),
                "payload_ms": payload_ms,
                "batch_ms": batch_ms,
                "results_ms": _elapsed_ms(parse_started),
            }
            summary_df = build_summary_df(
                run_id=run_dt_str,
                model=config.model_name,
                job_name=job.name,
                # .value, not str(): the sheet gets the bare state, not "JobState.X".
                job_state=job.state.value if job.state else "",
                counts={
                    "source_files": len(files),
                    "submitted": payload_rows,
                    "line_errors": line_errors,
                },
                usage=usage_summary,
                latency=latency,
            )

            # The one record that says what the run cost. Token counts and durations are not PII:
            # no page names, no extracted document, no model output.
            logger.info(
                "google_document.usage.summary",
                pages=usage_summary["files"],
                succeeded=usage_summary["succeeded"],
                failed=usage_summary["failed"],
                pages_with_usage=usage_summary["files_with_usage"],
                success_rate=round(usage_summary["success_rate"], 4),
                **{key: usage_summary[key] for key in USAGE_TOTALS},
                avg_total_tokens=round(usage_summary["avg_total_tokens"], 1),
                prompt_modalities=usage_summary["prompt_modalities"],
                traffic_types=usage_summary["traffic_types"],
                queue_seconds=latency["queue_seconds"],
                run_seconds=latency["run_seconds"],
                wall_seconds=latency["wall_seconds"],
            )

            dest_file_path = f"{config.dest_file}/{run_dt_str}"
            output_path = f"{dest_file_path}/{config.output_file_name}"

            # gt_df was read and validated before the batch job -- see the pre-flight above.
            with io.BytesIO() as output_buffer:
                with pd.ExcelWriter(output_buffer, engine='openpyxl') as writer:
                    gt_df.to_excel(writer, index=False, sheet_name=config.gt_sheet_name)
                    output_df.to_excel(writer, index=False, sheet_name=config.output_sheet_name)
                    # Written last, so the delivered workbook still opens on the ground truth and
                    # the result tabs and the ops sheets sit behind them.
                    summary_df.to_excel(
                        writer, index=False, sheet_name=config.metrics_summary_sheet_name
                    )
                    metrics_df.to_excel(writer, index=False, sheet_name=config.metrics_sheet_name)
                    # Metric names run long and the default width truncates them mid-word; the
                    # per-page sheet's own headers are already about as wide as its values.
                    summary_sheet = writer.sheets[config.metrics_summary_sheet_name]
                    summary_sheet.column_dimensions["A"].width = 32
                    summary_sheet.column_dimensions["B"].width = 26
                workbook_byte = output_buffer.getvalue()

            # Guarded: the predictions are in GCS and the batch job already paid for, so
            # a workbook fault (lock, 503, illegal path) must not take the run's record
            # with it. SharePointModule already logged the cause at its single ERROR site.
            output_uploaded = False
            try:
                client_sb.upload_file(upload_path=output_path, content=workbook_byte)
                output_uploaded = True
                # Both counts: `rows` is line items, `pages` is document pages -- the
                # two failure shapes are indistinguishable from `rows` alone.
                logger.info(
                    "google_document.output.uploaded",
                    path=output_path,
                    rows=len(output_df),
                    pages=len(submitted),
                    bytes=len(workbook_byte),
                )
            except Exception:
                logger.warning(
                    "google_document.output.skipped",
                    path=output_path,
                    rows=len(output_df),
                    pages=len(submitted),
                )
            del workbook_byte  # free the workbook copy once the upload is settled

        # Counts at every stage, not just the submitted row count: a run that submits 100
        # and writes 3 is a bad run, and "rows=100" alone reported it as a good one.
        logger.info(
            "google_document.run.completed",
            run_id=run_dt_str,
            job=job.name,
            state=str(job.state),
            source_files=len(files),
            submitted=payload_rows,
            parsed=len(parsed_results),
            # Both, because the grain differs: `written` is line-item rows and
            # `pages_written` is document pages.
            written=len(output_df),
            pages_written=len(submitted),
            output_path=output_path,
            # So a partial run reads as partial in one record.
            output_uploaded=output_uploaded,
            # Enough that run.completed alone can trend spend and success rate.
            success_rate=round(usage_summary["success_rate"], 4),
            succeeded=usage_summary["succeeded"],
            failed=usage_summary["failed"],
            prompt_tokens=usage_summary["prompt_tokens"],
            candidates_tokens=usage_summary["candidates_tokens"],
            thoughts_tokens=usage_summary["thoughts_tokens"],
            total_tokens=usage_summary["total_tokens"],
            batch_wall_seconds=latency["wall_seconds"],
        )