"""Offline tests for the integer env helpers.

``str_to_int`` is the strict layer (raises), ``get_env_int`` the fail-soft env
wrapper (swallows into the default) -- the same split ``str_to_bool`` /
``get_env_bool`` already establish.
"""

from __future__ import annotations

import pytest

from src.utils.common import get_env_int
from src.utils.data import str_to_int

ENV = "TEST_UTILS_ENV_INT"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("8", 8), (" 8 ", 8), ("-2", -2), ("0", 0)],
)
def test_str_to_int_parses(raw, expected):
    assert str_to_int(raw) == expected


@pytest.mark.parametrize("raw", ["eight", "8.5", "", "  ", "1_0.0"])
def test_str_to_int_raises_on_non_integers(raw):
    with pytest.raises(ValueError):
        str_to_int(raw)


def test_get_env_int_unset_returns_default(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    assert get_env_int(ENV, 7) == 7


def test_get_env_int_parses_value(monkeypatch):
    monkeypatch.setenv(ENV, "3")
    assert get_env_int(ENV, 7) == 3


def test_get_env_int_unparseable_returns_default(monkeypatch):
    monkeypatch.setenv(ENV, "junk")
    assert get_env_int(ENV, 7) == 7


def test_get_env_int_negative_passes_through(monkeypatch):
    # Range policy (e.g. a concurrency floor) belongs to the caller, not here.
    monkeypatch.setenv(ENV, "-2")
    assert get_env_int(ENV, 7) == -2
