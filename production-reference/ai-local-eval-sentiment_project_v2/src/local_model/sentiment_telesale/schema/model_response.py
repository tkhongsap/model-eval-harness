"""Structured-output contract for the local telesale pipeline: the scored subset.

Deliberately **narrower than
:mod:`src.google_model.sentiment_telesale.schema.model_response`**, and the narrowing is a
decision rather than drift. The google side runs the full production contract because the point
of that benchmark is to measure the call production actually issues. This side runs against a
slow local endpoint over an ASR transcript, so it asks only for what the ground-truth sheet
scores: the 39 boolean criteria, plus ``call_status``.

Dropped here and present there: ``transcript`` (the local pipeline produces its own, from ASR
plus a labelling call, so a transcript field would echo its own input), the thirteen Thai
``support_detail`` justifications, ``campaign_name``, ``campaign_ratio``, ``agent_strength``,
``agent_weakness``, ``sales_performance`` and ``customer_insight``.

**One consequence worth stating plainly:** ``support_detail`` is the prompt's
evidence-before-verdict step -- it makes the model quote the agent before judging them. Without
it the two model families are not merely different models answering the same question, and a
google-vs-local gap on the 39 flags mixes model quality with prompt shape.

Every ``Field(description=...)`` below is byte-identical to the google copy's, so the criteria
that do exist on both sides are described in exactly the same words. A test pins that.

Field order is generation order; every field is required and nothing carries a default, so the
OpenAI strict-mode grammar handed to the endpoint demands all of them.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator


def _drop_docstring_description(schema: dict) -> None:
    """Keep class docstrings out of the schema handed to the model.

    Pydantic promotes a model's docstring to the object's ``description``; only
    ``Field(description=...)`` is prompt text.
    """
    schema.pop("description", None)


class _SchemaModel(BaseModel):
    """Base for every response model in this contract.

    ``_coerced`` is a private attribute, so it is invisible to ``model_json_schema()`` and
    never asked of the model. The softened validators append dotted field paths to it.
    """

    model_config = ConfigDict(json_schema_extra=_drop_docstring_description)

    _coerced: list[str] = PrivateAttr(default_factory=list)


class CallOpening(_SchemaModel):
    """Prompt 1.1 -- how the agent opened the call."""

    proper_identification: bool = Field(
        ...,
        description='[System Prompt: 1.1.1] proper_identification - Proper Identification (การแสดงตัวตนผู้ติดต่อ): Agent provides greeting and informs agent name at call beginning. See system prompt section 1.1.1 for complete evaluation criteria.',
    )

    call_origin_disclosure: bool = Field(
        ...,
        description='[System Prompt: 1.1.2] call_origin_disclosure - Call Origin Disclosure (การแจ้งที่มาของการติดต่อ): Agent clearly informs which company calling from (True/Dtac). See system prompt section 1.1.2 for complete evaluation criteria.',
    )

    call_consent_before_engagement: bool = Field(
        ...,
        description="[System Prompt: 1.1.3] consent_before_engagement - Consent Before Engagement (การขออนุญาตก่อนสนทนา/นำเสนอ): Agent EXPLICITLY asks customer's permission/convenience before proceeding with detailed offer (e.g., 'ขออนุญาต', 'สะดวกคุยสาย', 'ขออนุญาตเรียนแจ้ง'). Consent can appear before OR after stating call objective or call introduction. CRITICAL: Announcing/pitching alone (e.g., 'วันนี้จะมาดูแล...') without any consent phrase is NOT consent — only explicit permission-seeking counts. See system prompt section 1.1.3 for complete evaluation criteria.",
    )


class CustomerIdentityVerification(_SchemaModel):
    """Prompt 1.2 -- identity checks performed once the customer accepted the offer.

    All three flags are nullable because a call with no transaction needs no
    verification at all: ``None`` there means "not applicable", which is a different
    answer from ``False`` ("required and not done")."""

    customer_verification: bool | None = Field(
        ...,
        description='[System Prompt: 1.2.1] customer_verification - Customer Verification (ยืนยันตัวตนถูกต้อง): Agent asks customer for name-surname or surname when completing sale/service. See system prompt section 1.2.1 for complete evaluation criteria.',
    )

    invalid_verification: bool | None = Field(
        ...,
        description='[System Prompt: 1.2.2] invalid_verification - Invalid Verification (ยืนยันตัวตนไม่ครบถ้วน): When customer is not the registrant and is the payer, all 3-part verification questions were completed. See system prompt section 1.2.2 for complete evaluation criteria.',
    )

    missing_verification: bool | None = Field(
        ...,
        description='[System Prompt: 1.2.3] missing_or_late_verification - Missing or Late Verification (ไม่ยืนยันตัวตน): Overall result — whether full required verification was performed. See system prompt section 1.2.3 for complete evaluation criteria.',
    )

    @model_validator(mode="after")
    def validate_verification_fields(self) -> "CustomerIdentityVerification":
        """Apply the prompt's seven-scenario truth table, coercing rather than raising.

        Identical in behaviour to the google copy, including the coercion record: a raise here
        would cost the whole section, and with it the file, over one malformed field.
        """
        if self.customer_verification is None:
            self.invalid_verification = None
            self.missing_verification = None
        elif self.customer_verification is False:
            self.invalid_verification = False
            self.missing_verification = False
        else:
            valid = {(None, None), (None, True), (True, True), (False, False)}
            if (self.invalid_verification, self.missing_verification) not in valid:
                self.missing_verification = (
                    True if self.invalid_verification is not False else False
                )
                self._coerced.append("customer_identity_verification.missing_verification")
        return self


class LanguageAndTone(_SchemaModel):
    """Prompt 1.3 -- professionalism of the agent's speech."""

    behavioral_violation: bool = Field(
        ...,
        description='[System Prompt: 1.3.1] behavioral_violation - Behavioral Violation (ความไม่เหมาะสม): Detects inappropriate language, sarcasm, or aggressive tone. See system prompt section 1.3.1 for complete evaluation criteria.',
    )

    clarity: bool = Field(
        ...,
        description='[System Prompt: 1.3.2] clarity - Clarity (ความไม่ชัดเจนของถ้อยคำ): Clear and direct communication without ambiguity. See system prompt section 1.3.2 for complete evaluation criteria.',
    )

    delivery_pace: bool = Field(
        ...,
        description='[System Prompt: 1.3.3] delivery_pace - Delivery Pace (ความเร็วในการพูด): Appropriate speaking speed, neither too fast nor too slow. See system prompt section 1.3.3 for complete evaluation criteria.',
    )


