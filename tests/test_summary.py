"""The first page is where a six-task result can be made to say anything.

WHY THIS FILE EXISTS. This page argues for continued investment, and the person it is prepared
for asked for the internal side to look close to or better than the incumbent. That is exactly
the pressure a test suite exists to resist.

These tests do not check that the page is flattering. They check that the favourable framings it
does use are the honest ones, and that every one of them carries its counterweight:

  * the headline is a RATIO, which compresses near the ceiling -- so the error-basis figure must
    be published beside it;
  * the internal column is the BEST arm per task, which is a selection -- so the exposure must
    be published (how much it flatters, and on which tasks no selection happened at all);
  * two tasks are ahead, but inside their own noise -- so they must read as parity, not wins;
  * the worst task must stay in the headline, because excluding it improves the number by 1.1
    points for a reason that does not survive the question "did it change who leads?".

If a future edit quietly drops a task, swaps in a kinder aggregation, or removes a
counterweight, one of these fails.
"""

from __future__ import annotations

import json
import re
import statistics
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SUMMARY = REPO / "docs" / "reports" / "summary.json"
MATRIX = REPO / "docs" / "reports" / "usecase-matrix.json"
PAGE = REPO / "docs" / "reports" / "usecase-matrix.html"


@pytest.fixture(scope="module")
def s() -> dict:
    if not SUMMARY.is_file():
        pytest.skip("summary.json not present")
    return json.loads(SUMMARY.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def mx() -> dict:
    if not MATRIX.is_file():
        pytest.skip("usecase-matrix.json not present")
    return json.loads(MATRIX.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def page() -> str:
    if not PAGE.is_file():
        pytest.skip("page not built")
    return PAGE.read_text(encoding="utf-8")


def _by_tab(mx):
    t = {}
    for c in mx["columns"]:
        t.setdefault(c["use_case"], []).append(c)
    return t


def test_no_use_case_is_dropped_from_the_headline(s, mx):
    """The single most important guard on this page.

    Excluding Sentiment Retention moves the figure by +1.1 points, and it is the task most
    likely to be quietly removed. The headline mean must be over ALL tabs in the matrix.
    """
    tabs = _by_tab(mx)
    assert len(s["per_use_case"]) == len(tabs), (
        f"the summary covers {len(s['per_use_case'])} tasks but the matrix has {len(tabs)}")
    assert {p["use_case"] for p in s["per_use_case"]} == set(tabs)
    assert "Sentiment Retention" in {p["use_case"] for p in s["per_use_case"]}
    inc = statistics.fmean(p["incumbent_f1"] for p in s["per_use_case"])
    internal = statistics.fmean(p["internal_f1"] for p in s["per_use_case"])
    assert s["quality"]["incumbent"] == pytest.approx(inc, abs=5e-5)
    assert s["quality"]["internal"] == pytest.approx(internal, abs=5e-5), (
        "the headline is not the mean of all six tasks")


def test_the_worst_task_is_on_the_page_with_its_number(s, page):
    worst = min(s["per_use_case"], key=lambda p: p["pct_of_incumbent"])
    assert worst["use_case"] == "Sentiment Retention", worst["use_case"]
    assert f"{worst['pct_of_incumbent']:.1f}%" in page, (
        "the worst task's figure is not printed on the page")
    assert "stays in the headline" in page


def test_the_exclusion_sensitivity_is_labelled_and_not_the_headline(s, page):
    sens = s["sensitivity_excluding_retention"]
    assert sens["pct_of_incumbent"] > s["quality"]["pct_ratio_of_means"], (
        "excluding the task no longer improves the number; re-read the caveat")
    assert "moved both arms" in sens["why_it_is_not_the_headline"], (
        "the reason the exclusion fails must be recorded, not just the number")
    # The headline tile must carry the all-six figure, not the flattering one.
    assert f"{s['quality']['pct_ratio_of_means']}%" in page
    assert re.search(r'class="big">\s*' + re.escape(f"{s['quality']['pct_ratio_of_means']}%"),
                     page), "the headline tile does not show the all-six percentage"


def test_the_internal_column_really_is_the_best_arm(s, mx):
    """The stated rule, verified. A wrong arm puts a false number on a management slide."""
    tabs = _by_tab(mx)
    for p in s["per_use_case"]:
        internal = [c for c in tabs[p["use_case"]] if c["side"] == "internal"]
        best = max(internal, key=lambda c: c["headline_f1"])
        assert p["internal_arm"] == best["arm"], (
            f"{p['use_case']}: claims {p['internal_arm']!r}, best is {best['arm']!r}")
        assert p["internal_f1"] == pytest.approx(best["headline_f1"], abs=1e-9)
        inc = next(c for c in tabs[p["use_case"]] if c["side"] == "incumbent")
        assert p["incumbent_f1"] == pytest.approx(inc["headline_f1"], abs=1e-9)
        assert p["internal_arms_available"] == len(internal)


def test_the_selection_exposure_is_published(s, page):
    """Best-of-k is a selection. It is defensible here, but only because the exposure is shown."""
    sel = s["selection_exposure"]
    assert sel["mean_of_all_internal_arms"] < s["quality"]["internal"], (
        "best-per-task should sit above the all-arms mean; if not, recheck the selection")
    assert sel["best_per_task_flatters_by"] > 0
    assert sel["tasks_with_only_one_internal_arm"], (
        "the tasks where no selection happened are the strongest part of this defence")
    assert f"{sel['mean_of_all_internal_arms']:.4f}" in page, (
        "the all-arms mean must be on the page or the selection looks concealed")
    assert "picked the best model per task" in page


def test_the_ratio_is_published_with_its_error_basis_counterweight(s, page):
    """A ratio of F1 flatters near the ceiling. The error multiple must travel with it."""
    e = s["error_basis"]
    assert e["incumbent_mean_error"] == pytest.approx(1 - s["quality"]["incumbent"], abs=5e-5)
    assert e["internal_mean_error"] == pytest.approx(1 - s["quality"]["internal"], abs=5e-5)
    assert e["internal_errors_relative"] == pytest.approx(
        e["internal_mean_error"] / e["incumbent_mean_error"], abs=5e-3)
    assert e["internal_errors_relative"] > 1
    assert f"{e['internal_errors_relative']}" in page, (
        "the error multiple is not on the page; the headline ratio is then unopposed")


def test_both_constructions_of_the_percentage_are_shown(s, page):
    """Ratio-of-means and mean-of-ratios differ by about a tenth of a point. Show both, or the
    one a reader finds unaided looks concealed."""
    q = s["quality"]
    assert abs(q["pct_ratio_of_means"] - q["pct_mean_of_ratios"]) < 1.0
    assert f"{q['pct_ratio_of_means']}%" in page
    assert f"{q['pct_mean_of_ratios']}%" in page


def test_the_two_leads_are_presented_as_parity(s, page):
    par = s["parity_not_wins"]
    leaders = {p["use_case"] for p in s["per_use_case"] if p["leader"] == "internal"}
    assert set(par["tasks"]) == leaders, "the parity list disagrees with the per-task leaders"
    assert "parity" in page
    for t in par["tasks"]:
        assert t in page, f"{t} is claimed as parity but is not named on the page"


def test_the_asr_split_is_computed_not_asserted(s, mx):
    g = s["where_the_gap_is"]
    per = {p["use_case"]: p for p in s["per_use_case"]}
    for key, want_asr in (("with_asr", True), ("without_asr", False)):
        names = [n for n, p in per.items() if p["has_asr_stage"] is want_asr]
        assert set(g[key]["use_cases"]) == set(names), f"{key}: wrong task grouping"
        inc = statistics.fmean(per[n]["incumbent_f1"] for n in names)
        internal = statistics.fmean(per[n]["internal_f1"] for n in names)
        assert g[key]["incumbent"] == pytest.approx(inc, abs=5e-5)
        assert g[key]["internal"] == pytest.approx(internal, abs=5e-5)
        assert g[key]["pct_of_incumbent"] == pytest.approx(internal / inc * 100, abs=0.05)
    assert g["without_asr"]["pct_of_incumbent"] > g["with_asr"]["pct_of_incumbent"], (
        "the no-speech-stage group no longer leads; the page's central reading is stale")


def test_the_asr_claim_carries_its_own_caveat(s, page):
    """The strongest claim on the page is also the one with a ceiling effect and n=2."""
    assert "ceiling" in s["where_the_gap_is"]["caveat"]
    assert "two data points" in s["where_the_gap_is"]["caveat"]
    assert "ceiling" in page, "the ceiling-effect caveat is not on the page"


def test_cost_never_reads_as_free_or_as_a_saving(s, page):
    c = s["cost"]
    assert "not $0" in c["internal_note"]
    assert "Not metered" in page or "not metered" in page
    zeros = re.findall(r"\$0\.00(?![0-9])", page)
    assert not zeros, "a self-hosted column must never print a zero cost"
    assert re.findall(r"\$0\.00(?![0-9])", "it cost $0.00"), "the zero-cost guard is inert"
    assert "cannot say what migrating would save" in c["break_even_is_not_computable"]
    assert any("saves money" in x for x in s["must_not_claim"])


def test_speed_is_on_the_page_because_it_is_the_hostile_question(s, page):
    sp = s["speed"]
    assert sp["range"][1] > sp["range"][0] > 1
    assert f"{sp['range'][0]}" in page, "the fastest ratio is not on the first page"


def test_the_slowdown_headline_uses_the_widest_KNOWN_ratio(s, page):
    """The task with no published latency is the SLOWEST one, so omitting it flatters us.

    Sentiment QA's selected arm publishes no timing at all ('cannot meansure'). Quoting only
    the five measured tasks caps the range at 41.7x, when the same task's other configuration
    is on record at 86.5x. The headline must use the widest ratio actually known, or it
    understates the one number a sceptical reader came for.
    """
    sp = s["speed"]
    unmeasured = sp["tasks_unmeasured"]
    if not unmeasured:
        pytest.skip("every task publishes a latency")
    known = [u["other_arm_ratio"] for u in unmeasured if u["other_arm_ratio"]]
    assert known, "an unmeasured task exists with no fallback figure; state that on the page"
    assert sp["widest_known_ratio"] == max(known + [sp["range"][1]])
    assert sp["widest_known_ratio"] > sp["range"][1], (
        "the unmeasured task is no longer the slowest; recheck whether this guard still bites")
    assert f"{sp['widest_known_ratio']}" in page, (
        f"the page must quote {sp['widest_known_ratio']}x, not stop at the measured "
        f"maximum of {sp['range'][1]}x")


def test_an_unpublished_timing_says_why_rather_than_showing_a_dash(s, mx, page):
    """A dash reads as 'we forgot to measure'. The workbook says why, and its words go in."""
    raw = {c["latency_raw"] for c in mx["columns"] if c["latency_s"] is None}
    raw |= {c["round_raw"] for c in mx["columns"] if c["round_s"] is None}
    raw = {r for r in raw if r}
    assert raw, "no timing is missing; this guard is unnecessary"
    for r in raw:
        assert r in page, f"the workbook's reason {r!r} is not shown in the cell"
    assert "cannot meansure" in sp_note(s), (
        "the unmeasured task must be named in the speed note as well as in its cell")


def sp_note(s: dict) -> str:
    return s["speed"]["note"]


def test_the_page_carries_the_must_not_claim_list(s, page):
    assert s["must_not_claim"]
    assert any("within noise" in x for x in s["must_not_claim"])
    for claim in s["must_not_claim"]:
        head = claim.split(".")[0][:55]
        assert head in page, f"the page does not carry: {head!r}"


def test_the_summary_appears_before_the_detail(page):
    i_sum = page.find("Internal models against production")
    i_detail = page.find("use case by use case")
    assert 0 < i_sum < i_detail, "the summary must come before the per-use-case detail"


def test_page_is_pure_ascii_and_balanced(page):
    bad = {c for c in page if ord(c) > 126}
    assert not bad, f"non-ascii would render as mojibake without a charset: {bad}"
    for tag in ("div", "table", "thead", "tbody", "tr", "td", "th", "p", "ul", "li", "span"):
        o = len(re.findall(rf"<{tag}[ >]", page))
        c = len(re.findall(rf"</{tag}>", page))
        assert o == c, f"<{tag}> unbalanced: {o} open, {c} close"
    for leak in ("&amp;mdash;", "&amp;middot;", "&amp;times;", "&amp;ldquo;", "&amp;minus;"):
        assert leak not in page, f"{leak} is printed as text"


# ---------------------------------------------------------------------------------------------
# Accuracy, tokens and cost, added to the first-page table.
#
# Accuracy has two real traps, both confirmed by direct read of the workbook before this was
# built: RTR-Fraud's Accuracy cells are "195/198  98.5%" -- the leading number is a raw COUNT,
# and a naive parse would average counts alongside 0-1 fractions from every other task. And
# Sentiment QA's third dimension is labelled "Accuracy  -  whole list matched", not "Accuracy" --
# an exact-label filter silently drops it and under-counts that task's aggregate to 2 of 3
# dimensions. These tests reproduce both traps directly, so a regression in either fails loudly
# rather than silently drifting the number on a management page.
# ---------------------------------------------------------------------------------------------

import importlib.util


def _load_summary_figures():
    spec = importlib.util.spec_from_file_location(
        "_sf", REPO / "scripts" / "summary_figures.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def sf():
    return _load_summary_figures()


@pytest.fixture(scope="module")
def book() -> dict:
    p = REPO / "docs" / "reports" / "parallel-eval-workbook.json"
    if not p.is_file():
        pytest.skip("parallel-eval-workbook.json not present")
    return json.loads(p.read_text(encoding="utf-8"))


def test_rtr_fraud_accuracy_parses_the_percentage_not_the_count(sf, book):
    """The regression guard for the count-vs-fraction trap.

    RTR-Fraud's Accuracy cells read '195/198  98.5%'. The workbook parser's generic `numeric`
    field holds 195 (the leading number). If `rebuild_row` (or anything downstream) ever reused
    that field instead of parsing the trailing percentage, this task's Accuracy would come back
    shaped like a raw count (order of 100+) instead of a fraction near 1 -- and averaging it
    alongside five 0-1 fractions would silently corrupt the blended accuracy figure.
    """
    tab = book["tabs"]["RTR-Fraud"]
    gemini_col = next(c for c in tab["arms"] if c.strip().upper() == "GEMINI")
    v = sf.rebuild_row(tab, gemini_col, "Accuracy")
    assert v is not None
    assert 0.9 < v < 1.0, (
        f"RTR-Fraud accuracy parsed as {v!r} -- looks like a raw count, not a fraction; the "
        f"percentage-parsing path in rebuild_row/_frac has regressed")


def test_sentiment_qa_accuracy_covers_all_three_dimensions(sf, book):
    """The regression guard for the exact-label-match trap.

    Sentiment QA's third dimension carries its accuracy-equivalent row as
    'Accuracy  -  whole list matched', not 'Accuracy'. A label filter using `==` instead of
    `startswith` silently drops it, and the aggregate becomes a mean of 2 dimensions instead of
    3 -- diverging from how the F1 headline (which does cover all 3) is built.
    """
    tab = book["tabs"]["Sentiment QA"]
    col = next(c for c in tab["arms"] if c.strip().upper() == "GEMINI-2.5-FLASH")
    got = sf.rebuild_row(tab, col, "Accuracy")

    # Hand-computed from the three dimensions' own Accuracy-equivalent rows, independently of
    # rebuild_row's internals: (0.8566 + 0.7852 + 0.4278) / 3.
    by_dim = {}
    for r in tab["rows"]:
        if r["section"] == "BUSINESS OUTCOME" and r["label"].startswith("Accuracy"):
            by_dim[r["dimension"]] = r
    assert len(by_dim) == 3, "expected 3 accuracy-bearing dimensions on Sentiment QA"
    hand = statistics.fmean(
        float(by_dim[dim]["values"][col]) for dim in tab["derived"]["dimensions"])
    assert got == pytest.approx(hand, abs=1e-6)
    assert got == pytest.approx(0.6899, abs=5e-4), (
        "if this task's mean drifted to ~0.821 (the mean of just the first two dimensions), "
        "the third dimension's row was silently dropped again")


def test_accuracy_weighting_mirrors_the_f1_headline_per_task(sf, book, mx):
    """Same dimensions, same weights as F1 -- verified per task, not assumed."""
    tabs = {c["use_case"]: c for c in mx["columns"]}  # any column, just to get use_case set
    for name, tab in book["tabs"].items():
        d = tab["derived"]
        if "recovered_weights" not in d and "headline_covers_dimensions" not in d:
            continue  # a plain unweighted mean over all dimensions -- nothing special to check
        arms = [c for c in tab["arms"] if c.strip().upper() != "GROUND TRUTH"]
        col = arms[0]
        acc = sf.rebuild_row(tab, col, "Accuracy")
        assert acc is not None, f"{name}: accuracy did not aggregate for {col!r}"
        # Recompute by hand using the SAME weight/slice rule, reading the Accuracy rows
        # directly rather than calling rebuild_row a second time -- an independent check.
        by_dim = {}
        for r in tab["rows"]:
            if r["section"] == "BUSINESS OUTCOME" and r["label"].startswith("Accuracy"):
                by_dim[r["dimension"]] = r
        vals = [sf._frac(by_dim[dim]["values"].get(col)) for dim in d["dimensions"]]
        w = d.get("recovered_weights")
        if w:
            hand = sum(v * w[dim] for v, dim in zip(vals, d["dimensions"]))
        else:
            covered = d["headline_covers_dimensions"]
            hand = statistics.fmean(vals[: len(covered)])
        assert acc == pytest.approx(hand, abs=1e-6), f"{name}: weighting diverged from the rule"


def test_accuracy_disclosure_matches_the_data_it_describes(s, book, sf):
    """The degeneracy classification is derived, not hand-typed -- recomputed independently."""
    classes = sf.classify_accuracy(book)
    assert set(classes) == set(book["tabs"]), "every tab must be classified"
    assert classes == s["accuracy_disclosure"]["per_task"], (
        "the committed summary.json's disclosure has drifted from a fresh recomputation")

    # The six concrete findings established by direct read of the workbook before this was
    # built. If any of these flips, the page's bullet list for that task is now describing the
    # wrong thing.
    expect_independent = {"Sentiment MNP", "Sentiment Retention"}
    for t in expect_independent:
        assert classes[t]["independent"], f"{t} was independent and should still be"
    for t, want in (("RTR-Fraud", "Precision and Recall"),
                    ("Tax-Invoice", "Precision"),
                    ("Sentiment Telesale", "Recall")):
        assert want in classes[t]["summary"], f"{t}: expected {want!r} in {classes[t]['summary']!r}"
    assert "1 of 3" in classes["Sentiment QA"]["summary"]


def test_accuracy_disclosure_note_and_every_task_bullet_are_on_the_page(s, page):
    d = s["accuracy_disclosure"]
    # No ordinal ("fifth"/"sixth" independent number) in this text: an earlier draft used one
    # inconsistently between this note and its HTML lead-in (the workflow that verified this
    # page caught it), and neither number actually counted anything real -- Accuracy is simply
    # a second metric that happens to duplicate the first on most tasks, not the Nth of a set.
    assert "independent" in d["note"]
    assert not re.search(r"\b(fifth|sixth|fourth|five|four)\s+independent\b", d["note"]), (
        "a stale count word ('five independent looks', etc) has crept back into the note")
    assert not re.search(r"\b(fifth|sixth|fourth)\b", page), (
        "a stale ordinal has crept back into the rendered page")
    # The page's bold lead-in sentence ("Accuracy is not independent information...") already
    # makes the note's opening claim; the note itself must CONTINUE that thought, not repeat
    # it -- rendering the same sentence twice back-to-back is what the workflow that reviewed
    # this page first caught.
    assert not d["note"].lstrip().startswith("Accuracy is not"), (
        "the note duplicates the page's bold lead-in sentence instead of continuing from it")
    for t, cls in d["per_task"].items():
        assert t in page, f"{t} is not named in the accuracy disclosure on the page"
        # The summary text is reworded slightly for the bullet ("identical to X" appears in
        # both), so check the distinguishing phrase rather than the exact string.
        key_phrase = cls["summary"].split(" on every dimension")[0].split(", not derivable")[0]
        assert key_phrase[:20] in page, f"{t}: {key_phrase!r} not reflected on the page"


def test_no_ratio_column_in_the_table(page):
    """The per-task '% of production' columns were removed by request.

    The overall ratio still belongs on the page -- it is the headline -- but it lives in the
    KPI tile and the All-six row's sub-label, not as a column repeated on every task row.
    """
    tbl = page.split('<table class="mx">', 1)[1].split("</table>", 1)[0]
    hdr = tbl.split("</thead>", 1)[0]
    assert "of prod." not in hdr, "a per-task ratio sub-column has come back"
    body = tbl.split("<tbody>", 1)[1]
    task_rows = re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.S)[:-1]
    for r in task_rows:
        assert "%" not in r, "a task row carries a percentage; the ratio columns were removed"


def test_every_row_has_eleven_cells(page):
    """Task + five two-column groups (F1, Accuracy, input, output, cost per item)."""
    tbl = page.split('<table class="mx">', 1)[1].split("</table>", 1)[0]
    body = tbl.split("<tbody>", 1)[1]
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.S)
    assert len(rows) == 7, f"expected 6 task rows + 1 total row, got {len(rows)}"
    for r in rows:
        cells = re.findall(r"<td[^>]*>", r)
        assert len(cells) == 11, f"expected 11 cells (task + 5 pairs), got {len(cells)}"


def test_cost_column_is_per_item_never_a_run_total(s, page):
    """The reader asked for average cost per item and said not to add them up.

    A run total in this column would be silently ~200x larger on RTR-Fraud (198 submissions)
    and would rescale with how many items each run happened to cover.
    """
    tbl = page.split('<table class="mx">', 1)[1].split("</table>", 1)[0]
    body = tbl.split("<tbody>", 1)[1]
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.S)[:-1]
    assert len(rows) == 6
    for r, p in zip(rows, sorted(s["per_use_case"], key=lambda p: -p["pct_of_incumbent"])):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", r, re.S)
        cost = cells[-2]
        per_item = p["cost_per_item_incumbent_usd"]
        assert f"${per_item:,.4f}" in cost, (
            f"{p['use_case']}: cost cell should show the per-item figure "
            f"${per_item:,.4f}, got {cost!r}")
        # The run total must NOT be what is printed there.
        run_total = p["cost_incumbent_usd"]
        if run_total is not None and abs(run_total - per_item) > 1e-6:
            assert f"${run_total:,.2f}" not in cost, (
                f"{p['use_case']}: the cost cell is showing the run total, not per item")
        assert f"per {p['cost_unit']}" in cost, (
            f"{p['use_case']}: the unit noun must ride with the figure")


