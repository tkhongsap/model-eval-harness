"""
Tests for output_validation models to boost coverage:
  - campaign_ratio.py  (73% → 80%+)
  - sales_performance.py (53% → 80%+)
  - common.py output_validation (88% → 80%+ already, adding extra for common.py missing branch)
"""

import pytest

from tasks.sentiment_telesale.output_validation.common.campaign_ratio import CampaignRatio
from tasks.sentiment_telesale.output_validation.common.sales_performance import SalesPerformance

# ---------------------------------------------------------------------------
# CampaignRatio validator tests (covers lines 23-25: decimal_limit body)
# ---------------------------------------------------------------------------


class TestCampaignRatio:
    """Tests for CampaignRatio Pydantic model."""

    def test_decimal_limit_rounds_values(self):
        """decimal_limit validator rounds main and other to 2 decimal places (lines 23-25)."""
        ratio = CampaignRatio(main=0.756, other=0.244)  # unambiguously round to .76 / .24
        assert ratio.main == 0.76
        assert ratio.other == 0.24

    def test_already_rounded_values_unchanged(self):
        """Values already at 2 dp come through unchanged."""
        ratio = CampaignRatio(main=0.75, other=0.25)
        assert ratio.main == 0.75
        assert ratio.other == 0.25

    def test_zero_values(self):
        """Zero values remain zero after rounding."""
        ratio = CampaignRatio(main=0.0, other=0.0)
        assert ratio.main == 0.0
        assert ratio.other == 0.0

    def test_full_main(self):
        """main=1.0, other=0.0 should be stored correctly."""
        ratio = CampaignRatio(main=1.0, other=0.0)
        assert ratio.main == 1.0
        assert ratio.other == 0.0

    def test_long_decimal_truncated(self):
        """Long float is rounded to 2dp."""
        ratio = CampaignRatio(main=0.33333333, other=0.66666667)
        assert ratio.main == 0.33
        assert ratio.other == 0.67


# ---------------------------------------------------------------------------
# SalesPerformance validator tests
# Lines 52-54: enforce_mutual_exclusion body (overlap removal)
# Lines 63-69: product_list_empty when crosssell list is empty
# Lines 77-86: product_list_empty when main list is empty
# ---------------------------------------------------------------------------


