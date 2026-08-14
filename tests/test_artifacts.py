"""Durability and privacy contracts for execution artifacts. No network calls."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from evalgen import artifacts  # noqa: E402
from evalgen.artifacts import (  # noqa: E402
    ArtifactError,
    RunJournal,
    append_jsonl,
    assert_shareable_payload,
    atomic_write_bytes,
    atomic_write_text,
    require_private_destination,
)
from evalharness.paths import ENV_DATA_DIR  # noqa: E402


def test_private_destination_must_be_below_configured_root(monkeypatch, tmp_path):
    private = tmp_path / "private"
    private.mkdir()
    monkeypatch.setenv(ENV_DATA_DIR, str(private))

    assert require_private_destination(private / "runs" / "one") == (
        private / "runs" / "one"
    ).resolve()
    with pytest.raises(ArtifactError, match="outside"):
        require_private_destination(tmp_path / "escaped")


def test_private_destination_rejects_a_git_worktree(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    monkeypatch.setenv(ENV_DATA_DIR, str(repo))

    with pytest.raises(ArtifactError, match="git worktree"):
        require_private_destination(repo / "run")


def test_shareable_payload_is_recursive():
    assert_shareable_payload({"summary": {"count": 2}, "item_keys": ["a1", "b2"]})

    with pytest.raises(ArtifactError, match="cited_span"):
        assert_shareable_payload({"items": [{"cited_span": "customer text"}]})
    with pytest.raises(ArtifactError, match="phone-like"):
        assert_shareable_payload({"notes": ["call 0812345678"]})
    for private_key in ("raw_response_text", "completion_raw"):
        with pytest.raises(ArtifactError, match=private_key):
            assert_shareable_payload({private_key: "private but no phone"})


def test_shareable_payload_accepts_phone_like_digits_inside_exact_sha256_fields():
    digest = "a" * 20 + "0812345678" + "b" * 34

    assert_shareable_payload({"request_sha256": digest})

    with pytest.raises(ArtifactError, match="phone-like"):
        assert_shareable_payload({"notes": digest})
    with pytest.raises(ArtifactError, match="phone-like"):
        assert_shareable_payload({"request_sha256": "0812345678"})


def test_shareable_payload_accepts_phone_like_digits_inside_judgment_unit_hmac():
    judgment_unit_id = "ju_" + "a" * 7 + "0812345678" + "b" * 7

    assert_shareable_payload({"judgment_unit_id": judgment_unit_id})

    with pytest.raises(ArtifactError, match="phone-like"):
        assert_shareable_payload({"other_id": judgment_unit_id})
    with pytest.raises(ArtifactError, match="phone-like"):
        assert_shareable_payload({"judgment_unit_id": "ju_0812345678"})


def test_atomic_write_replaces_complete_file(tmp_path):
    target = tmp_path / "run.json"
    atomic_write_text(target, "old\n")
    atomic_write_text(target, "new\n")
    assert target.read_bytes() == b"new\n"
    assert not list(tmp_path.glob("*.tmp"))


def test_append_jsonl_writes_one_canonical_record_per_call(tmp_path):
    target = tmp_path / "private.jsonl"
    append_jsonl(target, {"b": 2, "a": 1})
    append_jsonl(target, {"a": 3})
    assert target.read_text(encoding="utf-8").splitlines() == [
        '{"a":1,"b":2}',
        '{"a":3}',
    ]


def test_journal_round_trip_and_resume(tmp_path):
    contract = {"model": "local/model", "testset_sha": "abc", "repeats": 2}
    path = tmp_path / "run.journal.jsonl"
    journal = RunJournal.create(path, contract)
    journal.mark_started("A", 1)
    journal.append({"item_id": "A", "replicate": 1, "outcome": "ok"})

    resumed = RunJournal.open(path, contract)
    assert resumed.completed_rows() == (
        {"item_id": "A", "outcome": "ok", "replicate": 1},
    )
    resumed.mark_started("A", 2)
    resumed.append({"item_id": "A", "replicate": 2, "outcome": "transport_error"})
    assert len(RunJournal.open(path, contract).completed_rows()) == 2


def test_journal_refuses_wrong_contract_duplicate_and_tampering(tmp_path):
    path = tmp_path / "run.journal.jsonl"
    journal = RunJournal.create(path, {"run": 1})
    row = {"item_id": "A", "replicate": 1, "outcome": "ok"}
    journal.mark_started("A", 1)
    journal.append(row)
    with pytest.raises(ArtifactError, match="duplicate"):
        journal.append(row)
    with pytest.raises(ArtifactError, match="different"):
        RunJournal.open(path, {"run": 2})

    lines = path.read_text(encoding="utf-8").splitlines()
    envelope = json.loads(lines[2])
    envelope["row"]["outcome"] = "changed"
    lines[2] = json.dumps(envelope)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(ArtifactError, match="row hash"):
        RunJournal.open(path, {"run": 1})


def test_unresolved_started_cell_blocks_safe_replay_and_torn_tail_keeps_history(tmp_path):
    path = tmp_path / "run.journal.jsonl"
    journal = RunJournal.create(path, {"run": 1})
    journal.mark_started("A", 1)
    journal.append({"item_id": "A", "replicate": 1, "outcome": "ok"})
    journal.mark_started("B", 1)
    with path.open("ab") as handle:
        handle.write(b'{"event":"result"')

    resumed = RunJournal.open(path, {"run": 1})
    assert resumed.completed_rows() == (
        {"item_id": "A", "outcome": "ok", "replicate": 1},
    )
    assert resumed.unresolved_cells() == (("B", 1),)
    assert resumed.trailing_torn_record is True


# --- the atomic-replace retry, proved in both directions -----------------------------
#
# MEASURED 2026-08-12: `os.replace` inside `atomic_write_bytes` failed with
# `PermissionError [WinError 5]` writing `run.state.json`, once in ten full suite runs.
# Harness code, not test code -- and `run.state.json` is the crash-safe-resume record, so
# an unhandled failure there aborts a run that has already been paid for.
#
# A retry is only defensible if it cannot hide a real bug, so both directions are proved
# rather than asserted: a holder that lets go is absorbed, and a holder that never lets go
# still raises inside the budget.


def test_a_transient_holder_is_absorbed_and_the_write_still_lands():
    """The measured case: something else holds the destination, then releases it."""
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        target = root / "run.state.json"
        target.write_bytes(b'{"status": "RUNNING"}')

        handle = open(target, "rb")  # noqa: SIM115 - deliberately held

        def release():
            time.sleep(0.4)
            handle.close()

        threading.Thread(target=release, daemon=True).start()
        started = time.monotonic()
        try:
            atomic_write_bytes(target, b'{"status": "COMPLETE"}')
        finally:
            if not handle.closed:
                handle.close()

        elapsed = (time.monotonic() - started) * 1000
        assert target.read_bytes() == b'{"status": "COMPLETE"}'
        if sys.platform == "win32":
            # On POSIX the rename never blocks, so it lands immediately and there is
            # nothing to wait for; only Windows exercises the retry.
            assert elapsed >= 300, (
                f"expected to wait for the holder, returned in {elapsed:.0f} ms"
            )
        # No temp file is left behind either way.
        assert list(root.glob(".run.state.json.*.tmp")) == []


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="POSIX rename(2) succeeds over an open file, so there is no failure to bound",
)
def test_a_holder_that_never_releases_still_raises_inside_the_budget():
    """The property that stops this retry from hiding a real in-process leak.

    A handle held by THIS process is never released while the write is blocked, so the
    budget must expire and the original PermissionError must propagate. If this ever
    starts passing by absorbing the write, the retry has become a way to lose an error.
    """
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        target = root / "run.state.json"
        target.write_bytes(b"{}")
        handle = open(target, "rb")  # noqa: SIM115 - never released
        started = time.monotonic()
        try:
            with pytest.raises(PermissionError):
                with mock.patch.object(artifacts, "_REPLACE_BUDGET_S", 0.5):
                    atomic_write_bytes(target, b'{"status": "COMPLETE"}')
        finally:
            handle.close()
        elapsed = (time.monotonic() - started) * 1000
        assert 450 <= elapsed <= 3000, f"budget not respected: {elapsed:.0f} ms"
        # The failed write cleans up after itself rather than leaving a .tmp behind.
        assert list(root.glob(".run.state.json.*.tmp")) == []
