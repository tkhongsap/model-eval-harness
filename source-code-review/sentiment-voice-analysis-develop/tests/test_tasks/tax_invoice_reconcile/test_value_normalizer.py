"""Tests for the per-field-type value normalizer used by the fact-check comparison."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pandas as pd
import pytest

from tasks.tax_invoice_reconcile.helper.constant import (
    COMPARE_AMOUNT,
    COMPARE_BOOL,
    COMPARE_DATE,
    COMPARE_TAXID,
    COMPARE_TEXT,
    NA_SENTINEL,
)
from tasks.tax_invoice_reconcile.module.value_normalizer import ValueNormalizer


@pytest.fixture
def normalizer() -> ValueNormalizer:
    return ValueNormalizer()


class TestNullHandling:
    @pytest.mark.parametrize("value", [None, float("nan"), pd.NA, pd.NaT])
    def test_null_values_normalize_to_na_sentinel(self, normalizer, value):
        assert normalizer.normalize(value, COMPARE_TEXT) == NA_SENTINEL


class TestText:
    def test_text_nfc_strips_all_whitespace_and_lowercases(self, normalizer):
        # Aligned to reconcile's norm_text_sql: whitespace fully removed, not collapsed.
        assert normalizer.normalize("  Hello   World  ", COMPARE_TEXT) == "helloworld"

    def test_blank_text_is_na_sentinel(self, normalizer):
        assert normalizer.normalize("   ", COMPARE_TEXT) == NA_SENTINEL

    def test_zero_width_characters_are_stripped(self, normalizer):
        # ZWSP between AB and CD must not defeat the match.
        assert normalizer.normalize("AB​CD", COMPARE_TEXT) == "abcd"

    def test_nfc_equivalent_forms_agree(self, normalizer):
        # Decomposed ("e" + combining acute) vs precomposed ("é") normalize to the same string.
        decomposed = "café"  # e + U+0301 COMBINING ACUTE ACCENT
        precomposed = "café"  # é (U+00E9)
        assert normalizer.normalize(decomposed, COMPARE_TEXT) == normalizer.normalize(precomposed, COMPARE_TEXT)
        assert normalizer.normalize(decomposed, COMPARE_TEXT) == "café"


class TestTaxId:
    def test_taxid_keeps_digits_and_left_pads_to_13(self, normalizer):
        assert normalizer.normalize("1-2345", COMPARE_TAXID) == "0000000012345"

    def test_taxid_already_13_digits_unchanged(self, normalizer):
        assert normalizer.normalize("0105553045044", COMPARE_TAXID) == "0105553045044"

    def test_taxid_no_digits_is_na_sentinel(self, normalizer):
        assert normalizer.normalize("N/A", COMPARE_TAXID) == NA_SENTINEL


class TestAmount:
    def test_amount_strips_thousands_separator_and_formats_2dp(self, normalizer):
        assert normalizer.normalize("1,500.00", COMPARE_AMOUNT) == "1500.00"

    def test_amount_decimal_input_formats_2dp(self, normalizer):
        assert normalizer.normalize(Decimal("104"), COMPARE_AMOUNT) == "104.00"

    def test_amount_float_and_string_forms_agree(self, normalizer):
        assert normalizer.normalize(7.0, COMPARE_AMOUNT) == normalizer.normalize("7.00", COMPARE_AMOUNT)


class TestDate:
    def test_ddmmyyyy_string_parses_to_iso(self, normalizer):
        assert normalizer.normalize("02/03/2026", COMPARE_DATE) == "2026-03-02"

    def test_date_object_renders_iso(self, normalizer):
        assert normalizer.normalize(date(2026, 3, 2), COMPARE_DATE) == "2026-03-02"

    def test_datetime_object_renders_iso_date_only(self, normalizer):
        assert normalizer.normalize(datetime(2026, 3, 2, 15, 30), COMPARE_DATE) == "2026-03-02"

    def test_unparseable_date_is_na_sentinel(self, normalizer):
        assert normalizer.normalize("not-a-date", COMPARE_DATE) == NA_SENTINEL


class TestBool:
    @pytest.mark.parametrize("value", ["Yes", "true", "T", True, "1"])
    def test_truthy_tokens_normalize_to_true(self, normalizer, value):
        assert normalizer.normalize(value, COMPARE_BOOL) == "true"

    @pytest.mark.parametrize("value", ["No", "false", "F", False, "0"])
    def test_falsy_tokens_normalize_to_false(self, normalizer, value):
        assert normalizer.normalize(value, COMPARE_BOOL) == "false"

    def test_unknown_token_is_na_sentinel(self, normalizer):
        assert normalizer.normalize("maybe", COMPARE_BOOL) == NA_SENTINEL
