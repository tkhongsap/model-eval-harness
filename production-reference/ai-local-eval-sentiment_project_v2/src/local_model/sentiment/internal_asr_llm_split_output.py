"""Sentiment pipeline variant: one analysis LLM call per topic, merged afterwards.

A copy of ``internal_asr_llm_output.py`` whose analysis stage no longer asks for the
whole :class:`ModelResponse` in one structured-output call. That single call was the
pipeline's wall-clock ceiling -- >10k completion tokens generated strictly
sequentially. Here the stage fans out one call per :data:`SECTIONS` entry (the six
``ModelResponse`` topics plus a small classification call for the top-level fields),
runs them in parallel, and merges the parsed pieces back through
``ModelResponse.model_validate`` -- so the dest-JSON checkpoints, resume contract and
all four sheets are byte-compatible with the unsplit pipeline's.

Two deliberate differences from the baseline, both chosen by the team:

- Each call's system prompt is *sliced at runtime*: the shared preamble plus only
  that topic's ``### **Category N.: `<field>`**`` section of the (unchanged) prompt
  files. The model no longer sees other categories' rules, so scores are comparable
  to the baseline only via a fresh side-by-side run.
- With ``SENTI_LLM_CONCURRENCY=N`` files in flight, in-flight LLM requests peak at
  ``N x len(SECTIONS)``; the endpoint's own rate limits remain the real ceiling.

Log events live under ``internal_sentiment_split.*`` so the two pipelines are
distinguishable in Cloud Logging, and GCS/SharePoint destinations use their own
``internal_split`` prefixes so a split run can never be confused with -- or
cross-resumed against -- a baseline run.
"""

import contextvars
import io
import json
import math
import os
import random
import re
import struct
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import httpx
import pandas as pd
from openai import APIError, ContentFilterFinishReasonError, LengthFinishReasonError, OpenAI
from pandera.errors import SchemaError, SchemaErrors
from pydantic import ValidationError

from src.google_model.sentiment.schema.input_gt_schema import InputGTSchema
from src.google_model.usage_metrics import STATUS_FAILED, STATUS_SUCCESS, plan_row_order
from src.hook.gcp_gcs import GCSModule
from src.hook.sharepoint import SharePointModule
from src.local_model.sentiment.schema.metrics_schema import MetricsSchema
from src.local_model.sentiment.schema.model_response import ModelResponse
from src.local_model.sentiment.schema.output_schema import OutputSchema
from src.local_model.sentiment.schema.split_model_response import (
    EXPECTED_PROMPT_SECTIONS,
    SECTIONS,
)
from src.logger import Logger, TracedOperation
from src.utils.common import get_env, get_env_int

if TYPE_CHECKING:
    import numpy as np

logger = Logger.get_logger(__name__)

# Fixed rather than mimetypes.guess_type: every source is a WAV (G.711 telephony
# or PCM), and guess_type consults the OS registry on Windows, which can misname
# it. The multipart filename and content type are how the ASR endpoint decides
# what it was handed.
_ASR_MIME = "audio/wav"

# RIFF fmt-chunk format tags this pipeline decodes itself. stdlib `wave` accepts
# only PCM (1) and EXTENSIBLE-PCM (0xFFFE); the telephony sources are G.711.
_WAVE_FORMAT_ALAW = 6
_WAVE_FORMAT_MULAW = 7

# Cap on the endpoint error detail logged per ASR attempt: the SDK puts the whole
# response text into APIError.message when the body is not JSON, and a proxy's
# 413/502 page is full HTML.
_ASR_ERROR_DETAIL_MAX = 300

# Deterministic rejections of the request itself -- resending identical bytes
# cannot change the answer, so the ASR retry loop breaks after the first one
# instead of burning its whole budget (a 400 cost ~25 wasted seconds per file).
# Deliberately excludes 429 (rate limits clear) and 5xx (transient). 401 is in:
# a rejected token never heals by resending it -- the 2026-08-18 run burned 720
# retries (~36 min) on a single expired credential.
_ASR_NON_RETRYABLE_STATUS = frozenset({400, 401, 413, 422})

# Oversized-audio splitting. The cut target is nudged to the quietest moment within
# +/- _SPLIT_SEARCH_WINDOW_S so a word is not bisected mid-speech -- the garbled word
# would land straight in the CER-scored transcript. Energy is measured over
# _SPLIT_RMS_WINDOW_S windows; telephony calls carry plenty of inter-word silence.
_SPLIT_SEARCH_WINDOW_S = 10.0
_SPLIT_RMS_WINDOW_S = 0.25
# What wave.open + _pcm16_wav_bytes write ahead of the frames: RIFF + fmt + data
# headers. Counted against the upload cap so a chunk's WAV never exceeds it.
_WAV_HEADER_BYTES = 44

