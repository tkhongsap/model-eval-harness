"""Hand-derived expectations for thai_num.py.

Every value below was written out from the Thai reading rules by hand and NOT by running
the module. CLAUDE.md's governing rule applies here in full:

    "When a test fails, ask whether the fixture is wrong before assuming the code is."

If one of these disagrees with the implementation, one of the two has found something.
Work out which, and write the answer down here -- do not edit an expectation so the test
goes green, because the expectation is the only independent check this module has.

The three rules the awkward cases exercise:
  * tens digit 1 is bare สิบ, never หนึ่งสิบ            -> 10, 110, 1_010
  * tens digit 2 is ยี่สิบ, never สองสิบ                -> 20, 25, 1_290
  * units digit 1 with any higher place is เอ็ด        -> 11, 21, 101, 1_001
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from thai_num import (  # noqa: E402
    read_baht,
    read_date,
    read_digits,
    read_gb,
    read_months,
    read_number,
    read_speed,
    read_year_be,
)

# --- cardinals ------------------------------------------------------------------------

CARDINALS = [
    (0, "ศูนย์"),
    (1, "หนึ่ง"),
    (5, "ห้า"),
    (9, "เก้า"),
    (10, "สิบ"),                       # bare สิบ
    (11, "สิบเอ็ด"),                    # เอ็ด
    (12, "สิบสอง"),
    (19, "สิบเก้า"),
    (20, "ยี่สิบ"),                     # ยี่สิบ
    (21, "ยี่สิบเอ็ด"),                  # both rules at once
    (25, "ยี่สิบห้า"),
    (30, "สามสิบ"),
    (99, "เก้าสิบเก้า"),
    (100, "หนึ่งร้อย"),
    (101, "หนึ่งร้อยเอ็ด"),              # เอ็ด across a zero tens
    (110, "หนึ่งร้อยสิบ"),
    (111, "หนึ่งร้อยสิบเอ็ด"),
    (199, "หนึ่งร้อยเก้าสิบเก้า"),
    (200, "สองร้อย"),
    (399, "สามร้อยเก้าสิบเก้า"),
    (599, "ห้าร้อยเก้าสิบเก้า"),
    (1_000, "หนึ่งพัน"),
    (1_001, "หนึ่งพันเอ็ด"),            # เอ็ด across two zeros
    (1_010, "หนึ่งพันสิบ"),
    (1_250, "หนึ่งพันสองร้อยห้าสิบ"),
    (1_290, "หนึ่งพันสองร้อยเก้าสิบ"),
    (2_569, "สองพันห้าร้อยหกสิบเก้า"),   # the Buddhist year for 2026
    (10_000, "หนึ่งหมื่น"),
    (100_000, "หนึ่งแสน"),
    (1_000_000, "หนึ่งล้าน"),
    (1_234_567, "หนึ่งล้านสองแสนสามหมื่นสี่พันห้าร้อยหกสิบเจ็ด"),
]


@pytest.mark.parametrize("n,expected", CARDINALS, ids=[str(n) for n, _ in CARDINALS])
def test_read_number(n: int, expected: str) -> None:
    assert read_number(n) == expected


# --- digit strings --------------------------------------------------------------------
# A mobile number is read one digit at a time. Getting this wrong is not cosmetic: the
# audio would say "eight hundred ten million..." while the reference transcript said the
# digits, and every WER figure computed against it would be measuring our own bug.


def test_read_digits_is_digit_by_digit() -> None:
    assert read_digits("0810000301") == (
        "ศูนย์ แปด หนึ่ง ศูนย์ ศูนย์ ศูนย์ ศูนย์ สาม ศูนย์ หนึ่ง"
    )


def test_read_digits_handles_a_short_id() -> None:
    assert read_digits("4821") == "สี่ แปด สอง หนึ่ง"


# --- money ----------------------------------------------------------------------------


def test_read_baht_whole() -> None:
    assert read_baht(599) == "ห้าร้อยเก้าสิบเก้าบาท"


def test_read_baht_with_satang() -> None:
    assert read_baht(1290.50) == "หนึ่งพันสองร้อยเก้าสิบบาทห้าสิบสตางค์"


def test_read_baht_drops_zero_satang() -> None:
    assert read_baht(1290.00) == "หนึ่งพันสองร้อยเก้าสิบบาท"


# --- dates ----------------------------------------------------------------------------


def test_read_year_be_converts_to_buddhist_era() -> None:
    assert read_year_be(2026) == "สองพันห้าร้อยหกสิบเก้า"


def test_read_date_full() -> None:
    assert read_date(2026, 8, 25) == "วันที่ ยี่สิบห้า สิงหาคม สองพันห้าร้อยหกสิบเก้า"


def test_read_date_without_year() -> None:
    assert read_date(2026, 1, 1, with_year=False) == "วันที่ หนึ่ง มกราคม"


# --- units ----------------------------------------------------------------------------


def test_read_speed() -> None:
    assert read_speed(300) == "สามร้อย เมกะบิต"


def test_read_months() -> None:
    assert read_months(12) == "สิบสอง เดือน"


def test_read_gb() -> None:
    assert read_gb(40) == "สี่สิบ กิกะไบต์"
