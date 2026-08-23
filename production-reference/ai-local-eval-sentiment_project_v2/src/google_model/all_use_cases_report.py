"""Append the sentiment sheets (MNP / Telesale / Retention) to resources/All_use_cases.xlsx.

The workbook is the user's cross-use-case comparison book (RTR-Fraud / Tax-Invoice /
Sentiment QA), all in one house layout: METRIC column + one column per arm, bands
WHAT RAN -> BUSINESS OUTCOME (per-topic F1/Precision/Recall/Accuracy blocks) ->
LATENCY -> TOKENS. This module adds the sentiment call-centre use cases in the same
layout, leaving the existing sheets untouched (the file is user-curated, so it is backed
up before the first save).

Every figure is copied from a published cell of the source workbooks' dashboards and
Matrix Summary sheets - nothing is recomputed here beyond ms->s unit conversion. Source
blocks are located by banner text, never by row index.
"""

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

WORKBOOK_PATH = Path("resources/All_use_cases.xlsx")
BACKUP_PATH = Path("backup/2026-08-23") / WORKBOOK_PATH.name
LOCK_PATH = WORKBOOK_PATH.with_name("~$" + WORKBOOK_PATH.name)

MNP_DASHBOARD = "Evaluation Dashboard"
TELE_DASHBOARD = "Confusion Matrix Dashboard"
WEIGHTED = "Weighted-average"

# EVALUATION SUMMARY columns: Topic|Classes|N|Accuracy|Precision|Recall|F1|Note
T_ACC, T_PREC, T_REC, T_F1 = 3, 4, 5, 6
# MNP per-class block columns: Class|Label|TP|FP|FN|TN|N|Support|Acc|P|R|F1
A_TP, A_SUPPORT = 2, 7
# Telesale OVERALL columns: Category|Average|Criteria|Rows|Acc|P|R|F1|Non-disc|Excl|Note
O_CRIT, O_ACC, O_PREC, O_REC, O_F1, O_NONDISC, O_NOTE = 2, 4, 5, 6, 7, 8, 10

MNP_TOPICS = (
    ("Call Result", "Did the customer stay or churn  -  3 outcomes"),
    ("Reason", "Why they wanted to cancel  -  13 reasons"),
)
MNP_BANNERS = (("Call Result", "CALL RESULT (per class)"),
               ("Reason", "CANCELLATION REASON (per class)"))
RETENTION_TOPICS = (
    ("Call Result", "Did the customer stay or churn  -  4 outcomes"),
    ("Reason", "Why they wanted to cancel  -  11 reasons"),
    ("Product", "What product the call was about  -  4 products"),
)
RETENTION_BANNERS = MNP_BANNERS + (("Product", "PRODUCT (per class)"),)
TELE_CATEGORIES = (
    "Operations & professionalism",
    "Sales effectiveness",
    "Customer experience",
    "Compliance",
)
_MEAN_COSINE = re.compile(r"mean cosine ([\d.]+)")


@dataclass
class MnpArm:
    path: str
    topics: dict  # topic -> EVALUATION SUMMARY row
    averages: dict  # topic -> Weighted-average per-class row (for published TP/Support)
    transcript: dict  # metric name -> value
    summary: dict  # Matrix Summary key -> value
    failures: dict  # error text -> count


@dataclass
class TeleArm:
    path: str
    overall: dict  # (category, average) -> OVERALL row
    summary: dict
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
VALUE_FONT = Font(name=FONT, size=10, color="FF4A4A46")
WINNER_FONT = Font(name=FONT, size=10, bold=True, color="FF1A1A18")
MUTED_FONT = Font(name=FONT, size=10, italic=True, color="FF9A9A94")
EMPH_FILL = PatternFill("solid", fgColor="FFF6F3EE")
RIGHT = Alignment(horizontal="right")

_NUMBER = re.compile(r"-?\d[\d,]*\.?\d*")


