from pydantic import BaseModel, ConfigDict, Field, model_validator


class CallOpening(BaseModel):
    model_config = ConfigDict(extra="ignore")

    proper_identification: bool = Field(
        ...,
        description="[System Prompt: 1.1.1] proper_identification - Proper Identification (การแสดงตัวตนผู้ติดต่อ): Agent provides greeting and informs agent name at call beginning. See system prompt section 1.1.1 for complete evaluation criteria.",
    )

    call_origin_disclosure: bool = Field(
        ...,
        description="[System Prompt: 1.1.2] call_origin_disclosure - Call Origin Disclosure (การแจ้งที่มาของการติดต่อ): Agent clearly informs which company calling from (True/Dtac). See system prompt section 1.1.2 for complete evaluation criteria.",
    )

    call_consent_before_engagement: bool = Field(
        ...,
        description="[System Prompt: 1.1.3] consent_before_engagement - Consent Before Engagement (การขออนุญาตก่อนสนทนา/นำเสนอ): Agent EXPLICITLY asks customer's permission/convenience before proceeding with detailed offer (e.g., 'ขออนุญาต', 'สะดวกคุยสาย', 'ขออนุญาตเรียนแจ้ง'). Consent can appear before OR after stating call objective or call introduction. CRITICAL: Announcing/pitching alone (e.g., 'วันนี้จะมาดูแล...') without any consent phrase is NOT consent — only explicit permission-seeking counts. See system prompt section 1.1.3 for complete evaluation criteria.",
    )

    support_detail: str = Field(
        ...,
        # max_length=400,
        description="Quote exact agent phrases as evidence. For True: briefly cite. For False: quote what agent said (or note silence) + explain what criteria was not met. More False = more detail per finding. Maximum 400 characters (~100 tokens) - Vertex AI enforces this during generation.",
    )


class CustomerIdentityVerification(BaseModel):
    model_config = ConfigDict(extra="ignore")

    customer_verification: bool | None = Field(
        None,
        description="[System Prompt: 1.2.1] customer_verification - Customer Verification (ยืนยันตัวตนถูกต้อง): Agent asks customer for name-surname or surname when completing sale/service. See system prompt section 1.2.1 for complete evaluation criteria.",
    )

    invalid_verification: bool | None = Field(
        None,
        description="[System Prompt: 1.2.2] invalid_verification - Invalid Verification (ยืนยันตัวตนไม่ครบถ้วน): When customer is not the registrant and is the payer, all 3-part verification questions were completed. See system prompt section 1.2.2 for complete evaluation criteria.",
    )

    missing_verification: bool | None = Field(
        None,
        description="[System Prompt: 1.2.3] missing_or_late_verification - Missing or Late Verification (ไม่ยืนยันตัวตน): Overall result — whether full required verification was performed. See system prompt section 1.2.3 for complete evaluation criteria.",
    )

    support_detail: str = Field(
        ...,
        # max_length=400,
        description="Quote exact evidence. If None: quote the conversation moment proving no sale occurred. If True/False: cite verification steps with exact phrases. More False = more detail per finding. Maximum 400 characters (~100 tokens) - Vertex AI enforces during generation.",
    )

    @model_validator(mode="after")
    def validate_verification_fields(self):
        if self.customer_verification is None:
            # Scenario 1 / Scenario 7: No sale or agent redirect — force all to None
            self.invalid_verification = None
            self.missing_verification = None
        elif self.customer_verification is False:
            # Scenario 2: No verification attempted — force both to False
            self.invalid_verification = False
            self.missing_verification = False
        elif self.customer_verification is True:
            # Valid combinations when agent asked (customer_verification=True)
            valid_combinations = [
                (None, None),  # Special Null: not registrant & not payer
                (None, True),  # Scenarios 3 & 4: assumed/confirmed registrant — 3-part not triggered
                (True, True),  # Scenario 5: full 3-part verification done
                (False, False),  # Scenario 6: incomplete 3-part verification
            ]
            combo = (self.invalid_verification, self.missing_verification)
            if combo not in valid_combinations:
                raise ValueError(
                    f"Invalid combination: customer_verification=True with "
                    f"invalid_verification={self.invalid_verification}, "
                    f"missing_verification={self.missing_verification}. "
                    "Valid combinations: (None,None), (None,True), (True,True), (False,False)"
                )
        return self


class LanguageAndTone(BaseModel):
    model_config = ConfigDict(extra="ignore")

    behavioral_violation: bool = Field(
        ...,
        description="[System Prompt: 1.3.1] behavioral_violation - Behavioral Violation (ความไม่เหมาะสม): Detects inappropriate language, sarcasm, or aggressive tone. See system prompt section 1.3.1 for complete evaluation criteria.",
    )

    clarity: bool = Field(
        ...,
        description="[System Prompt: 1.3.2] clarity - Clarity (ความไม่ชัดเจนของถ้อยคำ): Clear and direct communication without ambiguity. See system prompt section 1.3.2 for complete evaluation criteria.",
    )

    delivery_pace: bool = Field(
        ...,
        description="[System Prompt: 1.3.3] delivery_pace - Delivery Pace (ความเร็วในการพูด): Appropriate speaking speed, neither too fast nor too slow. See system prompt section 1.3.3 for complete evaluation criteria.",
    )

    support_detail: str = Field(
        ...,
        # max_length=400,
        description="Quote exact agent phrases showing tone/clarity/pacing. For False: quote the problematic phrase + explain what criteria was not met. More False = more detail per finding. Maximum 400 characters (~100 tokens) - Vertex AI enforces during generation.",
    )


