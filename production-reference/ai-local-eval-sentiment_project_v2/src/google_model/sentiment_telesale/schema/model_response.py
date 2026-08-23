"""Structured-output contract for the outbound-telesale evaluation prompt.

Merged from the production ``real_model_response`` package into one importable module: the
originals import ``tasks.sentiment_telesale.output_validation.common.*``, a package root that
exists only in the production service. Every ``Field(description=...)`` is byte-identical to
the production text -- each one carries a ``[System Prompt: X.Y.Z]`` tag pinning it to a
section of ``prompt/system_prompt.txt``, and a paraphrase would silently respecify the task.

Four deliberate departures from the production files, each load-bearing:

1. **No defaults.** Production writes ``Field(None, ...)``, ``default=0`` and
   ``default_factory=list``. A default drops its field out of ``required``, which OpenAI
   strict mode rejects outright and which lets Vertex omit the field entirely -- so a
   ``bool | None`` keeps the union and loses the default.
2. **``check_list`` is dropped.** Production documents it as "Mock field to be overridden in
   subclasses"; the prompt names a fifth "Check List" dimension but never specifies its
   shape, so asking for it spends tokens on an unspecified answer nothing scores.
3. **Order-stable dedup** in :class:`SalesPerformance` -- see :meth:`SalesPerformance.dedup_list`.
4. **Coercion instead of raising** in the three validators that reject a malformed answer.
   Each records a dotted path on the private ``_coerced`` list, which
   :func:`collect_coercions` gathers, so a coerced row is counted on the Matrix sheet rather
   than passing as a compliant one.

``transcript`` is first because property order is generation order: the model writes down what
was said before it judges any of it. The prompt is otherwise transcript-in; here the batch job
is handed audio, so the transcript is produced rather than supplied.
"""

from __future__ import annotations

from typing import Any, Literal

from google.genai import types
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator

TRANSCRIPT_DESCRIPTION = 'ถอดบทสนทนาทั้งสายจากไฟล์เสียงตามที่ได้ยินจริง โดยแยกผู้พูดออกเป็น 2 บทบาท คือ `Agent` (พนักงาน call center) และ `Customer` (ลูกค้า)\n\n**1. Speaker identification (ต้องระบุจากน้ำเสียงและเนื้อหา ไม่ใช่จากลำดับการพูดหรือช่วงเงียบ):**\n- ห้ามสมมติว่าผู้ที่พูดประโยคแรกคือ `Agent` เสมอไป ต้องตัดสินบทบาทของแต่ละฝ่ายจากเนื้อหา บทบาท และพฤติกรรมการพูดที่ปรากฏจริงในไฟล์เสียง แล้วจึงใช้บทบาทนั้นตลอดทั้งสาย\n- ในสายมักมีช่วงเงียบ (dead air) เช่น พนักงานกำลังตรวจสอบข้อมูลในระบบ ลูกค้าหยุดคิด หรือสายเงียบชั่วคราว **ห้ามสมมติว่าเสียงที่พูดถัดจากช่วงเงียบเป็นของอีกฝ่ายเสมอไป** ผู้พูดคนเดิมอาจพูดต่อเองก็ได้ ให้ยึดจากลักษณะน้ำเสียงของผู้พูดและเนื้อหาที่พูดเป็นหลัก ไม่ใช่จากการมีช่วงเงียบคั่น\n- หากผู้พูดคนเดิมพูดต่อหลังช่วงเงียบ ให้รวมไว้ในเทิร์นเดิม และห้ามใส่หมายเหตุช่วงเงียบ เช่น `[เงียบ]` หรือ `[dead air]` ลงในผลลัพธ์\n- สัญญาณของ `Agent`: แจ้งชื่อบริษัท/ชื่อตนเองว่าเป็นพนักงาน, ขอยืนยันตัวตนลูกค้า (ชื่อ-นามสกุล / เลขบัตรประชาชน / เบอร์ที่ใช้บริการ), เสนอความช่วยเหลือ, ขอถือสายรอ (hold), อธิบายเงื่อนไข แพ็กเกจ ค่าบริการ หรือขั้นตอนดำเนินการ, เสนอขายหรือเสนอโปรโมชัน, ใช้ถ้อยคำสุภาพตามสคริปต์บริการ, เป็นฝ่ายควบคุมลำดับของบทสนทนาและกล่าวปิดการสนทนา\n- สัญญาณของ `Customer`: แจ้งปัญหาหรือความต้องการ, ตอบคำถามยืนยันตัวตน, ให้ข้อมูลส่วนตัวของตนเอง, สอบถามสิทธิ์ ค่าบริการ หรือผลการดำเนินการ, แสดงอารมณ์ ความกังวล หรือความไม่พอใจ, ขอให้ดำเนินการแทน\n- ใช้เกณฑ์เดียวกันทั้งสาย inbound (ลูกค้าโทรเข้า) และ outbound (พนักงานโทรออก) ผู้ที่เริ่มพูดก่อนเป็นฝ่ายใดก็ได้\n- หากมีการโอนสายและมีพนักงานคนที่สองเข้ามาในสาย ให้ใช้ป้ายว่า `Agent` เหมือนกัน (ห้ามแยกเป็น Agent 1 / Agent 2)\n- ข้ามเสียงที่ไม่ใช่บทสนทนาระหว่างสองฝ่าย เช่น เสียงระบบ IVR, เสียงประกาศอัตโนมัติ, เสียงเพลงรอสาย\n\n**2. Fidelity (ห้ามเปลี่ยนแปลงถ้อยคำ):**\n- ถอดตามที่ได้ยินจริงทุกคำ **ห้ามแปลภาษา ห้ามสรุป ห้ามย่อความ ห้ามเรียบเรียงใหม่ ห้ามแก้ไขไวยากรณ์หรือคำผิด**\n- ใช้ภาษาเดียวกับที่พูดในไฟล์เสียงเสมอ พูดไทยให้ถอดเป็นไทย พูดอังกฤษให้ถอดเป็นอังกฤษ พูดปนกัน (code-switching) ให้คงไว้ตามที่พูด\n- คงคำติดปาก คำซ้ำ คำลงท้าย และประโยคที่พูดไม่จบไว้ตามเดิม (เช่น "เอ่อ", "อ่า", "ค่ะ", "ครับ", "นะคะ")\n- ห้ามเติมข้อความที่ไม่ได้พูด และห้ามเดา หากช่วงใดฟังไม่ชัดหรือไม่ได้ยิน ให้ใส่ `[ไม่ชัดเจน]` เฉพาะช่วงนั้น\n- ถอดข้อมูลส่วนบุคคลที่พูดในสาย (ชื่อ เลขบัตรประชาชน เบอร์โทรศัพท์ ที่อยู่ เลขที่บัญชี) ตามที่ได้ยินจริง **ห้ามปิดบัง ห้ามแทนที่ด้วย placeholder และห้ามตัดออก** — กฎการใช้ placeholder ใช้กับ `summary_story` เท่านั้น ไม่ใช้กับฟิลด์นี้\n\n**3. Output format:**\n- หนึ่งเทิร์นต่อหนึ่งบรรทัด รูปแบบคือ `Agent: <ข้อความที่พูด>` หรือ `Customer: <ข้อความที่พูด>` ขึ้นบรรทัดใหม่ทุกครั้งที่เปลี่ยนผู้พูด **ห้ามเว้นบรรทัดว่างระหว่างเทิร์น**\n- ใช้ป้ายชื่อเป็นภาษาอังกฤษว่า `Agent` และ `Customer` เท่านั้น แม้เนื้อหาที่พูดจะเป็นภาษาไทย\n- รวมประโยคที่ผู้พูดคนเดียวกันพูดต่อเนื่องกันไว้ในเทิร์นเดียว และเรียงลำดับเทิร์นตามเวลาจริงในไฟล์เสียง\n- ห้ามใส่ timestamp, เลขลำดับเทิร์น, หัวข้อ, markdown, code fence หรือคำอธิบายใด ๆ เพิ่มเติม\n- หากไฟล์เสียงไม่มีบทสนทนา (สายเงียบ หรือสายหลุดก่อนเริ่มพูด) ให้ตอบเป็นข้อความว่าง\n- ตัวอย่างรูปแบบผลลัพธ์ (ผู้เริ่มพูดเป็นฝ่ายใดก็ได้ ขึ้นกับไฟล์เสียงจริง):\nAgent: <ข้อความที่พูดจริง>\nCustomer: <ข้อความที่พูดจริง>\nAgent: <ข้อความที่พูดจริง>\n'


