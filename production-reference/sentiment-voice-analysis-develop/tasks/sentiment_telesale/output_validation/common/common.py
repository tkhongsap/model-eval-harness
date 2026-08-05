from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tasks.sentiment_telesale.output_validation.common.campaign_ratio import CampaignRatio
from tasks.sentiment_telesale.output_validation.common.compliance import Compliance
from tasks.sentiment_telesale.output_validation.common.customer_experience import CustomerExperience
from tasks.sentiment_telesale.output_validation.common.customer_insight import CustomerInsight
from tasks.sentiment_telesale.output_validation.common.operations_and_professionalism import (
    OperationsAndProfessionalism,
)
from tasks.sentiment_telesale.output_validation.common.sales_effectiveness import SalesEffectiveness
from tasks.sentiment_telesale.output_validation.common.sales_performance import SalesPerformance


class Common(BaseModel):
    model_config = ConfigDict(extra="ignore")

    campaign_name: str = Field(..., description="Campaign identifier for the call evaluation.")

    campaign_ratio: CampaignRatio = Field(
        ...,
        description="[System Prompt: Campaign Ratio Analysis] Breakdown of conversation coverage - percentage discussing main campaign vs other topics. Fields 'main' and 'other' must sum to 1.0 (100%). Analyzes what portion of the call focuses on the primary campaign objective. See system prompt Campaign Ratio section for evaluation guidelines.",
    )

    call_status: Literal["Completed", "Abandoned"] = Field(
        ...,
        description="Identifies WHEN customer ended call in conversation flow. 'Completed' = call reached end stage (offer accepted/rejected, main business done, near closing - even if customer ended quickly). 'Abandoned' = customer hung up EARLY before conversation reached its purpose (e.g., during opening/consent, mid-presentation) OR technical issues caused immediate disconnection. Focus on timing, not closing quality.",
    )

    operations_and_professionalism: OperationsAndProfessionalism = Field(
        ...,
        description="[System Prompt: Main Category 1] Operations and Professionalism - Evaluates professional conduct, call structure, communication quality, and active listening. See system prompt Category 1 for complete criteria.",
    )

    sales_effectiveness: SalesEffectiveness = Field(
        ...,
        description="[System Prompt: Main Category 2] Sales Effectiveness - Evaluates needs analysis, offer presentation, objection handling, and sales closing. See system prompt Category 2 for complete criteria.",
    )

    customer_experience: CustomerExperience = Field(
        ...,
        description="[System Prompt: Main Category 3] Customer Experience - Evaluates positive experience creation, communication clarity, and trust building. See system prompt Category 3 for complete criteria.",
    )

    compliance: Compliance = Field(
        ...,
        description="[System Prompt: Main Category 4] Compliance - Evaluates adherence to legal, policy, and procedural requirements. See system prompt Category 4 for complete criteria.",
    )

    # Placeholder for subclasses to override with specific checklists
    check_list: str = Field(..., description="Mock field to be overridden in subclasses with specific checklists.")

    agent_strength: str = Field(
        ...,
        # max_length=800,
        description="Overall performance summary highlighting 2-4 key strengths demonstrated consistently across all evaluation categories (operations, sales, CX, compliance). Written in Thai with specific behavioral examples and redacted names [Agent]/[Customer]. Maximum 800 characters.",
    )

    agent_weakness: str = Field(
        ...,
        # max_length=800,
        description="Overall performance summary identifying 2-4 key improvement areas/weaknesses across all evaluation categories that impacted scores. Written in Thai with specific gaps/issues as constructive feedback and redacted names [Agent]/[Customer]. Maximum 800 characters.",
    )

    sales_performance: SalesPerformance = Field(
        ...,
        description="[System Prompt: Additional Category 1] Sell Performance - Evaluates key sales performance indicators such as main package offers/acceptances, upsell offers/acceptances, and cross-sell offers/acceptances. See system prompt Additional Category 1 for complete criteria.",
    )

    customer_insight: CustomerInsight = Field(
        ...,
        description="[System Prompt: Additional Category 2] Customer Insight - Evaluates customer-specific insights such as rejection reasons, network issues, churn risk, and sentiment. See system prompt Additional Category 2 for complete criteria.",
    )

    @model_validator(mode="after")
    def validate_data_privacy_compliance(self):
        if (
            self.operations_and_professionalism.customer_identity_verification.customer_verification is False
            or self.operations_and_professionalism.customer_identity_verification.invalid_verification is False
            or self.operations_and_professionalism.customer_identity_verification.missing_verification is False
        ):
            self.compliance.compliance.data_privacy_compliance = False
        return self

    @model_validator(mode="after")
    def sync_crosssell_upsell_with_25(self):
        missed = self.sales_effectiveness.cross_sell_upsell.missed_crosssell_upsell
        if missed is None or missed is False:
            # None  = no cross-sell/upsell opportunity existed
            # False = opportunity existed but agent didn't offer anything
            # Either way: no upsell or cross-sell was actually attempted
            sp = self.sales_performance
            sp.upsell_add_on_package_offered = 0
            sp.upsell_add_on_package_accepted = 0
            sp.crosssell_add_on_product_offered = 0
            sp.crosssell_add_on_product_accepted = 0
            sp.crosssell_add_on_product_offered_list = []
        return self
