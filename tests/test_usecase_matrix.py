"""The matrix page carries a COST figure, so its arithmetic is checked, not trusted.

WHY THIS FILE EXISTS. `usecase-matrix.html` is the one page that multiplies someone else's token
counts by a price and prints dollars. Three things can go wrong quietly:

  * the token cells are prose ("450,334 label + 471,549 analysis", "176,626 (+ 227,974
    thinking)") and can be mis-parsed;
  * thinking tokens sit OUTSIDE the printed output line and INSIDE the billable total, so a
    naive cost undercounts;
  * a rate can go missing and leave a blank cell that reads as zero.

The reconciliation identity is what makes the first two checkable at all:

    input + output + thinking == the workbook's own "Total tokens" row

Where the workbook publishes that row, this file recomputes it from the parsed parts. Where a
rate is absent, it insists the page says so in words.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
BOOK = REPO / "docs" / "reports" / "parallel-eval-workbook.json"
MATRIX = REPO / "docs" / "reports" / "usecase-matrix.json"
PAGE = REPO / "docs" / "reports" / "usecase-matrix.html"


@pytest.fixture(scope="module")
def mx() -> dict:
    if not MATRIX.is_file():
        pytest.skip(f"{MATRIX.name} not present")
    return json.loads(MATRIX.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def page() -> str:
    if not PAGE.is_file():
        pytest.skip(f"{PAGE.name} not built")
    return PAGE.read_text(encoding="utf-8")


def test_every_arm_in_the_workbook_has_a_column(mx):
    if not BOOK.is_file():
        pytest.skip("workbook JSON not present")
    book = json.loads(BOOK.read_text(encoding="utf-8"))
    expected = {(n, re.sub(r"\s+", " ", a).strip())
                for n, t in book["tabs"].items()
                for a in t["arms"] if a.strip().upper() != "GROUND TRUTH"}
    got = {(c["use_case"], c["arm"]) for c in mx["columns"]}
    assert got == expected, f"columns drifted: missing {expected - got}, extra {got - expected}"


def test_token_totals_reconcile_against_the_workbooks_own_total(mx):
    """The gate the cost depends on. A parse error shows up here before it shows up in dollars."""
    rec = mx["reconciliation"]
    assert rec["checked"] > 0, "nothing was reconciled -- the check is not running"
    assert not rec["failed"], f"token parse disagrees with the workbook: {rec['failed']}"
    for c in mx["columns"]:
        if c["total_tokens_published"] is None:
            continue
        assert (c["input_tokens"] + c["output_tokens"] + (c["thinking_tokens"] or 0)
                == c["total_tokens_published"]), (
            f"{c['use_case']}/{c['arm']}: parts do not sum to the published total")


def test_thinking_tokens_are_inside_billable_output(mx):
    """The 129% trap. Billable output must include thinking wherever thinking is reported."""
    with_thinking = [c for c in mx["columns"] if c["thinking_tokens"]]
    assert with_thinking, "no arm reports thinking tokens; the note about them is now stale"
    for c in with_thinking:
        assert c["billable_output_tokens"] == c["output_tokens"] + c["thinking_tokens"], (
            f"{c['use_case']}/{c['arm']}: thinking tokens are not in the billable output")
        assert c["billable_output_tokens"] > c["output_tokens"]


def test_cost_is_never_printed_without_a_rate(mx):
    """Any money figure must carry the rate and the source URL it came from."""
    for c in mx["columns"]:
        if c["cost_usd"] is None:
            assert c["cost_status"] in ("not-metered", "rate not established"), c
            continue
        assert c["rate_used"], f"{c['use_case']}/{c['arm']}: costed with no rate recorded"
        assert c["rate_used"].get("source_url"), (
            f"{c['use_case']}/{c['arm']}: a published cost needs a source URL for its rate")


def test_audio_runs_are_priced_at_the_audio_rate(mx):
    """The rate applied must match what the run actually sent the model.

    Gemini charges audio input at 3.3x the text rate on 2.5 Flash. A run whose own workbook row
    says "none - audio into the model" is mostly audio on the input side, so pricing it at the
    text rate would understate it. The floor -- everything priced as text -- is kept beside it
    because the system prompt inside that same total IS text, so the headline is an upper bound.
    """
    audio = [c for c in mx["columns"] if c["sends_audio"] and c["cost_usd"] is not None]
    assert audio, "no audio run is costed; the audio rate or its detection has been lost"
    for c in audio:
        r = c["rate_used"]
        assert r.get("input_audio_usd_per_1m"), f"{c['arm']}: audio run with no audio rate"
        assert c["input_rate_kind"] == "audio"
        assert c["input_rate_applied"] == r["input_audio_usd_per_1m"]
        assert c["cost_usd_floor"] is not None, "an audio run must carry its all-text floor"
        assert c["cost_usd_floor"] < c["cost_usd"], (
            f"{c['arm']}: the floor must be below the audio-rate figure")

    for c in mx["columns"]:
        if c["cost_usd"] is None or c["sends_audio"]:
            continue
        assert c["input_rate_kind"] == "text", f"{c['arm']}: non-audio run priced as audio"
        assert c["cost_usd_floor"] is None, "only audio runs need a floor"


def test_the_per_item_cost_is_not_rounded_twice(mx):
    """A stored intermediate at 5dp, displayed at 4dp, rounds twice.

    Sentiment Retention is 1.9838/97 = 0.0204515. Displayed straight it is
    $0.0205; stored first as 0.02045 -- a double sitting a hair below the tie
    -- it printed $0.0204, and that wrong digit reached both the HTML report
    and the deck. The stored value must format identically to the quotient.
    """
    for c in mx["columns"]:
        for value, total in (("cost_per_item_usd", "cost_usd"),
                             ("cost_per_item_usd_floor", "cost_usd_floor")):
            if c[value] is None or c[total] is None:
                continue
            quotient = c[total] / c["cost_denominator"]
            assert f"{c[value]:,.4f}" == f"{quotient:,.4f}", (
                f"{c['use_case']} {c['arm']} {value}: stored {c[value]!r} "
                f"prints {c[value]:,.4f} but the quotient prints "
                f"{quotient:,.4f}"
            )


def test_cost_arithmetic_is_reproducible(mx):
    """Recompute every printed cost from its own recorded rate and token counts."""
    checked = 0
    for c in mx["columns"]:
        if c["cost_usd"] is None:
            continue
        r = c["rate_used"]
        expect = (c["input_tokens"] / 1e6 * c["input_rate_applied"]
                  + c["billable_output_tokens"] / 1e6 * r["output_usd_per_1m"])
        assert c["cost_usd"] == pytest.approx(expect, abs=5e-4), (
            f"{c['use_case']}/{c['arm']}: ${c['cost_usd']} != ${expect:.4f} from its own rate")
        if c["cost_usd_floor"] is not None:
            floor = (c["input_tokens"] / 1e6 * r["input_usd_per_1m"]
                     + c["billable_output_tokens"] / 1e6 * r["output_usd_per_1m"])
            assert c["cost_usd_floor"] == pytest.approx(floor, abs=5e-4)
        if c["cost_per_item_usd"] is not None:
            assert c["cost_per_item_usd"] == pytest.approx(
                c["cost_usd"] / c["items_scored"], abs=5e-5)
        checked += 1
    assert checked, "no arm is costed"


def test_only_incumbent_arms_are_costed(mx):
    """Self-hosted runs produce no invoice. Printing a dollar figure for one would be invention."""
    for c in mx["columns"]:
        if c["side"] == "internal":
            assert c["cost_usd"] is None, f"{c['arm']} is self-hosted and must not carry a cost"
            assert c["cost_status"] == "not-metered"


def test_every_incumbent_run_is_costed(mx):
    """The user asked for a cost per run. A silently uncosted run makes the total look complete."""
    missing = [f"{c['use_case']}/{c['arm']}" for c in mx["columns"]
               if c["side"] == "incumbent" and c["cost_usd"] is None]
    assert not missing, f"these incumbent runs carry no cost: {missing}"


def test_an_assumed_model_is_flagged_and_explained(mx, page):
    """RTR-Fraud's model is not in the workbook, so its cost rests on an assumption.

    Costing it is right -- the user asked for every run -- but a figure that depends on a guess
    about which model ran must say so on the page, not only in the JSON.
    """
    assumed = [c for c in mx["columns"] if c.get("model_assumed")]
    if not assumed:
        pytest.skip("no model is assumed")
    for c in assumed:
        assert c["side"] == "incumbent"
        assert c["cost_usd"] is not None
    assert "rests on an assumption" in page, (
        "the page must say which figure depends on an assumed model")
    assert "names no model" in page


def test_the_page_never_leaves_a_cost_cell_blank(mx, page):
    """A blank cell in a money row reads as zero. Every cell must say something."""
    assert "not metered" in page, "self-hosted arms must be labelled, not left empty"
    for c in mx["columns"]:
        if c["cost_usd"] is not None or c["side"] == "internal":
            continue
        assert "no rate" in page, (
            f"{c['use_case']}/{c['arm']} is uncosted and the page does not say why")

def test_page_is_pure_ascii_and_structurally_sound(page):
    bad = {c for c in page if ord(c) > 126}
    assert not bad, f"non-ascii would render as mojibake without a charset: {bad}"
    for tag in ("div", "table", "thead", "tbody", "tr", "td", "th", "p", "ul", "li", "span"):
        o = len(re.findall(rf"<{tag}[ >]", page))
        c = len(re.findall(rf"</{tag}>", page))
        assert o == c, f"<{tag}> unbalanced: {o} open, {c} close"
    for leak in ("&amp;middot;", "&amp;mdash;", "&amp;times;"):
        assert leak not in page, f"{leak} is printed as text"


def test_the_measure_rows_the_user_asked_for_are_present(page):
    """Input tokens, output tokens and one per-item cost are the point of this page."""
    # Match ROW LABELS, not prose: the footnote legitimately discusses thinking tokens and the
    # whole-run totals, and a substring check would collide with both.
    labels = re.findall(r"<td class='ml'>([^<]*)", page)
    for label in ("Input tokens", "Output tokens", "Cost per item scored"):
        assert label in labels, f"the '{label}' row is missing; rows are {labels}"
    assert "Cost of the run" not in labels, (
        "the whole-run total row was removed from the table; it belongs in the footnote only")
    assert "Thinking tokens" not in labels, (
        "thinking tokens are folded into the output row, not given one of their own")


def test_the_rtr_denominator_is_items_not_requests(mx):
    """A live 3x trap: RTR-Fraud publishes 594 model calls against 198 scored submissions.

    Its workbook row is "Model calls" -- 198 for the incumbent, 594 for both internal arms over
    the SAME 198 items, because those arms call once per field. Dividing an arm's cost by its
    request count would understate its per-item cost by exactly 3x. Nothing renders that today
    (the internal arms are self-hosted and uncosted), which is precisely why it needs a test:
    the bug would arrive silently the first time a rate exists for them.
    """
    rtr = [c for c in mx["columns"] if c["use_case"] == "RTR-Fraud"]
    assert rtr, "RTR-Fraud is missing"
    assert {c["cost_denominator"] for c in rtr} == {198}, (
        f"every RTR-Fraud arm must divide by 198 scored submissions, got "
        f"{[(c['arm'], c['cost_denominator']) for c in rtr]}")
    assert any(c["items_scored"] == 594 for c in rtr), (
        "the 594 request count should still be visible in items_scored -- if it has gone, this "
        "test no longer guards anything")
    assert all(c["cost_unit"] == "submission" for c in rtr)


def test_tax_invoice_is_priced_per_page_not_per_document(mx):
    """147 pages sit inside 32 files. Choosing files would understate by 4.59x."""
    tax = [c for c in mx["columns"] if c["use_case"] == "Tax-Invoice"]
    assert tax, "Tax-Invoice is missing"
    assert {c["cost_denominator"] for c in tax} == {147}, (
        f"expected 147 pages, got {[(c['arm'], c['cost_denominator']) for c in tax]}")
    assert all(c["cost_unit"] == "page" for c in tax)


def test_every_cost_cell_names_its_unit(mx, page):
    """The units differ per column, so a bare row of dollars invites subtraction across them."""
    units = {c["cost_unit"] for c in mx["columns"] if c["cost_per_item_usd"] is not None}
    assert len(units) > 1, "if every column shared a unit this guard would be unnecessary"
    for u in units:
        assert f"per {u}" in page, f"the unit '{u}' is not named beside its figure"
    assert "Not comparable across columns" in page


def test_the_audio_upper_bound_survives_at_the_per_item_grain(mx, page):
    """The floor is roughly half, and this is now the page's only cost figure."""
    audio = [c for c in mx["columns"] if c["cost_per_item_usd"] is not None and c["sends_audio"]]
    assert audio, "no audio run is costed"
    for c in audio:
        assert c["cost_per_item_usd_floor"] is not None, (
            f"{c['use_case']}: per-item floor missing, so the band is invisible on the page")
        assert c["cost_per_item_usd_floor"] < c["cost_per_item_usd"]
        assert c["cost_per_item_usd_floor"] == pytest.approx(
            c["cost_usd_floor"] / c["cost_denominator"], abs=5e-5)
    assert "&dagger;" in page, "the audio cells must be marked as upper bounds"
    assert "upper bound" in page


