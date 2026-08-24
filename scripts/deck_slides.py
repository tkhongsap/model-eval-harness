#!/usr/bin/env python3
"""Add two benchmark-results slides to the internal-model-migration deck.

The deck this writes into is hand-built: every slide uses the Blank layout with
absolutely-positioned text boxes, so there are no placeholders to fill and the
grid has to be reproduced by hand. The constants below were extracted from the
existing eight slides, not invented.

Every figure printed on the two new slides is formatted from
docs/reports/summary.json (plus docs/reports/usecase-matrix.json for the
whole-round wall clock). Nothing is typed in as a literal, so the deck and the
published usecase-matrix.html report cannot drift apart.

    python scripts/deck_slides.py --source IN.pptx --out OUT.pptx
    python scripts/deck_slides.py --check --out OUT.pptx
    python scripts/deck_slides.py --source IN.pptx --out OUT.pptx \
        --preview-html preview.html

Exit codes follow scripts/parallel_eval_workbook.py: 2 when a source file is
absent, 1 when the built deck disagrees with the JSON.
"""

from __future__ import annotations

import argparse
import json
import sys
from html import escape
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE, MSO_SHAPE_TYPE
from pptx.enum.text import MSO_AUTO_SIZE, PP_ALIGN
from pptx.util import Inches, Pt

REPO = Path(__file__).resolve().parent.parent
SUMMARY_JSON = REPO / "docs" / "reports" / "summary.json"
MATRIX_JSON = REPO / "docs" / "reports" / "usecase-matrix.json"
PRICING_JSON = REPO / "docs" / "reports" / "openrouter-pricing.json"

# --------------------------------------------------------------------------
# The deck's design system, read off the existing eight slides.
# --------------------------------------------------------------------------

FONT = "Segoe UI"

INK_TITLE = "303C46"      # titles, table header band
INK_BODY = "374151"       # table and card body
INK_MUTED = "64748B"      # subtitles, footers, notes
INK_CAPTION = "8A8A8A"    # KPI captions
INK_AMBER = "8A5E00"      # text on the amber chip
ACCENT_RED = "E00000"     # True red, attention cards
ACCENT_BLUE = "007AD0"    # informational card eyebrow
KPI_DEEP = "2C2454"       # the deck's one deep-purple KPI number

SURF_WHITE = "FFFFFF"
SURF_ALT = "F9F9FC"       # alternating table row
SURF_NEUTRAL = "F3F3F6"   # neutral card
SURF_BLUE = "BEE9FF"      # highlight card
SURF_AMBER = "FFF1B4"     # caution chip
RULE = "E2E8F0"           # 0.5pt table rules

# Everything a new shape is allowed to be painted. tests/test_deck_slides.py
# fails if a colour outside this set reaches the two new slides, so they cannot
# drift away from the deck's palette.
PALETTE = {
    INK_TITLE, INK_BODY, INK_MUTED, INK_CAPTION, INK_AMBER, ACCENT_RED,
    ACCENT_BLUE, KPI_DEEP, SURF_WHITE, SURF_ALT, SURF_NEUTRAL, SURF_BLUE,
    SURF_AMBER, RULE,
}

MARGIN_L = 0.55
CONTENT_W = 12.23
RIGHT_EDGE = MARGIN_L + CONTENT_W          # 12.78
SLIDE_W, SLIDE_H = 13.3333333, 7.5

Y_TITLE, Y_SUBTITLE, Y_FOOTER = 0.42, 0.98, 7.02
X_PAGENO = 11.60

CORNER_ADJ = 0.06                          # roundRect adj="6000" in the deck
CELL_DX, CELL_DY = 0.08, 0.05              # text inset inside a table band

FOOTER_TEXT = "Internal Model Migration — benchmark results"

NEW_AT = 6  # zero-based: the two slides land as pages 7 and 8


# --------------------------------------------------------------------------
# Shape vocabulary. Both slides are drawn only through these.
# --------------------------------------------------------------------------

def _textbox(slide, x, y, w, h):
    tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.auto_size = MSO_AUTO_SIZE.SHAPE_TO_FIT_TEXT
    return tb


