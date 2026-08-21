"""`scripts/verify.py` is the command that says whether this repository is verified,
so the ways it could lie are the only tests worth writing.

There are three, and each has a section below.

  * **It could exit 0 while a gate failed.** Then the one command everybody trusts is
    the one command nobody should.
  * **It could count a SKIP as a PASS.** Four of six gates here depend on generated
    data that is not committed, so on a fresh clone the majority of the run skips. A
    summary that folds those into "passed" would report a green build for a checkout
    that has verified two things out of six.
  * **It could skip when the TOOLING is missing rather than the DATA.** This is the
    subtle one and the reason the runner exists at all. "The corpus has not been built
    yet" is a fact about the workspace; "the checker is gone" is the failure a
    verification command is for. If a deleted venv or a renamed script reads as SKIP,
    the harness quietly stops checking itself and keeps printing a reassuring summary.

The closed list of steps allowed to skip is pinned here rather than described in the
docstring, so moving a tooling failure into it breaks a build rather than a rule.

One test at the bottom re-measures the fact the whole SKIP design rests on: that
`leak_probe.py` exits NON-zero when `business.csv` is absent. If that ever stops being
true the skip precondition in `verify.py` is arguing against a straw man, and the
comment explaining it is wrong.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def _load():
    """Load `scripts/verify.py` by path.

    By path rather than by name for the reason `tests/test_run_index.py` records:
    `scripts/` holds `evalgen.py`, a launcher, so putting that directory on `sys.path`
    makes `import evalgen.cli` resolve to the launcher.

    The `sys.modules` line is not decoration. `verify.py` uses `@dataclass` under
    `from __future__ import annotations`, so every annotation is a string, and
    `dataclasses._is_type` resolves them through `sys.modules[cls.__module__]`. Load it
    without registering and the class body raises
    `AttributeError: 'NoneType' object has no attribute '__dict__'`.
    """
    spec = importlib.util.spec_from_file_location(
        "_verify_under_test", ROOT / "scripts" / "verify.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


verify = _load()

# The interpreter running this test. `interpreter()` accepts a venv root or a bare
# install prefix, and `sys.prefix` is whichever of those pytest was launched from.
THIS_PYTHON_HOME = Path(sys.prefix)

ASR_VENV = ROOT / ".venv-asr"


def _step(tmp_path: Path, name: str, body: str, **kwargs) -> "verify.Step":
    """A real step that runs a real subprocess, so the plumbing is never stubbed out."""
    script = tmp_path / f"{name.replace(' ', '_')}.py"
    script.write_text(body, encoding="utf-8")
    kwargs.setdefault("venv", THIS_PYTHON_HOME)
    kwargs.setdefault("cwd", tmp_path)
    return verify.Step(name=name, args=(str(script),), **kwargs)


def _result(name: str, status: str, seconds: float = 1.0) -> "verify.Result":
    return verify.Result(
        name=name, status=status, seconds=seconds, detail="", command="x"
    )


# --------------------------------------------------------------------------------------
# The plan: the right gates, in the right order, each on the interpreter that can run it.
# --------------------------------------------------------------------------------------

def test_the_plan_is_every_gate_in_the_documented_order() -> None:
    names = [step.name for step in verify.plan(ROOT)]
    assert names[:6] == [
        "root pytest suite",
        "asr-eval pytest suite",
        "score_asr self-test",
        "outcome leak probe",
        "audio contract checks",
        # Added 2026-08-21. doc_claims covered one document and ran outside this runner,
        # so every ASR-track figure was published unchecked -- and two went stale in a
        # single day before anyone noticed. Last of the static gates: it checks what the
        # others produced.
        "published figures match their source",
    ]
    # Step 7 fans out to one step per committed plan so each reports separately.
    assert names[6:], "experiment-check contributed no steps"
    assert all(name.startswith("experiment-check ") for name in names[6:])
    assert len(names[6:]) == len(sorted((ROOT / "experiments").glob("*.plan.json")))


def test_each_gate_runs_on_the_interpreter_that_can_import_its_dependencies() -> None:
    """The reason this is a Python runner and not a shell script.

    `.venv` has pandas and no soundfile; `.venv-asr` has soundfile and no pandas. A
    step pointed at the wrong one does not fail subtly, it fails on the first import --
    but only when somebody runs it, which is the situation this file exists to end.
    """
    venvs = {step.name: step.venv.name for step in verify.plan(ROOT)}
    assert venvs["root pytest suite"] == ".venv"
    assert venvs["asr-eval pytest suite"] == ".venv-asr"
    assert venvs["score_asr self-test"] == ".venv-asr"
    assert venvs["outcome leak probe"] == ".venv-asr"
    assert venvs["audio contract checks"] == ".venv-asr"
    assert all(
        name == ".venv" for step, name in venvs.items() if step.startswith("experiment-check")
    )


def test_the_root_gates_carry_pythonpath_src() -> None:
    """Without it the root suite and experiment-check cannot import `evalharness`."""
    for step in verify.plan(ROOT):
        if step.venv.name == ".venv":
            assert dict(step.env).get("PYTHONPATH", "").endswith("src"), step.name


def test_quick_is_exactly_the_two_test_suites() -> None:
    assert [step.name for step in verify.plan(ROOT, quick=True)] == [
        "root pytest suite",
        "asr-eval pytest suite",
    ]


# --------------------------------------------------------------------------------------
# A failing gate fails the run. End to end, exit code included.
# --------------------------------------------------------------------------------------

def test_a_failing_step_is_reported_as_fail(tmp_path: Path) -> None:
    step = _step(tmp_path, "boom", "import sys\nprint('the gate said no')\nsys.exit(3)\n")
    result = verify.run_step(step)
    assert result.status == verify.FAIL
    assert "exit 3" in result.detail
    assert "the gate said no" in result.output


def test_a_failing_step_makes_the_whole_run_exit_non_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """The property everything else depends on, exercised through `main`."""
    steps = [
        _step(tmp_path, "ok", "print('fine')\n"),
        _step(tmp_path, "bad", "import sys\nsys.exit(1)\n"),
    ]
    monkeypatch.setattr(verify, "plan", lambda repo=None, *, quick=False: steps)

    assert verify.main([]) == 1

    printed = capsys.readouterr().out
    assert "VERIFY FAILED" in printed
    assert "VERIFY PASS" not in printed


def test_one_failure_outranks_any_number_of_skips() -> None:
    """FAIL is checked before INCOMPLETE, or a skip could mask a broken gate."""
    results = [
        _result("a", verify.PASS),
        _result("b", verify.SKIP),
        _result("c", verify.FAIL),
    ]
    assert verify.summarize(results, quick=False) == 1


def test_a_hung_gate_is_a_failure_and_not_a_hang(tmp_path: Path) -> None:
    """A gate that never returns has checked nothing, so it cannot be left running."""
    step = _step(tmp_path, "hang", "import time\ntime.sleep(30)\n")
    result = verify.run_step(step, timeout=1.0)
    assert result.status == verify.FAIL
    assert "timed out" in result.detail
    assert result.seconds < 20, "the timeout did not actually cut the child off"


# --------------------------------------------------------------------------------------
# A skip is never a pass.
# --------------------------------------------------------------------------------------

def test_a_skip_does_not_count_as_a_pass_in_the_summary(
    capsys: pytest.CaptureFixture,
) -> None:
    results = [_result("ran", verify.PASS), _result("did not run", verify.SKIP)]

    code = verify.summarize(results, quick=False)

    printed = capsys.readouterr().out
    assert code == 2, "a run with an unexecuted gate reported itself complete"
    assert "1 passed, 0 failed, 1 skipped" in printed
    assert "VERIFY PASS" not in printed
    assert "VERIFY INCOMPLETE" in printed
    assert "did not run" in printed, "the skipped gate was not named"


def test_a_mostly_skipped_run_does_not_read_as_green(
    capsys: pytest.CaptureFixture,
) -> None:
    """Four of six skipped is the fresh-clone case, and it must be loud."""
    results = [_result(f"gate {i}", verify.PASS) for i in range(2)]
    results += [_result(f"gate {i}", verify.SKIP) for i in range(2, 6)]

    code = verify.summarize(results, quick=False)

    printed = capsys.readouterr().out
    assert code == 2
    assert "2 passed, 0 failed, 4 skipped" in printed
    assert "4 of 6 gates did not run" in printed
    assert "VERIFY PASS" not in printed


def test_only_an_entirely_clean_run_prints_verify_pass(
    capsys: pytest.CaptureFixture,
) -> None:
    results = [_result(f"gate {i}", verify.PASS) for i in range(3)]
    assert verify.summarize(results, quick=False) == 0
    assert "VERIFY PASS -- all 3 gates ran and passed." in capsys.readouterr().out


def test_quick_never_reports_a_complete_pass(capsys: pytest.CaptureFixture) -> None:
    """`--quick` skipped four gates by construction; passing two cannot clear the tree."""
    results = [_result(f"gate {i}", verify.PASS) for i in range(2)]
    assert verify.summarize(results, quick=True) == 2
    assert "VERIFY PASS" not in capsys.readouterr().out


def test_a_skipped_step_spawns_nothing(tmp_path: Path) -> None:
    """The precondition is checked BEFORE the subprocess, which is the whole point.

    `leak_probe.py` exits non-zero on a missing corpus, so running it and reading the
    exit code cannot produce a SKIP -- the decision has to be made here, first.
    """
    sentinel = tmp_path / "it-ran"
    step = _step(
        tmp_path,
        "would touch a file",
        f"open({str(sentinel)!r}, 'w').close()\n",
        generated=(verify.Generated("data not generated", lambda: False),),
    )

    result = verify.run_step(step)

    assert result.status == verify.SKIP
    assert result.detail == "data not generated"
    assert not sentinel.exists(), "the gate ran despite its precondition being absent"


# --------------------------------------------------------------------------------------
# Missing tooling FAILS. Skips are for missing data, and the list is closed.
# --------------------------------------------------------------------------------------

def test_a_missing_venv_fails_rather_than_skips(tmp_path: Path) -> None:
    step = _step(tmp_path, "orphan", "print('never runs')\n", venv=tmp_path / "no-such-venv")
    result = verify.run_step(step)
    assert result.status == verify.FAIL
    assert result.status != verify.SKIP
    assert "no interpreter" in result.detail
    assert "not missing data" in result.detail


def test_a_missing_script_fails_rather_than_skips(tmp_path: Path) -> None:
    step = _step(
        tmp_path,
        "renamed",
        "print('never runs')\n",
        tooling=(tmp_path / "score_asr.py",),
    )
    result = verify.run_step(step)
    assert result.status == verify.FAIL
    assert "missing tooling" in result.detail


def test_a_missing_working_directory_fails_rather_than_skips(tmp_path: Path) -> None:
    step = _step(tmp_path, "nowhere", "print('never runs')\n", cwd=tmp_path / "gone")
    result = verify.run_step(step)
    assert result.status == verify.FAIL
    assert "working directory" in result.detail


def test_missing_tooling_outranks_missing_data(tmp_path: Path) -> None:
    """The order matters: SKIP here would report "no data yet" about a broken machine."""
    step = _step(
        tmp_path,
        "both gone",
        "print('never runs')\n",
        venv=tmp_path / "no-such-venv",
        generated=(verify.Generated("data not generated", lambda: False),),
    )
    assert verify.run_step(step).status == verify.FAIL


def test_an_empty_plan_glob_is_a_refusal_not_a_silent_pass(tmp_path: Path) -> None:
    """A glob matching nothing is how a gate stops testing while still reporting success."""
    (tmp_path / "experiments").mkdir()

    steps = verify.plan(tmp_path)
    experiment_steps = [s for s in steps if s.name.startswith("experiment-check")]
    assert len(experiment_steps) == 1

    result = verify.run_step(experiment_steps[0])
    assert result.status == verify.FAIL
    assert "verified nothing" in result.detail


def test_the_list_of_gates_allowed_to_skip_is_closed() -> None:
    """Only generated data may excuse a gate. Widening this is a reviewed change.

    `business.csv` comes out of `compose_dialogues.py` and the wavs out of
    `synthesize.py`; neither is committed. Every other gate's inputs are in Git, so
    there is no honest reason for one to skip.
    """
    skippable = {step.name for step in verify.plan(ROOT) if step.generated}
    assert skippable == {"outcome leak probe", "audio contract checks"}


def test_the_skip_preconditions_watch_the_real_paths(tmp_path: Path) -> None:
    """A precondition pointed at the wrong path skips forever and is never noticed."""
    (tmp_path / "experiments").mkdir()
    ground_truth = tmp_path / "asr-eval" / "ground-truth"
    audio = tmp_path / "asr-eval" / "audio"
    ground_truth.mkdir(parents=True)
    audio.mkdir(parents=True)

    def probes(repo: Path) -> dict[str, bool]:
        return {
            step.name: all(g.present() for g in step.generated)
            for step in verify.plan(repo)
            if step.generated
        }

    assert probes(tmp_path) == {
        "outcome leak probe": False,
        "audio contract checks": False,
    }

    (ground_truth / "business.csv").write_text("call_id,call_result\n", encoding="utf-8")
    (audio / "7100_x.wav").write_bytes(b"RIFF")

    assert probes(tmp_path) == {
        "outcome leak probe": True,
        "audio contract checks": True,
    }


# --------------------------------------------------------------------------------------
# Reporting: duration per step, and a PASS that carries the gate's own summary line.
# --------------------------------------------------------------------------------------

def test_each_step_records_its_own_duration(tmp_path: Path) -> None:
    slow = verify.run_step(_step(tmp_path, "slow", "import time\ntime.sleep(0.4)\n"))
    assert slow.status == verify.PASS
    assert slow.seconds >= 0.4


def test_a_pass_carries_the_gate_s_own_last_line(tmp_path: Path) -> None:
    """So `PASS` is never a word with nothing behind it, and pytest's own skips show."""
    step = _step(tmp_path, "summarising", "print('840 passed, 12 skipped in 18.2s')\n")
    assert verify.run_step(step).detail == "840 passed, 12 skipped in 18.2s"


