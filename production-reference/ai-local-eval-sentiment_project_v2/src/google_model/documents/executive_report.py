"""Executive report: Document Extraction — four models over the same 147 receipt pages.

Rebuilds ``resources/Document Extraction Report.xlsx`` as a flat two-sheet scorecard in
the ``resources/photo_use_cases.xlsx`` house layout: one METRIC column plus one column
per arm, banded sections (PIPELINE SHAPE -> BUSINESS OUTCOME -> EXTRACTION QUALITY ->
TIME -> TOKENS -> RELIABILITY), bold single leaders on contested quality rows, grey
italic placeholders where a value was never recorded, and `-prefixed footnotes.

Two caveats this report must keep on its face:
- On this scoring method Precision equals Accuracy and Recall is 1.0000 by construction
  (the dashboards' own legend) - Accuracy is the number that carries information.
- Gemma failed 19 of 147 pages and read fewer line items, so its rates cover its own
  2,573-row subset, not the full 3,634 - stated in PIPELINE SHAPE, not hidden.

Every figure is copied from a published cell of the source workbooks' Evaluation
Dashboard / Matrix Summary sheets - nothing is recomputed here. The only parsing beyond
cell reads is lifting the published "mean coverage / mean CER / format pass" figures out
of the OVERALL note strings they are printed in.
"""

import re
from dataclasses import dataclass

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

OUTPUT_PATH = "resources/Document Extraction Report.xlsx"
DASHBOARD = "Evaluation Dashboard"
COMPARISON = "COMPARISON"
FAMILIES = "OVERALL (by family)"
EXTRACTION = "EXTRACTION (per column)"
CER = "CER (per column)"
FORMAT = "FORMAT COMPLIANCE (prediction only)"

# OVERALL (by family) rows: Family|Columns|Rows|Accuracy|F1|Note
FAM_ROWS, FAM_ACC, FAM_F1, FAM_NOTE = 2, 3, 4, 5
# EXTRACTION rows: Column|N|TP|FP|FN|TN|Accuracy|Precision|Recall|F1|GT filled|Coverage|Rule
E_ACC = 6
# CER rows: Column|Scored|Excluded|Mean|Median|Best|Worst|Pass rate|Mean char accuracy
C_MEAN, C_MEDIAN = 3, 4
# FORMAT rows: Check|Column|Checked|Passed|Failed|Pass rate
F_PASS_RATE = 5

_COMPARED = re.compile(
    r"^([\d,]+) of ([\d,]+) ground-truth rows \(([\d,]+) unmatched in ground truth, "
    r"([\d,]+) unmatched in result\)"
)
_COVERAGE = re.compile(r"mean coverage ([\d.]+)")
_MEAN_CER = re.compile(r"mean CER ([\d.]+) over")
_FORMAT_PASS = re.compile(r"format pass ([\d.]+) over")


@dataclass
class Arm:
    key: str
    path: str
    header: str
    batch: bool
    compared: tuple  # (scored, total, unmatched_gt, unmatched_result)
    families: dict
    columns: dict
    cer: dict
    format_checks: dict
    summary: dict
    unreported: frozenset
    failures: dict


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


def _published(pattern, note, path):
    match = pattern.search(note or "")
    if not match:
        raise ValueError(f"{path}: expected {pattern.pattern!r} in note {note!r}")
    return float(match.group(1))