def _numeric(cell):
    """First number inside a plain value cell, or None (muted cells never rank)."""
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
    """Writes the flat house-layout table: label column + one column per arm."""

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
        for c in range(1, self.columns + 2):
            self.sheet.cell(row=self.row, column=c).fill = EMPH_FILL
        self.sheet.cell(row=self.row, column=1, value=label).font = SUB_FONT
        self.row += 1

    def line(self, label, cells, best=None):
        """One metric row. Cells: plain str, or ("muted", str)."""
        self.sheet.cell(row=self.row, column=1, value=label).font = LABEL_FONT
        winner = _leader(cells, best) if best else None
        for i, spec in enumerate(cells):
            kind, text = spec if isinstance(spec, tuple) else ("value", spec)
            cell = self.sheet.cell(row=self.row, column=2 + i, value=text)
            cell.alignment = RIGHT
            if kind == "muted":
                cell.font = MUTED_FONT
            else:
                cell.font = WINNER_FONT if i == winner else VALUE_FONT
        self.row += 1


def f4(value):
    return f"{value:.4f}"


def thousands(value):
    return f"{value:,.0f}"


# ---------------------------------------------------------------------------
# Source parsing - banner scan, never row indices
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


def _matrix_summary(book):
    return {
        row[0]: row[1]
        for row in book["Matrix Summary"].iter_rows(values_only=True)
        if row and row[0] is not None
    }


def _matrix_failures(book):
    matrix = book["Matrix"].iter_rows(values_only=True)
    index = {name: i for i, name in enumerate(next(matrix)) if name}
    failures = {}
    if "Error Type" in index:
        for row in matrix:
            error = row[index["Error Type"]]
            if error is not None and str(error).strip():
                failures[str(error)] = failures.get(str(error), 0) + 1
    return failures


def _load_mnp_arm(path, topic_banners=MNP_BANNERS):
    book = load_workbook(path, read_only=True, data_only=True)
    rows = list(book[MNP_DASHBOARD].iter_rows(values_only=True))
    topics = {row[0]: row for row in _block(rows, "EVALUATION SUMMARY")}
    averages = {}
    for topic, banner in topic_banners:
        for row in _block(rows, banner, skip=3):
            if row[1] == WEIGHTED:
                averages[topic] = row
        if topic not in averages:
            raise ValueError(f"{path}: no {WEIGHTED} row under {banner}")
        # The Recall count printed on the sheet must restate the published decimal.
        published = topics[topic][T_REC]
        tp, support = averages[topic][A_TP], averages[topic][A_SUPPORT]
        if abs(published - tp / support) > 5e-5:
            raise ValueError(f"{path}: {topic} recall {published} != {tp}/{support}")
    transcript = {row[0]: row[1] for row in _block(rows, "TRANSCRIPT")}
    arm = MnpArm(path, topics, averages, transcript, _matrix_summary(book),
                 _matrix_failures(book))
    book.close()
    return arm


def _load_tele_arm(path):
    book = load_workbook(path, read_only=True, data_only=True)
    rows = list(book[TELE_DASHBOARD].iter_rows(values_only=True))
    overall = {(row[0], row[1]): row
               for row in _block(rows, "OVERALL (by category)", skip=3)}
    for category in TELE_CATEGORIES:
        if (category, WEIGHTED) not in overall:
            raise ValueError(f"{path}: no {WEIGHTED} row for {category!r}")
    failures = _matrix_failures(book)
    arm = TeleArm(path, overall, _matrix_summary(book), failures)
    book.close()
    return arm


# ---------------------------------------------------------------------------
# Shared bands
# ---------------------------------------------------------------------------
def _latency_cells(summary):
    """Per-call and whole-round strings from the published run clocks (ms->s only)."""
    if "Seconds / File" in summary:  # Vertex AI batch arm
        return (f"{summary['Seconds / File']} s",
                f"{summary['Batch Wall Seconds']:,.1f} s")
    elapsed_ms = summary["Files Elapsed (ms)"]
    return (f"{elapsed_ms / 1000 / summary['Succeeded']:,.1f} s",
            f"{elapsed_ms / 1000:,.1f} s")