def test_thai_output_survives_the_pipe(tmp_path: Path) -> None:
    """Every ASR gate prints Thai. Decoding it with the Windows codepage loses the run."""
    step = _step(tmp_path, "thai", "print('\\u0e2a\\u0e27\\u0e31\\u0e2a\\u0e14\\u0e35')\n")
    result = verify.run_step(step)
    assert result.status == verify.PASS
    assert "สวัสดี" in result.output


# --------------------------------------------------------------------------------------
# The measured facts the design rests on. These re-check the environment, not the code.
# --------------------------------------------------------------------------------------

@pytest.mark.skipif(
    verify.interpreter(ASR_VENV) is None,
    reason="no .venv-asr in this checkout; the ASR gates cannot be exercised here",
)
def test_the_leak_probe_really_does_exit_non_zero_without_the_corpus(
    tmp_path: Path,
) -> None:
    """The fact the SKIP precondition exists for.

    If `leak_probe.py` ever starts exiting 0 on a missing corpus, the comment in
    `verify.py` explaining why the precondition is checked before the subprocess is
    describing something that no longer happens -- and a reader would be entitled to
    delete the precondition. `ASR_EVAL_ROOT` points the probe at an empty tree so this
    holds whether or not the real corpus has been generated.
    """
    probe = ROOT / "asr-eval" / "scripts" / "leak_probe.py"
    if not probe.exists():
        pytest.skip("leak_probe.py is not present in this checkout")
    (tmp_path / "ground-truth").mkdir()

    completed = subprocess.run(
        [str(verify.interpreter(ASR_VENV)), "leak_probe.py"],
        cwd=str(probe.parent),
        env={**os.environ, "ASR_EVAL_ROOT": str(tmp_path), "PYTHONIOENCODING": "utf-8"},
        capture_output=True,
        timeout=120,
    )

    assert completed.returncode != 0, (
        "leak_probe.py now exits 0 with no business.csv. verify.py's SKIP precondition "
        "is documented as preventing a false FAIL; re-read both before changing either."
    )
    assert b"business.csv" in completed.stdout + completed.stderr
