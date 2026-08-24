"""Guards on the two benchmark slides added to the migration deck.

These slides go to CDAO, so the failure that matters is not a crash -- it is a
number that quietly stops matching docs/reports/summary.json, or a claim the
evidence does not carry. Every assertion below re-derives its expectation from
the JSON rather than comparing against a literal, so editing a slide to say
something nicer breaks a test instead of shipping.

The tests run against the shipped deck at docs/reports/ when it exists. When it
does not, they build into tmp from a synthesised eight-slide stand-in, so the
generator's logic stays covered on a clean checkout.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

pptx = pytest.importorskip("pptx", reason="python-pptx is not installed")

import deck_slides as ds  # noqa: E402
from pptx import Presentation  # noqa: E402
from pptx.util import Emu, Inches, Pt  # noqa: E402

BUILT = REPO / "docs" / "reports" / "internal-model-migration-update.pptx"
EPS = 0.01  # inches


# ---------------------------------------------------------------- fixtures

@pytest.fixture(scope="module")
def summary():
    return json.loads(ds.SUMMARY_JSON.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def matrix():
    return json.loads(ds.MATRIX_JSON.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def pricing():
    return json.loads(ds.PRICING_JSON.read_text(encoding="utf-8"))


def _stand_in(path):
    """An eight-slide source deck carrying the deck's real footer defects.

    Slides 1, 3 and 4 of the original render '1 /87', '3 87' and '4 87': the
    separator was lost and a stale seven-slide total survived as a third run.
    The stand-in reproduces that exactly so the repair is actually tested.
    """
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.3333333), Inches(7.5)
    broken = {1: ("1 /", "8", "7"), 3: ("3 ", "8", " 7"), 4: ("4 ", "8", " 7")}
    for n in range(1, 9):
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        box = slide.shapes.add_textbox(
            Inches(ds.X_PAGENO), Inches(ds.Y_FOOTER), Inches(1.18), Inches(0.14)
        )
        para = box.text_frame.paragraphs[0]
        for chunk in broken.get(n, (f"{n} / ", "8")):
            run = para.add_run()
            run.text = chunk
            run.font.size = Pt(8.5)
    prs.save(str(path))
    return path


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    if BUILT.exists():
        return Presentation(str(BUILT))
    tmp = tmp_path_factory.mktemp("deck")
    ds.build(_stand_in(tmp / "source.pptx"), tmp / "out.pptx")
    return Presentation(str(tmp / "out.pptx"))


@pytest.fixture(scope="module")
def new_slides(built):
    slides = list(built.slides)
    return slides[ds.NEW_AT], slides[ds.NEW_AT + 1]


def cells(slide):
    """Every text box's exact text. Each table cell is its own box."""
    return {
        s.text_frame.text for s in slide.shapes
        if s.has_text_frame and s.text_frame.text.strip()
    }


def blob(*slides):
    return "\n".join(
        s.text_frame.text for slide in slides for s in slide.shapes
        if s.has_text_frame
    )


# ------------------------------------------------------------- structure

def test_the_two_slides_land_as_pages_seven_and_eight(built, new_slides):
    assert len(built.slides) == 10
    accuracy, cost = new_slides
    assert "Benchmark results" in blob(accuracy)
    assert "accuracy" in blob(accuracy).split("\n")[0]
    assert "cost and speed" in blob(cost).split("\n")[0]


def test_every_footer_is_renumbered_and_the_stale_total_is_gone(built):
    seen = []
    for i, slide in enumerate(built.slides, start=1):
        for shape in slide.shapes:
            if not shape.has_text_frame or shape.left is None:
                continue
            if shape.left < Inches(10.4) or shape.top < Inches(6.9):
                continue
            seen.append(shape.text_frame.text)
            assert shape.text_frame.text == f"{i} / 10"
            # the defect was a page number split across three runs
            assert len(shape.text_frame.paragraphs[0].runs) == 1
    assert len(seen) == 10
    assert "87" not in "".join(seen)


def test_the_broken_page_numbers_collapse_to_a_single_run(tmp_path):
    source = _stand_in(tmp_path / "src.pptx")
    before = Presentation(str(source))
    first = before.slides[0].shapes[0].text_frame
    assert first.text == "1 /87", "the stand-in must reproduce the defect"

    ds.build(source, tmp_path / "out.pptx")
    after = Presentation(str(tmp_path / "out.pptx"))
    assert after.slides[0].shapes[0].text_frame.text == "1 / 10"
    assert after.slides[2].shapes[0].text_frame.text == "3 / 10"
    assert after.slides[3].shapes[0].text_frame.text == "4 / 10"


