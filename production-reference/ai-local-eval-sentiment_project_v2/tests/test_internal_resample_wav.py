"""Offline tests for WAV decoding/resampling and transcribe_file's decode short-circuit.

All WAV bytes are synthesized in-memory -- 16-bit PCM via stdlib wave, G.711 via a
hand-packed RIFF builder -- and a fake client stands in for the ASR endpoint. The
G.711 golden values are precomputed from CPython's audioop tables so they hold on
Python 3.13, where audioop itself is gone; the full-table oracle test additionally
cross-checks all 256 entries wherever audioop is still importable. No network.
"""

from __future__ import annotations

import io
import struct
import wave
from dataclasses import replace
from types import SimpleNamespace

import httpx
import numpy as np
import pytest
from openai import APIStatusError

from src.google_model.usage_metrics import STATUS_FAILED
from src.local_model.sentiment.internal_asr_llm_output import (
    Config,
    _g711_decode_table,
    resample_wav_bytes,
    transcribe_file,
)

# byte -> linear int16, per ITU-T G.711 (matches audioop.ulaw2lin/alaw2lin).
MULAW_GOLDEN = {0x00: -32124, 0x7F: 0, 0x80: 32124, 0xFF: 0}
ALAW_GOLDEN = {0x00: -5504, 0x55: -8, 0xD5: 8, 0xFF: 848}


def _pcm_wav(samples: list[int], rate: int, nchannels: int = 1, sampwidth: int = 2) -> bytes:
    with io.BytesIO() as buffer:
        with wave.open(buffer, "wb") as wav_out:
            wav_out.setnchannels(nchannels)
            wav_out.setsampwidth(sampwidth)
            wav_out.setframerate(rate)
            fmt = "<%d%s" % (len(samples), "h" if sampwidth == 2 else "b")
            wav_out.writeframes(struct.pack(fmt, *samples))
        return buffer.getvalue()


def _g711_wav(
    payload: bytes,
    *,
    format_tag: int = 7,
    rate: int = 8000,
    nchannels: int = 1,
    fmt_size: int = 16,
    extra_chunk: bytes | None = None,
    data_size_override: int | None = None,
    drop_data: bool = False,
) -> bytes:
    """Hand-packed RIFF/WAVE with a G.711 fmt chunk; knobs fabricate malformations."""
    fmt_body = struct.pack(
        "<HHIIHH", format_tag, nchannels, rate, rate * nchannels, nchannels, 8
    )
    if fmt_size > 16:
        fmt_body += bytes(fmt_size - 16)  # cbSize tail (WAVE_FORMAT_EXTENSIBLE style)
    chunks = b"fmt " + struct.pack("<I", len(fmt_body)) + fmt_body
    if extra_chunk is not None:
        chunks += extra_chunk
    if not drop_data:
        size = len(payload) if data_size_override is None else data_size_override
        chunks += b"data" + struct.pack("<I", size) + payload
    return b"RIFF" + struct.pack("<I", 4 + len(chunks)) + b"WAVE" + chunks


