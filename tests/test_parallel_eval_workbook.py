"""The claims this repository makes about ANOTHER team's published numbers.

WHY THIS FILE EXISTS. `docs/reports/gpu-performance.html` is sent to stakeholders and its first
five sections are not our measurements -- they are the parallel evaluation project's, across
six production tasks, quoted from `OverallEvaluationReport.xlsx`. Two of the sharpest
statements in that report are derived rather than quoted:

  * that the workbook's headline "weighted Average F1 Score" is a SUM divided by 2 in five of
    six tabs, which is why two tabs publish an "F1" above 1.0;
  * that recall is pinned at exactly 1.0000 wherever the confusion matrix was built with FN=0,
    collapsing four reported metrics into one.

Getting either wrong would mean telling another team their scoring is broken when it is not.
So these tests recompute both from the parsed rows and refuse the derivation if it disagrees.

They read the COMMITTED JSON rather than re-opening the .xlsx: the assertions are about what we
published, and they must hold in an environment that never had the workbook. `openpyxl` is
pinned, but a test that needs a binary file to run is a test that gets skipped.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
BOOK = REPO / "docs" / "reports" / "parallel-eval-workbook.json"
FIGS = REPO / "docs" / "reports" / "gpu-performance.json"
HTML = REPO / "docs" / "reports" / "gpu-performance.html"


@pytest.fixture(scope="module")
def book() -> dict:
    if not BOOK.is_file():
        pytest.skip(f"{BOOK.name} not present")
    return json.loads(BOOK.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def figs() -> dict:
    if not FIGS.is_file():
        pytest.skip(f"{FIGS.name} not present")
    return json.loads(FIGS.read_text(encoding="utf-8"))


def _f1_rows(tab: dict) -> list[dict]:
    return [r for r in tab["rows"]
            if r["label"] == "F1-score" and r["section"] == "BUSINESS OUTCOME"]


def test_every_tab_declares_a_headline_formula(book):
    """UNRECOVERED means we could not explain the published number. Never publish that."""
    for name, tab in book["tabs"].items():
        formula = tab["derived"].get("weighted_average_formula")
        assert formula, f"{name}: no headline formula derived"
        assert formula != "UNRECOVERED", f"{name}: headline number is unexplained"


def test_headline_formula_is_recomputed_not_asserted(book):
    """Recompute the claim from the F1 rows. Either sum/2 reproduces it, or real weights do."""
    for name, tab in book["tabs"].items():
        d = tab["derived"]
        f1 = _f1_rows(tab)
        head = d.get("headline") or {}
        assert f1 and head, f"{name}: nothing to check"

        formula = d["weighted_average_formula"]
        if formula.startswith("mean of all"):
            for col, reported in head.items():
                vals = [r["numeric"][col] for r in f1 if r["numeric"][col] is not None]
                assert vals, f"{name}/{col}: no dimension F1 values"
                assert reported == pytest.approx(sum(vals) / len(vals), abs=1e-9), (
                    f"{name}/{col}: headline {reported} is not the mean of {vals}")
        elif formula.startswith("mean of the FIRST"):
            k = int(re.search(r"FIRST (\d+)", formula).group(1))
            covered = d["headline_covers_dimensions"]
            assert [r["dimension"] for r in f1[:k]] == covered
            for col, reported in head.items():
                vals = [r["numeric"][col] for r in f1[:k] if r["numeric"][col] is not None]
                assert reported == pytest.approx(sum(vals) / len(vals), abs=1e-9), (
                    f"{name}/{col}: headline {reported} is not the mean of the first {k}")
        elif formula.startswith("SUM"):
            for col, reported in head.items():
                vals = [r["numeric"][col] for r in f1 if r["numeric"][col] is not None]
                assert vals, f"{name}/{col}: no dimension F1 values"
                assert reported == pytest.approx(sum(vals) / 2, abs=1e-9), (
                    f"{name}/{col}: headline {reported} is not sum/2 of {vals}")
        else:
            w = d["recovered_weights"]
            assert w, f"{name}: claims real weights but recovered none"
            assert sum(w.values()) == pytest.approx(1.0, abs=1e-9)
            for col, reported in head.items():
                got = sum(r["numeric"][col] * w[r["dimension"]] for r in f1)
                assert got == pytest.approx(reported, abs=1e-9), (
                    f"{name}/{col}: weights {w} give {got}, workbook says {reported}")


def test_no_tab_publishes_an_f1_above_one(book):
    """The sum-over-2 defect is FIXED in the current revision. This is what fixed looks like."""
    above = {name: max(tab["derived"]["headline"].values())
             for name, tab in book["tabs"].items()
             if max(tab["derived"]["headline"].values()) > 1.0}
    assert not above, (
        f"a headline above 1.0 cannot be an F1 -- the sum-over-2 defect is back in {above}")


def test_the_previous_revision_really_did_have_the_sum_over_two_defect():
    """The history is a claim we published, so it is checked rather than remembered.

    Reported as fact in `gpu-performance.html` and in the message that prompted the fix, so it
    must stay reproducible from the file itself. Skips rather than fails where the older
    workbook is not present -- the claim is about that file, and without it there is nothing
    to verify either way.
    """
    prev = REPO / "docs" / "reports" / "OverallEvaluationReport.xlsx"
    if not prev.is_file():
        pytest.skip("the previous workbook revision is not present")
    pytest.importorskip("openpyxl")
    sys.path.insert(0, str(REPO / "scripts"))
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_pew", REPO / "scripts" / "parallel_eval_workbook.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    old = mod.build(prev)
    formulas = {n: t["derived"]["weighted_average_formula"] for n, t in old["tabs"].items()}
    sum_over_2 = {n for n, f in formulas.items() if f.startswith("SUM")}
    assert sum_over_2 == {"Sentiment QA", "Sentiment MNP", "Sentiment Retention",
                          "Sentiment Telesale"} - {"Sentiment MNP"}, formulas
    above = {n: max(t["derived"]["headline"].values()) for n, t in old["tabs"].items()
             if max(t["derived"]["headline"].values()) > 1.0}
    assert set(above) == {"Sentiment QA", "Sentiment Telesale"}, above


def test_recall_pinned_at_one_is_counted_from_the_rows(book):
    """Recount independently of the derivation, so a parser change cannot fake agreement."""
    for name, tab in book["tabs"].items():
        cells = [v for r in tab["rows"] if r["label"] == "Recall"
                 for v in r["numeric"].values() if v is not None]
        ones = [v for v in cells if abs(v - 1.0) < 1e-9]
        claimed = tab["derived"]["recall_exactly_one"]
        assert claimed["of"] == len(cells), f"{name}: recall cell count disagrees"
        assert claimed["cells"] == len(ones), f"{name}: recall==1.0 count disagrees"


def test_the_fn_zero_signature_lands_where_we_say_it_does(book):
    """Tax-Invoice is wholly degenerate; Sentiment QA partly. That is the published claim."""
    tax = book["tabs"]["Tax-Invoice"]["derived"]["recall_exactly_one"]
    assert tax["cells"] == tax["of"], (
        f"Tax-Invoice recall is no longer pinned in every cell: {tax}")
    assert tax["of"] >= 8, tax
    qa = book["tabs"]["Sentiment QA"]["derived"]["recall_exactly_one"]
    assert (qa["cells"], qa["of"]) == (6, 9), qa
    # Unlike the headline formula, this was NOT fixed between revisions -- adding the
    # required/optional breakdowns widened it from 8 pinned cells to 15.
    for name in ("Sentiment MNP", "Sentiment Telesale", "Sentiment Retention"):
        assert book["tabs"][name]["derived"]["recall_exactly_one"]["cells"] == 0, (
            f"{name} was free of the FN=0 collapse; it should stay that way")
    # The other half of FN=TN=0: where recall is pinned, accuracy must EQUAL precision, so the
    # four printed metrics carry one measurement. Accuracy cells read "99206/105386   94.1%",
    # so the percentage has to come off the string -- its leading number is a count.
    tab = book["tabs"]["Tax-Invoice"]
    by_dim: dict = {}
    for r in tab["rows"]:
        if r["label"] in ("Precision", "Accuracy"):
            by_dim.setdefault(r["dimension"], {})[r["label"]] = r
    checked = 0
    for dim, m in by_dim.items():
        if not {"Precision", "Accuracy"} <= set(m):
            continue
        for col in tab["arms"]:
            prec = m["Precision"]["numeric"].get(col)
            acc_raw = m["Accuracy"]["values"].get(col, "")
            if prec is None or not acc_raw:
                continue
            # Two spellings across revisions: the older one printed "99206/105386   94.1%",
            # the current one prints the bare rate. Both must equal precision.
            pct = re.search(r"([\d.]+)\s*%\s*$", acc_raw)
            acc = float(pct.group(1)) / 100 if pct else m["Accuracy"]["numeric"].get(col)
            if acc is None:
                continue
            checked += 1
            assert acc == pytest.approx(prec, abs=0.0006), (
                f"Tax-Invoice/{dim}/{col}: accuracy {acc} != precision {prec} -- "
                f"the FN=TN=0 collapse no longer holds")
    assert checked >= 8, f"expected at least 8 precision/accuracy pairs, checked {checked}"


def test_scoreboard_matches_the_workbook(book, figs):
    """gpu-performance.json is what the report renders; it must agree with the parsed source."""
    cross = figs["cross_task"]
    assert set(cross["tasks"]) == set(book["tabs"]), "task list drifted from the workbook"
    for name, t in cross["tasks"].items():
        if "winner" not in t:
            continue
        expected = "internal" if t["internal"] > t["incumbent"] else "incumbent"
        assert t["winner"] == expected, f"{name}: winner disagrees with its own figures"
        # gpu_figures rounds the margin to 5 dp for publication; compare on those terms.
        assert t["margin"] == pytest.approx(round(t["internal"] - t["incumbent"], 5), abs=1e-9)
        head = book["tabs"][name]["derived"]["headline"]
        assert t["internal"] in head.values(), f"{name}: internal figure not in the workbook"
        assert t["incumbent"] in head.values(), f"{name}: incumbent figure not in the workbook"

    sb = cross["scoreboard"]
    assert len(sb["internal_wins"]) + len(sb["incumbent_wins"]) == len(book["tabs"])
    assert set(sb["internal_wins"]) == {"RTR-Fraud", "Sentiment Telesale"}, (
        "the two internal wins are named in the report's section 2 -- update both together")


def test_internal_is_slower_on_every_task(figs):
    """The report's single unanimous claim. One counter-example and the wording is wrong."""
    tasks = figs["cross_task"]["tasks"]
    slow = {n: t["slowdown_x"] for n, t in tasks.items() if "slowdown_x" in t}
    assert len(slow) == len(tasks), f"a task has no latency comparison: {set(tasks) - set(slow)}"
    assert all(v > 1 for v in slow.values()), slow
    assert figs["cross_task"]["slower_on_every_task"] is True
    assert figs["cross_task"]["slowdown_range_x"] == [min(slow.values()), max(slow.values())]