# ------------------------------------------------------------- accuracy

def test_every_f1_and_accuracy_cell_matches_summary_json(new_slides, summary):
    printed = cells(new_slides[0])
    for row in summary["per_use_case"]:
        assert row["use_case"] in printed
        for key in ("incumbent_f1", "internal_f1",
                    "accuracy_incumbent", "accuracy_internal"):
            assert ds.f4(row[key]) in printed, f"{row['use_case']} {key}"


def test_the_mean_row_is_the_published_headline(new_slides, summary):
    printed = cells(new_slides[0])
    for block in ("quality", "accuracy"):
        for side in ("incumbent", "internal"):
            assert ds.f4(summary[block][side]) in printed


def test_the_ratio_lives_in_the_tile_not_in_a_column(new_slides, summary):
    """The user removed the '% of production' columns from the report.

    The overall ratio still has to be visible, so it belongs in the KPI tile.
    """
    printed = cells(new_slides[0])
    assert f"{summary['quality']['pct_ratio_of_means']}%" in printed
    headers = [label for _, _, label in ds.ACC_COLS]
    assert not any("%" in h or "production" in h.lower() for h in headers)
    for row in summary["per_use_case"]:
        assert f"{row['pct_of_incumbent']}%" not in printed, row["use_case"]


def test_the_credited_workbook_is_the_one_that_was_parsed(new_slides, matrix):
    """Two revisions of this workbook sit side by side in docs/reports.

    The superseded one still shows Sentiment Retention at 0.66135/0.61750
    against the 0.8831/0.8082 the slide prints, so crediting it would send a
    reader to a file that contradicts the slide. The credit is taken from the
    parse rather than typed, and this refuses the other name outright.
    """
    body = blob(new_slides[0])
    parsed = matrix["source_file"]
    assert f"Source: {parsed}" in body

    superseded = parsed.replace(" (1)", "")
    if superseded != parsed:
        assert f"Source: {superseded}," not in body, (
            f"the deck credits {superseded}, which is not what was parsed")


def test_the_retention_rescore_is_disclosed(new_slides, summary):
    """The HTML report discloses it; a deck generated from the same JSON
    must not be less disclosed than the report it came from.

    It also matters which way the rescore cut: it moved against the internal
    side, so keeping the task in is the conservative choice, and the slide has
    to say that rather than merely admitting a revision happened.
    """
    body = blob(new_slides[0])
    sensitivity = summary["sensitivity_excluding_retention"]
    assert "rescored" in body
    assert "Sentiment Retention was rescored" in body
    assert f"{sensitivity['pct_of_incumbent']}%" in body
    assert f"{summary['quality']['pct_ratio_of_means']}%" in body
    assert "moved both arms" in body
    # the excluded-headline figure must never be presented as the headline
    assert "It stays in" in body


def test_accuracy_is_never_presented_as_independent_evidence(new_slides,
                                                             summary):
    """summary.must_not_claim forbids exactly this reading."""
    body = blob(new_slides[0])
    assert "not a second, independent look" in body
    degenerate = [
        task for task, d in summary["accuracy_disclosure"]["per_task"].items()
        if not d["independent"] and "equal" in d["summary"]
    ]
    for task in degenerate:
        assert task in body, f"{task} is degenerate but is not disclosed"


def test_the_two_internal_leads_are_called_parity_not_wins(new_slides,
                                                           summary):
    body = blob(new_slides[0])
    assert "parity, not wins" in body
    for task in summary["parity_not_wins"]["tasks"]:
        assert task in body
    leads = [r["use_case"] for r in summary["per_use_case"]
             if r["leader"] == "internal"]
    assert sorted(leads) == sorted(summary["parity_not_wins"]["tasks"])


# ------------------------------------------------------- cost and speed

def test_every_cost_cell_is_per_item_with_its_unit_noun(new_slides, summary):
    printed = cells(new_slides[1])
    for row in summary["per_use_case"]:
        cell = ds.cost_cell(row)
        assert cell in printed, row["use_case"]
        assert row["cost_unit"] in cell


def test_no_aggregate_cost_reaches_the_slide(new_slides, summary):
    """Three units cannot be summed or averaged -- see summary.cost."""
    body = blob(new_slides[1])
    per_item = [r["cost_per_item_incumbent_usd"]
                for r in summary["per_use_case"]]
    banned = {
        ds.money(summary["cost"]["incumbent_usd"]),          # the run total sum
        ds.money(sum(per_item) / len(per_item)),             # unweighted mean
        ds.money(sum(per_item)),                             # per-item sum
    }
    for value in banned:
        assert value not in body, f"{value} is not a quantity"