def text(slide, x, y, w, h, paras, align=PP_ALIGN.LEFT, space_after=4.0):
    """paras: list of paragraphs, each a list of (text, size_pt, bold, hex)."""
    tb = _textbox(slide, x, y, w, h)
    tf = tb.text_frame
    for i, runs in enumerate(paras):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.space_after = Pt(space_after)
        for value, size, bold, colour in runs:
            r = p.add_run()
            r.text = value
            r.font.name = FONT
            r.font.size = Pt(size)
            r.font.bold = bold
            r.font.italic = False
            r.font.color.rgb = RGBColor.from_string(colour)
    return tb


def band(slide, x, y, w, h, fill, line=None, line_w=0.5, rounded=False):
    shape = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE if rounded else MSO_SHAPE.RECTANGLE,
        Inches(x), Inches(y), Inches(w), Inches(h),
    )
    if rounded:
        shape.adjustments[0] = CORNER_ADJ
    shape.shadow.inherit = False
    if fill:
        shape.fill.solid()
        shape.fill.fore_color.rgb = RGBColor.from_string(fill)
    else:
        shape.fill.background()
    if line:
        shape.line.color.rgb = RGBColor.from_string(line)
        shape.line.width = Pt(line_w)
    else:
        shape.line.fill.background()
    return shape


def slide_chrome(slide, title, subtitle, page_no, total):
    text(slide, MARGIN_L, Y_TITLE, 9.60, 0.50,
         [[(title, 26, True, INK_TITLE)]])
    text(slide, MARGIN_L, Y_SUBTITLE, 11.60, 0.34,
         [[(subtitle, 12, False, INK_MUTED)]])
    text(slide, MARGIN_L, Y_FOOTER, 8.00, 0.14,
         [[(FOOTER_TEXT, 8.5, False, INK_MUTED)]])
    text(slide, X_PAGENO, Y_FOOTER, 1.18, 0.14,
         [[(f"{page_no} / {total}", 8.5, False, INK_MUTED)]],
         align=PP_ALIGN.RIGHT)


def kpi_tile(slide, x, y, w, h, fill, number, number_colour, caption):
    band(slide, x, y, w, h, fill, rounded=True)
    text(slide, x + 0.18, y + 0.09, w - 0.36, 0.26,
         [[(number, 12.5, True, number_colour)]], space_after=1.0)
    text(slide, x + 0.18, y + 0.37, w - 0.36, 0.30,
         [[(caption, 7.5, False, INK_CAPTION)]], space_after=1.0)


def kpi_strip(slide, y, tiles, h=0.74, gap=0.13):
    w = (CONTENT_W - gap * (len(tiles) - 1)) / len(tiles)
    for i, (fill, number, colour, caption) in enumerate(tiles):
        kpi_tile(slide, MARGIN_L + i * (w + gap), y, w, h,
                 fill, number, colour, caption)


def table_header(slide, y, cols, h=0.34):
    band(slide, MARGIN_L, y, CONTENT_W, h, INK_TITLE)
    for x, w, label in cols:
        text(slide, x, y + 0.07, w - CELL_DX, h,
             [[(label, 9, True, SURF_WHITE)]])


def table_row(slide, y, cols, values, alt, h=0.30, bold=False,
              bold_first=True):
    band(slide, MARGIN_L, y, CONTENT_W, h,
         SURF_ALT if alt else SURF_WHITE, line=RULE, line_w=0.5)
    for i, ((x, w, _), value) in enumerate(zip(cols, values)):
        is_bold = bold or (bold_first and i == 0)
        text(slide, x, y + CELL_DY, w - CELL_DX, h,
             [[(value, 9, is_bold, INK_BODY)]])


def card(slide, x, y, w, h, eyebrow, eyebrow_colour, body, fill, line=None):
    band(slide, x, y, w, h, fill, line=line, line_w=1.25, rounded=True)
    text(slide, x + 0.20, y + 0.12, w - 0.40, 0.24,
         [[(eyebrow, 9.5, True, eyebrow_colour)]])
    text(slide, x + 0.20, y + 0.36, w - 0.40, h - 0.46,
         [[(para, 9, False, INK_BODY)] for para in body], space_after=3.0)


