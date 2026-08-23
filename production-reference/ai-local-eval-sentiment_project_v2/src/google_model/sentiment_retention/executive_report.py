"""Executive report: Sentiment Retention — Gemini 2.5 Flash vs Typhoon + Qwen3.8-27b.

Rebuilds ``resources/Sentiment Retention Report.xlsx`` as a flat two-sheet scorecard in
the ``resources/photo_use_cases.xlsx`` house layout: one METRIC column plus one column per
arm, banded sections (PIPELINE SHAPE -> BUSINESS OUTCOME -> TRANSCRIPTION STAGE -> TIME
-> TOKENS -> RELIABILITY), bold single leaders on contested quality rows, grey italic
placeholders where a value was never recorded, and `-prefixed footnotes.

Every figure is copied from a published cell of the two source workbooks' Evaluation
Dashboard / Matrix Summary sheets — the one exception is the Qwen per-call latency,
elapsed / calls succeeded (user decision, 2026-08-23: the elapsed time was corrected by
the run owner and is used directly). Source blocks are located by banner text, never by
row index, so a dashboard that grows keeps parsing.
"""

import re
from dataclasses import dataclass

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

OUTPUT_PATH = "resources/Sentiment Retention Report.xlsx"
DASHBOARD = "Evaluation Dashboard"
SUMMARY_BANNER = "EVALUATION SUMMARY"
CALL_RESULT = "CALL RESULT (per class)"
REASON = "CANCELLATION REASON (per class)"
PRODUCT = "PRODUCT (per class)"
TRANSCRIPT = "TRANSCRIPT"
WEIGHTED = "Weighted-average"
MACRO = "Macro-average"
MICRO = "Micro-average"
AVERAGES = (MACRO, MICRO, WEIGHTED)
TOPICS = (("Call Result", CALL_RESULT), ("Reason", REASON), ("Product", PRODUCT))

# Column offsets in the per-class blocks: Class|Label|TP|FP|FN|TN|N|Support|Acc|P|R|F1
TP, FP, FN, TN, SUPPORT, ACC, PREC, REC, F1 = 2, 3, 4, 5, 7, 8, 9, 10, 11
# Column offsets in EVALUATION SUMMARY: Topic|Classes|N|Accuracy|Precision|Recall|F1|Note
T_ACC, T_PREC, T_REC, T_F1 = 3, 4, 5, 6

CALL_RESULT_LABELS = {
    "save": "Saved — the customer stayed",
    "churn": "Churned — the customer left",
    "unknown": "Unknown — the model could not tell",
    "undefined": "Undefined",
    "(missing)": "(missing) — rows the join left without a result label",
}
REASON_LABELS = {
    "network": "Network quality",
    "promotion related": "Promotion",
    "device promotion related": "Device promotion",
    "save cost": "Cost saving",
    "contract end": "Contract ended",
    "sale upsell problem": "Sales / upsell problem",
    "dissatisfied service": "Dissatisfied with service",
    "other": "Other",
    "post to pre": "Postpaid to prepaid",
    "customer reason": "Personal reason",
    "down sell not success": "Down-sell not successful",
    "unknown (out-of-vocabulary)": "Out-of-vocabulary reason",
}
# Published product names are kept as-is — the schema does not expand them.
PRODUCT_LABELS = {
    "Postpaid": "Postpaid",
    "TOL": "TOL",
    "TVS": "TVS",
    "unknown": "unknown",
    "unknown (out-of-vocabulary)": "Out-of-vocabulary product",
}
TOPIC_LABELS = {
    "Call Result": CALL_RESULT_LABELS,
    "Reason": REASON_LABELS,
    "Product": PRODUCT_LABELS,
}


@dataclass
class Arm:
    key: str
    path: str
    header: str
    topics: dict
    classes: dict  # topic -> {label: row}
    transcript: dict
    summary: dict
    unreported: frozenset


