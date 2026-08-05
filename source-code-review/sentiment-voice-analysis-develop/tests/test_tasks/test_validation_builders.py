"""
Tests for validation builder functions (build_*_validation) and force_campaign_name methods.
Covers the dynamic Pydantic model creation and campaign name enforcement.
"""
import pytest
from pydantic import ValidationError

from tasks.sentiment_telesale.output_validation.postpaid_upsell import (
    PostpaidUpsellValidation, build_postpaid_upsell_validation,
)
from tasks.sentiment_telesale.output_validation.true_cvg_digital import (
    CvgDigitalValidation, build_cvg_digital_validation,
)
from tasks.sentiment_telesale.output_validation.true_cvg_pos import (
    CvgPostValidation, build_cvg_post_validation,
)
from tasks.sentiment_telesale.output_validation.true_p2p_m1 import (
    P2PM1Validation, build_p2p_m1_validation,
)
from tasks.sentiment_telesale.output_validation.true_p2p_m2 import (
    P2PM2Validation, build_p2p_m2_validation,
)
from tasks.sentiment_telesale.output_validation.true_utol import (
    UtolValidation, build_utol_validation,
)
from tasks.sentiment_telesale.output_validation.promo_end import (
    PromoEndValidation, build_promo_end_validation,
)


# ---- Parametrized data for all 7 validation classes ----
VALIDATION_CONFIGS = [
    (PostpaidUpsellValidation, build_postpaid_upsell_validation,
     'Postpaid Upsell', ['PromoendNobenefit', 'PromoNoPrice']),
    (CvgDigitalValidation, build_cvg_digital_validation,
     '01_True_CVG_DIGITAL', ['CVGContract', 'CVGReserve']),
    (CvgPostValidation, build_cvg_post_validation,
     '03_True_CVG_Post', ['CVGPOSTContract', 'CVGPOSTReserve']),
    (P2PM1Validation, build_p2p_m1_validation,
     '08_True_P2P_M1', ['P2PM1NoProduct', 'P2PM1NoSpeed']),
    (P2PM2Validation, build_p2p_m2_validation,
     '09_True_P2P_M2', ['P2PM2NoProduct', 'P2PM2NoSpeed']),
    (UtolValidation, build_utol_validation,
     '10_True_UTOL', ['UPTOLNoProduct', 'UPTOLNoSpeed']),
    (PromoEndValidation, build_promo_end_validation,
     '13_True_Promo_End', ['PromoendNobenefit', 'PromoNoPrice']),
]

VALIDATION_IDS = [
    'PostpaidUpsell', 'CvgDigital', 'CvgPost',
    'P2PM1', 'P2PM2', 'Utol', 'PromoEnd',
]


class TestBuildValidationEmptyTags:
    """build_*_validation([]) should return the static class unchanged."""

    @pytest.mark.parametrize(
        "static_cls, builder, campaign, tags", VALIDATION_CONFIGS, ids=VALIDATION_IDS,
    )
    def test_empty_tags_returns_static_class(self, static_cls, builder, campaign, tags):
        result = builder([])
        assert result is static_cls

    @pytest.mark.parametrize(
        "static_cls, builder, campaign, tags", VALIDATION_CONFIGS, ids=VALIDATION_IDS,
    )
    def test_none_tags_returns_static_class(self, static_cls, builder, campaign, tags):
        result = builder(None)
        assert result is static_cls


class TestBuildValidationDynamic:
    """build_*_validation(tag_codes) should return a dynamic model constrained to those tags."""

    @pytest.mark.parametrize(
        "static_cls, builder, campaign, tags", VALIDATION_CONFIGS, ids=VALIDATION_IDS,
    )
    def test_dynamic_model_is_different_from_static(self, static_cls, builder, campaign, tags):
        dynamic_cls = builder(tags)
        assert dynamic_cls is not static_cls

    @pytest.mark.parametrize(
        "static_cls, builder, campaign, tags", VALIDATION_CONFIGS, ids=VALIDATION_IDS,
    )
    def test_dynamic_model_has_check_list_field(self, static_cls, builder, campaign, tags):
        dynamic_cls = builder(tags)
        assert 'check_list' in dynamic_cls.model_fields
        assert 'campaign_name' in dynamic_cls.model_fields

    @pytest.mark.parametrize(
        "static_cls, builder, campaign, tags", VALIDATION_CONFIGS, ids=VALIDATION_IDS,
    )
    def test_dynamic_model_single_tag(self, static_cls, builder, campaign, tags):
        """Build with a single tag code."""
        single_tag = [tags[0]]
        dynamic_cls = builder(single_tag)
        assert dynamic_cls is not static_cls
        assert 'check_list' in dynamic_cls.model_fields


class TestForceCampaignName:
    """force_campaign_name() should set campaign_name and return self."""

    @pytest.mark.parametrize(
        "static_cls, builder, campaign, tags", VALIDATION_CONFIGS, ids=VALIDATION_IDS,
    )
    def test_force_campaign_name_sets_value(self, static_cls, builder, campaign, tags):
        # Construct a minimal valid-enough instance via construct (skip validation)
        instance = static_cls.model_construct(
            campaign_name="wrong_name",
            check_list=None,
            campaign_ratio=None,
            call_status="Completed",
            operations_and_professionalism=None,
            sales_effectiveness=None,
            customer_experience=None,
            compliance=None,
            agent_strength="",
            agent_weakness="",
            sales_performance=None,
            customer_insight=None,
        )
        result = instance.force_campaign_name()
        assert instance.campaign_name == campaign
        assert result is instance

    @pytest.mark.parametrize(
        "static_cls, builder, campaign, tags", VALIDATION_CONFIGS, ids=VALIDATION_IDS,
    )
    def test_force_campaign_name_is_model_validator(self, static_cls, builder, campaign, tags):
        """force_campaign_name must be registered as a @model_validator, not just a plain method."""
        validators = static_cls.__pydantic_decorators__.model_validators
        assert 'force_campaign_name' in validators

    @pytest.mark.parametrize(
        "static_cls, builder, campaign, tags", VALIDATION_CONFIGS, ids=VALIDATION_IDS,
    )
    def test_dynamic_model_inherits_force_campaign_name(self, static_cls, builder, campaign, tags):
        """Dynamic model from build_* should inherit force_campaign_name from the specific validation class."""
        dynamic_cls = builder(tags)
        assert hasattr(dynamic_cls, 'force_campaign_name')