class TestSalesPerformance:
    """Tests for SalesPerformance Pydantic model validators."""

    def _make(self, **kwargs):
        defaults = {
            "main_package_offered": 1,
            "main_package_accepted": 1,
            "upsell_add_on_package_offered": 0,
            "upsell_add_on_package_accepted": 0,
            "crosssell_add_on_product_offered": 0,
            "crosssell_add_on_product_accepted": 0,
            "main_and_upsell_add_on_product_offered_list": [],
            "crosssell_add_on_product_offered_list": [],
        }
        defaults.update(kwargs)
        return SalesPerformance(**defaults)

    # --- dedup_list validator (lines 48-50) ---
    def test_dedup_removes_duplicates(self):
        """dedup_list removes duplicate entries from both lists."""
        sp = self._make(
            main_and_upsell_add_on_product_offered_list=["Mobile", "Mobile"],
            crosssell_add_on_product_offered_list=["TOL", "TOL"],
        )
        assert len(sp.main_and_upsell_add_on_product_offered_list) == 1
        assert len(sp.crosssell_add_on_product_offered_list) == 1

    # --- enforce_mutual_exclusion validator (lines 52-54) ---
    def test_mutual_exclusion_removes_overlap(self):
        """enforce_mutual_exclusion removes categories from crosssell that overlap with main (lines 52-54)."""
        sp = self._make(
            main_and_upsell_add_on_product_offered_list=["Mobile"],
            crosssell_add_on_product_offered_list=["Mobile", "TOL"],
            crosssell_add_on_product_offered=2,
            crosssell_add_on_product_accepted=1,
        )
        # 'Mobile' should be removed from crosssell list since it's in main list
        assert "Mobile" not in sp.crosssell_add_on_product_offered_list
        assert "TOL" in sp.crosssell_add_on_product_offered_list

    def test_mutual_exclusion_all_overlap_clears_crosssell(self):
        """When all crosssell categories overlap with main, crosssell list becomes empty."""
        sp = self._make(
            main_and_upsell_add_on_product_offered_list=["Mobile", "TOL"],
            crosssell_add_on_product_offered_list=["Mobile", "TOL"],
            crosssell_add_on_product_offered=2,
            crosssell_add_on_product_accepted=1,
        )
        assert sp.crosssell_add_on_product_offered_list == []
        # product_list_empty then resets counts to 0
        assert sp.crosssell_add_on_product_offered == 0
        assert sp.crosssell_add_on_product_accepted == 0

    def test_no_overlap_lists_unchanged(self):
        """When there is no overlap, both lists remain unchanged."""
        sp = self._make(
            main_and_upsell_add_on_product_offered_list=["Mobile"],
            crosssell_add_on_product_offered_list=["TOL"],
        )
        assert "Mobile" in sp.main_and_upsell_add_on_product_offered_list
        assert "TOL" in sp.crosssell_add_on_product_offered_list

    # --- product_list_empty validator – crosssell empty (lines 63-69) ---
    def test_crosssell_empty_resets_counts(self):
        """product_list_empty resets crosssell counts to 0 when list is empty (lines 63-69)."""
        sp = self._make(
            crosssell_add_on_product_offered_list=[],
            crosssell_add_on_product_offered=3,
            crosssell_add_on_product_accepted=2,
        )
        assert sp.crosssell_add_on_product_offered == 0
        assert sp.crosssell_add_on_product_accepted == 0

    def test_crosssell_non_empty_keeps_counts(self):
        """product_list_empty preserves counts when crosssell list is non-empty."""
        sp = self._make(
            main_and_upsell_add_on_product_offered_list=["Mobile"],
            crosssell_add_on_product_offered_list=["TOL"],
            crosssell_add_on_product_offered=1,
            crosssell_add_on_product_accepted=1,
        )
        assert sp.crosssell_add_on_product_offered == 1
        assert sp.crosssell_add_on_product_accepted == 1

    # --- product_list_empty validator – main list empty (lines 77-86) ---
    def test_main_list_empty_resets_all_main_counts(self):
        """product_list_empty resets all main/upsell counts to 0 when main list is empty (lines 77-86)."""
        sp = self._make(
            main_and_upsell_add_on_product_offered_list=[],
            main_package_offered=2,
            main_package_accepted=1,
            upsell_add_on_package_offered=3,
            upsell_add_on_package_accepted=2,
        )
        assert sp.main_package_offered == 0
        assert sp.main_package_accepted == 0
        assert sp.upsell_add_on_package_offered == 0
        assert sp.upsell_add_on_package_accepted == 0

    def test_main_list_non_empty_keeps_counts(self):
        """product_list_empty preserves counts when main list is non-empty."""
        sp = self._make(
            main_and_upsell_add_on_product_offered_list=["Mobile"],
            main_package_offered=1,
            main_package_accepted=1,
        )
        assert sp.main_package_offered == 1
        assert sp.main_package_accepted == 1

    def test_defaults_all_zero(self):
        """All fields default to 0 / empty lists."""
        sp = SalesPerformance()
        assert sp.main_package_offered == 0
        assert sp.crosssell_add_on_product_offered_list == []