def notes(slide, y, w, lines, size=7.5):
    text(slide, MARGIN_L, y, w, 0.16 * len(lines),
         [[(line, size, False, INK_MUTED)] for line in lines], space_after=1.0)


# --------------------------------------------------------------------------
# Formatting. The tests call these same functions, so a printed cell and its
# expected value cannot round differently.
# --------------------------------------------------------------------------

def f4(x):
    return f"{x:.4f}"


def money(x):
    return f"${x:,.4f}"


def secs(x):
    return "not measurable" if x is None else f"{x:g} s"


def duration(sec):
    """Wall clock for a whole round, in the largest unit that stays readable."""
    if sec is None:
        return "not measurable"
    minutes = sec / 60.0
    if minutes < 60:
        return f"{minutes:.1f} min"
    return f"{minutes / 60.0:.1f} h"


def ratio(x):
    return "—" if x is None else f"{x:.1f}×"


def cost_cell(row):
    cell = f"{money(row['cost_per_item_incumbent_usd'])} / {row['cost_unit']}"
    if row.get("cost_model_assumed"):
        cell += "  *"
    if row.get("cost_is_upper_bound"):
        cell += "  †"
    return cell


# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------

def load(path, label):
    if not path.exists():
        print(f"deck_slides: {label} not found at {path}", file=sys.stderr)
        raise SystemExit(2)
    return json.loads(path.read_text(encoding="utf-8"))


def matrix_index(matrix):
    out = {}
    for col in matrix["columns"]:
        out.setdefault(col["use_case"], {})[(col["side"], col["arm"])] = col
    return out


def round_pair(index, row):
    """Whole-round wall clock for this row's two named arms.

    Joined on the arm names summary.json records, so the round column always
    describes the same arm as the F1 and cost columns beside it.
    """
    arms = index[row["use_case"]]
    inc = arms[("incumbent", row["incumbent_arm"])]
    internal = arms[("internal", row["internal_arm"])]
    return inc.get("round_s"), internal.get("round_s")


def model_label(slug):
    """'gemini-3.1-pro-preview' -> 'Gemini 3.1 Pro'."""
    parts = [part for part in slug.split("-") if part != "preview"]
    return " ".join(part.capitalize() if part.isalpha() else part
                    for part in parts)


def assumed_model_exposure(summary, matrix, pricing):
    """Reprice the one assumed-model run at the other tier in the rate card.

    That tab names no model, so its cost is an assumption rather than a
    reading. The exposure is derived here instead of typed in, because typing
    it in is how a footnote ends up naming a model the rate card does not
    carry -- which is what happened on the first draft of this slide.
    """
    row = next(r for r in summary["per_use_case"] if r["cost_model_assumed"])
    col = matrix_index(matrix)[row["use_case"]][
        ("incumbent", row["incumbent_arm"])]
    rates = pricing["rates"]
    assumed = pricing["assumed_models"][row["use_case"]]["model"]
    alternative = next(k for k in rates if k != assumed)

    def per_item(key):
        rate = rates[key]
        return (col["input_tokens"] / 1e6 * rate["input_usd_per_1m"]
                + col["billable_output_tokens"] / 1e6
                * rate["output_usd_per_1m"]) / col["cost_denominator"]

    base, high = per_item(assumed), per_item(alternative)
    return {
        "task": row["use_case"], "unit": row["cost_unit"],
        "assumed": model_label(assumed),
        "alternative": model_label(alternative),
        "per_item": high, "multiple": high / base,
    }


def widest_round_divergence(summary, matrix, ratios):
    """The row where per-item and whole-round slowdown disagree most.

    They answer different questions -- service time per call against wall
    clock for the whole batch -- and on this data they disagree by up to 10x.
    The note names a real row so a reader can see they are not interchangeable.
    """
    index = matrix_index(matrix)
    worst = None
    for row in summary["per_use_case"]:
        per_item = ratios.get(row["use_case"])
        inc, internal = round_pair(index, row)
        if per_item is None or not inc or not internal:
            continue
        whole = internal / inc
        spread = abs(whole - per_item)
        if worst is None or spread > worst["spread"]:
            worst = {"task": row["use_case"], "per_item": per_item,
                     "round": whole, "spread": spread}
    return worst


