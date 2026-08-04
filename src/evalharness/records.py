"""The normalized record, and the normalisation rules it encodes.

Grain is `(call_id, phone, product)`, not call: `prepare_predictions` emits one row
per product (fact_checker.py:603-631) and the scorer merges on all three keys
(:1075). See tests/fixtures/WORKED-COMPUTATION.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .labelspaces import REASON_COLUMNS

# Values that production maps to None for phone_number (fact_checker.py:718-730).
_PHONE_NULLS = {"0", "", "none", "nan"}


def norm_text(value: object) -> str | None:
    """Lowercase and strip, mapping blanks to None.

    Production does this only when a column's dtype is 'object'
    (fact_checker.py:734, :738), which is False on pandas 3.x, so the guard
    silently stops firing on a version bump. This is permitted deviation 2 in the
    goal contract: we normalise unconditionally.
    """
    if value is None:
        return None
    text = str(value).strip().lower()
    return text or None


def norm_phone(value: object) -> str | None:
    """Phone number normalisation: 0, '0' and blanks all become None.

    Consequence worth knowing: a None phone drops the whole call out of the
    product dimension, because production groups on ['call_id','phone_number']
    without dropna=False (fact_checker.py:1080-1081). We reproduce that, and count it.
    """
    text = norm_text(value)
    if text is None or text in _PHONE_NULLS:
        return None
    return text


def parse_reasons(row: dict) -> frozenset[str]:
    """Union main/secondary/third into a set, comma-splitting each cell.

    Mirrors get_reasons_set (fact_checker.py:871-879). Rank is discarded: a cell
    of "network, save cost" is identical to "save cost, network", which is why a
    positional main-vs-main comparison is wrong.
    """
    reasons: set[str] = set()
    for col in REASON_COLUMNS:
        raw = row.get(col)
        if raw is None:
            continue
        for part in str(raw).split(","):
            cleaned = part.strip().lower()
            if cleaned:
                reasons.add(cleaned)
    return frozenset(reasons)


@dataclass(frozen=True)
class Record:
    """One scored row. Immutable so a scorer cannot mutate its own input."""

    call_id: str
    phone: str | None
    product: str | None
    call_result: str | None
    reasons: frozenset[str] = field(default_factory=frozenset)
    parse_ok: bool = True

    @property
    def key(self) -> tuple[str, str | None, str | None]:
        """The three-part merge key (fact_checker.py:1075)."""
        return (self.call_id, self.phone, self.product)

    @property
    def call_key(self) -> tuple[str, str | None]:
        """The call-grain key used by the product dimension (fact_checker.py:1080)."""
        return (self.call_id, self.phone)


def from_row(row: dict) -> Record:
    """Build a Record from a raw CSV/dict row, applying every normalisation."""
    parse_ok_raw = row.get("parse_ok")
    if parse_ok_raw is None or str(parse_ok_raw).strip() == "":
        parse_ok = True
    else:
        parse_ok = str(parse_ok_raw).strip().lower() in {"true", "1", "yes"}
    return Record(
        call_id=str(row["call_id"]).strip(),
        phone=norm_phone(row.get("phone_number")),
        product=norm_text(row.get("product")),
        call_result=norm_text(row.get("call_result")),
        reasons=parse_reasons(row),
        parse_ok=parse_ok,
    )