def test_the_all_six_cost_cell_carries_no_aggregate(s, page):
    """Three different units (submission / page / call) cannot be summed or averaged.

    A mean would divide dollars by submissions-plus-pages-plus-calls, which is not a quantity;
    a sum of run totals answers a different question. The cell says so instead of showing a
    number a reader would quote.
    """
    tbl = page.split('<table class="mx">', 1)[1].split("</table>", 1)[0]
    body = tbl.split("<tbody>", 1)[1]
    last = re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.S)[-1]
    cells = re.findall(r"<td[^>]*>(.*?)</td>", last, re.S)
    cost = cells[-2]
    assert "not aggregated" in cost, (
        f"the All-six cost cell must say it is not aggregated: {cost!r}")

    # The RANGE is allowed -- its endpoints are two real cells quoted with their own units, so
    # no two units are ever combined. What is banned is a single number standing for all six:
    # the sum of the run totals, or a mean that would divide dollars by
    # submissions-plus-pages-plus-calls.
    rng = s["cost"]["per_item_range"]
    assert f"${rng['min']:,.4f}" in cost and f"${rng['max']:,.4f}" in cost, (
        f"the range endpoints should be shown: {cost!r}")

    banned = [s["cost"]["incumbent_usd"]]                       # the $28.57 run-total sum
    per_item = [p["cost_per_item_incumbent_usd"] for p in s["per_use_case"]
                if p["cost_per_item_incumbent_usd"] is not None]
    banned.append(sum(per_item) / len(per_item))                # the unweighted mean
    total_items = sum(p["cost_denominator"] for p in s["per_use_case"]
                      if p.get("cost_denominator"))
    banned.append(s["cost"]["incumbent_usd"] / total_items)     # the item-weighted mean
    for b in banned:
        for fmt in (f"${b:,.2f}", f"${b:,.4f}"):
            assert fmt not in cost, (
                f"the All-six cost cell is showing an aggregate ({fmt}): {cost!r}")

    # ...and the reason is recorded in data, not only in the markup.
    assert len(s["cost"]["per_item_units"]) > 1
    assert "not a quantity" in s["cost"]["why_per_item_cannot_be_aggregated"]