def test_the_dagger_marks_the_upper_bounds_and_names_their_floors(new_slides,
                                                                  summary):
    printed = cells(new_slides[1])
    body = blob(new_slides[1])
    for row in summary["per_use_case"]:
        cell = next(c for c in printed if c.startswith(
            ds.money(row["cost_per_item_incumbent_usd"])))
        assert ("†" in cell) is bool(row["cost_is_upper_bound"]), \
            row["use_case"]
        if row["cost_is_upper_bound"]:
            assert ds.money(row["cost_per_item_floor_usd"]) in body
    assert "upper bounds" in body


def test_the_asterisk_marks_the_assumed_model_and_prices_the_alternative(
        new_slides, summary):
    printed = cells(new_slides[1])
    body = blob(new_slides[1])
    for row in summary["per_use_case"]:
        cell = ds.cost_cell(row)
        assert cell in printed, row["use_case"]
        assert ("*" in cell) is bool(row["cost_model_assumed"]), row["use_case"]
    assert "names no model at all" in body


def test_the_repriced_exposure_is_arithmetic_not_a_typed_figure(
        new_slides, summary, matrix, pricing):
    """The first draft of this footnote named a model the rate card lacks.

    It said "at 2.5 Pro rates", but the only Pro tier in
    openrouter-pricing.json is gemini-3.1-pro-preview, and the $0.0077 figure
    had been computed from that one. Deriving the whole sentence makes the two
    impossible to disagree, and this test additionally refuses any model name
    the rate card does not carry.
    """
    exposure = ds.assumed_model_exposure(summary, matrix, pricing)
    body = blob(new_slides[1])

    known = {ds.model_label(slug) for slug in pricing["rates"]}
    assert exposure["assumed"] in known
    assert exposure["alternative"] in known
    for name in ("Gemini 2.5 Pro", "Gemini 1.5 Pro", "2.5 Pro rates"):
        assert name not in body, f"{name} is not in the rate card"

    # recomputed here from raw tokens, independently of the generator's helper
    row = next(r for r in summary["per_use_case"] if r["cost_model_assumed"])
    col = ds.matrix_index(matrix)[row["use_case"]][
        ("incumbent", row["incumbent_arm"])]
    rate = pricing["rates"][
        next(k for k in pricing["rates"]
             if ds.model_label(k) == exposure["alternative"])]
    expected = (col["input_tokens"] / 1e6 * rate["input_usd_per_1m"]
                + col["billable_output_tokens"] / 1e6
                * rate["output_usd_per_1m"]) / col["cost_denominator"]
    assert exposure["per_item"] == pytest.approx(expected)
    assert ds.money(expected) in body
    assert f"{exposure['multiple']:.1f}× higher" in body
    assert exposure["assumed"] in body and exposure["alternative"] in body


def test_internal_cost_is_declared_unmetered_and_never_zero(new_slides):
    body = blob(new_slides[1])
    assert "not metered" in body
    assert "Not $0, not free and not n/a" in body
    assert "$0.0000" not in body


def test_slowdown_ratios_match_the_speed_block(new_slides, summary):
    printed = cells(new_slides[1])
    for entry in summary["speed"]["per_use_case"]:
        assert ds.ratio(entry["ratio"]) in printed, entry["use_case"]


def test_whole_round_conversions_match_round_seconds(new_slides, summary,
                                                     matrix):
    printed = cells(new_slides[1])
    index = ds.matrix_index(matrix)
    for row in summary["per_use_case"]:
        inc, internal = ds.round_pair(index, row)
        assert f"{ds.duration(inc)} → {ds.duration(internal)}" in printed


def test_the_round_column_is_not_confused_with_per_item_latency(
        new_slides, summary, matrix):
    """The two slowdown constructions differ; the slide has to say so.

    The note must name the row where they diverge most, computed from the
    data -- picking a row by hand lets the example go stale, and understates
    the divergence if a worse one appears.
    """
    body = blob(new_slides[1])
    assert "not per-item latency" in body

    ratios = {r["use_case"]: r["ratio"]
              for r in summary["speed"]["per_use_case"]}
    worst = ds.widest_round_divergence(summary, matrix, ratios)
    assert worst["task"] in body
    assert ds.ratio(worst["per_item"]) in body
    assert ds.ratio(worst["round"]) in body

    index = ds.matrix_index(matrix)
    for use_case, per_item in ratios.items():
        row = next(r for r in summary["per_use_case"]
                   if r["use_case"] == use_case)
        inc, internal = ds.round_pair(index, row)
        if not inc or not internal:
            continue
        spread = abs(internal / inc - per_item)
        assert spread <= worst["spread"] + 1e-9, \
            f"{use_case} diverges more than the row the note names"


