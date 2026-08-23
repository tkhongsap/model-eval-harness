"""Offline tests for the documents pipeline's opt-in LLM concurrency.

Covers the DOC_LLM_CONCURRENCY env read (fail-soft, floor 1), the worker
wrapper's Exception backstop, and the two threading properties the chunked
loop relies on: contextvars propagation via ``ctx.run`` and ``pool.map``
order preservation. No network, no GCS.
"""

from __future__ import annotations

import contextvars
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
import structlog

from src.google_model.usage_metrics import STATUS_FAILED, STATUS_SUCCESS
from src.local_model.documents import internal_direct_output as mod

ENV = "DOC_LLM_CONCURRENCY"


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


@pytest.fixture
def recorder(monkeypatch):
    stub = _RecorderLogger()
    monkeypatch.setattr(mod, "logger", stub)
    return stub


def test_concurrency_unset_defaults_to_one(monkeypatch, recorder):
    monkeypatch.delenv(ENV, raising=False)
    assert mod._doc_llm_concurrency() == 1
    assert recorder.events == []


def test_concurrency_blank_is_silently_sequential(monkeypatch, recorder):
    monkeypatch.setenv(ENV, "")
    assert mod._doc_llm_concurrency() == 1
    assert recorder.events == []


def test_concurrency_reads_valid_value(monkeypatch):
    monkeypatch.setenv(ENV, "4")
    assert mod._doc_llm_concurrency() == 4


@pytest.mark.parametrize("bad", ["0", "-2", "abc", "2.5"])
def test_concurrency_invalid_warns_and_falls_back(monkeypatch, recorder, bad):
    monkeypatch.setenv(ENV, bad)
    assert mod._doc_llm_concurrency() == 1
    assert [
        (level, event) for level, event, _ in recorder.events
    ] == [("warning", "internal_document.config.concurrency_invalid")]


def test_config_reads_env_at_instantiation(monkeypatch):
    # default_factory runs at Config() time -- what main.py's menu preview shows
    # is what the run uses.
    monkeypatch.setenv(ENV, "3")
    assert mod.Config().llm_concurrency == 3
    monkeypatch.setenv(ENV, "5")
    assert mod.Config().llm_concurrency == 5


def test_guard_turns_unexpected_error_into_failed_row(monkeypatch, recorder):
    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(mod, "extract_page", boom)
    content, metrics = mod._extract_page_guarded(
        None, b"", "doc_p1.png", mod.Config(), "prompt"
    )
    assert content is None
    assert metrics["status"] == STATUS_FAILED
    assert metrics["error_type"] == "RuntimeError"
    assert [
        (level, event) for level, event, _ in recorder.events
    ] == [("warning", "internal_document.page.failed")]


def test_guard_passes_success_through(monkeypatch):
    sentinel = ({"TAX_ID": "1"}, {"status": STATUS_SUCCESS})
    monkeypatch.setattr(mod, "extract_page", lambda *args, **kwargs: sentinel)
    assert (
        mod._extract_page_guarded(None, b"", "doc_p1.png", mod.Config(), "prompt")
        is sentinel
    )


def test_ctx_run_carries_contextvars_into_worker():
    # Pool threads start with an empty context; ctx.run is what keeps
    # run_id/model on worker-side log records.
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


def test_pool_map_preserves_submission_order():
    def slow_first(i: int) -> int:
        time.sleep(0.05 if i == 0 else 0)
        return i

    with ThreadPoolExecutor(max_workers=3) as pool:
        assert list(pool.map(slow_first, [0, 1, 2])) == [0, 1, 2]
