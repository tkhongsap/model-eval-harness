"""Retention adapter: raw source -> normalized Records.

**This is the seam that changes when real data arrives.** Everything downstream
(metrics, comparison, manifest) is final. The real ground truth is an .xlsm whose
column names are reconstructed from a TWO-ROW header block
(fact_checker.py:517-525), and we are guessing how those rows combine until the
team sends the first two header rows. That guess is contained here, on purpose:
when it turns out to be wrong, only this file changes.
"""

from __future__ import annotations

import csv
from pathlib import Path

from ..records import Record, from_row


def load_csv(path: str | Path) -> list[Record]:
    """Load a fixture or an extract in the flat CSV shape from the data contract."""
    with open(path, newline="", encoding="utf-8") as fh:
        return [from_row(row) for row in csv.DictReader(fh)]


def load_workbook(path: str | Path) -> list[Record]:  # pragma: no cover
    """Load the real .xlsm ground truth.

    Deliberately unimplemented. Implementing it against a guessed header layout
    would produce a loader that appears to work and silently mis-maps columns,
    which is worse than one that refuses. Ask 1 in docs/data-contract.md requests
    the two header rows that settle it.
    """
    raise NotImplementedError(
        "Workbook layout is unknown until the team sends the first two header rows "
        "(see docs/data-contract.md, Ask 1). Refusing rather than guessing."
    )