def test_the_unmeasured_latency_does_not_flatter_the_internal_side(new_slides,
                                                                   summary):
    unmeasured = summary["speed"]["tasks_unmeasured"][0]
    printed, body = cells(new_slides[1]), blob(new_slides[1])
    assert "not measurable" in printed
    assert unmeasured["workbook_says"] in body
    assert f"{unmeasured['other_arm_ratio']}×" in body
    tile = f"{summary['speed']['range'][0]:.1f}× – " \
           f"{summary['speed']['widest_known_ratio']:.1f}×"
    assert tile in printed, "the widest known ratio must reach the headline"


# ------------------------------------------------------------ formatting

@pytest.mark.parametrize("seconds,expected", [
    (121.95, "2.0 min"), (1013.16, "16.9 min"), (3599.0, "60.0 min"),
    (3600.0, "1.0 h"), (11715.9, "3.3 h"), (None, "not measurable"),
])
def test_duration_switches_unit_at_the_hour(seconds, expected):
    assert ds.duration(seconds) == expected


def test_the_deck_rounds_exactly_as_the_published_report_does(new_slides,
                                                             summary):
    """Both render summary.json through ':.4f'; they cannot disagree."""
    report = (REPO / "docs" / "reports" / "usecase-matrix.html")
    if not report.exists():
        pytest.skip("usecase-matrix.html not generated")
    html = report.read_text(encoding="utf-8")
    printed = cells(new_slides[0])
    for row in summary["per_use_case"]:
        value = ds.f4(row["incumbent_f1"])
        assert value in printed and value in html, row["use_case"]


# ---------------------------------------------------------------- layout

def test_no_shape_crosses_the_slide_bounds(new_slides):
    for slide in new_slides:
        for shape in slide.shapes:
            left = Emu(shape.left).inches
            right = left + Emu(shape.width).inches
            bottom = Emu(shape.top).inches + Emu(shape.height).inches
            assert left >= -EPS, shape.name
            assert right <= ds.RIGHT_EDGE + EPS, f"{shape.name} ends at {right}"
            assert bottom <= 7.30 + EPS, f"{shape.name} ends at {bottom}"


def test_table_columns_do_not_overlap():
    for cols in (ds.ACC_COLS, ds.COST_COLS):
        for (x, w, _), (nx, _, _) in zip(cols, cols[1:]):
            assert x + w <= nx + EPS
        last_x, last_w, _ = cols[-1]
        assert last_x + last_w <= ds.RIGHT_EDGE + EPS


def test_every_run_uses_the_decks_font_and_palette(new_slides):
    for slide in new_slides:
        for shape in slide.shapes:
            if not shape.has_text_frame:
                continue
            for para in shape.text_frame.paragraphs:
                for run in para.runs:
                    assert run.font.name == ds.FONT, run.text[:30]
                    assert str(run.font.color.rgb) in ds.PALETTE, run.text[:30]


def test_every_fill_and_rule_is_in_the_decks_palette(new_slides):
    for slide in new_slides:
        for shape in slide.shapes:
            if not shape.has_text_frame or shape.shape_type is None:
                continue
            try:
                if shape.fill.type == 1:
                    assert str(shape.fill.fore_color.rgb) in ds.PALETTE
            except (TypeError, AttributeError):
                pass
            try:
                if shape.line.fill.type == 1:
                    assert str(shape.line.color.rgb) in ds.PALETTE
            except (TypeError, AttributeError):
                pass


# ----------------------------------------------------------- the gate

def test_check_passes_on_the_built_deck(tmp_path):
    if not BUILT.exists():
        pytest.skip("shipped deck not built")
    assert ds.check(BUILT) == 0


def test_check_rejects_a_deck_that_was_not_built_from_this_json(tmp_path):
    """The staleness gate has to actually fail, not just pass on a good file."""
    source = _stand_in(tmp_path / "src.pptx")
    assert ds.check(source) == 1, "an eight-slide deck must not pass --check"


def test_the_gate_covers_the_derived_figures_too(summary, matrix, pricing):
    """A staleness gate that skips the derived numbers is not a gate."""
    facts = ds.expected_facts(summary, matrix, pricing)
    exposure = ds.assumed_model_exposure(summary, matrix, pricing)
    assert ds.money(exposure["per_item"]) in facts
    assert f"{exposure['multiple']:.1f}×" in facts
    assert exposure["alternative"] in facts


def test_check_exits_two_when_the_deck_is_absent(tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        ds.check(tmp_path / "nope.pptx")
    assert excinfo.value.code == 2
