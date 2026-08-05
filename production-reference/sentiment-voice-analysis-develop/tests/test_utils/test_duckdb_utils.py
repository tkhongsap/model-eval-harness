"""Tests for :mod:`src.utils.duckdb_utils`.

Regression coverage for the DuckDB decimal-inference overflow: a pandas ``object`` column
of ``decimal.Decimal`` values is typed from a strided 1000-row sample, so a large value
that falls between stride points under-sizes the inferred DECIMAL and the full-column scan
overflows. ``connect_decimal_safe`` forces a full-column scan so the type always fits.
"""

from __future__ import annotations

import decimal

import duckdb
import pandas as pd
import pytest
from pandera.engines import pandas_engine

from src.utils.duckdb_utils import connect_decimal_safe

_BIG_AMOUNT = decimal.Decimal("15902230.00")  # 8 integer digits — overflows DECIMAL(9,2)


def _frame_with_offstride_big_value() -> pd.DataFrame:
    """Build a >1000-row Decimal frame whose only large value sits off the sample stride."""
    money = pandas_engine.Decimal(precision=18, scale=2)
    values = [decimal.Decimal("100.00")] * 5000
    values[4999] = _BIG_AMOUNT  # last row, missed by the strided 1000-row sample
    return pd.DataFrame({"AMT": money.coerce(pd.Series(values, dtype=object))})


def test_connect_decimal_safe_preserves_large_offstride_decimal():
    # Arrange
    df = _frame_with_offstride_big_value()
    con = connect_decimal_safe()
    con.register("t", df)

    # Act
    result = con.execute("SELECT MAX(AMT) FROM t").fetchone()[0]

    # Assert
    assert result == _BIG_AMOUNT


def test_plain_connect_overflows_on_large_offstride_decimal():
    # Arrange — documents the bug the helper fixes: the default connection raises.
    df = _frame_with_offstride_big_value()
    con = duckdb.connect()
    con.register("t", df)

    # Act / Assert
    with pytest.raises(duckdb.InvalidInputException):
        con.execute("SELECT MAX(AMT) FROM t").fetchone()