def _token_cells(summary):
    """Input / output / total strings; splits are restated as text, never summed here."""
    if "Prompt Tokens" in summary:  # Vertex AI batch arm
        inp = thousands(summary["Prompt Tokens"])
        out = thousands(summary["Candidates Tokens"])
        if summary.get("Thoughts Tokens"):
            out += f"  (+ {thousands(summary['Thoughts Tokens'])} thinking)"
    else:  # internal ASR+LLM arm: label call + analysis call per file
        inp = (f"{thousands(summary['Label Prompt Tokens'])} label"
               f"  +  {thousands(summary['Analysis Prompt Tokens'])} analysis")
        out = (f"{thousands(summary['Label Completion Tokens'])} label"
               f"  +  {thousands(summary['Analysis Completion Tokens'])} analysis")
    return inp, out, thousands(summary["Total Tokens"])


def _tail_bands(w, gemini_summary, qwen_summary):
    w.band("LATENCY")
    g_per, g_whole = _latency_cells(gemini_summary)
    q_per, q_whole = _latency_cells(qwen_summary)
    w.line("Per call", [g_per, q_per])
    w.line("Time for the whole round  -  seconds", [g_whole, q_whole])
    w.band("TOKENS ")
    g_in, g_out, g_total = _token_cells(gemini_summary)
    q_in, q_out, q_total = _token_cells(qwen_summary)
    w.line("Total Input", [g_in, q_in])
    w.line("Total Output", [g_out, q_out])
    w.line("Total tokens", [g_total, q_total])


def _what_ran(w, gemini, qwen, scored_cells):
    w.band("WHAT RAN")
    w.line("Model", [str(gemini.summary["Model"]), str(qwen.summary["Model"])])
    w.line("Speech to text",
           ["none - audio into the model", str(qwen.summary["ASR Model"])])
    w.line("Where it ran", ["Vertex AI batch", "internal GPU endpoint"])
    w.line("Calls scored", scored_cells)


def _metric_rows(w, labelled_cells):
    for label, cells in labelled_cells:
        w.line(label, cells, best="max")


def _scored_cell(summary, failures):
    """Succeeded count, annotated with the published error notes when any call failed."""
    text = thousands(summary["Succeeded"])
    failed = int(summary["Failed"])
    if failed:
        errors = "  ·  ".join(f"{error} ×{count}"
                              for error, count in sorted(failures.items()))
        word = "call" if failed == 1 else "calls"
        text += f"  -  {failed} {word} failed ({errors})"
    return text


# ---------------------------------------------------------------------------
# Sheet builders
# ---------------------------------------------------------------------------
def build_mnp(sheet, gemini, qwen, topics=MNP_TOPICS):
    if set(gemini.topics) != set(qwen.topics):
        raise ValueError("the two workbooks disagree about the summary topics")
    w = SheetWriter(sheet, ["GEMINI-2.5-FLASH", "QWEN3.8-27B-FP8"])
    scored = [_scored_cell(arm.summary, arm.failures) for arm in (gemini, qwen)]
    _what_ran(w, gemini, qwen, scored)
    w.band("BUSINESS OUTCOME")
    for topic, caption in topics:
        w.sub(caption)
        rows = []
        for label, col in (("F1-score", T_F1), ("Precision", T_PREC),
                           ("Recall", T_REC), ("Accuracy", T_ACC)):
            cells = []
            for arm in (gemini, qwen):
                text = f4(arm.topics[topic][col])
                if label == "Recall":
                    avg = arm.averages[topic]
                    text += f"   ({int(avg[A_TP])}/{int(avg[A_SUPPORT])})"
                cells.append(text)
            rows.append((label, cells))
        _metric_rows(w, rows)
    w.sub("THE TRANSCRIPT   -   scored by meaning and by wording")
    w.line("Meaning match  -  cosine similarity",
           [f4(gemini.transcript["Mean cosine"]), f4(qwen.transcript["Mean cosine"])])
    w.line("Characters right  -  1 - mean CER",
           [f4(gemini.transcript["Mean char accuracy (1 - CER)"]),
            f4(qwen.transcript["Mean char accuracy (1 - CER)"])])
    _tail_bands(w, gemini.summary, qwen.summary)


