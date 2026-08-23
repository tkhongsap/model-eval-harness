import io
import json
import mimetypes
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from pandera.errors import SchemaError, SchemaErrors
from pydantic import ValidationError

from src.google_model.sentiment_mnp.schema.input_gt_schema import InputGTSchema
from src.google_model.sentiment_mnp.schema.metrics_schema import MetricsSchema
from src.google_model.sentiment_mnp.schema.model_response import ModelResponse, SchemaHelper
from src.google_model.sentiment_mnp.schema.output_schema import OutputSchema
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


def _elapsed_ms(started: float) -> float:
    """Milliseconds since a ``time.monotonic()`` reading, rounded for logging."""
    return round((time.monotonic() - started) * 1000, 1)

@dataclass
class Config:
    # Documentation only: run() reads its audio from ``gcs_src_path`` below -- the one
    # SharePoint listing that would have used this field is commented out. It records
    # which corpus the GCS objects came from, so a wrong-corpus run has a name to check
    # against. The QA folder was here by copy-paste, and this is not that corpus.
    src_file: str = '/poc_internal_model_migration/voicefiles_mnp'
    dest_file: str = '/poc_internal_model_migration/voicefiles_mnp_output'
    output_file_name: str = 'model_comparison.xlsx'

    # Prompt configuration (Local file). One prompt, not an inbound/outbound pair:
    # these source names carry no IN/OUT marker, so there is no direction to branch on.
    system_prompt_file: str = 'src/google_model/sentiment_mnp/prompt/system_prompt.txt'

    # GCS configuration
    gcs_bucket: str = 'internal-model-poc'
    gcs_src_path: str = 'poc_internal_model/sentiment_mnp/google/src_files'
    gcs_payload_path: str = 'poc_internal_model/sentiment_mnp/google/payload_files'
    gcs_dest_path: str = 'poc_internal_model/sentiment_mnp/google/dest_files'

    # Vertex AI configuration
    vertex_location: str = 'global'
    model_name: str = 'gemini-2.5-flash'

    # Bounded re-check window for a terminal-but-unreadable job. Vertex never moves a job
    # out of FAILED/CANCELLED/EXPIRED, so a genuinely failed job costs one extra timeout.
    readable_wait_timeout: float = 600.0
    readable_poll_interval: float = 60.0
    # A dict cannot be a bare dataclass default -- it must go through default_factory.
    # Production's own decoding settings for this use case, from config/model_setting/mnp_retention.yml. Deliberately not
    # this repo's earlier temperature 1 / topP 1: full-entropy sampling over a fixed label set
    # adds variance and buys nothing, and `seed` makes one run reproducible, not accurate. The
    # two use cases differ here because the two production systems differ -- do not unify them.
    generation_config: dict[str, Any] = field(
        default_factory=lambda: {
            "temperature": 0.1,
            "topP": 1,
            "maxOutputTokens": 65535,
            "seed": 0,
            "thinkingConfig": {
                "thinkingBudget": 0
            }
        }
    )

    # Output XLSX configuration
    gt_src_file: str = '/poc_internal_model_migration/AI Benchmark Report.xlsx'
    gt_sheet_name: str = 'Voice_mnp - Groundtruth'
    output_sheet_name: str = 'Voice_mnp - Google Result'
    metrics_sheet_name: str = 'Matrix'
    metrics_summary_sheet_name: str = 'Matrix Summary'

def _row_order(submitted: list[str], parsed: dict[str, Any]) -> list[str]:
    """Row order for both sheets, with every submitted/parsed disagreement logged.

    Thin wrapper over :func:`~src.google_model.usage_metrics.plan_row_order`; this
    adds the event namespace.
    """
    order, counts = plan_row_order(submitted, parsed)

    if counts["duplicates"]:
        # Cannot happen today (non-recursive listing keeps basenames unique), but a
        # silent collapse is exactly the row-count bug this function exists to remove.
        logger.warning(
            "google_sentiment_mnp.dataframe.duplicate_submitted", count=counts["duplicates"]
        )
    if counts["missing"]:
        # These rows land in the sheet as blanks; counts only -- a file name is
        # business data, and this log is indexed by Cloud Logging.
        logger.warning(
            "google_sentiment_mnp.dataframe.blank_rows",
            submitted=len(submitted),
            missing=counts["missing"],
        )
    if counts["extra"]:
        logger.warning("google_sentiment_mnp.dataframe.unsubmitted_rows", count=counts["extra"])

    return order