class ActiveListening(_SchemaModel):
    """Prompt 1.4 -- whether the agent listened rather than talked over."""

    no_interruption: bool = Field(
        ...,
        description='[System Prompt: 1.4.1] no_interruption - No Interruption (ไม่ขัดจังหวะลูกค้า): Agent must not interrupt while customer is explaining problems and must wait for customer to finish before responding. See system prompt section 1.4.1 for complete evaluation criteria.',
    )

    correct_understanding: bool = Field(
        ...,
        description="[System Prompt: 1.4.2] correct_understanding - Correct Understanding (จับประเด็นถูกต้อง): Agent must understand customer's problem and respond according to the Intent that customer communicated. See system prompt section 1.4.2 for complete evaluation criteria.",
    )

    acknowledgement_paraphrasing: bool = Field(
        ...,
        description='[System Prompt: 1.4.3] acknowledgement_paraphrasing - Acknowledgement & Paraphrasing (การทวนและยืนยันความเข้าใจ): Agent asks questions, paraphrases, and summarizes customer needs comprehensively. CRITICAL: If customer is NOT interested (rejected/declined), this MUST be True — paraphrasing is only required when customer is actively engaged. See system prompt section 1.4.3 for complete evaluation criteria.',
    )


class CallClosing(_SchemaModel):
    """Prompt 1.5 -- how the agent ended the call."""

    confirm_resolution: bool = Field(
        ...,
        description='[System Prompt: 1.5.1] confirm_resolution_and_next_steps - Confirm Resolution and Next Steps (ยืนยันการปรับเปลี่ยนและขั้นตอนถัดไปหรือสรุปการสนทนา): If sale completed, agent must confirm what was done, summarize sale/service details, and explain next steps. CRITICAL: If NO sale/service completed (customer rejected/declined), this MUST be True — summarizing is only required when a transaction occurred. See system prompt section 1.5.1 for complete evaluation criteria.',
    )

    courteous_ending: bool = Field(
        ...,
        description='[System Prompt: 1.5.2] courteous_and_proper_call_ending - Courteous and Proper Call Ending (ปิดการสนทนาด้วยมารยาทและความสุภาพ): Agent ends call with thank you, provides support hotline info (1242), and says goodbye with gentle tone. See system prompt section 1.5.2 for complete evaluation criteria.',
    )

    smooth_closing: bool = Field(
        ...,
        description='[System Prompt: 1.5.3] smooth_and_non_rushed_closing - Smooth and Non-Rushed Closing (ปิดบทสนทนาอย่างราบรื่น ไม่เร่งรีบ): Agent asks if customer has questions or waits few seconds after closing statement, giving customer opportunity to respond before ending. CRITICAL: If customer is busy/not available or ends the call themselves, this MUST be True — agent does not need to wait or ask additional questions. Can ONLY be False when agent (not customer) rushes the closing. See system prompt section 1.5.3 for complete evaluation criteria.',
    )