def _load_arm(key, path, header, batch, unreported_columns):
    book = load_workbook(path, read_only=True, data_only=True)
    rows = list(book[DASHBOARD].iter_rows(values_only=True))
    compared_text = _block(rows, COMPARISON)[0][0]
    match = _COMPARED.match(compared_text)
    if not match:
        raise ValueError(f"{path}: unrecognised COMPARISON banner {compared_text!r}")
    compared = tuple(int(group.replace(",", "")) for group in match.groups())
    families = {row[0]: row for row in _block(rows, FAMILIES)}
    columns = {row[0]: row for row in _block(rows, EXTRACTION)}
    cer = {row[0]: row for row in _block(rows, CER)}
    format_checks = {(row[0], row[1]): row for row in _block(rows, FORMAT)}
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
    return Arm(key, path, header, batch, compared, families, columns, cer, format_checks,
               summary, unreported, failures)


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
        "resources/doc-gemini-3.1-pro-preview.xlsx",
        "GEMINI 3.1 PRO",
        batch=True,
        unreported_columns=("Thoughts Tokens", "Cached Tokens"),
    )
    qwen = _load_arm(
        "qwen",
        "resources/doc-qwen3.8-27b-fp8.xlsx",
        "QWEN3.8-27B-FP8",
        batch=False,
        unreported_columns=(),
    )
    tuned = _load_arm(
        "tuned",
        "resources/doc-qwen3.8-27b-fp8 (Turning).xlsx",
        "QWEN3.8-27B (TUNED)",
        batch=False,
        unreported_columns=(),
    )
    gemma = _load_arm(
        "gemma",
        "resources/doc-gemma4-12b-it.xlsx",
        "GEMMA-4-12B-IT",
        batch=False,
        unreported_columns=(),
    )
    arms = (gemini, qwen, tuned, gemma)

    first = list(gemini.columns)
    for arm in arms[1:]:
        if list(arm.columns) != first:
            raise ValueError(f"{arm.path}: extraction columns differ from Gemini's")
        if list(arm.cer) != list(gemini.cer):
            raise ValueError(f"{arm.path}: CER columns differ from Gemini's")
        if list(arm.format_checks) != list(gemini.format_checks):
            raise ValueError(f"{arm.path}: format checks differ from Gemini's")
    for arm in arms:
        for family in ("Document Classification", "Document Extraction",
                       "Data Extraction Quality"):
            if family not in arm.families:
                raise ValueError(f"{arm.path}: missing family row {family!r}")

    book = Workbook()
    out = SheetWriter(book.active, [arm.header for arm in arms])
    book.active.title = "Scorecard"

    def per_arm(batch_value, internal_value):
        return [batch_value(a) if a.batch else internal_value(a) for a in arms]

    out.band("PIPELINE SHAPE")
    out.line("Model", [str(arm.summary["Model"]) for arm in arms])
    out.line("Input", ["147 receipt pages from 32 source files"] * len(arms))
    out.line("Runtime", per_arm(
        lambda a: ("muted", "Vertex AI batch (global)"),
        lambda a: ("muted", "internal GPU endpoint"),
    ))
    out.line("Line items scored — each arm against the same ground truth",
             [thousands(arm.compared[0]) for arm in arms], emph=True)
    out.line("Line items left unmatched", [
        "0" if not (arm.compared[2] or arm.compared[3])
        else f"{arm.compared[2]:,} in ground truth  ·  {arm.compared[3]:,} in result"
        for arm in arms
    ])

    out.band("BUSINESS OUTCOME   —   PRIMARY   —   Accuracy is the informative number")
    out.sub("Document classification — 1 column: which document type is this?")
    classification = [arm.families["Document Classification"] for arm in arms]
    out.line("Accuracy", [f4(r[FAM_ACC]) for r in classification], best="max", emph=True)
    out.line("F1-score", [f4(r[FAM_F1]) for r in classification], best="max")
    out.sub("Field extraction — 29 columns, exact / similarity match per line item")
    extraction = [arm.families["Document Extraction"] for arm in arms]
    out.line("Accuracy", [f4(r[FAM_ACC]) for r in extraction], best="max", emph=True)
    out.line("F1-score", [f4(r[FAM_F1]) for r in extraction], best="max", emph=True)
    out.line("Precision  —  equals Accuracy on this method",
             [f4(r[FAM_ACC]) for r in extraction], best="max")
    out.line("Recall  —  1.0000 by construction here",
             ["1.0000"] * len(arms), best="max")
    coverage = [_published(_COVERAGE, arm.families["Document Extraction"][FAM_NOTE], arm.path)
                for arm in arms]
    out.line("Fields also filled  (mean coverage)", [f4(v) for v in coverage], best="max")

    out.band("EXTRACTION QUALITY   —   no winner marked: Gemma covers a different row set")
    quality = [arm.families["Data Extraction Quality"][FAM_NOTE] for arm in arms]
    out.line("Free-text mean CER — 8 name/address columns  (lower is better)",
             [f4(_published(_MEAN_CER, note, arm.path))
              for arm, note in zip(arms, quality)], emph=True)
    out.line("Format compliance — 13 prediction-only checks",
             [f4(_published(_FORMAT_PASS, note, arm.path))
              for arm, note in zip(arms, quality)])

    out.band("TIME   —   no winner marked: cloud batch vs internal GPU loop")
    walls = per_arm(lambda a: a.summary["Batch Wall Seconds"],
                    lambda a: a.summary["Files Elapsed (ms)"] / 1000)
    out.line("Whole run, wall clock (as recorded)", [clock(w) for w in walls])
    # The internal clocks span checkpointed sessions (all three runs resumed), so a
    # per-page figure derived from them would misstate the endpoint.
    out.line("Per page, seconds", per_arm(
        lambda a: f"{a.summary['Seconds / File']:.2f}",
        lambda a: ("muted", "resumed run — see note"),
    ))
    out.line("Queue before the batch ran, seconds", per_arm(
        lambda a: f"{a.summary['Queue Seconds']:.1f}",
        lambda a: ("muted", "n/a — internal endpoint"),
    ))

    out.band("TOKENS   —   whole run; no winner marked: the pipelines differ")
    out.line("Input tokens, total", [thousands(arm.summary["Prompt Tokens"]) for arm in arms])
    out.line("Output tokens, total", per_arm(
        lambda a: thousands(a.summary["Candidates Tokens"]),
        lambda a: thousands(a.summary["Completion Tokens"]),
    ))
    out.line("Thinking tokens", per_arm(
        lambda a: ("muted", "not recorded") if "Thoughts Tokens" in a.unreported
        else thousands(a.summary["Thoughts Tokens"]),
        lambda a: ("muted", "not recorded"),
    ))
    out.line("Cached input tokens", per_arm(
        lambda a: ("muted", "not recorded") if "Cached Tokens" in a.unreported
        else thousands(a.summary["Cached Tokens"]),
        lambda a: ("muted", "not recorded"),
    ))
    out.line("Total tokens, whole run",
             [thousands(arm.summary["Total Tokens"]) for arm in arms], emph=True)
    out.line("Average per page", per_arm(
        lambda a: thousands(a.summary["Avg Total Tokens / File"]),
        lambda a: thousands(a.summary["Avg Total Tokens / Page"]),
    ))

    out.band("RELIABILITY")
    ran = per_arm(lambda a: int(a.summary["Source Files"]), lambda a: int(a.summary["Pages"]))
    good = [int(arm.summary["Succeeded"]) for arm in arms]
    out.line("Pages succeeded", [f"{g}/{r}" for g, r in zip(good, ran)])
    out.line("Failed or errored pages", [
        (("good" if r - g == 0 else "bad"), f"{r - g}/{r}") for g, r in zip(good, ran)
    ], emph=True)
    out.line("What failed", [
        ("muted", "—") if not arm.failures else
        "  ·  ".join(f"{error} ×{count}" for error, count in sorted(arm.failures.items()))
        for arm in arms
    ])
    out.line("Line parse errors", per_arm(
        lambda a: thousands(a.summary["Line Parse Errors"]),
        lambda a: ("muted", "not recorded"),
    ))
    out.line("Resumed from an earlier checkpoint", per_arm(
        lambda a: ("muted", "no — fresh run"),
        lambda a: ("muted",
                   f"yes — {int(a.summary['Resumed Pages'])} of {int(a.summary['Pages'])} "
                   "pages reused"),
    ))

    _footnotes(out, [
        "Scoring is one question per line item: did the model write the same value as the "
        "human, after folding money/date/case formats; names and addresses match by "
        "character similarity (0.90 / 0.80). On that method Precision equals Accuracy and "
        "Recall is 1.0000 on every row, and F1 = 2a/(1+a) is always above its Accuracy. "
        "Accuracy is the number to read.",
        "One row is one printed line item, not one document — a 20-line invoice weighs 20 "
        "times a single-line receipt in every rate on this sheet.",
        "GEMMA IS NOT STRICTLY COMPARABLE: it failed 19 of 147 pages and read fewer printed "
        "lines, so it is scored on its own 2,573-row subset; the 1,061 unmatched "
        "ground-truth rows are the lines it never produced. Its rates describe only what "
        "it returned.",
        "The Buyer tax id column is the headline trap: Gemini 1.0000 vs Qwen 0.0795, tuned "
        "0.1189, Gemma 0.0027 — and the mod-11 format check fails at the same rate, so the "
        "Qwen/Gemma failures are systematically corrupted digits, not blanks. This one "
        "column drives much of the extraction-accuracy gap; see the Extraction detail "
        "sheet.",
        "Two blank cells count as a match, so a column the humans rarely fill (Total "
        "amount, Vat amount, Withholding tax) scores high just by both sides leaving it "
        "blank — read those against 'GT filled' in the source workbook.",
        "TIME and TOKENS compare a cloud batch service with an internal GPU loop — no "
        "winner is marked. All three internal runs resumed from checkpoints, so their "
        "clocks span sessions and no per-page figure is derived from them.",
        "The tuned arm's source workbook is labelled '(Turning)'; its figures are read "
        "from that file verbatim.",
        "'not recorded' means the run never wrote that column — it is not a measured zero.",
    ])

    detail = SheetWriter(book.create_sheet("Extraction detail"), [arm.header for arm in arms])
    detail.band("FIELD ACCURACY   —   per column, share of line items matching the human")
    for name in gemini.columns:
        detail.line(name, [f4(arm.columns[name][E_ACC]) for arm in arms], best="max")
    detail.band("CHARACTER ERROR RATE   —   8 free-text columns  (lower is better; no bold:"
                " Gemma covers a different row set)")
    for name in gemini.cer:
        detail.line(name, [
            f"{arm.cer[name][C_MEAN]:.4f}   (median {arm.cer[name][C_MEDIAN]:.4f})"
            for arm in arms
        ])
    detail.band("FORMAT COMPLIANCE   —   13 checks on the prediction alone  (no ground truth)")
    for check, column in gemini.format_checks:
        detail.line(f"{check}  —  {column}", [
            f4(arm.format_checks[(check, column)][F_PASS_RATE]) for arm in arms
        ])
    _footnotes(detail, [
        "Per-column rows print Accuracy under each column's match rule (exact, or "
        "similarity 0.90 for names / 0.80 for addresses) — the one number that carries "
        "information on this method.",
        "CER counts wrong characters against the ground truth: 0.05 means one character in "
        "twenty. It can exceed 1.00 when the model wrote far more text than the human. The "
        "mean and the median are both printed because a single bad page can own the mean.",
        "Format compliance is judged on the prediction alone — a value can be "
        "format-perfect and still wrong. Read it with the accuracy block, not instead of "
        "it. No winner is marked: each arm is checked on its own returned rows.",
    ])

    book.save(OUTPUT_PATH)
    return OUTPUT_PATH


if __name__ == "__main__":
    print(build())
