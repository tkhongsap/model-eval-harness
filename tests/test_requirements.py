"""Version pins.

Not cosmetic. Under pandas 3.x a string column's dtype is `str`, so the production
scorer's `dtype == 'object'` guard (fact_checker.py:734, :738) is False, string
normalisation never fires, and `call_result` accuracy collapses from 0.75 to 0.25
with every weight zeroed. A harness on the wrong pandas silently scores a different
thing.

Two checks, deliberately split, because they have different availability:

  * **installed == our requirements.txt** -- ALWAYS runs. This is the gate. It
    depends on nothing outside this repository.
  * **our requirements.txt == production's** -- skips when True's source tree is not
    present, which is the normal case for a standalone checkout.

The split was found by extracting this repo. The earlier version resolved
production's requirements.txt by a hardcoded relative path, so the moment the
harness left its original parent directory the gate skipped permanently. A gate that
silently stops running is worse than no gate at all.
"""

from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

PINNED = ("pandas", "numpy", "openpyxl")


def _parse(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        m = re.match(r"^\s*([A-Za-z0-9_.\-]+)==(\S+)", line)
        if m and m.group(1).lower() in PINNED:
            out[m.group(1).lower()] = m.group(2)
    return out


def our_pins() -> dict[str, str]:
    return _parse((ROOT / "requirements.txt").read_text(encoding="utf-8"))


def production_pins() -> dict[str, str]:
    """Production's own requirements.txt, located via TRUE_SOURCE_ROOT."""
    from production_ref import source_root  # noqa: PLC0415

    path = source_root() / "requirements.txt"
    if not path.exists():
        pytest.skip(
            f"production requirements.txt not found at {path}. Set TRUE_SOURCE_ROOT "
            "to cross-check our pins against production's. The installed-version "
            "gate below runs regardless."
        )
    return _parse(path.read_text(encoding="utf-8"))


# --- the gate: always runs, needs nothing outside this repository -------------

def test_our_requirements_declare_every_pin():
    assert set(our_pins()) == set(PINNED), (
        f"requirements.txt must pin {PINNED}, found {sorted(our_pins())}"
    )


@pytest.mark.parametrize("package", PINNED)
def test_installed_version_matches_our_pin(package):
    """Fails loudly on a mismatched interpreter rather than quietly scoring a
    different thing.

    Demonstrated failing before being trusted: run under an interpreter with a
    different pandas and this names the package, both versions, and the consequence.
    """
    expected = our_pins()[package]
    installed = importlib.import_module(package).__version__
    assert installed == expected, (
        f"{package} {installed} is installed but this harness pins {expected}. "
        f"Interpreter: {sys.executable}. "
        "Under a mismatched pandas the pre_process dtype guard stops firing and "
        "call_result accuracy collapses from 0.75 to 0.25. Fix with: "
        "pip install -r requirements.txt"
    )


# --- the cross-check: skips without the production tree -----------------------

@pytest.mark.parametrize("package", PINNED)
def test_our_pins_match_production(package):
    """Our pins must equal production's. Skips when the source tree is absent."""
    prod = production_pins()
    assert our_pins().get(package) == prod[package], (
        f"{package}: we pin {our_pins().get(package)!r}, production pins "
        f"{prod[package]!r}. Production upgraded: re-read the diff and re-run the "
        "differential test before following."
    )
