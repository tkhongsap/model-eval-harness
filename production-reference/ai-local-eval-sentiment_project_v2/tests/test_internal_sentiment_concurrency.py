"""Offline tests for the sentiment pipeline's ASR chunking and opt-in concurrency.

Covers the SENTI_LLM_CONCURRENCY env read (fail-soft, floor 1), the WAV
splitter/merger that keeps uploads under the ~50 MB nginx cap, the
transcribe_file chunk orchestration, and the whole-file worker wrapper's
Exception backstop. No network, no GCS; WAV fixtures are synthesized in-memory
at low framerates (the helpers scale their windows by framerate) and are never
resampled, so torch/torchaudio stay unimported.
"""

from __future__ import annotations

import contextvars
import io
import struct
import wave
from types import SimpleNamespace
from typing import Any

import httpx
import numpy as np
import openai
import pytest

from src.google_model.usage_metrics import STATUS_FAILED, STATUS_SUCCESS
from src.local_model.sentiment import internal_asr_llm_output as mod

ENV = "SENTI_LLM_CONCURRENCY"

FRAMERATE = 1000  # tiny fixtures; _SPLIT_* windows scale by framerate
# usable = cap - 44 (header) - 20_000 (10 s of slack frames) = 70_000 bytes, so a
# 60_000-frame (120_000-byte) file always splits into exactly 2 chunks.
TWO_CHUNK_CAP = 90_044


class _RecorderLogger:
    """Stands in for the module logger; capture_logs is unreliable with cached loggers."""

    def __init__(self):
        self.events: list[tuple[str, str, dict]] = []

    def _record(self, level, event, **kwargs):
        self.events.append((level, event, kwargs))

    def debug(self, event, **kwargs):
        self._record("debug", event, **kwargs)

    def info(self, event, **kwargs):
        self._record("info", event, **kwargs)

    def warning(self, event, **kwargs):
        self._record("warning", event, **kwargs)

    def error(self, event, **kwargs):
        self._record("error", event, **kwargs)

    def names(self, event: str) -> list[tuple[str, str]]:
        return [(level, name) for level, name, _ in self.events if name == event]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)


@pytest.fixture
def recorder(monkeypatch):
    stub = _RecorderLogger()
    monkeypatch.setattr(mod, "logger", stub)
    return stub