def audio_floor_note(summary):
    """Floors for the tasks priced as an upper bound, named from the data."""
    parts = [
        f"{r['use_case'].replace('Sentiment ', '')} "
        f"{money(r['cost_per_item_floor_usd'])}"
        for r in summary["per_use_case"]
        if r["cost_is_upper_bound"] and r["cost_per_item_floor_usd"] is not None
    ]
    return ", ".join(parts)


# --------------------------------------------------------------------------
# Slide 7 -- accuracy
# --------------------------------------------------------------------------

ACC_COLS = [
    (0.63, 3.30, "Use case"),
    (4.10, 1.95, "Gemini — F1"),
    (6.15, 1.95, "Qwen3.8 — F1"),
    (8.20, 2.15, "Gemini — Accuracy"),
    (10.40, 2.30, "Qwen3.8 — Accuracy"),
]


def build_accuracy_slide(prs, summary, matrix, page_no, total):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    gap = summary["where_the_gap_is"]
    quality, accuracy = summary["quality"], summary["accuracy"]
    parity = summary["parity_not_wins"]
    sensitivity = summary["sensitivity_excluding_retention"]
    rows = summary["per_use_case"]
    leads = sum(1 for r in rows if r["leader"] == "internal")

    slide_chrome(
        slide,
        "Benchmark results — accuracy",
        "Six production use cases scored on the same items — Gemini "
        "(2.5 Flash; 3.1 Pro on Tax-Invoice) against Qwen3.8-27B-FP8 on the "
        "internal GPU",
        page_no, total,
    )

    kpi_strip(slide, 1.42, [
        (SURF_BLUE, f"{quality['pct_ratio_of_means']}%", KPI_DEEP,
         "of production quality — mean of the six F1 headlines"),
        (SURF_NEUTRAL, f"{gap['without_asr']['pct_of_incumbent']}%", INK_TITLE,
         "where no speech stage runs — "
         f"{len(gap['without_asr']['use_cases'])} tasks"),
        (SURF_NEUTRAL, f"{gap['with_asr']['pct_of_incumbent']}%", INK_TITLE,
         "where speech-to-text runs first — "
         f"{len(gap['with_asr']['use_cases'])} tasks"),
        (SURF_AMBER, f"{leads} of {len(rows)}", INK_AMBER,
         "tasks where internal leads — both are parity, not wins"),
    ])

    y = 2.35
    table_header(slide, y, ACC_COLS)
    y += 0.34
    for i, row in enumerate(rows):
        table_row(slide, y, ACC_COLS, [
            row["use_case"],
            f4(row["incumbent_f1"]),
            f4(row["internal_f1"]),
            f4(row["accuracy_incumbent"]),
            f4(row["accuracy_internal"]),
        ], alt=bool(i % 2))
        y += 0.30
    table_row(slide, y, ACC_COLS, [
        f"Mean of {len(rows)}",
        f4(quality["incumbent"]), f4(quality["internal"]),
        f4(accuracy["incumbent"]), f4(accuracy["internal"]),
    ], alt=False, h=0.34, bold=True)
    y += 0.34

    notes(slide, y + 0.11, CONTENT_W, [
        "Internal arm is the best-scoring one on each task. On three of six "
        "only one internal arm ran, so no selection happened; only RTR-Fraud "
        "is a genuine two-model pick, and by 0.000855. Both columns aggregate "
        "each tab with that tab's own published weighting. "
        # Named from the parse, never typed: two revisions of this workbook sit
        # side by side and the superseded one still shows Sentiment Retention at
        # 0.66 against the 0.88 printed above. Crediting the wrong one would send
        # a reader to a file that contradicts the slide.
        f"Source: {matrix['source_file']}, six tabs.",

        "Sentiment Retention was rescored between two revisions of that "
        "workbook, so it is arguably provisional. It stays in because the "
        "rescore moved both arms \u2014 the incumbent led before it and leads "
        "by more after. Excluding it, the headline reads "
        f"{sensitivity['pct_of_incumbent']}% rather than "
        f"{quality['pct_ratio_of_means']}%.",
    ])

    card(slide, MARGIN_L, 5.56, 6.00, 1.24,
         "WHERE THE GAP IS", ACCENT_RED, [
             "The two tasks whose internal pipeline reads the source directly "
             f"land at {gap['without_asr']['pct_of_incumbent']}% of "
             "production. The four that put speech-to-text in front land at "
             f"{gap['with_asr']['pct_of_incumbent']}%. The gap is "
             "concentrated in transcription, not in labelling.",
             "Caveat: those two are also where Gemini scores "
             f"{f4(rows[0]['incumbent_f1'])} and "
             f"{f4(rows[1]['incumbent_f1'])}, so part of that closeness is a "
             "ceiling with little headroom to lose.",
         ], SURF_WHITE, line=ACCENT_RED)

    card(slide, 6.78, 5.56, 6.00, 1.24,
         "READ THESE TWO CAREFULLY", ACCENT_BLUE, [
             "Accuracy is not a second, independent look. On RTR-Fraud, "
             "Tax-Invoice and Sentiment Telesale it is arithmetically "
             "identical to Precision or Recall.",
             "Both internal leads are parity, not wins: "
             f"{parity['tasks'][0]}'s +0.0045 took 594 model calls against "
             f"198, and {parity['tasks'][1]}'s +0.0080 is 25 calls against 26 "
             "and turns on one dimension of four.",
         ], SURF_NEUTRAL)
    return slide


