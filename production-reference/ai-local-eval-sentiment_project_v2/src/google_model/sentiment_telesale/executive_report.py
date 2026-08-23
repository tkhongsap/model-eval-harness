"""Executive report: Sentiment Telesale — Gemini 2.5 Flash vs Typhoon + Qwen3.8-27b.

Rebuilds ``resources/Sentiment Telesale Report.xlsx`` as a flat two-sheet scorecard in
the ``resources/photo_use_cases.xlsx`` house layout: one METRIC column plus one column
per arm, banded sections (PIPELINE SHAPE -> BUSINESS OUTCOME -> TRANSCRIPTION STAGE ->
TIME -> TOKENS -> RELIABILITY), bold single leaders on contested quality rows, grey
italic placeholders where a value was never recorded, and `-prefixed footnotes.

Every figure is copied from a published cell of the two source workbooks' Confusion
Matrix Dashboard / Matrix Summary sheets - nothing is recomputed here. The corpus trap
this report must not bury: the humans used one label on every call for 25 of the 39
criteria, so a constant answer scores 1.0000 there; every quality block carries its
non-discriminating count on its face.
"""

import re
from dataclasses import dataclass

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

OUTPUT_PATH = "resources/Sentiment Telesale Report.xlsx"
DASHBOARD = "Confusion Matrix Dashboard"
COVERAGE = "GROUND-TRUTH COVERAGE"
OVERALL = "OVERALL (by category)"
BY_SUB = "BY SUB-CATEGORY"
TRANSCRIPT = "TRANSCRIPT"
WEIGHTED = "Weighted-average"
MACRO = "Macro-average"
MICRO = "Micro-average"
AVERAGES = (MACRO, MICRO, WEIGHTED)
CATEGORIES = (
    "Operations & professionalism",
    "Sales effectiveness",
    "Customer experience",
    "Compliance",
)

# OVERALL rows: Category|Average|Criteria|Rows|Accuracy|Precision|Recall|F1|NonDisc|Excluded
O_CRIT, O_ROWS, O_ACC, O_PREC, O_REC, O_F1, O_CONST = 2, 3, 4, 5, 6, 7, 8
# BY SUB-CATEGORY rows: Category|Sub|Average|Criteria|Rows|Acc|Prec|Rec|F1|NonDisc|Excluded
S_CRIT, S_ACC, S_PREC, S_REC, S_F1, S_CONST = 3, 5, 6, 7, 8, 9
# COVERAGE rows: Category|Sub-category|Criteria|Cells|Violations|N/A cells|Discriminating|Note
C_CRIT, C_CELLS, C_VIOL, C_NA, C_DISC = 2, 3, 4, 5, 6


@dataclass
class Arm:
    key: str
    path: str
    header: str
    coverage: list
    overall: dict  # (category, average) -> row
    subs: dict  # (category, sub, average) -> row
    transcript: dict
    summary: dict
    unreported: frozenset
    failures: dict  # error text -> count


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


def constant_caption(total, constant):
    """``15 criteria — 8 hold one label on every call`` (grammar-safe)."""
    total, constant = int(total), int(constant)
    if not constant:
        return f"{total} criteria — all of them can discriminate"
    verb = "holds" if constant == 1 else "hold"
    return f"{total} criteria — {constant} {verb} one label on every call"


# ---------------------------------------------------------------------------
# Source parsing — banner scan, never row indices
# ---------------------------------------------------------------------------
def _find(rows, banner):
    for i, row in enumerate(rows):
        if row and row[0] == banner:
            return i
    raise KeyError(f"banner {banner!r} not found")


def _block(rows, banner, skip=2):
    """Rows following a banner (skip = banner + caption/header lines) to a blank row."""
    out = []
    for row in rows[_find(rows, banner) + skip:]:
        if not row or all(cell is None for cell in row):
            break
        out.append(row)
    return out


