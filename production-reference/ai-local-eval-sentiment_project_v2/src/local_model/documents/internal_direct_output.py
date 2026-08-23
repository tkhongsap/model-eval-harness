import base64
import contextvars
import io
import json
import os
import random
import re
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pandas as pd
from openai import APIError, ContentFilterFinishReasonError, LengthFinishReasonError, OpenAI
from pandera.errors import SchemaError, SchemaErrors
from pydantic import ValidationError

from src.google_model.documents.schema.input_gt_schema import InputGTSchema
from src.google_model.documents.schema.model_response import ReceiptExtraction
from src.google_model.documents.schema.output_schema import OutputSchema
from src.google_model.documents.schema.processing_schema import ProcessingSchema
from src.google_model.usage_metrics import STATUS_FAILED, STATUS_SUCCESS, plan_row_order
from src.hook.gcp_gcs import GCSModule
from src.hook.sharepoint import SharePointModule
from src.local_model.documents.schema.metrics_schema import MetricsSchema
from src.logger import Logger, TracedOperation
from src.utils.common import get_env, get_env_int

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

SPLIT_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png")

# Fixed map rather than ``mimetypes.guess_type``: the endpoint accepts exactly these three
# formats, and guess_type consults the OS registry on Windows, which can misname them.
_DATA_URL_MIME: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}

# The endpoint's documented image caps. 8 MB decoded is ~10.7 MB after base64, which stays
# under the 16 MB HTTP body limit, so the body needs no separate check.
_MAX_DECODED_IMAGE_BYTES = 8 * 1024 * 1024
_MAX_IMAGE_PIXELS = 40_000_000

# The metrics keys every per-page record carries; also the "absent row" template. Blank
# token cells mean "not reported", never 0 -- see the MetricsSchema docstring.
_METRICS_DEFAULTS: dict[str, Any] = {
    "status": STATUS_FAILED,
    "error_type": "",
    "attempts": None,
    "prompt_tokens": None,
    "completion_tokens": None,
    "total_tokens": None,
}


def _elapsed_ms(started: float) -> float:
    """Milliseconds since a ``time.monotonic()`` reading, rounded for logging."""
    return round((time.monotonic() - started) * 1000, 1)


def _backoff_s(attempt: int) -> float:
    """Delay before retrying after ``attempt`` failed: 1, 2, 4 ... capped at 16s.

    Jittered by up to +50% so parallel workers whose retries were synchronised by
    one gateway incident do not re-arrive as a single burst.
    """
    return min(16.0, 2.0 ** (attempt - 1)) * (1.0 + random.random() * 0.5)


class StreamDeadlineError(ValueError):
    """A stream ran past ``llm_stream_deadline`` without finishing.

    Subclasses ``ValueError`` so ``extract_page``'s existing except tuple catches it
    unchanged, while ``type(e).__name__`` still names it distinctly in the Error Type
    column. Its message carries a duration only -- never page content.
    """


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
        logger.warning("internal_document.gt.money_unparsable", columns=unparsable)

    return copied


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


def _doc_llm_concurrency() -> int:
    """Effective ``DOC_LLM_CONCURRENCY``: parallel ``extract_page`` calls per chunk.

    Read at ``Config()`` instantiation (via ``default_factory``), so ``main.py``'s
    ``load_dotenv()`` has already run and the menu's Config preview shows the value
    the run will use. Unset means 1 -- today's sequential behaviour, silently. A
    set-but-bad value (unparsable or < 1) warns and falls back to 1, matching
    ``LoggerConfig.from_env``'s fail-soft asymmetry: a typo must not crash startup.
    """
    raw = get_env("DOC_LLM_CONCURRENCY")
    if raw is None or raw == "":
        return 1
    # Sentinel 0: get_env_int returns it only when raw is unparsable, and a literal
    # "0" is itself invalid (< 1), so both bad shapes land in the one warning below.
    value = get_env_int("DOC_LLM_CONCURRENCY", 0)
    if value < 1:
        logger.warning(
            "internal_document.config.concurrency_invalid", value=raw, fallback=1
        )
        return 1
    return value