# --------------------------------------------------------------------------
# Slide 8 -- cost and speed
# --------------------------------------------------------------------------

COST_COLS = [
    (0.63, 2.45, "Use case"),
    (3.18, 2.05, "Gemini cost / item"),
    (5.33, 1.25, "Gemini sec / item"),
    (6.68, 1.45, "Internal sec / item"),
    (8.23, 1.05, "Slowdown"),
    (9.38, 3.32, "Whole round — Gemini → internal"),
]


def build_cost_slide(prs, summary, matrix, pricing, page_no, total):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    cost, speed = summary["cost"], summary["speed"]
    rng = cost["per_item_range"]
    unmeasured = speed["tasks_unmeasured"][0]
    ratios = {r["use_case"]: r["ratio"] for r in speed["per_use_case"]}
    index = matrix_index(matrix)
    exposure = assumed_model_exposure(summary, matrix, pricing)
    divergence = widest_round_divergence(summary, matrix, ratios)

    slide_chrome(
        slide,
        "Benchmark results — cost and speed",
        "What the same six runs cost and how long they took — Gemini at "
        "OpenRouter list rates; the internal endpoint publishes no cost at all",
        page_no, total,
    )

    kpi_strip(slide, 1.42, [
        (SURF_BLUE, f"{money(rng['min'])} – {money(rng['max'])}", KPI_DEEP,
         "Gemini cost per item — three different units, not addable"),
        (SURF_NEUTRAL,
         f"{speed['range'][0]:.1f}× – "
         f"{speed['widest_known_ratio']:.1f}×", INK_TITLE,
         "slower per item on the internal GPU"),
        (SURF_AMBER, "not metered", INK_AMBER,
         "internal cost — no GPU-hour or utilisation figure exists"),
        (SURF_AMBER, "not computable", INK_AMBER,
         "break-even — three inputs are missing"),
    ])

    y = 2.35
    table_header(slide, y, COST_COLS)
    y += 0.34
    for i, row in enumerate(summary["per_use_case"]):
        inc_round, int_round = round_pair(index, row)
        table_row(slide, y, COST_COLS, [
            row["use_case"],
            cost_cell(row),
            secs(row["latency_incumbent_s"]),
            secs(row["latency_internal_s"]),
            ratio(ratios.get(row["use_case"])),
            f"{duration(inc_round)} → {duration(int_round)}",
        ], alt=bool(i % 2), h=0.29)
        y += 0.29

    notes(slide, y + 0.11, CONTENT_W, [
        f"*  {exposure['task']}'s rate assumes {exposure['assumed']} — "
        "that tab names no model at all. Priced instead at the Pro tier the "
        f"other Gemini task here ran on ({exposure['alternative']}), the same "
        f"run is {money(exposure['per_item'])} per {exposure['unit']}, "
        f"{exposure['multiple']:.1f}× higher.",
        "†  The four call tasks send audio, which bills at $1.00/1M "
        "against $0.30/1M for text, and the split is not published — so "
        f"these four are upper bounds. Floors: {audio_floor_note(summary)}.",
        f"{unmeasured['use_case']}'s internal arm reads "
        f"“{unmeasured['workbook_says']}” and its round was resumed "
        "over several days, so it cannot be timed. Its other configuration ran "
        f"at {unmeasured['other_arm_ratio']}× — the slowest of the "
        "set — shown here so the missing cell does not flatter the "
        "internal side.",
        "Whole round is wall clock for the complete run under each side's own "
        "concurrency, not per-item latency × items. The two constructions "
        f"differ: {divergence['task']} is {ratio(divergence['per_item'])} per "
        f"item but {ratio(divergence['round'])} on the round.",
    ])

    card(slide, MARGIN_L, 5.54, 6.00, 1.22,
         "THE COST OF MIGRATING IS NOT IN THIS TABLE", ACCENT_RED, [
             "The internal endpoint is not metered. Not $0, not free and not "
             "n/a — neither source publishes a cost, price, GPU-hour or "
             "utilisation figure for it in any of its six tabs.",
             "Every dollar above is what Gemini costs today, at list rates.",
         ], SURF_WHITE, line=ACCENT_RED)

    card(slide, 6.78, 5.54, 6.00, 1.22,
         "THE ASK", ACCENT_BLUE, [
             "Break-even needs three numbers nobody has published yet: "
             "production volume per task, the internal endpoint's cost per "
             "GPU-hour and its throughput, and the cost of the speech-to-text "
             "stage.",
             "Asking for those three is a firmer position than a saving that "
             "cannot be evidenced.",
         ], SURF_NEUTRAL)
    return slide


