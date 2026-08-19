"""The outcome-leak gate, exercised in both directions.

A refusal nobody has watched fire is a comment. This builds the corpus the eval actually
ships, proves the gate refuses it by default, and proves the acknowledgement flag passes
only while publishing the measured lift -- so the escape hatch can never quietly become a
widened threshold.

The leak is not hypothetical: on 2026-08-18 the twenty-call set had `CUSTOMER_ACCEPT`
(5 lines) and `CUSTOMER_DECLINE` (3), disjoint and always firing, and a substring match
over those eight sentences recovered the outcome on 20 of 20 calls.

What these tests assert was REVERSED on 2026-08-19. They used to prove the gate could be
satisfied by diluting the outcome away. It could -- and the corpus that resulted was
unmeasurable, because a label stated in 38% of calls leaves the labeller guessing on the
rest. The corpus now states every label and openly fails the gate; see
`leak_probe.STATED_LABEL_RATIONALE` for why that is the right trade for an AUDIO eval,
where neither arm is given the transcript.
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))


def _build(root: Path):
    """Generate a corpus under `root`.

    The old `shared=False` switch emptied `CUSTOMER_CLOSE_SHARED` to synthesise the leaky
    pre-fix corpus. It is gone: since the two-turn closer fix the shared line is an extra
    turn rather than a substitute for the outcome line, so emptying the pool no longer
    models a corpus anyone would ship -- it just raises IndexError inside `add()`.
    """
    os.environ["ASR_EVAL_ROOT"] = str(root)
    for name in ("asr_common", "thai_corpus", "business_labels",
                 "compose_dialogues", "leak_probe"):
        sys.modules.pop(name, None)
    importlib.import_module("thai_corpus")
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


def test_gate_still_refuses_by_default_now_that_every_label_is_stated(tmp_path, capsys):
    """The corpus states its labels, so the gate fires. That is the honest state.

    These two tests used to assert the opposite: that `CUSTOMER_CLOSE_SHARED` diluted the
    outcome until lift fell under the threshold, and the gate passed. That design was
    reversed deliberately in the E23 corpus fix, and the reversal is the whole reason the
    eval measures anything at all -- diluting to beat the gate left the label stated in
    only 38% of calls, the labeller had nothing to read on the other 62%, it defaulted to
    `save`, and the ceiling arm scored 0.277 with a PERFECT transcript.

    So the assertion flips rather than the threshold. `MAX_LIFT` is untouched; what changed
    is that this corpus is openly on the failing side of it.
    """
    probe = _build(tmp_path / "stated")
    assert probe.main() == 1
    out = capsys.readouterr().out
    assert "LEAK" in out
    assert "REFUSED" in out


def test_acknowledgement_passes_but_publishes_the_lift_rather_than_hiding_it(tmp_path, capsys):
    """The escape hatch must cost a disclosure, not a widened threshold.

    Widening `MAX_LIFT` to 1.0 would also have made this corpus pass, and would have left
    no trace that the labels are recoverable by string match. The flag instead prints the
    measured lift, the LEAK marker, and why every score from this pack is an upper bound.
    """
    probe = _build(tmp_path / "stated")
    assert probe.main(["--acknowledge-stated-labels"]) == 0
    out = capsys.readouterr().out
    assert "ACKNOWLEDGED" in out
    assert "LEAK" in out           # the number is still shown, not suppressed
    assert "UPPER BOUND" in out    # and the consequence is stated


def test_singleton_signatures_are_excluded_from_the_headline(tmp_path):
    """A group of size one is predicted perfectly and proves nothing.

    Without this exclusion a channel with one distinct signature per call scores 100% while
    carrying no generalisable signal, which would fail the gate for the wrong reason.
    """
    probe = _build(tmp_path / "fixed")
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

    _build(tmp_path / "fixed")
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
    _build(tmp_path / "fixed")
    import thai_corpus
    from evalharness.labelspaces import RETENTION

    assert set(thai_corpus.CUSTOMER_CLOSE) == set(RETENTION.call_result)
    for label, pool in thai_corpus.CUSTOMER_CLOSE.items():
        assert len(pool) >= 4, f"{label} pool is too small to vary"
    assert len(thai_corpus.CUSTOMER_CLOSE_SHARED) >= 10