def _load_arm(key, path, header, unreported_columns):
    book = load_workbook(path, read_only=True, data_only=True)
    rows = list(book[DASHBOARD].iter_rows(values_only=True))
    coverage = _block(rows, COVERAGE, skip=3)
    overall = {(row[0], row[1]): row for row in _block(rows, OVERALL, skip=3)
               if row[1] in AVERAGES}
    subs = {(row[0], row[1], row[2]): row for row in _block(rows, BY_SUB, skip=3)}
    transcript = {row[0]: row[1] for row in _block(rows, TRANSCRIPT)}
    summary = {
        row[0]: row[1]
        for row in book["Matrix Summary"].iter_rows(values_only=True)
        if row and row[0] is not None
    }
    matrix = book["Matrix"].iter_rows(values_only=True)
    index = {name: i for i, name in enumerate(next(matrix)) if name}
    populated = set()
    failures = {}
    for row in matrix:
        for name in unreported_columns:
            if name in index and row[index[name]] is not None:
                populated.add(name)
        if row[index["Status"]] not in (None, "Success"):
            error = str(row[index["Error Type"]])
            failures[error] = failures.get(error, 0) + 1
    book.close()
    unreported = frozenset(c for c in unreported_columns if c in index) - populated
    return Arm(key, path, header, coverage, overall, subs, transcript, summary,
               unreported, failures)


# ---------------------------------------------------------------------------
# Report sections
# ---------------------------------------------------------------------------
def _tokens_cell(arm, column, text):
    if column in arm.unreported:
        return ("muted", "not recorded")
    return text


