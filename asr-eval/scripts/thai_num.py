"""Thai spoken-form readers for numbers, money, dates and phone numbers.

Why this exists at all: the reference transcript must be *what is actually said*, not the
digits that were in the template. A Thai speaker reading a mobile number says the digits
one at a time; a TTS voice handed "0810000301" will read it as a single enormous cardinal
and the reference transcript would then disagree with the audio it was generated from.

So every number is expanded to its spoken form BEFORE synthesis, and the digit form is
kept beside it as the entity's canonical `value`. Scoring compares the spoken form (WER)
and the canonical value (entity accuracy) separately.

The reading rules implemented here are standard Thai:

  * units 0-9                      ศูนย์ หนึ่ง สอง สาม สี่ ห้า หก เจ็ด แปด เก้า
  * tens digit 1 is bare สิบ       10 -> สิบ,        NOT หนึ่งสิบ
  * tens digit 2 is ยี่สิบ          20 -> ยี่สิบ,      NOT สองสิบ
  * units digit 1 after any        11 -> สิบเอ็ด,     101 -> หนึ่งร้อยเอ็ด
    higher place becomes เอ็ด      21 -> ยี่สิบเอ็ด
  * places                         สิบ ร้อย พัน หมื่น แสน ล้าน

These are asserted in tests/test_thai_num.py against hand-written expectations, written
before this module, in the discipline CLAUDE.md sets for retention_expected.csv.
"""

from __future__ import annotations

DIGITS = ("ศูนย์", "หนึ่ง", "สอง", "สาม", "สี่", "ห้า", "หก", "เจ็ด", "แปด", "เก้า")
PLACES = ("", "สิบ", "ร้อย", "พัน", "หมื่น", "แสน")

THAI_MONTHS = (
    "มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
    "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม",
)


def read_digits(s: str) -> str:
    """Digit-by-digit, the way a phone number or an ID is read aloud.

    >>> read_digits("0810000301")
    'ศูนย์ แปด หนึ่ง ศูนย์ ศูนย์ ศูนย์ ศูนย์ สาม ศูนย์ หนึ่ง'
    """
    out = []
    for ch in s:
        if ch.isdigit():
            out.append(DIGITS[int(ch)])
        elif ch in "-  ":
            continue
        else:
            out.append(ch)
    return " ".join(out)


def _read_under_million(n: int) -> str:
    """Read 0 <= n < 1_000_000 as a Thai cardinal."""
    if n == 0:
        return DIGITS[0]
    s = str(n)
    length = len(s)
    parts: list[str] = []
    for i, ch in enumerate(s):
        d = int(ch)
        place = length - i - 1          # 0 = units, 1 = tens, ...
        if d == 0:
            continue
        if place == 1:                  # tens
            if d == 1:
                parts.append("สิบ")
            elif d == 2:
                parts.append("ยี่สิบ")
            else:
                parts.append(DIGITS[d] + "สิบ")
        elif place == 0:                # units
            if d == 1 and length > 1:
                parts.append("เอ็ด")
            else:
                parts.append(DIGITS[d])
        else:
            parts.append(DIGITS[d] + PLACES[place])
    return "".join(parts)


def read_number(n: int) -> str:
    """Read a non-negative integer as a Thai cardinal, including ล้าน grouping."""
    if n < 0:
        return "ลบ" + read_number(-n)
    if n < 1_000_000:
        return _read_under_million(n)
    millions, rest = divmod(n, 1_000_000)
    head = read_number(millions) + "ล้าน"
    return head if rest == 0 else head + _read_under_million(rest)


def read_baht(amount: float | int) -> str:
    """Money, in the form a Thai agent actually says it.

    >>> read_baht(599)
    'ห้าร้อยเก้าสิบเก้าบาท'
    >>> read_baht(1290.5)
    'หนึ่งพันสองร้อยเก้าสิบบาทห้าสิบสตางค์'
    """
    baht = int(amount)
    satang = int(round((float(amount) - baht) * 100))
    out = read_number(baht) + "บาท"
    if satang:
        out += read_number(satang) + "สตางค์"
    return out


def read_year_be(gregorian_year: int) -> str:
    """Buddhist-era year, spoken. Thai call centres say พ.ศ. by default."""
    return read_number(gregorian_year + 543)


def read_date(year: int, month: int, day: int, with_year: bool = True) -> str:
    """'วันที่ ยี่สิบห้า สิงหาคม สองพันห้าร้อยหกสิบเก้า'"""
    out = f"วันที่ {read_number(day)} {THAI_MONTHS[month - 1]}"
    if with_year:
        out += f" {read_year_be(year)}"
    return out


def read_time(hour: int, minute: int) -> str:
    """Simple 24-hour reading ('สิบสี่นาฬิกาสามสิบนาที'), which is what an agent uses
    when quoting an appointment slot or an SLA."""
    out = f"{read_number(hour)}นาฬิกา"
    if minute:
        out += f"{read_number(minute)}นาที"
    return out


def read_speed(mbps: int) -> str:
    return f"{read_number(mbps)} เมกะบิต"


def read_months(n: int) -> str:
    return f"{read_number(n)} เดือน"


def read_gb(n: int) -> str:
    return f"{read_number(n)} กิกะไบต์"