class OperationsAndProfessionalism(_SchemaModel):
    """Prompt Main Category 1 -- the fifteen ``op_*`` columns of the sheet."""

    call_opening: CallOpening = Field(
        ...,
        description='[System Prompt: 1.1] Call Opening (การเปิดสาย): Opening procedures for outbound telesale calls. See system prompt section 1.1 for complete criteria.',
    )

    customer_identity_verification: CustomerIdentityVerification = Field(
        ...,
        description='[System Prompt: 1.2] Customer Identity Verification (การยืนยันตัวตนลูกค้า): Verification procedures. PLACEHOLDER - Reserved for future use.',
    )

    language_and_tone: LanguageAndTone = Field(
        ...,
        description='[System Prompt: 1.3] Language & Tone (ภาษาและน้ำเสียง): Professional communication quality. See system prompt section 1.3 for complete criteria.',
    )

    active_listening: ActiveListening = Field(
        ...,
        description='[System Prompt: 1.4] Active Listening (การรับฟังอย่างตั้งใจ): Attentive listening by focusing on both words and emotions. Each criterion is independent - evaluate separately. See system prompt section 1.4 for complete criteria.',
    )

    call_closing: CallClosing = Field(
        ...,
        description='[System Prompt: 1.5] Call Closing (การปิดสาย): Proper call conclusion procedures. See system prompt section 1.5 for complete criteria.',
    )


class CustomerNeedsAnalysis(_SchemaModel):
    """Prompt 2.1 -- did the agent work from the customer's actual usage."""

    usage_based_analysis: bool = Field(
        ...,
        description="[System Prompt: 2.1.1] usage_based_analysis - Usage-based Analysis: Agent references customer's actual usage data for recommendations. See system prompt section 2.1.1 for complete evaluation criteria.",
    )

    benefit_highlight: bool = Field(
        ...,
        description='[System Prompt: 2.1.2] benefit_highlight - Benefit Highlight: Agent connects package features to specific customer benefits. See system prompt section 2.1.2 for complete evaluation criteria.',
    )


class OfferPresentationQuality(_SchemaModel):
    """Prompt 2.2 -- how the offer itself was put."""

    clarity_of_explanation: bool = Field(
        ...,
        description='[System Prompt: 2.2.1] clarity_of_explanation - Clarity of Explanation: Explanations are clear, organized, and easy to understand. See system prompt section 2.2.1 for complete evaluation criteria.',
    )

    customer_benefit_highlight: bool = Field(
        ...,
        description="[System Prompt: 2.2.2] customer_benefit_highlight - Customer Benefit Highlight: Links offer to customer's specific needs showing value. See system prompt section 2.2.2 for complete evaluation criteria.",
    )


class EffectiveObjectionHandling(_SchemaModel):
    """Prompt 2.3 -- the agent's response to pushback."""

    failure_to_listen: bool = Field(
        ...,
        description='[System Prompt: 2.3.1] failure_to_listen_and_address - Listens and Addresses Customer Concerns: Agent listens to objections fully and provides relevant solutions. See system prompt section 2.3.1 for complete evaluation criteria.',
    )

    confrontational_tone: bool = Field(
        ...,
        description='[System Prompt: 2.3.2] using_confrontational_tone - Maintains Professional Tone: Agent keeps calm, polite tone without arguing. See system prompt section 2.3.2 for complete evaluation criteria.',
    )


class SalesClosingAttempt(_SchemaModel):
    """Prompt 2.4 -- the ask, and whether add-on costs were separated out."""

    value_based_closing: bool = Field(
        ...,
        description="[System Prompt: 2.4.1] value_based_closing - Value-Based Closing: Agent summarizes benefits clearly linked to customer's pain points before inviting decision. See system prompt section 2.4.1 for complete evaluation criteria.",
    )

    unclear_separation: bool = Field(
        ...,
        description='[System Prompt: 2.4.2] unclear_separation_primary_addon - Clear Separation Between Offers: Distinguishes primary offer from add-on. See system prompt section 2.4.2 for complete evaluation criteria.',
    )

    inadequate_addon_disclosure: bool = Field(
        ...,
        description='[System Prompt: 2.4.3] inadequate_disclosure_additional_charges - Discloses Additional Charges: Clearly states add-on pricing and conditions. See system prompt section 2.4.3 for complete evaluation criteria.',
    )