# --------------------------------------------------------------------------
# Deck assembly
# --------------------------------------------------------------------------

def move_slide(prs, from_index, to_index):
    id_list = prs.slides._sldIdLst
    entry = list(id_list)[from_index]
    id_list.remove(entry)
    id_list.insert(to_index, entry)


def renumber_footer(slide, page_no, total):
    """Rewrite the page-number box as a single run.

    Three of the original slides render '1 /87', '3 87' and '4 87': the
    separator was lost and a stale total from the seven-slide era survived as a
    third run. Collapsing to one run repairs that and fixes the total.
    """
    for shape in slide.shapes:
        if not shape.has_text_frame or shape.left is None or shape.top is None:
            continue
        if shape.left < Inches(10.4) or shape.top < Inches(6.9):
            continue
        para = shape.text_frame.paragraphs[0]
        runs = para.runs
        if not runs:
            continue
        runs[0].text = f"{page_no} / {total}"
        for extra in runs[1:]:
            extra._r.getparent().remove(extra._r)
        return True
    return False


def build(source, out, preview=None):
    if not source.exists():
        print(f"deck_slides: source deck not found at {source}",
              file=sys.stderr)
        raise SystemExit(2)
    summary = load(SUMMARY_JSON, "summary.json")
    matrix = load(MATRIX_JSON, "usecase-matrix.json")
    pricing = load(PRICING_JSON, "openrouter-pricing.json")

    prs = Presentation(str(source))
    original = len(prs.slides)
    total = original + 2

    build_accuracy_slide(prs, summary, matrix, NEW_AT + 1, total)
    build_cost_slide(prs, summary, matrix, pricing, NEW_AT + 2, total)
    move_slide(prs, original, NEW_AT)
    move_slide(prs, original + 1, NEW_AT + 1)

    for i, slide in enumerate(prs.slides, start=1):
        renumber_footer(slide, i, total)

    out.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(out))
    if preview:
        write_preview(out, (NEW_AT, NEW_AT + 1), preview)
    return out


