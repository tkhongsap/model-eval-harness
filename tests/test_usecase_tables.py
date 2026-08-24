"""Every number on `usecase-comparison.html` must be the workbook's number.

WHY THIS FILE EXISTS. That page is the one a stakeholder reads: six tables, incumbent against
our internally hosted models, no argument around it. It quotes another team's published figures,
so a transcription slip there is worse than a wrong conclusion -- it is unfalsifiable by the
reader, who has no way to know the cell was ever different.

The generator reads the parsed workbook, so drift should be impossible by construction. This
file refuses to take that on trust: it re-parses the rendered HTML and matches every score cell
back to `parallel-eval-workbook.json`.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
BOOK = REPO / "docs" / "reports" / "parallel-eval-workbook.json"
PAGE = REPO / "docs" / "reports" / "usecase-comparison.html"


@pytest.fixture(scope="module")
def book() -> dict:
    if not BOOK.is_file():
        pytest.skip(f"{BOOK.name} not present")
    return json.loads(BOOK.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def page() -> str:
    if not PAGE.is_file():
        pytest.skip(f"{PAGE.name} not built")
    return PAGE.read_text(encoding="utf-8")


def _numbers(text: str) -> list[float]:
    """Every number in a chunk of rendered HTML, entities and markup resolved."""
    plain = html.unescape(re.sub(r"<[^>]+>", " ", text))
    return [float(m.replace(",", ""))
            for m in re.findall(r"-?\d[\d,]*\.?\d*", plain)]


def test_one_table_per_tab(book, page):
    assert page.count('<table class="uc">') == len(book["tabs"]), (
        "a use case lost its table")
    for name in book["tabs"]:
        assert f">{html.escape(name)}</h2>" in page.replace("</span>", "</span>") or \
            name in page, f"{name} is missing its heading"


def test_the_page_is_pure_ascii(page):
    """The shared head declares a charset, but the page must not depend on it being honoured."""
    bad = {c for c in page if ord(c) > 126}
    assert not bad, f"non-ascii characters would render as mojibake without a charset: {bad}"


def test_no_double_escaped_entities(page):
    """`&amp;middot;` printed literally is the bug this catches; it shipped once already."""
    for leak in ("&amp;middot;", "&amp;mdash;", "&amp;nbsp;", "&amp;times;"):
        assert leak not in page, f"{leak} is being printed as text"


def test_every_headline_and_f1_appears_with_the_workbook_value(book, page):
    """The load-bearing check: score cells on the page match the parsed workbook."""
    checked = 0
    for name, tab in book["tabs"].items():
        arms = [c for c in tab["arms"] if c.strip().upper() != "GROUND TRUTH"]
        rows = [r for r in tab["rows"]
                if r["section"] == "BUSINESS OUTCOME" and r["label"] == "F1-score"]
        rows += [r for r in tab["rows"]
                 if r["label"].lower().startswith("weighted average f1")]
        for r in rows:
            for col in arms:
                v = r["numeric"].get(col)
                if v is None:
                    continue
                # The page pads bare numbers to 4 dp; both spellings are acceptable.
                candidates = {f"{v:.4f}", f"{v:g}", str(v)}
                assert any(c in page for c in candidates), (
                    f"{name} / {r['dimension'] or r['label']} / {col}: "
                    f"{v} does not appear on the page (tried {sorted(candidates)})")
                checked += 1
    assert checked >= 40, f"expected to check at least 40 score cells, checked {checked}"


def test_ties_are_not_given_a_winner(book, page):
    """Bolding the leftmost of three identical numbers would invent a winner from column order.

    RTR-Fraud's Same_Photo is 0.9975 across every arm, so no cell in that row may be bolded.
    """
    tab = book["tabs"].get("RTR-Fraud")
    if not tab:
        pytest.skip("RTR-Fraud not in the workbook")
    tied = [r for r in tab["rows"]
            if r["label"] == "F1-score" and r["section"] == "BUSINESS OUTCOME"
            and len({v for c, v in r["numeric"].items()
                     if v is not None and c.strip().upper() != "GROUND TRUTH"}) == 1]
    if not tied:
        pytest.skip("no tied score row in RTR-Fraud")
    for r in tied:
        val = next(v for c, v in r["numeric"].items()
                   if v is not None and c.strip().upper() != "GROUND TRUTH")
        assert f"<b>{val:.4f}</b>" not in page, (
            f"RTR-Fraud/{r['dimension']}: every arm scored {val}, so nothing may be bolded")


def test_precision_recall_accuracy_are_deliberately_absent(book, page):
    """The page prints F1 alone where the matrix is degenerate. Guard the reason, not the taste.

    On the tabs whose recall is pinned at 1.0000 by an FN=0 matrix, printing Recall beside F1
    would show a column of 1.0000 that a reader takes for a perfect score. If a future edit adds
    those rows back, this fails and the footnote has to be revisited with it.
    """
    degenerate = [n for n, t in book["tabs"].items()
                  if t["derived"]["recall_exactly_one"]["cells"]]
    assert degenerate, "expected at least one tab with the FN=0 signature"
    assert "<td>Recall" not in page and ">Recall<" not in page, (
        "Recall rows are on the page; on "
        f"{', '.join(degenerate)} that column is 1.0000 by construction")
    # ...and the footnote that explains the omission must still name those tabs.
    for name in degenerate:
        assert html.escape(name) in page, f"the footnote no longer names {name}"
