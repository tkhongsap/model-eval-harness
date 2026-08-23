"""The cancellation-reason class registry shared by the MNP and retention dashboards.

One ordered, numbered list so the two use cases number the same reason the same way, and a
reader comparing their dashboards is comparing like with like. MNP scores all twelve; retention
scores eleven -- its prompt has no ``true point, dtac reward``, folding that case into ``other``
-- and the gap in the numbering is deliberate, so class 12 stays class 12 on both sheets.

The order is **not** the schema enum's. Both production evaluators score in this order, which
moves ``other`` from last to eighth, and both stakeholder workbooks' ``Master Reason`` tabs number
it the same way. Reproducing it is what lets a number on our sheet be checked against theirs.

* retention: ``fact_checker.py`` ``reason_calculation.target_classes`` -- eleven entries, no
  ``true point, dtac reward``.
* MNP: the same list plus ``true point, dtac reward`` at 11 and ``down sell not success`` at 12.

Nothing here does I/O or knows about pandas: :func:`order_classes` takes a use case's
``ReasonCategory`` arguments and returns them in registry order, so the vocabulary still has
exactly one definition -- the schema -- and this module only decides how it is presented.
"""

from __future__ import annotations

from collections.abc import Sequence

# Class numbers as both production evaluators and both stakeholder workbooks assign them.
# `other` at 8 is the one that surprises: the schema enums put it last.
REASON_CLASS_NUMBERS: dict[str, int] = {
    "network": 1,
    "promotion related": 2,
    "device promotion related": 3,
    "save cost": 4,
    "contract end": 5,
    "sale upsell problem": 6,
    "dissatisfied service": 7,
    "other": 8,
    "post to pre": 9,
    "customer reason": 10,
    "true point, dtac reward": 11,
    "down sell not success": 12,
}

# The catch-all row for a graded value outside the schema's vocabulary. It exists so a
# ground-truth typo is visible on the sheet instead of vanishing: the MNP tab holds
# `contact end` and `cutomer reason`, which would otherwise cost `contract end` and
# `customer reason` a third and a half of their recall with nothing to say why.
#
# Deliberately not a member of REASON_CLASS_NUMBERS -- it has no class number, because it is not
# a class the prompt can ask for.
UNKNOWN_CLASS = "unknown (out-of-vocabulary)"


def order_classes(labels: Sequence[str]) -> tuple[str, ...]:
    """Sort one use case's reason vocabulary into registry order.

    Args:
        labels: The use case's ``ReasonCategory`` members, in any order.

    Returns:
        The same labels, ordered by class number.

    Raises:
        KeyError: If a label has no class number. That means the schema gained a category this
            registry does not know, and the fix is to add it here with a number rather than to
            let it sort silently to one end.
    """
    unknown = [label for label in labels if label not in REASON_CLASS_NUMBERS]
    if unknown:
        raise KeyError(
            f"No class number for {sorted(unknown)}; add them to REASON_CLASS_NUMBERS."
        )
    return tuple(sorted(labels, key=lambda label: REASON_CLASS_NUMBERS[label]))


def class_label(label: str) -> str:
    """The ``Class N`` cell for a reason, or a blank for the out-of-vocabulary row."""
    number = REASON_CLASS_NUMBERS.get(label)
    return f"Class {number}" if number is not None else ""