def _rank_reason(rank: dict[str, Any] | None) -> str:
    """The category of an optional ranked reason, as a sheet cell.

    ``Reasons.secondary`` and ``.third`` are nullable ``ReasonKeyword`` objects, so an
    absent rank is ``None`` rather than a dict with an empty ``reason``. Returns ``""``
    for that case, which is what the ground truth writes and what ``_clean`` folds
    together with its blanks.
    """
    return rank["reason"] if rank else ""


def build_output_df(items: list[dict[str, Any]], submitted: list[str]) -> pd.DataFrame:
    """Flatten parsed model responses into the output sheet's DataFrame.

    One row per *submitted* file: a file with no valid response gets a blank row --
    blank rather than a sentinel, because the scorers compare raw cell text and a
    blank is itself a real graded value here (the ground truth leaves
    ``reason_secondary`` and ``reason_third`` empty on most calls). Which files are
    blank, and why, is what the Matrix sheet is for.

    Args:
      items: ``{"file_name": str, "content": dict}`` rows, where ``content`` is a
        ``ModelResponse.model_dump(mode="json")``.
      submitted: Every file name the payload was built from. Defines the row set.

    Returns:
      A DataFrame with exactly :class:`OutputSchema`'s columns, in declaration
      order, coerced and validated, and ``len(submitted)`` rows.
    """
    # Declaration order, aliases included -- the sheet's column order is the schema's,
    # which is the ground-truth tab's. evaluate() joins the two sheets by identical
    # column name, so a column spelled differently here never reaches the scorer.
    output_columns = list(OutputSchema.to_schema().columns)

    by_name = {item["file_name"]: item for item in items}
    if collisions := len(items) - len(by_name):
        logger.warning("google_sentiment_mnp.dataframe.duplicate_parsed", count=collisions)

    rows = []
    for no, name in enumerate(_row_order(submitted, by_name), start=1):
        row: dict[str, Any] = {"No": no, "Voice File Name": name}
        item = by_name.get(name)
        if item is not None:
            content = item["content"]
            reasons = content["reasons"]
            # "" rather than None for an absent rank. Both work -- pandera coerces a
            # None to NaN, and _clean folds NaN, "" and the sheet's own blank to the
            # same value -- but "" keeps the column a real str for every parsed row,
            # so its dtype does not depend on whether any call had three reasons.
            values = {
                "call_sumary_call_result": content["call_result"],
                # Each rank is a {reason, keyword} object -- the sheet scores the
                # category, and the customer's quoted evidence stays in the shard.
                "reason_main": reasons["main"]["reason"],
                "reason_secondary": _rank_reason(reasons["secondary"]),
                "reason_third": _rank_reason(reasons["third"]),
            }
            # Indexed, not .get() -- a column with no source is a mapping bug, and
            # .get() would ship a silent all-null column to the sheet.
            row.update({col: values[col] for col in output_columns[2:]})
        rows.append(row)

    # The explicit columns= makes an empty `items` yield a valid 0-row frame and
    # fills a blank row's absent keys with NaN instead of raising.
    output_df = OutputSchema.validate(pd.DataFrame(rows, columns=output_columns))
    logger.info(
        "google_sentiment_mnp.dataframe.built",
        rows=len(output_df),
        columns=len(output_df.columns),
        submitted=len(submitted),
        parsed=len(by_name),
    )
    return output_df