# The metrics keys every per-file record carries; also the "absent row" template.
# Blank token cells mean "not reported", never 0 -- see the MetricsSchema docstring.
_METRICS_DEFAULTS: dict[str, Any] = {
    "status": STATUS_FAILED,
    "failed_stage": "",
    "error_type": "",
    "asr_attempts": None,
    "label_attempts": None,
    "analysis_attempts": None,
    "audio_seconds": None,
    "asr_segments": None,
    "label_prompt_tokens": None,
    "label_completion_tokens": None,
    "analysis_prompt_tokens": None,
    "analysis_completion_tokens": None,
    "analysis_reasoning_tokens": None,
    "analysis_cached_tokens": None,
    # Kept apart so `Total Tokens` sums two reported figures rather than
    # recomputing either. Not sheet columns.
    "label_total_tokens": None,
    "analysis_total_tokens": None,
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

    Subclasses ``ValueError`` so the existing except tuples in both LLM stages catch it
    unchanged. Its message carries a duration only -- never transcript text, which is
    the customer's own words and must not reach a log or the Error Type cell.
    """


def _senti_llm_concurrency() -> int:
    """Effective ``SENTI_LLM_CONCURRENCY``: files whose ASR->label->analysis chains run at once.

    Read at ``Config()`` instantiation (via ``default_factory``), so ``main.py``'s
    ``load_dotenv()`` has already run and the menu's Config preview shows the value
    the run will use. Unset means 1 -- today's sequential behaviour, silently. A
    set-but-bad value (unparsable or < 1) warns and falls back to 1, matching
    ``LoggerConfig.from_env``'s fail-soft asymmetry: a typo must not crash startup.
    """
    raw = get_env("SENTI_LLM_CONCURRENCY")
    if raw is None or raw == "":
        return 1
    # Sentinel 0: get_env_int returns it only when raw is unparsable, and a literal
    # "0" is itself invalid (< 1), so both bad shapes land in the one warning below.
    value = get_env_int("SENTI_LLM_CONCURRENCY", 0)
    if value < 1:
        logger.warning(
            "internal_sentiment_split.config.concurrency_invalid", value=raw, fallback=1
        )
        return 1
    return value


@dataclass
class Config:
    src_file: str = '/poc_internal_model_migration/voicefiles'
    # Split-pipeline destinations are deliberately disjoint from the baseline
    # script's: a split run must never be confused with -- or cross-resumed
    # against -- an unsplit one.
    dest_file: str = '/poc_internal_model_migration/voicefiles_internal_split_output'
    output_file_name: str = 'model_comparison.xlsx'

    # Prompt configuration (Local file)
    system_inbound_prompt_file: str = 'src/local_model/sentiment/prompt/inbound_prompt.txt'
    system_outbound_prompt_file: str = 'src/local_model/sentiment/prompt/outbound_prompt.txt'
    # The labelling call's prompt. Deliberately its own small file rather than a
    # section of the two big ones: it is sent alone, to a schema-free call.
    system_transcript_prompt_file: str = 'src/local_model/sentiment/prompt/transcript_prompt.md'

    # GCS configuration
    gcs_bucket: str = 'internal-model-poc'
    gcs_src_path: str = 'poc_internal_model/sentiment_qa/internal/src_files'
    gcs_payload_path: str = 'poc_internal_model/sentiment_qa/internal_split/payload_files'
    gcs_dest_path: str = 'poc_internal_model/sentiment_qa/internal_split/dest_files'

    # Both legs go to OpenRouter -- not api.modellismz.app: per resources/
    # Personal-API Manual.md that endpoint has an 8,192-token context and no
    # /v1/audio/transcriptions, while the analysis call alone measures ~35k
    # prompt tokens. Do not re-point these at it without sectioning the prompt.
    model_name: str = 'qwen3.8-27b-fp8'

    # Separate on purpose: the analysis model is the variable under evaluation;
    # swapping it must not also change the transcript, or CER stops being
    # comparable across models.
    label_model_name: str = "qwen3.8-27b-fp8"
    asr_model_name: str = "typhoon-whisper-large-v3"
    base_url: str = "https://token-fac-api.truecorp.co.th/v1" # "https://10.94.154.102/v1"
    asr_base_url: str = "https://token-fac-api.truecorp.co.th/v1" # "https://10.94.154.102/v1"
    # Both LLM legs stream, which makes this an inter-chunk idle timeout rather than a
    # whole-request budget: httpx applies its read timeout per read on the body.
    llm_timeout: float | None = 300.0
    # Higher than the LLM's: this leg uploads the audio before it decodes it.
    asr_timeout: float | None = 600.0

    # Set to a previous run's id (the timestamp folder name) to resume it: files
    # with a dest JSON are reused outright, files with a stored ASR payload skip
    # re-transcription, and files that failed are redone. None starts a fresh run.
    run_id: str | None = None # "2026-08-18_14-36-33"

    # Sources are 8 kHz telephony; speech encoders train on 16 kHz. None sends
    # the source bytes unchanged.
    asr_target_rate: int | None = 16000

    # Two stacked limits sit in front of the ASR model: nginx caps request bodies
    # at ~50 MB (a 60,433,964-byte upload 413'd), and behind it litellm enforces
    # its own audio_filesize_mb -- a 28.96 MiB chunk got 400 "Maximum file size
    # exceeded" (2026-08-17), so the effective ceiling is litellm's, likely the
    # common 25 MB. Audio above this cap is split at quiet points, transcribed
    # chunk by chunk, and merged; 24 MiB stays under both layers plus multipart
    # overhead. Files at or under the cap upload byte-identical to a run without
    # this field.
    asr_max_upload_bytes: int = 24 * 1024 * 1024

    # A dict cannot be a bare dataclass default -- it must go through default_factory.
    generation_config: dict[str, Any] = field(
        default_factory=lambda: {
            # Analysis needs near-greedy decoding: at temperature 1 this model
            # family produced repetition loops and off-domain text on this endpoint.
            "temperature": 0.0,
            "top_p": 1.0,
            # A cost ceiling, not a size expectation: a provider that drops the
            # response_format grammar can loop until the context window is full --
            # observed twice on qwen3.6-27b (102,071 completion tokens, ~$0.25 per
            # attempt). Sized fail-fast from the 2026-08-18 success envelope
            # (65 of 66 analysis successes were <= 4,955 completion tokens; only
            # one outlier hit 9,458, which the final-attempt escalation covers --
            # and each split section emits a fraction of the full response);
            # at ~20 tok/s the cap bounds a runaway stream to ~5 min. Per
            # section: every one of the 7 parallel section calls gets this cap.
            "max_tokens": 6000,
            "seed": 0,
        }
    )
    # The one-shot bigger budget for the last analysis attempt; only sections
    # that already failed once pay for it. Covers the observed legitimate tail
    # (9,458 completion tokens on 2026-08-18, full-schema) with margin while
    # still bounding a runaway final attempt to ~10 min.
    final_attempt_max_tokens: int = 12000
    # The labelling call. Greedy, because a transcript should not be sampled;
    # its output is transcript-sized, so the ceiling covers a long call with margin.
    label_generation_config: dict[str, Any] = field(
        default_factory=lambda: {
            "temperature": 0.0,
            "top_p": 1.0,
            # Sized fail-fast at ~2x the 2026-08-18 run's success envelope
            # (label max 3,146 completion tokens) -- a repetition loop is bounded
            # by tokens, not only by the wall-clock stream deadline.
            "max_tokens": 6000,
            "seed": 0,
        }
    )
    asr_generation_config: dict[str, Any] = field(
        default_factory=lambda: {
            "language": "th",
            "temperature": 0.1,
            # "json", not "verbose_json": the litellm/vLLM gateway 400s
            # verbose_json for qwen3-asr-1.7b (observed 2026-08-17; earlier runs
            # worked, so the gateway changed). The json shape carries text only --
            # _normalize_asr_payload rebuilds the verbose dict with a locally
            # computed duration and empty segments, so the label stage falls back
            # to the flat text without timestamps. If the team re-enables it,
            # restore "response_format": "verbose_json" plus
            # "timestamp_granularities": ["segment"] here -- downstream tolerates
            # both shapes. Never use "text"/"srt"/"vtt": the SDK returns a bare
            # str for those and model_dump breaks.
            "response_format": "json",
        }
    )
    # ASR attempt budget only. Kept at 3: the two longest audios of the
    # 2026-08-18 success set needed all three attempts to transcribe.
    max_attempts: int = 3
    # Label + per-section analysis attempt budget. 2, not 3: one seed-nudge
    # retry rescues transient faults, while a hallucinating section stops
    # burning a third full-length stream -- fail fast; the resume contract
    # handles the failed file.
    llm_max_attempts: int = 2
    # The SDK's retries sit *inside* each stage's seed-nudge loop, so the two multiply;
    # the default 2 made one file cost up to 9 requests against a gateway that answers
    # 504 only after 300s. Applies to the LLM legs, not the ASR client.
    llm_max_retries: int = 0
    # Files whose ASR->label->analysis chains run in parallel. Only the network
    # calls fan out -- GCS I/O and checkpointing stay sequential on the main
    # thread. No upper clamp: the endpoint's own rate limits are the real ceiling.
    llm_concurrency: int = field(default_factory=_senti_llm_concurrency)
    # Wall clock a single stream may run *after its first event arrives* before it
    # is abandoned. Counted from the first streamed event, not from the request:
    # under load this endpoint queues requests for minutes (ttft median 213s on
    # 2026-08-18), and counting that wait against the budget killed healthy
    # streams. Queue wait is bounded separately by the httpx read timeout, which
    # only resets once bytes flow. 600s keeps ~20% margin over the worst
    # observed successful stream (496s on 2026-08-18); with the tightened
    # max_tokens it is a backstop for stalls, not the primary cutoff.
    llm_stream_deadline: float = 600.0
    # Ask for the final usage-bearing chunk. Set False only if the endpoint rejects
    # stream_options outright -- the cost is blank token cells for the whole run.
    stream_include_usage: bool = True

    # Output XLSX configuration
    gt_src_file: str = '/poc_internal_model_migration/AI Benchmark Report.xlsx'
    gt_sheet_name: str = 'Voice - Groundtruth'
    output_sheet_name: str = 'Voice - Internal Model Result'
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


def _g711_decode_table(format_tag: int) -> "np.ndarray":
    """256-entry int16 LUT: one G.711 byte -> its linear sample (ITU-T G.711).

    Verified byte-for-byte against CPython's audioop tables (the stdlib decoder,
    removed in Python 3.13). mu-law spans +/-32124, A-law +/-32256 -- full scale
    by construction, no post-shift.
    """
    import numpy as np

    if format_tag == _WAVE_FORMAT_MULAW:
        inv = (~np.arange(256, dtype=np.uint8)).astype(np.int32)
        magnitude = (((inv & 0x0F) << 3) + 0x84) << ((inv & 0x70) >> 4)
        values = np.where(inv & 0x80, 0x84 - magnitude, magnitude - 0x84)
    else:  # _WAVE_FORMAT_ALAW; _decode_g711_wav has already screened the tag
        ax = np.arange(256, dtype=np.int32) ^ 0x55
        base = (ax & 0x0F) << 4
        segment = (ax & 0x70) >> 4
        # np.where evaluates both lanes, so the segment-0 lane's shift is clamped
        # rather than left to go negative.
        magnitude = np.where(
            segment == 0, base + 8, (base + 0x108) << np.maximum(segment - 1, 0)
        )
        # A-law's sign is inverted relative to mu-law: 0x80 set means positive.
        values = np.where(ax & 0x80, magnitude, -magnitude)
    return values.astype("<i2")


def _decode_g711_wav(wav_bytes: bytes) -> "tuple[np.ndarray, int, int]":
    """Decode a G.711 (A-law/mu-law) WAV that stdlib ``wave`` refuses to open.

    A minimal RIFF walk: the first ``fmt `` and ``data`` chunks, everything else
    skipped. Length-checks precede every unpack so ``struct.error`` cannot escape
    -- :func:`transcribe_file`'s never-raises contract catches ``ValueError`` only.

    Returns:
        ``(samples, nchannels, framerate)`` with ``samples`` int16 of shape
        ``(nframes, nchannels)``.

    Raises:
        ValueError: On a non-RIFF input, an unsupported format tag, corrupt fmt
            fields, or a missing or truncated ``data`` chunk -- truncation fails
            loudly rather than silently transcribing half a call.
    """
    import numpy as np

    if len(wav_bytes) < 12 or wav_bytes[:4] != b"RIFF" or wav_bytes[8:12] != b"WAVE":
        raise ValueError("input is not a RIFF/WAVE file")

    fmt_body: bytes | None = None
    data_body: bytes | None = None
    offset = 12
    while offset + 8 <= len(wav_bytes):
        chunk_id = wav_bytes[offset : offset + 4]
        (size,) = struct.unpack_from("<I", wav_bytes, offset + 4)
        body = wav_bytes[offset + 8 : offset + 8 + size]
        if chunk_id == b"fmt " and fmt_body is None:
            fmt_body = body
        elif chunk_id == b"data" and data_body is None:
            if len(body) < size:
                raise ValueError(
                    f"WAV data chunk truncated: header declares {size} bytes, "
                    f"file holds {len(body)}"
                )
            data_body = body
        # Odd-size chunks carry one pad byte (RIFF word alignment).
        offset += 8 + size + (size & 1)

    if fmt_body is None or len(fmt_body) < 16:
        raise ValueError("WAV fmt chunk missing or short")
    # First 16 bytes only; a cbSize/extensible tail is ignored by construction.
    format_tag, nchannels, framerate, _, _, bits = struct.unpack_from("<HHIIHH", fmt_body)
    if format_tag not in (_WAVE_FORMAT_ALAW, _WAVE_FORMAT_MULAW):
        raise ValueError(
            f"unsupported WAV format tag {format_tag}; "
            "only PCM, A-law (6) and mu-law (7) are supported"
        )
    if nchannels < 1 or framerate <= 0 or bits != 8:
        raise ValueError(
            f"corrupt G.711 fmt chunk: channels={nchannels}, rate={framerate}, bits={bits}"
        )
    if data_body is None:
        raise ValueError("WAV data chunk missing")

    table = _g711_decode_table(format_tag)
    raw = np.frombuffer(data_body, dtype=np.uint8)
    # Floor to whole frames, mirroring stdlib wave's behaviour on ragged tails.
    raw = raw[: raw.size // nchannels * nchannels]
    return table[raw].reshape(-1, nchannels), nchannels, framerate


def _pcm16_wav_bytes(frames: bytes, nchannels: int, framerate: int) -> bytes:
    """Wrap raw little-endian int16 frames in a complete PCM WAV container."""
    with io.BytesIO() as buffer:
        with wave.open(buffer, "wb") as wav_out:
            wav_out.setnchannels(nchannels)
            wav_out.setsampwidth(2)
            wav_out.setframerate(framerate)
            wav_out.writeframes(frames)
        return buffer.getvalue()


def resample_wav_bytes(wav_bytes: bytes, target_rate: int = 16000) -> bytes:
    """Resample a 16-bit PCM or G.711 (A-law/mu-law) WAV to ``target_rate``.

    Returns a complete PCM16 WAV as bytes. Sources are 8 kHz telephony; speech
    encoders train on 16 kHz. torch/torchaudio are imported lazily -- they cost
    ~0.5-1 GB RSS, and a fully-resumed run never needs them. A PCM input already
    at ``target_rate`` is returned unchanged; G.711 input is always decoded to
    PCM16, even at ``target_rate``, because neither the ASR endpoint nor stdlib
    ``wave`` reads G.711 bytes.

    Raises:
        ValueError: On a non-positive rate, an unsupported format tag, a
            non-16-bit PCM input, or a malformed/truncated WAV. Callers must
            guard -- one unreadable recording must not cost the run every other file.
    """
    if target_rate <= 0:
        raise ValueError(f"target_rate must be positive, got {target_rate}")

    decoded = None  # set only by the G.711 fallback path
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wav_in:
            nchannels = wav_in.getnchannels()
            sampwidth = wav_in.getsampwidth()
            framerate = wav_in.getframerate()
            frames = wav_in.readframes(wav_in.getnframes())
    except (wave.Error, EOFError):
        # wave accepts only PCM -- the telephony sources are G.711 -- and raises
        # EOFError, not wave.Error, on an input shorter than a chunk header. The
        # decoder raises ValueError itself on anything else, tag number included.
        decoded, nchannels, framerate = _decode_g711_wav(wav_bytes)
    else:
        if sampwidth != 2:
            raise ValueError(f"only 16-bit PCM WAV is supported, got sample width {sampwidth}")
        if framerate == target_rate:
            return wav_bytes

    # numpy alone first: the G.711 same-rate return must not pay for torch.
    import numpy as np

    if decoded is None:
        samples = np.frombuffer(frames, dtype=np.int16).reshape(-1, nchannels)
    else:
        samples = decoded
        if framerate == target_rate:
            # Decoded but no resample needed: re-encode; the original bytes are
            # G.711 and must never be returned.
            return _pcm16_wav_bytes(samples.astype("<i2").tobytes(), nchannels, framerate)

    import torch
    import torchaudio

    # (channels, samples) float32 in [-1, 1], the layout torchaudio resamples over.
    audio = torch.from_numpy(samples.astype(np.float32).T / 32768.0)
    if decoded is None:
        del frames  # the float copy is now the only reader of the raw buffer
    del samples, decoded
    resampled = torchaudio.functional.resample(audio, framerate, target_rate)
    del audio
    # Interpolation can overshoot the int16 range slightly; clamp before narrowing.
    # In place on the fresh resample result: same ops, order and values as the
    # chained form, without three more full-size temporaries.
    resampled.clamp_(-1.0, 1.0).mul_(32767.0).round_()
    out_frames = resampled.to(torch.int16).numpy().T.tobytes()

    return _pcm16_wav_bytes(out_frames, nchannels, target_rate)


def _ensure_splittable_pcm16(wav_bytes: bytes) -> tuple[bytes, bool]:
    """Return WAV bytes stdlib ``wave`` can cut on frame boundaries.

    Only the passthrough path (``asr_target_rate is None``) can hand the splitter
    G.711 bytes, which ``wave`` refuses to open -- everything else already went
    through :func:`resample_wav_bytes` and is PCM16 by construction. A G.711 input
    is decoded at its *native* rate: the file was going to be split anyway, so
    byte-identity with today's upload is already off the table for it, and the
    splitter's chunk-count math runs on the decoded (larger) bytes so each chunk
    still lands under the cap.

    Returns:
        ``(pcm16_wav_bytes, decoded)`` -- ``decoded`` True when a G.711 input was
        re-encoded, False when the input was returned unchanged.

    Raises:
        ValueError: On a non-16-bit PCM input or a malformed/unsupported WAV --
            propagates to :func:`transcribe_file`'s split-phase guard, the same
            never-raises contract as the resample guard.
    """
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wav_in:
            sampwidth = wav_in.getsampwidth()
    except (wave.Error, EOFError):
        samples, nchannels, framerate = _decode_g711_wav(wav_bytes)
        return _pcm16_wav_bytes(samples.astype("<i2").tobytes(), nchannels, framerate), True
    if sampwidth != 2:
        raise ValueError(f"only 16-bit PCM WAV is supported, got sample width {sampwidth}")
    return wav_bytes, False


def _min_energy_cut_frame(
    samples: "np.ndarray",
    target_frame: int,
    framerate: int,
    lo_frame: int,
    hi_frame: int,
) -> int:
    """The quietest frame to cut at within ``+/-_SPLIT_SEARCH_WINDOW_S`` of the target.

    Walks the clamped search region in ``_SPLIT_RMS_WINDOW_S`` hops, scores each
    window by mean-square energy (float64, summed across channels -- argmin of
    mean-square equals argmin of RMS, so the sqrt is skipped), and returns the
    centre frame of the quietest window. ``lo_frame``/``hi_frame`` are the caller's
    monotonicity bounds: cuts must move forward and stay off the file's edges.
    Falls back to the clamped target when the region is smaller than one window.
    """
    import numpy as np

    window = int(_SPLIT_SEARCH_WINDOW_S * framerate)
    rms_frames = max(1, int(_SPLIT_RMS_WINDOW_S * framerate))
    lo = max(lo_frame, target_frame - window)
    hi = min(hi_frame, target_frame + window)
    if hi - lo < rms_frames:
        return min(max(target_frame, lo_frame), hi_frame)

    region = samples[lo:hi].astype(np.float64)
    starts = range(0, (hi - lo) - rms_frames + 1, rms_frames)
    energies = [float((region[s : s + rms_frames] ** 2).mean()) for s in starts]
    quietest = min(range(len(energies)), key=energies.__getitem__)
    return lo + quietest * rms_frames + rms_frames // 2


def _split_pcm16_wav(wav_bytes: bytes, max_bytes: int) -> tuple[list[tuple[bytes, float]], float]:
    """Split a PCM16 WAV into equal-count chunks that each stay under ``max_bytes``.

    The chunk count budgets for the worst case up front: ``usable`` subtracts the
    WAV header *and* a full ``_SPLIT_SEARCH_WINDOW_S`` of frames, so a cut nudged
    all the way to the search edge still cannot push a chunk over the cap. Start
    offsets are exact (``cut_frame / framerate``) -- they become the segment
    timestamp offsets in :func:`_merge_chunk_payloads`, and drift there would
    corrupt the turn-boundary evidence the analysis stage reads.

    Returns:
        ``([(chunk_wav_bytes, start_seconds), ...], total_duration_seconds)``.

    Raises:
        ValueError: When ``max_bytes`` leaves no room for audio after the header
            and nudge slack -- an absurd cap, caught by the caller's guard.
    """
    import numpy as np

    with wave.open(io.BytesIO(wav_bytes), "rb") as wav_in:
        nchannels = wav_in.getnchannels()
        framerate = wav_in.getframerate()
        nframes = wav_in.getnframes()
        frames = wav_in.readframes(nframes)

    frame_size = nchannels * 2
    slack = int(_SPLIT_SEARCH_WINDOW_S * framerate) * frame_size
    usable = max_bytes - _WAV_HEADER_BYTES - slack
    if usable <= 0:
        raise ValueError(
            f"asr_max_upload_bytes={max_bytes} leaves no room for audio frames"
        )

    samples = np.frombuffer(frames, dtype="<i2").reshape(-1, nchannels)
    rms_frames = max(1, int(_SPLIT_RMS_WINDOW_S * framerate))
    n = max(2, math.ceil(nframes * frame_size / usable))

    cuts: list[int] = []
    prev_cut = 0
    for i in range(1, n):
        target = round(i * nframes / n)
        cut = _min_energy_cut_frame(
            samples,
            target,
            framerate,
            lo_frame=prev_cut + rms_frames,
            hi_frame=nframes - rms_frames,
        )
        cuts.append(cut)
        prev_cut = cut

    chunks: list[tuple[bytes, float]] = []
    for start, end in zip([0, *cuts], [*cuts, nframes], strict=True):
        chunks.append(
            (
                _pcm16_wav_bytes(
                    frames[start * frame_size : end * frame_size], nchannels, framerate
                ),
                start / framerate,
            )
        )
    return chunks, nframes / framerate


def _merge_chunk_payloads(
    payloads: list[dict[str, Any]],
    starts: list[float],
    duration: float,
) -> dict[str, Any]:
    """Merge per-chunk ASR dumps into one whole-file ``verbose_json``-shaped dict.

    Segment ``start``/``end`` are offset by their chunk's start time and ``id``
    renumbered, so downstream sees one continuous timeline -- exactly what an
    unsplit transcription would have carried. ``duration`` is the caller's exact
    total (frames / framerate), never a sum of the endpoint's per-chunk figures.
    ``usage`` is deliberately dropped: :func:`_asr_stats` reads ``duration`` first,
    and a summed usage block would be an invented SDK shape. The result satisfies
    :func:`_asr_payload_valid`, so the payload checkpoint and resume are unchanged.
    """
    segments = [
        {
            **segment,
            "id": idx,
            "start": (segment.get("start") or 0.0) + start,
            "end": (segment.get("end") or 0.0) + start,
        }
        for idx, (segment, start) in enumerate(
            (segment, start)
            for payload, start in zip(payloads, starts, strict=True)
            for segment in (payload.get("segments") or [])
        )
    ]
    texts = [text for payload in payloads if (text := (payload.get("text") or "").strip())]
    return {
        "task": payloads[0].get("task"),
        "language": payloads[0].get("language"),
        "duration": duration,
        "text": " ".join(texts),
        "segments": segments,
    }


def _wav_duration_seconds(wav_bytes: bytes) -> float | None:
    """Audio duration off the WAV header, or None when the container is unreadable.

    Exact for the PCM16 uploads this pipeline produces (frames / framerate). The
    None path is the G.711 passthrough shape stdlib ``wave`` refuses to open --
    fail-soft, because this feeds a metadata field, never a gate.
    """
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wav_in:
            framerate = wav_in.getframerate()
            if framerate <= 0:
                return None
            return wav_in.getnframes() / framerate
    except (wave.Error, EOFError):
        return None


def _normalize_asr_payload(
    candidate: dict[str, Any],
    content: bytes,
    config: Config,
) -> dict[str, Any]:
    """Rebuild the ``verbose_json`` shape around a plain ``json``-format dump.

    The gateway stopped accepting ``verbose_json`` (see ``asr_generation_config``),
    and the ``json`` shape carries ``text`` only -- but everything downstream reads
    the verbose dict: ``_asr_payload_valid`` gates resume on ``duration`` and
    ``segments`` being present, ``_asr_stats`` fills the Audio Seconds cell, and
    both LLM prompts print the duration/language metadata. So the missing fields
    are rebuilt here: ``duration`` computed locally from the upload's own WAV
    header (exact, not estimated), ``segments`` honestly empty, ``language`` the
    value the request asked for. A dump that already carries both keys -- a real
    verbose response -- is returned unchanged, which is what keeps the config
    switchable back without another code change.
    """
    if "segments" in candidate and "duration" in candidate:
        return candidate
    normalized: dict[str, Any] = {
        "task": "transcribe",
        "language": candidate.get("language")
        or config.asr_generation_config.get("language"),
        "duration": _wav_duration_seconds(content),
        "text": candidate.get("text") or "",
        "segments": [],
    }
    if candidate.get("usage") is not None:
        # _asr_stats falls back to usage.seconds when duration is None.
        normalized["usage"] = candidate["usage"]
    return normalized


def _asr_payload_valid(candidate: Any) -> bool:
    """Whether a stored payload JSON is an ASR dump this run can reuse.

    The payload prefix is shared with the deprecated chunked pipeline, which stores
    a *list* under the same ``{run_id}/{stem}.json`` path -- checked here so a
    cross-loaded checkpoint costs one warning and a re-transcription, not a crash.
    """
    return (
        isinstance(candidate, dict)
        and "duration" in candidate
        and "segments" in candidate
    )


def _asr_stats(payload: dict[str, Any]) -> dict[str, Any]:
    """Audio duration and segment count, read off a stored or fresh ASR dump.

    Shared by the transcribe and resume paths. ``asr_attempts`` is deliberately not
    set here: only a path that actually called the endpoint can report attempts.
    """
    segments = payload.get("segments") or []
    duration = payload.get("duration")
    if duration is None:
        # The usage block carries the same number and costs nothing to fall back on.
        duration = (payload.get("usage") or {}).get("seconds")
    return {
        "audio_seconds": float(duration) if duration is not None else None,
        "asr_segments": len(segments),
    }


def _build_user_content(file_name: str, payload: dict[str, Any]) -> str:
    """Render one ASR ``verbose_json`` dump as the analysis call's user message.

    Takes the dumped dict rather than the SDK model -- what the resume path holds,
    and testable without constructing an SDK object. Built by concatenation: a
    triple-quoted f-string would carry its indentation into every transcript line.
    Falls back to the flat ``text`` field when the endpoint returned no segments.
    """
    segments = payload.get("segments") or []
    lines = [
        f"[{segment.get('start')}-{segment.get('end')}] {(segment.get('text') or '').strip()}"
        for segment in segments
    ]
    body = "\n".join(lines) if lines else (payload.get("text") or "")
    return (
        "## METADATA\n"
        f"FILE name: {file_name}\n"
        f"Record duration: {payload.get('duration')} seconds\n"
        f"Language: {payload.get('language')}\n"
        "\n"
        "## TRANSCRIPT\n"
        f"{body}"
    )


def _labelled_transcript_error(text: str) -> str | None:
    """Name the way a labelling result is unusable, or None when it looks right.

    Guards the failure that motivated splitting the call out: the model echoing its
    ``[start-end]`` input verbatim -- silent, since it validates and uploads. An
    empty result is *not* an error: a silent recording is a real case.
    """
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return None
    if any(line.lstrip().startswith("[") for line in lines):
        return "transcript_echoed_input"
    if not any(line.lstrip().startswith(("Agent:", "Customer:")) for line in lines):
        return "transcript_unlabelled"
    return None


def label_transcript(
    client_llm: OpenAI,
    payload: dict[str, Any],
    file_name: str,
    config: Config,
    transcript_prompt: str,
) -> tuple[str | None, dict[str, Any]]:
    """Turn the ASR segments into ``Agent:`` / ``Customer:`` turns.

    Deliberately **no** ``response_format``: on an OpenAI-compatible endpoint a schema
    field's description is only a decoding grammar, so the labelling rules must ride as
    prompt text. ``.stream()`` without one behaves as ``.create()`` did -- the final
    completion still carries plain ``content`` -- so do not "fix" this by adding a
    schema. Streaming is a transport choice: this stage emits a transcript-sized
    response, the longest generation in the pipeline, and a silent connection is what
    the gateway in front of this endpoint answers with a 504. Never raises, exactly
    like the other two stages.

    Returns:
        ``(transcript, metrics)`` -- ``transcript`` None on failure. ``metrics``
        carries this stage's cells only; the caller merges it over
        :data:`_METRICS_DEFAULTS`.
    """
    user_content = _build_user_content(file_name, payload)
    transcript: str | None = None
    usage = None
    last_error: str | None = None
    attempts = 0
    for attempt in range(1, config.llm_max_attempts + 1):
        attempts = attempt
        generation_config = (
            config.label_generation_config
            if attempt == 1
            else {**config.label_generation_config, "seed": attempt - 1}
        )
        started = time.monotonic()
        ttft_ms: float | None = None
        try:
            with client_llm.chat.completions.stream(
                model=config.label_model_name,
                messages=[
                    {"role": "system", "content": transcript_prompt},
                    {"role": "user", "content": user_content},
                ],
                **generation_config,
                **_stream_options(config),
            ) as stream:
                # Armed by the first event, not the request: queue wait must not
                # spend the generation budget (it killed healthy streams under
                # load), and it is already bounded by the client's read timeout.
                deadline: float | None = None
                # A drain, not a consumer: the SDK accumulates the deltas, and a
                # half-labelled transcript is no use to this function.
                for event in stream:
                    if deadline is None:
                        deadline = time.monotonic() + config.llm_stream_deadline
                    if ttft_ms is None and event.type == "content.delta":
                        ttft_ms = _elapsed_ms(started)
                        logger.info(
                            "internal_sentiment_split.label.time_to_first_token",
                            file=file_name,
                            attempt=attempt,
                            ttft_ms=ttft_ms,
                        )
                    if time.monotonic() > deadline:
                        # Inside the with, so the manager still closes the response
                        # and releases the connection on the way out.
                        raise StreamDeadlineError(
                            f"stream exceeded {config.llm_stream_deadline:.0f}s"
                        )
                res = stream.get_final_completion()
            candidate = res.choices[0].message.content
            if candidate is None:
                raise ValueError(
                    f"no content, refusal={res.choices[0].message.refusal!r}"
                )
            candidate = candidate.strip()
            shape_error = _labelled_transcript_error(candidate)
            if shape_error:
                raise ValueError(shape_error)
            transcript = candidate
            usage = res.usage
            # Separates a queued request from a slow one: a large ttft_ms with a small
            # stream_ms means the backend sat in a queue, which no client change fixes.
            logger.info(
                "internal_sentiment_split.label.stream",
                file=file_name,
                attempt=attempt,
                ttft_ms=ttft_ms,
                stream_ms=_elapsed_ms(started),
            )
            break
        # httpx.HTTPError: raw transport faults (RemoteProtocolError, ReadError, ...)
        # surface unwrapped from the SSE drain -- the SDK only wraps them into
        # APIConnectionError on the initial request, not mid-stream.
        except (ValueError, LengthFinishReasonError, APIError, httpx.HTTPError) as e:
            last_error = str(e) if isinstance(e, ValueError) else type(e).__name__
            logger.warning(
                "internal_sentiment_split.label.retry",
                file=file_name,
                attempt=attempt,
                error_type=last_error,
                ttft_ms=ttft_ms,
                stream_ms=_elapsed_ms(started),
            )
            if attempt < config.llm_max_attempts:
                time.sleep(_backoff_s(attempt))

    if transcript is None:
        logger.error(
            "internal_sentiment_split.label.failed",
            file=file_name,
            attempts=attempts,
            error_type=last_error,
        )
        return None, {
            "status": STATUS_FAILED,
            "failed_stage": "label",
            "error_type": last_error or "",
            "label_attempts": attempts,
        }

    return transcript, {
        "label_attempts": attempts,
        "label_prompt_tokens": getattr(usage, "prompt_tokens", None),
        "label_completion_tokens": getattr(usage, "completion_tokens", None),
        "label_total_tokens": getattr(usage, "total_tokens", None),
    }


def _transcribe_once_with_retries(
    client_asr: OpenAI,
    content: bytes,
    file_name: str,
    config: Config,
    chunk: int | None = None,
    chunk_count: int | None = None,
) -> tuple[dict[str, Any] | None, int, dict[str, Any]]:
    """One upload's endpoint retry loop -- the whole file, or one chunk of it.

    ``chunk is None`` is the unsplit path and stays byte- and name-identical to a
    run without chunking; a chunk upload is named ``<stem>_c{n}.wav`` and its retry
    records carry ``chunk``/``chunks`` so the log distinguishes which piece failed.
    The terminal ``asr.failed`` record deliberately stays in
    :func:`transcribe_file`, which alone knows the file's summed attempt total.

    A status in :data:`_ASR_NON_RETRYABLE_STATUS` ends the loop after its first
    attempt -- the rejection is deterministic, so ``attempts`` honestly records 1
    rather than a budget burned on identical requests. The returned payload is
    always :func:`_normalize_asr_payload`'d, so the ``json`` response format
    yields the same verbose-shaped dict downstream expects.

    Returns:
        ``(payload, attempts, failure)`` -- ``payload`` None when every attempt
        failed; ``failure`` carries ``error_type``/``status_code``/``error`` (the
        truncated endpoint detail) for the caller's terminal record.
    """
    chunk_fields = {} if chunk is None else {"chunk": chunk, "chunks": chunk_count}
    stem = Path(file_name).stem
    upload_name = f"{stem}.wav" if chunk is None else f"{stem}_c{chunk}.wav"

    payload: dict[str, Any] | None = None
    last_error: str | None = None
    last_status: int | None = None
    last_detail: str | None = None
    attempts = 0
    for attempt in range(1, config.max_attempts + 1):
        attempts = attempt
        try:
            # The tuple form, not bare bytes: httpx names a bytes part "upload" and
            # derives its content type from that name, so the endpoint gets a file
            # it cannot identify as audio. The suffix is forced to .wav.
            response = client_asr.audio.transcriptions.create(
                file=(upload_name, content, _ASR_MIME),
                model=config.asr_model_name,
                **config.asr_generation_config,
            )
            candidate = response.model_dump(mode="json")
            if not (candidate.get("segments") or candidate.get("text")):
                # Not an error on the wire, but the analysis stage would score a
                # call it never saw. Retried, then failed honestly.
                raise ValueError("transcription returned no segments and no text")
            payload = _normalize_asr_payload(candidate, content, config)
            break
        except (ValueError, APIError) as e:
            last_error = type(e).__name__
            last_status = getattr(e, "status_code", None)
            # Endpoint/proxy metadata, never audio content -- a rejected request
            # was never transcribed.
            last_detail = (getattr(e, "message", None) or str(e)).strip()
            last_detail = last_detail[:_ASR_ERROR_DETAIL_MAX]
            logger.warning(
                "internal_sentiment_split.asr.retry",
                file=file_name,
                attempt=attempt,
                error_type=last_error,
                status_code=last_status,
                error=last_detail,
                upload_bytes=len(content),
                **chunk_fields,
            )
            if last_status in _ASR_NON_RETRYABLE_STATUS:
                # A deterministic rejection of this exact request: resending the
                # same bytes cannot succeed, so the remaining budget is waste.
                break
            if attempt < config.max_attempts:
                time.sleep(1)

    return payload, attempts, {
        "error_type": last_error,
        "status_code": last_status,
        "error": last_detail,
    }


def transcribe_file(
    client_asr: OpenAI,
    audio_bytes: bytes,
    file_name: str,
    config: Config,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Transcribe one recording, retrying on the same budget the analysis uses.

    Never raises: ``(None, metrics)`` with ``failed_stage="asr"`` costs the run one
    blank row, not the whole sheet. The resample runs once, before any upload: a
    corrupt or unsupported WAV fails deterministically, so it costs one log record
    -- no endpoint attempts, no sleeps -- and the retry budget covers endpoint
    calls only. Retry and failure records carry ``status_code``, ``error`` and
    ``upload_bytes`` so an endpoint rejection is diagnosable from the log alone.

    Audio whose upload would exceed ``config.asr_max_upload_bytes`` (the
    endpoint's stacked nginx/litellm size limits, with margin) is split at quiet
    points, transcribed chunk by chunk -- sequentially, each on its own
    ``max_attempts`` budget -- and
    merged back into one payload. Any chunk exhausting its retries fails the whole
    file: an analysis scored on half a call would be silently wrong, which is worse
    than an honest blank row. ``asr_attempts`` is the sum over chunks (= endpoint
    calls paid); chunk detail lives in the ``asr.split`` log record only, so the
    Matrix sheet's shape is unchanged.

    Returns:
        ``(payload, metrics)`` -- ``payload`` a
        ``TranscriptionVerbose.model_dump(mode="json")`` (or the merged equivalent
        for a split file) on success, None on failure.
    """
    content = audio_bytes
    if config.asr_target_rate is not None:
        try:
            content = resample_wav_bytes(audio_bytes, config.asr_target_rate)
        except ValueError as e:
            # asr_attempts stays absent: no endpoint call happened, and blank
            # means "not reported", never 0. attempts=0 in the log marks a decode
            # failure apart from an exhausted retry budget; str(e) is format
            # metadata (tag numbers, byte counts), never audio content.
            logger.error(
                "internal_sentiment_split.asr.failed",
                file=file_name,
                attempts=0,
                error_type=type(e).__name__,
                error=str(e),
            )
            return None, {
                "status": STATUS_FAILED,
                "failed_stage": "asr",
                "error_type": type(e).__name__,
            }

    if len(content) <= config.asr_max_upload_bytes:
        payload, attempts, failure = _transcribe_once_with_retries(
            client_asr, content, file_name, config
        )
        if payload is None:
            logger.error(
                "internal_sentiment_split.asr.failed",
                file=file_name,
                attempts=attempts,
                error_type=failure["error_type"],
                status_code=failure["status_code"],
                error=failure["error"],
                upload_bytes=len(content),
            )
            return None, {
                "status": STATUS_FAILED,
                "failed_stage": "asr",
                "error_type": failure["error_type"] or "",
                "asr_attempts": attempts,
            }
        return payload, {"asr_attempts": attempts, **_asr_stats(payload)}

    # Oversized: split at quiet points, transcribe sequentially, merge. Split
    # faults take the resample-failure shape: deterministic, attempts=0, no sleeps.
    oversized_bytes = len(content)
    decoded_g711 = False
    try:
        if config.asr_target_rate is None:
            content, decoded_g711 = _ensure_splittable_pcm16(content)
        chunks, duration = _split_pcm16_wav(content, config.asr_max_upload_bytes)
    except ValueError as e:
        logger.error(
            "internal_sentiment_split.asr.failed",
            file=file_name,
            attempts=0,
            error_type=type(e).__name__,
            error=str(e),
        )
        return None, {
            "status": STATUS_FAILED,
            "failed_stage": "asr",
            "error_type": type(e).__name__,
        }
    del content  # the chunks now hold the only frame copies

    # Byte counts, chunk indices and cut seconds only -- never audio content.
    logger.info(
        "internal_sentiment_split.asr.split",
        file=file_name,
        upload_bytes=oversized_bytes,
        max_upload_bytes=config.asr_max_upload_bytes,
        chunks=len(chunks),
        chunk_bytes=[len(chunk_bytes) for chunk_bytes, _ in chunks],
        cut_seconds=[round(start, 2) for _, start in chunks[1:]],
        duration_seconds=round(duration, 1),
        decoded_g711=decoded_g711,
    )

    total_attempts = 0
    payloads: list[dict[str, Any]] = []
    starts: list[float] = []
    for idx, (chunk_bytes, start_seconds) in enumerate(chunks, start=1):
        payload, attempts, failure = _transcribe_once_with_retries(
            client_asr, chunk_bytes, file_name, config, chunk=idx, chunk_count=len(chunks)
        )
        total_attempts += attempts
        if payload is None:
            # One exhausted chunk fails the whole file -- no partial transcript.
            logger.error(
                "internal_sentiment_split.asr.failed",
                file=file_name,
                attempts=total_attempts,
                chunk=idx,
                chunks=len(chunks),
                error_type=failure["error_type"],
                status_code=failure["status_code"],
                error=failure["error"],
                upload_bytes=len(chunk_bytes),
            )
            return None, {
                "status": STATUS_FAILED,
                "failed_stage": "asr",
                "error_type": failure["error_type"] or "",
                "asr_attempts": total_attempts,
            }
        payloads.append(payload)
        starts.append(start_seconds)

    merged = _merge_chunk_payloads(payloads, starts, duration)
    return merged, {"asr_attempts": total_attempts, **_asr_stats(merged)}


