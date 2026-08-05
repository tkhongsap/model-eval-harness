from pydantic import BaseModel, ConfigDict, Field, model_validator


class CustomerNeedsAnalysis(BaseModel):
    model_config = ConfigDict(extra="ignore")

    usage_based_analysis: bool = Field(
        ...,
        description="[System Prompt: 2.1.1] usage_based_analysis - Usage-based Analysis: Agent references customer's actual usage data for recommendations. See system prompt section 2.1.1 for complete evaluation criteria.",
    )

    benefit_highlight: bool = Field(
        ...,
        description="[System Prompt: 2.1.2] benefit_highlight - Benefit Highlight: Agent connects package features to specific customer benefits. See system prompt section 2.1.2 for complete evaluation criteria.",
    )

    support_detail: str = Field(
        ...,
        # max_length=400,
        description="Quote exact agent phrases. For False: cite what data was ignored + explain what criteria was not met. More False = more detail per finding. Maximum 400 characters (~100 tokens) - Vertex AI enforces during generation.",
    )


class OfferPresentationQuality(BaseModel):
    model_config = ConfigDict(extra="ignore")

    clarity_of_explanation: bool = Field(
        ...,
        description="[System Prompt: 2.2.1] clarity_of_explanation - Clarity of Explanation: Explanations are clear, organized, and easy to understand. See system prompt section 2.2.1 for complete evaluation criteria.",
    )

    customer_benefit_highlight: bool = Field(
        ...,
        description="[System Prompt: 2.2.2] customer_benefit_highlight - Customer Benefit Highlight: Links offer to customer's specific needs showing value. See system prompt section 2.2.2 for complete evaluation criteria.",
    )

    support_detail: str = Field(
        ...,
        # max_length=400,
        description="Quote exact agent phrases for prices/terms stated or omitted. For False: cite what was unclear or missing + explain what criteria was not met. More False = more detail per finding. Maximum 400 characters (~100 tokens) - Vertex AI enforces during generation.",
    )


class EffectiveObjectionHandling(BaseModel):
    model_config = ConfigDict(extra="ignore")

    failure_to_listen: bool = Field(
        ...,
        description="[System Prompt: 2.3.1] failure_to_listen_and_address - Listens and Addresses Customer Concerns: Agent listens to objections fully and provides relevant solutions. See system prompt section 2.3.1 for complete evaluation criteria.",
    )

    confrontational_tone: bool = Field(
        ...,
        description="[System Prompt: 2.3.2] using_confrontational_tone - Maintains Professional Tone: Agent keeps calm, polite tone without arguing. See system prompt section 2.3.2 for complete evaluation criteria.",
    )

    support_detail: str = Field(
        ...,
        # max_length=400,
        description="Quote exact customer objections and agent responses. For False: cite the objection moment + how agent should have responded. More False = more detail per finding. Maximum 400 characters (~100 tokens) - Vertex AI enforces during generation.",
    )


class SalesClosingAttempt(BaseModel):
    model_config = ConfigDict(extra="ignore")

    value_based_closing: bool = Field(
        ...,
        description="[System Prompt: 2.4.1] value_based_closing - Value-Based Closing: Agent summarizes benefits clearly linked to customer's pain points before inviting decision. See system prompt section 2.4.1 for complete evaluation criteria.",
    )

    unclear_separation: bool = Field(
        ...,
        description="[System Prompt: 2.4.2] unclear_separation_primary_addon - Clear Separation Between Offers: Distinguishes primary offer from add-on. See system prompt section 2.4.2 for complete evaluation criteria.",
    )

    inadequate_addon_disclosure: bool = Field(
        ...,
        description="[System Prompt: 2.4.3] inadequate_disclosure_additional_charges - Discloses Additional Charges: Clearly states add-on pricing and conditions. See system prompt section 2.4.3 for complete evaluation criteria.",
    )

    support_detail: str = Field(
        ...,
        # max_length=400,
        description="Quote exact agent phrases. For False: cite what was missing (benefit connection, add-on separation, or pricing) + explain what criteria was not met. More False = more detail per finding. Maximum 400 characters (~100 tokens) - Vertex AI enforces during generation.",
    )