def test_the_whole_run_totals_moved_to_the_footnote(mx, page):
    """Removing the total row must not lose the totals; they move, they do not vanish."""
    for c in mx["columns"]:
        if c["cost_usd"] is None:
            continue
        assert f"${c['cost_usd']:,.2f}" in page, (
            f"{c['use_case']}: the whole-run total ${c['cost_usd']:,.2f} is nowhere on the page")


def _rendered(mx):
    """The columns the page actually shows: incumbent + best internal arm per use case.

    Uses the generator's own selection so the test cannot drift from what is rendered.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_um", REPO / "scripts" / "usecase_matrix.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.shown_columns(mx["columns"])


def test_the_page_shows_two_columns_per_use_case(mx):
    """Narrowed from every arm to incumbent + best internal, to match the summary."""
    keep, dropped = _rendered(mx)
    by_tab: dict = {}
    for c in keep:
        by_tab.setdefault(c["use_case"], []).append(c)
    for tab, cs in by_tab.items():
        assert len(cs) == 2, f"{tab}: {len(cs)} columns rendered, expected 2"
        assert [c["side"] for c in cs] == ["incumbent", "internal"], tab
        pool = [c for c in mx["columns"]
                if c["use_case"] == tab and c["side"] == "internal"]
        assert cs[1]["headline_f1"] == max(c["headline_f1"] for c in pool), (
            f"{tab}: the rendered internal arm is not the best-scoring one")
    assert len(keep) + len(dropped) == len(mx["columns"])


def test_dropped_arms_are_named_on_the_page(mx, page):
    """Narrowing is fine; narrowing silently is not. Tax-Invoice's dropped arm is Gemma at
    0.6205, which is evidence that the choice of internal model matters."""
    _, dropped = _rendered(mx)
    if not dropped:
        pytest.skip("no arm is dropped")
    assert "not shown here" in page, "the page does not disclose that arms were dropped"
    plain = html.unescape(re.sub(r"<[^>]+>", " ", page))
    for d in dropped:
        arm = d.split(": ", 1)[1].rsplit(" (", 1)[0]
        assert arm in plain, f"dropped arm {arm!r} is not named on the page"


def test_every_token_figure_on_the_page_matches_the_json(mx, page):
    """Every RENDERED column's tokens must appear. Dropped arms are covered by the test above."""
    keep, _ = _rendered(mx)
    plain = html.unescape(re.sub(r"<[^>]+>", " ", page))
    for c in keep:
        for key in ("input_tokens", "output_tokens", "total_tokens_computed"):
            v = c[key]
            if v is None:
                continue
            assert f"{v:,}" in plain, (
                f"{c['use_case']}/{c['arm']}: {key} {v:,} is not on the page")