@dataclass
class Config:
    dest_file: str = '/poc_internal_model_migration/documentfiles_internal_output'
    output_file_name: str = 'model_comparison.xlsx'

    # Prompt configuration (Local file) -- a byte-identical copy of the google pipeline's
    # prompt, so the two runs stay on the same independent variable.
    system_prompt_file: str = 'src/local_model/documents/prompt/system_prompt_v2.md'

    # GCS configuration
    gcs_bucket: str = 'internal-model-poc'
    gcs_src_path: str = 'poc_internal_model/tax_invoice/internal/src_files'
    gcs_processing_path: str = 'poc_internal_model/tax_invoice/internal/processing_files'
    gcs_dest_path: str = 'poc_internal_model/tax_invoice/internal/dest_files'

    # Endpoint configuration
    model_name: str = "qwen3.8-27b-fp8" # 'gemma-4-12b-it'
    base_url: str = "https://10.94.154.102/v1" # "https://token-fac-api.truecorp.co.th/v1"
    # Streaming makes this an inter-chunk idle timeout rather than a whole-request
    # budget: httpx applies its read timeout per read on the response body.
    llm_timeout: float = 600.0
    # The gateway's own timeout multiplies with the SDK's retries and the seed-nudge
    # loop below (1 -> 2 HTTP attempts x max_attempts). At 2 a dead backend cost ~45
    # minutes of wall clock per page before the run gave up on it.
    llm_max_retries: int = 0
    # Parallel extract_page calls per chunk. Only the LLM call fans out -- GCS I/O
    # and checkpointing stay sequential on the main thread. No upper clamp: the
    # endpoint's own rate limits are the real ceiling.
    llm_concurrency: int = field(default_factory=_doc_llm_concurrency)
    # Wall clock a single stream may run *after its first event arrives* before it
    # is abandoned. Counted from the first streamed event, not from the request:
    # under load this endpoint queues requests for minutes (ttft median 213s on
    # 2026-08-18), and counting that wait against the budget killed 46 healthy
    # streams in one run. Queue wait is bounded separately by the client's read
    # timeout, which only resets once bytes flow.
    llm_stream_deadline: float = 900.0
    # Ask for the final usage-bearing chunk. Set False only if the endpoint rejects
    # stream_options outright -- the cost is blank token cells for the whole run.
    stream_include_usage: bool = True

    # Set to a previous run's id (the timestamp folder name) to resume it: pages with a
    # dest JSON are reused outright, pages already rendered to the processing prefix skip
    # re-render/re-upload, and pages that failed are extracted again. None starts a fresh
    # timestamped run. main.py's menu overrides this per invocation, so it stays None
    # here: a checked-in value silently resumes someone else's folder on every direct
    # run() call.
    run_id: str | None = None # "2026-08-20_09-30-43"

    # 200 DPI keeps an A4 page ~3.9 MP -- crisp enough for Thai diacritics, far under the
    # endpoint's 40 MP / 8 MB decoded caps. pypdfium2 renders at scale = dpi / 72.
    render_dpi: int = 200

    # A dict cannot be a bare dataclass default -- it must go through default_factory.
    generation_config: dict[str, Any] = field(
        default_factory=lambda: {
            # Extraction needs near-greedy decoding: at temperature 1 gemma produced
            # repetition loops and off-domain text on this endpoint (see the sentiment
            # pipeline), and a scored extraction should not sample.
            "temperature": 0.0,
            "top_p": 1.0,
            # A ceiling, not a size expectation: a full ReceiptExtraction JSON runs
            # only ~1-3k tokens even on a line-item-heavy page (2026-08-18 run:
            # max 6,795). A repetition loop is bounded by tokens, not only by the
            # wall-clock stream deadline.
            "max_tokens": 20000,
            "seed": 0,
        }
    )
    max_attempts: int = 2

    # Output XLSX configuration
    gt_src_file: str = '/poc_internal_model_migration/AI Benchmark Report.xlsx'
    gt_sheet_name: str = 'Doc - Groundtruth'
    output_sheet_name: str = 'Doc - Internal Model Result'
    metrics_sheet_name: str = 'Matrix'
    metrics_summary_sheet_name: str = 'Matrix Summary'


def _stream_options(config: Config) -> dict[str, Any]:
    """``stream_options`` for a streaming call, splatted so it can be absent entirely.

    Gated on config because a strict OpenAI-compatible server can reject an unknown
    top-level field with a 400. Asking for it matters: the SDK overwrites the snapshot's
    usage from every chunk, so with no usage-bearing final chunk ``res.usage`` is None
    and every token cell on the sheet blanks out.
    """
    if not config.stream_include_usage:
        return {}
    return {"stream_options": {"include_usage": True}}


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
            "internal_document.dataframe.duplicate_submitted", count=counts["duplicates"]
        )
    if counts["missing"]:
        # These pages land in the sheet as blank rows; counts only -- a page name
        # carries the source file name, and this log is indexed by Cloud Logging.
        logger.warning(
            "internal_document.dataframe.blank_rows",
            submitted=len(submitted),
            missing=counts["missing"],
        )
    if counts["extra"]:
        logger.warning("internal_document.dataframe.unsubmitted_rows", count=counts["extra"])

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
      submitted: Every page name the run extracted from. Defines the page set.
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
        logger.warning("internal_document.dataframe.duplicate_parsed", count=collisions)

    rows: list[dict[str, Any]] = []
    keys: list[str] = []
    for page in _row_order(submitted, by_page):
        item = by_page.get(page)
        if item is None:
            # A page that never parsed. Everything the model would have extracted stays null;
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
        logger.warning("internal_document.dataframe.duplicate_keys", count=duplicates)

    logger.info(
        "internal_document.dataframe.built",
        pages=len(keys) and len({key.rsplit("#", 1)[0] for key in keys}),
        rows=len(output_df),
        columns=len(output_df.columns),
        submitted=len(submitted),
        parsed=len(by_page),
    )
    return output_df