def _build_analysis_content(file_name: str, payload: dict[str, Any], transcript: str) -> str:
    """Render the analysis call's user message around an already-labelled transcript.

    Same metadata block as :func:`_build_user_content`, but the transcript is the
    labelled turns: most scoring criteria judge agent behaviour, so speaker identity
    is evidence -- and the timestamps dropped are scored by nothing downstream.
    """
    return (
        "## METADATA\n"
        f"FILE name: {file_name}\n"
        f"Record duration: {payload.get('duration')} seconds\n"
        f"Language: {payload.get('language')}\n"
        "\n"
        "## TRANSCRIPT\n"
        f"{transcript}"
    )


# The prompt files' per-topic delimiter: `### **Category N.: `<field>`**`, where
# <field> is the ModelResponse attribute the section's rules describe. Everything
# before the first match is the shared preamble (role, transcript context, and the
# three top-level field definitions the classification call answers).
_SECTION_HEADER_RE = re.compile(r"^### \*\*Category \d+\.: `(\w+)`\*\*", re.MULTILINE)


@dataclass(frozen=True)
class _SlicedPrompt:
    """One direction's system prompt, pre-cut into preamble + per-topic sections.

    Sliced once per run, not per file: the cut points depend only on the prompt
    text, and the ``{date}`` substitution has already happened upstream.
    """

    preamble: str
    sections: dict[str, str]

    def for_section(self, prompt_key: str | None) -> str:
        """The system prompt one section's call rides with.

        ``None`` is the classification call: the preamble alone already carries the
        three top-level field definitions. Indexing is deliberate -- ``run()``
        verified every :data:`EXPECTED_PROMPT_SECTIONS` key before any spend, so a
        missing key here is a bug, not a data condition.
        """
        if prompt_key is None:
            return self.preamble
        return f"{self.preamble}\n{self.sections[prompt_key]}"


