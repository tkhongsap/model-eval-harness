"""Version pins, checked against production's own requirements.txt.

Not cosmetic. Under pandas 3.x a string column's dtype is `str`, so `pre_process`'s
`dtype == 'object'` guard (fact_checker.py:734, :738) is False, normalisation never
fires, and `call_result` accuracy collapses from 0.75 to 0.25 with every weight
zeroed. A harness running on the wrong pandas silently scores a different thing.

This reads production's file rather than hardcoding versions, so a True upgrade
surfaces here instead of as a mysterious differential failure.
"""

from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PROD_REQ = (
    ROOT.parent / "source-code-review" / "sentiment-batch-retention-main" / "requirements.txt"
)
PINNED = ("pandas", "numpy", "openpyxl")


def _parse(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        m = re.match(r"^\s*([A-Za-z0-9_.\-]+)==(\S+)", line)
        if m and m.group(1).lower() in PINNED:
            out[m.group(1).lower()] = m.group(2)
    return out


def production_pins() -> dict[str, str]:
    if not PROD_REQ.exists():  # pragma: no cover
        pytest.skip(f"production requirements.txt not found at {PROD_REQ}")
    return _parse(PROD_REQ.read_text(encoding="utf-8"))


def test_production_declares_the_pins_we_care_about():
    pins = production_pins()
    assert set(pins) == set(PINNED), f"expected {PINNED} in production's requirements, got {pins}"


@pytest.mark.parametrize("package", PINNED)
def test_our_requirements_match_production(package):
    ours = _parse((ROOT / "requirements.txt").read_text(encoding="utf-8"))
    assert ours.get(package) == production_pins()[package], (
        f"{package}: our requirements.txt says {ours.get(package)!r}, "
        f"production pins {production_pins()[package]!r}"
    )


@pytest.mark.parametrize("package", PINNED)
def test_installed_version_matches_the_pin(package):
    """The gate. Fails loudly on a mismatched interpreter rather than scoring
    a different thing quietly.

    Demonstrated failing: running this suite under the system interpreter
    (pandas 3.0.5) fails exactly here, naming the package and both versions.
    """
    expected = production_pins()[package]
    installed = importlib.import_module(package).__version__
    assert installed == expected, (
        f"{package} {installed} is installed but production pins {expected}. "
        f"Interpreter: {sys.executable}. "
        "Under a mismatched pandas the pre_process dtype guard stops firing and "
        "call_result accuracy collapses from 0.75 to 0.25. Install the pins: "
        "pip install -r requirements.txt"
    )