def build_metrics_df(
    metrics_rows: list[dict[str, Any]],
    submitted: list[str],
    processing_metadata: dict[str, str],
) -> pd.DataFrame:
    """Flatten per-page usage records into the Matrix sheet.

    Seeded from the same submitted list as :func:`build_output_df`, so both sheets
    cover the same pages. Failed rows are included -- they still spent their tokens;
    a page absent from ``metrics_rows`` falls back to :data:`_METRICS_DEFAULTS`.
    ``No`` deliberately disagrees with the output sheet's ``No`` (that sheet is one
    row per line item); join on ``Page Name``.

    Args:
      metrics_rows: ``{"unique_name", **metrics-keys}`` records shaped like
        :data:`_METRICS_DEFAULTS`.
      submitted: Every page name the run extracted from. Defines the row set.
      processing_metadata: ``split_doc``'s ``{unique_name: file_name}`` mapping.

    Returns:
      A DataFrame with exactly :class:`MetricsSchema`'s columns, in declaration
      order, coerced and validated, and ``len(submitted)`` rows.
    """
    metrics_columns = list(MetricsSchema.to_schema().columns)
    by_page = {row["unique_name"]: row for row in metrics_rows}

    records = []
    for no, page in enumerate(_row_order(submitted, by_page), start=1):
        row = {**_METRICS_DEFAULTS, **by_page.get(page, {})}
        records.append(
            {
                "No": no,
                "Page Name": page,
                "File name": processing_metadata.get(page),
                "Status": row["status"],
                "Error Type": row["error_type"],
                "Attempts": row["attempts"],
                "Prompt Tokens": row["prompt_tokens"],
                "Completion Tokens": row["completion_tokens"],
                "Total Tokens": row["total_tokens"],
            }
        )

    metrics_df = MetricsSchema.validate(pd.DataFrame(records, columns=metrics_columns))
    logger.info(
        "internal_document.metrics.built",
        rows=len(metrics_df),
        columns=len(metrics_df.columns),
    )
    return metrics_df


def build_summary_df(
    *,
    run_id: str,
    model: str,
    resumed: bool,
    counts: dict[str, int],
    metrics_df: pd.DataFrame,
    latency: dict[str, float],
) -> pd.DataFrame:
    """Render the run-level block: two columns, ``Metric`` and ``Value``.

    Shaped like the google pipeline's summary sheet so the two workbooks read side
    by side; the content is this pipeline's OpenAI token totals and local timings.

    Args:
        counts: ``{"source_files", "pages", "resumed_pages", "extracted_pages"}``.
        metrics_df: :func:`build_metrics_df`'s frame -- the same numbers the Matrix
            sheet shows, so the two cannot disagree.

    Returns:
        A two-column DataFrame; None values stay None so cells render blank.
    """
    pages = len(metrics_df)
    succeeded = int((metrics_df["Status"] == STATUS_SUCCESS).sum())
    with_usage = int(metrics_df["Total Tokens"].notna().sum())
    token_columns = ("Prompt Tokens", "Completion Tokens", "Total Tokens")
    # int() rather than the pandas scalar: neither openpyxl nor structlog should
    # meet a numpy type.
    totals = {column: int(metrics_df[column].sum()) for column in token_columns}

    rows: list[tuple[str, Any]] = [
        ("Run ID", run_id),
        ("Model", model),
        ("Resumed", resumed),
        ("Source Files", counts["source_files"]),
        ("Pages", counts["pages"]),
        ("Resumed Pages", counts["resumed_pages"]),
        ("Extracted Pages", counts["extracted_pages"]),
        ("Succeeded", succeeded),
        ("Failed", pages - succeeded),
        ("Success Rate", round(succeeded / pages, 4) if pages else 0.0),
        ("Pages With Usage", with_usage),
        *totals.items(),
        (
            "Avg Total Tokens / Page",
            round(totals["Total Tokens"] / with_usage, 1) if with_usage else 0.0,
        ),
        ("Files Elapsed (ms)", latency["files_ms"]),
        ("Results Elapsed (ms)", latency["results_ms"]),
    ]
    return pd.DataFrame(rows, columns=["Metric", "Value"])