def build_metrics_df(usage_rows: list[dict[str, Any]], submitted: list[str]) -> pd.DataFrame:
    """Flatten per-row usage records into the Matrix sheet.

    Seeded from the same submitted list as :func:`build_output_df`, so the two sheets
    share a row set. Failed rows are included -- they still burned the tokens Vertex
    charged for. A file Vertex never answered gets ``Status=Failed`` with blank token
    cells; its ``Error Type`` is blank, while a row that came back and failed to
    parse carries the exception class name.

    Args:
      usage_rows: ``{"file_name", "status", "error_type", **extract_usage(item)}``
        records, one per prediction row.
      submitted: Every file name the payload was built from. Defines the row set.

    Returns:
      A DataFrame with exactly :class:`MetricsSchema`'s columns, in declaration
      order, coerced and validated, and ``len(submitted)`` rows.
    """
    metrics_columns = list(MetricsSchema.to_schema().columns)
    by_name = {row["file_name"]: row for row in usage_rows}
    # A complete record so every field below can be indexed -- .get() would paper
    # over a wiring bug with a silent null column.
    absent = {"status": STATUS_FAILED, "error_type": "", **dict.fromkeys(USAGE_FIELDS)}

    records = []
    for no, name in enumerate(_row_order(submitted, by_name), start=1):
        row = by_name.get(name, absent)
        records.append(
            {
                "No": no,
                "Voice File Name": name,
                "Status": row["status"],
                "Error Type": row["error_type"],
                **{header: row[key] for key, header in USAGE_TOTALS.items()},
                "Prompt Modalities": row["prompt_modalities"],
                "Traffic Type": row["traffic_type"],
            }
        )

    metrics_df = MetricsSchema.validate(pd.DataFrame(records, columns=metrics_columns))
    logger.info(
        "google_sentiment_mnp.metrics.built",
        rows=len(metrics_df),
        columns=len(metrics_df.columns),
    )
    return metrics_df