def _read_pcm16(wav_bytes: bytes) -> tuple[list[int], int, int]:
    """(samples, framerate, nchannels) of a PCM16 WAV -- asserts it IS one."""
    with wave.open(io.BytesIO(wav_bytes), "rb") as wav_in:
        assert wav_in.getsampwidth() == 2
        frames = wav_in.readframes(wav_in.getnframes())
        samples = list(struct.unpack("<%dh" % (len(frames) // 2), frames))
        return samples, wav_in.getframerate(), wav_in.getnchannels()


def _asr_response(dump: dict) -> SimpleNamespace:
    return SimpleNamespace(model_dump=lambda mode="json": dump)


def _success_response() -> SimpleNamespace:
    return _asr_response(
        {
            "duration": 1.0,
            "language": "th",
            "segments": [{"start": 0.0, "end": 1.0, "text": "x"}],
            "text": "x",
        }
    )


def _empty_response() -> SimpleNamespace:
    """A wire-level success that carries nothing -- drives the retry path."""
    return _asr_response({"duration": 0.0, "language": "th", "segments": [], "text": ""})


def _status_error(code: int = 413, text: str = "Request Entity Too Large") -> APIStatusError:
    """A real SDK status error, built the way the SDK builds one from a response."""
    request = httpx.Request("POST", "https://asr.test/v1/audio/transcriptions")
    return APIStatusError(text, response=httpx.Response(code, request=request), body=None)


class FakeAsrClient:
    """Records each create call's kwargs, then raises or returns per the script."""

    def __init__(self, script):
        self.calls: list[dict] = []
        self._script = list(script)
        self.audio = SimpleNamespace(transcriptions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        step = self._script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(
        "src.local_model.sentiment.internal_asr_llm_output.time.sleep", lambda _: None
    )


class TestResamplePcm:
    def test_pcm_at_target_rate_passes_through_unchanged(self):
        data = _pcm_wav([0, 1000, -1000, 32767], rate=16000)
        assert resample_wav_bytes(data, 16000) == data

    def test_pcm_resamples_to_target_rate(self):
        data = _pcm_wav(list(range(-400, 400)), rate=8000)
        samples, rate, nchannels = _read_pcm16(resample_wav_bytes(data, 16000))
        assert (rate, nchannels) == (16000, 1)
        assert len(samples) == 1600  # exactly 2x at an 8k -> 16k ratio

    def test_8bit_pcm_is_rejected(self):
        data = _pcm_wav([0, 10, -10], rate=8000, sampwidth=1)
        with pytest.raises(ValueError, match="16-bit"):
            resample_wav_bytes(data, 16000)


class TestG711Decode:
    def test_mulaw_at_target_rate_is_reencoded_not_passed_through(self):
        payload = bytes(MULAW_GOLDEN)
        data = _g711_wav(payload, format_tag=7, rate=8000)
        result = resample_wav_bytes(data, 8000)
        assert result != data  # original mu-law bytes must never come back
        samples, rate, nchannels = _read_pcm16(result)
        assert (rate, nchannels) == (8000, 1)
        assert samples == list(MULAW_GOLDEN.values())

    def test_alaw_golden_values(self):
        payload = bytes(ALAW_GOLDEN)
        samples, rate, _ = _read_pcm16(resample_wav_bytes(_g711_wav(payload, format_tag=6), 8000))
        assert rate == 8000
        assert samples == list(ALAW_GOLDEN.values())

    def test_mulaw_resamples_to_target_rate(self):
        data = _g711_wav(bytes(range(100)), format_tag=7, rate=8000)
        samples, rate, nchannels = _read_pcm16(resample_wav_bytes(data, 16000))
        assert (rate, nchannels) == (16000, 1)
        assert len(samples) == 200

    def test_tables_match_audioop_for_all_256_bytes(self):
        audioop = pytest.importorskip("audioop")  # stdlib on <=3.12, gone on 3.13
        every_byte = bytes(range(256))
        for tag, decode in ((7, audioop.ulaw2lin), (6, audioop.alaw2lin)):
            expected = np.frombuffer(decode(every_byte, 2), dtype="<i2")
            assert (_g711_decode_table(tag) == expected).all(), f"tag {tag}"

    def test_unsupported_format_tag_names_the_tag(self):
        with pytest.raises(ValueError, match="tag 3"):
            resample_wav_bytes(_g711_wav(b"\x00\x00", format_tag=3), 16000)


class TestRiffRobustness:
    def test_oversized_fmt_and_odd_extra_chunk_still_decode(self):
        # An 18-byte fmt (cbSize tail) plus an odd-size chunk with its pad byte
        # between fmt and data -- the walk must land on data regardless.
        fact = b"fact" + struct.pack("<I", 3) + b"abc" + b"\x00"
        data = _g711_wav(bytes(MULAW_GOLDEN), fmt_size=18, extra_chunk=fact)
        samples, _, _ = _read_pcm16(resample_wav_bytes(data, 8000))
        assert samples == list(MULAW_GOLDEN.values())

    def test_truncated_data_chunk_is_rejected(self):
        data = _g711_wav(b"\x00\x7f", data_size_override=100)
        with pytest.raises(ValueError, match="truncated"):
            resample_wav_bytes(data, 16000)

    def test_missing_data_chunk_is_rejected(self):
        with pytest.raises(ValueError, match="data chunk missing"):
            resample_wav_bytes(_g711_wav(b"", drop_data=True), 16000)

    def test_non_riff_bytes_are_rejected(self):
        with pytest.raises(ValueError, match="RIFF"):
            resample_wav_bytes(b"garbage that is not audio", 16000)


class TestTranscribeFileShortCircuit:
    def test_undecodable_input_fails_once_without_touching_the_endpoint(self, monkeypatch):
        monkeypatch.setattr(
            "src.local_model.sentiment.internal_asr_llm_output.time.sleep",
            lambda _: pytest.fail("decode failure must not sleep"),
        )
        client = FakeAsrClient([])
        config = replace(Config(), asr_target_rate=16000)

        payload, metrics = transcribe_file(client, b"garbage", "a_IN.wav", config)

        assert payload is None
        assert client.calls == []
        assert metrics["status"] == STATUS_FAILED
        assert metrics["failed_stage"] == "asr"
        assert metrics["error_type"] == "ValueError"
        # Absent, not 0: no endpoint call happened, so the Matrix cell stays blank.
        assert "asr_attempts" not in metrics

    def test_resample_runs_once_across_endpoint_retries(self, monkeypatch):
        import src.local_model.sentiment.internal_asr_llm_output as mod

        resample_calls = []
        real = resample_wav_bytes

        def counting(wav_bytes, target_rate):
            resample_calls.append(target_rate)
            return real(wav_bytes, target_rate)

        monkeypatch.setattr(mod, "resample_wav_bytes", counting)
        client = FakeAsrClient([_empty_response(), _success_response()])
        config = replace(Config(), asr_target_rate=16000)
        data = _g711_wav(bytes(range(64)), format_tag=7, rate=8000)

        payload, metrics = transcribe_file(client, data, "a_IN.wav", config)

        assert payload is not None
        assert resample_calls == [16000]
        assert len(client.calls) == 2
        assert metrics["asr_attempts"] == 2

    def test_rate_none_sends_source_bytes_untouched(self, monkeypatch):
        import src.local_model.sentiment.internal_asr_llm_output as mod

        monkeypatch.setattr(
            mod,
            "resample_wav_bytes",
            lambda *_: pytest.fail("rate None must not resample"),
        )
        client = FakeAsrClient([_success_response()])
        config = replace(Config(), asr_target_rate=None)
        data = _g711_wav(bytes(range(16)))

        payload, _ = transcribe_file(client, data, "a_IN.wav", config)

        assert payload is not None
        assert client.calls[0]["file"][1] is data


class TestAsrErrorLogging:
    """The retry/failed records must say what the endpoint said -- a 413 was
    previously visible only in httpx's own INFO line.

    capsys rather than caplog: structlog writes through its own logger factory,
    not the stdlib logging hooks, so a caplog assertion here passes vacuously.
    """

    def test_status_errors_log_status_size_and_detail(self, capsys):
        # One error object: a 413 is deterministic, so the retry loop breaks
        # after the first attempt instead of resending the identical bytes.
        client = FakeAsrClient([_status_error()])
        config = replace(Config(), asr_target_rate=None)
        data = _g711_wav(bytes(range(16)))

        payload, metrics = transcribe_file(client, data, "a_IN.wav", config)

        assert payload is None
        assert metrics["asr_attempts"] == 1
        assert metrics["error_type"] == "APIStatusError"
        captured = capsys.readouterr()
        record = captured.err + captured.out
        assert "413" in record
        assert "upload_bytes" in record
        assert str(len(data)) in record
        assert "Request Entity Too Large" in record

    def test_error_detail_is_truncated(self, capsys):
        # 400 x's: the log must carry the first 300 and never the 301st. A 503,
        # not a 413 -- transient statuses keep retrying, so the success lands.
        client = FakeAsrClient([_status_error(code=503, text="x" * 400), _success_response()])
        config = replace(Config(), asr_target_rate=None)

        payload, _ = transcribe_file(client, _g711_wav(bytes(range(16))), "a_IN.wav", config)

        assert payload is not None
        captured = capsys.readouterr()
        record = captured.err + captured.out
        assert "x" * 300 in record
        assert "x" * 301 not in record

    def test_value_error_path_logs_detail_without_status(self, capsys):
        client = FakeAsrClient([_empty_response()] * 3)
        config = replace(Config(), asr_target_rate=None)

        payload, metrics = transcribe_file(client, _g711_wav(bytes(range(16))), "a_IN.wav", config)

        assert payload is None
        assert metrics["asr_attempts"] == 3
        assert metrics["error_type"] == "ValueError"
        captured = capsys.readouterr()
        record = captured.err + captured.out
        assert "no segments and no text" in record