def split_doc(
    files: list[str],
    gcs_client: GCSModule,
    bucket_name: str,
    processing_prefix: str,
    dpi: int,
    existing: frozenset[str] = frozenset(),
) -> dict[str, str]:
    """Split each source document into one GCS image object per page.

    The endpoint takes one image per request and no PDFs, so a multi-page PDF becomes
    one *rendered* PNG per page -- unlike the google pipeline there is no text layer
    to preserve. The uploaded pages double as the run's audit trail (exactly the
    bytes the model saw) and as the resume cache: a page already under the processing
    prefix (``existing``) keeps its metadata row but skips the render and upload.

    Args:
      files: Object names under the source prefix.
      gcs_client: The GCS client to read the sources and write the pages with.
      bucket_name: The bucket holding both.
      processing_prefix: Blob prefix (no ``gs://``) the page images are written under.
      dpi: Render resolution for PDF pages; pypdfium2 renders at ``scale = dpi / 72``.
      existing: Page names already present under ``processing_prefix`` (resume).

    Returns:
      ``{unique_name: file_name}`` (``<stem>_p{n}.png`` / ``<stem>_p1<ext>`` ->
      source document), in page order -- what lets ``build_output_df`` report a page
      under its source document. Two sources sharing a stem would collide here; a
      collision beats two pages silently claiming to be the same one.
    """
    import pypdfium2 as pdfium

    started = time.monotonic()
    metadata: dict[str, str] = {}
    skipped = 0
    reused = 0

    stems = [Path(file).stem for file in files]
    if duplicate_stems := len(stems) - len(set(stems)):
        logger.warning("internal_document.split.duplicate_stems", count=duplicate_stems)

    for file in files:
        file_name = Path(file).name
        ext = Path(file).suffix.lower()
        stem = Path(file).stem

        if ext != ".pdf" and ext not in SPLIT_IMAGE_EXTENSIONS:
            # Silently dropping it would leave a source file with no page, no metadata row and
            # no record of either -- the run would just come back short.
            skipped += 1
            logger.warning("internal_document.split.file_skipped", file=file_name, ext=ext)
            continue

        pages = 0
        try:
            if ext == ".pdf":
                content = gcs_client.download_file(bucket_name=bucket_name, file_path=file)
                doc = pdfium.PdfDocument(content)
                try:
                    for page_idx in range(len(doc)):
                        unique_name = f"{stem}_p{page_idx + 1}.png"
                        if unique_name in existing:
                            metadata[unique_name] = file_name
                            reused += 1
                            continue

                        # Rendered, not re-saved: the endpoint reads pixels only. to_pil()
                        # hands the bitmap to Pillow, which owns the PNG encode.
                        bitmap = doc[page_idx].render(scale=dpi / 72)
                        with io.BytesIO() as buf:
                            bitmap.to_pil().save(buf, format="PNG")
                            page_content = buf.getvalue()

                        gcs_client.upload_file(
                            bucket_name=bucket_name,
                            upload_path=f"{processing_prefix}/{unique_name}",
                            content=page_content,
                            mime_type=_DATA_URL_MIME[".png"],
                        )
                        metadata[unique_name] = file_name
                        pages += 1
                finally:
                    # Once, after every page. Closing inside the loop invalidates the handle
                    # the next iteration renders from.
                    doc.close()
            else:
                # An image is already a single page; it only needs the same _p1 naming, so a
                # unique_name has one shape whatever the source was.
                unique_name = f"{stem}_p1{ext}"
                if unique_name in existing:
                    metadata[unique_name] = file_name
                    reused += 1
                else:
                    content = gcs_client.download_file(
                        bucket_name=bucket_name, file_path=file
                    )
                    gcs_client.upload_file(
                        bucket_name=bucket_name,
                        upload_path=f"{processing_prefix}/{unique_name}",
                        content=content,
                        mime_type=_DATA_URL_MIME[ext],
                    )
                    metadata[unique_name] = file_name
                    pages = 1
        except pdfium.PdfiumError:
            # One corrupt PDF must not kill the run. Pages already recorded for this
            # file stay recorded -- they rendered fine.
            skipped += 1
            logger.warning(
                "internal_document.split.file_failed",
                file=file_name,
                exc_info=True,
            )
            continue

        logger.debug("internal_document.split.file_completed", file=file_name, pages=pages)

    logger.info(
        "internal_document.split.summary",
        source_files=len(files),
        pages=len(metadata),
        skipped=skipped,
        reused=reused,
        elapsed_ms=_elapsed_ms(started),
    )
    return metadata


def _image_limit_error(image_bytes: bytes) -> str | None:
    """Name the endpoint limit an image would trip, or None when it fits.

    Checked before the call so an oversized page costs nothing -- the server's 413
    would come only after the base64 body uploaded. Pillow's ``open`` reads only the
    header for ``size``, so the pixel check is cheap.
    """
    if len(image_bytes) > _MAX_DECODED_IMAGE_BYTES:
        return "image_bytes_over_8mb"

    from PIL import Image

    with Image.open(io.BytesIO(image_bytes)) as image:
        width, height = image.size
    if width * height > _MAX_IMAGE_PIXELS:
        return "image_area_over_40mp"
    return None


