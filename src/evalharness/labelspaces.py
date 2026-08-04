"""Label spaces, verbatim from True's production code.

Do not edit these without re-reading the source. They are the contract, and the
differential test compares against the same lists in
`sentiment-batch-retention-main/src/modules/fact_checker.py`.
"""

from __future__ import annotations

from dataclasses import dataclass

# fact_checker.py:791
CALL_RESULT_CLASSES: tuple[str, ...] = ("save", "churn", "unknown", "undefined")

# fact_checker.py:857-869
REASON_CLASSES: tuple[str, ...] = (
    "network",
    "promotion related",
    "device promotion related",
    "save cost",
    "contract end",
    "sale upsell problem",
    "dissatisfied service",
    "other",
    "post to pre",
    "customer reason",
    "down sell not success",
)

# fact_checker.py:971
PRODUCT_CLASSES: tuple[str, ...] = ("postpaid", "tol", "tvs", "unknown")

# The reason columns that get unioned into a set (fact_checker.py:873)
REASON_COLUMNS: tuple[str, ...] = ("main", "secondary", "third")


@dataclass(frozen=True)
class LabelSpace:
    """One app's label spaces. MNP differs from Retention by a single reason class."""

    app: str
    call_result: tuple[str, ...]
    reason: tuple[str, ...]
    product: tuple[str, ...]


RETENTION = LabelSpace(
    app="retention",
    call_result=CALL_RESULT_CLASSES,
    reason=REASON_CLASSES,
    product=PRODUCT_CLASSES,
)

# MNP carries a 12th reason class (fact_checker.py:835-848 in the MNP repo).
# Declared here so the difference is data, not a code fork. Not yet in scope.
MNP = LabelSpace(
    app="mnp",
    call_result=CALL_RESULT_CLASSES,
    reason=REASON_CLASSES + ("true point, dtac reward",),
    product=PRODUCT_CLASSES,
)
