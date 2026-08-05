"""The run index must describe the runs on disk, or it is worse than no index.

`RUNS.md` is committed while `out/runs/` is not, so the index is the only record a
reader who clones this repository ever sees. An index that silently omits a run, or
keeps describing one after it changed, is a provenance claim nobody can check -- which
is the failure mode `manifest.py` and the `RECONCILED` stamp exist to prevent.

Two properties carry the weight here:

  * **`--check` must actually fail when the file is stale.** A check that only ever
    passes is not a check, and `tests/test_requirements.py` makes the same argument
    about the version-pin gate ("a gate that has only ever been seen to pass is not yet
    a gate"). Every assertion below that matters is paired with a mutation proving it
    can fail.
  * **A split arm must be visibly loud.** `split_items` is the one signal a router
    cannot fake, and the 2026-08-04 defect (one model id, two backends) was invisible
    precisely because nothing rendered it. If the index ever prints a split run the same
    way it prints a clean one, the column has stopped working.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def _load():
    """Load `scripts/run_index.py` by path.

    By path rather than by name for the reason `export_xlsx.py:66-68` records: `scripts/`
    holds `evalgen.py`, a launcher, so putting that directory on `sys.path` makes
    `import evalgen.cli` resolve to the launcher and fail with "'evalgen' is not a
    package".
    """
    spec = importlib.util.spec_from_file_location(
        "_run_index_under_test", ROOT / "scripts" / "run_index.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


run_index = _load()


def _meta(**over):
    base = {
        "run_id": "20260101-000000Z-arm",
        "created_utc": "2026-01-01T00:00:00+00:00",
        "arm": "arm",
        "model_requested": "vendor/model",
        "provider_requested": "SomeProvider",
        "prompt_id": "v9_16_base",
        "prompt_sha": "a" * 64,
        "testset_sha": "b" * 64,
        "gt_sha": "c" * 64,
        "scorer_sha": "abc1234",
        "repeats": 3,
        "items": 20,
        "rows": 60,
        "outcome_counts": {"ok": 60},
        "prompt_token_spread": {f"RET-{i:02d}": [100 + i] for i in range(1, 21)},
        "split_items": {},
        "total_cost_usd_lower_bound": 0.05,
    }
    base.update(over)
    return base


def _write_run(base: Path, name: str, meta: dict | None) -> Path:
    d = base / name
    d.mkdir(parents=True)
    if meta is not None:
        (d / "run.json").write_text(json.dumps(meta), encoding="utf-8")
        (d / "run.jsonl").write_text("{}\n", encoding="utf-8")
    else:  # a dry run: requests + prompt, and deliberately no run.json
        (d / "requests.jsonl").write_text("{}\n", encoding="utf-8")
        (d / "prompt.txt").write_text("prompt", encoding="utf-8")
    return d


# ------------------------------------------------------------------ collection


def test_every_run_with_meta_is_indexed(tmp_path):
    for n in range(3):
        _write_run(tmp_path, f"20260101-00000{n}Z-arm{n}", _meta(run_id=f"r{n}", arm=f"arm{n}"))
    rows, dry = run_index.collect(tmp_path)
    assert [r["arm"] for r in rows] == ["arm0", "arm1", "arm2"]
    assert dry == []


def test_a_dry_run_is_reported_not_silently_dropped(tmp_path):
    """A directory with no run.json is a real thing on disk and must appear somewhere.

    Dropping it would make the index disagree with `ls out/runs/` for a reason the
    reader cannot see, and the reader has no way to check because `out/` is gitignored.
    """
    _write_run(tmp_path, "20260101-000000Z-real", _meta())
    _write_run(tmp_path, "20260101-000001Z-dryrun", None)
    rows, dry = run_index.collect(tmp_path)
    assert len(rows) == 1
    assert dry == ["20260101-000001Z-dryrun"]
    assert "20260101-000001Z-dryrun" in run_index.render(rows, dry)


def test_an_unreadable_meta_is_reported_rather_than_crashing(tmp_path):
    d = tmp_path / "20260101-000000Z-broken"
    d.mkdir()
    (d / "run.json").write_text("{not json", encoding="utf-8")
    rows, dry = run_index.collect(tmp_path)
    assert rows == []
    assert any("unreadable" in name for name in dry)


def test_runs_are_ordered_by_creation_not_by_directory_listing(tmp_path):
    _write_run(tmp_path, "zzz-late-name", _meta(created_utc="2026-01-01T00:00:00+00:00", arm="first"))
    _write_run(tmp_path, "aaa-early-name", _meta(created_utc="2026-06-01T00:00:00+00:00", arm="second"))
    rows, _ = run_index.collect(tmp_path)
    assert [r["arm"] for r in rows] == ["first", "second"]


def test_a_missing_runs_directory_is_empty_not_an_error(tmp_path):
    rows, dry = run_index.collect(tmp_path / "does-not-exist")
    assert (rows, dry) == ([], [])


# ------------------------------------------------------------------ rendering


def test_a_clean_run_renders_one_ok_token():
    assert run_index._outcomes({"ok": 60}) == "ok=60"


def test_every_failure_outcome_is_named_never_totalled():
    """`empty_length` is a run-configuration bug and `empty_other` is a fact about the
    model (`report.py:727-730`). A single "errors" count would hide that difference."""
    rendered = run_index._outcomes({"ok": 50, "empty_other": 3, "schema_violation": 7})
    assert "empty_other=3" in rendered
    assert "schema_violation=7" in rendered
    assert "ok=50" in rendered


def test_a_split_arm_is_rendered_loudly(tmp_path):
    """The 2026-08-04 defect was one model id served by two backends, invisible because
    nothing rendered it. If a split ever prints like a clean run, this column is dead."""
    meta = _meta(split_items={"RET-03": [2538, 3691], "RET-07": [2540, 3700]})
    clean = run_index._pin_proof(_meta())
    split = run_index._pin_proof(meta)
    assert clean == "20/20"
    assert "SPLIT" in split and "**" in split
    assert split != clean


def test_one_replicate_cannot_prove_a_pin_and_says_so():
    """A single replicate cannot disagree with itself, so `20/20` there would be
    arithmetic dressed as evidence -- the same refusal `cli._print_backend_identity`
    makes.

    The assertion is "renders no N/N fraction", not "contains no slash": the honest
    answer this returns is `n/a (1 rep)`, and `n/a` contains a slash of its own. An
    earlier version of this test checked for the character and failed against correct
    output -- the test was wrong, not the code.
    """
    proof = run_index._pin_proof(_meta(repeats=1))
    assert "n/a" in proof
    assert not re.search(r"\d+\s*/\s*\d+", proof), (
        f"a 1-replicate run rendered a fraction ({proof!r}), which reads as pin evidence "
        "it cannot possibly be"
    )


def test_an_unpinned_run_is_marked_not_left_blank(tmp_path):
    _write_run(tmp_path, "20260101-000000Z-arm", _meta(provider_requested=None))
    rows, dry = run_index.collect(tmp_path)
    assert "*unpinned*" in run_index.render(rows, dry)


def test_cost_is_labelled_a_lower_bound():
    """Quoted without that word the number reads as a total, and it is not: providers
    that report no cost are skipped rather than counted as free (`runner.total_cost`)."""
    text = run_index.render([_meta()], [])
    assert "LOWER BOUND" in text


def test_the_total_sums_only_runs_that_reported_a_cost(tmp_path):
    rows = [_meta(total_cost_usd_lower_bound=0.25), _meta(total_cost_usd_lower_bound=None)]
    text = run_index.render(rows, [])
    assert "$0.2500" in text


# ------------------------------------------------------------------ the --check gate


def test_check_passes_when_the_index_is_current(tmp_path, capsys):
    _write_run(tmp_path, "20260101-000000Z-arm", _meta())
    out = tmp_path / "RUNS.md"
    assert run_index.main(["--runs", str(tmp_path), "--out", str(out)]) == 0
    assert run_index.main(["--runs", str(tmp_path), "--out", str(out), "--check"]) == 0


def test_check_FAILS_when_a_run_was_added_after_the_index_was_written(tmp_path):
    """The mutation that proves the gate works. Without this the check could return 0
    unconditionally and every test above would still pass."""
    _write_run(tmp_path, "20260101-000000Z-arm", _meta())
    out = tmp_path / "RUNS.md"
    run_index.main(["--runs", str(tmp_path), "--out", str(out)])

    _write_run(tmp_path, "20260102-000000Z-arm2", _meta(arm="arm2"))
    assert run_index.main(["--runs", str(tmp_path), "--out", str(out), "--check"]) == 1


def test_check_FAILS_when_the_index_does_not_exist(tmp_path):
    _write_run(tmp_path, "20260101-000000Z-arm", _meta())
    missing = tmp_path / "nope.md"
    assert run_index.main(["--runs", str(tmp_path), "--out", str(missing), "--check"]) == 1


def test_generation_is_idempotent(tmp_path):
    _write_run(tmp_path, "20260101-000000Z-arm", _meta())
    out = tmp_path / "RUNS.md"
    run_index.main(["--runs", str(tmp_path), "--out", str(out)])
    first = out.read_text(encoding="utf-8")
    run_index.main(["--runs", str(tmp_path), "--out", str(out)])
    assert out.read_text(encoding="utf-8") == first


def test_the_index_carries_no_model_output(tmp_path):
    """The whole reason this file may be committed while `out/` may not.

    `run.jsonl` holds the model's text verbatim; if the index ever read it, `RUNS.md`
    would become an untracked-data leak wearing a tracked filename.
    """
    d = _write_run(tmp_path, "20260101-000000Z-arm", _meta())
    (d / "run.jsonl").write_text(
        json.dumps({"raw_content": "SECRET-MODEL-OUTPUT-SHOULD-NEVER-APPEAR"}) + "\n",
        encoding="utf-8",
    )
    rows, dry = run_index.collect(tmp_path)
    assert "SECRET-MODEL-OUTPUT-SHOULD-NEVER-APPEAR" not in run_index.render(rows, dry)


@pytest.mark.parametrize("field", ["testset_sha", "scorer_sha", "prompt_id"])
def test_provenance_fields_reach_the_table(tmp_path, field):
    marker = {"testset_sha": "d" * 64, "scorer_sha": "beefcafe", "prompt_id": "v9_16_e1"}[field]
    _write_run(tmp_path, "20260101-000000Z-arm", _meta(**{field: marker}))
    rows, dry = run_index.collect(tmp_path)
    assert marker[:8] in run_index.render(rows, dry)