def extract_page(
    client_llm: OpenAI,
    image_bytes: bytes,
    unique_name: str,
    config: Config,
    system_prompt: str,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Extract one page's :class:`ReceiptExtraction`, retrying with a seed nudge.

    Streams rather than calling ``.parse()``, which is a transport decision, not a
    consumption one -- nothing here wants the deltas. A non-streaming request sends no
    bytes for the whole generation, and the gateway in front of this endpoint answered
    504 at ~300s on pages the backend was still working on. Streaming keeps bytes moving
    so an idle-based cutoff never fires. ``get_final_completion()`` returns the same
    ``ParsedChatCompletion`` ``.parse()`` would, so everything downstream is unchanged.

    Never raises for a failed page: ``(None, metrics)`` with ``error_type`` naming the
    exception class costs the run one blank row instead of the whole sheet.

    Returns:
        ``(content_dump, metrics)`` -- ``content_dump`` a
        ``ReceiptExtraction.model_dump(mode="json")`` on success, None on failure. A
        page failing every attempt reports token cells blank; the SDK's parse
        exceptions do not reliably carry usage.
    """
    mime_type = _DATA_URL_MIME[Path(unique_name).suffix.lower()]
    encoded = base64.b64encode(image_bytes).decode("ascii")
    res = None
    parsed: ReceiptExtraction | None = None
    last_error: str | None = None
    attempts = 0
    for attempt in range(1, config.max_attempts + 1):
        attempts = attempt
        # Near-greedy decoding reproduces the same failure on an identical retry, so
        # only the seed is nudged -- tie-breaking changes, decoding stays greedy.
        generation_config = (
            config.generation_config
            if attempt == 1
            else {**config.generation_config, "seed": attempt - 1}
        )
        started = time.monotonic()
        ttft_ms: float | None = None
        try:
            with client_llm.chat.completions.stream(
                model=config.model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime_type};base64,{encoded}"
                            },
                        }
                    ]}
                ],
                response_format=ReceiptExtraction,
                **generation_config,
                **_stream_options(config),
            ) as stream:
                # Armed by the first event, not the request: queue wait must not
                # spend the generation budget (it killed 46 healthy streams in the
                # 2026-08-18 run), and it is already bounded by the client's read
                # timeout.
                deadline: float | None = None
                # A drain, not a consumer: the SDK accumulates the deltas, and a
                # partially parsed ReceiptExtraction is no use to this function.
                for _ in stream:
                    if ttft_ms is None:
                        ttft_ms = _elapsed_ms(started)
                        deadline = time.monotonic() + config.llm_stream_deadline
                    if time.monotonic() > deadline:
                        # Inside the with, so the manager still closes the response
                        # and releases the connection on the way out.
                        raise StreamDeadlineError(
                            f"stream exceeded {config.llm_stream_deadline:.0f}s"
                        )
                res = stream.get_final_completion()
            parsed = res.choices[0].message.parsed
            if parsed is None:
                # refusal, content filter, or truncated JSON -- nothing to validate
                raise ValueError(
                    f"no parsed content, refusal={res.choices[0].message.refusal!r}"
                )
            # Separates a queued request from a slow one: a large ttft_ms with a small
            # stream_ms means the backend sat in a queue, which no client change fixes.
            logger.info(
                "internal_document.page.stream",
                page=unique_name,
                attempt=attempt,
                ttft_ms=ttft_ms,
                stream_ms=_elapsed_ms(started),
            )
            break
        except (
            ValidationError,
            ValueError,
            LengthFinishReasonError,
            ContentFilterFinishReasonError,
            APIError,
            # Raw transport faults (RemoteProtocolError, ReadError, ...) surface
            # unwrapped from the SSE drain -- the SDK only wraps them into
            # APIConnectionError on the initial request, not mid-stream.
            httpx.HTTPError,
        ) as e:
            # The parsed-is-None ValueError fires after `res` was assigned; without
            # the reset the stale response would be recorded as a success below.
            res = None
            last_error = type(e).__name__
            usage = getattr(getattr(e, "completion", None), "usage", None)
            logger.warning(
                "internal_document.page.retry",
                page=unique_name,
                attempt=attempt,
                error_type=last_error,
                completion_tokens=getattr(usage, "completion_tokens", None),
                ttft_ms=ttft_ms,
                stream_ms=_elapsed_ms(started),
            )
            if attempt < config.max_attempts:
                time.sleep(_backoff_s(attempt))
    # The base64 payload is ~1.3x the image; drop it before the caller moves on rather than
    # letting the copies pile up across pages.
    del encoded

    if res is None or parsed is None:
        logger.error(
            "internal_document.page.failed",
            page=unique_name,
            attempts=attempts,
            error_type=last_error,
        )
        return None, {
            **_METRICS_DEFAULTS,
            "error_type": last_error or "",
            "attempts": attempts,
        }

    usage = res.usage
    return parsed.model_dump(mode="json"), {
        "status": STATUS_SUCCESS,
        "error_type": "",
        "attempts": attempts,
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
    }


@dataclass
class _PendingPage:
    """A downloaded, limit-checked page waiting for its ``extract_page`` call.

    ``ctx`` is a per-page contextvars snapshot taken on the main thread inside the
    run's TracedOperation blocks -- pool threads start with an empty context, so
    without it every worker log record would silently lose ``run_id``/``model``
    and its OTel span parent. One snapshot per page, never shared: a ``Context``
    cannot be entered twice concurrently.
    """

    unique_name: str
    page_stem: str
    source_name: str | None
    image_bytes: bytes
    ctx: contextvars.Context


def _extract_page_guarded(
    client_llm: OpenAI,
    image_bytes: bytes,
    unique_name: str,
    config: Config,
    system_prompt: str,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """:func:`extract_page` plus the page loop's ``Exception`` backstop.

    ``extract_page`` never raises for a model fault; this catches everything else
    so one page's surprise costs the run a Failed row, not the whole sheet. It
    runs inside the worker under the submitter's copied context, so the warning
    here still carries ``run_id``.
    """
    try:
        return extract_page(client_llm, image_bytes, unique_name, config, system_prompt)
    except Exception as e:
        logger.warning(
            "internal_document.page.failed",
            page=unique_name,
            error_type=type(e).__name__,
            exc_info=True,
        )
        return None, {**_METRICS_DEFAULTS, "error_type": type(e).__name__}


def run(config: Config | None = None):
    tz = "Asia/Bangkok"
    run_dt = datetime.now(ZoneInfo(tz))
    config = config if config is not None else Config()
    # Colons are forbidden in SharePoint item names and are Graph's own path
    # delimiter; one string serves the log's run_id, the GCS prefixes and the
    # SharePoint folder.
    run_id = config.run_id or run_dt.strftime("%Y-%m-%d_%H-%M-%S")
    resuming = config.run_id is not None

    processing_prefix = f"{config.gcs_processing_path}/{run_id}"
    dest_prefix = f"{config.gcs_dest_path}/{run_id}"
    dest_gcs_uri = f"gs://{config.gcs_bucket}/{dest_prefix}"

    # run_id / model are bound as contextvars for the whole block, so every record the hook
    # modules emit underneath carries them without being passed through.
    with TracedOperation(
        "internal_document.run",
        run_id=run_id,
        model=config.model_name,
    ):
        # Every destination this run writes to, recorded before any of them exist -- so a
        # run never leaves its output somewhere unrecorded, even if preempted.
        logger.info(
            "internal_document.run.starting",
            run_id=run_id,
            resuming=resuming,
            timezone=tz,
            bucket=config.gcs_bucket,
            src_prefix=config.gcs_src_path,
            processing_prefix=processing_prefix,
            dest_uri=dest_gcs_uri,
            sharepoint_dest=config.dest_file,
            model=config.model_name,
            concurrency=config.llm_concurrency,
        )

        client_gcs = GCSModule(
            project_id=os.environ["GCP_PROJECT_ID"],
            timezone=tz,
        )
        # Owns the XLSX upload at the end of the run. The constructor acquires a token
        # eagerly, so bad config fails before hours of LLM calls are paid for.
        client_sb = SharePointModule(
            client_id=os.environ["SANDBOX_CLIENT_ID"],
            client_secret=os.environ["SANDBOX_CLIENT_SECRET"],
            tenant_id=os.environ["SANDBOX_TENANT_ID"],
            site_domain=os.environ["SANDBOX_SITE_DOMAIN"],
            site_path=os.environ["SANDBOX_SITE_PATH"],
            timezone=tz,
        )

        client_llm = OpenAI(
            api_key=os.environ["API_KEY"],
            base_url=config.base_url,
            # timeout=config.llm_timeout,
            # The SDK's retries sit *inside* extract_page's seed-nudge loop, so the two
            # multiply; the default 2 made one dead page cost 9 requests.
            max_retries=config.llm_max_retries,
            http_client=httpx.Client(verify=False),
        )

        # Ground truth read before any LLM spend -- a renamed tab or dropped column must
        # fail here, not after the whole page loop.
        with io.BytesIO(client_sb.download_file(config.gt_src_file).content) as f:
            book = pd.ExcelFile(f, engine="openpyxl")
            if config.gt_sheet_name not in book.sheet_names:
                logger.error(
                    "internal_document.run.aborted",
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
                "internal_document.run.aborted",
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
            "internal_document.gt.loaded",
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
                "internal_document.run.aborted",
                reason="no_source_files",
                bucket=config.gcs_bucket,
                prefix=config.gcs_src_path,
            )
            return

        logger.info(
            "internal_document.source.listed",
            bucket=config.gcs_bucket,
            prefix=config.gcs_src_path,
            count=len(src_files),
        )

        with open(config.system_prompt_file, 'r', encoding='utf-8') as f:
            system_prompt = f.read()

        logger.debug(
            "internal_document.prompts.loaded",
            system_prompt_chars=len(system_prompt),
        )

        # Resume state, listed once -- per-page existence checks would cost one
        # round-trip per page.
        done_by_stem: dict[str, str] = {}
        existing_pages: frozenset[str] = frozenset()
        if resuming:
            done_by_stem = {
                Path(obj).stem: obj
                for obj in client_gcs.list_files(
                    bucket_name=config.gcs_bucket, prefix=dest_prefix, pattern=r"\.json$"
                )
            }
            existing_pages = frozenset(
                Path(obj).name
                for obj in client_gcs.list_files(
                    bucket_name=config.gcs_bucket,
                    prefix=processing_prefix,
                    recursive=True,
                )
            )
            logger.info(
                "internal_document.resume.plan",
                source_files=len(src_files),
                done=len(done_by_stem),
                rendered=len(existing_pages),
            )

        with TracedOperation("internal_document.split"):
            processing_metadata = split_doc(
                files=src_files,
                gcs_client=client_gcs,
                bucket_name=config.gcs_bucket,
                processing_prefix=processing_prefix,
                dpi=config.render_dpi,
                existing=existing_pages,
            )

        # What was actually written, not what split_doc says it wrote -- the two are
        # logged side by side precisely because they can disagree.
        pages = client_gcs.list_files(
            bucket_name=config.gcs_bucket,
            prefix=processing_prefix,
            recursive=True,
        )

        logger.info(
            "internal_document.processing.listed",
            bucket=config.gcs_bucket,
            prefix=processing_prefix,
            objects=len(pages),
            metadata_rows=len(processing_metadata),
        )

        if not pages or len(processing_metadata) == 0:
            logger.error(
                "internal_document.run.aborted",
                reason="no_processing_files",
                bucket=config.gcs_bucket,
                prefix=processing_prefix,
            )
            return

        # The submitted set, and both sheets' page set: every object under this run's
        # processing prefix, i.e. every page the loop below will attempt.
        submitted = [Path(page).name for page in pages]

        items: list[dict[str, Any]] = []
        metrics_rows: list[dict[str, Any]] = []
        resumed_pages = 0
        extracted_pages = 0
        unmatched_pages = 0

        pages_started = time.monotonic()
        with TracedOperation("internal_document.pages"):
            # At most llm_concurrency pages (and their image payloads) are in
            # flight at once: only the LLM calls fan out, everything else stays on
            # this thread. Insertion order is submission order, which settle()
            # uses to drain simultaneous completions deterministically.
            in_flight: dict[Future, _PendingPage] = {}

            def run_task(
                pending: _PendingPage,
            ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
                # ctx.run restores run_id/model/operation and OTel span parenting
                # inside the pool thread; the Exception backstop lives in
                # _extract_page_guarded, so a future's result() can never raise.
                return pending.ctx.run(
                    _extract_page_guarded,
                    client_llm,
                    pending.image_bytes,
                    pending.unique_name,
                    config,
                    system_prompt,
                )

            def settle(finished: set[Future]) -> None:
                # (d) Runs on the main thread as soon as *one* call returns --
                # unlike the previous fixed chunk, a slow page no longer holds the
                # freed slots idle, and a settled page checkpoints immediately, so
                # a crash loses at most the still-running calls -- a resume simply
                # re-extracts them.
                nonlocal extracted_pages
                order = {future: index for index, future in enumerate(in_flight)}
                for future in sorted(finished, key=order.__getitem__):
                    pending = in_flight.pop(future)
                    content_dump, page_metrics = future.result()
                    if content_dump is not None:
                        # (e) Checkpoint before the in-memory append, so a preemption
                        # never loses a settled page. Self-contained -- everything a
                        # resume needs.
                        client_gcs.upload_file(
                            bucket_name=config.gcs_bucket,
                            upload_path=f"{dest_prefix}/{pending.page_stem}.json",
                            content=json.dumps(
                                {
                                    "unique_name": pending.unique_name,
                                    "file_name": pending.source_name,
                                    "content": content_dump,
                                    "metrics": page_metrics,
                                },
                                ensure_ascii=False,
                            ).encode("utf-8"),
                            mime_type="application/json",
                        )
                        items.append(
                            {
                                "unique_name": pending.unique_name,
                                "file_name": pending.source_name,
                                "content": content_dump,
                            }
                        )
                        extracted_pages += 1
                        logger.info(
                            "internal_document.page.extracted",
                            page=pending.unique_name,
                            attempts=page_metrics["attempts"],
                            prompt_tokens=page_metrics["prompt_tokens"],
                            completion_tokens=page_metrics["completion_tokens"],
                        )

                    # (f) Either way the page keeps its Matrix row. No dest JSON is
                    # written for a failure, so a resume retries exactly those pages.
                    metrics_rows.append(
                        {"unique_name": pending.unique_name, **page_metrics}
                    )
                    # in_flight.pop dropped the _PendingPage -- and with it the
                    # image_bytes reference -- as each future settled.

            # max_workers=1 is today's sequential run through the same code path:
            # one download, one submitted call, one wait, one checkpoint.
            with ThreadPoolExecutor(max_workers=config.llm_concurrency) as pool:
                for page in pages:
                    unique_name = Path(page).name
                    page_stem = Path(page).stem

                    source_name = processing_metadata.get(unique_name)
                    if source_name is None:
                        # A page the split never recorded. It still gets its row; the
                        # count is logged after the loop.
                        unmatched_pages += 1

                    # (a) Fully done in a previous run: reuse the stored result.
                    # Never pooled -- no LLM call to overlap.
                    if page_stem in done_by_stem:
                        stored = json.loads(
                            client_gcs.download_file(
                                bucket_name=config.gcs_bucket,
                                file_path=done_by_stem[page_stem],
                            )
                        )
                        # Re-validated rather than trusted: the stored dump is the resume
                        # contract, and drift should fail loudly here.
                        content = ReceiptExtraction.model_validate(stored["content"])
                        items.append(
                            {
                                "unique_name": unique_name,
                                "file_name": stored["file_name"],
                                "content": content.model_dump(mode="json"),
                            }
                        )
                        metrics_rows.append(
                            {"unique_name": unique_name, **stored["metrics"]}
                        )
                        resumed_pages += 1
                        logger.info("internal_document.page.resumed", page=unique_name)
                        continue

                    # (b) The audit copy under the processing prefix is what the model
                    # sees -- exactly the bytes split_doc wrote.
                    image_bytes = client_gcs.download_file(
                        bucket_name=config.gcs_bucket,
                        file_path=page,
                    )

                    # (c) An image the endpoint would reject costs nothing: no call, one
                    # Failed row naming the limit. Never pooled.
                    limit_error = _image_limit_error(image_bytes)
                    if limit_error:
                        metrics_rows.append(
                            {
                                "unique_name": unique_name,
                                **_METRICS_DEFAULTS,
                                "error_type": limit_error,
                            }
                        )
                        logger.warning(
                            "internal_document.page.oversized",
                            page=unique_name,
                            error_type=limit_error,
                            bytes=len(image_bytes),
                        )
                        del image_bytes
                        continue

                    # The context snapshot is per page, taken here so worker logs
                    # inherit everything bound by the surrounding TracedOperations.
                    pending_page = _PendingPage(
                        unique_name=unique_name,
                        page_stem=page_stem,
                        source_name=source_name,
                        image_bytes=image_bytes,
                        ctx=contextvars.copy_context(),
                    )
                    del image_bytes  # the _PendingPage now owns the only reference

                    # Backpressure: block until a slot frees, settling whichever
                    # call finished first -- not, as the old fixed chunk did,
                    # waiting for the whole batch's slowest member.
                    while len(in_flight) >= config.llm_concurrency:
                        finished, _ = wait(
                            tuple(in_flight), return_when=FIRST_COMPLETED
                        )
                        settle(finished)

                    in_flight[pool.submit(run_task, pending_page)] = pending_page

                while in_flight:
                    finished, _ = wait(tuple(in_flight), return_when=FIRST_COMPLETED)
                    settle(finished)

        if unmatched_pages:
            # Counts only; a page name is a file name.
            logger.warning(
                "internal_document.page.unmatched_pages",
                count=unmatched_pages,
            )

        files_ms = _elapsed_ms(pages_started)
        logger.info(
            "internal_document.pages.completed",
            source_files=len(src_files),
            pages=len(pages),
            resumed=resumed_pages,
            extracted=extracted_pages,
            elapsed_ms=files_ms,
        )

        with TracedOperation("internal_document.results"):
            results_started = time.monotonic()

            output_df = build_output_df(items, submitted, processing_metadata)
            metrics_df = build_metrics_df(metrics_rows, submitted, processing_metadata)
            summary_df = build_summary_df(
                run_id=run_id,
                model=config.model_name,
                resumed=resuming,
                counts={
                    "source_files": len(src_files),
                    "pages": len(pages),
                    "resumed_pages": resumed_pages,
                    "extracted_pages": extracted_pages,
                },
                metrics_df=metrics_df,
                latency={"files_ms": files_ms, "results_ms": _elapsed_ms(results_started)},
            )

            succeeded = int((metrics_df["Status"] == STATUS_SUCCESS).sum())
            # The one record that says what the run cost -- counts and tokens only.
            logger.info(
                "internal_document.usage.summary",
                pages=len(metrics_df),
                succeeded=succeeded,
                failed=len(metrics_df) - succeeded,
                prompt_tokens=int(metrics_df["Prompt Tokens"].sum()),
                completion_tokens=int(metrics_df["Completion Tokens"].sum()),
                total_tokens=int(metrics_df["Total Tokens"].sum()),
            )

            dest_file_path = f"{config.dest_file}/{run_id}"
            output_path = f"{dest_file_path}/{config.output_file_name}"

            # gt_df was read and validated before any LLM spend -- see the pre-flight above.
            with io.BytesIO() as output_buffer:
                with pd.ExcelWriter(output_buffer, engine='openpyxl') as writer:
                    gt_df.to_excel(writer, index=False, sheet_name=config.gt_sheet_name)
                    output_df.to_excel(writer, index=False, sheet_name=config.output_sheet_name)
                    # Written last, so the delivered workbook still opens on the ground truth
                    # and the ops sheets sit behind the result tabs.
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

            # Guarded: the results are in GCS and every call already paid for, so a
            # workbook fault (lock, 503, illegal path) must not take the run's record
            # with it. SharePointModule already logged the cause at its single ERROR site.
            output_uploaded = False
            try:
                client_sb.upload_file(upload_path=output_path, content=workbook_byte)
                output_uploaded = True
                # Both counts: `rows` is line items, `pages` is document pages -- the
                # two failure shapes are indistinguishable from `rows` alone.
                logger.info(
                    "internal_document.output.uploaded",
                    path=output_path,
                    rows=len(output_df),
                    pages=len(submitted),
                    bytes=len(workbook_byte),
                )
            except Exception:
                logger.warning(
                    "internal_document.output.skipped",
                    path=output_path,
                    rows=len(output_df),
                    pages=len(submitted),
                )
            del workbook_byte  # free the workbook copy once the upload is settled

        # Counts at every stage, not just the submitted page count: a run that lists 100
        # pages and writes 3 is a bad run, and "rows=100" alone reported it as a good one.
        logger.info(
            "internal_document.run.completed",
            run_id=run_id,
            source_files=len(src_files),
            pages=len(pages),
            resumed=resumed_pages,
            extracted=extracted_pages,
            # Both, because the grain differs: `written` is line-item rows and
            # `pages_written` is document pages.
            written=len(output_df),
            pages_written=len(submitted),
            succeeded=succeeded,
            failed=len(metrics_df) - succeeded,
            output_path=output_path,
            # So a partial run reads as partial in one record.
            output_uploaded=output_uploaded,
            total_tokens=int(metrics_df["Total Tokens"].sum()),
        )