class ActiveListening(BaseModel):
    model_config = ConfigDict(extra="ignore")

    no_interruption: bool = Field(
        ...,
        description="[System Prompt: 1.4.1] no_interruption - No Interruption (ไม่ขัดจังหวะลูกค้า): Agent must not interrupt while customer is explaining problems and must wait for customer to finish before responding. See system prompt section 1.4.1 for complete evaluation criteria.",
    )

    correct_understanding: bool = Field(
        ...,
        description="[System Prompt: 1.4.2] correct_understanding - Correct Understanding (จับประเด็นถูกต้อง): Agent must understand customer's problem and respond according to the Intent that customer communicated. See system prompt section 1.4.2 for complete evaluation criteria.",
    )

    acknowledgement_paraphrasing: bool = Field(
        ...,
        description="[System Prompt: 1.4.3] acknowledgement_paraphrasing - Acknowledgement & Paraphrasing (การทวนและยืนยันความเข้าใจ): Agent asks questions, paraphrases, and summarizes customer needs comprehensively. CRITICAL: If customer is NOT interested (rejected/declined), this MUST be True — paraphrasing is only required when customer is actively engaged. See system prompt section 1.4.3 for complete evaluation criteria.",
    )

    support_detail: str = Field(
        ...,
        # max_length=400,
        description="Quote exact agent/customer phrases. For False: cite the interruption moment, misunderstanding, or missing paraphrase + explain what criteria was not met. More False = more detail per finding. Maximum 400 characters (~100 tokens) - Vertex AI enforces during generation.",
    )


class CallClosing(BaseModel):
    model_config = ConfigDict(extra="ignore")

    confirm_resolution: bool = Field(
        ...,
        description="[System Prompt: 1.5.1] confirm_resolution_and_next_steps - Confirm Resolution and Next Steps (ยืนยันการปรับเปลี่ยนและขั้นตอนถัดไปหรือสรุปการสนทนา): If sale completed, agent must confirm what was done, summarize sale/service details, and explain next steps. CRITICAL: If NO sale/service completed (customer rejected/declined), this MUST be True — summarizing is only required when a transaction occurred. See system prompt section 1.5.1 for complete evaluation criteria.",
    )

    courteous_ending: bool = Field(
        ...,
        description="[System Prompt: 1.5.2] courteous_and_proper_call_ending - Courteous and Proper Call Ending (ปิดการสนทนาด้วยมารยาทและความสุภาพ): Agent ends call with thank you, provides support hotline info (1242), and says goodbye with gentle tone. See system prompt section 1.5.2 for complete evaluation criteria.",
    )

    smooth_closing: bool = Field(
        ...,
        description="[System Prompt: 1.5.3] smooth_and_non_rushed_closing - Smooth and Non-Rushed Closing (ปิดบทสนทนาอย่างราบรื่น ไม่เร่งรีบ): Agent asks if customer has questions or waits few seconds after closing statement, giving customer opportunity to respond before ending. CRITICAL: If customer is busy/not available or ends the call themselves, this MUST be True — agent does not need to wait or ask additional questions. Can ONLY be False when agent (not customer) rushes the closing. See system prompt section 1.5.3 for complete evaluation criteria.",
    )

    support_detail: str = Field(
        ...,
        # max_length=400,
        description="Quote exact closing phrases or note their absence. For False: cite what agent said (or didn't say) + what was expected. More False = more detail per finding. Maximum 400 characters (~100 tokens) - Vertex AI enforces during generation.",
    )


class OperationsAndProfessionalism(BaseModel):
    model_config = ConfigDict(extra="ignore")

    call_opening: CallOpening = Field(
        ...,
        description="[System Prompt: 1.1] Call Opening (การเปิดสาย): Opening procedures for outbound telesale calls. See system prompt section 1.1 for complete criteria.",
    )

    customer_identity_verification: CustomerIdentityVerification = Field(
        ...,
        description="[System Prompt: 1.2] Customer Identity Verification (การยืนยันตัวตนลูกค้า): Verification procedures. PLACEHOLDER - Reserved for future use.",
    )

    language_and_tone: LanguageAndTone = Field(
        ...,
        description="[System Prompt: 1.3] Language & Tone (ภาษาและน้ำเสียง): Professional communication quality. See system prompt section 1.3 for complete criteria.",
    )

    active_listening: ActiveListening = Field(
        ...,
        description="[System Prompt: 1.4] Active Listening (การรับฟังอย่างตั้งใจ): Attentive listening by focusing on both words and emotions. Each criterion is independent - evaluate separately. See system prompt section 1.4 for complete criteria.",
    )

    call_closing: CallClosing = Field(
        ...,
        description="[System Prompt: 1.5] Call Closing (การปิดสาย): Proper call conclusion procedures. See system prompt section 1.5 for complete criteria.",
    )
