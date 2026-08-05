from tasks.sentiment_telesale.output_validation.postpaid_upsell import (
    PostpaidUpsellValidation,
    build_postpaid_upsell_validation,
)
from tasks.sentiment_telesale.output_validation.promo_end import PromoEndValidation, build_promo_end_validation
from tasks.sentiment_telesale.output_validation.true_cvg_digital import (
    CvgDigitalValidation,
    build_cvg_digital_validation,
)
from tasks.sentiment_telesale.output_validation.true_cvg_pos import CvgPostValidation, build_cvg_post_validation
from tasks.sentiment_telesale.output_validation.true_p2p_m1 import P2PM1Validation, build_p2p_m1_validation
from tasks.sentiment_telesale.output_validation.true_p2p_m2 import P2PM2Validation, build_p2p_m2_validation
from tasks.sentiment_telesale.output_validation.true_utol import UtolValidation, build_utol_validation


class ValidationMapping:
    validate_mapping = {
        "01_True_CVG_DIGITAL": (CvgDigitalValidation, build_cvg_digital_validation),
        "03_True_CVG_Post": (CvgPostValidation, build_cvg_post_validation),
        "08_True_P2P_M1": (P2PM1Validation, build_p2p_m1_validation),
        "09_True_P2P_M2": (P2PM2Validation, build_p2p_m2_validation),
        "10_True_UTOL": (UtolValidation, build_utol_validation),
        "13_True_Promo_End": (PromoEndValidation, build_promo_end_validation),
        "Postpaid Upsell": (PostpaidUpsellValidation, build_postpaid_upsell_validation),
    }