class CrossSellUpsell(BaseModel):
    model_config = ConfigDict(extra="ignore")

    missed_crosssell_upsell: bool | None = Field(
        default=None,
        description="[System Prompt: 2.5.1] missed_cross_sell_upsell_opportunity - Missed Cross-sell/Upsell Opportunity: Appropriately offers related/upgraded services. True = agent offered and explained (even if customer declines after). False = opportunity existed but agent didn't offer. None = no opportunity (customer has no time, not interested before details, immediately declines). NOTE: Customer declining AFTER proper explanation = True (not penalized). See system prompt section 2.5 for complete evaluation criteria.",
    )

    unclear_addon_separation_crosssell: bool | None = Field(
        default=None,
        description="[System Prompt: 2.5.2] unclear_separation_cross_sell - Unclear Separation in Cross-sell: Separates primary from supplementary offers with distinct pricing. True = clear separation. False = confusing/combined. None = no cross-sell attempted. NOTE: This field must be None if missed_crosssell_upsell is None. See system prompt section 2.5 for complete evaluation criteria.",
    )

    inadequate_addon_disclosure_crosssell: bool | None = Field(
        default=None,
        description="[System Prompt: 2.5.3] inadequate_disclosure_cross_sell - Inadequate Disclosure in Cross-sell: Fully discloses supplementary offer costs and conditions. True = complete disclosure. False = incomplete/misleading. None = no cross-sell attempted. NOTE: This field must be None if missed_crosssell_upsell is None. See system prompt section 2.5 for complete evaluation criteria.",
    )

    support_detail: str = Field(
        ...,
        # max_length=400,
        description="If None: quote the conversation moment proving no opportunity existed. If True/False: cite agent's exact attempt and customer response. For False: explain what was missing. More False = more detail per finding. Maximum 400 characters (~100 tokens) - Vertex AI enforces during generation.",
    )

    @model_validator(mode="after")
    def validate_crosssell_fields(self):
        if self.missed_crosssell_upsell is None:
            self.unclear_addon_separation_crosssell = None
            self.inadequate_addon_disclosure_crosssell = None
        elif self.missed_crosssell_upsell is False:
            self.unclear_addon_separation_crosssell = False
            self.inadequate_addon_disclosure_crosssell = False
        elif self.missed_crosssell_upsell and (
            self.unclear_addon_separation_crosssell is None or self.inadequate_addon_disclosure_crosssell is None
        ):
            raise ValueError(
                "If 'missed_crosssell_upsell' is True or False, 'unclear_addon_separation_crosssell' and 'inadequate_addon_disclosure_crosssell' cannot be None."
            )
        return self


class SalesEffectiveness(BaseModel):
    model_config = ConfigDict(extra="ignore")

    customer_needs_analysis: CustomerNeedsAnalysis = Field(
        ...,
        description="[System Prompt: 2.1] Customer Needs Analysis (การวิเคราะห์ความต้องการของลูกค้า): Agent analyzes customer requirements. See system prompt section 2.1 for complete criteria.",
    )

    offer_presentation_quality: OfferPresentationQuality = Field(
        ...,
        description="[System Prompt: 2.2] Offer Presentation Quality (คุณภาพการนำเสนอข้อเสนอ): Quality of how agent presents offers. See system prompt section 2.2 for complete criteria.",
    )

    effective_objection_handling: EffectiveObjectionHandling = Field(
        ...,
        description="[System Prompt: 2.3] Effective Objection Handling: Agent's objection handling skills. See system prompt section 2.3 for complete criteria.",
    )

    sales_closing_attempt: SalesClosingAttempt = Field(
        ...,
        description="[System Prompt: 2.4] Sales Closing Attempt: Agent's approach to closing sales. See system prompt section 2.4 for complete criteria.",
    )

    cross_sell_upsell: CrossSellUpsell = Field(
        ...,
        description="[System Prompt: 2.5] Cross-sell / Upsell: Agent's cross-sell and upsell abilities. See system prompt section 2.5 for complete criteria.",
    )