def _drop_docstring_description(schema: dict[str, Any]) -> None:
    """Keep class docstrings out of the schema handed to the model.

    Pydantic promotes a model's docstring to the object's ``description``; only
    ``Field(description=...)`` is prompt text.
    """
    schema.pop("description", None)


class _SchemaModel(BaseModel):
    """Base for every response model in this contract.

    ``extra="forbid"`` is deliberately *not* set -- it would emit ``additionalProperties``,
    which the Gemini API rejects; the OpenAI rendering injects it separately.

    ``_coerced`` is a private attribute, so it is invisible to ``model_json_schema()`` and
    never asked of the model. The softened validators append dotted field paths to it.
    """

    model_config = ConfigDict(json_schema_extra=_drop_docstring_description)

    _coerced: list[str] = PrivateAttr(default_factory=list)


class CampaignRatio(_SchemaModel):
    """Share of the call spent on the campaign named in ``campaign_name``."""

    main: float = Field(
        ...,
        description="[System Prompt: Campaign Ratio] Percentage (0.0-1.0) of conversation discussing the main campaign referenced in campaign_name. Represents time/content spent on primary campaign offer. Must sum with 'other' to equal 1.0. Example: 0.75 means 75% of conversation is about main campaign.",
    )

    other: float = Field(
        ...,
        description="[System Prompt: Campaign Ratio] Percentage (0.0-1.0) of conversation discussing topics NOT related to main campaign (billing, technical support, other promotions, general inquiries). Must sum with 'main' to equal 1.0. Example: 0.25 means 25% of conversation is about other topics.",
    )

    @model_validator(mode="after")
    def decimal_limit(self) -> "CampaignRatio":
        """Round both shares to 2dp, as production does.

        Not a sum check: the prompt asks for two numbers totalling 1.0 but nothing enforces
        it, and rejecting 0.99 would cost a whole call over an unscored field.
        """
        self.main = round(self.main, 2)
        self.other = round(self.other, 2)
        return self


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

    support_detail: str = Field(
        ...,
        description='Quote exact agent phrases as evidence. For True: briefly cite. For False: quote what agent said (or note silence) + explain what criteria was not met. More False = more detail per finding. Maximum 400 characters (~100 tokens) - Vertex AI enforces this during generation.',
    )


