"""The corpus freeze refuses the things it exists to refuse.

WHY THIS GATE EXISTS AT ALL. `retention-e23.plan.json` states the stamping policy in prose
and E23 still stands at `status: draft` with every corpus hash null, after 1,002 model calls
against it, with a gate reading "No model call in E23 happens before this". Nobody decided to
skip it. There was no command that performed it, and a step with no command behind it does
not happen.

So the freeze is a script, and these tests are what stop it becoming a script that stamps
anything put in front of it.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
# `freeze_corpus` lives in scripts/, which CI's `PYTHONPATH: src` does not cover. Following
# the convention the rest of this directory uses rather than relying on an env var that
# differs between the CI step and a local shell -- test_arm_wiring.py and test_arm_parity.py
# have each broken CI once already over exactly this.
sys.path.insert(0, str(REPO / "scripts"))

from freeze_corpus import Refused, aggregate, main  # noqa: E402


def _wav(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)


@pytest.fixture()
def pack(tmp_path: Path) -> Path:
    """A minimal but internally consistent two-item corpus."""
    root = tmp_path / "asr-eval-vX"
    audio, gt = root / "audio", root / "ground-truth"
    rows = []
    for i, body in ((1, b"RIFF....one"), (2, b"RIFF....two")):
        name = f"71{i:02d}_0810000{300 + i}_x_D_20260803_200_IN.wav"
        _wav(audio / name, body)
        rows.append({"item_id": f"ASR-{i:03d}", "filename": name,
                     "sha256": hashlib.sha256(body).hexdigest(), "duration_s": 200.0})
        (gt / f"ASR-{i:03d}.txt").parent.mkdir(parents=True, exist_ok=True)
        (gt / f"ASR-{i:03d}.txt").write_text(f"reference {i}\n", encoding="utf-8")
        (gt / f"ASR-{i:03d}.entities.json").write_text("[]", encoding="utf-8")
    (root / "manifest.json").write_text(json.dumps(rows), encoding="utf-8")
    (gt / "business.csv").write_text(
        "call_id,phone_number,product,call_result,main,secondary,third\n"
        "7101,0810000301,tol,churn,network,,\n"
        "7102,0810000302,postpaid,save,save cost,,\n", encoding="utf-8")
    return root


@pytest.fixture()
def plan_path(tmp_path: Path, pack: Path) -> Path:
    plan = {
        "experiment_id": "retention-vX",
        "status": "draft",
        "assets": {
            "corpus_root": {"path": f"{pack.name}/"},
            "corpus_manifest": {"path": "m", "sha256": None, "items": 2},
            "audio_bytes": {"path": "a", "aggregate_sha256": None},
            "business_ground_truth": {"path": "b", "sha256": None, "rows": None},
            "reference_transcripts": {"path": "r", "aggregate_sha256": None},
            "entity_annotations": {"path": "e", "aggregate_sha256": None},
        },
    }
    p = tmp_path / "plan.json"
    p.write_text(json.dumps(plan), encoding="utf-8")
    return p


def run(plan_path: Path, pack: Path, *extra: str) -> int:
    return main(["--plan", str(plan_path), "--pack", str(pack), *extra])


def test_a_clean_corpus_stamps_every_null_and_qualifies_the_plan(plan_path, pack):
    assert run(plan_path, pack) == 0
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    assets = plan["assets"]
    assert assets["corpus_manifest"]["sha256"]
    assert assets["audio_bytes"]["aggregate_sha256"]
    assert assets["business_ground_truth"]["sha256"]
    assert assets["reference_transcripts"]["aggregate_sha256"]
    assert assets["entity_annotations"]["aggregate_sha256"]
    assert assets["business_ground_truth"]["rows"] == 2
    assert plan["status"] == "qualified"
    assert plan["approvals_given"][0]["gate"].startswith("1")


def test_check_writes_nothing(plan_path, pack):
    before = plan_path.read_text(encoding="utf-8")
    assert run(plan_path, pack, "--check") == 0
    assert plan_path.read_text(encoding="utf-8") == before


def test_stamping_twice_is_refused(plan_path, pack):
    """'Stamped exactly once' is the entire policy."""
    assert run(plan_path, pack) == 0
    with pytest.raises(Refused) as excinfo:
        run(plan_path, pack)
    assert "already stamped" in str(excinfo.value)


def test_a_pack_the_plan_does_not_name_is_refused(plan_path, pack, tmp_path):
    other = tmp_path / "asr-eval-other"
    shutil.copytree(pack, other)
    with pytest.raises(Refused) as excinfo:
        run(plan_path, other)
    assert "corpus_root" in str(excinfo.value)


def test_a_stale_render_leaving_an_extra_wav_is_refused(plan_path, pack):
    """The 185-files-for-138-calls failure: a duration change renames, it does not replace."""
    _wav(pack / "audio" / "7101_0810000301_x_D_20260803_207_IN.wav", b"RIFF....old")
    with pytest.raises(Refused) as excinfo:
        run(plan_path, pack)
    msg = str(excinfo.value)
    assert "manifest.json names" in msg and "Only on disk" in msg


def test_audio_that_no_longer_matches_the_manifest_is_refused(plan_path, pack):
    rows = json.loads((pack / "manifest.json").read_text(encoding="utf-8"))
    (pack / "audio" / rows[0]["filename"]).write_bytes(b"RIFF....rerendered")
    with pytest.raises(Refused) as excinfo:
        run(plan_path, pack)
    assert "no longer match their manifest sha256" in str(excinfo.value)


def test_an_item_count_disagreeing_with_the_plan_is_refused(plan_path, pack):
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["assets"]["corpus_manifest"]["items"] = 138
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(Refused) as excinfo:
        run(plan_path, pack)
    assert "the plan expects 138" in str(excinfo.value)


def test_an_existing_transcript_means_the_freeze_no_longer_precedes_the_run(plan_path, pack):
    d = pack / "hypotheses" / "qwen3-asr-1.7b"
    d.mkdir(parents=True)
    (d / "ASR-001.txt").write_text("already transcribed", encoding="utf-8")
    with pytest.raises(Refused) as excinfo:
        run(plan_path, pack)
    assert "before the freeze" in str(excinfo.value)


def test_a_preflight_probe_is_not_the_run(plan_path, pack):
    """One reachability probe must not block the gate, or nobody will probe."""
    d = pack / "hypotheses" / "preflight-qwen"
    d.mkdir(parents=True)
    (d / "ASR-001.txt").write_text("probe", encoding="utf-8")
    assert run(plan_path, pack) == 0


def test_an_unrendered_corpus_is_refused(plan_path, pack):
    (pack / "manifest.json").unlink()
    with pytest.raises(Refused) as excinfo:
        run(plan_path, pack)
    assert "not rendered" in str(excinfo.value)


def test_the_aggregate_hash_does_not_depend_on_directory_order(tmp_path):
    """Otherwise the same corpus hashes differently on CI than on a laptop."""
    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    a.write_text("alpha", encoding="utf-8")
    b.write_text("beta", encoding="utf-8")
    assert aggregate([a, b]) == aggregate([b, a])


def test_the_aggregate_hash_changes_when_a_file_changes(tmp_path):
    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    a.write_text("alpha", encoding="utf-8")
    b.write_text("beta", encoding="utf-8")
    before = aggregate([a, b])
    b.write_text("beta!", encoding="utf-8")
    assert aggregate([a, b]) != before


def test_the_aggregate_hash_notices_a_rename(tmp_path):
    """Content-only hashing would miss a renamed file, and the filename carries duration."""
    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    a.write_text("alpha", encoding="utf-8")
    b.write_text("beta", encoding="utf-8")
    before = aggregate([a, b])
    c = tmp_path / "c.txt"
    b.rename(c)
    assert aggregate([a, c]) != before