def build_telesale(sheet, gemini, qwen):
    w = SheetWriter(sheet, ["GEMINI-2.5-FLASH", "QWEN3.8-27B-FP8"])
    scored = [_scored_cell(arm.summary, arm.failures) for arm in (gemini, qwen)]
    _what_ran(w, gemini, qwen, scored)
    w.band("BUSINESS OUTCOME")
    for category in TELE_CATEGORIES:
        g_row = gemini.overall[(category, WEIGHTED)]
        q_row = qwen.overall[(category, WEIGHTED)]
        if (g_row[O_CRIT], g_row[O_NONDISC]) != (q_row[O_CRIT], q_row[O_NONDISC]):
            raise ValueError(f"the two telesale workbooks disagree about {category!r}")
        criteria, nondisc = int(g_row[O_CRIT]), int(g_row[O_NONDISC])
        word = "holds" if nondisc == 1 else "hold"
        w.sub(f"{category}  -  {criteria} criteria, "
              f"{nondisc} {word} one label on every call")
        _metric_rows(w, [
            ("F1-score", [f4(g_row[O_F1]), f4(q_row[O_F1])]),
            ("Precision", [f4(g_row[O_PREC]), f4(q_row[O_PREC])]),
            ("Recall", [f4(g_row[O_REC]), f4(q_row[O_REC])]),
            ("Accuracy", [f4(g_row[O_ACC]), f4(q_row[O_ACC])]),
        ])
    w.sub("THE TRANSCRIPT   -   scored by meaning and by wording")
    cosines, chars = [], []
    for arm in (gemini, qwen):
        row = arm.overall[("Transcript", "1 - mean CER")]
        match = _MEAN_COSINE.search(str(row[O_NOTE]))
        if not match:
            raise ValueError(f"{arm.path}: no mean cosine on the Transcript row")
        cosines.append(f"{float(match.group(1)):.4f}")
        chars.append(f4(row[O_ACC]))
    w.line("Meaning match  -  cosine similarity", cosines)
    w.line("Characters right  -  1 - mean CER", chars)
    _tail_bands(w, gemini.summary, qwen.summary)


# ---------------------------------------------------------------------------
def build():
    if LOCK_PATH.exists():
        raise RuntimeError(f"{WORKBOOK_PATH} is open in Excel - close it first")
    if not WORKBOOK_PATH.exists():
        raise FileNotFoundError(WORKBOOK_PATH)
    if not BACKUP_PATH.exists():  # keep the first backup; never overwrite it
        BACKUP_PATH.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(WORKBOOK_PATH, BACKUP_PATH)
    mnp_gemini = _load_mnp_arm("resources/sentiment-mnp-gemini-2.5-flash.xlsx")
    mnp_qwen = _load_mnp_arm("resources/sentiment-mnp-qwen3.8-27b-fp8.xlsx")
    tele_gemini = _load_tele_arm("resources/sentiment-telesale-gemini-2.5-flash.xlsx")
    tele_qwen = _load_tele_arm("resources/sentiment-telesale-qwen3.8-27b-fp8.xlsx")
    ret_gemini = _load_mnp_arm("resources/sentiment-retention-gemini-2.5-flash.xlsx",
                               RETENTION_BANNERS)
    ret_qwen = _load_mnp_arm("resources/sentiment-retention-qwen3.8-27b-fp8.xlsx",
                             RETENTION_BANNERS)
    book = load_workbook(WORKBOOK_PATH)
    for title in ("Sentiment MNP", "Sentiment Telesale", "Sentiment Retention"):
        if title in book.sheetnames:
            del book[title]
    build_mnp(book.create_sheet("Sentiment MNP"), mnp_gemini, mnp_qwen)
    build_telesale(book.create_sheet("Sentiment Telesale"), tele_gemini, tele_qwen)
    build_mnp(book.create_sheet("Sentiment Retention"), ret_gemini, ret_qwen,
              RETENTION_TOPICS)
    book.save(WORKBOOK_PATH)
    return str(WORKBOOK_PATH)


if __name__ == "__main__":
    print(build())