def test_the_range_endpoints_are_real_cells_with_named_units(s, page):
    """A range across incommensurable units is only honest if each endpoint keeps its unit.

    Both endpoints must be actual per-task figures (not computed), and both tasks' unit nouns
    must appear in their own rows so a reader can see that $0.0012 is per submission and
    $0.0874 is per page rather than two points on one scale.
    """
    rng = s["cost"]["per_item_range"]
    per = {p["use_case"]: p for p in s["per_use_case"]}
    assert per[rng["min_task"]]["cost_per_item_incumbent_usd"] == pytest.approx(
        rng["min"], abs=1e-9)
    assert per[rng["max_task"]]["cost_per_item_incumbent_usd"] == pytest.approx(
        rng["max"], abs=1e-9)
    assert rng["min_task"] != rng["max_task"]
    for t in (rng["min_task"], rng["max_task"]):
        assert f"per {per[t]['cost_unit']}" in page, (
            f"{t}'s unit noun must be on the page for its endpoint to be readable")


def test_run_totals_survive_in_the_note(s, page):
    """Removing the aggregate from the table must not delete the per-run totals entirely --
    'what did this evaluation cost' is a fair question with a real answer."""
    for p in s["per_use_case"]:
        if p["cost_incumbent_usd"] is None:
            continue
        assert f"${p['cost_incumbent_usd']:,.2f}" in page, (
            f"{p['use_case']}: its run total is no longer stated anywhere on the page")