# ---------------------------------------------------------------------------
# Style block (photo_use_cases palette; copied per report by standing decision)
# ---------------------------------------------------------------------------
FONT = "Calibri"
HEADER_FONT = Font(name=FONT, size=9, bold=True, color="FF9A9A94")
BAND_FONT = Font(name=FONT, size=9, bold=True, color="FF6B6862")
BAND_FILL = PatternFill("solid", fgColor="FFEDE9E3")
SUB_FONT = Font(name=FONT, size=10, bold=True, color="FF3D3A34")
LABEL_FONT = Font(name=FONT, size=10, color="FF1A1A18")
LABEL_EMPH_FONT = Font(name=FONT, size=10, bold=True, color="FF1A1A18")
VALUE_FONT = Font(name=FONT, size=10, color="FF4A4A46")
WINNER_FONT = Font(name=FONT, size=10, bold=True, color="FF1A1A18")
MUTED_FONT = Font(name=FONT, size=10, italic=True, color="FF9A9A94")
GOOD_FONT = Font(name=FONT, size=10, bold=True, color="FF3F6B3A")
GOOD_FILL = PatternFill("solid", fgColor="FFDFEBDD")
BAD_FONT = Font(name=FONT, size=10, bold=True, color="FF8A3B36")
BAD_FILL = PatternFill("solid", fgColor="FFF3DBD7")
EMPH_FILL = PatternFill("solid", fgColor="FFF6F3EE")
RIGHT = Alignment(horizontal="right")

_NUMBER = re.compile(r"-?\d[\d,]*\.?\d*")


def _numeric(cell):
    """First number inside a plain value cell, or None (muted/pill cells never rank)."""
    if not isinstance(cell, str):
        return None
    match = _NUMBER.search(cell)
    return float(match.group().replace(",", "")) if match else None


def _leader(cells, best):
    """Index of the strict single leader among plain value cells, or None on any tie."""
    scores = [_numeric(c) for c in cells]
    ranked = [s for s in scores if s is not None]
    if len(ranked) < 2:
        return None
    top = max(ranked) if best == "max" else min(ranked)
    if ranked.count(top) != 1:
        return None
    return scores.index(top)


class SheetWriter:
    """Writes the flat photo_use_cases table: label column + one column per arm."""

    def __init__(self, sheet, headers):
        self.sheet = sheet
        sheet.sheet_view.showGridLines = False
        sheet.column_dimensions["A"].width = 54
        for i in range(len(headers)):
            sheet.column_dimensions[get_column_letter(2 + i)].width = 27
        self.sheet.cell(row=1, column=1, value="METRIC").font = HEADER_FONT
        for i, header in enumerate(headers):
            cell = sheet.cell(row=1, column=2 + i, value=header)
            cell.font = HEADER_FONT
            cell.alignment = RIGHT
        self.columns = len(headers)
        self.row = 2

    def band(self, title):
        for c in range(1, self.columns + 2):
            self.sheet.cell(row=self.row, column=c).fill = BAND_FILL
        self.sheet.cell(row=self.row, column=1, value=title).font = BAND_FONT
        self.row += 1

    def sub(self, label):
        self.sheet.cell(row=self.row, column=1, value=label).font = SUB_FONT
        self.row += 1

    def line(self, label, cells, best=None, emph=False):
        """One metric row. Cells: plain str, or ("muted"|"good"|"bad", str)."""
        label_cell = self.sheet.cell(row=self.row, column=1, value=label)
        label_cell.font = LABEL_EMPH_FONT if emph else LABEL_FONT
        winner = _leader(cells, best) if best else None
        pills = set()
        for i, spec in enumerate(cells):
            kind, text = spec if isinstance(spec, tuple) else ("value", spec)
            cell = self.sheet.cell(row=self.row, column=2 + i, value=text)
            cell.alignment = RIGHT
            if kind == "muted":
                cell.font = MUTED_FONT
            elif kind == "good":
                cell.font = GOOD_FONT
                cell.fill = GOOD_FILL
                pills.add(2 + i)
            elif kind == "bad":
                cell.font = BAD_FONT
                cell.fill = BAD_FILL
                pills.add(2 + i)
            else:
                cell.font = WINNER_FONT if i == winner else VALUE_FONT
        if emph:
            for c in range(1, self.columns + 2):
                if c not in pills:
                    self.sheet.cell(row=self.row, column=c).fill = EMPH_FILL
        self.row += 1

    def blank(self):
        self.row += 1

    def note(self, text):
        self.sheet.cell(row=self.row, column=1, value=f"·  {text}").font = MUTED_FONT
        self.row += 1


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------
def f4(value):
    return "n/a" if value == "n/a" else f"{value:.4f}"


def pct(value):
    return f"{value * 100:.1f}%"


def thousands(value):
    return f"{value:,.0f}"