class CrossSellUpsell(_SchemaModel):
    """Prompt 2.5 -- the cross-sell attempt, nullable when no opening existed."""

    missed_crosssell_upsell: bool | None = Field(
        ...,
        description="[System Prompt: 2.5.1] missed_cross_sell_upsell_opportunity - Missed Cross-sell/Upsell Opportunity: Appropriately offers related/upgraded services. True = agent offered and explained (even if customer declines after). False = opportunity existed but agent didn't offer. None = no opportunity (customer has no time, not interested before details, immediately declines). NOTE: Customer declining AFTER proper explanation = True (not penalized). See system prompt section 2.5 for complete evaluation criteria.",
    )

    unclear_addon_separation_crosssell: bool | None = Field(
        ...,
        description='[System Prompt: 2.5.2] unclear_separation_cross_sell - Unclear Separation in Cross-sell: Separates primary from supplementary offers with distinct pricing. True = clear separation. False = confusing/combined. None = no cross-sell attempted. NOTE: This field must be None if missed_crosssell_upsell is None. See system prompt section 2.5 for complete evaluation criteria.',
    )

    inadequate_addon_disclosure_crosssell: bool | None = Field(
        ...,
        description='[System Prompt: 2.5.3] inadequate_disclosure_cross_sell - Inadequate Disclosure in Cross-sell: Fully discloses supplementary offer costs and conditions. True = complete disclosure. False = incomplete/misleading. None = no cross-sell attempted. NOTE: This field must be None if missed_crosssell_upsell is None. See system prompt section 2.5 for complete evaluation criteria.',
    )

    @model_validator(mode="after")
    def validate_crosssell_fields(self) -> "CrossSellUpsell":
        """Propagate the gate field, coercing rather than raising on a dangling null."""
        if self.missed_crosssell_upsell is None:
            self.unclear_addon_separation_crosssell = None
            self.inadequate_addon_disclosure_crosssell = None
        elif self.missed_crosssell_upsell is False:
            self.unclear_addon_separation_crosssell = False
            self.inadequate_addon_disclosure_crosssell = False
        else:
            if self.unclear_addon_separation_crosssell is None:
                self.unclear_addon_separation_crosssell = True
                self._coerced.append("cross_sell_upsell.unclear_addon_separation_crosssell")
            if self.inadequate_addon_disclosure_crosssell is None:
                self.inadequate_addon_disclosure_crosssell = True
                self._coerced.append("cross_sell_upsell.inadequate_addon_disclosure_crosssell")
        return self