class CustomerIdentityVerification(_SchemaModel):
    """Prompt 1.2 -- identity checks performed once the customer accepted the offer.

    All three flags are nullable because a call with no transaction needs no
    verification at all: ``None`` there means "not applicable", which is a different
    answer from ``False`` ("required and not done"). The ground-truth sheet agrees --
    it leaves these cells blank on 20 of 26 calls."""

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

    support_detail: str = Field(
        ...,
        description='Quote exact evidence. If None: quote the conversation moment proving no sale occurred. If True/False: cite verification steps with exact phrases. More False = more detail per finding. Maximum 400 characters (~100 tokens) - Vertex AI enforces during generation.',
    )

    @model_validator(mode="after")
    def validate_verification_fields(self) -> "CustomerIdentityVerification":
        """Apply the prompt's seven-scenario truth table.

        Production *raises* on an illegal combination. Here it is coerced instead and the
        coercion recorded on :attr:`_coerced`, because a raise costs the whole call out of a
        26-call benchmark with no retry on the batch path. ``missing_verification`` is the
        field derived, since ``invalid_verification`` is the one the prompt reasons about
        directly -- so the coercion is deterministic rather than a guess between two answers.
        """
        if self.customer_verification is None:
            # Scenario 1 / 7: no sale, or the agent redirected -- nothing to verify.
            self.invalid_verification = None
            self.missing_verification = None
        elif self.customer_verification is False:
            # Scenario 2: no verification attempted at all.
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

    support_detail: str = Field(
        ...,
        description='Quote exact agent phrases showing tone/clarity/pacing. For False: quote the problematic phrase + explain what criteria was not met. More False = more detail per finding. Maximum 400 characters (~100 tokens) - Vertex AI enforces during generation.',
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

    support_detail: str = Field(
        ...,
        description='Quote exact agent/customer phrases. For False: cite the interruption moment, misunderstanding, or missing paraphrase + explain what criteria was not met. More False = more detail per finding. Maximum 400 characters (~100 tokens) - Vertex AI enforces during generation.',
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

    support_detail: str = Field(
        ...,
        description="Quote exact closing phrases or note their absence. For False: cite what agent said (or didn't say) + what was expected. More False = more detail per finding. Maximum 400 characters (~100 tokens) - Vertex AI enforces during generation.",
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

    support_detail: str = Field(
        ...,
        description='Quote exact agent phrases. For False: cite what data was ignored + explain what criteria was not met. More False = more detail per finding. Maximum 400 characters (~100 tokens) - Vertex AI enforces during generation.',
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

    support_detail: str = Field(
        ...,
        description='Quote exact agent phrases for prices/terms stated or omitted. For False: cite what was unclear or missing + explain what criteria was not met. More False = more detail per finding. Maximum 400 characters (~100 tokens) - Vertex AI enforces during generation.',
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

    support_detail: str = Field(
        ...,
        description='Quote exact customer objections and agent responses. For False: cite the objection moment + how agent should have responded. More False = more detail per finding. Maximum 400 characters (~100 tokens) - Vertex AI enforces during generation.',
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

    support_detail: str = Field(
        ...,
        description='Quote exact agent phrases. For False: cite what was missing (benefit connection, add-on separation, or pricing) + explain what criteria was not met. More False = more detail per finding. Maximum 400 characters (~100 tokens) - Vertex AI enforces during generation.',
    )


class CrossSellUpsell(_SchemaModel):
    """Prompt 2.5 -- the cross-sell attempt, nullable when no opening existed.

    ``missed_crosssell_upsell`` gates the other two: ``None`` (no opportunity) and
    ``False`` (opportunity missed) both mean there is nothing to judge, so the
    validator propagates rather than leaving a judgement attached to nothing."""

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

    support_detail: str = Field(
        ...,
        description="If None: quote the conversation moment proving no opportunity existed. If True/False: cite agent's exact attempt and customer response. For False: explain what was missing. More False = more detail per finding. Maximum 400 characters (~100 tokens) - Vertex AI enforces during generation.",
    )

    @model_validator(mode="after")
    def validate_crosssell_fields(self) -> "CrossSellUpsell":
        """Propagate the gate field, coercing rather than raising on a dangling null.

        Production raises when ``missed_crosssell_upsell`` is True but a child is null. The
        children are coerced to ``True`` instead: True on this block means the agent did offer
        and explain, so True is the reading consistent with the gate the model already chose --
        and it is the only value the ground-truth sheet ever carries here.
        """
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

    support_detail: str = Field(
        ...,
        description='Quote exact agent phrases. For False: cite the moment lacking empathy or deflecting + explain what criteria was not met. More False = more detail per finding. Maximum 400 characters (~100 tokens) - Vertex AI enforces during generation.',
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

    support_detail: str = Field(
        ...,
        description='Quote exact agent phrases. For False: cite the jargon/unclear language used + what simpler alternative should have been said. More False = more detail per finding. Maximum 400 characters (~100 tokens) - Vertex AI enforces during generation.',
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

    support_detail: str = Field(
        ...,
        description='Quote exact agent phrases. For False: cite the unclear/misleading statement + what accurate information should have been provided. More False = more detail per finding. Maximum 400 characters (~100 tokens) - Vertex AI enforces during generation.',
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

    support_detail: str = Field(
        ...,
        description='Quote exact agent phrases. For False: cite what was missing (disclosure, verification, data handling) + explain what criteria was not met. More False = more detail per finding. Maximum 400 characters (~100 tokens) - Vertex AI enforces during generation.',
    )


class Compliance(_SchemaModel):
    """Prompt Main Category 4.

    A one-field wrapper, so the path really is ``compliance.compliance.*``. Kept
    because production emits that shape and the sheet's ``cp_compliance_*`` column
    names were derived from it."""

    compliance: SubCompliance = Field(
        ...,
        description='[System Prompt: 4.1] Compliance: Evaluates adherence to standards. See system prompt section 4.1 for complete criteria.',
    )


class SalesPerformance(_SchemaModel):
    """Prompt Additional Category 1 -- what was offered and what was taken.

    The three validators run in declaration order and the order is load-bearing:
    dedup, then drop cross-sell categories that duplicate the main list, then zero the
    counts of any list left empty -- including one emptied by the previous step."""

    main_package_offered: int = Field(
        ...,
        description="[System Prompt: Additional Category 1.1] Main Package Offered - Count of distinct main packages offered by the agent. The main package replaces or upgrades the customer's current package. Same package mentioned multiple times = 1 offer.",
    )

    main_package_accepted: int = Field(
        ...,
        description='[System Prompt: Additional Category 1.2] Main Package Accepted - Count of distinct main packages accepted by the customer. Requires identity verification per Sub-Category 1.2.',
    )

    upsell_add_on_package_offered: int = Field(
        ...,
        description='[System Prompt: Additional Category 1.3] Upsell Add-On Package Offered - Count of distinct upsell add-on packages offered. Upsell = SAME product category as main package. A package in a DIFFERENT category is cross-sell, not upsell.',
    )

    upsell_add_on_package_accepted: int = Field(
        ...,
        description='[System Prompt: Additional Category 1.4] Upsell Add-On Package Accepted - Count of distinct upsell add-on packages accepted by the customer. Requires identity verification per Sub-Category 1.2.',
    )

    crosssell_add_on_product_offered: int = Field(
        ...,
        description='[System Prompt: Additional Category 1.5] Cross-Sell Package Offered - Count of distinct cross-sell packages offered. Cross-sell = DIFFERENT product category from main package. Count individual packages, not categories. Must be 0 if crosssell_add_on_product_offered_list is empty.',
    )

    crosssell_add_on_product_accepted: int = Field(
        ...,
        description='[System Prompt: Additional Category 1.6] Cross-Sell Package Accepted - Count of distinct cross-sell packages accepted by the customer. Must be 0 if crosssell_add_on_product_offered_list is empty. Requires identity verification per Sub-Category 1.2.',
    )

    main_and_upsell_add_on_product_offered_list: list[Literal["Mobile", "TOL", "TVS"]] = Field(
        ...,
        description="[System Prompt: Additional Category 1.7] Product categories for main package and upsell add-ons. Includes the main package's category. Must NOT overlap with crosssell_add_on_product_offered_list. Tags: 'Mobile', 'TOL', 'TVS'.",
    )

    crosssell_add_on_product_offered_list: list[Literal["Mobile", "TOL", "TVS"]] = Field(
        ...,
        description="[System Prompt: Additional Category 1.8] Product categories for cross-sell packages. Must be a DIFFERENT category from the main package. Must NEVER contain the same category as main_and_upsell_add_on_product_offered_list. Tags: 'Mobile', 'TOL', 'TVS'.",
    )

    @model_validator(mode="after")
    def dedup_list(self) -> "SalesPerformance":
        """Step 1: drop repeated categories, preserving first-seen order.

        ``list(set(...))`` -- what production uses -- reorders unpredictably: CPython
        randomises string hashing per process, so the same model output writes a different
        cell on every run. ``dict.fromkeys`` yields the same set, stably.
        """
        self.crosssell_add_on_product_offered_list = list(
            dict.fromkeys(self.crosssell_add_on_product_offered_list)
        )
        self.main_and_upsell_add_on_product_offered_list = list(
            dict.fromkeys(self.main_and_upsell_add_on_product_offered_list)
        )
        return self

    @model_validator(mode="after")
    def enforce_mutual_exclusion(self) -> "SalesPerformance":
        """Step 2: a category in both lists belongs to the main one.

        The main package's category defines the upsell boundary, so anything sharing it is by
        definition not a cross-sell.
        """
        if self.main_and_upsell_add_on_product_offered_list and self.crosssell_add_on_product_offered_list:
            main_categories = set(self.main_and_upsell_add_on_product_offered_list)
            self.crosssell_add_on_product_offered_list = [
                category
                for category in self.crosssell_add_on_product_offered_list
                if category not in main_categories
            ]
        return self

    @model_validator(mode="after")
    def product_list_empty(self) -> "SalesPerformance":
        """Step 3, last: zero the counts of an empty list.

        Must run after :meth:`enforce_mutual_exclusion`, which can empty a list that arrived
        populated -- hence the declaration order of these three.
        """
        if not self.crosssell_add_on_product_offered_list:
            self.crosssell_add_on_product_offered = 0
            self.crosssell_add_on_product_accepted = 0

        if not self.main_and_upsell_add_on_product_offered_list:
            self.main_package_offered = 0
            self.main_package_accepted = 0
            self.upsell_add_on_package_offered = 0
            self.upsell_add_on_package_accepted = 0
        return self


class CustomerInsight(_SchemaModel):
    """Prompt Additional Category 2 -- why the customer said no, and how likely they are to go."""

    rejection_reason: Literal["Price Sensitivity", "Package Mismatch", "Existing Plan Satisfaction", "Decision Deferral"] | None = Field(
        ...,
        description='[System Prompt: Additional Category 2.1] Rejection Reason - Identifies primary reason for customer rejecting offer. See system prompt Additional Category 2.1 for complete criteria.',
    )

    network_issue: Literal["Signal", "Speed", "Coverage"] | None = Field(
        ...,
        description='[System Prompt: Additional Category 2.2] Network Issue - Identifies specific network issue cited by customer. See system prompt Additional Category 2.2 for complete criteria.',
    )

    churn_risk_indicator: int = Field(
        ...,
        description='[System Prompt: Additional Category 2.3] Churn Risk - Percentage likelihood (0-100) that customer will churn based on conversation insights. See system prompt Additional Category 2.3 for evaluation guidelines.',
    )

    customer_sentiment_emotional: Literal["Positive", "Neutral", "Negative"] = Field(
        ...,
        description='[System Prompt: Additional Category 2.4] Customer Sentiment - Assesses the overall sentiment of the customer throughout the call based on their statements and tone. See system prompt Additional Category 2.4 for complete criteria.',
    )

    @model_validator(mode="after")
    def validate_churn_risk_indicator(self) -> "CustomerInsight":
        """Clamp the risk score to 0-100 rather than raising on an out-of-range answer.

        Production raises. A score of 120 is a formatting slip on an unscored field; losing
        the call's 39 scored flags over it would be a poor trade, so it is clamped and
        recorded.
        """
        clamped = min(100, max(0, self.churn_risk_indicator))
        if clamped != self.churn_risk_indicator:
            self.churn_risk_indicator = clamped
            self._coerced.append("customer_insight.churn_risk_indicator")
        return self


class ModelResponse(_SchemaModel):
    """Everything the model returns for one outbound telesale call.

    Twelve fields: the transcript it produces first, then the eleven production
    fields. Only the thirty-nine boolean leaves under the four judging blocks have
    ground truth; the rest are generated because production generates them, and land
    in the GCS prediction shards rather than the workbook.
    """

    transcript: str = Field(
        ...,
        description=TRANSCRIPT_DESCRIPTION,
    )

    campaign_name: str = Field(
        ...,
        description='Campaign identifier for the call evaluation.',
    )

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

    agent_strength: str = Field(
        ...,
        description='Overall performance summary highlighting 2-4 key strengths demonstrated consistently across all evaluation categories (operations, sales, CX, compliance). Written in Thai with specific behavioral examples and redacted names [Agent]/[Customer]. Maximum 800 characters.',
    )

    agent_weakness: str = Field(
        ...,
        description='Overall performance summary identifying 2-4 key improvement areas/weaknesses across all evaluation categories that impacted scores. Written in Thai with specific gaps/issues as constructive feedback and redacted names [Agent]/[Customer]. Maximum 800 characters.',
    )

    sales_performance: SalesPerformance = Field(
        ...,
        description='[System Prompt: Additional Category 1] Sell Performance - Evaluates key sales performance indicators such as main package offers/acceptances, upsell offers/acceptances, and cross-sell offers/acceptances. See system prompt Additional Category 1 for complete criteria.',
    )

    customer_insight: CustomerInsight = Field(
        ...,
        description='[System Prompt: Additional Category 2] Customer Insight - Evaluates customer-specific insights such as rejection reasons, network issues, churn risk, and sentiment. See system prompt Additional Category 2 for complete criteria.',
    )

    @model_validator(mode="after")
    def validate_data_privacy_compliance(self) -> "ModelResponse":
        """A failed identity check forces ``data_privacy_compliance`` False.

        This overwrites a *scored* column with a rule rather than the model's own answer.
        That is what production does, and the sheet was labelled under the same rule, so it
        stays -- but it is the reason a compliance verdict here is not purely a model output.
        """
        block = self.operations_and_professionalism.customer_identity_verification
        if False in (
            block.customer_verification,
            block.invalid_verification,
            block.missing_verification,
        ):
            self.compliance.compliance.data_privacy_compliance = False
        return self

    @model_validator(mode="after")
    def sync_crosssell_upsell_with_25(self) -> "ModelResponse":
        """No cross-sell attempt means no cross-sell numbers.

        ``None`` (no opportunity) and ``False`` (opportunity missed) both mean nothing was
        actually offered, so the counts cannot be non-zero whatever the model wrote.
        """
        missed = self.sales_effectiveness.cross_sell_upsell.missed_crosssell_upsell
        if missed is None or missed is False:
            performance = self.sales_performance
            performance.upsell_add_on_package_offered = 0
            performance.upsell_add_on_package_accepted = 0
            performance.crosssell_add_on_product_offered = 0
            performance.crosssell_add_on_product_accepted = 0
            performance.crosssell_add_on_product_offered_list = []
        return self


def collect_coercions(response: ModelResponse) -> list[str]:
    """Every field the softened validators had to rewrite, as dotted paths.

    Walks the nested models rather than reading one counter, because the coercions happen
    inside three different sub-models. An empty list means the model answered within the
    prompt's own rules; a non-empty one is a prompt-compliance failure that was repaired
    rather than fatal, and belongs on the Matrix sheet where it can be read.
    """
    found: list[str] = []

    def walk(model: BaseModel) -> None:
        if isinstance(model, _SchemaModel):
            found.extend(model._coerced)
        for name in type(model).model_fields:
            value = getattr(model, name, None)
            if isinstance(value, BaseModel):
                walk(value)

    walk(response)
    return found


def _forbid_additional_properties(node: Any) -> Any:
    """Set ``additionalProperties: False`` on every object node, recursively.

    OpenAI's strict mode requires it on every object; the Gemini API rejects it, so it is
    injected on the OpenAI rendering only rather than via ``ConfigDict(extra="forbid")``.
    """
    if isinstance(node, dict):
        out = {k: _forbid_additional_properties(v) for k, v in node.items()}
        if out.get("type") == "object":
            out["additionalProperties"] = False
        return out
    if isinstance(node, list):
        return [_forbid_additional_properties(i) for i in node]
    return node


class SchemaHelper:
    """Renders :class:`ModelResponse` for each backend's schema dialect.

    Vertex and OpenAI want contradictory schema forms, so there is one renderer per target.
    For online google-genai calls, pass the class straight to ``GenerateContentConfig`` and
    read the typed result off ``response.parsed`` instead.
    """

    @staticmethod
    def vertex_schema() -> types.Schema:
        """Convert :class:`ModelResponse` to Vertex's OpenAPI-subset ``Schema``.

        ``from_json_schema`` inlines ``$defs`` (required: Vertex ingests batch JSONL via a
        BigQuery load, which rejects ``$``-prefixed keys), uppercases types, and folds
        ``anyOf: [X, null]`` into ``nullable: True`` -- hand-rolling that fold silently breaks
        nullable fields. ``raise_error_on_unsupported_field`` prevents silent field drops.
        """
        return types.Schema.from_json_schema(
            json_schema=types.JSONSchema.model_validate(ModelResponse.model_json_schema()),
            api_option="VERTEX_AI",
            raise_error_on_unsupported_field=True,
        )

    @staticmethod
    def vertex_generation_config() -> dict[str, Any]:
        """Return the ``generation_config`` block for a Vertex batch JSONL row.

        Batch rows are serialized ``GenerateContentRequest`` objects read straight off GCS
        (``inlined_requests`` is rejected on Vertex). ``by_alias=False`` emits snake_case, a
        convention choice -- ProtoJSON accepts both spellings.
        """
        return types.GenerationConfig(
            response_mime_type="application/json",
            response_schema=SchemaHelper.vertex_schema(),
        ).model_dump(exclude_none=True, mode="json", by_alias=False)

    @staticmethod
    def openai_response_format() -> dict[str, Any]:
        """Return the ``response_format`` payload for an OpenAI-compatible endpoint.

        ``union_format="primitive_type_array"`` gives OpenAI's documented nullable form;
        strict mode's every-property-required rule already holds (no field has a default),
        and ``$defs``/``$ref`` are left in place -- OpenAI supports them.
        """
        schema = ModelResponse.model_json_schema(union_format="primitive_type_array")
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "model_response",
                "strict": True,
                "schema": _forbid_additional_properties(schema),
            },
        }


__all__ = [
    "TRANSCRIPT_DESCRIPTION",
    "ActiveListening",
    "BuildingTrust",
    "CallClosing",
    "CallOpening",
    "CampaignRatio",
    "ClarityOfCommunication",
    "Compliance",
    "CrossSellUpsell",
    "CustomerExperience",
    "CustomerIdentityVerification",
    "CustomerInsight",
    "CustomerNeedsAnalysis",
    "EffectiveObjectionHandling",
    "LanguageAndTone",
    "ModelResponse",
    "OfferPresentationQuality",
    "OperationsAndProfessionalism",
    "PositiveCustomerExperience",
    "SalesClosingAttempt",
    "SalesEffectiveness",
    "SalesPerformance",
    "SchemaHelper",
    "SubCompliance",
    "collect_coercions",
]