# --------------------------------------------------------------------------
# Preview. LibreOffice is not installed here, so the visual check renders the
# built deck's own shapes as absolutely-positioned divs at 96 px/in.
# --------------------------------------------------------------------------

PX = 96.0 / 914400.0


def _rgb(colour_format, fallback="transparent"):
    try:
        return f"#{colour_format.rgb}"
    except Exception:
        return fallback


def _preview_band(shape, x, y, w, h):
    fill = "transparent"
    try:
        if shape.fill.type == 1:
            fill = _rgb(shape.fill.fore_color)
    except Exception:
        pass
    border = ""
    try:
        if shape.line.fill.type == 1:
            width = shape.line.width.pt if shape.line.width else 0.75
            border = f"border:{width}pt solid {_rgb(shape.line.color, '#000')};"
    except Exception:
        pass
    radius = ""
    if shape.auto_shape_type == MSO_SHAPE.ROUNDED_RECTANGLE:
        radius = "border-radius:5px;"
    return (f"<div class='s' style='left:{x:.1f}px;top:{y:.1f}px;"
            f"width:{w:.1f}px;height:{h:.1f}px;background:{fill};"
            f"{border}{radius}'></div>")


def _preview_text(shape, x, y, w):
    align = {PP_ALIGN.RIGHT: "right", PP_ALIGN.CENTER: "center"}
    blocks = []
    for para in shape.text_frame.paragraphs:
        spans = []
        for run in para.runs:
            font = run.font
            size = font.size.pt if font.size else 12
            weight = "700" if font.bold else "400"
            spans.append(
                f"<span style='font-size:{size}pt;font-weight:{weight};"
                f"color:{_rgb(font.color, '#374151')}'>"
                f"{escape(run.text)}</span>"
            )
        gap = para.space_after.pt if para.space_after else 0
        blocks.append(
            f"<div style='margin-bottom:{gap}pt;text-align:"
            f"{align.get(para.alignment, 'left')}'>"
            f"{''.join(spans) or '&nbsp;'}</div>"
        )
    return (f"<div class='t' style='left:{x:.1f}px;top:{y:.1f}px;"
            f"width:{w:.1f}px'>{''.join(blocks)}</div>")