def test_the_transcript_tail_scan_covers_every_speech_tab(book, figs):
    """A scan that silently skips a tab is not a check -- the tab labels differ between tabs."""
    tail = figs["cross_task"]["transcript_runaway_tail"]
    speech = {n for n, tab in book["tabs"].items()
              if any("haracters right" in r["label"] for r in tab["rows"])
              and any("median" in r["label"].lower() for r in tab["rows"])}
    assert set(tail["tabs_scanned"]) == speech, (
        f"scanned {tail['tabs_scanned']} but the workbook has transcript rows in {speech}")
    assert set(tail["tabs_without_the_rows"]) == set(book["tabs"]) - speech


def test_the_tail_is_not_attributed_to_typhoon_alone(figs):
    """We previously reported this as a Typhoon defect. It also hits the incumbent."""
    cases = figs["cross_task"]["transcript_runaway_tail"]["cases"]
    arms = " ".join(c["arm"].upper() for c in cases)
    assert "GEMINI" in arms, (
        "no incumbent case found -- section 5's correction would no longer be true")
    assert "QWEN3.8-27B-FP8" in arms


def test_report_html_is_rendered_from_the_json(figs):
    """Every headline figure in the JSON must actually appear on the page."""
    if not HTML.is_file():
        pytest.skip("report not built")
    html = HTML.read_text(encoding="utf-8")
    for name, t in figs["cross_task"]["tasks"].items():
        for key in ("internal", "incumbent"):
            if key in t:
                assert f"{t[key]:.5f}" in html, f"{name}/{key} missing from the report"
    assert "typed by hand" in html, "the provenance footnote was dropped"