def run():
    tz = "Asia/Bangkok"
    run_dt = datetime.now(ZoneInfo(tz))
    # Colons are forbidden in SharePoint item names and are Graph's own path delimiter,
    # so one safe form keeps the log's run_id, the GCS prefix and the SharePoint folder
    # the same string.
    run_dt_str = run_dt.strftime("%Y-%m-%d_%H-%M-%S")
    run_date = run_dt.strftime("%Y-%m-%d")
    config = Config()

    gcs_dest_file = config.gcs_dest_path + "/" + run_dt_str
    gcs_payload_file = config.gcs_payload_path + "/" + run_dt_str

    payload_gcs_uri = f"gs://{config.gcs_bucket}/{gcs_payload_file}/payload.jsonl"
    dest_gcs_uri = f"gs://{config.gcs_bucket}/{gcs_dest_file}"

    # run_id / model are bound as contextvars for the whole block, so every record the
    # hook modules emit underneath carries them without being passed through.
    with TracedOperation(
        "google_sentiment_mnp.run",
        run_id=run_dt_str,
        model=config.model_name,
    ):
        # Every destination this run writes to, recorded before any of them exist -- so a
        # run never leaves its output somewhere unrecorded, even if it dies mid-way.
        logger.info(
            "google_sentiment_mnp.run.starting",
            run_id=run_dt_str,
            timezone=tz,
            bucket=config.gcs_bucket,
            src_prefix=config.gcs_src_path,
            payload_uri=payload_gcs_uri,
            dest_uri=dest_gcs_uri,
            sharepoint_dest=config.dest_file,
            model=config.model_name,
        )

        client_gcs = GCSModule(
            project_id=os.environ["GCP_PROJECT_ID"],
            timezone=tz,
        )
        # Owns the ground-truth read and the XLSX upload. The constructor
        # acquires a token eagerly, so bad config fails before the batch job is paid for.
        client_sb = SharePointModule(
            client_id=os.environ["SANDBOX_CLIENT_ID"],
            client_secret=os.environ["SANDBOX_CLIENT_SECRET"],
            tenant_id=os.environ["SANDBOX_TENANT_ID"],
            site_domain=os.environ["SANDBOX_SITE_DOMAIN"],
            site_path=os.environ["SANDBOX_SITE_PATH"],
            timezone=tz,
        )

        # Read before the batch job: a renamed tab or dropped column must fail here,
        # not at the final write after a completed Vertex job.
        with io.BytesIO(client_sb.download_file(config.gt_src_file).content) as f:
            # pd.ExcelFile: sheet_names comes off the same parse the frame is read from.
            book = pd.ExcelFile(f, engine="openpyxl")
            if config.gt_sheet_name not in book.sheet_names:
                logger.error(
                    "google_sentiment_mnp.run.aborted",
                    reason="gt_sheet_missing",
                    file=config.gt_src_file,
                    sheet=config.gt_sheet_name,
                    # Sheet names only -- never cell content.
                    available=book.sheet_names,
                )
                return
            # pandas' default NA set contains 'N/A', 'NA', 'null' and 'None' -- all real
            # grader-entered values here, and gt_df ships back into the delivered
            # workbook. Keep only the empty cell as NaN so a typed 'N/A' survives.
            gt_df = book.parse(
                config.gt_sheet_name,
                dtype=str,
                header=0,
                keep_default_na=False,
                na_values=[""],
            )

        # strict=False admits extra columns but not missing ones, so this is a real gate on
        # the report's shape rather than a formality.
        try:
            gt_df = InputGTSchema.validate(gt_df)
        except (SchemaError, SchemaErrors) as e:
            # Computed here rather than read off the exception: e.data, e.failure_cases
            # and str(e) can all quote offending cell values.
            missing = sorted(set(InputGTSchema.to_schema().columns) - set(gt_df.columns))
            logger.error(
                "google_sentiment_mnp.run.aborted",
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
            "google_sentiment_mnp.gt.loaded",
            file=config.gt_src_file,
            sheet=config.gt_sheet_name,
            rows=len(gt_df),
        )

        # Sources are read straight from GCS. The SharePoint -> GCS transfer is kept
        # commented out (here and in the payload loop below) until that step exists again.
        # files = client_sb.list_files(config.src_file)

        # recursive=False: only immediate children of gcs_src_path. Objects nested under
        # a further prefix are deliberately not picked up.
        files = client_gcs.list_files(
            bucket_name=config.gcs_bucket,
            prefix=config.gcs_src_path,
        )

        # Abort rather than warn: an empty payload would fail minutes later at Vertex
        # with an error that never mentions the source prefix.
        if not files:
            logger.error(
                "google_sentiment_mnp.run.aborted",
                reason="no_source_files",
                bucket=config.gcs_bucket,
                prefix=config.gcs_src_path,
            )
            return

        # .replace is a no-op on a prompt with no placeholder; kept so a future
        # prompt can date itself without a code change.
        with open(config.system_prompt_file, 'r', encoding='utf-8') as f:
            system_prompt = f.read().replace("{date}", run_date)

        logger.debug(
            "google_sentiment_mnp.prompt.loaded",
            file=config.system_prompt_file,
            chars=len(system_prompt),
            run_date=run_date,
        )

        # Hoisted: identical for every row. Spread whole -- vertex_generation_config()
        # returns the entire generation_config block, not just the schema.
        response_config = SchemaHelper.vertex_generation_config()

        # Each phase gets its own span, so duration_ms is attributable per phase.
        with TracedOperation("google_sentiment_mnp.payload"):
            # Built as bytes directly: a list of row strings joined and encoded at the end
            # holds the payload ~3x at the join. Separator-first keeps the bytes identical.
            buffer = bytearray()
            payload_rows = 0
            started = time.monotonic()
            for file in files:
                file_name = Path(file).name
                gcs_uri = f"gs://{config.gcs_bucket}/{file}"

                # Sources are already in GCS; kept for when the SharePoint leg returns.
                # client_gcs.upload_file(
                #     bucket_name=config.gcs_bucket,
                #     upload_path=gcs_uri,
                #     content=client_sb.download_file(file).content
                # )
                mime_type, _ = mimetypes.guess_type(file_name)

                # A None mime_type serializes as JSON null and Vertex rejects the row
                # hours later; record the cause at submit time instead.
                if mime_type is None:
                    logger.warning("google_sentiment_mnp.mime.unresolved", file=file_name)

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

            # One summary for the whole loop -- per-file GCS calls are already logged.
            logger.info(
                "google_sentiment_mnp.payload.assembled",
                source_files=len(files),
                rows=payload_rows,
                bytes=len(jsonl_byte),
                elapsed_ms=payload_ms,
            )

        with TracedOperation("google_sentiment_mnp.batch"):
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
                display_name=f"google_{run_dt_str}",
            )

            # Recorded at INFO before the wait -- a run killed mid-poll otherwise loses
            # the handle for re-pulling results by hand.
            logger.info(
                "google_sentiment_mnp.batch.submitted",
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
                        "google_sentiment_mnp.batch.finished",
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
                    "google_sentiment_mnp.batch.unreadable",
                    job=job.name,
                    state=str(job.state),
                    retry_in=config.readable_poll_interval,
                    remaining=round(remaining, 1),
                )
                # min(...) so the last sleep cannot overshoot the deadline and turn a
                # 600s budget into 660s.
                time.sleep(min(config.readable_poll_interval, remaining))
                job = client_genai.pull_batch_job(job.name)

            # Bound for the same reason as payload_ms -- the summary sheet reports it.
            batch_ms = _elapsed_ms(batch_started)

            logger.info(
                "google_sentiment_mnp.batch.finished",
                job=job.name,
                state=str(job.state),
                elapsed_ms=batch_ms,
            )

            # Positional, and the BatchJob itself rather than its name: the object is
            # already terminal, so passing the name would only cost a redundant fetch.
            output_uri = client_genai.pull_batch_job_results(job)
            # A directory of JSONL shards, not one file. Reading a fixed
            # "predictions.jsonl" would silently drop rows on a sharded job.
            prediction_shards = client_gcs.list_files(
                bucket_name=config.gcs_bucket,
                prefix=output_uri,
                recursive=True,
                pattern=r"\.jsonl$",
            )

            # The hook logs the resolved URI at DEBUG only, so at INFO a run would
            # otherwise never record where it read its results from.
            logger.info(
                "google_sentiment_mnp.results.located",
                job=job.name,
                output_uri=output_uri,
                shards=len(prediction_shards),
            )

            if not prediction_shards:
                logger.error(
                    "google_sentiment_mnp.run.aborted",
                    reason="no_prediction_shards",
                    job=job.name,
                    output_uri=output_uri,
                )
                return
        with TracedOperation("google_sentiment_mnp.results"):
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
                                "google_sentiment_mnp.parse.line_empty",
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
                            # model's reading of the call and this log is indexed.
                            # Shard, position and size locate it by hand.
                            logger.warning(
                                "google_sentiment_mnp.parse.line_failed",
                                shard=shard_name,
                                line_no=line_no,
                                line_bytes=len(line),
                                error_type=type(e).__name__,
                                error_pos=e.pos,
                            )
                        except Exception as e:
                            shard_errors += 1
                            logger.warning(
                                "google_sentiment_mnp.parse.line_failed",
                                shard=shard_name,
                                line_no=line_no,
                                line_bytes=len(line),
                                error_type=type(e).__name__,
                                exc_info=True,
                            )

                total_lines += shard_lines
                line_errors += shard_errors
                logger.debug(
                    "google_sentiment_mnp.parse.shard",
                    shard=shard_name,
                    lines=shard_lines,
                    errors=shard_errors,
                )

            logger.info(
                "google_sentiment_mnp.parse.summary",
                shards=len(prediction_shards),
                total_lines=total_lines,
                parse_errors=line_errors,
                parsed_results=len(parsed_results),
                elapsed_ms=_elapsed_ms(parse_started),
            )

            items = []
            transcripts = []
            usage_rows = []
            item_errors = 0
            for item in parsed_results:
                # Bound before the try so a failure still names the file it belongs to.
                file_name = ""
                # Read before the try by a function that cannot raise: tokens burned are
                # a fact about the row whether or not its content parses.
                usage = extract_usage(item)
                status = STATUS_FAILED
                error_type = ""
                try:
                    # Guarded step by step: a row Vertex failed on carries "status", not
                    # "response", and a chained .get() would raise outside any handler.
                    request = item.get("request") or {}
                    contents = request.get("contents") or [{}]
                    req_parts = contents[0].get("parts") or [{}]
                    file_uri = (req_parts[0].get("file_data") or {}).get("file_uri", "")
                    file_name = Path(file_uri).name if file_uri else file_uri

                    response = item.get("response") or {}
                    candidates = response.get("candidates") or []
                    if not candidates:
                        raise ValueError("prediction row carries no candidates")
                    parts = (candidates[0].get("content") or {}).get("parts") or []
                    if not parts:
                        raise ValueError("prediction candidate carries no parts")
                    text = parts[0].get("text") or ""

                    parsed = ModelResponse.model_validate_json(text)
                    items.append({"file_name": file_name, "content": parsed.model_dump(mode="json")})
                    # Attribute access: ModelResponse is a pydantic model with no .get.
                    transcripts.append({"file_name": file_name, "transcript": parsed.transcript})
                    # Last statement in the try, so nothing above it can be skipped and still
                    # report success.
                    status = STATUS_SUCCESS
                except ValidationError as e:
                    item_errors += 1
                    error_type = type(e).__name__
                    # Field paths only: pydantic echoes the offending value into
                    # errors() and str(e), and a malformed row can hold anything.
                    logger.warning(
                        "google_sentiment_mnp.item.failed",
                        file=file_name,
                        error_type=error_type,
                        error_fields=[
                            ".".join(str(part) for part in err["loc"]) for err in e.errors()
                        ],
                    )
                except Exception as e:
                    item_errors += 1
                    error_type = type(e).__name__
                    logger.warning(
                        "google_sentiment_mnp.item.failed",
                        file=file_name,
                        error_type=error_type,
                        exc_info=True,
                    )

                # After the try/except, not a finally (which would also fire on
                # KeyboardInterrupt): every prediction row contributes exactly one
                # metrics row, failures included.
                usage_rows.append(
                    {
                        "file_name": file_name,
                        "status": status,
                        "error_type": error_type,
                        **usage,
                    }
                )

            logger.info(
                "google_sentiment_mnp.item.summary",
                parsed=len(items),
                failed=item_errors,
                transcripts=len(transcripts),
            )

            # The sheets' row set: one name per payload row, so 300 files in produce
            # 300 rows out however many of them the model failed on.
            submitted = [Path(file).name for file in files]

            output_df = build_output_df(items, submitted)
            metrics_df = build_metrics_df(usage_rows, submitted)
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
            # no file names, no transcript, no model output.
            logger.info(
                "google_sentiment_mnp.usage.summary",
                files=usage_summary["files"],
                succeeded=usage_summary["succeeded"],
                failed=usage_summary["failed"],
                files_with_usage=usage_summary["files_with_usage"],
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
                    # Written last, so the delivered workbook still opens on the ground
                    # truth and the ops sheets sit behind the result tabs.
                    summary_df.to_excel(
                        writer, index=False, sheet_name=config.metrics_summary_sheet_name
                    )
                    metrics_df.to_excel(writer, index=False, sheet_name=config.metrics_sheet_name)
                    # Metric names run long and the default width truncates them mid-word; the
                    # per-file sheet's own headers are already about as wide as its values.
                    summary_sheet = writer.sheets[config.metrics_summary_sheet_name]
                    summary_sheet.column_dimensions["A"].width = 32
                    summary_sheet.column_dimensions["B"].width = 26
                workbook_byte = output_buffer.getvalue()

            # Guarded: the predictions are already in GCS, so a workbook fault
            # (lock, 503, illegal path) must not take the run down with it.
            # SharePointModule already logged the cause at its single ERROR site.
            output_uploaded = False
            try:
                client_sb.upload_file(upload_path=output_path, content=workbook_byte)
                output_uploaded = True
                # The row count says whether the run produced anything.
                logger.info(
                    "google_sentiment_mnp.output.uploaded",
                    path=output_path,
                    rows=len(output_df),
                    bytes=len(workbook_byte),
                )
            except Exception:
                logger.warning(
                    "google_sentiment_mnp.output.skipped",
                    path=output_path,
                    rows=len(output_df),
                )
            del workbook_byte  # free the workbook before the per-transcript upload loop

            transcript_errors = 0
            for transcript in transcripts:
                # <stem>.txt, not <name>.txt -- every source name ends in .wav, which
                # would otherwise produce "a.wav.txt" and stop the scorer's stem join
                # from matching the human transcript beside it.
                transcript_path = f"{dest_file_path}/{Path(transcript['file_name']).stem}.txt"
                try:
                    # The transcript text itself, not json.dumps of the row -- the file is
                    # read by people, and the file name already carries the other key.
                    client_sb.upload_file(
                        upload_path=transcript_path,
                        content=transcript["transcript"].encode("utf-8"),
                    )
                except Exception:
                    transcript_errors += 1
                    # SharePointModule already logged the fault; this records only that
                    # the run carried on, so one locked path does not cost the set.
                    logger.warning(
                        "google_sentiment_mnp.transcript.skipped",
                        file=transcript["file_name"],
                    )

            logger.info(
                "google_sentiment_mnp.transcripts.uploaded",
                count=len(transcripts) - transcript_errors,
                failed=transcript_errors,
                directory=dest_file_path,
            )

        # Counts at every stage, not just the submitted row count: a run that submits 100
        # and writes 3 is a bad run, and "rows=100" alone reported it as a good one.
        logger.info(
            "google_sentiment_mnp.run.completed",
            run_id=run_dt_str,
            job=job.name,
            state=str(job.state),
            source_files=len(files),
            submitted=payload_rows,
            parsed=len(parsed_results),
            written=len(output_df),
            transcripts=len(transcripts) - transcript_errors,
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