def test_per_task_tokens_and_cost_on_the_page_match_summary_json(s, page):
    for p in s["per_use_case"]:
        for key in ("input_tokens_incumbent", "input_tokens_internal",
                    "output_tokens_incumbent", "output_tokens_internal"):
            assert f"{p[key]:,}" in page, f"{p['use_case']}/{key}: {p[key]:,} not on the page"
        if p["cost_incumbent_usd"] is not None:
            assert f"${p['cost_incumbent_usd']:,.2f}" in page, (
                f"{p['use_case']}: cost ${p['cost_incumbent_usd']:,.2f} not on the page")


def test_internal_cost_column_always_reads_not_metered(mx, page):
    """No internal cost figure exists in either source -- the column must never show a number."""
    tbl = page.split('<table class="mx">', 1)[1].split("</table>", 1)[0]
    assert tbl.count("not metered") >= 7, (
        "every task row plus the total row must show 'not metered' in the internal cost cell")


def test_accuracy_blended_row_is_the_mean_of_the_six_per_task_figures(s):
    a = s["accuracy"]
    inc = statistics.fmean(p["accuracy_incumbent"] for p in s["per_use_case"])
    internal = statistics.fmean(p["accuracy_internal"] for p in s["per_use_case"])
    assert a["incumbent"] == pytest.approx(inc, abs=5e-5)
    assert a["internal"] == pytest.approx(internal, abs=5e-5)
    assert a["pct_ratio_of_means"] == pytest.approx(internal / inc * 100, abs=0.05)
