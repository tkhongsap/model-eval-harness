"""Run every gate this repository has, in order, and refuse to call the run green
unless they all actually ran.

    python scripts/verify.py            # all gates
    python scripts/verify.py --quick    # the two test suites only, ~2 minutes

WHY THIS EXISTS. The gates were built one at a time, and each one only fires when a
human remembers to type it. `.github/workflows/ci.yml` covers one half of the
repository -- the root suite, the testset packs, the run index -- and nothing at all
under `asr-eval/`: not the ASR suite, not `score_asr.py --self-test`, not the outcome
leak probe, not `validate_audio.py`. Four gates that exist to stop a wrong number
reaching a decision document have no scheduled reader. This is the single command that
reads them.

WHY A RUNNER AND NOT A SHELL SCRIPT. Two reasons, both measured rather than assumed.

  1. THE GATES DO NOT SHARE AN INTERPRETER. `.venv` carries production's pinned pandas
     and no `soundfile`; `.venv-asr` carries `numpy`/`scipy`/`soundfile` and no
     `pandas`. Checked 2026-08-18: each venv raises ModuleNotFoundError on the other's
     first import. "Which python" is a per-step fact, so a script that says `python`
     once at the top runs at most half of these gates.

  2. AN EXIT CODE ALONE CANNOT TELL "THE GATE FAILED" FROM "THE GATE HAD NOTHING TO
     MEASURE". `asr-eval/ground-truth/business.csv` is written by
     `compose_dialogues.py` and is not committed, so it is absent in a clean checkout
     (absent here on 2026-08-18). `leak_probe.py` handles that honestly for a human --
     it prints "no business labels at ..." -- but it exits **1** (`leak_probe.py:106`),
     which is indistinguishable from a detected leak. A runner that only read exit
     codes would show step 4 red on every fresh clone, and a gate that is red by
     default is a gate people learn to scroll past. The same holds for
     `validate_audio.py`, which records "no wav found for this dialogue"
     (`validate_audio.py:310`) and exits 1 when `asr-eval/audio/` has not been
     synthesized. So the data preconditions are checked HERE, before the subprocess,
     and reported as SKIP.

SKIP IS NOT PASS. A run where four of six gates skipped has verified almost nothing,
and the way that becomes dangerous is if it prints something a reader can quote as
"verify passed". It cannot: the only line that says every gate ran and passed is
printed when every gate ran and passed. Anything else names the gates that did not run
and why. Exit codes carry the same three-way distinction, because in CI the exit code
IS the summary:

    0   every gate was selected, ran, and passed
    1   at least one gate failed
    2   nothing failed, but the run is incomplete -- something skipped, or --quick

There is deliberately no flag that turns 2 into 0. The way to earn a 0 is to generate
the data the skipped gate needs, not to tell this script to stop counting.

SKIPS ARE FOR MISSING GENERATED DATA, NEVER FOR MISSING TOOLING. A venv that is not
installed, a script that has been renamed, a plan glob that matches nothing: all FAIL.
The distinction is the whole point. "We could not check this because the corpus has
not been built yet" is a fact about the workspace; "we could not check this because
the checker is gone" is the exact condition a verification command exists to catch.
`tests/test_verify.py` pins the closed list of steps allowed to skip, so moving a
tooling failure into that list breaks a build rather than a rule.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

REPO = Path(__file__).resolve().parent.parent

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"

# A gate that hangs is a gate that never reports. Thirty minutes is far above the
# slowest thing here (the root suite, tens of seconds) and far below "somebody went
# home". A timeout is a FAIL, never a SKIP: a hung checker has not checked anything.
DEFAULT_TIMEOUT_S = 1800

# How much of a failed gate's output to echo. The full text is one rerun away, which is
# why every step prints the exact command it ran.
TAIL_LINES = 40


@dataclass(frozen=True)
class Generated:
    """A precondition that is DATA, and therefore a legitimate reason to SKIP.

    `present()` must answer "has this been generated?" and nothing else. Do not reach
    for this to paper over a missing tool -- see the module docstring.
    """

    description: str
    present: Callable[[], bool]


@dataclass(frozen=True)
class Step:
    """One gate: an interpreter, an argv, a working directory and its preconditions."""

    name: str
    venv: Path
    args: tuple[str, ...]
    cwd: Path
    # Paths that must exist or the step FAILS. Tooling, never data.
    tooling: tuple[Path, ...] = ()
    # Data preconditions. Absent means SKIP.
    generated: tuple[Generated, ...] = ()
    env: tuple[tuple[str, str], ...] = ()
    # Included by --quick. The fast pair, nothing else.
    quick: bool = False
    # Set to make the step FAIL without spawning anything. For conditions discovered
    # while building the plan -- an empty plan glob -- which must still be reported in
    # running order rather than aborting the suites that precede them.
    refusal: str = ""


@dataclass(frozen=True)
class Result:
    name: str
    status: str
    seconds: float
    detail: str
    command: str
    output: str = ""


def interpreter(venv: Path) -> Path | None:
    """The python inside `venv`, or None if there is not one.

    Three layouts, because this has to resolve on all of them: Windows venvs put it in
    `Scripts/`, POSIX venvs in `bin/`, and a bare system install puts it at the root of
    its prefix -- which is what `sys.prefix` looks like when pytest was not launched
    from a venv, so the tests depend on that last case.
    """
    for relative in (
        "Scripts/python.exe",
        "bin/python",
        "bin/python3",
        "python.exe",
        "python",
    ):
        candidate = venv / relative
        if candidate.is_file():
            return candidate
    return None


def child_env(step: Step) -> dict[str, str]:
    env = dict(os.environ)
    env.update(dict(step.env))
    # Thai transcripts travel through these pipes. A child writing to a pipe on Windows
    # picks the locale codepage (cp874 or worse), and a gate that dies of
    # UnicodeEncodeError while printing its own PASS line is a failure this runner
    # invented. Force UTF-8 on the child; decode as UTF-8 below.
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def last_meaningful_line(text: str) -> str:
    """The line worth putting beside PASS.

    Every gate here ends by summarising itself -- pytest's "840 passed, 12 skipped",
    the self-test's verdict, experiment-check's "No model call was made". Showing it
    keeps PASS from being a word with nothing behind it, and it is how pytest's own
    skips stay visible instead of hiding inside this script's green.
    """
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        if stripped:
            return stripped if len(stripped) <= 96 else stripped[:93] + "..."
    return ""


def render_command(step: Step) -> str:
    found = interpreter(step.venv)
    binary = str(found) if found else f"{step.venv}/<no interpreter>"
    return " ".join([binary, *step.args])


def run_step(step: Step, *, timeout: float = DEFAULT_TIMEOUT_S) -> Result:
    """Execute one gate. Returns a Result and prints nothing."""
    command = render_command(step)
    started = time.monotonic()

    def done(status: str, detail: str, output: str = "") -> Result:
        return Result(
            name=step.name,
            status=status,
            seconds=time.monotonic() - started,
            detail=detail,
            command=command,
            output=output,
        )

    if step.refusal:
        return done(FAIL, step.refusal)

    # Tooling before data, and the order is load-bearing. When the venv is missing AND
    # the corpus is missing, the honest report is FAIL: SKIP would say "no data yet"
    # about a machine that could not have run the gate even with the data.
    python = interpreter(step.venv)
    if python is None:
        return done(
            FAIL,
            f"no interpreter under {step.venv} -- create the venv; this is missing "
            f"tooling, not missing data",
        )
    for required in step.tooling:
        if not required.exists():
            return done(FAIL, f"missing tooling: {required}")
    if not step.cwd.is_dir():
        return done(FAIL, f"missing tooling: working directory {step.cwd}")

    for precondition in step.generated:
        if not precondition.present():
            return done(SKIP, precondition.description)

    try:
        completed = subprocess.run(
            [str(python), *step.args],
            cwd=str(step.cwd),
            env=child_env(step),
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return done(FAIL, f"timed out after {timeout:.0f}s -- a hung gate checked nothing")
    except OSError as exc:
        return done(FAIL, f"could not start: {exc}")

    output = (completed.stdout + completed.stderr).decode("utf-8", errors="replace")
    if completed.returncode != 0:
        return done(
            FAIL,
            f"exit {completed.returncode}: {last_meaningful_line(output)}",
            output,
        )
    return done(PASS, last_meaningful_line(output), output)


def plan(repo: Path = REPO, *, quick: bool = False) -> list[Step]:
    """The gates, in the order a human would run them: cheapest broad signal first."""
    root_venv = repo / ".venv"
    asr_venv = repo / ".venv-asr"
    asr_scripts = repo / "asr-eval" / "scripts"
    business_csv = repo / "asr-eval" / "ground-truth" / "business.csv"
    audio_dir = repo / "asr-eval" / "audio"

    steps: list[Step] = [
        Step(
            name="root pytest suite",
            venv=root_venv,
            args=("-m", "pytest", "tests/", "-q"),
            cwd=repo,
            tooling=(repo / "tests",),
            env=(("PYTHONPATH", str(repo / "src")),),
            quick=True,
        ),
        Step(
            name="asr-eval pytest suite",
            venv=asr_venv,
            args=("-m", "pytest", "asr-eval/tests/", "-q"),
            cwd=repo,
            tooling=(repo / "asr-eval" / "tests",),
            quick=True,
        ),
        Step(
            name="score_asr self-test",
            venv=asr_venv,
            args=("score_asr.py", "--self-test"),
            cwd=asr_scripts,
            tooling=(asr_scripts / "score_asr.py",),
        ),
        Step(
            name="outcome leak probe",
            venv=asr_venv,
            args=("leak_probe.py",),
            cwd=asr_scripts,
            tooling=(asr_scripts / "leak_probe.py",),
            generated=(
                Generated(
                    "corpus not generated: asr-eval/ground-truth/business.csv is "
                    "written by compose_dialogues.py and is not committed",
                    business_csv.exists,
                ),
            ),
        ),
        Step(
            name="audio contract checks",
            venv=asr_venv,
            args=("validate_audio.py",),
            cwd=asr_scripts,
            tooling=(asr_scripts / "validate_audio.py",),
            generated=(
                Generated(
                    "audio not synthesized: no *.wav under asr-eval/audio/",
                    lambda: any(audio_dir.glob("*.wav")),
                ),
            ),
        ),
        Step(
            # Published figures against their source. This lived outside the runner while
            # it covered a single document; it now covers the ASR track too, and a gate
            # nobody runs is a gate that does not exist. Last of the static steps because
            # it checks what the others produced.
            name="published figures match their source",
            venv=root_venv,
            args=("scripts/doc_claims.py", "--check"),
            cwd=repo,
            env=(("PYTHONPATH", str(repo / "src")),),
            tooling=(repo / "scripts" / "doc_claims.py",),
        ),
    ]

    if quick:
        return [step for step in steps if step.quick]

    # One step per plan, not one step covering all of them: a plan that stops
    # validating should name itself, and each carries its own duration.
    plans = sorted((repo / "experiments").glob("*.plan.json"))
    if not plans:
        # A glob that matches nothing is how a gate quietly stops testing anything
        # while continuing to report success. A refusal, not a skip: the plans are
        # committed files, so their absence is a broken checkout, not missing data.
        steps.append(
            Step(
                name="experiment-check",
                venv=root_venv,
                args=(),
                cwd=repo,
                refusal="no plan matched experiments/*.plan.json -- this gate would "
                "have verified nothing and reported success",
            )
        )
    for plan_path in plans:
        steps.append(
            Step(
                name=f"experiment-check {plan_path.name}",
                venv=root_venv,
                args=(
                    "scripts/evalgen.py",
                    "experiment-check",
                    "--plan",
                    plan_path.relative_to(repo).as_posix(),
                ),
                cwd=repo,
                tooling=(repo / "scripts" / "evalgen.py", plan_path),
                env=(("PYTHONPATH", str(repo / "src")),),
            )
        )
    return steps


def summarize(results: Sequence[Result], *, quick: bool, out=None) -> int:
    """Print the verdict and return the exit code. The three states never blur.

    `out` resolves at call time, not as a default argument. A default of `sys.stdout`
    binds the stream that existed at import, so anything that replaces `sys.stdout`
    afterwards -- a test harness, a caller teeing the summary to a file -- is silently
    written past.
    """
    out = sys.stdout if out is None else out
    passed = [r for r in results if r.status == PASS]
    failed = [r for r in results if r.status == FAIL]
    skipped = [r for r in results if r.status == SKIP]
    total = sum(r.seconds for r in results)

    print("", file=out)
    print("-" * 78, file=out)
    print(
        f"{len(results)} gates: {len(passed)} passed, {len(failed)} failed, "
        f"{len(skipped)} skipped in {total:.1f}s",
        file=out,
    )

    for result in failed:
        print(f"  FAIL  {result.name}: {result.detail}", file=out)
    for result in skipped:
        print(f"  SKIP  {result.name}: {result.detail}", file=out)

    print("", file=out)
    if failed:
        print(f"VERIFY FAILED -- {len(failed)} of {len(results)} gates failed.", file=out)
        return 1

    if quick:
        print(
            "VERIFY INCOMPLETE -- --quick ran the two test suites only. The self-test, "
            "the leak probe, the audio contract and experiment-check did not run.",
            file=out,
        )
        return 2

    if skipped:
        print(
            f"VERIFY INCOMPLETE -- {len(skipped)} of {len(results)} gates did not run. "
            f"Nothing failed, but nothing above shows those gates would pass. Generate "
            f"the data they name and run this again.",
            file=out,
        )
        return 2

    print(f"VERIFY PASS -- all {len(results)} gates ran and passed.", file=out)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run every gate in this repository and report PASS/FAIL/SKIP."
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="run only the two test suites (~2 min). Never reports a complete pass.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_S,
        help=f"per-step timeout in seconds (default {DEFAULT_TIMEOUT_S:.0f}).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="echo every gate's full output, not just a failed gate's tail.",
    )
    args = parser.parse_args(argv)

    # The same trap as PYTHONIOENCODING, on our own side: this echoes a failed gate's
    # output, and some of that output is Thai.
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass

    steps = plan(REPO, quick=args.quick)
    results: list[Result] = []

    for index, step in enumerate(steps, 1):
        print(f"[{index}/{len(steps)}] {step.name}")
        print(f"        $ {render_command(step)}   (cwd {step.cwd})")
        sys.stdout.flush()

        result = run_step(step, timeout=args.timeout)
        results.append(result)

        print(f"        {result.status}  {result.seconds:6.1f}s  {result.detail}")
        if args.verbose and result.output:
            for line in result.output.splitlines():
                print(f"        | {line}")
        elif result.status == FAIL and result.output:
            lines = result.output.splitlines()
            if len(lines) > TAIL_LINES:
                print(f"        | ... {len(lines) - TAIL_LINES} earlier lines omitted")
            for line in lines[-TAIL_LINES:]:
                print(f"        | {line}")
        sys.stdout.flush()

    return summarize(results, quick=args.quick)


if __name__ == "__main__":
    raise SystemExit(main())
