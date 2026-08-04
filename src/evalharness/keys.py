"""Item keys, and the refusal that keeps customer identifiers out of reports.

`phone_number` cannot simply be dropped: it is part of the merge key
(fact_checker.py:1075) and the product groupby key (:1080). So it is hashed, not
omitted, and the hash is computed identically on the ground truth and on every arm.
A mismatch would be indistinguishable from a missing prediction and would read as a
false regression.

The HMAC key never lives in this repository. True's team keeps it: when the review
needs to hear a specific call, they resolve the hash inside their own systems.
"""

from __future__ import annotations

import hashlib
import hmac
import os

from .records import Record

_ENV_KEY = "EVAL_HARNESS_KEY_HMAC"

# Columns that must never reach a shareable artifact.
FORBIDDEN_COLUMNS = frozenset(
    {"phone_number", "phone", "msisdn", "customer_name", "agent_name", "raw_output"}
)


class ForbiddenColumn(RuntimeError):
    """Raised when a writer is handed a customer identifier."""


def hmac_key() -> str:
    key = os.environ.get(_ENV_KEY)
    if not key:
        raise RuntimeError(
            f"{_ENV_KEY} is not set. The item key is an HMAC, not a bare hash, so a "
            "key is required. Never commit it: True's team holds it and resolves "
            "hashes back to calls inside their systems."
        )
    return key


def item_key(record: Record, key: str) -> str:
    """HMAC-SHA256 over the normalised join key. Truncated to 16 hex chars: enough
    to be unique across a workbook, short enough to read in a review."""
    payload = f"{record.call_id}|{record.phone or ''}|{record.product or ''}"
    return hmac.new(key.encode(), payload.encode(), hashlib.sha256).hexdigest()[:16]


def assert_shareable(columns: list[str]) -> None:
    """Refuse a shareable artifact carrying a customer identifier.

    A rule that lives only in a docstring is a rule that gets forgotten under
    deadline. This raises.
    """
    offending = sorted(set(c.strip().lower() for c in columns) & FORBIDDEN_COLUMNS)
    if offending:
        raise ForbiddenColumn(
            f"refusing to write a shareable artifact containing {offending}. "
            "Use item_key instead; the private mapping stays in the data directory."
        )
