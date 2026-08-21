"""The audio-validation allowlist lets through exactly what it names, and nothing else.

An allowlist is only better than ignoring an exit code while it stays narrow. These tests are
what keep it narrow: a new failure must abort, a report for the wrong corpus must abort, and
an entry that has stopped being needed must be reported rather than inherited.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from check_audio_validation import KNOWN_EXCEPTIONS, main  # noqa: E402


def write_report(tmp_path: Path, failures, files=138) -> Path:
    checks = [{"item": "ASR-001", "group": "FORMAT", "check": "mono",
               "status": "PASS", "detail": "got 1 channels"}]
    for item, check in failures:
        checks.append({"item": item, "group": "LEVELS", "check": check,
                       "status": "FAIL", "detail": "envelope peaks at 1.07 Hz"})
    report = {
        "checks": checks,
        "files": [{"item_id": f"ASR-{i + 1:03d}"} for i in range(files)],
        "summary": {"pass": 1, "fail": len(failures)},
    }
    p = tmp_path / "validation.json"
    p.write_text(json.dumps(report), encoding="utf-8")
    return p


MOD = "envelope modulates at a speech rate"


def test_the_two_known_exceptions_pass(tmp_path):
    p = write_report(tmp_path, [("ASR-049", MOD), ("ASR-069", MOD)])
    assert main(["--report", str(p), "--expect-items", "138"]) == 0


def test_a_clean_report_passes(tmp_path):
    p = write_report(tmp_path, [])
    assert main(["--report", str(p), "--expect-items", "138"]) == 0


def test_a_new_failure_aborts(tmp_path, capsys):
    """The whole point. A real defect must not hide behind two accepted ones."""
    p = write_report(tmp_path, [("ASR-049", MOD), ("ASR-069", MOD),
                                ("ASR-100", "sample rate 8000")])
    assert main(["--report", str(p), "--expect-items", "138"]) == 1
    out = capsys.readouterr().out
    assert "ASR-100" in out and "not on the known list" in out


def test_the_same_check_failing_on_a_different_item_aborts(tmp_path):
    """The exception is per (item, check), not per check."""
    p = write_report(tmp_path, [("ASR-050", MOD)])
    assert main(["--report", str(p), "--expect-items", "138"]) == 1


def test_a_report_for_a_different_corpus_aborts(tmp_path, capsys):
    """The 20-vs-138 mistake: a report for the seed set proves nothing about the run set."""
    p = write_report(tmp_path, [], files=20)
    assert main(["--report", str(p), "--expect-items", "138"]) == 1
    assert "covers 20 files" in capsys.readouterr().out


def test_a_stale_exception_is_reported(tmp_path, capsys):
    p = write_report(tmp_path, [("ASR-049", MOD)])
    assert main(["--report", str(p), "--expect-items", "138"]) == 0
    assert "STALE EXCEPTION" in capsys.readouterr().out


@pytest.mark.parametrize("key", sorted(KNOWN_EXCEPTIONS))
def test_every_exception_carries_its_evidence(key):
    """A reason living only in a commit message is a reason nobody can re-audit."""
    reason = KNOWN_EXCEPTIONS[key]
    assert len(reason) > 120, f"{key}: reason is too thin to re-audit"
    assert "chars" in reason and "reference" in reason, (
        f"{key}: the reason must cite the transcription evidence that refutes the check's "
        "own premise, not merely assert the file is fine"
    )


def test_the_allowlist_stays_small():
    """Two is a documented quirk. Twenty is a check that should have been redesigned."""
    assert len(KNOWN_EXCEPTIONS) <= 4, (
        f"{len(KNOWN_EXCEPTIONS)} exceptions. Past a handful this stops being an allowlist "
        "and becomes a way of not fixing the check."
    )


def test_the_real_report_is_accounted_for():
    """The live corpus, not a fixture -- skipped when it has not been rendered."""
    report = REPO / "asr-eval-v3" / "reports" / "validation.json"
    if not report.is_file():
        pytest.skip("asr-eval-v3 is not rendered in this checkout")
    assert main(["--report", str(report), "--expect-items", "138"]) == 0
