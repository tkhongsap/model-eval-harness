"""The outcome-leak gate, exercised in both directions.

A refusal nobody has watched fire is a comment. This builds a corpus with the leak and
proves the gate catches it, then builds one without and proves the gate passes -- so a
future change that quietly reintroduces disjoint outcome pools fails a test rather than
producing a confident, meaningless business-accuracy number.

The leak these lock down is not hypothetical: on 2026-08-18 the twenty-call set had
`CUSTOMER_ACCEPT` (5 lines) and `CUSTOMER_DECLINE` (3), disjoint and always firing, and a
substring match over those eight sentences recovered the outcome on 20 of 20 calls.
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))


def _build(root: Path, *, shared: bool):
    """Generate a corpus under `root`, with or without the shared closing pool."""
    os.environ["ASR_EVAL_ROOT"] = str(root)
    for name in ("asr_common", "thai_corpus", "business_labels",
                 "compose_dialogues", "leak_probe"):
        sys.modules.pop(name, None)
    thai_corpus = importlib.import_module("thai_corpus")
    if not shared:
        thai_corpus.CUSTOMER_CLOSE_SHARED = []
    importlib.import_module("compose_dialogues").main()
    return importlib.import_module("leak_probe")


@pytest.fixture(autouse=True)
def _restore_env():
    before = os.environ.get("ASR_EVAL_ROOT")
    yield
    if before is None:
        os.environ.pop("ASR_EVAL_ROOT", None)
    else:
        os.environ["ASR_EVAL_ROOT"] = before
    for name in ("asr_common", "thai_corpus", "business_labels",
                 "compose_dialogues", "leak_probe"):
        sys.modules.pop(name, None)


def test_gate_refuses_a_corpus_whose_closing_lines_determine_the_outcome(tmp_path, capsys):
    """Disjoint per-outcome pools -> lift 1.0 -> exit 1. This is the pre-fix state."""
    probe = _build(tmp_path / "leaky", shared=False)
    assert probe.main() == 1
    out = capsys.readouterr().out
    assert "LEAK" in out
    assert "REFUSED" in out


def test_gate_passes_once_the_shared_pool_dilutes_the_signal(tmp_path, capsys):
    probe = _build(tmp_path / "fixed", shared=True)
    assert probe.main() == 0
    assert "PASS" in capsys.readouterr().out


def test_singleton_signatures_are_excluded_from_the_headline(tmp_path):
    """A group of size one is predicted perfectly and proves nothing.

    Without this exclusion a channel with one distinct signature per call scores 100% while
    carrying no generalisable signal, which would fail the gate for the wrong reason.
    """
    probe = _build(tmp_path / "fixed", shared=True)
    sigs = [frozenset({i}) for i in range(5)]          # every signature unique
    accuracy, scored, singletons, groups = probe.best_accuracy(sigs, list("abcde"))
    assert (scored, singletons, groups) == (0, 5, 5)
    assert accuracy != accuracy                        # NaN: nothing was scoreable


def test_business_labels_match_the_scorer_s_schema(tmp_path):
    """The CSV must be readable by evalharness, not merely by us.

    Column names and order are `retention_v3.gt.csv`'s. If they drift, the audio pack stops
    being scoreable through the same code path as the text packs and starts needing a second
    implementation -- which is how two sets of numbers that look comparable stop being so.
    """
    import csv as _csv

    _build(tmp_path / "fixed", shared=True)
    repo = Path(__file__).resolve().parents[2]
    reference = (repo / "tests" / "fixtures" / "testsets" / "retention_v3.gt.csv")
    expected = next(iter(_csv.reader(reference.open(encoding="utf-8"))))

    produced = tmp_path / "fixed" / "ground-truth" / "business.csv"
    with produced.open(encoding="utf-8", newline="") as handle:
        rows = list(_csv.DictReader(handle))
        handle.seek(0)
        header = next(iter(_csv.reader(handle)))

    assert header == expected
    assert len(rows) == 20

    from evalharness.labelspaces import RETENTION
    for row in rows:
        assert row["call_result"] in RETENTION.call_result
        assert row["product"] in RETENTION.product
        for key in ("main", "secondary", "third"):
            assert row[key] == "" or row[key] in RETENTION.reason


def test_every_outcome_pool_can_express_its_label(tmp_path):
    """Four call_result values, four non-empty pools.

    The old binary had no way to render `unknown` or `undefined`, which is 12% of real rows.
    A pool that silently empties would make its label unreachable and skew the mix without
    any error.
    """
    _build(tmp_path / "fixed", shared=True)
    import thai_corpus
    from evalharness.labelspaces import RETENTION

    assert set(thai_corpus.CUSTOMER_CLOSE) == set(RETENTION.call_result)
    for label, pool in thai_corpus.CUSTOMER_CLOSE.items():
        assert len(pool) >= 4, f"{label} pool is too small to vary"
    assert len(thai_corpus.CUSTOMER_CLOSE_SHARED) >= 10
