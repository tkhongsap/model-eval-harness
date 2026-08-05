from pydantic import BaseModel, ConfigDict, Field


class SubCompliance(BaseModel):
    model_config = ConfigDict(extra="ignore")

    data_privacy_compliance: bool = Field(
        ...,
        description="[System Prompt: 4.1.1] data_privacy_permission_compliance - Data Privacy & Permission Compliance: Agent uses customer data within authorized scope, discloses company identity and call purpose, does not pressure customer, and completes verification if sale occurred. NOTE: 'permission' here means data privacy scope — NOT consent before presenting (that is 1.1.3 call_consent_before_engagement). See system prompt section 4.1.1 for complete evaluation criteria.",
    )

    sales_integrity_compliance: bool = Field(
        ...,
        description="[System Prompt: 4.1.2] sales_integrity_transparency_compliance - Sales Integrity & Transparency: Provides complete, truthful information with all terms disclosed. See system prompt section 4.1.2 for complete evaluation criteria.",
    )

    professional_conduct_compliance: bool = Field(
        ...,
        description="[System Prompt: 4.1.3] professional_conduct_behavioral_compliance - Professional Conduct: Maintains professional, respectful communication throughout call. See system prompt section 4.1.3 for complete evaluation criteria.",
    )

    support_detail: str = Field(
        ...,
        # max_length=400,
        description="Quote exact agent phrases. For False: cite what was missing (disclosure, verification, data handling) + explain what criteria was not met. More False = more detail per finding. Maximum 400 characters (~100 tokens) - Vertex AI enforces during generation.",
    )


class Compliance(BaseModel):
    model_config = ConfigDict(extra="ignore")

    compliance: SubCompliance = Field(
        ...,
        description="[System Prompt: 4.1] Compliance: Evaluates adherence to standards. See system prompt section 4.1 for complete criteria.",
    )
