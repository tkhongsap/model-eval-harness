"""Executive report: Sentiment QA — Gemini 2.5 Flash vs two Qwen3.8-27b pipelines.

Rebuilds ``resources/Sentiment QA Report.xlsx`` as a flat two-sheet scorecard in the
``resources/photo_use_cases.xlsx`` house layout: one METRIC column plus one column per
arm, banded sections (PIPELINE SHAPE -> BUSINESS OUTCOME -> TRANSCRIPTION STAGE -> TIME
-> TOKENS -> RELIABILITY), bold single leaders on contested quality rows, grey italic
placeholders where a value was never recorded, and `-prefixed footnotes.

Two caveats this report must keep on its face:
- The arms are NOT scored on a common set: Gemini answered 300 calls, both Qwen runs 180.
- The two Qwen arms change two variables at once - the prompt (full vs trimmed) AND the
  speech engine (Qwen3-ASR 1.7B vs Typhoon Whisper) - so their gap cannot be attributed
  to the prompt alone.

Every figure is copied from a published cell of the source workbooks' Evaluation
Dashboard / Matrix Summary sheets - nothing is recomputed here.
"""

import re
from dataclasses import dataclass

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

OUTPUT_PATH = "resources/Sentiment QA Report.xlsx"
DASHBOARD = "Evaluation Dashboard"
FAMILIES = "OVERALL (by family)"
QA_CRITERIA = "QA CRITERIA (per column)"
SENTIMENT = "SENTIMENT (per column)"
CALL_TYPE = "CALL TYPE"
CALL_TYPE_LABELS = "CALL TYPE (per label)"
SUMMARY_STORY = "SUMMARY STORY"
TRANSCRIPT = "TRANSCRIPT"

# OVERALL (by family) rows: Family|Columns|Rows|Accuracy|F1|Note
FAM_ROWS, FAM_ACC, FAM_F1 = 2, 3, 4
# Per-column rows: Column|N|TP|FP|FN|TN|Accuracy|Precision|Recall|F1
COL_ACC = 6
# CALL TYPE (per label) rows: Label|TP|TN|FP|FN|Precision|Recall|F1|Support
L_TP, L_TN, L_FP, L_FN, L_PREC, L_REC, L_F1, L_SUPPORT = 1, 2, 3, 4, 5, 6, 7, 8


@dataclass
class Arm:
    key: str
    path: str
    header: str
    transcriber: str
    prompt: str
    families: dict
    qa_columns: dict
    sentiment_columns: dict
    call_type: dict
    call_type_labels: dict
    summary_story: dict
    transcript: dict
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


def _load_arm(key, path, header, transcriber, prompt, unreported_columns):
    book = load_workbook(path, read_only=True, data_only=True)
    rows = list(book[DASHBOARD].iter_rows(values_only=True))
    families = {row[0]: row for row in _block(rows, FAMILIES)}
    qa_columns = {row[0]: row for row in _block(rows, QA_CRITERIA)}
    sentiment_columns = {row[0]: row for row in _block(rows, SENTIMENT)}
    call_type = {row[0]: row[1] for row in _block(rows, CALL_TYPE)}
    call_type_labels = {row[0]: row for row in _block(rows, CALL_TYPE_LABELS)}
    summary_story = {row[0]: row[1] for row in _block(rows, SUMMARY_STORY)}
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
    return Arm(key, path, header, transcriber, prompt, families, qa_columns,
               sentiment_columns, call_type, call_type_labels, summary_story, transcript,
               summary, unreported, failures)


# ---------------------------------------------------------------------------
# Report sections
# ---------------------------------------------------------------------------
def _tokens_cell(arm, column, text):
    if column in arm.unreported:
        return ("muted", "not recorded")
    return text


