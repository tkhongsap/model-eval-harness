"""DuckDB helpers for safely scanning pandas frames that carry Decimal columns."""

from __future__ import annotations

import duckdb

# DuckDB infers a DECIMAL column's width/scale for a pandas ``object`` column of
# ``decimal.Decimal`` values from a strided sample (``pandas_analyze_sample``, default
# 1000 rows). When the largest-magnitude value falls between stride points the inferred
# type is too narrow (e.g. ``DECIMAL(9,2)``) and the full-column scan overflows. Sizing
# the sample beyond any realistic frame forces a full-column scan so the inferred DECIMAL
# always fits the data — exactly, with no float rounding.
_DECIMAL_SAFE_ANALYZE_SAMPLE = 1_000_000_000


def connect_decimal_safe() -> duckdb.DuckDBPyConnection:
    """Return an in-memory DuckDB connection that types pandas Decimal columns by a
    full-column scan instead of the default 1000-row strided sample.
    """
    con = duckdb.connect()
    con.execute(f"SET pandas_analyze_sample = {_DECIMAL_SAFE_ANALYZE_SAMPLE}")
    return con