def clock(seconds):
    total = int(round(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours} h {minutes:02d} m"
    if minutes:
        return f"{minutes} m {secs:02d} s"
    return f"{secs} s"


# ---------------------------------------------------------------------------
# Source parsing — banner scan, never row indices
# ---------------------------------------------------------------------------
def _find(rows, banner):
    for i, row in enumerate(rows):
        if row and row[0] == banner:
            return i
    raise KeyError(f"banner {banner!r} not found")


def _block(rows, banner, skip=2):
    """Rows following a banner (skip = banner + header lines) up to a fully blank row."""
    out = []
    for row in rows[_find(rows, banner) + skip:]:
        if not row or all(cell is None for cell in row):
            break
        out.append(row)
    return out


def _load_arm(key, path, header, unreported_columns):
    book = load_workbook(path, read_only=True, data_only=True)
    rows = list(book[DASHBOARD].iter_rows(values_only=True))
    topics = {row[0]: row for row in _block(rows, SUMMARY_BANNER)}
    classes = {
        topic: {row[1]: row for row in _block(rows, banner, skip=3)}
        for topic, banner in TOPICS
    }
    transcript = {row[0]: row[1] for row in _block(rows, TRANSCRIPT)}
    summary = {
        row[0]: row[1]
        for row in book["Matrix Summary"].iter_rows(values_only=True)
        if row and row[0] is not None
    }
    matrix = book["Matrix"].iter_rows(values_only=True)
    index = {name: i for i, name in enumerate(next(matrix)) if name}
    populated = set()
    for row in matrix:
        for name in unreported_columns:
            if name in index and row[index[name]] is not None:
                populated.add(name)
    book.close()
    unreported = frozenset(c for c in unreported_columns if c in index) - populated
    return Arm(key, path, header, topics, classes, transcript, summary, unreported)


# ---------------------------------------------------------------------------
# Report sections
# ---------------------------------------------------------------------------
def _tokens_cell(arm, column, text):
    if column in arm.unreported:
        return ("muted", "not recorded")
    return text


def _four_metrics(out, arms, topic):
    """The weighted four-metric block plus Macro F1, photo_use_cases style."""
    rows = [arm.topics[topic] for arm in arms]
    out.line("F1-score, weighted", [f4(r[T_F1]) for r in rows], best="max", emph=True)
    out.line("Precision, weighted", [f4(r[T_PREC]) for r in rows], best="max")
    out.line("Recall, weighted", [f4(r[T_REC]) for r in rows], best="max")
    out.line("Accuracy, weighted", [f4(r[T_ACC]) for r in rows], best="max", emph=True)
    macro = [arm.classes[topic][MACRO] for arm in arms]
    out.line(
        "Macro F1  —  every class counts the same",
        [f4(r[F1]) for r in macro],
        best="max",
    )


def _class_block(out, arms, topic, name, total):
    labels = TOPIC_LABELS[topic]
    rows = [arm.classes[topic][name] for arm in arms]
    support = int(rows[0][SUPPORT])
    if support:
        calls = "1 call" if support == 1 else f"{support} calls"
        out.sub(f"{labels[name]}   —   {calls} of {total}")
    else:
        out.sub(f"{labels[name]}   —   the human grader never used it")
    if all(row[F1] == "n/a" for row in rows):
        out.line("F1-score", [("muted", "n/a — never used by either side")] * len(arms))
        return
    rank = "max" if support else None
    out.line("F1-score", [f4(r[F1]) for r in rows], best=rank, emph=True)
    out.line("Precision", [f4(r[PREC]) for r in rows], best=rank)
    out.line("Recall", [f4(r[REC]) for r in rows], best=rank)
    out.line("Accuracy", [f4(r[ACC]) for r in rows], best=rank)
    counts = [" · ".join(str(int(r[i])) for i in (TP, FP, FN, TN)) for r in rows]
    out.line("Confusion  —  TP · FP · FN · TN", counts)


def _transcription(out, arms):
    out.band("TRANSCRIPTION STAGE   —   no winner marked: different speech engines")
    tx = [arm.transcript for arm in arms]
    out.line("Typical call — median CER  (lower is better)", [f4(t["Median CER"]) for t in tx],
             emph=True)
    out.line("Average across all calls — mean CER", [f4(t["Mean CER"]) for t in tx])
    out.line("Worst single call — CER", [f4(t["Worst CER (highest)"]) for t in tx])
    out.line("Calls at or below 0.20 CER", [pct(t["CER pass rate (<= 0.20)"]) for t in tx])
    out.line("Same conversation? — mean cosine", [f4(t["Mean cosine"]) for t in tx])


def _footnotes(out, notes):
    out.blank()
    for text in notes:
        out.note(text)


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------
def build():
    gemini = _load_arm(
        "gemini",
        "resources/sentiment-retention-gemini-2.5-flash.xlsx",
        "GEMINI 2.5 FLASH",
        ("Thoughts Tokens", "Cached Tokens"),
    )
    qwen = _load_arm(
        "qwen",
        "resources/sentiment-retention-qwen3.8-27b-fp8.xlsx",
        "TYPHOON + QWEN3.8-27B",
        ("Analysis Reasoning Tokens", "Analysis Cached Tokens"),
    )
    arms = (gemini, qwen)

    for arm in arms:
        for topic, _ in TOPICS:
            missing = [a for a in AVERAGES if a not in arm.classes[topic]]
            if missing:
                raise ValueError(f"{arm.path}: {topic} lacks average rows {missing}")
    for topic, _ in TOPICS:
        if list(gemini.classes[topic]) != list(qwen.classes[topic]):
            raise ValueError(f"the two workbooks scored different {topic} classes")
    calls = int(gemini.topics["Call Result"][2])
    if calls != int(qwen.topics["Call Result"][2]):
        raise ValueError("the two workbooks scored different call counts")

    book = Workbook()
    out = SheetWriter(book.active, [arm.header for arm in arms])
    book.active.title = "Scorecard"

    out.band("PIPELINE SHAPE")
    out.line("Transcriber", [("muted", "none — direct audio"), "Typhoon Whisper large-v3"])
    out.line("Labeller", [("muted", "(same model)"), "Qwen3.8-27b-fp8"])
    out.line("Model calls per call", ["1", "2"])
    out.line("Runtime", [("muted", "Vertex AI batch (global)"), ("muted", "internal GPU endpoint")])
    out.line("Calls scored — common set", [str(calls)] * 2, emph=True)
    out.line("Calls this arm ran", [thousands(arm.summary["Source Files"]) for arm in arms])

    out.band("BUSINESS OUTCOME   —   PRIMARY")
    out.sub("Call result — did the customer stay or leave?   (4 classes, one answer per call)")
    _four_metrics(out, arms, "Call Result")
    out.sub("Cancellation reason — why they wanted to leave   (11 reasons, scored as a set)")
    _four_metrics(out, arms, "Reason")
    out.sub("Product — what the call was about   (4 products, scored as a set per call)")
    _four_metrics(out, arms, "Product")

    _transcription(out, arms)

    out.band("TIME   —   no winner marked: cloud batch vs internal GPU loop")
    good = [int(arm.summary["Succeeded"]) for arm in arms]
    wall = (gemini.summary["Batch Wall Seconds"], qwen.summary["Files Elapsed (ms)"] / 1000)
    out.line("Whole run, wall clock", [clock(w) for w in wall])
    # Gemini per-call is the published batch figure (queue excluded); the Qwen figure is
    # elapsed / calls succeeded — the elapsed time was corrected by the run owner
    # (2026-08-23) and is used directly.
    out.line("Per call, seconds",
             [f"{gemini.summary['Seconds / File']:.2f}", f"{wall[1] / good[1]:.1f}"])
    out.line(
        "Queue before the batch ran, seconds",
        [f"{gemini.summary['Queue Seconds']:.1f}", ("muted", "n/a — internal endpoint")],
    )

    out.band("TOKENS   —   whole run; no winner marked: the pipelines differ")
    q = qwen.summary
    out.line("Input tokens, total", [
        thousands(gemini.summary["Prompt Tokens"]),
        f"{q['Label Prompt Tokens']:,.0f} label  +  {q['Analysis Prompt Tokens']:,.0f} analysis",
    ])
    out.line("Output tokens, total", [
        thousands(gemini.summary["Candidates Tokens"]),
        f"{q['Label Completion Tokens']:,.0f} label  +  "
        f"{q['Analysis Completion Tokens']:,.0f} analysis",
    ])
    out.line("Thinking tokens", [
        _tokens_cell(gemini, "Thoughts Tokens", thousands(gemini.summary["Thoughts Tokens"])),
        _tokens_cell(qwen, "Analysis Reasoning Tokens", "0"),
    ])
    out.line("Cached input tokens", [
        _tokens_cell(gemini, "Cached Tokens", thousands(gemini.summary["Cached Tokens"])),
        _tokens_cell(qwen, "Analysis Cached Tokens", "0"),
    ])
    out.line("Total tokens, whole run",
             [thousands(arm.summary["Total Tokens"]) for arm in arms], emph=True)
    out.line("Average per call",
             [thousands(arm.summary["Avg Total Tokens / File"]) for arm in arms])

    out.band("RELIABILITY")
    ran = [int(arm.summary["Source Files"]) for arm in arms]
    out.line("Calls succeeded", [f"{g}/{r}" for g, r in zip(good, ran)])
    out.line("Failed or errored calls", [
        (("good" if r - g == 0 else "bad"), f"{r - g}/{r}") for g, r in zip(good, ran)
    ], emph=True)
    out.line("Line parse errors", [
        thousands(gemini.summary["Line Parse Errors"]), ("muted", "not recorded"),
    ])
    resumed = int(qwen.summary.get("Resumed Files", 0))
    out.line("Resumed from an earlier checkpoint", [
        ("muted", "no — fresh run"), ("muted", f"yes — {resumed} of {ran[1]} calls reused"),
    ])

    _footnotes(out, [
        "Both arms scored the same 97 ground-truth calls, so every BUSINESS OUTCOME row is "
        "a fair head-to-head; bold marks the single leader. Scores here are far below the "
        "other sentiment projects because many result rows did not line up with the ground "
        "truth (see the next note) — read them with that in mind.",
        "The dashboards publish the join as-is: Gemini compared '144 of 97 ground-truth "
        "rows (36 unmatched in ground truth, 47 unmatched in result)'; Qwen '142 of 97 "
        "(39 unmatched in ground truth, 45 unmatched in result)'. 47 and 45 row(s) were "
        "excluded from Call result as out-of-vocabulary, per the published summary notes.",
        "Precision / Recall / Accuracy here are the weighted averages over classes, from "
        "the same dashboard rows as the F1 beside them. Nothing is recomputed in this "
        "report except the per-call seconds noted below.",
        "TRANSCRIPTION carries no winner: Gemini transcribes natively from audio while "
        "Qwen reads a Typhoon Whisper transcript, so the engines differ. One Qwen call at "
        "CER 44.69 drags its mean to 1.2930 — the median (0.2039) is the typical-call "
        "figure.",
        "TIME and TOKENS compare a cloud batch service with an internal GPU loop — "
        "infrastructure, not model quality — so no winner is marked. Gemini's whole-run "
        "clock includes 107.8 s of queue; its per-call figure is the published batch "
        "rate. The Qwen per-call figure is elapsed / 96 succeeded calls — its elapsed "
        "time was corrected by the run owner (2026-08-23) and is used directly.",
        "The Qwen run resumed from a checkpoint (94 of 97 calls reused); its one failed "
        "call hit the endpoint's stream limit (stream exceeded 900s).",
        "'not recorded' means the run never wrote that column — it is not a measured zero.",
    ])

    detail = SheetWriter(book.create_sheet("Class detail"), [arm.header for arm in arms])
    detail.band(f"DID THE CALL SAVE OR CHURN   —   per class, {calls} calls")
    for name in gemini.classes["Call Result"]:
        if name in AVERAGES:
            continue
        _class_block(detail, arms, "Call Result", name, calls)
    detail.band("WHY THEY WANTED TO LEAVE   —   per reason, scored as a set")
    for name in gemini.classes["Reason"]:
        if name in AVERAGES:
            continue
        _class_block(detail, arms, "Reason", name, calls)
    detail.band(f"WHAT PRODUCT THE CALL WAS ABOUT   —   per product, {calls} calls")
    for name in gemini.classes["Product"]:
        if name in AVERAGES:
            continue
        _class_block(detail, arms, "Product", name, calls)
    _footnotes(detail, [
        "A class the human never used cannot be won: its Support is 0, its Recall is 0 by "
        "definition, and no leader is marked on the block.",
        "Confusion counts are the four cells every rate in the block is computed from: "
        "TP found · FP over-called · FN missed · TN correctly left alone.",
        "The (missing) row counts ground-truth rows the join left without a result label "
        "(36 Gemini / 39 Qwen — the published 'unmatched in ground truth' counts); the "
        "dashboard scores it as its own class so the mismatch stays visible.",
        "Blocks with no bold and a 1.0000 tie mean both models got every call right — "
        "or the class is too rare for the difference to mean anything (check the call count).",
    ])

    book.save(OUTPUT_PATH)
    return OUTPUT_PATH


if __name__ == "__main__":
    print(build())