def _family_metrics(out, arms, family):
    """Accuracy-driven four-metric block: on this method P = Acc and R = 1 (footnoted)."""
    rows = [arm.families[family] for arm in arms]
    out.line("F1-score", [f4(r[FAM_F1]) for r in rows], best="max", emph=True)
    out.line("Accuracy", [f4(r[FAM_ACC]) for r in rows], best="max", emph=True)
    out.line("Precision  —  equals Accuracy on this method",
             [f4(r[FAM_ACC]) for r in rows], best="max")
    out.line("Recall  —  1.0000 by construction here", ["1.0000"] * len(rows), best="max")


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
        "resources/sentiment-qa-gemini-2.5-flash.xlsx",
        "GEMINI 2.5 FLASH",
        transcriber=None,
        prompt=None,
        unreported_columns=("Thoughts Tokens", "Cached Tokens"),
    )
    qwen_full = _load_arm(
        "qwen_full",
        "resources/semtimet-qa-qwen3.8-27b-fp8 (full prompt).xlsx",
        "QWEN ASR + QWEN3.8 (FULL)",
        transcriber="Qwen3-ASR 1.7B",
        prompt="full",
        unreported_columns=("Analysis Reasoning Tokens", "Analysis Cached Tokens"),
    )
    qwen_trim = _load_arm(
        "qwen_trim",
        "resources/semtimet-qa-qwen3.8-27b-fp8 (trim prompt).xlsx",
        "TYPHOON + QWEN3.8 (TRIM)",
        transcriber="Typhoon Whisper large-v3",
        prompt="trimmed",
        unreported_columns=("Analysis Reasoning Tokens", "Analysis Cached Tokens"),
    )
    arms = (gemini, qwen_full, qwen_trim)

    for arm in arms:
        for family in ("QA Criteria", "Sentiment", "Call Type"):
            if family not in arm.families:
                raise ValueError(f"{arm.path}: missing family row {family!r}")
    first = list(gemini.qa_columns)
    for arm in arms[1:]:
        if list(arm.qa_columns) != first:
            raise ValueError("the workbooks scored different QA criteria")
        if list(arm.call_type_labels) != list(gemini.call_type_labels):
            raise ValueError("the workbooks scored different call-type labels")
    scored = [int(arm.families["QA Criteria"][FAM_ROWS]) for arm in arms]

    book = Workbook()
    out = SheetWriter(book.active, [arm.header for arm in arms])
    book.active.title = "Scorecard"

    out.band("PIPELINE SHAPE")
    out.line("Transcriber", [
        ("muted", "none — direct audio") if arm.transcriber is None else arm.transcriber
        for arm in arms
    ])
    out.line("Labeller", [("muted", "(same model)"), "Qwen3.8-27b-fp8", "Qwen3.8-27b-fp8"])
    out.line("Prompt variant", [
        ("muted", "not recorded") if arm.prompt is None else arm.prompt for arm in arms
    ])
    out.line("Model calls per call", ["1", "2", "2"])
    out.line("Runtime", [
        ("muted", "Vertex AI batch (global)"),
        ("muted", "internal GPU endpoint"),
        ("muted", "internal GPU endpoint"),
    ])
    out.line("Calls scored — NOT a common set", [str(s) for s in scored], emph=True)
    out.line("Calls this arm ran", [thousands(arm.summary["Source Files"]) for arm in arms])

    out.band("BUSINESS OUTCOME   —   PRIMARY   —   the arms scored different call sets")
    out.sub("QA criteria — 22 columns, did the model grade the call like the human?")
    _family_metrics(out, arms, "QA Criteria")
    out.sub("Sentiment — 3 columns  (overall / initial / final)")
    _family_metrics(out, arms, "Sentiment")
    out.sub("Call type — 5 types, the one block where precision and recall are real")
    out.line("Exact set match", [f4(arm.call_type["Exact set match"]) for arm in arms],
             best="max", emph=True)
    out.line("Macro F1  —  every type counts the same",
             [f4(arm.call_type["Macro-F1"]) for arm in arms], best="max")
    out.line("Micro F1  —  frequent types dominate",
             [f4(arm.call_type["Micro-F1"]) for arm in arms], best="max")
    out.line("Micro Precision", [f4(arm.call_type["Micro-Precision"]) for arm in arms],
             best="max")
    out.line("Micro Recall", [f4(arm.call_type["Micro-Recall"]) for arm in arms], best="max")
    out.sub("Summary story — Thai summary, meaning similarity")
    out.line("Mean cosine", [f4(arm.summary_story["Mean cosine"]) for arm in arms], best="max")
    out.line("Summaries at or above 0.80",
             [pct(arm.summary_story["Pass rate (>= 0.80)"]) for arm in arms], best="max")

    _transcription(out, arms)

    out.band("TIME   —   no winner marked: cloud batch vs internal GPU loop")
    walls = [gemini.summary["Batch Wall Seconds"],
             qwen_full.summary["Files Elapsed (ms)"] / 1000,
             qwen_trim.summary["Files Elapsed (ms)"] / 1000]
    out.line("Whole run, wall clock (as recorded)", [clock(w) for w in walls])
    # The Qwen clocks span checkpointed sessions (both runs resumed), so a per-call figure
    # derived from them would misstate the endpoint; only the published batch rate prints.
    out.line("Per call, seconds", [
        f"{gemini.summary['Seconds / File']:.2f}",
        ("muted", "resumed run — see note"),
        ("muted", "resumed run — see note"),
    ])
    out.line("Queue before the batch ran, seconds", [
        f"{gemini.summary['Queue Seconds']:.1f}",
        ("muted", "n/a — internal endpoint"),
        ("muted", "n/a — internal endpoint"),
    ])

    out.band("TOKENS   —   whole run; no winner marked: different call sets and pipelines")
    def split(arm):
        s = arm.summary
        return (f"{s['Label Prompt Tokens']:,.0f} label  +  "
                f"{s['Analysis Prompt Tokens']:,.0f} analysis",
                f"{s['Label Completion Tokens']:,.0f} label  +  "
                f"{s['Analysis Completion Tokens']:,.0f} analysis")
    out.line("Input tokens, total", [
        thousands(gemini.summary["Prompt Tokens"]), split(qwen_full)[0], split(qwen_trim)[0],
    ])
    out.line("Output tokens, total", [
        thousands(gemini.summary["Candidates Tokens"]), split(qwen_full)[1],
        split(qwen_trim)[1],
    ])
    out.line("Thinking tokens", [
        _tokens_cell(gemini, "Thoughts Tokens", thousands(gemini.summary["Thoughts Tokens"])),
        _tokens_cell(qwen_full, "Analysis Reasoning Tokens", "0"),
        _tokens_cell(qwen_trim, "Analysis Reasoning Tokens", "0"),
    ])
    out.line("Cached input tokens", [
        _tokens_cell(gemini, "Cached Tokens", thousands(gemini.summary["Cached Tokens"])),
        _tokens_cell(qwen_full, "Analysis Cached Tokens", "0"),
        _tokens_cell(qwen_trim, "Analysis Cached Tokens", "0"),
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
    out.line("Line parse errors", [
        thousands(gemini.summary["Line Parse Errors"]),
        ("muted", "not recorded"), ("muted", "not recorded"),
    ])
    out.line("Resumed from an earlier checkpoint", [
        ("muted", "no — fresh run"),
        ("muted", f"yes — {int(qwen_full.summary['Resumed Files'])} of {ran[1]} calls reused"),
        ("muted", f"yes — {int(qwen_trim.summary['Resumed Files'])} of {ran[2]} calls reused"),
    ])

    _footnotes(out, [
        "READ THIS FIRST: the three arms did NOT score the same calls — Gemini answered 300, "
        "both Qwen runs 180. Bold still marks the row leader, but a gap can partly be the "
        "call set. The counts are on the face of PIPELINE SHAPE.",
        "The two Qwen arms change two variables at once: the prompt (full vs trimmed) AND "
        "the speech engine (Qwen3-ASR 1.7B vs Typhoon Whisper). Their gap cannot be "
        "attributed to the prompt alone.",
        "QA criteria and Sentiment are scored one way — did the model write the same grade "
        "as the human. On that method Precision equals Accuracy and Recall is 1.0000 on "
        "every row, and F1 = 2a/(1+a) is always above the Accuracy it is derived from. "
        "Accuracy is the number that carries information; Call type is the block where "
        "Precision and Recall are genuine.",
        "A criterion that is mostly 'N/A' scores high just by both sides agreeing on N/A — "
        "read the per-criterion sheet before crediting a high row.",
        "TRANSCRIPTION carries no winner: three different speech paths. The trim-prompt "
        "run's mean CER (0.8348) is dragged by one call at 27.31 — its median (0.3259) is "
        "the typical-call figure.",
        "TIME and TOKENS compare a cloud batch service with an internal GPU loop and "
        "different call sets — no winner is marked. Both Qwen clocks span checkpointed "
        "sessions (resumed runs), so no per-call figure is derived from them.",
        "'not recorded' means the run never wrote that column — it is not a measured zero.",
    ])

    detail = SheetWriter(book.create_sheet("Criteria detail"), [arm.header for arm in arms])
    detail.band("QA CRITERIA   —   share of calls graded like the human, per criterion")
    for name in gemini.qa_columns:
        detail.line(name, [f4(arm.qa_columns[name][COL_ACC]) for arm in arms], best="max")
    detail.band("SENTIMENT   —   share of calls graded like the human")
    for name in gemini.sentiment_columns:
        detail.line(name, [f4(arm.sentiment_columns[name][COL_ACC]) for arm in arms],
                    best="max")
    detail.band("CALL TYPE   —   per label; precision and recall are real here")
    for name in gemini.call_type_labels:
        supports = [int(arm.call_type_labels[name][L_SUPPORT]) for arm in arms]
        detail.sub(f"{name}   —   the human used it on {' / '.join(map(str, supports))} calls")
        rows = [arm.call_type_labels[name] for arm in arms]
        detail.line("F1-score", [f4(r[L_F1]) for r in rows], best="max", emph=True)
        detail.line("Precision", [f4(r[L_PREC]) for r in rows], best="max")
        detail.line("Recall", [f4(r[L_REC]) for r in rows], best="max")
        counts = [" · ".join(str(int(r[i])) for i in (L_TP, L_FP, L_FN, L_TN)) for r in rows]
        detail.line("Confusion  —  TP · FP · FN · TN", counts)
    _footnotes(detail, [
        "Per-criterion and per-sentiment rows print Accuracy — the one number that carries "
        "information on this scoring method (its F1 is derived as 2a/(1+a)).",
        "Support counts differ because the arms scored different call sets (300 vs 180 vs "
        "180). Compare the two Qwen columns with each other before comparing either to "
        "Gemini.",
        "Sale has almost no support (9 / 5 / 5 human-labelled calls) — one call moves its "
        "scores more than any model difference.",
    ])

    book.save(OUTPUT_PATH)
    return OUTPUT_PATH


if __name__ == "__main__":
    print(build())