class TestAdditionalOutputValidation:
    def test_customer_insight_rejects_out_of_range_churn_score(self):
        from tasks.sentiment_telesale.output_validation.common.customer_insight import CustomerInsight

        with pytest.raises(Exception, match="Churn Risk Indicator must be percentage between 0 and 100"):
            CustomerInsight(churn_risk_indicator=101, customer_sentiment_emotional="Positive")

    def test_customer_identity_verification_none_resets_related_fields(self):
        from tasks.sentiment_telesale.output_validation.common.operations_and_professionalism import (
            CustomerIdentityVerification,
        )

        result = CustomerIdentityVerification(
            customer_verification=None,
            invalid_verification=True,
            missing_verification=True,
            support_detail="no sale",
        )

        assert result.invalid_verification is None
        assert result.missing_verification is None

    def test_customer_identity_verification_false_resets_related_fields(self):
        from tasks.sentiment_telesale.output_validation.common.operations_and_professionalism import (
            CustomerIdentityVerification,
        )

        result = CustomerIdentityVerification(
            customer_verification=False,
            invalid_verification=True,
            missing_verification=None,
            support_detail="not attempted",
        )

        assert result.invalid_verification is False
        assert result.missing_verification is False

    def test_customer_identity_verification_invalid_true_combo_raises(self):
        from tasks.sentiment_telesale.output_validation.common.operations_and_professionalism import (
            CustomerIdentityVerification,
        )

        with pytest.raises(Exception, match="Invalid combination"):
            CustomerIdentityVerification(
                customer_verification=True,
                invalid_verification=True,
                missing_verification=False,
                support_detail="invalid combo",
            )

    def test_cross_sell_upsell_none_resets_related_fields(self):
        from tasks.sentiment_telesale.output_validation.common.sales_effectiveness import CrossSellUpsell

        result = CrossSellUpsell(
            missed_crosssell_upsell=None,
            unclear_addon_separation_crosssell=True,
            inadequate_addon_disclosure_crosssell=True,
            support_detail="no opportunity",
        )

        assert result.unclear_addon_separation_crosssell is None
        assert result.inadequate_addon_disclosure_crosssell is None

    def test_cross_sell_upsell_true_requires_detail_fields(self):
        from tasks.sentiment_telesale.output_validation.common.sales_effectiveness import CrossSellUpsell

        payload = CrossSellUpsell.model_construct(
            missed_crosssell_upsell=True,
            unclear_addon_separation_crosssell=None,
            inadequate_addon_disclosure_crosssell=True,
            support_detail="missing details",
        )

        with pytest.raises(ValueError, match="cannot be None"):
            payload.validate_crosssell_fields()

    def test_promo_end_force_campaign_name_and_empty_builder_fallback(self):
        from tasks.sentiment_telesale.output_validation.promo_end import PromoEndValidation, build_promo_end_validation

        payload = PromoEndValidation.model_construct(campaign_name="wrong-value")

        assert payload.force_campaign_name().campaign_name == "13_True_Promo_End"
        assert build_promo_end_validation([]) is PromoEndValidation

    def test_build_promo_end_validation_creates_dynamic_checklist_model(self):
        from tasks.sentiment_telesale.output_validation.promo_end import build_promo_end_validation

        dynamic_model = build_promo_end_validation(["PromoNoPrice"])

        assert "check_list" in dynamic_model.model_fields
        assert dynamic_model is not None

    def test_check_list_tags_schema_normalizes_boolean_values(self):
        import pandas as pd

        from tasks.sentiment_telesale.schemas.check_list_schema import CheckListTagsSchema

        df = pd.DataFrame(
            {
                "commission_skill_code": [1, 1, 1, 1, 1],
                "item_no": ["1", "2", "3", "4", "5"],
                "tag_code": ["A", "B", "C", "D", "E"],
                "rule_and_logic": ["r"] * 5,
                "positive_example_th": ["p"] * 5,
                "negative_example_th": ["n"] * 5,
                "is_active": [pd.NA, True, "false", "maybe", "y"],
            }
        )

        result = CheckListTagsSchema.coalesce_blank_to_null(df.copy())

        assert pd.isna(result.loc[0, "is_active"])
        assert result.loc[1, "is_active"] is True
        assert result.loc[2, "is_active"] is False
        assert result.loc[3, "is_active"] == "maybe"
        assert result.loc[4, "is_active"] is True
