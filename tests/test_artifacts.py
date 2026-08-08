"""Durability and privacy contracts for execution artifacts. No network calls."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from evalgen.artifacts import (  # noqa: E402
    ArtifactError,
    RunJournal,
    append_jsonl,
    assert_shareable_payload,
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