def _split_system_prompt(prompt_text: str) -> _SlicedPrompt:
    """Cut a full system prompt at its ``Category`` headers.

    Spans are contiguous -- each section runs from its header to the next header
    (or EOF), and the preamble is everything before the first -- so concatenating
    ``preamble + sections`` in match order reproduces the input exactly: nothing
    the prompt authors wrote can silently fall between two slices. A prompt with
    no headers comes back whole as the preamble with no sections; ``run()``'s
    fail-fast check is what turns that into an abort.
    """
    matches = list(_SECTION_HEADER_RE.finditer(prompt_text))
    if not matches:
        return _SlicedPrompt(preamble=prompt_text, sections={})
    sections = {
        match.group(1): prompt_text[match.start() : end]
        for match, end in zip(
            matches, [*(m.start() for m in matches[1:]), len(prompt_text)], strict=True
        )
    }
    return _SlicedPrompt(preamble=prompt_text[: matches[0].start()], sections=sections)


def _analyze_section(
    client_llm: OpenAI,
    user_content: str,
    file_name: str,
    config: Config,
    system_prompt: str,
    section_key: str,
    response_model: type,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """One topic's structured-output call, on the baseline's full retry budget.

    The loop is the unsplit pipeline's analysis loop verbatim -- seed nudge,
    final-attempt ``max_tokens`` escalation, stream deadline armed by the first
    event -- only the schema is narrower and every record carries ``section`` so
    the log tells which topic queued, retried or failed. The terminal
    ``file.failed`` record deliberately stays in :func:`analyze_transcript`,
    which alone knows the file's summed attempt total -- the
    ``_transcribe_once_with_retries`` / ``transcribe_file`` split.

    Returns:
        ``(dump, metrics)`` -- ``dump`` the parsed model's ``model_dump
        (mode="json")`` (alias keys included, which is what
        ``ModelResponse.model_validate`` expects back) on success, None on
        failure. ``metrics`` carries this section's ``attempts``/``error_type``
        and short-named token counts for the caller to sum.
    """
    res = None
    parsed = None
    last_error: str | None = None
    attempts = 0
    for attempt in range(1, config.llm_max_attempts + 1):
        attempts = attempt
        # Near-greedy decoding reproduces the same failure on an identical retry,
        # so only the seed is nudged -- tie-breaking changes, decoding stays greedy.
        generation_config = (
            config.generation_config
            if attempt == 1
            else {**config.generation_config, "seed": attempt - 1}
        )
        # Escalate only the last of several attempts, so files that fit the ceiling
        # stay at today's cost; attempt > 1 keeps a llm_max_attempts=1 config plain.
        if attempt == config.llm_max_attempts and attempt > 1:
            generation_config = {
                **generation_config,
                "max_tokens": config.final_attempt_max_tokens,
            }
        started = time.monotonic()
        ttft_ms: float | None = None
        try:
            with client_llm.chat.completions.stream(
                model=config.model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                response_format=response_model,
                **generation_config,
                **_stream_options(config),
            ) as stream:
                # Armed by the first event, not the request: queue wait must not
                # spend the generation budget (it killed healthy streams under
                # load), and it is already bounded by the client's read timeout.
                deadline: float | None = None
                # A drain, not a consumer: the SDK accumulates the deltas, and a
                # partially parsed section is no use to this function.
                for event in stream:
                    if deadline is None:
                        deadline = time.monotonic() + config.llm_stream_deadline
                    if ttft_ms is None and event.type == "content.delta":
                        ttft_ms = _elapsed_ms(started)
                        logger.info(
                            "internal_sentiment_split.analyze.time_to_first_token",
                            file=file_name,
                            section=section_key,
                            attempt=attempt,
                            ttft_ms=ttft_ms,
                        )
                    if time.monotonic() > deadline:
                        # Inside the with, so the manager still closes the response
                        # and releases the connection on the way out.
                        raise StreamDeadlineError(
                            f"stream exceeded {config.llm_stream_deadline:.0f}s"
                        )
                res = stream.get_final_completion()
            parsed = res.choices[0].message.parsed
            if parsed is None:
                # Refusal, content filter, or truncated JSON. No refusal text (it
                # can echo the call); the response rides on the exception the way
                # LengthFinishReasonError.completion does.
                err = ValueError("no parsed content")
                err.completion = res
                raise err
            # Separates a queued request from a slow one: a large ttft_ms with a small
            # stream_ms means the backend sat in a queue, which no client change fixes.
            logger.info(
                "internal_sentiment_split.section.stream",
                file=file_name,
                section=section_key,
                attempt=attempt,
                ttft_ms=ttft_ms,
                stream_ms=_elapsed_ms(started),
            )
            break
        except ValidationError as e:
            # Field paths only: pydantic echoes the offending value -- here the
            # model's own transcript of a customer call -- into errors() and str(e).
            res = None
            last_error = type(e).__name__
            logger.warning(
                "internal_sentiment_split.section.retry",
                file=file_name,
                section=section_key,
                attempt=attempt,
                error_type=last_error,
                error_fields=[
                    ".".join(str(part) for part in err["loc"]) for err in e.errors()
                ],
                ttft_ms=ttft_ms,
                stream_ms=_elapsed_ms(started),
            )
            if attempt < config.llm_max_attempts:
                time.sleep(_backoff_s(attempt))
        except (
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
            # Lengths, counts and enums only: content and refusal text echo the
            # customer call and must never reach the record. The reasoning/content
            # split separates a reasoning loop from a legitimately long response.
            completion = getattr(e, "completion", None)
            usage = getattr(completion, "usage", None)
            details = getattr(usage, "completion_tokens_details", None)
            choices = getattr(completion, "choices", None)
            message = choices[0].message if choices else None
            content = getattr(message, "content", None)
            logger.warning(
                "internal_sentiment_split.section.retry",
                file=file_name,
                section=section_key,
                attempt=attempt,
                error_type=last_error,
                completion_tokens=getattr(usage, "completion_tokens", None),
                reasoning_tokens=getattr(details, "reasoning_tokens", None),
                finish_reason=choices[0].finish_reason if choices else None,
                content_chars=None if content is None else len(content),
                refused=getattr(message, "refusal", None) is not None if message else None,
                ttft_ms=ttft_ms,
                stream_ms=_elapsed_ms(started),
            )
            if attempt < config.llm_max_attempts:
                time.sleep(_backoff_s(attempt))

    # Token cells stay None on failure: the SDK's parse exceptions do not
    # reliably carry usage, and blank means "not reported", never 0.
    if res is None or parsed is None:
        return None, {
            "attempts": attempts,
            "error_type": last_error,
            "prompt_tokens": None,
            "completion_tokens": None,
            "reasoning_tokens": None,
            "cached_tokens": None,
            "total_tokens": None,
        }

    usage = res.usage
    completion_details = getattr(usage, "completion_tokens_details", None)
    prompt_details = getattr(usage, "prompt_tokens_details", None)
    return parsed.model_dump(mode="json"), {
        "attempts": attempts,
        "error_type": None,
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "reasoning_tokens": getattr(completion_details, "reasoning_tokens", None),
        "cached_tokens": getattr(prompt_details, "cached_tokens", None),
        # Reported, never recomputed -- see the MetricsSchema docstring.
        "total_tokens": getattr(usage, "total_tokens", None),
    }


def analyze_transcript(
    client_llm: OpenAI,
    payload: dict[str, Any],
    transcript: str,
    file_name: str,
    config: Config,
    system_prompt: _SlicedPrompt,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Analyse one transcript by fanning out one call per :data:`SECTIONS` entry.

    All sections share the same user message and run concurrently -- the split's
    whole point is that seven short generations overlap where one long one could
    not. Each submitted task runs under a fresh contextvars snapshot, the
    ``_PendingFile.ctx`` pattern one level down: this function already executes on
    a file-pool worker, and the inner pool's threads would otherwise start blank.

    Any section exhausting its retry budget fails the whole file -- a
    ``ModelResponse`` with an invented section would be silently wrong, which is
    worse than an honest blank row (the ASR chunk rule). ``analysis_attempts`` is
    the sum over sections (= endpoint calls paid), and each token cell sums the
    sections that reported one -- all-None stays None. Unlike the unsplit
    pipeline, a failed file still reports the token cells its finished sections
    paid for; ``failed_stage``/``error_type`` mark the row failed either way.

    Returns:
        ``(content_dump, metrics)`` -- ``content_dump`` a
        ``ModelResponse.model_dump(mode="json")`` built from the merged sections
        on success, None on failure. Never raises for a model fault; the file
        keeps its stored transcript so a resume retries analysis alone.
    """
    user_content = _build_analysis_content(file_name, payload, transcript)
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=len(SECTIONS)) as pool:
        futures = {
            section.key: pool.submit(
                contextvars.copy_context().run,
                _analyze_section,
                client_llm,
                user_content,
                file_name,
                config,
                system_prompt.for_section(section.prompt_key),
                section.key,
                section.model,
            )
            for section in SECTIONS
        }
        results = {key: future.result() for key, future in futures.items()}

    section_metrics = {key: metrics for key, (_, metrics) in results.items()}
    attempts = sum(metrics["attempts"] for metrics in section_metrics.values())

    def summed(field_key: str) -> int | None:
        values = [
            metrics[field_key]
            for metrics in section_metrics.values()
            if metrics[field_key] is not None
        ]
        return sum(values) if values else None

    token_cells = {
        "analysis_prompt_tokens": summed("prompt_tokens"),
        "analysis_completion_tokens": summed("completion_tokens"),
        "analysis_reasoning_tokens": summed("reasoning_tokens"),
        "analysis_cached_tokens": summed("cached_tokens"),
        "analysis_total_tokens": summed("total_tokens"),
    }

    failed_sections = [s.key for s in SECTIONS if results[s.key][0] is None]
    if failed_sections:
        # The first failure in SECTIONS order names the row -- deterministic, so
        # two runs over the same faults produce the same Error Type cell.
        first = failed_sections[0]
        error_type = f"{first}:{section_metrics[first]['error_type'] or ''}"
        logger.error(
            "internal_sentiment_split.file.failed",
            file=file_name,
            attempts=attempts,
            sections=len(SECTIONS),
            failed_sections=failed_sections,
            error_type=error_type,
        )
        return None, {
            "status": STATUS_FAILED,
            "failed_stage": "analysis",
            "error_type": error_type,
            "analysis_attempts": attempts,
            **token_cells,
        }

    merged: dict[str, Any] = {}
    for section in SECTIONS:
        dump = results[section.key][0]
        if section.key == "classification":
            # The classification dump *is* the top level: service_number,
            # call_type, call_type_confident.
            merged.update(dump)
        else:
            merged[section.key] = dump
    try:
        content_dump = ModelResponse.model_validate(merged).model_dump(mode="json")
    except ValidationError as e:
        # Defensive: every piece already validated against its own schema, so
        # this firing means the section registry and ModelResponse disagree.
        # Field paths only -- values echo the customer call.
        logger.error(
            "internal_sentiment_split.file.failed",
            file=file_name,
            attempts=attempts,
            error_type="merge:ValidationError",
            error_fields=[
                ".".join(str(part) for part in err["loc"]) for err in e.errors()
            ],
        )
        return None, {
            "status": STATUS_FAILED,
            "failed_stage": "analysis",
            "error_type": "merge:ValidationError",
            "analysis_attempts": attempts,
            **token_cells,
        }

    # The file-level latency record: flush_chunk's file.analyzed has the token
    # sums, this has the fan-out wall clock the split exists to shrink.
    logger.info(
        "internal_sentiment_split.file.sections_completed",
        file=file_name,
        sections=len(SECTIONS),
        attempts=attempts,
        elapsed_ms=_elapsed_ms(started),
    )
    return content_dump, {
        "status": STATUS_SUCCESS,
        "failed_stage": "",
        "error_type": "",
        "analysis_attempts": attempts,
        **token_cells,
    }


@dataclass
class _PendingFile:
    """A file whose network chain (ASR -> label -> analysis) awaits a pool worker.

    ``ctx`` is a per-file contextvars snapshot taken on the main thread inside the
    run's TracedOperation blocks -- pool threads start with an empty context, so
    without it every worker log record would silently lose ``run_id``/``model``
    and its OTel span parent. One snapshot per file, never shared: a ``Context``
    cannot be entered twice concurrently. ``payload`` is set when a previous run's
    stored ASR dump is being reused (the worker then skips transcription);
    ``audio_bytes`` is None exactly in that case.
    """

    file_name: str
    stem: str
    audio_bytes: bytes | None
    payload: dict[str, Any] | None
    asr_metrics: dict[str, Any]
    system_prompt: _SlicedPrompt
    ctx: contextvars.Context


@dataclass
class _FileResult:
    """What one worker chain produced; the main thread checkpoints and appends it.

    ``transcribed`` marks a *fresh* ASR result -- the payload checkpoint must be
    uploaded even when a later stage failed, so a resume skips re-transcription,
    exactly as when the checkpoint landed mid-loop. ``metrics`` is the complete
    merged Matrix row (:data:`_METRICS_DEFAULTS` + every stage that ran).
    """

    payload: dict[str, Any] | None
    transcribed: bool
    transcript: str | None
    content_dump: dict[str, Any] | None
    metrics: dict[str, Any]


def _process_file_guarded(
    client_asr: OpenAI,
    client_llm: OpenAI,
    pending: _PendingFile,
    config: Config,
    transcript_prompt: str,
) -> _FileResult:
    """One file's ASR -> label -> analysis chain, with the loop's Exception backstop.

    Runs inside a pool worker under the submitter's copied context. The three stage
    functions never raise for a model fault by contract; the backstop here is for
    everything else, so one file's surprise costs the run a Failed row, not the
    sheet -- ``stage`` names the leg that blew up, generalising the analysis-only
    backstop the sequential loop had. Failure shapes are byte-identical to the
    sequential loop's early-``continue`` merges. Never logs transcript text.
    """
    payload = pending.payload
    asr_metrics = dict(pending.asr_metrics)
    transcribed = False
    transcript: str | None = None
    content_dump: dict[str, Any] | None = None
    label_metrics: dict[str, Any] = {}
    analysis_metrics: dict[str, Any] = {}
    stage = "asr"
    try:
        if payload is None:
            payload, asr_metrics = transcribe_file(
                client_asr, pending.audio_bytes, pending.file_name, config
            )
            if payload is None:
                return _FileResult(
                    None, False, None, None, {**_METRICS_DEFAULTS, **asr_metrics}
                )
            transcribed = True

        stage = "label"
        transcript, label_metrics = label_transcript(
            client_llm, payload, pending.file_name, config, transcript_prompt
        )
        if transcript is None:
            return _FileResult(
                payload,
                transcribed,
                None,
                None,
                {**_METRICS_DEFAULTS, **asr_metrics, **label_metrics},
            )

        stage = "analysis"
        content_dump, analysis_metrics = analyze_transcript(
            client_llm,
            payload,
            transcript,
            pending.file_name,
            config,
            pending.system_prompt,
        )
    except Exception as e:
        content_dump = None
        analysis_metrics = {"failed_stage": stage, "error_type": type(e).__name__}
        logger.warning(
            "internal_sentiment_split.file.failed",
            file=pending.file_name,
            stage=stage,
            error_type=type(e).__name__,
            exc_info=True,
        )

    # Merge order is defensive only: an ASR or label failure already returned
    # above, so a later stage cannot overwrite failed_stage.
    return _FileResult(
        payload,
        transcribed,
        transcript,
        content_dump,
        {**_METRICS_DEFAULTS, **asr_metrics, **label_metrics, **analysis_metrics},
    )


def _row_order(submitted: list[str], parsed: dict[str, Any]) -> list[str]:
    """Row order for both sheets, with every submitted/parsed disagreement logged.

    Thin wrapper over :func:`~src.google_model.usage_metrics.plan_row_order`; this
    adds the event namespace.
    """
    order, counts = plan_row_order(submitted, parsed)

    if counts["duplicates"]:
        logger.warning(
            "internal_sentiment_split.dataframe.duplicate_submitted", count=counts["duplicates"]
        )
    if counts["missing"]:
        # These rows land in the sheet as blanks; counts only -- a file name is
        # business data, and this log is indexed by Cloud Logging.
        logger.warning(
            "internal_sentiment_split.dataframe.blank_rows",
            submitted=len(submitted),
            missing=counts["missing"],
        )
    if counts["extra"]:
        logger.warning("internal_sentiment_split.dataframe.unsubmitted_rows", count=counts["extra"])

    return order


def build_output_df(items: list[dict[str, Any]], submitted: list[str]) -> pd.DataFrame:
    """Flatten parsed model responses into the output sheet's DataFrame.

    One row per *source* file: a file that failed any stage gets a blank row --
    blank rather than a sentinel, because the scorers compare raw cell text and
    ``"N/A"`` is a real grader-entered value in this sheet. Which files are blank,
    and why, is what the Matrix sheet is for.

    Args:
        items: ``{"file_name": str, "content": dict}`` rows, where ``content``
            is a ``ModelResponse.model_dump(mode="json")``.
        submitted: Every source file name. Defines the sheet's row set.

    Returns:
        A DataFrame with exactly :class:`OutputSchema`'s columns, in declaration
        order, coerced and validated, and ``len(submitted)`` rows.
    """
    # Column names come from OutputSchema; only these two disagree with
    # ModelResponse. The sheet's headers predate the schema rename
    # (company_verification -> customer_verification, self_service ->
    # true_application), so the sheet kept the old names while the schema moved on.
    column_renames = {
        "company_verification": "customer_verification",
        "self_service": "true_application",
    }
    # Declaration order, aliases included -- the sheet's column order is the schema's.
    output_columns = list(OutputSchema.to_schema().columns)

    by_name = {item["file_name"]: item for item in items}
    if collisions := len(items) - len(by_name):
        logger.warning("internal_sentiment_split.dataframe.duplicate_parsed", count=collisions)

    rows = []
    for no, name in enumerate(_row_order(submitted, by_name), start=1):
        row: dict[str, Any] = {"No": no, "Voice File Name": name}
        item = by_name.get(name)
        if item is not None:
            content = item["content"]
            values = {
                # Each criterion is {evaluation, reason}; the sheet keeps evaluation only.
                # The guard skips service_quality_performance_insight, a bare str.
                **{
                    key: crit["evaluation"]
                    for key, crit in content["service_quality"].items()
                    if isinstance(crit, dict)
                },
                **content["customer_sentiment"],
                "summary_story": content["customer_insight"]["summary_story"],
                "call_type": content["call_type"],
            }
            # Indexed, not .get() -- a column with no source is a mapping bug, and
            # .get() would ship a silent all-null column to the sheet.
            row.update({col: values[column_renames.get(col, col)] for col in output_columns[2:]})
        rows.append(row)

    # The explicit columns= makes an empty `items` yield a valid 0-row frame and
    # fills a blank row's absent keys with NaN instead of raising.
    output_df = OutputSchema.validate(pd.DataFrame(rows, columns=output_columns))
    logger.info(
        "internal_sentiment_split.dataframe.built",
        rows=len(output_df),
        columns=len(output_df.columns),
        submitted=len(submitted),
        parsed=len(by_name),
    )
    return output_df


def build_metrics_df(metrics_rows: list[dict[str, Any]], submitted: list[str]) -> pd.DataFrame:
    """Flatten per-file usage records into the Matrix sheet.

    Seeded from the same submitted list as :func:`build_output_df`, so the two
    sheets share a row set. Failed rows are included -- they still spent their
    calls. A file absent from ``metrics_rows`` falls back to
    :data:`_METRICS_DEFAULTS`.

    Args:
        metrics_rows: ``{"file_name", **metrics-keys}`` records shaped like
            :data:`_METRICS_DEFAULTS`.
        submitted: Every source file name. Defines the sheet's row set.

    Returns:
        A DataFrame with exactly :class:`MetricsSchema`'s columns, in declaration
        order, coerced and validated, and ``len(submitted)`` rows.
    """
    metrics_columns = list(MetricsSchema.to_schema().columns)
    by_name = {row["file_name"]: row for row in metrics_rows}

    records = []
    for no, name in enumerate(_row_order(submitted, by_name), start=1):
        row = {**_METRICS_DEFAULTS, **by_name.get(name, {})}
        # The two calls' reported totals, added. Absent stays absent: a file that
        # reached neither call reports blank, never 0.
        reported = [
            row["label_total_tokens"],
            row["analysis_total_tokens"],
        ]
        total_tokens = (
            sum(value for value in reported if value is not None)
            if any(value is not None for value in reported)
            else None
        )
        records.append(
            {
                "No": no,
                "Voice File Name": name,
                "Status": row["status"],
                "Failed Stage": row["failed_stage"],
                "Error Type": row["error_type"],
                "ASR Attempts": row["asr_attempts"],
                "Label Attempts": row["label_attempts"],
                "Analysis Attempts": row["analysis_attempts"],
                "Audio Seconds": row["audio_seconds"],
                "ASR Segments": row["asr_segments"],
                "Label Prompt Tokens": row["label_prompt_tokens"],
                "Label Completion Tokens": row["label_completion_tokens"],
                "Analysis Prompt Tokens": row["analysis_prompt_tokens"],
                "Analysis Completion Tokens": row["analysis_completion_tokens"],
                "Analysis Reasoning Tokens": row["analysis_reasoning_tokens"],
                "Analysis Cached Tokens": row["analysis_cached_tokens"],
                "Total Tokens": total_tokens,
            }
        )

    metrics_df = MetricsSchema.validate(pd.DataFrame(records, columns=metrics_columns))
    logger.info(
        "internal_sentiment_split.metrics.built",
        rows=len(metrics_df),
        columns=len(metrics_df.columns),
    )
    return metrics_df


def build_summary_df(
    *,
    run_id: str,
    model: str,
    label_model: str,
    asr_model: str,
    resumed: bool,
    counts: dict[str, int],
    metrics_df: pd.DataFrame,
    latency: dict[str, float],
) -> pd.DataFrame:
    """Render the run-level block: two columns, ``Metric`` and ``Value``.

    Shaped like the google pipeline's summary sheet so the two workbooks read side
    by side; the content is this pipeline's (OpenAI token totals, audio duration,
    per-stage failure split). ``label_model`` is recorded separately because it is
    held fixed while ``model`` is the variable under evaluation.

    Args:
        counts: ``{"source_files", "resumed_files", "transcribed_files",
            "labelled_files", "analyzed_files"}``.
        metrics_df: :func:`build_metrics_df`'s frame -- the same numbers the
            Matrix sheet shows, so the two cannot disagree.

    Returns:
        A two-column DataFrame; None values stay None so cells render blank.
    """
    files = len(metrics_df)
    succeeded = int((metrics_df["Status"] == STATUS_SUCCESS).sum())
    asr_failed = int((metrics_df["Failed Stage"] == "asr").sum())
    label_failed = int((metrics_df["Failed Stage"] == "label").sum())
    analysis_failed = int((metrics_df["Failed Stage"] == "analysis").sum())
    with_usage = int(metrics_df["Total Tokens"].notna().sum())
    token_columns = (
        "Label Prompt Tokens",
        "Label Completion Tokens",
        "Analysis Prompt Tokens",
        "Analysis Completion Tokens",
        "Analysis Reasoning Tokens",
        "Analysis Cached Tokens",
        "Total Tokens",
    )
    # int()/float() rather than the pandas scalar: neither openpyxl nor structlog
    # should meet a numpy type.
    totals = {column: int(metrics_df[column].sum()) for column in token_columns}
    audio_seconds = float(metrics_df["Audio Seconds"].sum())

    rows: list[tuple[str, Any]] = [
        ("Run ID", run_id),
        ("Model", model),
        ("Label Model", label_model),
        ("ASR Model", asr_model),
        ("Resumed", resumed),
        ("Source Files", counts["source_files"]),
        ("Resumed Files", counts["resumed_files"]),
        ("Transcribed Files", counts["transcribed_files"]),
        ("Labelled Files", counts["labelled_files"]),
        ("Analyzed Files", counts["analyzed_files"]),
        ("Succeeded", succeeded),
        ("Failed", files - succeeded),
        ("Success Rate", round(succeeded / files, 4) if files else 0.0),
        ("ASR Failures", asr_failed),
        ("Label Failures", label_failed),
        ("Analysis Failures", analysis_failed),
        ("Total Audio Seconds", round(audio_seconds, 1)),
        ("Total ASR Segments", int(metrics_df["ASR Segments"].sum())),
        ("Files With Usage", with_usage),
        *totals.items(),
        (
            "Avg Total Tokens / File",
            round(totals["Total Tokens"] / with_usage, 1) if with_usage else 0.0,
        ),
        (
            # The number that makes two models comparable when their file sets
            # differ in length, which is what a partially-failed run produces.
            "Avg Tokens / Audio Minute",
            round(totals["Total Tokens"] / (audio_seconds / 60), 1) if audio_seconds else 0.0,
        ),
        ("Files Elapsed (ms)", latency["files_ms"]),
        ("Results Elapsed (ms)", latency["results_ms"]),
    ]
    return pd.DataFrame(rows, columns=["Metric", "Value"])


def run(config: Config | None = None):
    tz = "Asia/Bangkok"
    run_dt = datetime.now(ZoneInfo(tz))
    run_date = run_dt.strftime("%Y-%m-%d")
    config = config if config is not None else Config()
    # Colons are forbidden in SharePoint item names and are Graph's own path
    # delimiter; one string serves the log's run_id, the GCS prefixes and the
    # SharePoint folder.
    run_id = config.run_id or run_dt.strftime("%Y-%m-%d_%H-%M-%S")
    resuming = config.run_id is not None

    payload_prefix = f"{config.gcs_payload_path}/{run_id}"
    dest_prefix = f"{config.gcs_dest_path}/{run_id}"
    dest_gcs_uri = f"gs://{config.gcs_bucket}/{dest_prefix}"

    # run_id / model are bound as contextvars for the whole block, so every record
    # the hook modules emit underneath carries them without being passed through.
    with TracedOperation(
        "internal_sentiment_split.run",
        run_id=run_id,
        model=config.model_name,
        model_asr=config.asr_model_name,
    ):
        # Every destination this run writes to, recorded before any of them exist --
        # so a run never leaves its output somewhere unrecorded, even if preempted.
        logger.info(
            "internal_sentiment_split.run.starting",
            run_id=run_id,
            resuming=resuming,
            timezone=tz,
            bucket=config.gcs_bucket,
            src_prefix=config.gcs_src_path,
            payload_prefix=payload_prefix,
            dest_uri=dest_gcs_uri,
            sharepoint_dest=config.dest_file,
            model=config.model_name,
            asr_model=config.asr_model_name,
            concurrency=config.llm_concurrency,
        )

        client_gcs = GCSModule(
            project_id=os.environ["GCP_PROJECT_ID"],
            timezone=tz,
        )
        # Owns the XLSX and transcript uploads. The constructor acquires a token
        # eagerly, so bad config fails before hours of ASR/LLM calls are paid for.
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
            # The SDK's retries sit *inside* each stage's seed-nudge loop, so the two
            # multiply; the default 2 made one file cost up to 9 requests. The ASR
            # client below keeps the default -- different leg, different failure mode.
            max_retries=config.llm_max_retries,
            http_client=httpx.Client(verify=False),
        )

        client_asr = OpenAI(
            api_key=os.environ["ASR_API_KEY"],
            base_url=config.asr_base_url,
            # timeout=config.asr_timeout,
            http_client=httpx.Client(verify=False),
        )

        # Ground truth read before any spend -- a renamed tab or dropped column
        # must fail here, not after the whole file loop.
        with io.BytesIO(client_sb.download_file(config.gt_src_file).content) as f:
            book = pd.ExcelFile(f, engine="openpyxl")
            if config.gt_sheet_name not in book.sheet_names:
                logger.error(
                    "internal_sentiment_split.run.aborted",
                    reason="gt_sheet_missing",
                    file=config.gt_src_file,
                    sheet=config.gt_sheet_name,
                    # Sheet names only -- never cell content.
                    available=book.sheet_names,
                )
                return
            # Sentinel detection runs before dtype coercion: pandas' default NA set
            # contains 'N/A' and friends, which are real grader-entered values here.
            gt_df = book.parse(
                config.gt_sheet_name,
                dtype=str,
                header=0,
                keep_default_na=False,
                na_values=[""],
            )

        # strict=False admits extra columns but not missing ones, so this is a real
        # gate on the report's shape rather than a formality.
        try:
            gt_df = InputGTSchema.validate(gt_df)
        except (SchemaError, SchemaErrors) as e:
            # Computed here rather than read off the exception: e.data and
            # e.failure_cases carry the offending cell values.
            missing = sorted(set(InputGTSchema.to_schema().columns) - set(gt_df.columns))
            logger.error(
                "internal_sentiment_split.run.aborted",
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
            "internal_sentiment_split.gt.loaded",
            file=config.gt_src_file,
            sheet=config.gt_sheet_name,
            rows=len(gt_df),
        )

        # recursive=False: only immediate children of gcs_src_path.
        files = client_gcs.list_files(
            bucket_name=config.gcs_bucket,
            prefix=config.gcs_src_path,
        )

        if not files:
            logger.error(
                "internal_sentiment_split.run.aborted",
                reason="no_source_files",
                bucket=config.gcs_bucket,
                prefix=config.gcs_src_path,
            )
            return

        logger.info(
            "internal_sentiment_split.source.listed",
            bucket=config.gcs_bucket,
            prefix=config.gcs_src_path,
            count=len(files),
        )

        with open(config.system_inbound_prompt_file, 'r', encoding='utf-8') as f:
            system_in_prompt = f.read().replace("{date}", run_date)
        with open(config.system_outbound_prompt_file, 'r', encoding='utf-8') as f:
            system_out_prompt = f.read().replace("{date}", run_date)
        # No {date} substitution: the labelling call formats speech, it does not
        # reason about when the call happened.
        with open(config.system_transcript_prompt_file, 'r', encoding='utf-8') as f:
            transcript_prompt = f.read()

        # Sliced once per run; every per-topic call reuses these. A prompt edit
        # that renames or drops a Category header must abort here, before any
        # ASR/LLM spend -- _SlicedPrompt.for_section indexes without a fallback.
        sliced_in_prompt = _split_system_prompt(system_in_prompt)
        sliced_out_prompt = _split_system_prompt(system_out_prompt)
        for prompt_file, sliced in (
            (config.system_inbound_prompt_file, sliced_in_prompt),
            (config.system_outbound_prompt_file, sliced_out_prompt),
        ):
            missing = sorted(EXPECTED_PROMPT_SECTIONS - sliced.sections.keys())
            if missing:
                logger.error(
                    "internal_sentiment_split.run.aborted",
                    reason="prompt_section_missing",
                    file=prompt_file,
                    missing_sections=missing,
                    # Section names only -- never prompt text.
                    found_sections=sorted(sliced.sections),
                )
                return

        logger.debug(
            "internal_sentiment_split.prompts.loaded",
            inbound_chars=len(system_in_prompt),
            outbound_chars=len(system_out_prompt),
            transcript_chars=len(transcript_prompt),
            inbound_sections=len(sliced_in_prompt.sections),
            outbound_sections=len(sliced_out_prompt.sections),
            run_date=run_date,
        )

        # Resume state, listed once -- per-file existence checks would cost one
        # round-trip per file.
        done_by_stem: dict[str, str] = {}
        payload_by_stem: dict[str, str] = {}
        if resuming:
            done_by_stem = {
                Path(obj).stem: obj
                for obj in client_gcs.list_files(
                    bucket_name=config.gcs_bucket, prefix=dest_prefix, pattern=r"\.json$"
                )
            }
            payload_by_stem = {
                Path(obj).stem: obj
                for obj in client_gcs.list_files(
                    bucket_name=config.gcs_bucket, prefix=payload_prefix, pattern=r"\.json$"
                )
            }
            logger.info(
                "internal_sentiment_split.resume.plan",
                source_files=len(files),
                done=len(done_by_stem),
                transcribed=len(payload_by_stem),
            )

        pattern = re.compile(r'.*_(IN|OUT)(_.*\.wav$|\.wav$)')
        items: list[dict[str, Any]] = []
        transcripts: list[dict[str, Any]] = []
        metrics_rows: list[dict[str, Any]] = []
        resumed_files = 0
        transcribed_files = 0
        labelled_files = 0
        analyzed_files = 0

        files_started = time.monotonic()
        with TracedOperation("internal_sentiment_split.files"):
            # The chunk buffer holds at most llm_concurrency pending files: only
            # the network chains (ASR -> label -> analysis) fan out; downloads,
            # checkpoints and appends stay on this thread.
            chunk: list[_PendingFile] = []

            def run_task(pending: _PendingFile) -> _FileResult:
                # ctx.run restores run_id/model/operation and OTel span parenting
                # inside the pool thread; the Exception backstop lives in
                # _process_file_guarded, so pool.map can never see a raise.
                return pending.ctx.run(
                    _process_file_guarded,
                    client_asr,
                    client_llm,
                    pending,
                    config,
                    transcript_prompt,
                )

            def flush_chunk() -> None:
                # The chunk's chains run in parallel; the rest is sequential here.
                # Checkpoints land only after the whole chunk returns, so a crash
                # mid-chunk re-pays at most len(chunk) files' calls on resume; the
                # per-stage info logs now emit here, at flush time, same names and
                # fields as the sequential loop's.
                nonlocal transcribed_files, labelled_files, analyzed_files
                results = list(pool.map(run_task, chunk))  # order-preserving
                for pending, result in zip(chunk, results, strict=True):
                    metrics = result.metrics
                    if result.transcribed and result.payload is not None:
                        # Uploaded even when a later stage failed, so a resume
                        # skips re-transcription -- the mid-loop semantics.
                        client_gcs.upload_file(
                            bucket_name=config.gcs_bucket,
                            upload_path=f"{payload_prefix}/{pending.stem}.json",
                            content=json.dumps(
                                result.payload, ensure_ascii=False
                            ).encode("utf-8"),
                            mime_type="application/json",
                        )
                        transcribed_files += 1
                        logger.info(
                            "internal_sentiment_split.file.transcribed",
                            file=pending.file_name,
                            attempts=metrics["asr_attempts"],
                            audio_seconds=metrics["audio_seconds"],
                            segments=metrics["asr_segments"],
                        )
                    if result.transcript is not None:
                        labelled_files += 1
                        logger.info(
                            "internal_sentiment_split.file.labelled",
                            file=pending.file_name,
                            attempts=metrics["label_attempts"],
                            # Turn count, never the text.
                            turns=len(result.transcript.splitlines()),
                            prompt_tokens=metrics["label_prompt_tokens"],
                            completion_tokens=metrics["label_completion_tokens"],
                        )
                    if result.content_dump is not None:
                        # (f) Checkpoint before the in-memory append, so a
                        # preemption never loses a paid-for file. Self-contained;
                        # the transcript is stored beside `content` --
                        # ModelResponse no longer carries one.
                        client_gcs.upload_file(
                            bucket_name=config.gcs_bucket,
                            upload_path=f"{dest_prefix}/{pending.stem}.json",
                            content=json.dumps(
                                {
                                    "file_name": pending.file_name,
                                    "transcript": result.transcript,
                                    "content": result.content_dump,
                                    "metrics": metrics,
                                },
                                ensure_ascii=False,
                            ).encode("utf-8"),
                            mime_type="application/json",
                        )
                        items.append(
                            {"file_name": pending.file_name, "content": result.content_dump}
                        )
                        transcripts.append(
                            {"file_name": pending.file_name, "transcript": result.transcript}
                        )
                        analyzed_files += 1
                        logger.info(
                            "internal_sentiment_split.file.analyzed",
                            file=pending.file_name,
                            attempts=metrics["analysis_attempts"],
                            prompt_tokens=metrics["analysis_prompt_tokens"],
                            completion_tokens=metrics["analysis_completion_tokens"],
                        )
                    # (g) Either way the file keeps its Matrix row. No dest JSON is
                    # written for a failure, so a resume retries exactly those files.
                    metrics_rows.append({"file_name": pending.file_name, **metrics})
                chunk.clear()  # drops the audio_bytes references

            # max_workers=1 is today's sequential run through the same code path:
            # chunk size 1 -- one download, one chain, one checkpoint pass.
            with ThreadPoolExecutor(max_workers=config.llm_concurrency) as pool:
                for file in files:
                    file_name = Path(file).name
                    stem = Path(file).stem

                    # (a) Fully done in a previous run: reuse the stored result.
                    # Never pooled -- no network chain to overlap.
                    if stem in done_by_stem:
                        stored = json.loads(
                            client_gcs.download_file(
                                bucket_name=config.gcs_bucket,
                                file_path=done_by_stem[stem],
                            )
                        )
                        # Re-validated rather than trusted: the stored dump is the
                        # resume contract, and drift should fail loudly here.
                        content = ModelResponse.model_validate(stored["content"])
                        content_dump = content.model_dump(mode="json")
                        items.append({"file_name": file_name, "content": content_dump})
                        # The transcript rides beside `content`, not inside it --
                        # ModelResponse no longer carries one.
                        transcripts.append(
                            {"file_name": file_name, "transcript": stored["transcript"]}
                        )
                        metrics_rows.append({"file_name": file_name, **stored["metrics"]})
                        resumed_files += 1
                        logger.info("internal_sentiment_split.file.resumed", file=file_name)
                        continue

                    # (b) Transcribed in a previous run: reuse the stored payload;
                    # the file still pools for label + analysis.
                    payload: dict[str, Any] | None = None
                    asr_metrics: dict[str, Any] = {}
                    if stem in payload_by_stem:
                        candidate = json.loads(
                            client_gcs.download_file(
                                bucket_name=config.gcs_bucket,
                                file_path=payload_by_stem[stem],
                            )
                        )
                        if _asr_payload_valid(candidate):
                            payload = candidate
                            # asr_attempts stays absent: this run made no ASR call,
                            # and blank means "not reported", never 0.
                            asr_metrics = _asr_stats(payload)
                            logger.info(
                                "internal_sentiment_split.file.payload_reused", file=file_name
                            )
                        else:
                            logger.warning(
                                "internal_sentiment_split.file.payload_invalid",
                                file=file_name,
                                stored_type=type(candidate).__name__,
                            )

                    # (c) No stored payload: the worker will transcribe; only the
                    # download happens here. A 5-minute 16 kHz WAV is ~10 MB --
                    # the chunk buffer bounds how many are held at once.
                    audio_bytes: bytes | None = None
                    if payload is None:
                        audio_bytes = client_gcs.download_file(
                            bucket_name=config.gcs_bucket,
                            file_path=file,
                        )

                    # (d) A name matching neither IN nor OUT silently takes the
                    # outbound prompt, so the fallback is recorded rather than left
                    # to be inferred from the results. Fires at enqueue time -- now
                    # also for files whose chain later fails at ASR or label.
                    direction_match = pattern.match(file_name)
                    direction = direction_match.group(1) if direction_match else None
                    if direction is None:
                        logger.warning(
                            "internal_sentiment_split.direction.unmatched",
                            file=file_name,
                            fallback="OUT",
                        )

                    # The context snapshot is per file, taken here so worker logs
                    # inherit everything bound by the surrounding TracedOperations.
                    chunk.append(
                        _PendingFile(
                            file_name=file_name,
                            stem=stem,
                            audio_bytes=audio_bytes,
                            payload=payload,
                            asr_metrics=asr_metrics,
                            system_prompt=(
                                sliced_in_prompt if direction == "IN" else sliced_out_prompt
                            ),
                            ctx=contextvars.copy_context(),
                        )
                    )
                    del audio_bytes  # the _PendingFile now owns the only reference

                    if len(chunk) >= config.llm_concurrency:
                        flush_chunk()

                if chunk:
                    flush_chunk()

        files_ms = _elapsed_ms(files_started)
        logger.info(
            "internal_sentiment_split.files.completed",
            source_files=len(files),
            resumed=resumed_files,
            transcribed=transcribed_files,
            labelled=labelled_files,
            analyzed=analyzed_files,
            elapsed_ms=files_ms,
        )

        with TracedOperation("internal_sentiment_split.results"):
            results_started = time.monotonic()

            # Both sheets' row set: 300 files in produce 300 rows out however many
            # the endpoints failed on.
            submitted = [Path(file).name for file in files]

            output_df = build_output_df(items, submitted)
            metrics_df = build_metrics_df(metrics_rows, submitted)
            summary_df = build_summary_df(
                run_id=run_id,
                model=config.model_name,
                label_model=config.label_model_name,
                asr_model=config.asr_model_name,
                resumed=resuming,
                counts={
                    "source_files": len(files),
                    "resumed_files": resumed_files,
                    "transcribed_files": transcribed_files,
                    "labelled_files": labelled_files,
                    "analyzed_files": analyzed_files,
                },
                metrics_df=metrics_df,
                latency={"files_ms": files_ms, "results_ms": _elapsed_ms(results_started)},
            )

            succeeded = int((metrics_df["Status"] == STATUS_SUCCESS).sum())
            total_tokens = int(metrics_df["Total Tokens"].sum())
            # The one record that says what the run cost. Counts, durations and
            # token totals are not PII: no transcript, no model output.
            logger.info(
                "internal_sentiment_split.usage.summary",
                files=len(metrics_df),
                succeeded=succeeded,
                failed=len(metrics_df) - succeeded,
                asr_failed=int((metrics_df["Failed Stage"] == "asr").sum()),
                label_failed=int((metrics_df["Failed Stage"] == "label").sum()),
                analysis_failed=int((metrics_df["Failed Stage"] == "analysis").sum()),
                audio_seconds=round(float(metrics_df["Audio Seconds"].sum()), 1),
                label_prompt_tokens=int(metrics_df["Label Prompt Tokens"].sum()),
                label_completion_tokens=int(metrics_df["Label Completion Tokens"].sum()),
                analysis_prompt_tokens=int(metrics_df["Analysis Prompt Tokens"].sum()),
                analysis_completion_tokens=int(
                    metrics_df["Analysis Completion Tokens"].sum()
                ),
                analysis_reasoning_tokens=int(metrics_df["Analysis Reasoning Tokens"].sum()),
                analysis_cached_tokens=int(metrics_df["Analysis Cached Tokens"].sum()),
                total_tokens=total_tokens,
            )

            dest_file_path = f"{config.dest_file}/{run_id}"
            output_path = f"{dest_file_path}/{config.output_file_name}"

            # gt_df was read and validated before any spend -- see the pre-flight above.
            with io.BytesIO() as output_buffer:
                with pd.ExcelWriter(output_buffer, engine='openpyxl') as writer:
                    gt_df.to_excel(writer, index=False, sheet_name=config.gt_sheet_name)
                    output_df.to_excel(writer, index=False, sheet_name=config.output_sheet_name)
                    # Written last, so the delivered workbook still opens on the
                    # ground truth and the ops sheets sit behind the result tabs.
                    summary_df.to_excel(
                        writer, index=False, sheet_name=config.metrics_summary_sheet_name
                    )
                    metrics_df.to_excel(writer, index=False, sheet_name=config.metrics_sheet_name)
                    # Metric names run long; the default width truncates them mid-word.
                    summary_sheet = writer.sheets[config.metrics_summary_sheet_name]
                    summary_sheet.column_dimensions["A"].width = 32
                    summary_sheet.column_dimensions["B"].width = 26
                workbook_byte = output_buffer.getvalue()

            # Guarded: the transcripts are in memory and the results in GCS, so a
            # workbook fault (lock, 503, illegal path) must not take them with it.
            # SharePointModule already logged the cause at its single ERROR site.
            output_uploaded = False
            try:
                client_sb.upload_file(upload_path=output_path, content=workbook_byte)
                output_uploaded = True
                logger.info(
                    "internal_sentiment_split.output.uploaded",
                    path=output_path,
                    rows=len(output_df),
                    bytes=len(workbook_byte),
                )
            except Exception:
                logger.warning(
                    "internal_sentiment_split.output.skipped",
                    path=output_path,
                    rows=len(output_df),
                )
            del workbook_byte  # free the workbook before the per-transcript upload loop

            transcript_errors = 0
            for transcript in transcripts:
                # <stem>.txt, not <name>.txt -- every source name ends in .wav,
                # which would otherwise produce "a_IN.wav.txt".
                transcript_path = f"{dest_file_path}/{Path(transcript['file_name']).stem}.txt"
                try:
                    # The transcript text itself, not json.dumps of the row -- the
                    # file is read by people, and the file name carries the other key.
                    client_sb.upload_file(
                        upload_path=transcript_path,
                        content=transcript["transcript"].encode("utf-8"),
                    )
                except Exception:
                    transcript_errors += 1
                    # SharePointModule already logged the fault; this records only that
                    # the run carried on, so one locked path does not cost the set.
                    logger.warning(
                        "internal_sentiment_split.transcript.skipped",
                        file=transcript["file_name"],
                    )

            logger.info(
                "internal_sentiment_split.transcripts.uploaded",
                count=len(transcripts) - transcript_errors,
                failed=transcript_errors,
                directory=dest_file_path,
            )

        # Counts at every stage: a run that lists 100 files and writes 3 is a bad
        # run, and "rows=100" alone reported it as a good one.
        logger.info(
            "internal_sentiment_split.run.completed",
            run_id=run_id,
            source_files=len(files),
            resumed=resumed_files,
            transcribed=transcribed_files,
            labelled=labelled_files,
            analyzed=analyzed_files,
            written=len(output_df),
            succeeded=succeeded,
            failed=len(metrics_df) - succeeded,
            transcripts=len(transcripts) - transcript_errors,
            output_path=output_path,
            # So a partial run reads as partial in one record.
            output_uploaded=output_uploaded,
            total_tokens=total_tokens,
        )
