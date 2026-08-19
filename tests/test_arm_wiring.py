"""The arm-to-transcript wiring, which decides whose numbers are whose.

WHY THIS FILE EXISTS. `experiment21_pipeline_delta.py` mapped each text arm to its
hypothesis directory in TWO hand-written tuples -- one in `build_inputs`, which loads the
transcripts the model actually reads, and one in `main`, which records `input_paths` for the
provenance and drift refusal. Two copies of the same mapping, forty lines apart.

That is the worst possible place in this repository for a copy-paste to drift. If the two
disagree, the run labels one arm's calls with another arm's transcripts while the provenance
block certifies the arm it *meant* to read. Nothing downstream can catch it: every record is
well formed, every count is right, the F1 is computed cleanly, and the number is wrong.

Adding the typhoon arm required touching both copies, which is exactly the edit during which
that happens. They are now one dict, `HYP_DIRS`, and these tests keep them one.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
E21_PATH = REPO / "scripts" / "experiment21_pipeline_delta.py"
SOURCE = E21_PATH.read_text(encoding="utf-8")


def _literals(*names: str) -> dict:
    """Read module-level literal assignments WITHOUT executing the module.

    Deliberately static, and CI is the reason. `experiment21_pipeline_delta` imports `httpx`
    at module scope because it calls models; `requirements.txt` pins production's scoring
    dependencies EXACTLY and contains no HTTP client. Importing the module here therefore
    passed locally -- where `.venv` has httpx as a transitive dependency of the openai
    client -- and failed collection on CI with ModuleNotFoundError, taking the whole suite
    down with it.

    Adding httpx to requirements.txt would have been the wrong repair: those pins mirror the
    production app, and `tests/test_boundary.py` exists precisely to stop a model client
    creeping into the scoring path. A test that asserts how the arms are wired has no need
    to open a socket, so it reads the literals instead.
    """
    tree = ast.parse(SOURCE)
    found: dict = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id in names:
                found[target.id] = ast.literal_eval(node.value)
    missing = set(names) - set(found)
    if missing:
        raise AssertionError(
            f"{sorted(missing)} is no longer a module-level literal in {E21_PATH.name}. "
            "If it became computed, these assertions stopped checking anything and need "
            "rewriting rather than deleting.")
    return found


_LITERALS = _literals("ARMS", "HYP_DIRS")
ARMS = _LITERALS["ARMS"]
HYP_DIRS = _LITERALS["HYP_DIRS"]


def test_every_hypothesis_backed_arm_is_a_declared_arm():
    """A mapping entry for an arm that does not exist would never load anything."""
    unknown = set(HYP_DIRS) - set(ARMS)
    assert not unknown, f"HYP_DIRS names arms that are not in ARMS: {sorted(unknown)}"


def test_the_three_transcript_arms_are_mapped_and_distinct():
    """Three arms read three DIFFERENT transcript sets.

    The distinctness assertion is the one that matters. Two arms pointed at one directory
    is precisely the silent mis-attribution this file exists to prevent, and it would look
    like two arms that agree suspiciously well.
    """
    assert set(HYP_DIRS) == {"qwen-pipeline", "typhoon-pipeline", "gemini-asr-text"}
    directories = list(HYP_DIRS.values())
    assert len(set(directories)) == len(directories), f"duplicate hyp dirs: {directories}"


def test_typhoon_reads_the_typhoon_transcripts():
    """Named explicitly, so a rename cannot quietly repoint it at Qwen's output."""
    assert HYP_DIRS["typhoon-pipeline"] == "typhoon-whisper-large-v3"
    assert HYP_DIRS["qwen-pipeline"] == "qwen3-asr-1.7b"


def test_the_mapping_is_defined_exactly_once_in_the_source():
    """Guards the fix itself, not just its current output.

    The literal directory names should appear once each -- inside `HYP_DIRS`. A second
    occurrence means someone has reintroduced a local copy of the mapping, which is how the
    two copies got out of step in the first place.
    """
    source = SOURCE
    for directory in HYP_DIRS.values():
        occurrences = source.count(f'"{directory}"')
        assert occurrences == 1, (
            f'"{directory}" appears {occurrences} times in the source; it should appear '
            "once, inside HYP_DIRS. A second copy of the arm->directory mapping is how an "
            "arm ends up scored against another arm's transcripts.")


def test_the_paired_comparisons_cover_the_new_arm():
    """Adding an arm that no comparison names produces a run nobody can read."""
    pairs = {(a, b) for a, b in [
        ("ceiling", "format-control"), ("ceiling", "qwen-pipeline"),
        ("ceiling", "typhoon-pipeline"), ("ceiling", "gemini-asr-text"),
        ("ceiling-gemini", "gemini-audio"), ("qwen-pipeline", "gemini-audio"),
        ("typhoon-pipeline", "gemini-audio"), ("qwen-pipeline", "typhoon-pipeline")]}
    source = SOURCE
    # The two head-to-heads the migration question actually needs.
    assert '("typhoon-pipeline", "gemini-audio")' in source
    assert '("qwen-pipeline", "typhoon-pipeline")' in source
    assert ("typhoon-pipeline", "gemini-audio") in pairs


def test_an_unknown_arm_name_is_refused_not_silently_dropped():
    """`run_arms = [a for a in ARMS if a in args.arms]` drops typos in silence.

    Ask for `typhoon-pipline` and the old code ran zero arms, exited 0, and wrote a results
    file that looked healthy until someone counted rows. The refusal is asserted on the
    source because the filter sits inside `main`, behind credential loading and network
    setup that a unit test should not have to stand up.
    """
    source = SOURCE
    assert "unknown = sorted(set(args.arms) - set(ARMS))" in source
    assert "unknown arm(s)" in source


def test_transcribe_refuses_a_missing_api_key():
    """Without this the runner sends no Authorization header at all.

    The gateway's nginx then answers a bare "401 Authorization Required" and the retry loop
    reports "transcription failed after 3 attempts" for every item -- which reads like a
    broken model rather than an unset variable. It cost a real diagnostic detour on
    2026-08-20 before the message was made to name its own cause.
    """
    source = (REPO / "asr-eval" / "scripts" / "transcribe.py").read_text(encoding="utf-8")
    assert "if not api_key:" in source
    assert "does not read .env" in source
    # And the retry loop must name the underlying failure, not just count attempts.
    assert "transcription failed after {retries} attempts: " in source


@pytest.mark.parametrize("arm", ["qwen-pipeline", "typhoon-pipeline", "gemini-asr-text"])
def test_arms_are_declared_before_they_are_mapped(arm):
    assert arm in ARMS