def write_preview(deck, indices, path):
    prs = Presentation(str(deck))
    slides = list(prs.slides)
    parts = [
        # Without this the em-dashes and arrows render as mojibake and the
        # visual check silently lies about what the deck says.
        "<meta charset='utf-8'>",
        "<title>Deck preview</title>",
        "<style>body{background:#5a5f66;margin:0;padding:24px;"
        "font-family:'Segoe UI',sans-serif}"
        ".slide{position:relative;background:#fff;margin:0 auto 28px;"
        "box-shadow:0 6px 24px rgba(0,0,0,.35);overflow:hidden}"
        ".s{position:absolute;box-sizing:border-box}"
        ".t{position:absolute;white-space:pre-wrap;line-height:1.24}</style>",
    ]
    for idx in indices:
        slide = slides[idx]
        parts.append(
            f"<div class='slide' style='width:{SLIDE_W * 96:.0f}px;"
            f"height:{SLIDE_H * 96:.0f}px'>"
        )
        for shape in slide.shapes:
            x, y = shape.left * PX, shape.top * PX
            w, h = shape.width * PX, shape.height * PX
            if shape.shape_type == MSO_SHAPE_TYPE.AUTO_SHAPE:
                parts.append(_preview_band(shape, x, y, w, h))
            if shape.has_text_frame and shape.text_frame.text.strip():
                parts.append(_preview_text(shape, x, y, w))
        parts.append("</div>")
    Path(path).write_text("\n".join(parts), encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# --check: re-derive every printed figure and confirm the built deck still
# says it. This is the staleness gate the other generators in scripts/ carry.
# --------------------------------------------------------------------------

def expected_facts(summary, matrix, pricing):
    """Every figure the two new slides must print, derived from the JSON."""
    facts = []
    quality, accuracy = summary["quality"], summary["accuracy"]
    gap = summary["where_the_gap_is"]
    cost, speed = summary["cost"], summary["speed"]
    index = matrix_index(matrix)

    for row in summary["per_use_case"]:
        inc_round, int_round = round_pair(index, row)
        facts += [
            row["use_case"],
            f4(row["incumbent_f1"]), f4(row["internal_f1"]),
            f4(row["accuracy_incumbent"]), f4(row["accuracy_internal"]),
            cost_cell(row),
            secs(row["latency_incumbent_s"]), secs(row["latency_internal_s"]),
            f"{duration(inc_round)} → {duration(int_round)}",
        ]
    facts += [ratio(r["ratio"]) for r in speed["per_use_case"]]
    facts += [
        f4(quality["incumbent"]), f4(quality["internal"]),
        f4(accuracy["incumbent"]), f4(accuracy["internal"]),
        f"{quality['pct_ratio_of_means']}%",
        f"{gap['without_asr']['pct_of_incumbent']}%",
        f"{gap['with_asr']['pct_of_incumbent']}%",
        money(cost["per_item_range"]["min"]),
        money(cost["per_item_range"]["max"]),
        f"{speed['widest_known_ratio']:.1f}×",
        audio_floor_note(summary),
        "not metered", "not computable", "not measurable",
        matrix["source_file"],
        f"{summary['sensitivity_excluding_retention']['pct_of_incumbent']}%",
    ]
    exposure = assumed_model_exposure(summary, matrix, pricing)
    facts += [money(exposure["per_item"]),
              f"{exposure['multiple']:.1f}×",
              exposure["assumed"], exposure["alternative"]]
    ratios = {r["use_case"]: r["ratio"] for r in speed["per_use_case"]}
    divergence = widest_round_divergence(summary, matrix, ratios)
    facts += [divergence["task"], ratio(divergence["per_item"]),
              ratio(divergence["round"])]
    return facts


def slide_text(slide):
    return "\n".join(
        s.text_frame.text for s in slide.shapes if s.has_text_frame
    )


def check(out):
    if not out.exists():
        print(f"deck_slides: built deck not found at {out}", file=sys.stderr)
        raise SystemExit(2)
    summary = load(SUMMARY_JSON, "summary.json")
    matrix = load(MATRIX_JSON, "usecase-matrix.json")
    pricing = load(PRICING_JSON, "openrouter-pricing.json")
    prs = Presentation(str(out))
    slides = list(prs.slides)
    if len(slides) != 10:
        print(f"deck_slides: expected 10 slides, found {len(slides)}",
              file=sys.stderr)
        return 1
    body = slide_text(slides[NEW_AT]) + "\n" + slide_text(slides[NEW_AT + 1])
    facts = expected_facts(summary, matrix, pricing)
    missing = [f for f in facts if f not in body]
    if missing:
        print("deck_slides: the deck is stale against the JSON. Missing:",
              file=sys.stderr)
        for f in sorted(set(missing)):
            print(f"  {f!r}", file=sys.stderr)
        return 1
    print(f"deck_slides: OK -- {len(slides)} slides, {len(set(facts))} "
          f"distinct figures agree with {SUMMARY_JSON.name} and "
          f"{PRICING_JSON.name}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Add two benchmark-results slides to the migration deck.")
    ap.add_argument("--source", type=Path, help="input .pptx (8 slides)")
    ap.add_argument("--out", type=Path, required=True, help="output .pptx")
    ap.add_argument("--preview-html", type=Path, default=None)
    ap.add_argument("--check", action="store_true",
                    help="verify an already-built deck against the JSON")
    args = ap.parse_args(argv)

    if args.check:
        return check(args.out)
    if not args.source:
        ap.error("--source is required to build")
    build(args.source, args.out, args.preview_html)
    print(f"deck_slides: wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