class SalesEffectiveness(_SchemaModel):
    """Prompt Main Category 2 -- the twelve ``se_*`` columns of the sheet."""

    customer_needs_analysis: CustomerNeedsAnalysis = Field(
        ...,
        description='[System Prompt: 2.1] Customer Needs Analysis (การวิเคราะห์ความต้องการของลูกค้า): Agent analyzes customer requirements. See system prompt section 2.1 for complete criteria.',
    )

    offer_presentation_quality: OfferPresentationQuality = Field(
        ...,
        description='[System Prompt: 2.2] Offer Presentation Quality (คุณภาพการนำเสนอข้อเสนอ): Quality of how agent presents offers. See system prompt section 2.2 for complete criteria.',
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


class PositiveCustomerExperience(_SchemaModel):
    """Prompt 3.1 -- empathy, ownership and de-escalation."""

    failure_to_demonstrate_empathy: bool = Field(
        ...,
        description="[System Prompt: 3.1.1] failure_to_demonstrate_empathy - Demonstrates Empathy: Shows understanding and compassion for customer's service issues. See system prompt section 3.1.1 for complete evaluation criteria.",
    )

    deflecting_responsibility: bool = Field(
        ...,
        description='[System Prompt: 3.1.2] deflecting_responsibility - Takes Responsibility: Accepts complaints clearly and commits to resolution. See system prompt section 3.1.2 for complete evaluation criteria.',
    )

    escalates_customer_emotion: bool = Field(
        ...,
        description='[System Prompt: 3.1.3] tone_escalates_emotion - De-escalates Customer Emotion: Maintains calm tone that reduces customer frustration. See system prompt section 3.1.3 for complete evaluation criteria.',
    )


class ClarityOfCommunication(_SchemaModel):
    """Prompt 3.2 -- plain language, and adapting when the customer is lost."""

    overly_technical_language: bool = Field(
        ...,
        description='[System Prompt: 3.2.1] uses_overly_technical_language - Uses Simple Language: Communicates in plain language avoiding jargon. See system prompt section 3.2.1 for complete evaluation criteria.',
    )

    fails_to_clarify_limitations: bool = Field(
        ...,
        description='[System Prompt: 3.2.2] fails_to_clarify_misunderstandings - Clarifies Common Misunderstandings: Proactively explains limitations and conditions. See system prompt section 3.2.2 for complete evaluation criteria.',
    )

    no_adjustment_for_complexity: bool = Field(
        ...,
        description='[System Prompt: 3.2.3] does_not_adjust_explanation_style - Adjusts Explanation When Needed: Adapts explanation method when customer shows confusion. See system prompt section 3.2.3 for complete evaluation criteria.',
    )


class BuildingTrust(_SchemaModel):
    """Prompt 3.3 -- accuracy, and connecting price to value."""

    provides_unclear_information: bool = Field(
        ...,
        description='[System Prompt: 3.3.1] provides_unclear_information - Provides Clear, On-Point Information: Answers directly to questions asked. See system prompt section 3.3.1 for complete evaluation criteria.',
    )

    provides_misleading_information: bool = Field(
        ...,
        description='[System Prompt: 3.3.2] provides_misleading_information - Provides Accurate Information: Information matches system facts and policies. See system prompt section 3.3.2 for complete evaluation criteria.',
    )

    fails_to_connect_value: bool = Field(
        ...,
        description='[System Prompt: 3.3.3] fails_to_connect_price_to_value - Connects Price to Value: Links pricing and features to specific customer benefits. See system prompt section 3.3.3 for complete evaluation criteria.',
    )


class CustomerExperience(_SchemaModel):
    """Prompt Main Category 3 -- the nine ``cx_*`` columns of the sheet."""

    positive_customer_experience: PositiveCustomerExperience = Field(
        ...,
        description='[System Prompt: 3.1] Positive Customer Experience: Agent creates positive feelings through empathy and de-escalation. See system prompt section 3.1 for complete criteria.',
    )

    clarity_of_communication: ClarityOfCommunication = Field(
        ...,
        description='[System Prompt: 3.2] Clarity of Communication: Agent communicates clearly and adapts to customer needs. See system prompt section 3.2 for complete criteria.',
    )

    building_trust: BuildingTrust = Field(
        ...,
        description='[System Prompt: 3.3] Building Trust and Customer Value: Agent builds trust through accurate information. See system prompt section 3.3 for complete criteria.',
    )


class SubCompliance(_SchemaModel):
    """Prompt 4.1 -- the three ``cp_*`` columns of the sheet."""

    data_privacy_compliance: bool = Field(
        ...,
        description="[System Prompt: 4.1.1] data_privacy_permission_compliance - Data Privacy & Permission Compliance: Agent uses customer data within authorized scope, discloses company identity and call purpose, does not pressure customer, and completes verification if sale occurred. NOTE: 'permission' here means data privacy scope — NOT consent before presenting (that is 1.1.3 call_consent_before_engagement). See system prompt section 4.1.1 for complete evaluation criteria.",
    )

    sales_integrity_compliance: bool = Field(
        ...,
        description='[System Prompt: 4.1.2] sales_integrity_transparency_compliance - Sales Integrity & Transparency: Provides complete, truthful information with all terms disclosed. See system prompt section 4.1.2 for complete evaluation criteria.',
    )

    professional_conduct_compliance: bool = Field(
        ...,
        description='[System Prompt: 4.1.3] professional_conduct_behavioral_compliance - Professional Conduct: Maintains professional, respectful communication throughout call. See system prompt section 4.1.3 for complete evaluation criteria.',
    )


class Compliance(_SchemaModel):
    """Prompt Main Category 4.

    A one-field wrapper, so the path really is ``compliance.compliance.*``. Kept
    because the sheet's ``cp_compliance_*`` column names were derived from it and the
    google copy emits that shape."""

    compliance: SubCompliance = Field(
        ...,
        description='[System Prompt: 4.1] Compliance: Evaluates adherence to standards. See system prompt section 4.1 for complete criteria.',
    )


class OperationsSection(_SchemaModel):
    """What the ``operations`` section call returns.

    ``call_status`` rides along here rather than in a section of its own: it is a single
    enum, and judging how a call opened and closed already requires reading the whole
    call flow. The merge spreads this dump across the top level, exactly as the QA
    pipeline's ``CallClassification`` does.
    """

    call_status: Literal["Completed", "Abandoned"] = Field(
        ...,
        description="Identifies WHEN customer ended call in conversation flow. 'Completed' = call reached end stage (offer accepted/rejected, main business done, near closing - even if customer ended quickly). 'Abandoned' = customer hung up EARLY before conversation reached its purpose (e.g., during opening/consent, mid-presentation) OR technical issues caused immediate disconnection. Focus on timing, not closing quality.",
    )

    operations_and_professionalism: OperationsAndProfessionalism = Field(
        ...,
        description='[System Prompt: Main Category 1] Operations and Professionalism - Evaluates professional conduct, call structure, communication quality, and active listening. See system prompt Category 1 for complete criteria.',
    )


class ModelResponse(_SchemaModel):
    """The merged answer for one call: ``call_status`` and the four judged blocks.

    Never generated in one request -- the pipeline fans out four section calls and
    validates their merged dumps through this class, so this is the shape the sheet is
    built from and the shape a resumed run re-reads from its checkpoint.
    """

    call_status: Literal["Completed", "Abandoned"] = Field(
        ...,
        description="Identifies WHEN customer ended call in conversation flow. 'Completed' = call reached end stage (offer accepted/rejected, main business done, near closing - even if customer ended quickly). 'Abandoned' = customer hung up EARLY before conversation reached its purpose (e.g., during opening/consent, mid-presentation) OR technical issues caused immediate disconnection. Focus on timing, not closing quality.",
    )

    operations_and_professionalism: OperationsAndProfessionalism = Field(
        ...,
        description='[System Prompt: Main Category 1] Operations and Professionalism - Evaluates professional conduct, call structure, communication quality, and active listening. See system prompt Category 1 for complete criteria.',
    )

    sales_effectiveness: SalesEffectiveness = Field(
        ...,
        description='[System Prompt: Main Category 2] Sales Effectiveness - Evaluates needs analysis, offer presentation, objection handling, and sales closing. See system prompt Category 2 for complete criteria.',
    )

    customer_experience: CustomerExperience = Field(
        ...,
        description='[System Prompt: Main Category 3] Customer Experience - Evaluates positive experience creation, communication clarity, and trust building. See system prompt Category 3 for complete criteria.',
    )

    compliance: Compliance = Field(
        ...,
        description='[System Prompt: Main Category 4] Compliance - Evaluates adherence to legal, policy, and procedural requirements. See system prompt Category 4 for complete criteria.',
    )

    @model_validator(mode="after")
    def validate_data_privacy_compliance(self) -> "ModelResponse":
        """A failed identity check forces ``data_privacy_compliance`` False.

        Runs at merge time here, because the verification flags and the compliance flags are
        produced by two different section calls. That is the whole reason the merge goes
        through this class rather than straight into the sheet.
        """
        block = self.operations_and_professionalism.customer_identity_verification
        if False in (
            block.customer_verification,
            block.invalid_verification,
            block.missing_verification,
        ):
            self.compliance.compliance.data_privacy_compliance = False
        return self


def collect_coercions(model: BaseModel) -> list[str]:
    """Every field the softened validators had to rewrite, as dotted paths.

    Accepts any model in this module, not only :class:`ModelResponse`, because the split
    pipeline validates one section at a time and needs the count per section as well as per
    file.
    """

    def walk(node: BaseModel) -> Iterator[str]:
        if isinstance(node, _SchemaModel):
            yield from node._coerced
        for name in type(node).model_fields:
            value = getattr(node, name, None)
            if isinstance(value, BaseModel):
                yield from walk(value)

    return list(walk(model))


__all__ = [
    "ActiveListening",
    "BuildingTrust",
    "CallClosing",
    "CallOpening",
    "ClarityOfCommunication",
    "Compliance",
    "CrossSellUpsell",
    "CustomerExperience",
    "CustomerIdentityVerification",
    "CustomerNeedsAnalysis",
    "EffectiveObjectionHandling",
    "LanguageAndTone",
    "ModelResponse",
    "OfferPresentationQuality",
    "OperationsAndProfessionalism",
    "OperationsSection",
    "PositiveCustomerExperience",
    "SalesClosingAttempt",
    "SalesEffectiveness",
    "SubCompliance",
    "collect_coercions",
]