def _loud(n: int) -> np.ndarray:
    """Constant-energy signal so a zeroed patch is the unique quietest window."""
    return np.tile(np.array([9000, -9000], dtype=np.int16), n // 2 + 1)[:n].copy()


def _make_wav(samples: np.ndarray, framerate: int = FRAMERATE) -> bytes:
    with io.BytesIO() as buf:
        with wave.open(buf, "wb") as wav_out:
            wav_out.setnchannels(1)
            wav_out.setsampwidth(2)
            wav_out.setframerate(framerate)
            wav_out.writeframes(samples.astype("<i2").tobytes())
        return buf.getvalue()


def _frames(wav_bytes: bytes) -> bytes:
    with wave.open(io.BytesIO(wav_bytes), "rb") as wav_in:
        return wav_in.readframes(wav_in.getnframes())


def _mulaw_wav(data: bytes, framerate: int = 8000) -> bytes:
    fmt = struct.pack("<HHIIHH", 7, 1, framerate, framerate, 1, 8)
    body = (
        b"fmt " + struct.pack("<I", len(fmt)) + fmt
        + b"data" + struct.pack("<I", len(data)) + data
    )
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WAVE" + body


def _payload(text: str, start: float = 0.0, end: float = 1.0) -> dict[str, Any]:
    return {
        "task": "transcribe",
        "language": "th",
        "duration": end,
        "text": text,
        "segments": [{"id": 0, "start": start, "end": end, "text": text}],
    }


class _StubResponse:
    def __init__(self, payload):
        self._payload = payload

    def model_dump(self, mode="json"):
        return self._payload


class _StubTranscriptions:
    def __init__(self, payloads, uploads):
        self._payloads = payloads
        self._uploads = uploads

    def create(self, *, file, model, **kwargs):
        self._uploads.append(file)
        return _StubResponse(self._payloads.pop(0))


class _StubASR:
    """Duck-typed OpenAI client: records every multipart tuple it was handed."""

    def __init__(self, payloads):
        self.uploads: list[tuple] = []
        self.audio = SimpleNamespace(
            transcriptions=_StubTranscriptions(list(payloads), self.uploads)
        )


class _RaisingTranscriptions:
    def __init__(self, exc):
        self._exc = exc
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        raise self._exc


def _raising_asr(exc):
    return SimpleNamespace(audio=SimpleNamespace(transcriptions=_RaisingTranscriptions(exc)))


def _api_status_error(status_code: int) -> openai.APIStatusError:
    response = httpx.Response(
        status_code, request=httpx.Request("POST", "http://asr/v1/audio/transcriptions")
    )
    return openai.APIStatusError("rejected", response=response, body=None)


# --- SENTI_LLM_CONCURRENCY env factory -------------------------------------------------


def test_concurrency_unset_defaults_to_one(recorder):
    assert mod._senti_llm_concurrency() == 1
    assert recorder.events == []


def test_concurrency_blank_is_silently_sequential(monkeypatch, recorder):
    monkeypatch.setenv(ENV, "")
    assert mod._senti_llm_concurrency() == 1
    assert recorder.events == []


def test_concurrency_reads_valid_value(monkeypatch):
    monkeypatch.setenv(ENV, "4")
    assert mod._senti_llm_concurrency() == 4


@pytest.mark.parametrize("bad", ["0", "-2", "abc", "2.5"])
def test_concurrency_invalid_warns_and_falls_back(monkeypatch, recorder, bad):
    monkeypatch.setenv(ENV, bad)
    assert mod._senti_llm_concurrency() == 1
    assert [
        (level, event) for level, event, _ in recorder.events
    ] == [("warning", "internal_sentiment.config.concurrency_invalid")]


def test_config_reads_env_at_instantiation(monkeypatch):
    # default_factory runs at Config() time -- what main.py's menu preview shows
    # is what the run uses.
    monkeypatch.setenv(ENV, "3")
    assert mod.Config().llm_concurrency == 3
    monkeypatch.setenv(ENV, "5")
    assert mod.Config().llm_concurrency == 5


# --- WAV splitting ---------------------------------------------------------------------


def test_split_two_chunks_cuts_at_quiet_point():
    samples = _loud(60_000)
    samples[34_000:34_250] = 0  # aligns with a search window; unique energy minimum
    wav = _make_wav(samples)

    chunks, duration = mod._split_pcm16_wav(wav, TWO_CHUNK_CAP)

    assert duration == 60.0
    # Cut = quiet-window start + half an RMS window (its centre).
    assert [start for _, start in chunks] == [0.0, 34.125]
    assert all(len(chunk_bytes) <= TWO_CHUNK_CAP for chunk_bytes, _ in chunks)
    reassembled = b"".join(_frames(chunk_bytes) for chunk_bytes, _ in chunks)
    assert reassembled == samples.tobytes()


def test_split_three_chunks_monotonic_cuts():
    samples = _loud(180_000)
    samples[55_000:55_250] = 0
    samples[126_000:126_250] = 0
    wav = _make_wav(samples)
    cap = 150_044  # usable 130_000 -> ceil(360_000 / 130_000) = 3 chunks

    chunks, duration = mod._split_pcm16_wav(wav, cap)

    assert duration == 180.0
    starts = [start for _, start in chunks]
    assert starts == [0.0, 55.125, 126.125]
    assert starts == sorted(starts)
    assert all(len(chunk_bytes) <= cap for chunk_bytes, _ in chunks)
    reassembled = b"".join(_frames(chunk_bytes) for chunk_bytes, _ in chunks)
    assert reassembled == samples.tobytes()


def test_split_edge_nudge_stays_under_cap():
    # Quietest point at the far edge of the +10 s search window: the slack term
    # in the chunk-count math is what keeps the stretched chunk under the cap.
    samples = _loud(60_000)
    samples[39_750:40_000] = 0
    wav = _make_wav(samples)

    chunks, _ = mod._split_pcm16_wav(wav, TWO_CHUNK_CAP)

    assert [start for _, start in chunks] == [0.0, 39.875]
    assert all(len(chunk_bytes) <= TWO_CHUNK_CAP for chunk_bytes, _ in chunks)


def test_split_absurd_cap_raises():
    with pytest.raises(ValueError):
        mod._split_pcm16_wav(_make_wav(_loud(60_000)), 1_000)


def test_min_energy_cut_frame_argmin_and_clamp():
    samples = _loud(3_000).reshape(-1, 1)
    samples[1_250:1_500] = 0
    cut = mod._min_energy_cut_frame(
        samples, target_frame=1_500, framerate=FRAMERATE, lo_frame=250, hi_frame=2_750
    )
    assert cut == 1_375  # quiet window start + rms_frames // 2

    # Region smaller than one RMS window: clamped target fallback.
    assert (
        mod._min_energy_cut_frame(
            samples, target_frame=1_500, framerate=FRAMERATE, lo_frame=100, hi_frame=100
        )
        == 100
    )


def test_ensure_splittable_pcm16_paths():
    pcm = _make_wav(_loud(100))
    assert mod._ensure_splittable_pcm16(pcm) == (pcm, False)

    data = bytes(range(256))
    converted, decoded = mod._ensure_splittable_pcm16(_mulaw_wav(data))
    assert decoded is True
    with wave.open(io.BytesIO(converted), "rb") as wav_in:
        assert wav_in.getsampwidth() == 2
        assert wav_in.getframerate() == 8000
        frames = wav_in.readframes(wav_in.getnframes())
    expected = mod._g711_decode_table(mod._WAVE_FORMAT_MULAW)[
        np.frombuffer(data, dtype=np.uint8)
    ]
    assert frames == expected.tobytes()

    with io.BytesIO() as buf:  # 8-bit PCM: wave opens it, but it is not splittable here
        with wave.open(buf, "wb") as wav_out:
            wav_out.setnchannels(1)
            wav_out.setsampwidth(1)
            wav_out.setframerate(8000)
            wav_out.writeframes(b"\x80" * 100)
        eight_bit = buf.getvalue()
    with pytest.raises(ValueError):
        mod._ensure_splittable_pcm16(eight_bit)


# --- payload merging -------------------------------------------------------------------


def test_merge_chunk_payloads_offsets_and_shape():
    first = _payload("hello", start=0.0, end=2.5)
    second = {**_payload("world", start=1.0, end=3.0), "language": "en"}

    merged = mod._merge_chunk_payloads([first, second], [0.0, 30.5], 60.0)

    assert merged["language"] == "th"  # first chunk wins
    assert merged["duration"] == 60.0  # caller's exact total, not a sum
    assert merged["text"] == "hello world"
    assert [segment["id"] for segment in merged["segments"]] == [0, 1]
    assert merged["segments"][0]["start"] == 0.0
    assert merged["segments"][1]["start"] == 31.5
    assert merged["segments"][1]["end"] == 33.5
    assert "usage" not in merged
    assert mod._asr_payload_valid(merged)
    assert mod._asr_stats(merged) == {"audio_seconds": 60.0, "asr_segments": 2}


# --- json-format payload normalization -------------------------------------------------


def test_wav_duration_seconds_exact_and_failsoft():
    assert mod._wav_duration_seconds(_make_wav(_loud(60_000))) == 60.0
    assert mod._wav_duration_seconds(b"not a riff wave") is None


def test_normalize_asr_payload_rebuilds_verbose_shape():
    wav = _make_wav(_loud(2_000))
    candidate = {"text": "hello", "logprobs": None, "usage": {"type": "duration", "seconds": 2.0}}

    normalized = mod._normalize_asr_payload(candidate, wav, mod.Config())

    assert normalized["duration"] == 2.0  # from the WAV header, not the endpoint
    assert normalized["segments"] == []
    assert normalized["language"] == "th"  # the language the request asked for
    assert normalized["text"] == "hello"
    assert normalized["usage"] == {"type": "duration", "seconds": 2.0}
    assert mod._asr_payload_valid(normalized)


def test_normalize_asr_payload_verbose_passthrough():
    verbose = _payload("hi")
    assert mod._normalize_asr_payload(verbose, b"", mod.Config()) is verbose


# --- transcribe_file orchestration -----------------------------------------------------


def test_transcribe_json_format_is_normalized(recorder):
    wav = _make_wav(_loud(3_000))
    client = _StubASR([{"text": "hello world", "logprobs": None, "usage": None}])
    config = mod.Config(asr_target_rate=None)

    payload, metrics = mod.transcribe_file(client, wav, "call_IN.wav", config)

    assert payload["segments"] == []
    assert payload["duration"] == 3.0
    assert payload["text"] == "hello world"
    assert metrics == {"asr_attempts": 1, "audio_seconds": 3.0, "asr_segments": 0}


def test_non_retryable_400_stops_after_one_attempt(recorder):
    client = _raising_asr(_api_status_error(400))
    config = mod.Config(asr_target_rate=None)

    payload, metrics = mod.transcribe_file(client, _make_wav(_loud(100)), "a_IN.wav", config)

    assert payload is None
    assert client.audio.transcriptions.calls == 1  # no budget burned on a sure loss
    assert metrics["asr_attempts"] == 1
    assert metrics["failed_stage"] == "asr"
    assert recorder.names("internal_sentiment.asr.retry") == [
        ("warning", "internal_sentiment.asr.retry")
    ]
    assert recorder.names("internal_sentiment.asr.failed") == [
        ("error", "internal_sentiment.asr.failed")
    ]


def test_transient_500_keeps_full_retry_budget(monkeypatch, recorder):
    monkeypatch.setattr("time.sleep", lambda seconds: None)  # skip the 1 s backoff
    client = _raising_asr(_api_status_error(500))
    config = mod.Config(asr_target_rate=None)

    payload, metrics = mod.transcribe_file(client, _make_wav(_loud(100)), "a_IN.wav", config)

    assert payload is None
    assert client.audio.transcriptions.calls == 3
    assert metrics["asr_attempts"] == 3


def test_transcribe_subcap_upload_byte_identical(recorder):
    wav = _make_wav(_loud(1_000))
    payload = _payload("hi")
    client = _StubASR([payload])
    config = mod.Config(asr_target_rate=None)

    result, metrics = mod.transcribe_file(client, wav, "call_IN.wav", config)

    assert result == payload
    assert metrics["asr_attempts"] == 1
    name, content, mime = client.uploads[0]
    assert name == "call_IN.wav"
    assert content == wav  # byte-identical to a run without chunking
    assert mime == "audio/wav"
    assert recorder.names("internal_sentiment.asr.split") == []


def test_transcribe_oversized_splits_and_merges(recorder):
    samples = _loud(60_000)
    samples[34_000:34_250] = 0
    wav = _make_wav(samples)
    client = _StubASR([_payload("part one", end=34.0), _payload("part two", end=25.0)])
    config = mod.Config(asr_target_rate=None, asr_max_upload_bytes=TWO_CHUNK_CAP)

    merged, metrics = mod.transcribe_file(client, wav, "call_IN.wav", config)

    assert [upload[0] for upload in client.uploads] == ["call_IN_c1.wav", "call_IN_c2.wav"]
    assert metrics["asr_attempts"] == 2  # one attempt per chunk, summed
    assert metrics["audio_seconds"] == 60.0
    assert merged["text"] == "part one part two"
    assert merged["segments"][1]["start"] == pytest.approx(34.125)
    assert recorder.names("internal_sentiment.asr.split") == [
        ("info", "internal_sentiment.asr.split")
    ]
    split_kwargs = next(
        kwargs for _, event, kwargs in recorder.events
        if event == "internal_sentiment.asr.split"
    )
    assert split_kwargs["chunks"] == 2
    assert split_kwargs["cut_seconds"] == [round(34.125, 2)]  # rounded for the log only


def test_transcribe_chunk_failure_fails_whole_file(monkeypatch, recorder):
    calls: list[int | None] = []

    def fake_once(client_asr, content, file_name, config, chunk=None, chunk_count=None):
        calls.append(chunk)
        if chunk == 1:
            return _payload("ok"), 2, {"error_type": None, "status_code": None, "error": None}
        return None, 3, {"error_type": "APIStatusError", "status_code": 413, "error": "big"}

    monkeypatch.setattr(mod, "_transcribe_once_with_retries", fake_once)
    wav = _make_wav(_loud(60_000))
    config = mod.Config(asr_target_rate=None, asr_max_upload_bytes=TWO_CHUNK_CAP)

    result, metrics = mod.transcribe_file(_StubASR([]), wav, "call_IN.wav", config)

    assert result is None
    assert metrics == {
        "status": STATUS_FAILED,
        "failed_stage": "asr",
        "error_type": "APIStatusError",
        "asr_attempts": 5,  # 2 (chunk 1) + 3 (chunk 2)
    }
    assert calls == [1, 2]  # no chunk transcribed after the failure
    assert recorder.names("internal_sentiment.asr.failed") == [
        ("error", "internal_sentiment.asr.failed")
    ]


# --- whole-file worker wrapper ---------------------------------------------------------

ASR_METRICS = {"asr_attempts": 1, "audio_seconds": 9.5, "asr_segments": 3}
LABEL_METRICS = {"label_attempts": 1, "label_prompt_tokens": 10, "label_completion_tokens": 4}
ANALYSIS_METRICS = {
    "status": STATUS_SUCCESS,
    "failed_stage": "",
    "error_type": "",
    "analysis_attempts": 1,
}


def _pending(payload=None, asr_metrics=None):
    return mod._PendingFile(
        file_name="a_IN.wav",
        stem="a_IN",
        audio_bytes=None if payload is not None else b"wav-bytes",
        payload=payload,
        asr_metrics=asr_metrics or {},
        system_prompt="sys",
        ctx=contextvars.copy_context(),
    )


def _stub_stages(monkeypatch, *, transcribe=None, label=None, analyze=None):
    if transcribe is not None:
        monkeypatch.setattr(mod, "transcribe_file", transcribe)
    if label is not None:
        monkeypatch.setattr(mod, "label_transcript", label)
    if analyze is not None:
        monkeypatch.setattr(mod, "analyze_transcript", analyze)


def test_guard_success_merges_all_stage_metrics(monkeypatch):
    payload = _payload("hi")
    _stub_stages(
        monkeypatch,
        transcribe=lambda *a, **k: (payload, dict(ASR_METRICS)),
        label=lambda *a, **k: ("Agent: hello", dict(LABEL_METRICS)),
        analyze=lambda *a, **k: ({"call_type": "x"}, dict(ANALYSIS_METRICS)),
    )
    result = mod._process_file_guarded(None, None, _pending(), mod.Config(), "prompt")

    assert result.transcribed is True  # fresh ASR -> payload checkpoint due
    assert result.payload == payload
    assert result.transcript == "Agent: hello"
    assert result.content_dump == {"call_type": "x"}
    assert result.metrics == {
        **mod._METRICS_DEFAULTS,
        **ASR_METRICS,
        **LABEL_METRICS,
        **ANALYSIS_METRICS,
    }


def test_guard_reused_payload_skips_transcription(monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("transcribe_file must not be called for a reused payload")

    _stub_stages(
        monkeypatch,
        transcribe=explode,
        label=lambda *a, **k: ("Agent: hello", dict(LABEL_METRICS)),
        analyze=lambda *a, **k: ({"call_type": "x"}, dict(ANALYSIS_METRICS)),
    )
    stored = _payload("stored")
    result = mod._process_file_guarded(
        None, None, _pending(payload=stored, asr_metrics=mod._asr_stats(stored)),
        mod.Config(), "prompt",
    )

    assert result.transcribed is False  # nothing fresh to checkpoint
    assert result.payload == stored
    assert result.metrics["status"] == STATUS_SUCCESS


def test_guard_preserves_asr_failure_shape(monkeypatch):
    fail_metrics = {"status": STATUS_FAILED, "failed_stage": "asr", "error_type": "APIError"}
    _stub_stages(monkeypatch, transcribe=lambda *a, **k: (None, dict(fail_metrics)))
    result = mod._process_file_guarded(None, None, _pending(), mod.Config(), "prompt")

    assert result.payload is None
    assert result.transcribed is False
    assert result.metrics == {**mod._METRICS_DEFAULTS, **fail_metrics}


def test_guard_preserves_label_failure_shape(monkeypatch):
    payload = _payload("hi")
    fail_metrics = {
        "status": STATUS_FAILED,
        "failed_stage": "label",
        "error_type": "transcript_unlabelled",
        "label_attempts": 3,
    }
    _stub_stages(
        monkeypatch,
        transcribe=lambda *a, **k: (payload, dict(ASR_METRICS)),
        label=lambda *a, **k: (None, dict(fail_metrics)),
    )
    result = mod._process_file_guarded(None, None, _pending(), mod.Config(), "prompt")

    assert result.transcript is None
    assert result.content_dump is None
    assert result.transcribed is True  # the payload checkpoint must still land
    assert result.metrics == {**mod._METRICS_DEFAULTS, **ASR_METRICS, **fail_metrics}


@pytest.mark.parametrize(
    ("raising_stage", "expected_stage"),
    [("transcribe", "asr"), ("label", "label"), ("analyze", "analysis")],
)
def test_guard_backstop_names_the_raising_stage(
    monkeypatch, recorder, raising_stage, expected_stage
):
    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    stubs = {
        "transcribe": lambda *a, **k: (_payload("hi"), dict(ASR_METRICS)),
        "label": lambda *a, **k: ("Agent: hello", dict(LABEL_METRICS)),
        "analyze": lambda *a, **k: ({"call_type": "x"}, dict(ANALYSIS_METRICS)),
    }
    stubs[raising_stage] = boom
    _stub_stages(
        monkeypatch,
        transcribe=stubs["transcribe"],
        label=stubs["label"],
        analyze=stubs["analyze"],
    )
    result = mod._process_file_guarded(None, None, _pending(), mod.Config(), "prompt")

    assert result.content_dump is None
    assert result.metrics["status"] == STATUS_FAILED
    assert result.metrics["failed_stage"] == expected_stage
    assert result.metrics["error_type"] == "RuntimeError"
    assert recorder.names("internal_sentiment.file.failed") == [
        ("warning", "internal_sentiment.file.failed")
    ]


def test_ctx_run_carries_contextvars_into_worker():
    # Pool threads start with an empty context; ctx.run is what keeps
    # run_id/model on worker-side log records.
    from concurrent.futures import ThreadPoolExecutor

    import structlog

    structlog.contextvars.bind_contextvars(run_id="r1")
    try:
        ctx = contextvars.copy_context()
        with ThreadPoolExecutor(max_workers=2) as pool:
            bare = list(
                pool.map(lambda _: structlog.contextvars.get_contextvars(), [None])
            )
            carried = list(
                pool.map(lambda c: c.run(structlog.contextvars.get_contextvars), [ctx])
            )
        assert "run_id" not in bare[0]
        assert carried[0].get("run_id") == "r1"
    finally:
        structlog.contextvars.unbind_contextvars("run_id")
