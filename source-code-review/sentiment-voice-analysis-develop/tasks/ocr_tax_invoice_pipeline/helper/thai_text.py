"""Normalize Thai text copied from PDF text layers into standard Unicode.

Digital Thai PDFs encode *positional glyph variants* of combining marks (tone marks shifted
for tall consonants, left-shifted upper vowels, descenderless ฐ/ญ) in the Private Use Area
U+F700–U+F71A — the Microsoft/Adobe Thai font convention. When the model reads such a page's
text layer verbatim instead of the rendered glyphs, those codepoints leak into the output,
render as boxes in the report, and depress the reconcile name matching. Every PUA glyph has
an exact standard-Thai equivalent, so the mapping below is lossless.
"""

from __future__ import annotations

import re
from typing import Any

# Thai PUA glyph variants → standard codepoints (Microsoft/Adobe Thai font convention).
_THAI_PUA_TO_STANDARD = str.maketrans(
    {
        0xF700: "ฐ",  # ฐ (descenderless glyph)
        0xF701: "ิ",  # ิ (left-shifted)
        0xF702: "ี",  # ี (left-shifted)
        0xF703: "ึ",  # ึ (left-shifted)
        0xF704: "ื",  # ื (left-shifted)
        0xF705: "่",  # ่ (low-left)
        0xF706: "้",  # ้ (low-left)
        0xF707: "๊",  # ๊ (low-left)
        0xF708: "๋",  # ๋ (low-left)
        0xF709: "์",  # ์ (low-left)
        0xF70A: "่",  # ่ (low)
        0xF70B: "้",  # ้ (low)
        0xF70C: "๊",  # ๊ (low)
        0xF70D: "๋",  # ๋ (low)
        0xF70E: "์",  # ์ (low)
        0xF70F: "ญ",  # ญ (descenderless glyph)
        0xF710: "ั",  # ั (left-shifted)
        0xF711: "ํ",  # ํ (left-shifted)
        0xF712: "็",  # ็ (left-shifted)
        0xF713: "่",  # ่ (left-shifted)
        0xF714: "้",  # ้ (left-shifted)
        0xF715: "๊",  # ๊ (left-shifted)
        0xF716: "๋",  # ๋ (left-shifted)
        0xF717: "์",  # ์ (left-shifted)
        0xF718: "ุ",  # ุ (low)
        0xF719: "ู",  # ู (low)
        0xF71A: "ฺ",  # ฺ (low)
    }
)


def normalize_thai_text(value: Any) -> Any:
    """Return ``value`` with PDF-text-layer artifacts mapped back to standard Thai.

    Applied as a ``mode="before"`` sanitizer on every string the model returns, so a non-str
    input (numbers, ``None``, lists) is passed through untouched. For strings:

    1. Map the Thai PUA glyph variants (U+F700–U+F71A) to their standard codepoints.
    2. Drop any remaining private-use codepoint — an unmapped PUA glyph has no text meaning
       and renders as a box.
    3. Recompose decomposed sara-am ``ํ`` + ``า`` (U+0E4D U+0E32) into ``ำ`` (U+0E33); runs
       after step 1 so a PUA nikhahit recomposes too.
    4. Collapse each run of control characters (newline/carriage-return/tab), together with
       any spaces touching it, into a single space, and trim leading/trailing whitespace —
       interior printed spacing stays verbatim.

    Args:
        value: Any parsed JSON value from the model response.

    Returns:
        The sanitized string, or the input unchanged when it is not a string.
    """
    if not isinstance(value, str):
        return value
    text = value.translate(_THAI_PUA_TO_STANDARD)
    text = "".join(ch for ch in text if not 0xE000 <= ord(ch) <= 0xF8FF)
    text = text.replace("ํา", "ำ")
    return re.sub(r" *+[\n\r\t]++ *+", " ", text).strip()