def _four_metrics(out, arms, category):
    """The weighted four-metric block plus Macro F1 for one category."""
    rows = [arm.overall[(category, WEIGHTED)] for arm in arms]
    out.line("F1-score, weighted", [f4(r[O_F1]) for r in rows], best="max", emph=True)
    out.line("Precision, weighted", [f4(r[O_PREC]) for r in rows], best="max")
    out.line("Recall, weighted", [f4(r[O_REC]) for r in rows], best="max")
    out.line("Accuracy", [f4(r[O_ACC]) for r in rows], best="max", emph=True)
    macro = [arm.overall[(category, MACRO)] for arm in arms]
    out.line(
        "Macro F1  —  every criterion counts the same",
        [f4(r[O_F1]) for r in macro],
        best="max",
    )


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
        "resources/sentiment-telesale-gemini-2.5-flash.xlsx",
        "GEMINI 2.5 FLASH",
        ("Thoughts Tokens", "Cached Tokens"),
    )
    qwen = _load_arm(
        "qwen",
        "resources/sentiment-telesale-qwen3.8-27b-fp8.xlsx",
        "TYPHOON + QWEN3.8-27B",
        ("Analysis Reasoning Tokens", "Analysis Cached Tokens"),
    )
    arms = (gemini, qwen)

    # The coverage block describes the corpus, not the run — it must be identical.
    if gemini.coverage != qwen.coverage:
        raise ValueError("the two workbooks disagree about the ground-truth coverage block")
    for arm in arms:
        for category in CATEGORIES:
            for average in AVERAGES:
                if (category, average) not in arm.overall:
                    raise ValueError(f"{arm.path}: missing {category} / {average}")
    total = next(row for row in gemini.coverage if row[0] == "TOTAL")

    book = Workbook()
    out = SheetWriter(book.active, [arm.header for arm in arms])
    book.active.title = "Scorecard"

    out.band("PIPELINE SHAPE")
    out.line("Transcriber", [("muted", "none — direct audio"), "Typhoon Whisper large-v3"])
    out.line("Labeller", [("muted", "(same model)"), "Qwen3.8-27b-fp8"])
    out.line("Model calls per call", ["1", "2"])
    out.line("Runtime", [("muted", "Vertex AI batch (global)"), ("muted", "internal GPU endpoint")])
    scored = [int(arm.overall[(CATEGORIES[0], WEIGHTED)][O_ROWS]) for arm in arms]
    out.line("Calls scored — as run", [str(s) for s in scored], emph=True)
    out.line("Calls this arm ran", [thousands(arm.summary["Source Files"]) for arm in arms])

    out.band("BUSINESS OUTCOME   —   PRIMARY   —   read the criteria counts first")
    for category in CATEGORIES:
        overall = gemini.overall[(category, WEIGHTED)]
        out.sub(f"{category}   —   {constant_caption(overall[O_CRIT], overall[O_CONST])}")
        _four_metrics(out, arms, category)

    _transcription(out, arms)

    out.band("TIME   —   no winner marked: cloud batch vs internal GPU loop")
    wall = (gemini.summary["Batch Wall Seconds"], qwen.summary["Files Elapsed (ms)"] / 1000)
    out.line("Whole run, wall clock", [clock(w) for w in wall])
    # Gemini per-call is the published batch figure (queue excluded). The Qwen figure is
    # elapsed / 25 returned calls — the failed call burned its full 900 s stream timeout —
    # confirmed by the run owner on 2026-08-22.
    returned = int(qwen.summary["Succeeded"])
    out.line("Per call, seconds",
             [f"{gemini.summary['Seconds / File']:.2f}", f"{wall[1] / returned:.1f}"])
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
    good = [int(arm.summary["Succeeded"]) for arm in arms]
    out.line("Calls succeeded", [f"{g}/{r}" for g, r in zip(good, ran)])
    out.line("Failed or errored calls", [
        (("good" if r - g == 0 else "bad"), f"{r - g}/{r}") for g, r in zip(good, ran)
    ], emph=True)
    out.line("What failed", [
        ("muted", "—") if not arm.failures else
        "  ·  ".join(f"{error} ×{count}" for error, count in sorted(arm.failures.items()))
        for arm in arms
    ])
    resumed = int(qwen.summary.get("Resumed Files", 0))
    out.line("Resumed from an earlier checkpoint", [
        ("muted", "no — fresh run"), ("muted", f"yes — {resumed} of {ran[1]} calls reused"),
    ])

    _footnotes(out, [
        f"READ THIS FIRST: the humans used one label on every call for "
        f"{int(total[C_CRIT]) - int(total[C_DISC])} of the {int(total[C_CRIT])} criteria — a "
        f"model that ignores the audio and answers a constant scores 1.0000 on those. Only "
        f"{int(total[C_DISC])} criteria can discriminate, and the corpus holds just "
        f"{int(total[C_VIOL])} violation cells in {int(total[C_CELLS]):,}. These figures "
        "mostly measure the ground truth, not the models.",
        "That floor is why weighted F1 sits near 0.95 everywhere: weighted follows the many "
        "constant criteria, macro weights each criterion equally and drops to ~0.7. The gap "
        "between the two rows IS the floor.",
        "As-run counting: Gemini answered all 26 calls, Qwen returned 25 — its lost call hit "
        "the 900 s stream timeout, so its rates cover one call fewer. Bold still marks the "
        "row leader; the one-call gap is on the face of PIPELINE SHAPE.",
        "TRANSCRIPTION carries no winner: different speech engines, and one Gemini call at "
        "CER 11.74 (the model wrote ~12× the human's text) drags its mean to 0.5812 while "
        "its median (0.1079) beats Qwen's (0.1632). '1 - mean CER' would reverse the verdict.",
        "TIME and TOKENS compare a cloud batch service with an internal GPU loop — "
        "infrastructure, not model quality — so no winner is marked. The Qwen per-call "
        "figure divides by the 25 calls that returned (confirmed by the run owner).",
        "'not recorded' means the run never wrote that column — it is not a measured zero. "
        "Gemini's thinking tokens are blank on every call of this run.",
    ])

    detail = SheetWriter(book.create_sheet("Sub-category detail"), [arm.header for arm in arms])
    coverage = {row[1]: row for row in gemini.coverage if row[0] != "TOTAL"}
    for category in CATEGORIES:
        detail.band(category.upper() + "   —   per sub-category")
        names = [key[1] for key in gemini.subs
                 if key[0] == category and key[2] == WEIGHTED]
        for name in names:
            fact = coverage[name]
            violations = int(fact[C_VIOL])
            found = "no violation in ground truth" if not violations else (
                "1 violation in ground truth" if violations == 1
                else f"{violations} violations in ground truth")
            caption = constant_caption(fact[C_CRIT], int(fact[C_CRIT]) - int(fact[C_DISC]))
            detail.sub(f"{name}   —   {caption}   ·   {found}")
            rank = "max" if int(fact[C_DISC]) else None
            weighted = [arm.subs[(category, name, WEIGHTED)] for arm in arms]
            macro = [arm.subs[(category, name, MACRO)] for arm in arms]
            detail.line("F1-score, weighted", [f4(r[S_F1]) for r in weighted],
                        best=rank, emph=True)
            detail.line("Macro F1", [f4(r[S_F1]) for r in macro], best=rank)
            detail.line("Accuracy", [f4(r[S_ACC]) for r in weighted], best=rank)
    _footnotes(detail, [
        "Blocks where every criterion holds one label on every call ('0 can discriminate') "
        "carry no bold: their scores are the constant-answer floor, not a result.",
        "The violation counts describe the human ground truth over all 26 calls — they are "
        "corpus facts, identical whichever model ran.",
        "Macro F1 under weighted F1 is the lazy-model detector: a model that answers the "
        "majority label everywhere keeps weighted high and collapses macro.",
    ])

    book.save(OUTPUT_PATH)
    return OUTPUT_PATH


if __name__ == "__main__":
    print(build())
