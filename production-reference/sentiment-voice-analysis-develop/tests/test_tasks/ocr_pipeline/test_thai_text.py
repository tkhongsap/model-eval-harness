"""Tests for :func:`normalize_thai_text` (Thai PDF text-layer PUA sanitizer).

PUA codepoints are written as ``\\uf7xx`` escapes on purpose — they are invisible (or render
as boxes) as literals, so escapes keep the fixtures reviewable.
"""

from __future__ import annotations

from tasks.ocr_tax_invoice_pipeline.helper.thai_text import normalize_thai_text


class TestPuaGlyphMapping:
    def test_low_mai_tho_maps_to_standard_tone_mark(self):
        # Arrange: the real 2026-07-08 DOC_NAME leak (U+F70B = low mai tho).
        raw = "ต\uf70bนฉบับ"  # ต + PUA ้ + นฉบับ

        # Act / Assert
        assert normalize_thai_text(raw) == "ต้นฉบับ"

    def test_low_thanthakhat_maps_in_vendor_name(self):
        # U+F70E = low thanthakhat, as leaked on the Scenario-7 AWN receipt.
        raw = "บริษัท แอดวานซ\uf70e ไวร\uf70eเลส เน็ทเวอร\uf70eค จำกัด"

        assert normalize_thai_text(raw) == "บริษัท แอดวานซ์ ไวร์เลส เน็ทเวอร์ค จำกัด"

    def test_low_mai_ek_maps_after_tall_consonant(self):
        # U+F70A = low mai ek.
        assert normalize_thai_text("ใหญ\uf70a") == "ใหญ่"

    def test_left_shifted_variants_map_to_same_marks(self):
        # F701 = left-shifted sara i, F713 = left-shifted mai ek — both map to standard marks.
        assert normalize_thai_text("ป\uf701\uf713") == "ปิ่"

    def test_unmapped_private_use_codepoint_is_dropped(self):
        assert normalize_thai_text("ABC\ue000กขค") == "ABCกขค"


class TestSaraAmRecomposition:
    def test_decomposed_sara_am_recomposes(self):
        # Arrange: nikhahit (U+0E4D) + sara aa (U+0E32) as PDF text layers emit it, plus the
        # low mai ek PUA glyph — the real Scenario-7 CUSTOMER_BRANCH_NAME shape.
        raw = "สํานักงานใหญ\uf70a"

        assert normalize_thai_text(raw) == "สำนักงานใหญ่"

    def test_decomposed_sara_am_in_company_suffix(self):
        assert normalize_thai_text("จํากัด") == "จำกัด"

    def test_pua_nikhahit_recomposes_too(self):
        # F711 (left-shifted nikhahit) maps to U+0E4D first, then recomposes with า.
        assert normalize_thai_text("จ\uf711ากัด") == "จำกัด"


class TestFidelityPassthrough:
    def test_plain_thai_and_english_unchanged(self):
        values = [
            "คิสมอลล์",
            "บริษัท ซีนิเพล็กซ์ จำกัด",
            "38, 38/1-3, 39 Moo 6 Bangna-Trad Rd., Bangkaew, Bangplee, Samutprakarn, 10540",
            "เลขที่ 18 อาคารทรู ทาวเวอร์ ถนนรัชดาภิเษก แขวงห้วยขวาง เขตห้วยขวาง กรุงเทพมหานคร 10310",
        ]

        for v in values:
            assert normalize_thai_text(v) == v

    def test_interior_double_space_is_kept_verbatim(self):
        assert normalize_thai_text("อำเภอสันกำแพง  จังหวัดเชียงใหม่") == "อำเภอสันกำแพง  จังหวัดเชียงใหม่"

    def test_composed_sara_am_unchanged(self):
        assert normalize_thai_text("สำนักงานใหญ่") == "สำนักงานใหญ่"

    def test_non_string_inputs_pass_through(self):
        assert normalize_thai_text(None) is None
        assert normalize_thai_text(1234.5) == 1234.5
        assert normalize_thai_text(["ต\uf70bน"]) == ["ต\uf70bน"]


class TestControlCharCollapse:
    def test_newline_run_collapses_to_single_space(self):
        raw = "Revenue Sharing Dec' 25/Postpaid\nRevenue Sharing Dec' 25/Prepaid"

        assert normalize_thai_text(raw) == "Revenue Sharing Dec' 25/Postpaid Revenue Sharing Dec' 25/Prepaid"

    def test_spaces_around_control_chars_fold_into_one(self):
        assert normalize_thai_text("A \r\n\t B") == "A B"

    def test_leading_and_trailing_whitespace_trimmed(self):
        assert normalize_thai_text("  ABC \n") == "ABC"
